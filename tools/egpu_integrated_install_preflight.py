#!/usr/bin/env python3
"""Read-only installation preflight for carrot-wip-integrated-v6.

This tool never switches branches, stops processes, writes Params, reboots, or
modifies either checkout. It only reports whether the current comma state is
ready for a controlled all-features-OFF install/boot test.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
from typing import Any

EXPECTED_BRANCH = "carrot-wip-integrated-v6"
FEATURE_MARKERS = (
  Path("/data/egpu_integrated/observer_enabled"),
  Path("/data/egpu_integrated/telemetry_enabled"),
  Path("/tmp/egpu_integrated_shadow_tap.enable"),
)


def run_git(repo: Path, *args: str) -> tuple[int, str, str]:
  p = subprocess.run(["git", "-C", str(repo), *args], text=True, capture_output=True, check=False)
  return p.returncode, p.stdout.strip(), p.stderr.strip()


def read_param_bool(name: str) -> bool | None:
  path = Path("/data/params/d") / name
  try:
    raw = path.read_bytes().strip()
  except OSError:
    return None
  if raw in {b"1", b"true", b"True"}:
    return True
  if raw in {b"0", b"false", b"False", b""}:
    return False
  return None


def check(condition: bool, name: str, detail: Any) -> dict[str, Any]:
  return {"name": name, "pass": bool(condition), "detail": detail}


def main() -> int:
  ap = argparse.ArgumentParser()
  ap.add_argument("--live", type=Path, default=Path("/data/openpilot"))
  ap.add_argument("--target", type=Path, default=Path("/data/openpilot-egpu-integrated-v6-src"))
  ap.add_argument("--backup", type=Path, required=True)
  ap.add_argument("--expected-target-head", default=None)
  args = ap.parse_args()

  live = args.live.resolve()
  target = args.target.resolve()
  backup = args.backup.resolve()
  checks: list[dict[str, Any]] = []

  is_onroad = read_param_bool("IsOnroad")
  is_offroad = read_param_bool("IsOffroad")
  checks.append(check(is_onroad is False, "vehicle_not_onroad", is_onroad))
  checks.append(check(is_offroad is True, "vehicle_offroad", is_offroad))

  checks.append(check((live / ".git").exists(), "live_git_checkout", str(live)))
  checks.append(check((target / ".git").exists(), "target_git_checkout", str(target)))
  checks.append(check(backup.is_dir(), "backup_directory_exists", str(backup)))
  checks.append(check((backup / "tracked_changes.patch").is_file(), "tracked_patch_backup_exists", str(backup / "tracked_changes.patch")))
  checks.append(check((backup / "status.txt").is_file(), "status_backup_exists", str(backup / "status.txt")))

  if (live / ".git").exists():
    rc, live_head, err = run_git(live, "rev-parse", "HEAD")
    checks.append(check(rc == 0, "live_head_readable", live_head or err))
    rc, live_branch, err = run_git(live, "rev-parse", "--abbrev-ref", "HEAD")
    checks.append(check(rc == 0, "live_branch_readable", live_branch or err))
    rc, current_status, err = run_git(live, "status", "--porcelain=v1")
    checks.append(check(rc == 0, "live_status_readable", err or f"entries={len(current_status.splitlines())}"))
    try:
      saved_status = (backup / "status.txt").read_text(encoding="utf-8").rstrip("\n")
      checks.append(check(rc == 0 and current_status == saved_status, "live_tree_matches_backup_snapshot",
                          {"currentEntries": len(current_status.splitlines()), "savedEntries": len(saved_status.splitlines())}))
    except OSError as exc:
      checks.append(check(False, "live_tree_matches_backup_snapshot", type(exc).__name__))

  target_head = None
  if (target / ".git").exists():
    rc, target_head, err = run_git(target, "rev-parse", "HEAD")
    checks.append(check(rc == 0, "target_head_readable", target_head or err))
    rc, target_branch, err = run_git(target, "rev-parse", "--abbrev-ref", "HEAD")
    checks.append(check(rc == 0 and target_branch == EXPECTED_BRANCH, "target_branch_expected", target_branch or err))
    rc, target_status, err = run_git(target, "status", "--porcelain=v1")
    checks.append(check(rc == 0 and not target_status, "target_tree_clean", target_status or err or "clean"))
    if args.expected_target_head:
      checks.append(check(target_head == args.expected_target_head, "target_head_expected",
                          {"actual": target_head, "expected": args.expected_target_head}))

    verifier = target / "tools/apply_egpu_integrated_modeld_patch.py"
    if verifier.is_file():
      p = subprocess.run(["python3", str(verifier)], cwd=target, text=True, capture_output=True, check=False)
      checks.append(check(p.returncode == 0 and "byteRestoreVerification=PASS" in p.stdout,
                          "modeld_exact_restore_verification", (p.stdout + p.stderr).strip()[-1000:]))
    else:
      checks.append(check(False, "modeld_exact_restore_verification", "verifier missing"))

  active_markers = [str(path) for path in FEATURE_MARKERS if path.exists()]
  checks.append(check(not active_markers, "integrated_features_markers_off", active_markers or "all absent"))

  failed = [item for item in checks if not item["pass"]]
  report = {
    "schemaVersion": 1,
    "purpose": "all-features-off-install-preflight",
    "status": "PASS" if not failed else "HOLD",
    "live": str(live),
    "target": str(target),
    "backup": str(backup),
    "targetHead": target_head,
    "checks": checks,
    "failedChecks": [item["name"] for item in failed],
    "authorizations": {
      "installAuthorization": False,
      "rebootAuthorization": False,
      "publicRoadAuthorization": False,
      "controlAuthorization": False,
    },
    "interpretation": "PASS means prerequisites for a controlled install/boot test are present; it does not perform or authorize the install itself.",
  }
  print(json.dumps(report, indent=2, ensure_ascii=False))
  return 0 if not failed else 2


if __name__ == "__main__":
  raise SystemExit(main())
