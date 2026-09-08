#!/usr/bin/env python3
"""Qualify S2A telemetry-only interference using explicit research policy.

No acceptance threshold is embedded. Missing policy or insufficient evidence is
HOLD. PASS only opens an observer+telemetry coexistence plan.
"""
from __future__ import annotations

from dataclasses import dataclass
import argparse
import json
import math
from pathlib import Path
from typing import Any

LEGS = ("S2A_OFF_BEFORE", "S2A_TELEMETRY_ON", "S2A_OFF_AFTER")


def finite_optional(value: Any) -> float | None:
  if value is None:
    return None
  try:
    out = float(value)
  except (TypeError, ValueError):
    return None
  return out if math.isfinite(out) else None


@dataclass(frozen=True)
class S2AMetrics:
  leg: str
  samples: int
  p50_ms: float
  p95_ms: float
  p99_ms: float
  max_ms: float
  frame_gaps: int
  fallbacks: int
  guard_violations: int
  telemetry_state_fresh: bool | None
  hardware_samples: int
  hardware_valid_samples: int
  hardware_error_samples: int
  supply_fault_samples: int
  p95_hardware_sample_duration_ms: float
  max_hardware_sample_duration_ms: float
  max_gpu_temp_c: float | None
  max_memory_temp_c: float | None
  max_power_draw_w: float | None

  @classmethod
  def from_dict(cls, value: dict[str, Any]) -> "S2AMetrics":
    fresh = value.get("telemetryStateFresh")
    return cls(
      leg=str(value["leg"]),
      samples=int(value["samples"]),
      p50_ms=float(value["p50ModelExecutionMs"]),
      p95_ms=float(value["p95ModelExecutionMs"]),
      p99_ms=float(value["p99ModelExecutionMs"]),
      max_ms=float(value["maxModelExecutionMs"]),
      frame_gaps=int(value.get("frameGapCount", 0)),
      fallbacks=int(value.get("fallbackCount", 0)),
      guard_violations=int(value.get("guardViolationCount", 0)),
      telemetry_state_fresh=fresh if isinstance(fresh, bool) else None,
      hardware_samples=int(value.get("hardwareSamples", 0)),
      hardware_valid_samples=int(value.get("hardwareValidSamples", 0)),
      hardware_error_samples=int(value.get("hardwareErrorSamples", 0)),
      supply_fault_samples=int(value.get("supplyFaultSamples", 0)),
      p95_hardware_sample_duration_ms=float(value.get("p95HardwareSampleDurationMs", 0.0)),
      max_hardware_sample_duration_ms=float(value.get("maxHardwareSampleDurationMs", 0.0)),
      max_gpu_temp_c=finite_optional(value.get("maxGpuTempC")),
      max_memory_temp_c=finite_optional(value.get("maxMemoryTempC")),
      max_power_draw_w=finite_optional(value.get("maxPowerDrawW")),
    )

  def validate(self) -> None:
    if self.leg not in LEGS:
      raise ValueError(f"unexpected leg: {self.leg}")
    counts = (
      self.samples, self.frame_gaps, self.fallbacks, self.guard_violations,
      self.hardware_samples, self.hardware_valid_samples,
      self.hardware_error_samples, self.supply_fault_samples,
    )
    if any(v < 0 for v in counts):
      raise ValueError("counts must be non-negative")
    if self.hardware_valid_samples > self.hardware_samples:
      raise ValueError("hardwareValidSamples cannot exceed hardwareSamples")
    for value in (
      self.p50_ms, self.p95_ms, self.p99_ms, self.max_ms,
      self.p95_hardware_sample_duration_ms, self.max_hardware_sample_duration_ms,
    ):
      if not math.isfinite(value) or value < 0:
        raise ValueError("timing values must be finite and non-negative")
    if not (self.p50_ms <= self.p95_ms <= self.p99_ms <= self.max_ms):
      raise ValueError("model latency percentiles must be monotonic")
    if self.p95_hardware_sample_duration_ms > self.max_hardware_sample_duration_ms:
      raise ValueError("hardware sample duration percentile exceeds max")


