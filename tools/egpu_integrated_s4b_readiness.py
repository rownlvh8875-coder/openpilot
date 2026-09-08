#!/usr/bin/env python3
"""Evaluate whether a completed commissioning ledger may open S4B plan review.

PASS here is still plan/review-only. It never starts shadow inference, changes
feature markers, reboots, publishes controls, or authorizes public-road use.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

EXPECTED_BRANCH = "carrot-wip-integrated-v6"


def evaluate(ledger: dict[str, Any], *, expected_head: str, expected_branch: str = EXPECTED_BRANCH) -> dict[str, Any]:
  reasons: list[str] = []
  if ledger.get("stage") != "INTEGRATED_COMMISSIONING_LEDGER":
    reasons.append("ledger_stage_mismatch")
  if ledger.get("status") != "READY_S4B_REVIEW":
    reasons.append("ledger_not_ready_s4b_review")
  if ledger.get("s4bReviewEligible") is not True:
    reasons.append("s4b_review_not_eligible")
  if ledger.get("sourceHead") != expected_head or ledger.get("sourceBranch") != expected_branch:
    reasons.append("source_identity_mismatch")
  if ledger.get("missingRequirements") not in ([], None):
    reasons.append("ledger_has_missing_requirements")
  if ledger.get("nextGate") != "S4B_PARKED_SHADOW_LOAD_PROBE_REVIEW":
    reasons.append("ledger_next_gate_mismatch")

  auth = ledger.get("authorizations", {})
  for name in (
    "installAuthorization", "rebootAuthorization", "observerEnableAuthorization",
    "telemetryEnableAuthorization", "shadowAuthorization", "publicRoadAuthorization",
    "controlAuthorization",
  ):
    if auth.get(name) is not False:
      reasons.append(f"authorization_boundary:{name}")

  completed = ledger.get("completedStages")
  required_stages = (
    "POSTBOOT_ALL_FEATURES_OFF",
    "S1_OBSERVER_QUALIFICATION",
    "S2A_TELEMETRY_ONLY_QUALIFICATION",
    "S2B_OBSERVER_TELEMETRY_COEXISTENCE_QUALIFICATION",
  )
  names = [item.get("stage") for item in completed if isinstance(item, dict)] if isinstance(completed, list) else []
  if names != list(required_stages):
    reasons.append("completed_stage_chain_mismatch")

  passed = not reasons
  return {
    "schemaVersion": 1,
    "stage": "S4B_REVIEW_READINESS",
    "status": "PASS" if passed else "HOLD",
    "sourceHead": expected_head,
    "sourceBranch": expected_branch,
    "reasons": reasons,
    "nextGate": "S4B_PARKED_SHADOW_LOAD_PROBE_PLAN_ONLY" if passed else None,
    "authorizations": {
      "shadowExecutionAuthorization": False,
      "observerEnableAuthorization": False,
      "telemetryEnableAuthorization": False,
      "rebootAuthorization": False,
      "publicRoadAuthorization": False,
      "controlAuthorization": False,
    },
    "interpretation": "PASS means only that S4B plan review may be generated; it does not authorize execution.",
  }


def main() -> int:
  ap = argparse.ArgumentParser()
  ap.add_argument("--ledger", type=Path, required=True)
  ap.add_argument("--expected-head", required=True)
  ap.add_argument("--expected-branch", default=EXPECTED_BRANCH)
  ap.add_argument("--output", type=Path)
  args = ap.parse_args()
  ledger = json.loads(args.ledger.read_text(encoding="utf-8"))
  report = evaluate(ledger, expected_head=args.expected_head, expected_branch=args.expected_branch)
  text = json.dumps(report, indent=2, ensure_ascii=False) + "\n"
  print(text, end="")
  if args.output:
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(text, encoding="utf-8")
  return 0 if report["status"] == "PASS" else 2


if __name__ == "__main__":
  raise SystemExit(main())
