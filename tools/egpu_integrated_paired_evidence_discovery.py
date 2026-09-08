#!/usr/bin/env python3
"""Read-only discovery of offline BIG/SMALL evidence and route-log candidates."""
from __future__ import annotations

import argparse
from collections import defaultdict
import json
import os
from pathlib import Path
from typing import Any

from tools.egpu_integrated_paired_input_adapter import ADAPTER_OUTPUT_FILES, verify_adapter_output

STAGE = "PAIRED_EVIDENCE_DISCOVERY"
FALSE_BOUNDARY = {
  "modelExecutionVerified": False,
  "producerAssertionsVerified": False,
  "hardwareValidationPerformed": False,
  "commissioningEligible": False,
  "controlAuthorization": False,
  "publicRoadAuthorization": False,
  "runtimeActivationAuthorization": False,
}
RAW_LOG_NAMES = {"rlog.zst", "qlog.zst", "rlog.bz2", "qlog.bz2"}
SMALL_NAMES = {"small_actions.jsonl", "small-input.jsonl", "small_model_actions.jsonl"}
BIG_NAMES = {"big_actions.jsonl", "big-input.jsonl", "big_model_actions.jsonl"}
PAIRED_NAMES = {"paired_shadow.jsonl", "paired_actions.jsonl", "paired_model_actions.jsonl"}


def _json_bytes(value: Any) -> bytes:
  return (json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False) + "\n").encode("utf-8")


def _is_link(path: Path) -> bool:
  try:
    return path.is_symlink() or path.is_junction()
  except OSError:
    return True


def _safe_names(path: Path) -> set[str] | None:
  try:
    if _is_link(path) or not path.is_dir():
      return None
    return {entry.name for entry in path.iterdir()}
  except OSError:
    return None


def _first_json_objects(path: Path, *, limit: int = 8, max_bytes: int = 256 * 1024) -> list[dict[str, Any]]:
  rows: list[dict[str, Any]] = []
  read_bytes = 0
  try:
    if _is_link(path) or not path.is_file():
      return rows
    with path.open("rb") as stream:
      for raw in stream:
        read_bytes += len(raw)
        if read_bytes > max_bytes:
          break
        if not raw.strip():
          continue
        value = json.loads(raw.decode("utf-8"))
        if isinstance(value, dict):
          rows.append(value)
          if len(rows) >= limit:
            break
  except (OSError, UnicodeDecodeError, json.JSONDecodeError):
    return []
  return rows


def _looks_shadow_jsonl(path: Path) -> bool:
  for row in _first_json_objects(path):
    if row.get("type") in {"shadow_output", "shadow_error", "startup"}:
      return True
    if "shadowBackend" in row and ("activeBackend" in row or "action" in row):
      return True
  return False


def _file_kind(name: str) -> str | None:
  lower = name.lower()
  if lower in RAW_LOG_NAMES:
    return "raw_route_log"
  if lower in SMALL_NAMES or (lower.endswith(".jsonl") and "small" in lower and "shadow" not in lower):
    return "small_jsonl"
  if lower in BIG_NAMES or (lower.endswith(".jsonl") and "big" in lower):
    return "big_jsonl"
  if lower in PAIRED_NAMES or (lower.endswith(".jsonl") and "paired" in lower):
    return "paired_jsonl"
  return None


def _walk_root(root: Path, *, max_depth: int, max_files: int) -> tuple[list[Path], list[str], int, bool]:
  files: list[Path] = []
  skipped: list[str] = []
  root_parts = len(root.parts)
  seen = 0
  stack = [root]
  while stack:
    directory = stack.pop()
    try:
      if _is_link(directory):
        skipped.append(str(directory))
        continue
      depth = len(directory.parts) - root_parts
      if depth > max_depth:
        continue
      entries = list(os.scandir(directory))
    except OSError:
      skipped.append(str(directory))
      continue
    for entry in entries:
      path = Path(entry.path)
      try:
        if entry.is_symlink():
          skipped.append(str(path))
        elif entry.is_dir(follow_symlinks=False):
          if depth < max_depth:
            stack.append(path)
        elif entry.is_file(follow_symlinks=False):
          files.append(path)
          seen += 1
          if seen >= max_files:
            return files, skipped, seen, True
      except OSError:
        skipped.append(str(path))
  return files, skipped, seen, False


def _verify_adapter_candidate(path: Path, *, expected_head: str, expected_branch: str) -> dict[str, Any]:
  try:
    verified = verify_adapter_output(path, expected_source_head=expected_head, expected_source_branch=expected_branch)
    return {"path": str(path), "status": "VERIFIED", "verification": verified}
  except (OSError, ValueError, TypeError, OverflowError) as exc:
    return {"path": str(path), "status": "INVALID", "reason": str(exc)}