@dataclass(frozen=True)
class S2APolicy:
  min_model_samples_per_leg: int
  min_hardware_samples_on_leg: int
  min_hardware_valid_fraction: float
  max_baseline_p95_drift_ms: float
  max_baseline_p99_drift_ms: float
  max_telemetry_p50_increase_ms: float
  max_telemetry_p95_increase_ms: float
  max_telemetry_p99_increase_ms: float
  max_additional_frame_gaps: int
  max_fallbacks_per_leg: int
  max_guard_violations_per_leg: int
  max_hardware_error_samples: int
  max_supply_fault_samples: int
  max_p95_hardware_sample_duration_ms: float
  max_gpu_temp_c: float | None
  max_memory_temp_c: float | None
  max_power_draw_w: float | None

  @classmethod
  def from_dict(cls, value: dict[str, Any]) -> "S2APolicy":
    return cls(
      min_model_samples_per_leg=int(value["minModelSamplesPerLeg"]),
      min_hardware_samples_on_leg=int(value["minHardwareSamplesOnLeg"]),
      min_hardware_valid_fraction=float(value["minHardwareValidFraction"]),
      max_baseline_p95_drift_ms=float(value["maxBaselineP95DriftMs"]),
      max_baseline_p99_drift_ms=float(value["maxBaselineP99DriftMs"]),
      max_telemetry_p50_increase_ms=float(value["maxTelemetryP50IncreaseMs"]),
      max_telemetry_p95_increase_ms=float(value["maxTelemetryP95IncreaseMs"]),
      max_telemetry_p99_increase_ms=float(value["maxTelemetryP99IncreaseMs"]),
      max_additional_frame_gaps=int(value["maxAdditionalFrameGaps"]),
      max_fallbacks_per_leg=int(value["maxFallbacksPerLeg"]),
      max_guard_violations_per_leg=int(value["maxGuardViolationsPerLeg"]),
      max_hardware_error_samples=int(value["maxHardwareErrorSamples"]),
      max_supply_fault_samples=int(value["maxSupplyFaultSamples"]),
      max_p95_hardware_sample_duration_ms=float(value["maxP95HardwareSampleDurationMs"]),
      max_gpu_temp_c=finite_optional(value["maxGpuTempC"]),
      max_memory_temp_c=finite_optional(value["maxMemoryTempC"]),
      max_power_draw_w=finite_optional(value["maxPowerDrawW"]),
    )

  def validate(self) -> None:
    if self.min_model_samples_per_leg <= 0 or self.min_hardware_samples_on_leg <= 0:
      raise ValueError("minimum sample counts must be positive")
    if not math.isfinite(self.min_hardware_valid_fraction) or not 0.0 <= self.min_hardware_valid_fraction <= 1.0:
      raise ValueError("minHardwareValidFraction must be in [0,1]")
    nonnegative = (
      self.max_baseline_p95_drift_ms, self.max_baseline_p99_drift_ms,
      self.max_telemetry_p50_increase_ms, self.max_telemetry_p95_increase_ms,
      self.max_telemetry_p99_increase_ms, self.max_p95_hardware_sample_duration_ms,
    )
    if any(not math.isfinite(v) or v < 0 for v in nonnegative):
      raise ValueError("timing limits must be finite and non-negative")
    count_limits = (
      self.max_additional_frame_gaps, self.max_fallbacks_per_leg,
      self.max_guard_violations_per_leg, self.max_hardware_error_samples,
      self.max_supply_fault_samples,
    )
    if any(v < 0 for v in count_limits):
      raise ValueError("count limits must be non-negative")
    for name, value in (
      ("maxGpuTempC", self.max_gpu_temp_c),
      ("maxMemoryTempC", self.max_memory_temp_c),
      ("maxPowerDrawW", self.max_power_draw_w),
    ):
      if value is not None and (not math.isfinite(value) or value <= 0):
        raise ValueError(f"{name} must be positive when not null")


def mean(a: float, b: float) -> float:
  return (a + b) / 2.0


