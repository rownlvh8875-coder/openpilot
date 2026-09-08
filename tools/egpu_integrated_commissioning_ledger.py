#!/usr/bin/env python3
"""Build a read-only commissioning ledger from verified stage artifacts.

The ledger never authorizes installation, reboot, public-road use, shadow, or
vehicle control. It only proves which evidence chain is complete on one exact
integrated-v6 source revision and identifies the next missing gate.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

EXPECTED_BRANCH = "carrot-wip-integrated-v6"
DIGEST_KEYS = ("evidenceSha256", "policySha256", "qualificationSha256")


def sha256_file(path: Path) -> str:
  h = hashlib.sha256()
  with path.open("rb") as f:
    for chunk in iter(lambda: f.read(1024 * 1024), b""):
      h.update(chunk)
  return h.hexdigest()


def _valid_digest(value: Any) -> bool:
  return isinstance(value, str) and len(value) == 64 and all(c in "0123456789abcdef" for c in value)


def validate_postboot(postboot: dict[str, Any], *, expected_head: str, expected_branch: str) -> None:
  if postboot.get("stage") != "POSTBOOT_ALL_FEATURES_OFF":
    raise ValueError("postboot stage mismatch")
  if postboot.get("status") != "PASS" or postboot.get("nextGate") != "S1_OBSERVER_ONLY":
    raise ValueError("postboot verification must PASS and open S1")
  if postboot.get("head") != expected_head or postboot.get("branch") != expected_branch:
    raise ValueError("postboot source identity mismatch")
  if postboot.get("failedChecks") not in ([], None):
    raise ValueError("postboot contains failed checks")
  checks = postboot.get("checks")
  if not isinstance(checks, list) or not checks or any(item.get("pass") is not True for item in checks if isinstance(item, dict)):
    raise ValueError("postboot checks must all pass")
  auth = postboot.get("authorizations", {})
  for name in ("publicRoadAuthorization", "controlAuthorization", "shadowAuthorization"):
    if auth.get(name) is not False:
      raise ValueError(f"postboot {name} must remain false")


def validate_binding(binding: dict[str, Any], *, qualification_stage: str, next_gate: str,
                     expected_head: str, expected_branch: str, label: str) -> None:
  if binding.get("stage") != "COMMISSIONING_QUALIFICATION_BINDING":
    raise ValueError(f"{label} binding stage mismatch")
  if binding.get("qualificationStage") != qualification_stage:
    raise ValueError(f"{label} qualification stage mismatch")
  if binding.get("status") != "PASS" or binding.get("nextGate") != next_gate:
    raise ValueError(f"{label} binding must represent PASS with expected nextGate")
  if binding.get("sourceHead") != expected_head or binding.get("sourceBranch") != expected_branch:
    raise ValueError(f"{label} source identity mismatch")
  if binding.get("recomputedExactMatch") is not True:
    raise ValueError(f"{label} binding was not exactly recomputed")
  if binding.get("controlAuthorization") is not False or binding.get("publicRoadAuthorization") is not False:
    raise ValueError(f"{label} authorization boundary violated")
  for name in DIGEST_KEYS:
    if not _valid_digest(binding.get(name)):
      raise ValueError(f"{label} invalid digest: {name}")


def build_ledger(postboot: dict[str, Any], *, expected_head: str, expected_branch: str = EXPECTED_BRANCH,
                 s1_binding: dict[str, Any] | None = None,
                 s2a_binding: dict[str, Any] | None = None,
                 s2b_binding: dict[str, Any] | None = None) -> dict[str, Any]:
  validate_postboot(postboot, expected_head=expected_head, expected_branch=expected_branch)

  stages: list[dict[str, Any]] = [
    {"stage": "POSTBOOT_ALL_FEATURES_OFF", "status": "PASS", "sourceHead": expected_head},
  ]
  missing: list[str] = []

  if s1_binding is None:
    missing.append("S1_BINDING")
  else:
    validate_binding(
      s1_binding, qualification_stage="S1_OBSERVER_QUALIFICATION", next_gate="S2_TELEMETRY_PLAN_ONLY",
      expected_head=expected_head, expected_branch=expected_branch, label="S1",
    )
    stages.append({"stage": "S1_OBSERVER_QUALIFICATION", "status": "PASS", "binding": {k: s1_binding[k] for k in DIGEST_KEYS}})

  if not missing:
    if s2a_binding is None:
      missing.append("S2A_BINDING")
    else:
      validate_binding(
        s2a_binding, qualification_stage="S2A_TELEMETRY_ONLY_QUALIFICATION",
        next_gate="S2B_OBSERVER_TELEMETRY_COEXISTENCE_PLAN_ONLY",
        expected_head=expected_head, expected_branch=expected_branch, label="S2A",
      )
      stages.append({"stage": "S2A_TELEMETRY_ONLY_QUALIFICATION", "status": "PASS", "binding": {k: s2a_binding[k] for k in DIGEST_KEYS}})

  if not missing:
    if s2b_binding is None:
      missing.append("S2B_BINDING")
    else:
      validate_binding(
        s2b_binding, qualification_stage="S2B_OBSERVER_TELEMETRY_COEXISTENCE_QUALIFICATION",
        next_gate="S4B_PARKED_SHADOW_LOAD_PROBE_REVIEW",
        expected_head=expected_head, expected_branch=expected_branch, label="S2B",
      )
      stages.append({"stage": "S2B_OBSERVER_TELEMETRY_COEXISTENCE_QUALIFICATION", "status": "PASS", "binding": {k: s2b_binding[k] for k in DIGEST_KEYS}})

  if "S1_BINDING" in missing:
    status, next_gate = "HOLD_S1_BINDING_REQUIRED", "S1_OBSERVER_ONLY"
  elif "S2A_BINDING" in missing:
    status, next_gate = "HOLD_S2A_BINDING_REQUIRED", "S2_TELEMETRY_PLAN_ONLY"
  elif "S2B_BINDING" in missing:
    status, next_gate = "HOLD_S2B_BINDING_REQUIRED", "S2B_OBSERVER_TELEMETRY_COEXISTENCE_PLAN_ONLY"
  else:
    status, next_gate = "READY_S4B_REVIEW", "S4B_PARKED_SHADOW_LOAD_PROBE_REVIEW"

  return {
    "schemaVersion": 1,
    "stage": "INTEGRATED_COMMISSIONING_LEDGER",
    "status": status,
    "sourceHead": expected_head,
    "sourceBranch": expected_branch,
    "completedStages": stages,
    "missingRequirements": missing,
    "nextGate": next_gate,
    "s4bReviewEligible": status == "READY_S4B_REVIEW",
    "authorizations": {
      "installAuthorization": False,
      "rebootAuthorization": False,
      "observerEnableAuthorization": False,
      "telemetryEnableAuthorization": False,
      "shadowAuthorization": False,
      "publicRoadAuthorization": False,
      "controlAuthorization": False,
    },
    "interpretation": "READY_S4B_REVIEW only permits a separate parked/offroad S4B review; it is not execution or control authorization.",
  }


def main() -> int:
  ap = argparse.ArgumentParser()
  ap.add_argument("--postboot", type=Path, required=True)
  ap.add_argument("--s1-binding", type=Path)
  ap.add_argument("--s2a-binding", type=Path)
  ap.add_argument("--s2b-binding", type=Path)
  ap.add_argument("--expected-head", required=True)
  ap.add_argument("--expected-branch", default=EXPECTED_BRANCH)
  ap.add_argument("--output", type=Path, required=True)
  args = ap.parse_args()

  def load(path: Path | None) -> dict[str, Any] | None:
    return json.loads(path.read_text(encoding="utf-8")) if path else None

  try:
    ledger = build_ledger(
      load(args.postboot), expected_head=args.expected_head, expected_branch=args.expected_branch,
      s1_binding=load(args.s1_binding), s2a_binding=load(args.s2a_binding), s2b_binding=load(args.s2b_binding),
    )
  except (ValueError, KeyError, TypeError, json.JSONDecodeError, OSError) as exc:
    print(json.dumps({"status": "HOLD", "reason": str(exc)}, indent=2))
    return 2

  artifacts = {"postbootSha256": sha256_file(args.postboot)}
  for name, path in (("s1BindingSha256", args.s1_binding), ("s2aBindingSha256", args.s2a_binding), ("s2bBindingSha256", args.s2b_binding)):
    if path:
      artifacts[name] = sha256_file(path)
  ledger["artifactManifest"] = artifacts
  text = json.dumps(ledger, indent=2, ensure_ascii=False) + "\n"
  args.output.parent.mkdir(parents=True, exist_ok=True)
  args.output.write_text(text, encoding="utf-8")
  print(text, end="")
  return 0


if __name__ == "__main__":
  raise SystemExit(main())
