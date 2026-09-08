#!/usr/bin/env python3
"""Generate plan-only S2B observer+telemetry coexistence commissioning.

S2B remains non-executable. It opens only when bound S1 and S2A qualifications
were recomputed from exact evidence/policy artifacts on the same source revision.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

EXPECTED_BRANCH = "carrot-wip-integrated-v6"


def _valid_digest(value: Any) -> bool:
  return isinstance(value, str) and len(value) == 64 and all(c in "0123456789abcdef" for c in value)


def _validate_binding(binding: dict[str, Any], *, qualification_stage: str, status: str, next_gate: str,
                      expected_head: str, expected_branch: str, label: str) -> None:
  if binding.get("stage") != "COMMISSIONING_QUALIFICATION_BINDING":
    raise ValueError(f"{label} binding stage mismatch")
  if binding.get("qualificationStage") != qualification_stage:
    raise ValueError(f"{label} binding qualificationStage mismatch")
  if binding.get("status") != status or binding.get("nextGate") != next_gate:
    raise ValueError(f"{label} binding status/nextGate mismatch")
  if binding.get("sourceHead") != expected_head or binding.get("sourceBranch") != expected_branch:
    raise ValueError(f"{label} binding source identity mismatch")
  if binding.get("recomputedExactMatch") is not True or binding.get("controlAuthorization") is not False:
    raise ValueError(f"{label} binding integrity/authorization mismatch")
  for name in ("evidenceSha256", "policySha256", "qualificationSha256"):
    if not _valid_digest(binding.get(name)):
      raise ValueError(f"invalid {label} binding digest: {name}")


def build_s2b_plan(s1: dict[str, Any], s1_binding: dict[str, Any], s2a: dict[str, Any], s2a_binding: dict[str, Any], *,
                   expected_head: str, expected_branch: str = EXPECTED_BRANCH) -> dict[str, Any]:
  if s1.get("status") != "PASS" or s1.get("nextGate") != "S2_TELEMETRY_PLAN_ONLY":
    raise ValueError("S1 qualification must be PASS with S2 telemetry nextGate")
  if s2a.get("status") != "PASS" or s2a.get("nextGate") != "S2B_OBSERVER_TELEMETRY_COEXISTENCE_PLAN_ONLY":
    raise ValueError("S2A qualification must be PASS with coexistence nextGate")
  if s1.get("controlAuthorization") is not False or s2a.get("controlAuthorization") is not False:
    raise ValueError("qualification controlAuthorization must remain false")
  if s1.get("sourceHead") != expected_head or s1.get("sourceBranch") != expected_branch:
    raise ValueError("S1 source identity does not match expected integrated source")

  _validate_binding(
    s1_binding,
    qualification_stage="S1_OBSERVER_QUALIFICATION",
    status="PASS",
    next_gate="S2_TELEMETRY_PLAN_ONLY",
    expected_head=expected_head,
    expected_branch=expected_branch,
    label="S1",
  )
  _validate_binding(
    s2a_binding,
    qualification_stage="S2A_TELEMETRY_ONLY_QUALIFICATION",
    status="PASS",
    next_gate="S2B_OBSERVER_TELEMETRY_COEXISTENCE_PLAN_ONLY",
    expected_head=expected_head,
    expected_branch=expected_branch,
    label="S2A",
  )

  return {
    "schemaVersion": 3,
    "stage": "S2B_OBSERVER_TELEMETRY_COEXISTENCE_PLAN_ONLY",
    "expectedHead": expected_head,
    "expectedBranch": expected_branch,
    "sourceHead": expected_head,
    "sourceBranch": expected_branch,
    "bindings": {
      "s1": {
        "evidenceSha256": s1_binding["evidenceSha256"],
        "policySha256": s1_binding["policySha256"],
        "qualificationSha256": s1_binding["qualificationSha256"],
      },
      "s2a": {
        "evidenceSha256": s2a_binding["evidenceSha256"],
        "policySha256": s2a_binding["policySha256"],
        "qualificationSha256": s2a_binding["qualificationSha256"],
      },
    },
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
  ap.add_argument("--s1-binding", type=Path, required=True)
  ap.add_argument("--s2a-qualification", type=Path, required=True)
  ap.add_argument("--s2a-binding", type=Path, required=True)
  ap.add_argument("--expected-head", required=True)
  ap.add_argument("--expected-branch", default=EXPECTED_BRANCH)
  ap.add_argument("--output", type=Path)
  args = ap.parse_args()
  s1 = json.loads(args.s1_qualification.read_text(encoding="utf-8"))
  s1_binding = json.loads(args.s1_binding.read_text(encoding="utf-8"))
  s2a = json.loads(args.s2a_qualification.read_text(encoding="utf-8"))
  s2a_binding = json.loads(args.s2a_binding.read_text(encoding="utf-8"))
  try:
    plan = build_s2b_plan(
      s1, s1_binding, s2a, s2a_binding,
      expected_head=args.expected_head, expected_branch=args.expected_branch,
    )
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
