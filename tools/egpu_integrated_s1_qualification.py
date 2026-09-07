#!/usr/bin/env python3
"""Qualify observer-only S1 evidence using explicit research limits.

No default acceptance thresholds are embedded. Missing policy/evidence yields HOLD.
PASS only opens the next telemetry plan stage; it never authorizes controls.
"""
from __future__ import annotations

from dataclasses import dataclass
import argparse
import json
import math
from pathlib import Path
from typing import Any

LEGS = ("S1_OFF_BEFORE", "S1_ON", "S1_OFF_AFTER")


@dataclass(frozen=True)
class S1Metrics:
  leg: str
  samples: int
  p50_ms: float
  p95_ms: float
  p99_ms: float
  max_ms: float
  frame_gaps: int
  fallbacks: int
  guard_violations: int
  observer_write_errors: int
  observer_state_fresh: bool | None

  @classmethod
  def from_dict(cls, value: dict[str, Any]) -> "S1Metrics":
    fresh_raw = value.get("observerStateFresh")
    fresh = fresh_raw if isinstance(fresh_raw, bool) else None
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
      observer_write_errors=int(value.get("observerWriteErrors", 0)),
      observer_state_fresh=fresh,
    )

  def validate(self) -> None:
    if self.leg not in LEGS:
      raise ValueError(f"unexpected leg: {self.leg}")
    if self.samples < 0 or self.frame_gaps < 0 or self.fallbacks < 0 or self.guard_violations < 0 or self.observer_write_errors < 0:
      raise ValueError("counts must be non-negative")
    for value in (self.p50_ms, self.p95_ms, self.p99_ms, self.max_ms):
      if not math.isfinite(value) or value < 0:
        raise ValueError("latency values must be finite and non-negative")
    if not (self.p50_ms <= self.p95_ms <= self.p99_ms <= self.max_ms):
      raise ValueError("latency percentiles must be monotonic")


@dataclass(frozen=True)
class S1Policy:
  min_samples_per_leg: int
  max_baseline_p95_drift_ms: float
  max_baseline_p99_drift_ms: float
  max_observer_p50_increase_ms: float
  max_observer_p95_increase_ms: float
  max_observer_p99_increase_ms: float
  max_additional_frame_gaps: int
  max_fallbacks_per_leg: int
  max_guard_violations_per_leg: int
  max_observer_write_errors: int

  @classmethod
  def from_dict(cls, value: dict[str, Any]) -> "S1Policy":
    return cls(
      min_samples_per_leg=int(value["minSamplesPerLeg"]),
      max_baseline_p95_drift_ms=float(value["maxBaselineP95DriftMs"]),
      max_baseline_p99_drift_ms=float(value["maxBaselineP99DriftMs"]),
      max_observer_p50_increase_ms=float(value["maxObserverP50IncreaseMs"]),
      max_observer_p95_increase_ms=float(value["maxObserverP95IncreaseMs"]),
      max_observer_p99_increase_ms=float(value["maxObserverP99IncreaseMs"]),
      max_additional_frame_gaps=int(value["maxAdditionalFrameGaps"]),
      max_fallbacks_per_leg=int(value["maxFallbacksPerLeg"]),
      max_guard_violations_per_leg=int(value["maxGuardViolationsPerLeg"]),
      max_observer_write_errors=int(value["maxObserverWriteErrors"]),
    )

  def validate(self) -> None:
    if self.min_samples_per_leg <= 0:
      raise ValueError("minSamplesPerLeg must be positive")
    numeric = (
      self.max_baseline_p95_drift_ms, self.max_baseline_p99_drift_ms,
      self.max_observer_p50_increase_ms, self.max_observer_p95_increase_ms,
      self.max_observer_p99_increase_ms,
    )
    if any(not math.isfinite(v) or v < 0 for v in numeric):
      raise ValueError("latency policy limits must be finite and non-negative")
    counts = (
      self.max_additional_frame_gaps, self.max_fallbacks_per_leg,
      self.max_guard_violations_per_leg, self.max_observer_write_errors,
    )
    if any(v < 0 for v in counts):
      raise ValueError("count policy limits must be non-negative")


def _mean(a: float, b: float) -> float:
  return (a + b) / 2.0


