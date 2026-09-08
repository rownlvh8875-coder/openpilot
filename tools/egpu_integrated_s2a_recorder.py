#!/usr/bin/env python3
"""Manual read-only S2A telemetry-only evidence recorder.

This tool does not change feature markers, Params, branches, processes, power,
or vehicle controls. It records source identity, active model timing, and fresh
hardware telemetry.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import subprocess
import time
from typing import Any

LEGS = ("S2A_OFF_BEFORE", "S2A_TELEMETRY_ON", "S2A_OFF_AFTER")
EXPECTED_BRANCH = "carrot-wip-integrated-v6"
OBSERVER_MARKER = Path("/data/egpu_integrated/observer_enabled")
TELEMETRY_MARKER = Path("/data/egpu_integrated/telemetry_enabled")
SHADOW_MARKER = Path("/tmp/egpu_integrated_shadow_tap.enable")
HARDWARE_STATE = Path("/data/egpu_integrated/hardware.json")
USBGPU_ACTIVE = Path("/data/params/d/UsbGpuActive")


def percentile(values: list[float], q: float) -> float:
  if not values:
    return 0.0
  xs = sorted(values)
  if len(xs) == 1:
    return xs[0]
  pos = max(0.0, min(1.0, q)) * (len(xs) - 1)
  lo = int(math.floor(pos)); hi = min(lo + 1, len(xs) - 1)
  frac = pos - lo
  return xs[lo] * (1.0 - frac) + xs[hi] * frac


def read_bool(path: Path) -> bool | None:
  try:
    raw = path.read_bytes().strip()
  except OSError:
    return None
  if raw in {b"1", b"true", b"True"}:
    return True
  if raw in {b"0", b"false", b"False", b""}:
    return False
  return None


def git_identity(repo: Path) -> tuple[str | None, str | None]:
  def read(*args: str) -> str | None:
    p = subprocess.run(["git", "-C", str(repo), *args], text=True, capture_output=True, check=False)
    return p.stdout.strip() if p.returncode == 0 and p.stdout.strip() else None
  return read("rev-parse", "HEAD"), read("rev-parse", "--abbrev-ref", "HEAD")


def normalize_gear(value: Any) -> str:
  text = str(value).lower()
  if "park" in text: return "park"
  if "drive" in text: return "drive"
  if "reverse" in text: return "reverse"
  if "neutral" in text: return "neutral"
  return text.rsplit(".", 1)[-1]


def finite(value: Any) -> float | None:
  try:
    out = float(value)
  except (TypeError, ValueError):
    return None
  return out if math.isfinite(out) else None


def read_hardware() -> dict[str, Any] | None:
  try:
    value = json.loads(HARDWARE_STATE.read_text(encoding="utf-8"))
  except (OSError, ValueError, TypeError, json.JSONDecodeError):
    return None
  return value if isinstance(value, dict) else None


def main() -> int:
  ap = argparse.ArgumentParser()
  ap.add_argument("--leg", choices=LEGS, required=True)
  ap.add_argument("--duration", type=float, required=True)
  ap.add_argument("--output-dir", type=Path, required=True)
  ap.add_argument("--expected-head", required=True)
  ap.add_argument("--expected-branch", default=EXPECTED_BRANCH)
  args = ap.parse_args()
  if not 0 < args.duration <= 600:
    raise SystemExit("--duration must be >0 and <=600 seconds")

  repo = Path(__file__).resolve().parents[1]
  source_head, source_branch = git_identity(repo)
  if source_head != args.expected_head or source_branch != args.expected_branch:
    print(json.dumps({
      "status": "HOLD", "reason": "source_identity_mismatch",
      "sourceHead": source_head, "expectedHead": args.expected_head,
      "sourceBranch": source_branch, "expectedBranch": args.expected_branch,
    }, indent=2))
    return 3

  expected_telemetry = args.leg == "S2A_TELEMETRY_ON"
  marker_ok = TELEMETRY_MARKER.exists() == expected_telemetry
  isolation_ok = not OBSERVER_MARKER.exists() and not SHADOW_MARKER.exists()
  if not marker_ok or not isolation_ok:
    print(json.dumps({
      "status": "HOLD", "reason": "marker_state_mismatch",
      "telemetryExpected": expected_telemetry, "telemetryMarker": TELEMETRY_MARKER.exists(),
      "observerMarker": OBSERVER_MARKER.exists(), "shadowMarker": SHADOW_MARKER.exists(),
    }, indent=2))
    return 3

  import openpilot.cereal.messaging as messaging

  out_dir = args.output_dir.resolve(); out_dir.mkdir(parents=True, exist_ok=True)
  jsonl_path = out_dir / f"{args.leg}.jsonl"
  summary_path = out_dir / f"{args.leg}.summary.json"
  sm = messaging.SubMaster(["modelV2", "carState", "carControl", "selfdriveState"])

  start_mono = time.monotonic(); deadline = start_mono + args.duration
  model_latencies: list[float] = []; hardware_durations: list[float] = []
  last_frame: int | None = None; last_hw_timestamp: float | None = None
  frame_gaps = fallbacks = guard_violations = 0
  hardware_samples = hardware_valid_samples = hardware_error_samples = supply_fault_samples = 0
  max_temp_c: float | None = None; max_memory_temp_c: float | None = None; max_power_draw_w: float | None = None
  last_usbgpu = read_bool(USBGPU_ACTIVE)
  if last_usbgpu is not True: fallbacks += 1

  with jsonl_path.open("w", encoding="utf-8", buffering=1) as out:
    while time.monotonic() < deadline:
      sm.update(100)
      if not sm.updated["modelV2"]: continue

      model, car_state, car_control, selfdrive = sm["modelV2"], sm["carState"], sm["carControl"], sm["selfdriveState"]
      frame_id = int(model.frameId); latency_ms = max(0.0, float(model.modelExecutionTime) * 1000.0)
      model_latencies.append(latency_ms)
      if last_frame is not None and frame_id > last_frame + 1: frame_gaps += frame_id - last_frame - 1
      last_frame = frame_id

      usbgpu_now = read_bool(USBGPU_ACTIVE)
      if last_usbgpu is True and usbgpu_now is False: fallbacks += 1
      last_usbgpu = usbgpu_now

      v_ego = float(car_state.vEgo); standstill = bool(car_state.standstill); gear = normalize_gear(car_state.gearShifter)
      lat_active, long_active, selfdrive_active = bool(car_control.latActive), bool(car_control.longActive), bool(selfdrive.active)
      guard_ok = standstill and abs(v_ego) < 0.01 and gear == "park" and not lat_active and not long_active and not selfdrive_active

      hw_event = None
      if expected_telemetry:
        hw = read_hardware()
        if hw is not None:
          timestamp = finite(hw.get("timestampMonoS"))
          if timestamp is not None and timestamp >= start_mono and timestamp != last_hw_timestamp:
            last_hw_timestamp = timestamp; hardware_samples += 1
            if bool(hw.get("valid", False)): hardware_valid_samples += 1
            errors = hw.get("errors")
            if isinstance(errors, list) and errors: hardware_error_samples += 1
            if bool(hw.get("supplyFault", False)): supply_fault_samples += 1
            duration = finite(hw.get("sampleDurationMs"))
            if duration is not None and duration >= 0: hardware_durations.append(duration)
            temp = finite(hw.get("tempC")); mem_temp = finite(hw.get("memoryTempC")); power = finite(hw.get("powerDrawW"))
            if temp is not None: max_temp_c = temp if max_temp_c is None else max(max_temp_c, temp)
            if mem_temp is not None: max_memory_temp_c = mem_temp if max_memory_temp_c is None else max(max_memory_temp_c, mem_temp)
            if power is not None: max_power_draw_w = power if max_power_draw_w is None else max(max_power_draw_w, power)
            hw_event = {
              "timestampMonoS": timestamp, "valid": bool(hw.get("valid", False)),
              "errors": errors if isinstance(errors, list) else [], "supplyFault": bool(hw.get("supplyFault", False)),
              "sampleDurationMs": duration, "tempC": temp, "memoryTempC": mem_temp, "powerDrawW": power,
              "powerLimitW": finite(hw.get("powerLimitW")), "gpuUsagePercent": finite(hw.get("gpuUsagePercent")),
              "gpuClockMhz": finite(hw.get("gpuClockMhz")), "fanSpeedRpm": finite(hw.get("fanSpeedRpm")),
              "supplyVoltageMv": finite(hw.get("supplyVoltageMv")), "supplyCurrentMa": finite(hw.get("supplyCurrentMa")),
              "usbSpeedMbps": finite(hw.get("usbSpeedMbps")), "usbLinkErrorCount": finite(hw.get("usbLinkErrorCount")),
              "pcieLtssm": finite(hw.get("pcieLtssm")),
            }

      event = {
        "leg": args.leg, "sourceHead": source_head, "sourceBranch": source_branch,
        "timestampMonoS": time.monotonic(), "frameId": frame_id, "frameAge": int(model.frameAge),
        "modelExecutionMs": latency_ms, "vEgo": v_ego, "standstill": standstill, "gear": gear,
        "latActive": lat_active, "longActive": long_active, "selfdriveActive": selfdrive_active,
        "UsbGpuActive": usbgpu_now, "guardOk": guard_ok, "hardware": hw_event,
      }
      out.write(json.dumps(event, separators=(",", ":"), allow_nan=False) + "\n")
      if not guard_ok: guard_violations += 1; break
      if hw_event is not None and hw_event["supplyFault"]: break

  telemetry_fresh = last_hw_timestamp is not None if expected_telemetry else None
  summary = {
    "leg": args.leg, "sourceHead": source_head, "sourceBranch": source_branch,
    "samples": len(model_latencies), "p50ModelExecutionMs": percentile(model_latencies, 0.50),
    "p95ModelExecutionMs": percentile(model_latencies, 0.95), "p99ModelExecutionMs": percentile(model_latencies, 0.99),
    "maxModelExecutionMs": max(model_latencies) if model_latencies else 0.0,
    "frameGapCount": frame_gaps, "fallbackCount": fallbacks, "guardViolationCount": guard_violations,
    "telemetryStateFresh": telemetry_fresh, "hardwareSamples": hardware_samples,
    "hardwareValidSamples": hardware_valid_samples, "hardwareErrorSamples": hardware_error_samples,
    "supplyFaultSamples": supply_fault_samples, "p95HardwareSampleDurationMs": percentile(hardware_durations, 0.95),
    "maxHardwareSampleDurationMs": max(hardware_durations) if hardware_durations else 0.0,
    "maxGpuTempC": max_temp_c, "maxMemoryTempC": max_memory_temp_c, "maxPowerDrawW": max_power_draw_w,
  }
  summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8")
  print(json.dumps(summary, indent=2, ensure_ascii=False, allow_nan=False))
  if guard_violations or supply_fault_samples: return 3
  if expected_telemetry and not telemetry_fresh: return 2
  return 0


if __name__ == "__main__":
  raise SystemExit(main())