def discover_root(root: Path, *, expected_head: str, expected_branch: str, max_depth: int,
                  max_files: int, verify_paired: bool) -> dict[str, Any]:
  try:
    accessible = root.exists() and root.is_dir() and not _is_link(root)
  except OSError:
    accessible = False
  if not accessible:
    return {"root": str(root), "accessible": False, "filesScanned": 0, "truncated": False,
            "verifiedPaired": [], "invalidPaired": [], "loosePaired": [], "shadowOutputs": [],
            "rawRouteLogs": [], "skipped": []}

  files, skipped, seen, truncated = _walk_root(root, max_depth=max_depth, max_files=max_files)
  by_dir: dict[Path, dict[str, list[str]]] = defaultdict(lambda: defaultdict(list))
  shadow_outputs: list[str] = []
  raw_logs: list[str] = []
  for path in files:
    kind = _file_kind(path.name)
    if kind:
      by_dir[path.parent][kind].append(str(path))
      if kind == "raw_route_log":
        raw_logs.append(str(path))
    if path.suffix.lower() == ".jsonl" and _looks_shadow_jsonl(path):
      shadow_outputs.append(str(path))
  verified_paired: list[dict[str, Any]] = []
  invalid_paired: list[dict[str, Any]] = []
  loose_paired: list[dict[str, Any]] = []
  candidate_dirs = sorted({path.parent for path in files}, key=lambda p: str(p))
  required = set(ADAPTER_OUTPUT_FILES)
  for directory in candidate_dirs:
    names = _safe_names(directory)
    if names is None:
      continue
    if required <= names:
      item = (_verify_adapter_candidate(directory, expected_head=expected_head, expected_branch=expected_branch)
              if verify_paired else {"path": str(directory), "status": "FOUND_NOT_VERIFIED"})
      (verified_paired if item["status"] == "VERIFIED" else invalid_paired).append(item)
      continue
    kinds = by_dir.get(directory, {})
    has_small = bool(kinds.get("small_jsonl"))
    has_big = bool(kinds.get("big_jsonl"))
    has_paired = bool(kinds.get("paired_jsonl"))
    if (has_small and has_big) or has_paired:
      loose_paired.append({
        "directory": str(directory),
        "small": sorted(kinds.get("small_jsonl", [])),
        "big": sorted(kinds.get("big_jsonl", [])),
        "paired": sorted(kinds.get("paired_jsonl", [])),
        "status": "PROVENANCE_REQUIRED",
      })

  return {
    "root": str(root), "accessible": True, "filesScanned": seen, "truncated": truncated,
    "verifiedPaired": verified_paired, "invalidPaired": invalid_paired, "loosePaired": loose_paired,
    "shadowOutputs": sorted(set(shadow_outputs)), "rawRouteLogs": sorted(set(raw_logs)),
    "skipped": sorted(set(skipped))[:200],
  }


def _overall_state(roots: list[dict[str, Any]]) -> tuple[str, list[str]]:
  verified = sum(len(root["verifiedPaired"]) for root in roots)
  invalid = sum(len(root["invalidPaired"]) for root in roots)
  loose = sum(len(root["loosePaired"]) for root in roots)
  shadow = sum(len(root["shadowOutputs"]) for root in roots)
  raw = sum(len(root["rawRouteLogs"]) for root in roots)
  accessible = sum(1 for root in roots if root["accessible"])
  if verified:
    return "VERIFIED_PAIRED_EVIDENCE_FOUND", ["run provided_paired_offline review on the verified evidence directory"]
  if invalid:
    return "INVALID_PAIRED_EVIDENCE_FOUND", ["preserve the candidate unchanged and inspect its failed provenance/receipt verification"]
  if loose:
    return "LOOSE_PAIRED_CANDIDATE_FOUND", ["recover exact source/model/input provenance before adapter intake"]
  if shadow:
    return "SHADOW_OUTPUT_ONLY_FOUND", ["locate the matching active-model output and exact common-input provenance"]
  if raw:
    return "RAW_ROUTE_ONLY_FOUND", ["raw route logs alone cannot reconstruct same-input BIG/SMALL model outputs"]
  if accessible:
    return "NO_PAIRED_EVIDENCE_FOUND", ["collect new paired evidence only after the commissioning gates authorize it"]
  return "NO_ACCESSIBLE_ROOTS", ["retry discovery from a machine/network where the evidence storage is mounted"]


def build_report(roots: list[Path], *, expected_head: str, expected_branch: str, max_depth: int,
                 max_files: int, verify_paired: bool) -> dict[str, Any]:
  root_reports = [discover_root(root, expected_head=expected_head, expected_branch=expected_branch,
                                max_depth=max_depth, max_files=max_files, verify_paired=verify_paired)
                  for root in roots]
  state, next_actions = _overall_state(root_reports)
  return {
    "schemaVersion": 1, "stage": STAGE, "state": state,
    "expectedSource": {"head": expected_head, "branch": expected_branch},
    "roots": root_reports, "nextActions": next_actions, **FALSE_BOUNDARY,
  }


def main() -> int:
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument("--root", type=Path, action="append", required=True,
                      help="local or mounted evidence root; repeat for multiple roots")
  parser.add_argument("--expected-source-head", required=True)
  parser.add_argument("--expected-source-branch", required=True)
  parser.add_argument("--max-depth", type=int, default=8)
  parser.add_argument("--max-files", type=int, default=100000)
  parser.add_argument("--no-verify-paired", action="store_true")
  parser.add_argument("--output", type=Path)
  args = parser.parse_args()
  if args.max_depth < 0 or args.max_depth > 64:
    raise SystemExit("--max-depth must be between 0 and 64")
  if args.max_files < 1:
    raise SystemExit("--max-files must be positive")
  report = build_report(args.root, expected_head=args.expected_source_head,
                        expected_branch=args.expected_source_branch, max_depth=args.max_depth,
                        max_files=args.max_files, verify_paired=not args.no_verify_paired)
  text = json.dumps(report, indent=2, ensure_ascii=False, allow_nan=False) + "\n"
  print(text, end="")
  if args.output:
    if args.output.exists() or _is_link(args.output):
      raise SystemExit("output already exists; choose a new path")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(text, encoding="utf-8")
  return 0 if report["state"] == "VERIFIED_PAIRED_EVIDENCE_FOUND" else 2


if __name__ == "__main__":
  raise SystemExit(main())
