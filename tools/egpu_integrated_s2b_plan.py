#!/usr/bin/env python3
"""Generate plan-only S2B observer+telemetry coexistence commissioning.

S2B is not executable until S1 and S2A qualifications are PASS and their
source-bound evidence matches the exact expected integrated-v6 revision.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

EXPECTED_BRANCH = "carrot-wip-integrated-v6"


def build_s2b_plan(s1: dict[str, Any], s2a: dict[str, Any], s2a_evidence: dict[str, Any], *,
                   expected_head: str, expected_branch: str = EXPECTED_BRANCH) -> dict[str, Any]:
  if s1.get("status") != "PASS" or s1.get("nextGate") != "S2_TELEMETRY_PLAN_ONLY":
    raise ValueError("S1 qualification must be PASS with S2 telemetry nextGate")
  if s2a.get("status") != "PASS" or s2a.get("nextGate") != "S2B_OBSERVER_TELEMETRY_COEXISTENCE_PLAN_ONLY":
    raise ValueError("S2A qualification must be PASS with coexistence nextGate")
  for name, value in (
    ("s1.controlAuthorization", s1.get("controlAuthorization")),
    ("s2a.controlAuthorization", s2a.get("controlAuthorization")),
  ):
    if value is not False:
      raise ValueError(f"{name} must remain false")

  if s1.get("sourceHead") != expected_head or s1.get("sourceBranch") != expected_branch:
    raise ValueError("S1 source identity does not match expected integrated source")
  if s2a_evidence.get("sourceHead") != expected_head or s2a_evidence.get("sourceBranch") != expected_branch:
    raise ValueError("S2A evidence source identity does not match expected integrated source")
  if s2a_evidence.get("stage") != "S2A_TELEMETRY_ONLY_EVIDENCE":
    raise ValueError("S2A evidence stage mismatch")
  if s2a_evidence.get("controlAuthorization") is not False:
    raise ValueError("S2A evidence controlAuthorization must remain false")

  return {
    "schemaVersion": 2,
    "stage": "S2B_OBSERVER_TELEMETRY_COEXISTENCE_PLAN_ONLY",
    "expectedHead": expected_head,
    "expectedBranch": expected_branch,
    "sourceHead": expected_head,
    "sourceBranch": expected_branch,
    "purpose": "measure incremental telemetry overhead when the already-qualified observer is also enabled",
    "sequence": [
      {"id": "S2B_OBSERVER_ONLY_BEFORE", "observer": True, "telemetry": False, "shadow": False, "stationaryOnly": True, "controlsInactive": True},
      {"id": "S2B_OBSERVER_TELEMETRY_ON", "observer": True, "telemetry": True, "shadow": False, "stationaryOnly": True, "controlsInactive": True},
      {"id": "S2B_OBSERVER_ONLY_AFTER", "observer": True, "telemetry": False, "shadow": False, "stationaryOnly": True, "controlsInactive": True},
    ],
    "restartPolicy": {"fullDeviceRebootBetweenLegs": True, "rebootAuthorization": False},
    "developmentBoundary": {
      "recorderImplemented": False,
      "qualificationImplemented": False,
      "reason": "S2B execution tooling remains intentionally deferred until real S2A device evidence passes",
    },
    "authorizations": {
      "observerEnableAuthorization": False,
      "telemetryEnableAuthorization": False,
      "rebootAuthorization": False,
      "shadowAuthorization": False,
      "publicRoadAuthorization": False,
      "controlAuthorization": False,
    },
    "nextGateOnFuturePass": "S4B_PARKED_SHADOW_LOAD_PROBE_REVIEW",
  }


def main() -> int:
  ap = argparse.ArgumentParser()
  ap.add_argument("--s1-qualification", type=Path, required=True)
  ap.add_argument("--s2a-qualification", type=Path, required=True)
  ap.add_argument("--s2a-evidence", type=Path, required=True)
  ap.add_argument("--expected-head", required=True)
  ap.add_argument("--expected-branch", default=EXPECTED_BRANCH)
  ap.add_argument("--output", type=Path)
  args = ap.parse_args()
  s1 = json.loads(args.s1_qualification.read_text(encoding="utf-8"))
  s2a = json.loads(args.s2a_qualification.read_text(encoding="utf-8"))
  s2a_evidence = json.loads(args.s2a_evidence.read_text(encoding="utf-8"))
  try:
    plan = build_s2b_plan(s1, s2a, s2a_evidence, expected_head=args.expected_head, expected_branch=args.expected_branch)
  except ValueError as exc:
    print(json.dumps({"status": "HOLD", "reason": str(exc)}, indent=2))
    return 2
  text = json.dumps(plan, indent=2, ensure_ascii=False) + "\n"
  print(text, end="")
  if args.output:
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(text, encoding="utf-8")
  return 0


if __name__ == "__main__":
  raise SystemExit(main())