def qualify(metrics: dict[str, S2AMetrics], policy: S2APolicy) -> dict[str, Any]:
  policy.validate()
  if set(metrics) != set(LEGS):
    return {"status": "HOLD", "reasons": ["all three S2A legs are required"]}
  for item in metrics.values():
    item.validate()

  before, on, after = (metrics[name] for name in LEGS)
  hold: list[str] = []
  hard_fail: list[str] = []
  attribution_fail: list[str] = []

  for item in (before, on, after):
    if item.samples < policy.min_model_samples_per_leg:
      hold.append(f"{item.leg}:sample_shortage")
    if item.guard_violations > policy.max_guard_violations_per_leg:
      hard_fail.append(f"{item.leg}:guard_violations")
    if item.fallbacks > policy.max_fallbacks_per_leg:
      hard_fail.append(f"{item.leg}:fallbacks")

  if on.telemetry_state_fresh is not True:
    hard_fail.append("telemetry_state_not_fresh")
  if on.hardware_samples < policy.min_hardware_samples_on_leg:
    hold.append("hardware_sample_shortage")
  valid_fraction = on.hardware_valid_samples / on.hardware_samples if on.hardware_samples else 0.0
  if on.hardware_samples and valid_fraction < policy.min_hardware_valid_fraction:
    hard_fail.append("hardware_valid_fraction")
  if on.hardware_error_samples > policy.max_hardware_error_samples:
    hard_fail.append("hardware_error_samples")
  if on.supply_fault_samples > policy.max_supply_fault_samples:
    hard_fail.append("supply_fault_samples")
  if on.p95_hardware_sample_duration_ms > policy.max_p95_hardware_sample_duration_ms:
    hard_fail.append("hardware_sample_duration_p95")

  for name, measured, limit in (
    ("gpu_temp", on.max_gpu_temp_c, policy.max_gpu_temp_c),
    ("memory_temp", on.max_memory_temp_c, policy.max_memory_temp_c),
    ("power_draw", on.max_power_draw_w, policy.max_power_draw_w),
  ):
    if limit is not None:
      if measured is None:
        hold.append(f"{name}_evidence_missing")
      elif measured > limit:
        hard_fail.append(f"{name}_over_policy")

  baseline_p95_drift = abs(before.p95_ms - after.p95_ms)
  baseline_p99_drift = abs(before.p99_ms - after.p99_ms)
  if baseline_p95_drift > policy.max_baseline_p95_drift_ms:
    hold.append("baseline_p95_drift")
  if baseline_p99_drift > policy.max_baseline_p99_drift_ms:
    hold.append("baseline_p99_drift")

  baseline = {
    "p50": mean(before.p50_ms, after.p50_ms),
    "p95": mean(before.p95_ms, after.p95_ms),
    "p99": mean(before.p99_ms, after.p99_ms),
    "frameGaps": max(before.frame_gaps, after.frame_gaps),
  }
  deltas = {
    "p50Ms": on.p50_ms - baseline["p50"],
    "p95Ms": on.p95_ms - baseline["p95"],
    "p99Ms": on.p99_ms - baseline["p99"],
    "additionalFrameGaps": on.frame_gaps - baseline["frameGaps"],
  }
  if deltas["p50Ms"] > policy.max_telemetry_p50_increase_ms:
    attribution_fail.append("telemetry_p50_increase")
  if deltas["p95Ms"] > policy.max_telemetry_p95_increase_ms:
    attribution_fail.append("telemetry_p95_increase")
  if deltas["p99Ms"] > policy.max_telemetry_p99_increase_ms:
    attribution_fail.append("telemetry_p99_increase")
  if deltas["additionalFrameGaps"] > policy.max_additional_frame_gaps:
    attribution_fail.append("telemetry_frame_gap_increase")

  if hard_fail:
    status = "FAIL"; fail = hard_fail + attribution_fail
  elif hold:
    status = "HOLD"; fail = []
  elif attribution_fail:
    status = "FAIL"; fail = attribution_fail
  else:
    status = "PASS"; fail = []

  return {
    "schemaVersion": 1,
    "stage": "S2A_TELEMETRY_ONLY_QUALIFICATION",
    "status": status,
    "holdReasons": hold,
    "failReasons": fail,
    "baselineDrift": {"p95Ms": baseline_p95_drift, "p99Ms": baseline_p99_drift},
    "telemetryDeltaVsMeanBaseline": deltas,
    "hardwareEvidence": {
      "samples": on.hardware_samples,
      "validSamples": on.hardware_valid_samples,
      "validFraction": valid_fraction,
      "errorSamples": on.hardware_error_samples,
      "supplyFaultSamples": on.supply_fault_samples,
      "p95SampleDurationMs": on.p95_hardware_sample_duration_ms,
      "maxGpuTempC": on.max_gpu_temp_c,
      "maxMemoryTempC": on.max_memory_temp_c,
      "maxPowerDrawW": on.max_power_draw_w,
    },
    "observerAuthorization": False,
    "controlAuthorization": False,
    "publicRoadAuthorization": False,
    "shadowAuthorization": False,
    "nextGate": "S2B_OBSERVER_TELEMETRY_COEXISTENCE_PLAN_ONLY" if status == "PASS" else None,
    "thresholdProvenance": "explicit-project-research-policy-not-official-comma-safety-thresholds",
  }


def main() -> int:
  ap = argparse.ArgumentParser()
  ap.add_argument("--evidence", type=Path, required=True)
  ap.add_argument("--policy", type=Path)
  ap.add_argument("--output", type=Path)
  args = ap.parse_args()
  if args.policy is None:
    report = {
      "schemaVersion": 1,
      "stage": "S2A_TELEMETRY_ONLY_QUALIFICATION",
      "status": "HOLD",
      "holdReasons": ["explicit policy file required"],
      "controlAuthorization": False,
      "publicRoadAuthorization": False,
      "nextGate": None,
    }
  else:
    evidence = json.loads(args.evidence.read_text(encoding="utf-8"))
    policy = S2APolicy.from_dict(json.loads(args.policy.read_text(encoding="utf-8")))
    values = evidence.get("legs", evidence)
    metrics = {name: S2AMetrics.from_dict(values[name]) for name in LEGS if name in values}
    report = qualify(metrics, policy)
  text = json.dumps(report, indent=2, ensure_ascii=False, allow_nan=False) + "\n"
  print(text, end="")
  if args.output:
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(text, encoding="utf-8")
  return 0 if report["status"] == "PASS" else (1 if report["status"] == "HOLD" else 2)


if __name__ == "__main__":
  raise SystemExit(main())
