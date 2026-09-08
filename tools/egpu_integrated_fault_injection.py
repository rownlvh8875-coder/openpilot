#!/usr/bin/env python3
"""Synthetic fault-injection harness for integrated Carrot-WIP eGPU semantics.

No hardware or model process is touched. Scenarios feed pure observations into
the observe-only FaultSequenceTracker and verify expected state transitions.
"""
from __future__ import annotations

import argparse
import json
from typing import Any

from openpilot.selfdrive.modeld.egpu_integrated_fault_state import FaultObservation, FaultSequenceTracker


def obs(frame: int, attempted: str, active: str, *, usb_active: bool, startup_failed: bool,
        fallback: bool = False, output: bool = True, **kwargs: Any) -> FaultObservation:
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
}


def run_scenario(name: str) -> dict[str, Any]:
  if name not in SCENARIOS:
    raise KeyError(name)
  spec = SCENARIOS[name]
  tracker = FaultSequenceTracker()
  events = [tracker.observe(item) for item in spec["observations"]]
  states = [event["state"] for event in events]
  hard = [issue for event in events for issue in event["hardIssues"]]
  review = [issue for event in events for issue in event["reviewIssues"]]

  reasons: list[str] = []
  if states != spec["expectedStates"]:
    reasons.append("state_sequence_mismatch")
  if "expectedHardIssueCount" in spec and len(hard) != int(spec["expectedHardIssueCount"]):
    reasons.append("hard_issue_count_mismatch")
  if spec.get("requiredHardIssue") and spec["requiredHardIssue"] not in hard:
    reasons.append("required_hard_issue_missing")
  if spec.get("requiredReviewIssue") and spec["requiredReviewIssue"] not in review:
    reasons.append("required_review_issue_missing")

  return {
    "schemaVersion": 1,
    "stage": "SYNTHETIC_FAULT_INJECTION",
    "scenario": name,
    "status": "PASS" if not reasons else "FAIL",
    "reasons": reasons,
    "states": states,
    "hardIssues": hard,
    "reviewIssues": review,
    "events": events,
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
  print(json.dumps(report, indent=2, ensure_ascii=False))
  return 0 if report["status"] == "PASS" else 2


if __name__ == "__main__":
  raise SystemExit(main())
