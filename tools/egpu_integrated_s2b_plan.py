#!/usr/bin/env python3
"""Generate plan-only S2B observer+telemetry coexistence commissioning.

S2B is not executable until both S1 and S2A qualifications are PASS. It keeps
observer ON for all three legs and varies telemetry OFF/ON/OFF to measure the
incremental telemetry effect on the validated observer stack.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def build_s2b_plan(s1: dict[str, Any], s2a: dict[str, Any], *, expected_head: str) -> dict[str, Any]:
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

  return {
    "schemaVersion": 1,
    "stage": "S2B_OBSERVER_TELEMETRY_COEXISTENCE_PLAN_ONLY",
    "expectedHead": expected_head,
    "purpose": "measure incremental telemetry overhead when the already-qualified observer is also enabled",
    "sequence": [
      {
        "id": "S2B_OBSERVER_ONLY_BEFORE",
        "observer": True,
        "telemetry": False,
        "shadow": False,
        "stationaryOnly": True,
        "controlsInactive": True,
      },
      {
        "id": "S2B_OBSERVER_TELEMETRY_ON",
        "observer": True,
        "telemetry": True,
        "shadow": False,
        "stationaryOnly": True,
        "controlsInactive": True,
      },
      {
        "id": "S2B_OBSERVER_ONLY_AFTER",
        "observer": True,
        "telemetry": False,
        "shadow": False,
        "stationaryOnly": True,
        "controlsInactive": True,
      },
    ],
    "restartPolicy": {
      "fullDeviceRebootBetweenLegs": True,
      "rebootAuthorization": False,
    },
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
  ap.add_argument("--expected-head", required=True)
  ap.add_argument("--output", type=Path)
  args = ap.parse_args()
  s1 = json.loads(args.s1_qualification.read_text(encoding="utf-8"))
  s2a = json.loads(args.s2a_qualification.read_text(encoding="utf-8"))
  try:
    plan = build_s2b_plan(s1, s2a, expected_head=args.expected_head)
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
