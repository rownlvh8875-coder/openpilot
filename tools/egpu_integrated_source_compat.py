#!/usr/bin/env python3
"""Classify upstream carrot-wip drift against reviewed eGPU and local-v6 boundaries."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import subprocess
from typing import Mapping

# The complete upstream delta was reviewed and explicitly dispositioned. Only
# six offline radar files are selected; Tesla/control/CAN changes are retained
# at the prior integrated source. This is not full upstream integration.
PREVIOUS_REVIEWED_HEAD = "a92d3a787e84a29949ca2b802f6a78fc9b580e87"
REVIEWED_HEAD = "b7ab68addc29c3b9dc8f02023de36dba7109ed8c"
RETAINED_SOURCE_HEAD = "8997be8b7cbc9f8b978ef942b7c9d577bf76f790"
SELECTED_SOURCE_HEAD = "fdac75aedb7afe280cd67590fd02f693a4eb72d5"
ROOT = Path(__file__).resolve().parents[1]
DISPOSITION_PATH = "config/egpu_integrated_upstream_disposition.json"
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
  # Whole trees detect future newly named/deleted control and CAN sources.
  "openpilot/selfdrive/controls",
  "panda/board",
)
PROVENANCE_PATHS = (
  "openpilot/selfdrive/carrot/radar/tools/radar_web_export.py",
  "tools/carrot_route_vault/build_bundle.py",
  "opendbc_repo/opendbc",
  "tools/carrot_route_vault",
)
SELECTED_RADAR_PATHS = frozenset({
  "openpilot/selfdrive/carrot/radar/tools/radar_web_export.py",
  "tools/carrot_route_vault/README.md",
  "tools/carrot_route_vault/radar_view.js",
  "tools/carrot_route_vault/tests/test_radar.py",
  "tools/carrot_route_vault/tests/test_viewer.py",
  "tools/carrot_route_vault/viewer.py",
})
RETAINED_TESLA_PATHS = frozenset({
  "docs/user/en/settings.md", "docs/user/en/tesla.md", "docs/user/ko/settings.md", "docs/user/ko/tesla.md",
  "opendbc_repo/opendbc/car/tesla/carstate.py", "opendbc_repo/opendbc/car/tesla/tests/test_tesla.py",
  "openpilot/selfdrive/carrot_settings.json", "openpilot/selfdrive/controls/controlsd.py",
  "openpilot/selfdrive/controls/tests/test_controlsd.py", "panda/board/drivers/can_common.h",
  "panda/board/drivers/can_common_declarations.h", "panda/board/main.c",
  "panda/tests/libpanda/libpanda_py.py", "panda/tests/test_ignition_can.py",
})
RETAINED_ABSENT_PATHS = frozenset({"openpilot/selfdrive/controls/tests/test_controlsd.py", "panda/tests/test_ignition_can.py"})
PROTECTED_LOCAL_TREES = frozenset({"opendbc_repo/opendbc", "openpilot/selfdrive/controls", "panda"})
SOURCE_SUFFIXES = frozenset({
  ".py", ".pyi", ".pyx", ".pxd", ".c", ".h", ".cc", ".cpp", ".hpp", ".capnp", ".dbc", ".js", ".mjs", ".ts",
  ".sh", ".bash", ".json", ".toml", ".yaml", ".yml", ".ini", ".cfg", ".mk", ".cmake",
})
SOURCE_FILENAMES = frozenset({"SConstruct", "SConscript", "Makefile", "Dockerfile", "CMakeLists.txt"})
GENERATED_CACHE_DIRS = frozenset({"__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache"})
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
  p = subprocess.run(args, text=True, encoding="utf-8", capture_output=True, check=False, cwd=ROOT,
                     env=dict(os.environ, GIT_NO_LAZY_FETCH="1", GIT_TERMINAL_PROMPT="0"))
  if p.returncode != 0:
    raise RuntimeError(p.stderr.strip() or "command failed")
  return p.stdout.strip()


def run_input(input_text: str, *args: str) -> str:
  p = subprocess.run(args, input=input_text, text=True, encoding="utf-8", capture_output=True, check=False, cwd=ROOT,
                     env=dict(os.environ, GIT_NO_LAZY_FETCH="1", GIT_TERMINAL_PROMPT="0"))
  if p.returncode:
    raise RuntimeError(p.stderr.strip() or "command with input failed")
  return p.stdout.strip()


def blob(ref: str, path: str) -> str | None:
  # ls-tree distinguishes an absent path from a missing/corrupt Git revision.
  entry = run("git", "ls-tree", ref, "--", path)
  return entry.split()[2] if entry else None


def git_object(ref: str, path: str) -> dict[str, str] | None:
  entries = run("git", "ls-tree", "-z", ref, "--", path).split("\0")
  entries = [entry for entry in entries if entry]
  if not entries:
    return None
  if len(entries) != 1:
    raise ValueError(f"one exact Git object required: {path}")
  metadata, actual_path = entries[0].split("\t", 1)
  mode, kind, oid = metadata.split()
  if actual_path != path:
    raise ValueError(f"Git path mismatch: {path}")
  return {"mode": mode, "type": kind, "oid": oid}


def _exact_keys(value, expected: set[str], label: str) -> None:
  if not isinstance(value, dict) or set(value) != expected:
    raise ValueError(f"{label}: exact required fields missing or extra fields supplied")


def _object(value, *, allow_absent: bool, tree: bool = False) -> None:
  if value is None and allow_absent:
    return
  _exact_keys(value, {"mode", "type", "oid"}, "Git object")
  modes = {"040000"} if tree else {"100644", "100755"}
  if value["mode"] not in modes or value["type"] != ("tree" if tree else "blob"):
    raise ValueError("unexpected Git mode or object type")
  if not isinstance(value["oid"], str) or re.fullmatch(r"[0-9a-f]{40}", value["oid"]) is None:
    raise ValueError("full lowercase Git object ID required")


def _unique_pairs(pairs):
  result = {}
  for key, value in pairs:
    if key in result:
      raise ValueError(f"duplicate disposition JSON key: {key}")
    result[key] = value
  return result


def load_disposition() -> dict:
  path = ROOT / DISPOSITION_PATH
  if path.is_symlink() or path.is_junction() or not path.is_file():
    raise ValueError("regular committed disposition file required")
  value = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_unique_pairs)
  # A modified allow-list is not itself reviewed provenance.
  committed = git_object("HEAD", DISPOSITION_PATH)
  if committed is None or committed["type"] != "blob" or local_path_issues(DISPOSITION_PATH, committed):
    raise ValueError("disposition record must be committed and unchanged")
  return value


def validate_disposition(record: dict) -> None:
  _exact_keys(record, {"schemaVersion", "previousReviewedHead", "reviewedUpstreamHead", "retainedSourceHead", "selectedSourceHead",
                       "paths", "localProtectedTrees"}, "disposition")
  if type(record["schemaVersion"]) is not int or record["schemaVersion"] != 1:
    raise ValueError("unsupported disposition schema")
  expected_heads = {"previousReviewedHead": PREVIOUS_REVIEWED_HEAD, "reviewedUpstreamHead": REVIEWED_HEAD,
                    "retainedSourceHead": RETAINED_SOURCE_HEAD, "selectedSourceHead": SELECTED_SOURCE_HEAD}
  for key, expected in expected_heads.items():
    if record[key] != expected or run("git", "cat-file", "-t", record[key]) != "commit":
      raise ValueError(f"unreviewed disposition source commit: {key}")
  if not isinstance(record["paths"], list) or not isinstance(record["localProtectedTrees"], list):
    raise ValueError("disposition path arrays required")
  expected_paths = SELECTED_RADAR_PATHS | RETAINED_TESLA_PATHS | {"AGENTS.md"}
  seen = set()
  for row in record["paths"]:
    _exact_keys(row, {"path", "disposition", "previousUpstreamObject", "reviewedUpstreamObject", "expectedIntegratedObject"}, "disposition row")
    path = row["path"]
    if not isinstance(path, str) or path not in expected_paths or path in seen:
      raise ValueError("unexpected or duplicate disposition path")
    seen.add(path)
    expected_decision = "SELECTIVE_INTEGRATION" if path in SELECTED_RADAR_PATHS else "RETAIN_USER_INSTRUCTIONS" if path == "AGENTS.md" else "RETAIN_INTEGRATED"
    if row["disposition"] != expected_decision:
      raise ValueError(f"unreviewed path disposition: {path}")
    for key, ref in (("previousUpstreamObject", PREVIOUS_REVIEWED_HEAD), ("reviewedUpstreamObject", REVIEWED_HEAD)):
      _object(row[key], allow_absent=path in RETAINED_ABSENT_PATHS and key == "previousUpstreamObject")
      if row[key] != git_object(ref, path):
        raise ValueError(f"disposition does not match reviewed upstream objects: {path}")
    _object(row["expectedIntegratedObject"], allow_absent=path in RETAINED_ABSENT_PATHS)
    if path not in SELECTED_RADAR_PATHS:
      if row["expectedIntegratedObject"] != git_object(RETAINED_SOURCE_HEAD, path):
        raise ValueError(f"retained source object changed in disposition: {path}")
    elif row["expectedIntegratedObject"] != git_object(SELECTED_SOURCE_HEAD, path):
      raise ValueError(f"selected source object does not match reviewed radar commit: {path}")
    # Selected sources can preserve reviewed local hardening instead of copying
    # upstream verbatim; their distinct integrated objects are pinned explicitly
    # and must match the committed checkout, index and working file below.
  actual_delta = set(run("git", "diff", "--name-only", "--no-renames", PREVIOUS_REVIEWED_HEAD, REVIEWED_HEAD).splitlines())
  if seen != expected_paths or seen != actual_delta:
    raise ValueError("disposition must cover the complete reviewed upstream delta exactly once")
  seen_trees = set()
  for row in record["localProtectedTrees"]:
    _exact_keys(row, {"path", "expectedIntegratedObject"}, "protected tree")
    path = row["path"]
    if not isinstance(path, str) or path not in PROTECTED_LOCAL_TREES or path in seen_trees:
      raise ValueError("unexpected or duplicate protected local tree")
    seen_trees.add(path)
    _object(row["expectedIntegratedObject"], allow_absent=False, tree=True)
    if row["expectedIntegratedObject"] != git_object(RETAINED_SOURCE_HEAD, path):
      raise ValueError(f"protected tree is not the retained source: {path}")
  if seen_trees != PROTECTED_LOCAL_TREES:
    raise ValueError("all protected local trees required")


def _regular_worktree_path(path: str, *, directory: bool = False) -> bool:
  actual = ROOT / path
  if not actual.resolve().is_relative_to(ROOT.resolve()):
    return False
  for item in (actual, *actual.parents):
    if item == ROOT:
      break
    if item.is_symlink() or item.is_junction():
      return False
  return actual.is_dir() if directory else actual.is_file()


def local_tree_issues(path: str, expected: dict) -> list[str]:
  """Verify every retained tracked leaf, without trusting index clean hints."""
  if not _regular_worktree_path(path, directory=True):
    return ["nonregular_worktree_tree"]
  entries = [entry for entry in run("git", "ls-tree", "-r", "-z", expected["oid"]).split("\0") if entry]
  leaves = {}
  for entry in entries:
    metadata, relative = entry.split("\t", 1)
    mode, kind, oid = metadata.split()
    # The reviewed protected trees contain regular blobs only; a future link,
    # gitlink, or unsupported mode requires explicit review.
    if kind != "blob" or mode not in {"100644", "100755"}:
      return ["unsupported_protected_tree_entry"]
    leaves[f"{path}/{relative}"] = {"mode": mode, "oid": oid}
  actual_index = [entry for entry in run("git", "ls-files", "--stage", "-z", "--", path).split("\0") if entry]
  expected_index = [f"{entry['mode']} {entry['oid']} 0\t{name}" for name, entry in leaves.items()]
  issues = []
  if sorted(actual_index) != sorted(expected_index):
    issues.append("index_tree_mismatch")
  regular_paths = []
  for name, entry in sorted(leaves.items()):
    if not _regular_worktree_path(name):
      issues.append(f"nonregular_worktree_source:{name}")
      continue
    if os.name != "nt" and bool((ROOT / name).stat().st_mode & 0o111) != (entry["mode"] == "100755"):
      issues.append(f"worktree_mode_mismatch:{name}")
    regular_paths.append(name)
  if regular_paths:
    # --stdin-paths applies each file's Git clean filters, including text EOL
    # handling, and performs all hashes in one subprocess per protected tree.
    input_text = "".join(json.dumps(name, ensure_ascii=False) + "\n" for name in regular_paths)
    hashes = run_input(input_text, "git", "hash-object", "--stdin-paths").splitlines()
    raw_hashes = run_input(input_text, "git", "hash-object", "--no-filters", "--stdin-paths").splitlines()
    if len(hashes) != len(regular_paths) or len(raw_hashes) != len(regular_paths):
      raise ValueError("incomplete protected worktree hash result")
    # Some historical DBC/vendor-header blobs contain CRLF even though current
    # attributes mark text=auto. Exact original bytes remain valid; ordinary
    # checkout EOL conversion is valid only when Git's filtered hash matches.
    issues.extend(f"worktree_content_mismatch:{name}" for name, oid, raw_oid in zip(regular_paths, hashes, raw_hashes)
                  if leaves[name]["oid"] not in (oid, raw_oid))
  # Git status omits ignored files. Independently reject new ignored source and
  # configuration files, while leaving generated caches/logs outside this gate.
  for directory, folders, files in os.walk(ROOT / path, followlinks=False):
    keep = []
    for folder in folders:
      child = Path(directory) / folder
      if child.is_symlink() or child.is_junction():
        issues.append(f"nonregular_worktree_directory:{child.relative_to(ROOT).as_posix()}")
      elif folder not in GENERATED_CACHE_DIRS:
        keep.append(folder)
    folders[:] = keep
    for name in files:
      file_path = Path(directory) / name
      relative = file_path.relative_to(ROOT).as_posix()
      if relative not in leaves and (file_path.suffix.lower() in SOURCE_SUFFIXES or name in SOURCE_FILENAMES):
        issues.append(f"unreviewed_worktree_source:{relative}")
  return issues


def local_path_issues(path: str, expected: dict | None, *, checkout_ref: str = "HEAD") -> list[str]:
  issues = []
  if git_object(checkout_ref, path) != expected:
    issues.append("committed_object_mismatch")
  if run("git", "status", "--porcelain=v1", "--untracked-files=all", "--", path):
    issues.append("index_or_worktree_changed")
  actual = ROOT / path
  if expected is None:
    # Explicit absence includes ignored/untracked files, not just ls-tree.
    if actual.exists() or actual.is_symlink() or actual.is_junction():
      issues.append("expected_absent_path_exists")
  elif expected["type"] == "tree":
    issues.extend(local_tree_issues(path, expected))
  elif expected["type"] == "blob":
    if not _regular_worktree_path(path):
      issues.append("nonregular_worktree_source")
    else:
      index = run("git", "ls-files", "--stage", "--", path).splitlines()
      wanted = f"{expected['mode']} {expected['oid']} 0\t{path}"
      if index != [wanted]:
        issues.append("index_object_mismatch")
      # Apply the repository's Git clean filters, preserving LF/CRLF checkout
      # equivalence while checking actual working bytes even with index hints.
      if run("git", "hash-object", f"--path={path}", "--", str(actual)) != expected["oid"]:
        issues.append("worktree_content_mismatch")
  return issues


def disposition_report(record: dict, upstream_head: str) -> dict:
  validate_disposition(record)
  path_rows = []
  for row in record["paths"]:
    current_upstream = git_object(upstream_head, row["path"])
    path_rows.append({**row, "currentUpstreamObject": current_upstream,
                      "remoteChanged": current_upstream != row["reviewedUpstreamObject"],
                      "localIssues": local_path_issues(row["path"], row["expectedIntegratedObject"])})
  trees = [{**row, "localIssues": local_path_issues(row["path"], row["expectedIntegratedObject"])}
           for row in record["localProtectedTrees"]]
  changed_remote = [row["path"] for row in path_rows if row["remoteChanged"]]
  changed_local = sorted({row["path"] for row in path_rows + trees if row["localIssues"]})
  return {"status": "REVIEW_REQUIRED" if changed_remote or changed_local else "REVIEWED_WITH_EXCLUSIONS",
          "upstreamFullyIntegrated": False, "verificationScope": "WATCHED_BOUNDARIES_ONLY",
          "retainedSourceHead": RETAINED_SOURCE_HEAD, "localHead": run("git", "rev-parse", "HEAD"),
          "selectedSourceHead": SELECTED_SOURCE_HEAD,
          "paths": path_rows, "localProtectedTrees": trees, "changedRemotePaths": changed_remote,
          "changedLocalPaths": changed_local}


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
  try:
    disposition = disposition_report(load_disposition(), upstream_head)
  except (ValueError, RuntimeError, OSError, TypeError) as exc:
    disposition = {"status": "REVIEW_REQUIRED", "reason": str(exc), "upstreamFullyIntegrated": False,
                   "verificationScope": "WATCHED_BOUNDARIES_ONLY"}
  safe = (integration_status != "REVIEW_REQUIRED" and v6_status != "V6_REBASE_REVIEW_REQUIRED"
          and provenance_status != "REVIEW_REQUIRED" and disposition["status"] == "REVIEWED_WITH_EXCLUSIONS")
  report = {
    "schemaVersion": 5,
    "reviewedHead": REVIEWED_HEAD,
    "previousReviewedHead": PREVIOUS_REVIEWED_HEAD,
    "upstreamHead": upstream_head,
    "branch": args.branch,
    "integrationBoundaryStatus": integration_status,
    "v6RebaseStatus": v6_status,
    "provenanceStatus": provenance_status,
    "reviewStatus": "REVIEWED_WITH_EXCLUSIONS" if safe else "REVIEW_REQUIRED",
    "compatibleScope": "offline reviewed selective integration; WATCHED_BOUNDARIES_ONLY",
    "upstreamFullyIntegrated": False,
    "reviewedDisposition": disposition,
    "upstreamAbsentLocalPaths": sorted(UPSTREAM_ABSENT_LOCAL_PATHS),
    "criticalPaths": rows(CRITICAL_PATHS, reviewed_critical, upstream_critical),
    "changedCriticalPaths": critical_changed,
    "v6MigrationPaths": rows(V6_MIGRATION_PATHS, reviewed_v6, upstream_v6),
    "changedV6MigrationPaths": v6_changed,
    "provenancePaths": rows(PROVENANCE_PATHS, reviewed_provenance, upstream_provenance),
    "changedProvenancePaths": provenance_changed,
    "safeToReuseReviewedIntegrationBoundary": integration_status != "REVIEW_REQUIRED" and disposition["status"] == "REVIEWED_WITH_EXCLUSIONS",
    "safeToReuseCurrentV6MigrationWithoutRebaseReview": v6_status != "V6_REBASE_REVIEW_REQUIRED" and disposition["status"] == "REVIEWED_WITH_EXCLUSIONS",
    "overallCompatible": safe,
    "controlAuthorization": False,
    "hardwareValidation": False,
    "publicRoadAuthorization": False,
    "runtimeIntegrationAuthorization": False,
  }
  text = json.dumps(report, indent=2, ensure_ascii=False) + "\n"
  print(text, end="")
  if args.output:
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(text, encoding="utf-8")
  return 0 if safe else 2


if __name__ == "__main__":
  raise SystemExit(main())
