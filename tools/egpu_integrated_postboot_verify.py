#!/usr/bin/env python3
"""Read-only post-boot verification for an all-integrated-features-OFF install.

No Params, files, processes, branches, or power state are modified.
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


def run(repo: Path, *args: str) -> tuple[int, str]:
  p = subprocess.run(["git", "-C", str(repo), *args], text=True, capture_output=True, check=False)
  return p.returncode, (p.stdout + p.stderr).strip()


def read_param_bool(name: str) -> bool | None:
  try:
    raw = (Path("/data/params/d") / name).read_bytes().strip()
  except OSError:
    return None
  if raw in {b"1", b"true", b"True"}:
    return True
  if raw in {b"0", b"false", b"False", b""}:
    return False
  return None


def add(checks: list[dict[str, Any]], name: str, passed: bool, detail: Any) -> None:
  checks.append({"name": name, "pass": bool(passed), "detail": detail})


def main() -> int:
  ap = argparse.ArgumentParser()
  ap.add_argument("--live", type=Path, default=Path("/data/openpilot"))
  ap.add_argument("--expected-head", required=True)
  args = ap.parse_args()
  live = args.live.resolve()
  checks: list[dict[str, Any]] = []

  is_onroad = read_param_bool("IsOnroad")
  is_offroad = read_param_bool("IsOffroad")
  add(checks, "vehicle_not_onroad", is_onroad is False, is_onroad)
  add(checks, "vehicle_offroad", is_offroad is True, is_offroad)
  add(checks, "live_git_checkout", (live / ".git").exists(), str(live))

  head = branch = None
  if (live / ".git").exists():
    rc, text = run(live, "rev-parse", "HEAD")
    head = text.splitlines()[0] if rc == 0 and text else None
    add(checks, "expected_head", rc == 0 and head == args.expected_head,
        {"actual": head, "expected": args.expected_head})
    rc, text = run(live, "rev-parse", "--abbrev-ref", "HEAD")
    branch = text.splitlines()[0] if rc == 0 and text else None
    add(checks, "expected_branch", rc == 0 and branch == EXPECTED_BRANCH, branch)

    verifier = live / "tools/apply_egpu_integrated_modeld_patch.py"
    if verifier.is_file():
      p = subprocess.run(["python3", str(verifier)], cwd=live, text=True, capture_output=True, check=False)
      detail = (p.stdout + p.stderr).strip()[-1200:]
      add(checks, "modeld_exact_restore_verification",
          p.returncode == 0 and "byteRestoreVerification=PASS" in p.stdout, detail)
    else:
      add(checks, "modeld_exact_restore_verification", False, "verifier missing")

    compile_targets = [
      live / "openpilot/selfdrive/modeld/modeld.py",
      live / "openpilot/selfdrive/modeld/egpu_integration_observer.py",
      live / "openpilot/selfdrive/modeld/egpu_hardware_telemetry.py",
      live / "openpilot/selfdrive/modeld/egpu_integrated_shadow_tap.py",
    ]
    p = subprocess.run(["python3", "-m", "py_compile", *map(str, compile_targets)], text=True, capture_output=True, check=False)
    add(checks, "integrated_python_compile", p.returncode == 0, (p.stdout + p.stderr).strip()[-1200:] or "PASS")

  active_markers = [str(path) for path in FEATURE_MARKERS if path.exists()]
  add(checks, "integrated_features_off", not active_markers, active_markers or "all absent")

  # Informational only: Carrot's normal eGPU primary may be active later onroad.
  usb_gpu_active = read_param_bool("UsbGpuActive")
  failed = [c for c in checks if not c["pass"]]
  report = {
    "schemaVersion": 1,
    "stage": "POSTBOOT_ALL_FEATURES_OFF",
    "status": "PASS" if not failed else "HOLD",
    "head": head,
    "branch": branch,
    "checks": checks,
    "failedChecks": [c["name"] for c in failed],
    "informational": {"UsbGpuActive": usb_gpu_active},
    "nextGate": "S1_OBSERVER_ONLY" if not failed else "ROLLBACK_OR_REVIEW",
    "authorizations": {
      "publicRoadAuthorization": False,
      "controlAuthorization": False,
      "shadowAuthorization": False,
    },
  }
  print(json.dumps(report, indent=2, ensure_ascii=False))
  return 0 if not failed else 2


if __name__ == "__main__":
  raise SystemExit(main())
