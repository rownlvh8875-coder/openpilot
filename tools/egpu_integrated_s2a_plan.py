#!/usr/bin/env python3
"""Generate plan-only S2A telemetry commissioning from bound S1 PASS evidence."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

EXPECTED_BRANCH = "carrot-wip-integrated-v6"


def _valid_digest(value: Any) -> bool:
  return isinstance(value, str) and len(value) == 64 and all(c in "0123456789abcdef" for c in value)


def build_s2a_plan(s1: dict[str, Any], binding: dict[str, Any], *, expected_head: str,
                   expected_branch: str = EXPECTED_BRANCH) -> dict[str, Any]:
  if s1.get("status") != "PASS" or s1.get("nextGate") != "S2_TELEMETRY_PLAN_ONLY":
    raise ValueError("S1 qualification must be PASS with S2 telemetry nextGate")
  if s1.get("controlAuthorization") is not False:
    raise ValueError("S1 controlAuthorization must remain false")
  if s1.get("sourceHead") != expected_head or s1.get("sourceBranch") != expected_branch:
    raise ValueError("S1 source identity does not match expected integrated source")

  if binding.get("stage") != "COMMISSIONING_QUALIFICATION_BINDING":
    raise ValueError("S1 binding stage mismatch")
  if binding.get("qualificationStage") != "S1_OBSERVER_QUALIFICATION":
    raise ValueError("S1 binding qualificationStage mismatch")
  if binding.get("status") != "PASS" or binding.get("nextGate") != "S2_TELEMETRY_PLAN_ONLY":
    raise ValueError("S1 binding must represent the PASS qualification")
  if binding.get("sourceHead") != expected_head or binding.get("sourceBranch") != expected_branch:
    raise ValueError("S1 binding source identity mismatch")
  if binding.get("recomputedExactMatch") is not True or binding.get("controlAuthorization") is not False:
    raise ValueError("S1 binding integrity/authorization mismatch")
  for name in ("evidenceSha256", "policySha256", "qualificationSha256"):
    if not _valid_digest(binding.get(name)):
      raise ValueError(f"invalid S1 binding digest: {name}")

  return {
    "schemaVersion": 3,
    "stage": "S2A_TELEMETRY_ONLY_PLAN",
    "expectedHead": expected_head,
    "expectedBranch": expected_branch,
    "sourceHead": expected_head,
    "sourceBranch": expected_branch,
    "s1Binding": {
      "evidenceSha256": binding["evidenceSha256"],
      "policySha256": binding["policySha256"],
      "qualificationSha256": binding["qualificationSha256"],
    },
    "purpose": "isolate read-only eGPU hardware telemetry overhead on active Carrot/eGPU inference",
    "sequence": [
      {"id": "S2A_OFF_BEFORE", "observer": False, "telemetry": False, "shadow": False, "stationaryOnly": True, "controlsInactive": True},
      {"id": "S2A_TELEMETRY_ON", "observer": False, "telemetry": True, "shadow": False, "stationaryOnly": True, "controlsInactive": True},
      {"id": "S2A_OFF_AFTER", "observer": False, "telemetry": False, "shadow": False, "stationaryOnly": True, "controlsInactive": True},
    ],
    "restartPolicy": {
      "fullDeviceRebootBetweenLegs": True,
      "reason": "telemetry enable state is sampled when modeld constructs EgpuHardwareTelemetry; clean process boundaries are required",
      "rebootAuthorization": False,
    },
    "telemetryExpectations": {
      "readOnly": True,
      "hardwareWritesAllowed": False,
      "statePath": "/data/egpu_integrated/hardware.json",
      "expectedCadenceSeconds": 2.0,
      "cadenceIsImplementationReferenceNotAcceptanceThreshold": True,
    },
    "qualificationPolicy": {
      "explicitPolicyFileRequired": True,
      "officialCommaThresholds": False,
      "baselineDriftMustPass": True,
      "hardwareTelemetryFreshnessMustBeObserved": True,
    },
    "stopConditions": [
      "vehicle_moves", "gear_not_park", "lat_or_long_controls_active",
      "observer_marker_enabled", "shadow_marker_enabled", "egpu_fallback",
      "unexpected_modeld_failure", "supply_fault", "source_identity_mismatch",
    ],
    "authorizations": {
      "telemetryEnableAuthorization": False,
      "observerEnableAuthorization": False,
      "rebootAuthorization": False,
      "shadowAuthorization": False,
      "publicRoadAuthorization": False,
      "controlAuthorization": False,
    },
    "nextGateOnPass": "S2B_OBSERVER_TELEMETRY_COEXISTENCE_PLAN_ONLY",
  }


def main() -> int:
  ap = argparse.ArgumentParser()
  ap.add_argument("--s1-qualification", type=Path, required=True)
  ap.add_argument("--s1-binding", type=Path, required=True)
  ap.add_argument("--expected-head", required=True)
  ap.add_argument("--expected-branch", default=EXPECTED_BRANCH)
  ap.add_argument("--output", type=Path)
  args = ap.parse_args()
  s1 = json.loads(args.s1_qualification.read_text(encoding="utf-8"))
  binding = json.loads(args.s1_binding.read_text(encoding="utf-8"))
  try:
    plan = build_s2a_plan(s1, binding, expected_head=args.expected_head, expected_branch=args.expected_branch)
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
