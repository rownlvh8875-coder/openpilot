#!/usr/bin/env python3
"""Classify upstream carrot-wip drift against the reviewed eGPU integration boundary."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
from typing import Mapping

REVIEWED_HEAD = "6f4c00e625dc3d41d3776427a272a5c3fed75e6c"
DEFAULT_UPSTREAM = "https://github.com/ajouatom/openpilot.git"
DEFAULT_BRANCH = "carrot-wip"
TEMP_REF = "refs/egpu-integrated/upstream-carrot-wip"
CRITICAL_PATHS = (
  "openpilot/selfdrive/modeld/modeld.py",
  "openpilot/selfdrive/modeld/fill_model_msg.py",
  "openpilot/selfdrive/modeld/helpers.py",
  "openpilot/selfdrive/modeld/big_model.py",
  "openpilot/system/hardware/usbgpu.py",
  "openpilot/common/usbgpu_bus_lock.py",
  "openpilot/system/manager/process_config.py",
  "launch_chffrplus.sh",
  "tinygrad_repo/tinygrad/runtime/ops_amd.py",
  "tinygrad_repo/tinygrad/runtime/support/am/ip.py",
  "tinygrad_repo/tinygrad/runtime/support/usb.py",
  "tinygrad_repo/extra/amdpci/am_smi.py",
)


def run(*args: str) -> str:
  p = subprocess.run(args, text=True, capture_output=True, check=False)
  if p.returncode != 0:
    raise RuntimeError(p.stderr.strip() or "command failed")
  return p.stdout.strip()


def blob(ref: str, path: str) -> str | None:
  p = subprocess.run(["git", "rev-parse", f"{ref}:{path}"], text=True, capture_output=True, check=False)
  return p.stdout.strip() if p.returncode == 0 else None


def classify(reviewed_head: str, upstream_head: str,
             reviewed_blobs: Mapping[str, str | None], upstream_blobs: Mapping[str, str | None]) -> tuple[str, list[str]]:
  changed = [p for p in reviewed_blobs if reviewed_blobs[p] != upstream_blobs.get(p)]
  if upstream_head == reviewed_head and not changed:
    return "EXACT_REVIEWED", []
  if not changed:
    return "CODE_EQUIVALENT_HEAD_DRIFT", []
  return "REVIEW_REQUIRED", changed


def main() -> int:
  ap = argparse.ArgumentParser()
  ap.add_argument("--upstream", default=DEFAULT_UPSTREAM)
  ap.add_argument("--branch", default=DEFAULT_BRANCH)
  ap.add_argument("--output", type=Path)
  args = ap.parse_args()

  ls = run("git", "ls-remote", args.upstream, f"refs/heads/{args.branch}")
  if not ls:
    raise SystemExit("upstream branch not found")
  upstream_head = ls.split()[0]
  run("git", "fetch", "--no-tags", "--force", args.upstream,
      f"refs/heads/{args.branch}:{TEMP_REF}")

  reviewed = {p: blob(REVIEWED_HEAD, p) for p in CRITICAL_PATHS}
  upstream = {p: blob(TEMP_REF, p) for p in CRITICAL_PATHS}
  status, changed = classify(REVIEWED_HEAD, upstream_head, reviewed, upstream)
  report = {
    "schemaVersion": 1,
    "reviewedHead": REVIEWED_HEAD,
    "upstreamHead": upstream_head,
    "branch": args.branch,
    "status": status,
    "criticalPaths": [
      {"path": p, "reviewedBlob": reviewed[p], "upstreamBlob": upstream[p], "changed": reviewed[p] != upstream[p]}
      for p in CRITICAL_PATHS
    ],
    "changedCriticalPaths": changed,
    "safeToReuseReviewedIntegrationBoundary": status != "REVIEW_REQUIRED",
  }
  text = json.dumps(report, indent=2, ensure_ascii=False) + "\n"
  print(text, end="")
  if args.output:
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(text, encoding="utf-8")
  return 0 if status != "REVIEW_REQUIRED" else 2


if __name__ == "__main__":
  raise SystemExit(main())
