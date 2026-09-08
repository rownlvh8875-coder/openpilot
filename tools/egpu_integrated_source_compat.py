#!/usr/bin/env python3
"""Classify upstream carrot-wip drift against reviewed eGPU and local-v6 boundaries."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
from typing import Mapping

# Reviewed after merging ajouatom's 2026-09-08 radar cut-out series. The merge
# review confirmed all eGPU-critical blobs remained identical while the two v6
# overlap files (`log.capnp`, `longitudinal_planner.py`) merged in disjoint hunks.
REVIEWED_HEAD = "e8937726af50ac57681b560015076a5b596e960d"
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
V6_MIGRATION_PATHS = (
  "openpilot/cereal/custom.capnp",
  "openpilot/cereal/log.capnp",
  "openpilot/cereal/services.py",
  "openpilot/selfdrive/carrot/carrot_functions.py",
  "openpilot/selfdrive/controls/lib/longitudinal_planner.py",
  "openpilot/selfdrive/controls/plannerd.py",
  "openpilot/selfdrive/controls/lib/h1_observability.py",
  "opendbc_repo/opendbc/dbc/generator/hyundai/hyundai_canfd_radar.dbc",
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
             reviewed_blobs: Mapping[str, str | None], upstream_blobs: Mapping[str, str | None],
             *, equivalent_label: str = "CODE_EQUIVALENT_HEAD_DRIFT",
             review_label: str = "REVIEW_REQUIRED") -> tuple[str, list[str]]:
  changed = [p for p in reviewed_blobs if reviewed_blobs[p] != upstream_blobs.get(p)]
  if upstream_head == reviewed_head and not changed:
    return "EXACT_REVIEWED", []
  if not changed:
    return equivalent_label, []
  return review_label, changed


def compare_paths(paths: tuple[str, ...]) -> tuple[dict[str, str | None], dict[str, str | None]]:
  return ({p: blob(REVIEWED_HEAD, p) for p in paths}, {p: blob(TEMP_REF, p) for p in paths})


def rows(paths: tuple[str, ...], reviewed: Mapping[str, str | None], upstream: Mapping[str, str | None]) -> list[dict]:
  return [
    {"path": p, "reviewedBlob": reviewed[p], "upstreamBlob": upstream[p], "changed": reviewed[p] != upstream[p]}
    for p in paths
  ]


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

  reviewed_critical, upstream_critical = compare_paths(CRITICAL_PATHS)
  integration_status, critical_changed = classify(REVIEWED_HEAD, upstream_head, reviewed_critical, upstream_critical)

  reviewed_v6, upstream_v6 = compare_paths(V6_MIGRATION_PATHS)
  v6_status, v6_changed = classify(
    REVIEWED_HEAD, upstream_head, reviewed_v6, upstream_v6,
    equivalent_label="NO_V6_PATH_DRIFT",
    review_label="V6_REBASE_REVIEW_REQUIRED",
  )

  safe = integration_status != "REVIEW_REQUIRED" and v6_status != "V6_REBASE_REVIEW_REQUIRED"
  report = {
    "schemaVersion": 2,
    "reviewedHead": REVIEWED_HEAD,
    "upstreamHead": upstream_head,
    "branch": args.branch,
    "integrationBoundaryStatus": integration_status,
    "v6RebaseStatus": v6_status,
    "criticalPaths": rows(CRITICAL_PATHS, reviewed_critical, upstream_critical),
    "changedCriticalPaths": critical_changed,
    "v6MigrationPaths": rows(V6_MIGRATION_PATHS, reviewed_v6, upstream_v6),
    "changedV6MigrationPaths": v6_changed,
    "safeToReuseReviewedIntegrationBoundary": integration_status != "REVIEW_REQUIRED",
    "safeToReuseCurrentV6MigrationWithoutRebaseReview": v6_status != "V6_REBASE_REVIEW_REQUIRED",
    "overallCompatible": safe,
  }
  text = json.dumps(report, indent=2, ensure_ascii=False) + "\n"
  print(text, end="")
  if args.output:
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(text, encoding="utf-8")
  return 0 if safe else 2


if __name__ == "__main__":
  raise SystemExit(main())
