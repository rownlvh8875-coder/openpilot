#!/usr/bin/env python3
"""Bind existing offline replay analysis to exact inputs, policy and model metadata.

This tool reads local files and committed Git objects only. It never executes
models or hardware commissioning. Verification means reproducible consistency,
not authentic producer observations or verified execution of a declared model.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict
import hashlib
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys
from typing import Any

from openpilot.selfdrive.modeld.egpu_integrated_guardian import GuardianPolicy
from openpilot.selfdrive.modeld.egpu_integrated_guardian_temporal import TemporalHeuristicPolicy
from openpilot.selfdrive.modeld.egpu_integrated_model_contract import (
  DEFAULT_QCOM_POINTER, EXPECTED_BRANCH, ModelPairContract, contract_from_dict, parse_git_lfs_pointer_text,
)
from tools.egpu_integrated_guardian_replay import analyze_rows
from tools.egpu_integrated_paired_input_adapter import (
  ADAPTER_OUTPUT_FILES, BIG_INPUT_FILE, CONTRACT_FILE, PRODUCER_SOURCE_FILE, PROVENANCE_DECLARATION_FILE, RECEIPT_FILE,
  ROWS_FILE, SMALL_INPUT_FILE, verify_adapter_evidence_bytes,
)
from tools.egpu_integrated_review_queue import build_review_queue

ROOT = Path(__file__).resolve().parents[1]
STAGE = "OFFLINE_REPLAY_REVIEW_BUNDLE"
INPUT_FILES = ("rows.jsonl", "model-contract.json", "policy.json")
PAIRED_EXTRA_FILES = (SMALL_INPUT_FILE, BIG_INPUT_FILE, PRODUCER_SOURCE_FILE, PROVENANCE_DECLARATION_FILE, RECEIPT_FILE)
RESULT_FILES = ("events.jsonl", "summary.json", "review-queue.json")
MEMBER_FILES = INPUT_FILES + RESULT_FILES
EVIDENCE_KINDS = ("synthetic", "provided_offline", "provided_paired_offline")


def _input_files(evidence_kind: str) -> tuple[str, ...]:
  return INPUT_FILES + (PAIRED_EXTRA_FILES if evidence_kind == "provided_paired_offline" else ())


def _member_files(evidence_kind: str) -> tuple[str, ...]:
  return _input_files(evidence_kind) + RESULT_FILES
BOUNDARY = {
  "controlAuthorization": False,
  "runtimeActivationAuthorization": False,
  "shadowExecutionAuthorization": False,
  "publicRoadAuthorization": False,
  "hardwareValidationPerformed": False,
  "commissioningEligible": False,
  "modelExecutionVerified": False,
  "producerAssertionsVerified": False,
}
BUNDLE_POLICY = {"includeObserve": True, **BOUNDARY}
ANALYZER_PATHS = (
  "tools/egpu_integrated_review_bundle.py",
  "tools/egpu_integrated_paired_input_adapter.py",
  "tools/egpu_integrated_guardian_replay.py",
  "tools/egpu_integrated_review_queue.py",
  "openpilot/selfdrive/modeld/egpu_integrated_guardian.py",
  "openpilot/selfdrive/modeld/egpu_integrated_guardian_temporal.py",
  "openpilot/selfdrive/modeld/egpu_integrated_fault_state.py",
  "openpilot/selfdrive/modeld/egpu_integrated_model_contract.py",
  "openpilot/selfdrive/modeld/egpu_integrated_model_slots.py",
  "openpilot/selfdrive/modeld/big_model.py",
  "openpilot/selfdrive/modeld/big_model_status.py",
  "openpilot/__init__.py",
  "openpilot/selfdrive/__init__.py",
  "openpilot/selfdrive/modeld/__init__.py",
)


def _sha256(data: bytes) -> str:
  return hashlib.sha256(data).hexdigest()


def _json_bytes(value: Any) -> bytes:
  return (json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False) + "\n").encode("utf-8")


def _bundle_id(manifest: dict[str, Any]) -> str:
  return _sha256(_json_bytes({key: value for key, value in manifest.items() if key != "bundleId"}))


def _exact_keys(value: Any, keys: set[str], label: str) -> None:
  if not isinstance(value, dict) or set(value) != keys:
    raise ValueError(f"{label}: exact required fields are missing or unknown fields were supplied")


def _no_duplicate_keys(pairs):
  value = {}
  for key, item in pairs:
    if key in value:
      raise ValueError(f"duplicate JSON key: {key}")
    value[key] = item
  return value


def _finite_tree(value: Any) -> None:
  if isinstance(value, float) and not math.isfinite(value):
    raise ValueError("nonfinite JSON number")
  if isinstance(value, dict):
    for item in value.values():
      _finite_tree(item)
  elif isinstance(value, list):
    for item in value:
      _finite_tree(item)


def _load_json(raw: bytes) -> Any:
  value = json.loads(raw.decode("utf-8"), object_pairs_hook=_no_duplicate_keys)
  _finite_tree(value)
  return value


def _source_identity(head: Any, branch: Any) -> dict[str, str]:
  if not isinstance(head, str) or re.fullmatch(r"[0-9a-f]{40}", head) is None:
    raise ValueError("full lowercase source HEAD required")
  if not isinstance(branch, str) or not branch or any(char.isspace() for char in branch):
    raise ValueError("nonempty source branch token required")
  return {"head": head, "branch": branch}


def _git_bytes(*args: str) -> bytes:
  # A partial clone must fail on absent local objects, never fetch evidence.
  env = dict(os.environ, GIT_NO_LAZY_FETCH="1", GIT_TERMINAL_PROMPT="0")
  result = subprocess.run(["git", "-C", str(ROOT), *args], capture_output=True, check=False, env=env)
  if result.returncode:
    raise ValueError("required local Git source object or identity is unavailable")
  return result.stdout


def _regular_file(path: Path) -> None:
  if path.is_symlink() or path.is_junction() or not path.is_file():
    raise ValueError(f"regular local file required: {path.name}")


def _read_file(path: Path) -> bytes:
  _regular_file(path)
  return path.read_bytes()


def _normalize_source(raw: bytes) -> bytes:
  return raw.decode("utf-8").replace("\r\n", "\n").encode("utf-8")


def _loaded_source_hashes() -> dict[str, str]:
  if (ROOT / "tools/__init__.py").exists() or getattr(sys.modules.get("tools"), "__file__", None) is not None:
    raise ValueError("tools package initializer requires a new analyzer source review")
  hashes = {}
  for path in ANALYZER_PATHS:
    name = path.removesuffix("/__init__.py").removesuffix(".py").replace("/", ".")
    module = sys.modules[__name__] if path == ANALYZER_PATHS[0] else sys.modules.get(name)
    location = getattr(module, "__file__", None)
    if location is None or Path(location).resolve() != (ROOT / path).resolve():
      raise ValueError(f"analyzer module loaded outside this checkout: {path}")
    hashes[path] = _sha256(_normalize_source(_read_file(ROOT / path)))
  return hashes


# Source changes after import require a fresh process, even if they were then
# committed. Do not label previously imported code as the new on-disk revision.
_IMPORT_SOURCE_HASHES = _loaded_source_hashes()


def _capture_analyzer_identity() -> dict[str, Any]:
  head = _git_bytes("rev-parse", "HEAD").decode("ascii").strip()
  branch = _git_bytes("rev-parse", "--abbrev-ref", "HEAD").decode("utf-8").strip()
  source = _source_identity(head, branch)
  hashes = _loaded_source_hashes()
  if hashes != _IMPORT_SOURCE_HASHES:
    raise ValueError("analyzer source changed after import; start a fresh process")
  for name in ANALYZER_PATHS:
    path = ROOT / name
    if not path.resolve().is_relative_to(ROOT):
      raise ValueError("analyzer source leaves the checkout")
    actual = _normalize_source(_read_file(path))
    committed = _normalize_source(_git_bytes("cat-file", "blob", f"{head}:{name}"))
    if actual != committed:
      raise ValueError(f"uncommitted analyzer source: {name}")
    hashes[name] = _sha256(actual)
  return {**source, "sourceNormalization": "utf8-lf", "sourceHashes": hashes, "sourceFingerprint": _sha256(_json_bytes(hashes))}


def _validate_analyzer(value: Any) -> None:
  _exact_keys(value, {"head", "branch", "sourceNormalization", "sourceHashes", "sourceFingerprint"}, "analyzer")
  _source_identity(value["head"], value["branch"])
  if value["sourceNormalization"] != "utf8-lf":
    raise ValueError("unsupported analyzer source normalization")
  _exact_keys(value["sourceHashes"], set(ANALYZER_PATHS), "analyzer source hashes")
  for digest in value["sourceHashes"].values():
    if not isinstance(digest, str) or re.fullmatch(r"[0-9a-f]{64}", digest) is None:
      raise ValueError("invalid analyzer source hash")
  if value["sourceFingerprint"] != _sha256(_json_bytes(value["sourceHashes"])):
    raise ValueError("analyzer source fingerprint mismatch")


def _verify_contract_source(contract: ModelPairContract) -> None:
  head = contract.source.head
  if _git_bytes("cat-file", "-t", head).strip() != b"commit":
    raise ValueError("model contract source HEAD must identify a Git commit")
  for path, expected in contract.source.interface_blobs:
    actual = _git_bytes("rev-parse", "--verify", f"{head}:{path}").decode("ascii").strip()
    if actual != expected:
      raise ValueError("model contract interface does not match committed source")
  pointer = _git_bytes("cat-file", "blob", f"{head}:{DEFAULT_QCOM_POINTER}").decode("utf-8")
  artifact = parse_git_lfs_pointer_text(pointer, file_name=DEFAULT_QCOM_POINTER)
  if artifact != contract.registry.qcom.artifact:
    raise ValueError("QCOM contract artifact does not match committed source LFS pointer")


def _verify_analyzer_commit(analyzer: dict[str, Any]) -> None:
  if _git_bytes("cat-file", "-t", analyzer["head"]).strip() != b"commit":
    raise ValueError("recorded analyzer HEAD must identify a Git commit")
  for path, expected in analyzer["sourceHashes"].items():
    data = _git_bytes("cat-file", "blob", f"{analyzer['head']}:{path}")
    if _sha256(_normalize_source(data)) != expected:
      raise ValueError("recorded analyzer source hashes do not match its Git commit")


def _policy(raw: bytes) -> tuple[GuardianPolicy, TemporalHeuristicPolicy]:
  value = _load_json(raw)
  _exact_keys(value, {"schemaVersion", "guardian", "temporal"}, "review policy")
  if type(value["schemaVersion"]) is not int or value["schemaVersion"] != 1:
    raise ValueError("unsupported review policy schema")
  specifications = (
    ("guardian", GuardianPolicy, {"max_frame_age"}, {"reject_supply_fault"}, {"max_execution_ms", "max_hardware_age_s"}),
    ("temporal", TemporalHeuristicPolicy, {"max_scene_gap_frames"}, set(), {"max_action_timestamp_skew_s"}),
  )
  policies = []
  for name, cls, integers, booleans, nullable in specifications:
    fields = value[name]
    # Defaults discover the field set only; every actual value is explicit.
    _exact_keys(fields, set(asdict(cls())), name)
    for key, item in fields.items():
      if key in nullable and item is None:
        continue
      expected_type = bool if key in booleans else int if key in integers else (int, float)
      if not isinstance(item, expected_type) or (key not in booleans and isinstance(item, bool)):
        raise ValueError(f"invalid policy type: {name}.{key}")
      if key not in booleans and not math.isfinite(item):
        raise ValueError(f"nonfinite policy: {name}.{key}")
    instance = cls(**fields)
    instance.validate()
    policies.append(instance)
  return policies[0], policies[1]


def _compute(inputs: dict[str, bytes], source: dict[str, str], evidence_kind: str) -> tuple[dict[str, bytes], str]:
  contract = contract_from_dict(_load_json(inputs["model-contract.json"]))
  if {"head": contract.source.head, "branch": contract.source.branch} != source:
    raise ValueError("declared model contract source identity mismatch")
  _verify_contract_source(contract)
  if evidence_kind == "provided_paired_offline":
    missing = set(PAIRED_EXTRA_FILES) - inputs.keys()
    if missing:
      raise ValueError(f"provided_paired_offline missing paired evidence members: {sorted(missing)!r}")
    verify_adapter_evidence_bytes(
      small=inputs[SMALL_INPUT_FILE], big=inputs[BIG_INPUT_FILE], producer_source=inputs[PRODUCER_SOURCE_FILE],
      provenance=inputs[PROVENANCE_DECLARATION_FILE], rows=inputs[ROWS_FILE], receipt=inputs[RECEIPT_FILE],
      contract=contract, expected_source=source,
    )
  elif any(name in inputs for name in PAIRED_EXTRA_FILES):
    raise ValueError("paired source evidence is only valid for provided_paired_offline")
  guardian, temporal = _policy(inputs["policy.json"])
  rows = [_load_json(line) for line in inputs["rows.jsonl"].splitlines() if line.strip()]
  if not rows:
    raise ValueError("nonempty replay input required")
  events, summary = analyze_rows(rows, expected_source_head=source["head"], expected_source_branch=source["branch"],
                                 guardian_policy=guardian, temporal_policy=temporal)
  queue = build_review_queue(events, include_observe=True)
  return {
    "events.jsonl": b"".join(_json_bytes(event) for event in events),
    "summary.json": _json_bytes(summary),
    "review-queue.json": _json_bytes(queue),
  }, contract.resolved_contract_id


def build_review_bundle(rows_path: Path, contract_path: Path, policy_path: Path, output: Path, *,
                        expected_source_head: str, expected_source_branch: str, evidence_kind: str,
                        paired_evidence_dir: Path | None = None) -> dict[str, Any]:
  source = _source_identity(expected_source_head, expected_source_branch)
  if evidence_kind not in EVIDENCE_KINDS:
    raise ValueError("explicit evidence kind required: synthetic, provided_offline, or provided_paired_offline")
  if evidence_kind == "provided_paired_offline" and paired_evidence_dir is None:
    raise ValueError("provided_paired_offline requires --paired-evidence-dir")
  if evidence_kind != "provided_paired_offline" and paired_evidence_dir is not None:
    raise ValueError("--paired-evidence-dir is only valid for provided_paired_offline")
  if output.exists() or output.is_symlink() or output.is_junction():
    raise ValueError("output already exists; choose a new directory")
  inputs = {name: _read_file(path) for name, path in zip(INPUT_FILES, (rows_path, contract_path, policy_path))}
  if paired_evidence_dir is not None:
    if paired_evidence_dir.is_symlink() or paired_evidence_dir.is_junction() or not paired_evidence_dir.is_dir():
      raise ValueError("regular paired evidence directory required")
    if {path.name for path in paired_evidence_dir.iterdir()} != set(ADAPTER_OUTPUT_FILES):
      raise ValueError("paired evidence directory has missing or extra members")
    if inputs[ROWS_FILE] != _read_file(paired_evidence_dir / ROWS_FILE):
      raise ValueError("--rows must be the exact rows.jsonl from --paired-evidence-dir")
    if inputs["model-contract.json"] != _read_file(paired_evidence_dir / CONTRACT_FILE):
      raise ValueError("--contract must be the exact model-contract.json from --paired-evidence-dir")
    for name in PAIRED_EXTRA_FILES:
      inputs[name] = _read_file(paired_evidence_dir / name)
  analyzer = _capture_analyzer_identity()
  _validate_analyzer(analyzer)
  results, contract_id = _compute(inputs, source, evidence_kind)
  if _capture_analyzer_identity() != analyzer:
    raise ValueError("analyzer source changed during analysis")
  members = {**inputs, **results}
  manifest = {
    "schemaVersion": 1, "stage": STAGE, "source": source, "evidenceKind": evidence_kind,
    "declaredModelContractId": contract_id, "analyzer": analyzer, "policy": dict(BUNDLE_POLICY),
    "files": {name: {"sha256": _sha256(data), "size": len(data)} for name, data in members.items()},
  }
  manifest["bundleId"] = _bundle_id(manifest)
  output.parent.mkdir(parents=True, exist_ok=True)
  output.mkdir(exist_ok=False)
  for name, data in {**members, "manifest.json": _json_bytes(manifest)}.items():
    with (output / name).open("xb") as destination:
      destination.write(data)
  return manifest


def verify_review_bundle(bundle: Path, *, expected_bundle_id: str, expected_source_head: str,
                         expected_source_branch: str) -> dict[str, Any]:
  source = _source_identity(expected_source_head, expected_source_branch)
  if not isinstance(expected_bundle_id, str) or re.fullmatch(r"[0-9a-f]{64}", expected_bundle_id) is None:
    raise ValueError("externally retained expected bundle ID required")
  if bundle.is_symlink() or bundle.is_junction() or not bundle.is_dir():
    raise ValueError("regular bundle directory required")
  manifest = _load_json(_read_file(bundle / "manifest.json"))
  _exact_keys(manifest, {"schemaVersion", "stage", "source", "evidenceKind", "declaredModelContractId",
                         "analyzer", "policy", "files", "bundleId"}, "manifest")
  if type(manifest["schemaVersion"]) is not int or manifest["schemaVersion"] != 1 or manifest["stage"] != STAGE:
    raise ValueError("unsupported bundle manifest")
  if manifest["source"] != source or manifest["evidenceKind"] not in EVIDENCE_KINDS:
    raise ValueError("bundle source or evidence kind mismatch")
  member_files = _member_files(manifest["evidenceKind"])
  if {path.name for path in bundle.iterdir()} != set(member_files) | {"manifest.json"}:
    raise ValueError("bundle has missing or extra members")
  if _json_bytes(manifest["policy"]) != _json_bytes(BUNDLE_POLICY):
    raise ValueError("bundle authorization/review policy mismatch")
  if manifest["bundleId"] != expected_bundle_id or _bundle_id(manifest) != expected_bundle_id:
    raise ValueError("bundle ID mismatch")
  _validate_analyzer(manifest["analyzer"])
  _verify_analyzer_commit(manifest["analyzer"])
  current_analyzer = _capture_analyzer_identity()
  if current_analyzer["sourceFingerprint"] != manifest["analyzer"]["sourceFingerprint"]:
    raise ValueError("verifier analyzer source differs from the recorded analyzer")
  _exact_keys(manifest["files"], set(member_files), "manifest files")
  members = {}
  for name in member_files:
    entry = manifest["files"][name]
    _exact_keys(entry, {"sha256", "size"}, "member digest")
    if type(entry["size"]) is not int or entry["size"] < 0:
      raise ValueError("invalid member size")
    data = _read_file(bundle / name)
    if len(data) != entry["size"] or _sha256(data) != entry["sha256"]:
      raise ValueError(f"member hash/size mismatch: {name}")
    members[name] = data
  input_names = _input_files(manifest["evidenceKind"])
  expected, contract_id = _compute({name: members[name] for name in input_names}, source, manifest["evidenceKind"])
  if contract_id != manifest["declaredModelContractId"]:
    raise ValueError("declared model contract ID mismatch")
  for name in RESULT_FILES:
    if members[name] != expected[name]:
      raise ValueError(f"recomputed result differs: {name}")
  if _capture_analyzer_identity() != current_analyzer:
    raise ValueError("verifier source changed during analysis")
  return {
    "schemaVersion": 1, "stage": "OFFLINE_REPLAY_REVIEW_BUNDLE_VERIFICATION", "status": "VERIFIED_OFFLINE_CONSISTENCY",
    "bundleId": expected_bundle_id, "source": source, "evidenceKind": manifest["evidenceKind"],
    "declaredModelContractId": contract_id, "analyzerSourceMatch": True, "verifierAnalyzer": current_analyzer,
    **BOUNDARY,
  }


def main() -> int:
  parser = argparse.ArgumentParser(description=__doc__)
  sub = parser.add_subparsers(dest="command", required=True)
  build = sub.add_parser("build", help="build a new offline review directory from local files")
  build.add_argument("--rows", type=Path, required=True)
  build.add_argument("--contract", type=Path, required=True)
  build.add_argument("--policy", type=Path, required=True)
  build.add_argument("--output", type=Path, required=True)
  build.add_argument("--evidence-kind", choices=EVIDENCE_KINDS, required=True)
  build.add_argument("--paired-evidence-dir", type=Path)
  verify = sub.add_parser("verify", help="check exact files and recompute with the recorded analyzer sources")
  verify.add_argument("--bundle", type=Path, required=True)
  verify.add_argument("--expected-bundle-id", required=True)
  for command in (build, verify):
    command.add_argument("--expected-source-head", required=True)
    command.add_argument("--expected-source-branch", default=EXPECTED_BRANCH)
  args = parser.parse_args()
  try:
    if args.command == "build":
      manifest = build_review_bundle(args.rows, args.contract, args.policy, args.output, expected_source_head=args.expected_source_head,
                                     expected_source_branch=args.expected_source_branch, evidence_kind=args.evidence_kind,
                                     paired_evidence_dir=args.paired_evidence_dir)
      result = {"stage": STAGE, "status": "BUILT_FOR_OFFLINE_REVIEW", "bundleId": manifest["bundleId"],
                "source": manifest["source"], "evidenceKind": manifest["evidenceKind"], **BOUNDARY}
    else:
      result = verify_review_bundle(args.bundle, expected_bundle_id=args.expected_bundle_id,
                                    expected_source_head=args.expected_source_head, expected_source_branch=args.expected_source_branch)
  except (OSError, ValueError, TypeError, KeyError, OverflowError) as error:
    print(_json_bytes({"stage": STAGE, "status": "HOLD", "reason": str(error), **BOUNDARY}).decode("utf-8"), end="")
    return 2
  print(_json_bytes(result).decode("utf-8"), end="")
  return 0


if __name__ == "__main__":
  raise SystemExit(main())
