#!/usr/bin/env python3
"""Recompute and bind commissioning qualification to exact evidence/policy bytes.

The output is an immutable audit manifest: it recomputes the qualification from
the supplied evidence+policy and refuses to bind if the supplied qualification
differs. No vehicle, marker, process, Params, branch, or power state is changed.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

from tools.egpu_integrated_s1_qualification import LEGS as S1_LEGS, S1Metrics, S1Policy, qualify as qualify_s1
from tools.egpu_integrated_s2a_qualification import LEGS as S2A_LEGS, S2AMetrics, S2APolicy, qualify as qualify_s2a


def sha256_file(path: Path) -> str:
  h = hashlib.sha256()
  with path.open("rb") as f:
    for chunk in iter(lambda: f.read(1024 * 1024), b""):
      h.update(chunk)
  return h.hexdigest()


def recompute(evidence: dict[str, Any], policy: dict[str, Any], stage: str) -> dict[str, Any]:
  values = evidence.get("legs", evidence)
  if stage == "S1_OBSERVER_QUALIFICATION":
    metrics = {name: S1Metrics.from_dict(values[name]) for name in S1_LEGS if name in values}
    return qualify_s1(
      metrics,
      S1Policy.from_dict(policy),
      source_head=evidence.get("sourceHead"),
      source_branch=evidence.get("sourceBranch"),
    )
  if stage == "S2A_TELEMETRY_ONLY_QUALIFICATION":
    metrics = {name: S2AMetrics.from_dict(values[name]) for name in S2A_LEGS if name in values}
    return qualify_s2a(metrics, S2APolicy.from_dict(policy))
  raise ValueError(f"unsupported qualification stage: {stage}")


def build_binding(evidence_path: Path, policy_path: Path, qualification_path: Path) -> dict[str, Any]:
  evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
  policy = json.loads(policy_path.read_text(encoding="utf-8"))
  qualification = json.loads(qualification_path.read_text(encoding="utf-8"))
  stage = str(qualification.get("stage", ""))
  expected = recompute(evidence, policy, stage)
  if qualification != expected:
    raise ValueError("qualification does not exactly match recomputation from supplied evidence and policy")
  if qualification.get("controlAuthorization") is not False:
    raise ValueError("qualification controlAuthorization must remain false")

  source_head = evidence.get("sourceHead")
  source_branch = evidence.get("sourceBranch")
  if not isinstance(source_head, str) or not source_head:
    raise ValueError("evidence sourceHead is required")
  if not isinstance(source_branch, str) or not source_branch:
    raise ValueError("evidence sourceBranch is required")
  if stage == "S1_OBSERVER_QUALIFICATION":
    if qualification.get("sourceHead") != source_head or qualification.get("sourceBranch") != source_branch:
      raise ValueError("S1 qualification source identity does not match evidence")

  return {
    "schemaVersion": 1,
    "stage": "COMMISSIONING_QUALIFICATION_BINDING",
    "qualificationStage": stage,
    "status": qualification.get("status"),
    "nextGate": qualification.get("nextGate"),
    "sourceHead": source_head,
    "sourceBranch": source_branch,
    "evidenceSha256": sha256_file(evidence_path),
    "policySha256": sha256_file(policy_path),
    "qualificationSha256": sha256_file(qualification_path),
    "recomputedExactMatch": True,
    "controlAuthorization": False,
    "publicRoadAuthorization": False,
  }


def main() -> int:
  ap = argparse.ArgumentParser()
  ap.add_argument("--evidence", type=Path, required=True)
  ap.add_argument("--policy", type=Path, required=True)
  ap.add_argument("--qualification", type=Path, required=True)
  ap.add_argument("--output", type=Path, required=True)
  args = ap.parse_args()
  try:
    result = build_binding(args.evidence, args.policy, args.qualification)
  except (ValueError, KeyError, TypeError, json.JSONDecodeError, OSError) as exc:
    print(json.dumps({"status": "HOLD", "reason": str(exc)}, indent=2))
    return 2
  text = json.dumps(result, indent=2, ensure_ascii=False) + "\n"
  args.output.parent.mkdir(parents=True, exist_ok=True)
  args.output.write_text(text, encoding="utf-8")
  print(text, end="")
  return 0


if __name__ == "__main__":
  raise SystemExit(main())