def qualify(metrics: dict[str, S1Metrics], policy: S1Policy) -> dict[str, Any]:
  policy.validate()
  if set(metrics) != set(LEGS):
    return {"status": "HOLD", "reasons": ["all three S1 legs are required"]}
  for item in metrics.values():
    item.validate()

  before, on, after = (metrics[name] for name in LEGS)
  hold: list[str] = []
  hard_fail: list[str] = []
  attribution_fail: list[str] = []

  for item in (before, on, after):
    if item.samples < policy.min_samples_per_leg:
      hold.append(f"{item.leg}:sample_shortage")
    if item.guard_violations > policy.max_guard_violations_per_leg:
      hard_fail.append(f"{item.leg}:guard_violations")
    if item.fallbacks > policy.max_fallbacks_per_leg:
      hard_fail.append(f"{item.leg}:fallbacks")

  if on.observer_state_fresh is not True:
    hard_fail.append("observer_state_not_fresh")

  baseline_p95_drift = abs(before.p95_ms - after.p95_ms)
  baseline_p99_drift = abs(before.p99_ms - after.p99_ms)
  if baseline_p95_drift > policy.max_baseline_p95_drift_ms:
    hold.append("baseline_p95_drift")
  if baseline_p99_drift > policy.max_baseline_p99_drift_ms:
    hold.append("baseline_p99_drift")

  baseline = {
    "p50": _mean(before.p50_ms, after.p50_ms),
    "p95": _mean(before.p95_ms, after.p95_ms),
    "p99": _mean(before.p99_ms, after.p99_ms),
    "frameGaps": max(before.frame_gaps, after.frame_gaps),
  }
  deltas = {
    "p50Ms": on.p50_ms - baseline["p50"],
    "p95Ms": on.p95_ms - baseline["p95"],
    "p99Ms": on.p99_ms - baseline["p99"],
    "additionalFrameGaps": on.frame_gaps - baseline["frameGaps"],
  }

  if deltas["p50Ms"] > policy.max_observer_p50_increase_ms:
    attribution_fail.append("observer_p50_increase")
  if deltas["p95Ms"] > policy.max_observer_p95_increase_ms:
    attribution_fail.append("observer_p95_increase")
  if deltas["p99Ms"] > policy.max_observer_p99_increase_ms:
    attribution_fail.append("observer_p99_increase")
  if deltas["additionalFrameGaps"] > policy.max_additional_frame_gaps:
    attribution_fail.append("observer_frame_gap_increase")
  if on.observer_write_errors > policy.max_observer_write_errors:
    hard_fail.append("observer_write_errors")

  # Hard operational faults remain FAIL. Otherwise unstable/incomplete baselines
  # take precedence over attribution-based regressions because causality is not established.
  if hard_fail:
    status = "FAIL"
    fail = hard_fail + attribution_fail
  elif hold:
    status = "HOLD"
    fail = []
  elif attribution_fail:
    status = "FAIL"
    fail = attribution_fail
  else:
    status = "PASS"
    fail = []

  return {
    "schemaVersion": 1,
    "stage": "S1_OBSERVER_QUALIFICATION",
    "status": status,
    "holdReasons": hold,
    "failReasons": fail,
    "baselineDrift": {"p95Ms": baseline_p95_drift, "p99Ms": baseline_p99_drift},
    "observerDeltaVsMeanBaseline": deltas,
    "observerStateFresh": on.observer_state_fresh,
    "controlAuthorization": False,
    "publicRoadAuthorization": False,
    "telemetryAuthorization": False,
    "shadowAuthorization": False,
    "nextGate": "S2_TELEMETRY_PLAN_ONLY" if status == "PASS" else None,
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
      "stage": "S1_OBSERVER_QUALIFICATION",
      "status": "HOLD",
      "holdReasons": ["explicit policy file required"],
      "controlAuthorization": False,
      "publicRoadAuthorization": False,
      "nextGate": None,
    }
  else:
    evidence = json.loads(args.evidence.read_text(encoding="utf-8"))
    policy = S1Policy.from_dict(json.loads(args.policy.read_text(encoding="utf-8")))
    values = evidence.get("legs", evidence)
    metrics = {name: S1Metrics.from_dict(values[name]) for name in LEGS if name in values}
    report = qualify(metrics, policy)

  text = json.dumps(report, indent=2, ensure_ascii=False) + "\n"
  print(text, end="")
  if args.output:
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(text, encoding="utf-8")
  return 0 if report["status"] == "PASS" else (1 if report["status"] == "HOLD" else 2)


if __name__ == "__main__":
  raise SystemExit(main())
