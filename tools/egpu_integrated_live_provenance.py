#!/usr/bin/env python3
"""Create/verify an exact source-only fingerprint for the current live Carrot v6 tree.

Generated build products are deliberately ignored. This tool is read-only unless
--output is supplied, in which case it only writes the requested JSON manifest.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
from typing import Any

TRACKED_V6_PATHS = (
  "openpilot/cereal/custom.capnp",
  "openpilot/cereal/log.capnp",
  "openpilot/cereal/services.py",
  "openpilot/selfdrive/carrot/carrot_functions.py",
  "openpilot/selfdrive/controls/lib/longitudinal_planner.py",
  "openpilot/selfdrive/controls/plannerd.py",
)
SOURCE_V6_PATHS = TRACKED_V6_PATHS + (
  "openpilot/selfdrive/controls/lib/h1_observability.py",
  "opendbc_repo/opendbc/dbc/generator/hyundai/hyundai_canfd_radar.dbc",
)


def _run(repo: Path, *args: str) -> str:
  p = subprocess.run(["git", "-C", str(repo), *args], text=False, capture_output=True, check=False)
  if p.returncode != 0:
    raise RuntimeError(p.stderr.decode("utf-8", errors="replace").strip() or "git command failed")
  return p.stdout.decode("utf-8", errors="replace").strip()


def _run_bytes(repo: Path, *args: str) -> bytes:
  p = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, check=False)
  if p.returncode != 0:
    raise RuntimeError(p.stderr.decode("utf-8", errors="replace").strip() or "git command failed")
  return p.stdout


def sha256_bytes(data: bytes) -> str:
  return hashlib.sha256(data).hexdigest()


def file_digest(path: Path) -> str | None:
  try:
    return sha256_bytes(path.read_bytes())
  except OSError:
    return None


def build_snapshot(repo: Path) -> dict[str, Any]:
  repo = repo.resolve()
  head = _run(repo, "rev-parse", "HEAD")
  branch = _run(repo, "rev-parse", "--abbrev-ref", "HEAD")
  diff = _run_bytes(repo, "diff", "--binary", "--no-ext-diff", "HEAD", "--", *TRACKED_V6_PATHS)
  files = {path: file_digest(repo / path) for path in SOURCE_V6_PATHS}
  return {
    "schemaVersion": 1,
    "purpose": "live-v6-source-provenance",
    "repo": str(repo),
    "head": head,
    "branch": branch,
    "trackedDiffSha256": sha256_bytes(diff),
    "sourceFiles": files,
    "ignoredGeneratedFiles": True,
  }


def compare_snapshots(saved: dict[str, Any], current: dict[str, Any]) -> dict[str, Any]:
  mismatches: list[str] = []
  for key in ("head", "branch", "trackedDiffSha256"):
    if saved.get(key) != current.get(key):
      mismatches.append(key)
  saved_files = saved.get("sourceFiles") if isinstance(saved.get("sourceFiles"), dict) else {}
  current_files = current.get("sourceFiles") if isinstance(current.get("sourceFiles"), dict) else {}
  for path in sorted(set(saved_files) | set(current_files)):
    if saved_files.get(path) != current_files.get(path):
      mismatches.append(f"sourceFiles:{path}")
  return {
    "match": not mismatches,
    "mismatches": mismatches,
    "savedHead": saved.get("head"),
    "currentHead": current.get("head"),
    "savedBranch": saved.get("branch"),
    "currentBranch": current.get("branch"),
  }


def main() -> int:
  ap = argparse.ArgumentParser()
  sub = ap.add_subparsers(dest="command", required=True)
  snap = sub.add_parser("snapshot")
  snap.add_argument("--repo", type=Path, default=Path("/data/openpilot"))
  snap.add_argument("--output", type=Path)
  verify = sub.add_parser("verify")
  verify.add_argument("--repo", type=Path, default=Path("/data/openpilot"))
  verify.add_argument("--manifest", type=Path, required=True)
  args = ap.parse_args()

  current = build_snapshot(args.repo)
  if args.command == "snapshot":
    text = json.dumps(current, indent=2, ensure_ascii=False) + "\n"
    print(text, end="")
    if args.output:
      args.output.parent.mkdir(parents=True, exist_ok=True)
      args.output.write_text(text, encoding="utf-8")
    return 0

  saved = json.loads(args.manifest.read_text(encoding="utf-8"))
  result = compare_snapshots(saved, current)
  print(json.dumps(result, indent=2, ensure_ascii=False))
  return 0 if result["match"] else 2


if __name__ == "__main__":
  raise SystemExit(main())
