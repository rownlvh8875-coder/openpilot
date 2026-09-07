#!/usr/bin/env python3
"""Manual S1 observer evidence recorder.

This tool never enables/disables observer, telemetry, shadow, controls, or Params.
It only reads cereal/Param-file state and writes research evidence files.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import time
from typing import Any

LEGS = ("S1_OFF_BEFORE", "S1_ON", "S1_OFF_AFTER")
OBSERVER_MARKER = Path("/data/egpu_integrated/observer_enabled")
TELEMETRY_MARKER = Path("/data/egpu_integrated/telemetry_enabled")
SHADOW_MARKER = Path("/tmp/egpu_integrated_shadow_tap.enable")
OBSERVER_STATE = Path("/data/egpu_integrated/state.json")
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


def normalize_gear(value: Any) -> str:
  text = str(value).lower()
  if "park" in text:
    return "park"
  if "drive" in text:
    return "drive"
  if "reverse" in text:
    return "reverse"
  if "neutral" in text:
    return "neutral"
  return text.rsplit(".", 1)[-1]


def observer_state_after(start_mono: float) -> tuple[bool | None, int, int]:
  try:
    value = json.loads(OBSERVER_STATE.read_text(encoding="utf-8"))
    timestamp = float(value.get("timestampMonoS", -1.0))
    fresh = timestamp >= start_mono
    return fresh, int(value.get("writeErrors", 0)), int(value.get("fallbackCount", 0))
  except (OSError, ValueError, TypeError, json.JSONDecodeError):
    return False, 0, 0


def main() -> int:
  ap = argparse.ArgumentParser()
  ap.add_argument("--leg", choices=LEGS, required=True)
  ap.add_argument("--duration", type=float, required=True)
  ap.add_argument("--output-dir", type=Path, required=True)
  args = ap.parse_args()
  if not 0 < args.duration <= 600:
    raise SystemExit("--duration must be >0 and <=600 seconds")

  expected_observer = args.leg == "S1_ON"
  marker_ok = OBSERVER_MARKER.exists() == expected_observer
  auxiliary_off = not TELEMETRY_MARKER.exists() and not SHADOW_MARKER.exists()
  if not marker_ok or not auxiliary_off:
    print(json.dumps({
      "status": "HOLD",
      "reason": "marker_state_mismatch",
      "observerExpected": expected_observer,
      "observerMarker": OBSERVER_MARKER.exists(),
      "telemetryMarker": TELEMETRY_MARKER.exists(),
      "shadowMarker": SHADOW_MARKER.exists(),
    }, indent=2))
    return 3

  import openpilot.cereal.messaging as messaging

  out_dir = args.output_dir.resolve()
  out_dir.mkdir(parents=True, exist_ok=True)
  jsonl_path = out_dir / f"{args.leg}.jsonl"
  summary_path = out_dir / f"{args.leg}.summary.json"
  sm = messaging.SubMaster(["modelV2", "carState", "carControl", "selfdriveState"])

  start_mono = time.monotonic()
  deadline = start_mono + args.duration
  latencies: list[float] = []
  last_frame: int | None = None
  frame_gaps = 0
  fallbacks = 0
  guard_violations = 0
  last_usbgpu = read_bool(USBGPU_ACTIVE)
  if last_usbgpu is not True:
    fallbacks += 1

  with jsonl_path.open("w", encoding="utf-8", buffering=1) as out:
    while time.monotonic() < deadline:
      sm.update(100)
      if not sm.updated["modelV2"]:
        continue

      model = sm["modelV2"]
      car_state = sm["carState"]
      car_control = sm["carControl"]
      selfdrive = sm["selfdriveState"]
      frame_id = int(model.frameId)
      latency_ms = max(0.0, float(model.modelExecutionTime) * 1000.0)
      latencies.append(latency_ms)

      if last_frame is not None and frame_id > last_frame + 1:
        frame_gaps += frame_id - last_frame - 1
      last_frame = frame_id

      usbgpu_now = read_bool(USBGPU_ACTIVE)
      if last_usbgpu is True and usbgpu_now is False:
        fallbacks += 1
      last_usbgpu = usbgpu_now

      v_ego = float(car_state.vEgo)
      standstill = bool(car_state.standstill)
      gear = normalize_gear(car_state.gearShifter)
      lat_active = bool(car_control.latActive)
      long_active = bool(car_control.longActive)
      selfdrive_active = bool(selfdrive.active)
      guard_ok = standstill and abs(v_ego) < 0.01 and gear == "park" and not lat_active and not long_active and not selfdrive_active

      event = {
        "leg": args.leg,
        "timestampMonoS": time.monotonic(),
        "frameId": frame_id,
        "frameAge": int(model.frameAge),
        "modelExecutionMs": latency_ms,
        "vEgo": v_ego,
        "standstill": standstill,
        "gear": gear,
        "latActive": lat_active,
        "longActive": long_active,
        "selfdriveActive": selfdrive_active,
        "UsbGpuActive": usbgpu_now,
        "guardOk": guard_ok,
      }
      out.write(json.dumps(event, separators=(",", ":"), allow_nan=False) + "\n")
      if not guard_ok:
        guard_violations += 1
        break

  observer_fresh: bool | None = None
  observer_write_errors = 0
  observer_fallbacks = 0
  if expected_observer:
    observer_fresh, observer_write_errors, observer_fallbacks = observer_state_after(start_mono)
    fallbacks = max(fallbacks, observer_fallbacks)

  summary = {
    "leg": args.leg,
    "samples": len(latencies),
    "p50ModelExecutionMs": percentile(latencies, 0.50),
    "p95ModelExecutionMs": percentile(latencies, 0.95),
    "p99ModelExecutionMs": percentile(latencies, 0.99),
    "maxModelExecutionMs": max(latencies) if latencies else 0.0,
    "frameGapCount": frame_gaps,
    "fallbackCount": fallbacks,
    "guardViolationCount": guard_violations,
    "observerWriteErrors": observer_write_errors,
    "observerStateFresh": observer_fresh,
  }
  summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
  print(json.dumps(summary, indent=2, ensure_ascii=False))
  if guard_violations:
    return 3
  if expected_observer and observer_fresh is not True:
    return 2
  return 0


if __name__ == "__main__":
  raise SystemExit(main())
