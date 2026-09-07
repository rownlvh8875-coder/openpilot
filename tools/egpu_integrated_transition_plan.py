#!/usr/bin/env python3
"""Generate a non-executing transition plan from a PASS install preflight report.

This tool never changes branches, files, Params, processes, or power state. It
only emits a structured plan and keeps every authorization false.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def build_plan(preflight: dict[str, Any], *, expected_target_head: str) -> dict[str, Any]:
  if preflight.get("status") != "PASS":
    raise ValueError("install preflight must be PASS")
  failed = preflight.get("failedChecks")
  if failed not in ([], None):
    raise ValueError("install preflight contains failed checks")
  target_head = str(preflight.get("targetHead") or "")
  if target_head != expected_target_head:
    raise ValueError("preflight targetHead does not match expected target head")

  return {
    "schemaVersion": 1,
    "stage": "INSTALL_PLAN_ONLY",
    "targetHead": target_head,
    "liveHead": preflight.get("liveHead"),
    "liveBranch": preflight.get("liveBranch"),
    "target": preflight.get("target"),
    "live": preflight.get("live"),
    "backup": preflight.get("backup"),
    "manualAuthorizationRequired": True,
    "steps": [
      {"id": "P0", "action": "freeze_preinstall_evidence", "mutatesSystem": False},
      {"id": "P1", "action": "confirm_offroad_and_features_off", "mutatesSystem": False},
      {"id": "P2", "action": "operator_authorizes_controlled_install", "mutatesSystem": False},
      {"id": "P3", "action": "install_target_source_without_enabling_integrated_features", "mutatesSystem": True},
      {"id": "P4", "action": "full_device_reboot", "mutatesSystem": True},
      {"id": "P5", "action": "read_only_postboot_verification", "mutatesSystem": False},
      {"id": "P6", "action": "rollback_if_postboot_not_pass", "mutatesSystem": True},
      {"id": "P7", "action": "only_after_postboot_pass_start_s1_observer_commissioning", "mutatesSystem": False},
    ],
    "stopConditions": [
      "vehicle_onroad",
      "live_source_provenance_changed",
      "target_head_changed",
      "integrated_feature_marker_enabled_before_authorized_stage",
      "postboot_verification_hold_or_fail",
      "unexpected_modeld_or_manager_failure",
    ],
    "authorizations": {
      "installAuthorization": False,
      "rebootAuthorization": False,
      "publicRoadAuthorization": False,
      "controlAuthorization": False,
      "shadowAuthorization": False,
    },
    "interpretation": "This file is a plan only. It does not authorize or execute installation, reboot, shadow inference, or vehicle control.",
  }


def main() -> int:
  ap = argparse.ArgumentParser()
  ap.add_argument("--preflight", type=Path, required=True)
  ap.add_argument("--expected-target-head", required=True)
  ap.add_argument("--output", type=Path)
  args = ap.parse_args()

  preflight = json.loads(args.preflight.read_text(encoding="utf-8"))
  try:
    plan = build_plan(preflight, expected_target_head=args.expected_target_head)
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
