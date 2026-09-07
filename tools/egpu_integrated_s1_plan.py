#!/usr/bin/env python3
"""Generate a plan-only S1 observer commissioning sequence from postboot PASS evidence."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def build_s1_plan(postboot: dict[str, Any], *, expected_head: str) -> dict[str, Any]:
  if postboot.get("status") != "PASS":
    raise ValueError("postboot verification must be PASS")
  if postboot.get("head") != expected_head:
    raise ValueError("postboot head does not match expected integrated head")
  if postboot.get("nextGate") != "S1_OBSERVER_ONLY":
    raise ValueError("postboot nextGate is not S1_OBSERVER_ONLY")

  return {
    "schemaVersion": 1,
    "stage": "S1_OBSERVER_PLAN_ONLY",
    "expectedHead": expected_head,
    "purpose": "measure observer-only interference before telemetry or shadow workloads",
    "sequence": [
      {
        "id": "S1_OFF_BEFORE",
        "observer": False,
        "telemetry": False,
        "shadow": False,
        "stationaryOnly": True,
        "controlsInactive": True,
        "collect": "active model latency/frame continuity baseline",
      },
      {
        "id": "S1_ON",
        "observer": True,
        "telemetry": False,
        "shadow": False,
        "stationaryOnly": True,
        "controlsInactive": True,
        "collect": "observer state + active model latency/frame continuity",
      },
      {
        "id": "S1_OFF_AFTER",
        "observer": False,
        "telemetry": False,
        "shadow": False,
        "stationaryOnly": True,
        "controlsInactive": True,
        "collect": "post-observer active model latency/frame continuity baseline",
      },
    ],
    "restartPolicy": {
      "fullDeviceRebootBetweenLegs": True,
      "reason": "observer enable state is sampled when modeld constructs EgpuIntegrationObserver; reboot also gives a clean process boundary",
      "rebootAuthorization": False,
    },
    "qualificationPolicy": {
      "explicitPolicyFileRequired": True,
      "officialCommaThresholds": False,
      "baselineDriftMustPass": True,
    },
    "stopConditions": [
      "vehicle_moves",
      "gear_not_park",
      "lat_or_long_controls_active",
      "telemetry_marker_enabled",
      "shadow_marker_enabled",
      "egpu_fallback",
      "unexpected_modeld_failure",
    ],
    "authorizations": {
      "observerEnableAuthorization": False,
      "rebootAuthorization": False,
      "telemetryAuthorization": False,
      "shadowAuthorization": False,
      "publicRoadAuthorization": False,
      "controlAuthorization": False,
    },
    "nextGateOnPass": "S2_TELEMETRY_PLAN_ONLY",
  }


def main() -> int:
  ap = argparse.ArgumentParser()
  ap.add_argument("--postboot", type=Path, required=True)
  ap.add_argument("--expected-head", required=True)
  ap.add_argument("--output", type=Path)
  args = ap.parse_args()
  postboot = json.loads(args.postboot.read_text(encoding="utf-8"))
  try:
    plan = build_s1_plan(postboot, expected_head=args.expected_head)
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
