#!/usr/bin/env python3
"""Synthetic fault-injection harness for integrated Carrot-WIP eGPU semantics.

No hardware or model process is touched. Scenarios feed pure observations into
the existing fault tracker and Guardian replay analyzer. PASS means the injected
evidence received the expected classification, never a runtime safety approval.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict
import json
from typing import Any

from openpilot.selfdrive.modeld.egpu_integrated_fault_state import FaultObservation, FaultSequenceTracker
from openpilot.selfdrive.modeld.egpu_integrated_guardian import GuardianPolicy
from tools.egpu_integrated_guardian_replay import EXPECTED_BRANCH, analyze_rows


SYNTHETIC_HEAD = "a" * 40
# Reuse and report the existing review policy. No vehicle safety limits are set
# by this harness; stale fixtures are derived from these explicit test inputs.
TEST_GUARDIAN_POLICY = GuardianPolicy()


def obs(frame: int, attempted: str, active: str, *, usb_active: bool, startup_failed: bool,
        fallback: bool = False, output: bool = True, **kwargs: Any) -> FaultObservation:
  if fallback and output:
    kwargs.setdefault("output_frame_id", frame)
    kwargs.setdefault("model_output_finite", True)
  return FaultObservation(
    frame_id=frame,
    attempted_backend=attempted,
    active_backend=active,
    usbgpu_active=usb_active,
    startup_failed=startup_failed,
    fallback_observed=fallback,
    model_output_present=output,
    **kwargs,
  )


def replay_row(frame: int, *, active_backend: str = "egpu", fault: dict[str, Any] | None = None,
               stop_shadow: bool = False, cutout_time: float | None = None,
               cutout_confidence: float | None = None) -> dict[str, Any]:
  timestamp = 100.0 + frame / 20.0
  def action(backend: str, stop: bool) -> dict[str, Any]:
    return {
      "frameId": frame, "frameAge": 0, "modelExecutionMs": 30.0,
      "curvature": 0.001, "acceleration": -0.2, "shouldStop": stop,
      "backend": backend, "timestampMonoS": timestamp,
    }
  row = {
    "sourceHead": SYNTHETIC_HEAD,
    "sourceBranch": EXPECTED_BRANCH,
    "active": action(active_backend, False),
    "shadow": action("qcom", stop_shadow),
    "scene": {
      "frameId": frame, "timestampMonoS": timestamp, "speedMps": 10.0,
      "standstill": False, "leadPresent": True, "leadDistanceM": 25.0,
      "leadRelSpeedMps": 0.0, "leadCutOutTimeS": cutout_time,
      "leadCutOutConfidence": cutout_confidence,
    },
    "hardware": {"valid": True, "timestampMonoS": timestamp, "supplyFault": False},
  }
  if fault is not None:
    row["fault"] = fault
  return row


def fallback_fault(frame: int, **changes: Any) -> dict[str, Any]:
  return {
    "frameId": frame, "attemptedBackend": "egpu", "activeBackend": "qcom",
    "usbGpuActive": False, "startupFailed": True, "fallbackObserved": True,
    "modelOutputPresent": True, "outputFrameId": frame, "modelOutputFinite": True,
    **changes,
  }


SCENARIOS: dict[str, dict[str, Any]] = {
  "runtime_exception_same_frame": {
    "observations": [
      obs(100, "egpu", "egpu", usb_active=True, startup_failed=False),
      obs(101, "egpu", "qcom", usb_active=False, startup_failed=True, fallback=True, output=True),
      obs(102, "qcom", "qcom", usb_active=False, startup_failed=True),
    ],
    "expectedStates": ["BIG_ACTIVE", "SAME_FRAME_FALLBACK", "SMALL_LATCHED"],
    "expectedHardIssueCount": 0,
  },
  "fallback_output_missing": {
    "observations": [
      obs(200, "egpu", "egpu", usb_active=True, startup_failed=False),
      obs(201, "egpu", "qcom", usb_active=False, startup_failed=True, fallback=True, output=False),
    ],
    "expectedStates": ["BIG_ACTIVE", "INCONSISTENT"],
    "requiredHardIssue": "same_frame_output_missing",
  },
  "unexpected_big_reentry": {
    "observations": [
      obs(300, "egpu", "egpu", usb_active=True, startup_failed=False),
      obs(301, "egpu", "qcom", usb_active=False, startup_failed=True, fallback=True),
      obs(302, "qcom", "qcom", usb_active=False, startup_failed=True),
      obs(303, "egpu", "egpu", usb_active=True, startup_failed=False),
    ],
    "expectedStates": ["BIG_ACTIVE", "SAME_FRAME_FALLBACK", "SMALL_LATCHED", "BIG_ACTIVE"],
    "requiredHardIssue": "unexpected_big_reentry_without_restart_boundary",
  },
  "small_startup_without_big": {
    "observations": [
      obs(400, "qcom", "qcom", usb_active=False, startup_failed=False, hardware_present=False, compiled_big=True),
      obs(401, "qcom", "qcom", usb_active=False, startup_failed=False, hardware_present=False, compiled_big=True),
    ],
    "expectedStates": ["STARTUP_SMALL", "STARTUP_SMALL"],
    "expectedHardIssueCount": 0,
  },
  "pcie_degraded_evidence_only": {
    "observations": [
      obs(500, "egpu", "egpu", usb_active=True, startup_failed=False, pcie_ready=False, telemetry_valid=True),
    ],
    "expectedStates": ["BIG_ACTIVE"],
    "requiredReviewIssue": "pcie_not_ready",
  },
  "egpu_disconnect_while_active": {
    "observations": [
      obs(600, "egpu", "egpu", usb_active=True, startup_failed=False, hardware_present=True),
      obs(601, "egpu", "qcom", usb_active=False, startup_failed=True, fallback=True, hardware_present=False),
      obs(602, "qcom", "qcom", usb_active=False, startup_failed=True, hardware_present=False),
    ],
    "expectedStates": ["BIG_ACTIVE", "SAME_FRAME_FALLBACK", "SMALL_LATCHED"],
    "expectedHardIssueCount": 0,
  },
  "usb_pcie_degraded_big_returns": {
    "observations": [
      obs(700, "egpu", "egpu", usb_active=True, startup_failed=False, pcie_ready=True, usb_speed_mbps=5000),
      obs(701, "egpu", "egpu", usb_active=True, startup_failed=False, pcie_ready=False, usb_speed_mbps=480),
    ],
    "expectedStates": ["BIG_ACTIVE", "BIG_ACTIVE"],
    "expectedHardIssueCount": 0,
    "requiredReviewIssues": ["pcie_not_ready", "usb_below_superspeed_5g"],
  },
  "fallback_output_stale_frame": {
    "observations": [obs(800, "egpu", "qcom", usb_active=False, startup_failed=True, fallback=True, frame_age=1)],
    "expectedStates": ["INCONSISTENT"],
    "requiredHardIssue": "same_frame_output_stale",
  },
  "fallback_output_frame_id_mismatch": {
    "observations": [obs(810, "egpu", "qcom", usb_active=False, startup_failed=True, fallback=True, output_frame_id=809)],
    "expectedStates": ["INCONSISTENT"],
    "requiredHardIssue": "same_frame_output_frame_mismatch",
  },
  "fallback_output_nonfinite": {
    "observations": [obs(820, "egpu", "qcom", usb_active=False, startup_failed=True, fallback=True, model_output_finite=False)],
    "expectedStates": ["INCONSISTENT"],
    "requiredHardIssue": "model_output_nonfinite",
  },
  "repeated_big_exception": {
    "observations": [
      obs(900, "egpu", "egpu", usb_active=True, startup_failed=False),
      obs(901, "egpu", "qcom", usb_active=False, startup_failed=True, fallback=True),
      obs(902, "egpu", "qcom", usb_active=False, startup_failed=True, fallback=True),
    ],
    "requiredHardIssue": "repeated_big_attempt_while_small_latched",
  },
  "small_fallback_itself_fails": {
    "observations": [
      obs(910, "egpu", "egpu", usb_active=True, startup_failed=False),
      obs(911, "egpu", "qcom", usb_active=False, startup_failed=True, fallback=True, output=False),
      obs(912, "qcom", "qcom", usb_active=False, startup_failed=True, output=False),
    ],
    "expectedStates": ["BIG_ACTIVE", "INCONSISTENT", "INCONSISTENT"],
    "requiredHardIssues": ["same_frame_output_missing", "model_output_missing"],
  },
}


def _add_replay_scenarios() -> None:
  stale = replay_row(1000, active_backend="qcom", fault=fallback_fault(1000))
  stale["active"]["frameAge"] = TEST_GUARDIAN_POLICY.max_frame_age + 1
  SCENARIOS["fallback_action_stale_under_review_policy"] = {
    "rows": [stale], "requiredHardIssue": "active_frame_stale", "expectedBuckets": ["ROOT_CAUSE"],
  }

  mismatch = replay_row(1010, active_backend="qcom", fault=fallback_fault(1010))
  mismatch["active"]["frameId"] = 1009
  SCENARIOS["fallback_replay_frame_id_mismatch"] = {
    "rows": [mismatch], "requiredHardIssues": ["frame_mismatch", "fault_guardian_frame_mismatch"],
    "expectedBuckets": ["ROOT_CAUSE"],
  }

  for label, value in (("nan", float("nan")), ("inf", float("inf"))):
    for side in ("active", "shadow"):
      row = replay_row(1020)
      row[side]["curvature"] = value
      SCENARIOS[f"{side}_{label}_output"] = {
        "rows": [row], "requiredHardIssue": f"{side}_nonfinite", "expectedBuckets": ["ROOT_CAUSE"],
      }

  telemetry = replay_row(1030)
  assert TEST_GUARDIAN_POLICY.max_hardware_age_s is not None
  telemetry["hardware"]["timestampMonoS"] -= TEST_GUARDIAN_POLICY.max_hardware_age_s + 1.0
  SCENARIOS["stale_hardware_telemetry"] = {
    "rows": [telemetry], "requiredHardIssue": "hardware_evidence_stale", "expectedBuckets": ["ROOT_CAUSE"],
  }

  supply = replay_row(1040, fault={
    "frameId": 1040, "attemptedBackend": "egpu", "activeBackend": "egpu",
    "usbGpuActive": True, "startupFailed": False, "supplyFault": True,
  })
  supply["hardware"]["supplyFault"] = True
  SCENARIOS["supply_fault_big_output_normal"] = {
    "rows": [supply], "requiredHardIssue": "egpu_supply_fault",
    "requiredReviewIssue": "supply_fault_observed", "expectedStates": ["BIG_ACTIVE"],
    "expectedBuckets": ["ROOT_CAUSE"],
  }

  restart_rows = [
    replay_row(1050, active_backend="qcom", fault=fallback_fault(1050)),
    replay_row(1051, active_backend="qcom", fault={
      "frameId": 1051, "attemptedBackend": "qcom", "activeBackend": "qcom",
      "usbGpuActive": False, "startupFailed": True,
    }),
    replay_row(1, fault={
      "frameId": 1, "attemptedBackend": "egpu", "activeBackend": "egpu",
      "usbGpuActive": True, "startupFailed": False,
    }),
  ]
  for index, row in enumerate(restart_rows):
    row["processId"] = "synthetic-process-after" if index == 2 else "synthetic-process-before"
    row["restartBoundary"] = index == 2
  SCENARIOS["restart_boundary_big_returns"] = {
    "rows": restart_rows, "expectedStates": ["SAME_FRAME_FALLBACK", "SMALL_LATCHED", "BIG_ACTIVE"],
    "expectedHardIssueCount": 0,
  }

  for field, changed in (("sourceHead", "b" * 40), ("sourceBranch", "unreviewed-branch")):
    row = replay_row(1060)
    row[field] = changed
    SCENARIOS[f"replay_{field}_mismatch"] = {
      "rows": [row], "expectedRejection": "source identity mismatch",
    }
  SCENARIOS["observer_replay_active_backend_mismatch"] = {
    "rows": [replay_row(1070, active_backend="egpu", fault=fallback_fault(1070))],
    "requiredHardIssue": "fault_guardian_active_backend_mismatch", "expectedBuckets": ["ROOT_CAUSE"],
  }

  for name, time_s, confidence, issues in (
    ("invalid", -0.1, 1.2, ["lead_cutout_time_negative", "lead_cutout_confidence_out_of_range"]),
    ("nan_time", float("nan"), 0.5, ["lead_cutout_time_nonfinite"]),
    ("nan_confidence", 0.7, float("nan"), ["lead_cutout_confidence_nonfinite"]),
    ("inf_time", float("inf"), 0.5, ["lead_cutout_time_nonfinite"]),
  ):
    SCENARIOS[f"cutout_metadata_{name}"] = {
      "rows": [replay_row(1080, cutout_time=time_s, cutout_confidence=confidence)],
      "requiredHardIssues": issues, "expectedBuckets": ["ROOT_CAUSE"],
    }
  SCENARIOS["cutout_onset_with_stop_disagreement"] = {
    "rows": [
      replay_row(1090, cutout_time=0.0, cutout_confidence=0.0),
      replay_row(1091, stop_shadow=True, cutout_time=0.7, cutout_confidence=0.35),
    ],
    "expectedBuckets": ["OBSERVE", "PRIORITY_REVIEW"], "expectedHardIssueCount": 0,
    "requiredTemporalTags": ["lead_cutout_prediction_onset", "stop_disagreement_onset"],
  }


_add_replay_scenarios()


def run_scenario(name: str) -> dict[str, Any]:
  if name not in SCENARIOS:
    raise KeyError(name)
  spec = SCENARIOS[name]
  reasons: list[str] = []
  rejection: str | None = None
  buckets: list[str] = []
  if "rows" in spec:
    try:
      events, _ = analyze_rows(spec["rows"], expected_source_head=SYNTHETIC_HEAD, guardian_policy=TEST_GUARDIAN_POLICY)
    except ValueError as exc:
      if not spec.get("expectedRejection") or spec["expectedRejection"] not in str(exc):
        raise
      rejection = str(exc)
      events = []
    if spec.get("expectedRejection") and rejection is None:
      reasons.append("expected_source_rejection_missing")
    buckets = [event["combinedReviewBucket"] for event in events]
    assessments = [part for event in events for part in (event["guardian"], event["fault"]) if part is not None]
    states = [event["fault"]["state"] for event in events if event["fault"] is not None]
  else:
    tracker = FaultSequenceTracker()
    events = [tracker.observe(item) for item in spec["observations"]]
    assessments = events
    states = [event["state"] for event in events]
  hard = [issue for part in assessments for issue in part["hardIssues"]]
  review = [issue for part in assessments for issue in part["reviewIssues"]]
  temporal = [tag for part in assessments for tag in part.get("temporalTags", [])]

  if "expectedStates" in spec and states != spec["expectedStates"]:
    reasons.append("state_sequence_mismatch")
  if "expectedBuckets" in spec and buckets != spec["expectedBuckets"]:
    reasons.append("review_bucket_sequence_mismatch")
  if "expectedHardIssueCount" in spec and len(hard) != int(spec["expectedHardIssueCount"]):
    reasons.append("hard_issue_count_mismatch")
  if spec.get("requiredHardIssue") and spec["requiredHardIssue"] not in hard:
    reasons.append("required_hard_issue_missing")
  if spec.get("requiredReviewIssue") and spec["requiredReviewIssue"] not in review:
    reasons.append("required_review_issue_missing")
  for field, actual in (("requiredHardIssues", hard), ("requiredReviewIssues", review), ("requiredTemporalTags", temporal)):
    for expected in spec.get(field, []):
      if expected not in actual:
        reasons.append(f"{field}_missing:{expected}")

  return {
    "schemaVersion": 1,
    "stage": "SYNTHETIC_FAULT_INJECTION",
    "scenario": name,
    "status": "PASS" if not reasons else "FAIL",
    "reasons": reasons,
    "states": states,
    "reviewBuckets": buckets,
    "rejection": rejection,
    "hardIssues": hard,
    "reviewIssues": review,
    "temporalTags": temporal,
    "events": events,
    "testGuardianPolicy": asdict(TEST_GUARDIAN_POLICY) if "rows" in spec else None,
    "policyProvenance": "existing-research-review-policy-not-vehicle-safety-limits",
    "evidenceScope": "synthetic-classification-only-no-runtime-fault-executed",
    "hardwareTouched": False,
    "controlAuthorization": False,
    "publicRoadAuthorization": False,
  }


def main() -> int:
  ap = argparse.ArgumentParser()
  ap.add_argument("--scenario", choices=sorted(SCENARIOS))
  ap.add_argument("--all", action="store_true")
  args = ap.parse_args()
  if not args.all and args.scenario is None:
    raise SystemExit("provide --scenario NAME or --all")
  names = sorted(SCENARIOS) if args.all else [args.scenario]
  results = [run_scenario(name) for name in names]
  report = {
    "schemaVersion": 1,
    "stage": "SYNTHETIC_FAULT_INJECTION_SUITE",
    "status": "PASS" if all(x["status"] == "PASS" for x in results) else "FAIL",
    "results": results,
    "hardwareTouched": False,
    "controlAuthorization": False,
    "publicRoadAuthorization": False,
  }
  print(json.dumps(report, indent=2, ensure_ascii=False, allow_nan=False))
  return 0 if report["status"] == "PASS" else 2


if __name__ == "__main__":
  raise SystemExit(main())
