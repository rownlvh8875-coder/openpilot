#!/usr/bin/env python3
"""Classify upstream carrot-wip drift against reviewed eGPU and local-v6 boundaries."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
from typing import Mapping

# Reviewed 2026-09-08 after upstream radar CUT-OUT + route-bundle changes.
# eGPU/modeld/tinygrad critical blobs were unchanged from 6f4c00e, while the
# v6-sensitive radar/longitudinal additions below were explicitly inspected.
REVIEWED_HEAD = "a92d3a787e84a29949ca2b802f6a78fc9b580e87"
DEFAULT_UPSTREAM = "https://github.com/ajouatom/openpilot.git"
DEFAULT_BRANCH = "carrot-wip"
TEMP_REF = "refs/egpu-integrated/upstream-carrot-wip"
CRITICAL_PATHS = (
  "openpilot/selfdrive/modeld/modeld.py",
  "openpilot/selfdrive/modeld/fill_model_msg.py",
  "openpilot/selfdrive/modeld/parse_model_outputs.py",
  "openpilot/selfdrive/modeld/helpers.py",
  "openpilot/selfdrive/modeld/big_model.py",
  "openpilot/system/hardware/usbgpu.py",
  "openpilot/common/usbgpu_bus_lock.py",
  "openpilot/system/manager/process_config.py",
  "launch_chffrplus.sh",
  "launch_openpilot.sh",
  "launch_env.sh",
  "tinygrad_repo/tinygrad/runtime/ops_amd.py",
  "tinygrad_repo/tinygrad/runtime/support/am/ip.py",
  "tinygrad_repo/tinygrad/runtime/support/usb.py",
  "tinygrad_repo/extra/amdpci/am_smi.py",
  # Tree IDs include added/deleted files, not just the known leaf files.
  "tinygrad_repo/tinygrad/runtime/support/am",
  "tinygrad_repo/tinygrad/runtime/support/amd.py",
  "tinygrad_repo/tinygrad/runtime/support/compiler_amd.py",
  "tinygrad_repo/tinygrad/runtime/autogen/am",
  "tinygrad_repo/tinygrad/runtime/autogen/amd",
  "tinygrad_repo/tinygrad/runtime/autogen/amd_gpu.py",
  "tinygrad_repo/tinygrad/runtime/autogen/amdgpu_drm.py",
  "tinygrad_repo/tinygrad/runtime/autogen/amdgpu_kd.py",
  "tinygrad_repo/tinygrad/runtime/autogen/libusb.py",
  "tinygrad_repo/extra/amdpci",
)
PROVENANCE_PATHS = (
  "openpilot/selfdrive/carrot/radar/tools/radar_web_export.py",
  "tools/carrot_route_vault/build_bundle.py",
  "opendbc_repo/opendbc",
)
# These local-v6 additions are deliberately absent from the reviewed upstream.
# Their appearance upstream still requires review; any other missing path fails.
UPSTREAM_ABSENT_LOCAL_PATHS = frozenset({
  "openpilot/selfdrive/controls/lib/h1_observability.py",
  "opendbc_repo/opendbc/dbc/generator/hyundai/hyundai_canfd_radar.dbc",
})
V6_MIGRATION_PATHS = (
  "openpilot/cereal/custom.capnp",
  "openpilot/cereal/log.capnp",
  "openpilot/cereal/services.py",
  "openpilot/selfdrive/carrot/carrot_functions.py",
  "openpilot/selfdrive/controls/lib/longitudinal_planner.py",
  "openpilot/selfdrive/controls/plannerd.py",
  "openpilot/selfdrive/controls/lib/h1_observability.py",
  "opendbc_repo/opendbc/dbc/generator/hyundai/hyundai_canfd_radar.dbc",
  # Radar CUT-OUT / future-headway behavior added upstream on 2026-09-08.
  # These are watched as v6 integration surfaces because they can change
  # longitudinal behavior even though they do not touch eGPU/modeld itself.
  "openpilot/selfdrive/carrot/radar/radard_dpath.py",
  "openpilot/selfdrive/carrot/radar_motion/controller.py",
  "openpilot/selfdrive/carrot/radar_motion/trajectory_cutout.py",
  "openpilot/selfdrive/controls/lib/longitudinal_cutout.py",
  "openpilot/selfdrive/controls/lib/longitudinal_mpc_lib/long_mpc.py",
)


def run(*args: str) -> str:
  p = subprocess.run(args, text=True, capture_output=True, check=False)
  if p.returncode != 0:
    raise RuntimeError(p.stderr.strip() or "command failed")
  return p.stdout.strip()


def blob(ref: str, path: str) -> str | None:
  # ls-tree distinguishes an absent path from a missing/corrupt Git revision.
  entry = run("git", "ls-tree", ref, "--", path)
  return entry.split()[2] if entry else None


def classify(reviewed_head: str, upstream_head: str,
             reviewed_blobs: Mapping[str, str | None], upstream_blobs: Mapping[str, str | None],
             *, equivalent_label: str = "CODE_EQUIVALENT_HEAD_DRIFT",
             review_label: str = "REVIEW_REQUIRED",
             allowed_absent: frozenset[str] = frozenset()) -> tuple[str, list[str]]:
  changed = sorted(p for p in reviewed_blobs.keys() | upstream_blobs.keys()
                   if reviewed_blobs.get(p) != upstream_blobs.get(p)
                   or (reviewed_blobs.get(p) is None and p not in allowed_absent))
  if upstream_head == reviewed_head and not changed:
    return "EXACT_REVIEWED", []
  if not changed:
    return equivalent_label, []
  return review_label, changed


def compare_paths(paths: tuple[str, ...], upstream_head: str) -> tuple[dict[str, str | None], dict[str, str | None]]:
  return ({p: blob(REVIEWED_HEAD, p) for p in paths}, {p: blob(upstream_head, p) for p in paths})


def rows(paths: tuple[str, ...], reviewed: Mapping[str, str | None], upstream: Mapping[str, str | None]) -> list[dict]:
  return [
    {"path": p, "reviewedBlob": reviewed[p], "upstreamBlob": upstream[p], "changed": reviewed[p] != upstream[p]}
    for p in paths
  ]


def main() -> int:
  ap = argparse.ArgumentParser()
  ap.add_argument("--upstream", default=DEFAULT_UPSTREAM)
  ap.add_argument("--branch", default=DEFAULT_BRANCH)
  ap.add_argument("--upstream-ref", help="Compare an already-fetched local Git ref without network access")
  ap.add_argument("--output", type=Path)
  args = ap.parse_args()

  if args.upstream_ref:
    upstream_head = run("git", "rev-parse", "--verify", args.upstream_ref + "^{commit}")
  else:
    run("git", "fetch", "--no-tags", "--force", args.upstream,
        f"refs/heads/{args.branch}:{TEMP_REF}")
    # Bind both identity and object comparisons to the same fetched commit.
    upstream_head = run("git", "rev-parse", "--verify", TEMP_REF + "^{commit}")
  run("git", "rev-parse", "--verify", REVIEWED_HEAD + "^{commit}")

  reviewed_critical, upstream_critical = compare_paths(CRITICAL_PATHS, upstream_head)
  integration_status, critical_changed = classify(REVIEWED_HEAD, upstream_head, reviewed_critical, upstream_critical)

  reviewed_v6, upstream_v6 = compare_paths(V6_MIGRATION_PATHS, upstream_head)
  v6_status, v6_changed = classify(
    REVIEWED_HEAD, upstream_head, reviewed_v6, upstream_v6,
    equivalent_label="NO_V6_PATH_DRIFT",
    review_label="V6_REBASE_REVIEW_REQUIRED",
    allowed_absent=UPSTREAM_ABSENT_LOCAL_PATHS,
  )

  reviewed_provenance, upstream_provenance = compare_paths(PROVENANCE_PATHS, upstream_head)
  provenance_status, provenance_changed = classify(
    REVIEWED_HEAD, upstream_head, reviewed_provenance, upstream_provenance,
    equivalent_label="NO_PROVENANCE_PATH_DRIFT",
  )
  safe = (integration_status != "REVIEW_REQUIRED" and v6_status != "V6_REBASE_REVIEW_REQUIRED"
          and provenance_status != "REVIEW_REQUIRED")
  report = {
    "schemaVersion": 4,
    "reviewedHead": REVIEWED_HEAD,
    "upstreamHead": upstream_head,
    "branch": args.branch,
    "integrationBoundaryStatus": integration_status,
    "v6RebaseStatus": v6_status,
    "provenanceStatus": provenance_status,
    "reviewStatus": "REVIEWED_COMPATIBLE" if safe else "REVIEW_REQUIRED",
    "upstreamAbsentLocalPaths": sorted(UPSTREAM_ABSENT_LOCAL_PATHS),
    "criticalPaths": rows(CRITICAL_PATHS, reviewed_critical, upstream_critical),
    "changedCriticalPaths": critical_changed,
    "v6MigrationPaths": rows(V6_MIGRATION_PATHS, reviewed_v6, upstream_v6),
    "changedV6MigrationPaths": v6_changed,
    "provenancePaths": rows(PROVENANCE_PATHS, reviewed_provenance, upstream_provenance),
    "changedProvenancePaths": provenance_changed,
    "safeToReuseReviewedIntegrationBoundary": integration_status != "REVIEW_REQUIRED",
    "safeToReuseCurrentV6MigrationWithoutRebaseReview": v6_status != "V6_REBASE_REVIEW_REQUIRED",
    "overallCompatible": safe,
    "controlAuthorization": False,
    "hardwareValidation": False,
  }
  text = json.dumps(report, indent=2, ensure_ascii=False) + "\n"
  print(text, end="")
  if args.output:
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(text, encoding="utf-8")
  return 0 if safe else 2


if __name__ == "__main__":
  raise SystemExit(main())
