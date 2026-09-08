#!/usr/bin/env python3
"""Manual parked-only <=5 Hz QCOM shadow load probe for integrated Carrot-WIP.

This is NOT a model-quality comparison runner. Sampling a temporal driving model
at 5 Hz changes hidden-state history, so outputs are not eligible for active-vs-
shadow behavioral conclusions. The purpose is only to measure whether a second
QCOM inference workload perturbs the active eGPU driving path.

The script publishes no cereal services, no modelV2, and no vehicle commands.
Never add it to manager process configuration in Stage 4B.

S4B entry is additionally locked by a source-bound S4B_REVIEW_READINESS PASS
artifact. The readiness gate is checked before camera/QCOM model initialization.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time
from typing import Any

MAX_S4B_HZ = 5.0
EXPECTED_BRANCH = "carrot-wip-integrated-v6"


def write_event(out, event: dict, *, flush: bool = False) -> None:
  event.setdefault("schemaVersion", 1)
  event.setdefault("stage", "S4B-load-probe")
  event.setdefault("shadowOnly", True)
  event.setdefault("controlEligible", False)
  event.setdefault("qualityComparisonEligible", False)
  out.write(json.dumps(event, ensure_ascii=False, separators=(",", ":"), allow_nan=False) + "\n")
  if flush:
    out.flush()


def resource_isolation(nice_inc: int, cpu_affinity: str | None) -> dict:
  result = {"niceIncrement": int(nice_inc), "niceApplied": False, "cpuAffinity": None}
  try:
    os.nice(max(0, int(nice_inc))); result["niceApplied"] = True
  except OSError:
    pass
  if cpu_affinity and hasattr(os, "sched_setaffinity"):
    try:
      cpus = {int(x) for x in cpu_affinity.split(",") if x.strip()}
      if cpus:
        os.sched_setaffinity(0, cpus); result["cpuAffinity"] = sorted(cpus)
    except (OSError, ValueError):
      pass
  return result


def sha256_file(path: Path) -> str:
  h = hashlib.sha256()
  with path.open("rb") as f:
    for chunk in iter(lambda: f.read(1024 * 1024), b""):
      h.update(chunk)
  return h.hexdigest()


def git_identity(repo: Path) -> tuple[str | None, str | None]:
  def read(*args: str) -> str | None:
    p = subprocess.run(["git", "-C", str(repo), *args], text=True, capture_output=True, check=False)
    return p.stdout.strip() if p.returncode == 0 and p.stdout.strip() else None
  return read("rev-parse", "HEAD"), read("rev-parse", "--abbrev-ref", "HEAD")


def validate_readiness(readiness: dict[str, Any], *, expected_head: str,
                       expected_branch: str = EXPECTED_BRANCH) -> None:
  if readiness.get("stage") != "S4B_REVIEW_READINESS":
    raise ValueError("S4B readiness stage mismatch")
  if readiness.get("status") != "PASS":
    raise ValueError("S4B readiness must be PASS")
  if readiness.get("sourceHead") != expected_head or readiness.get("sourceBranch") != expected_branch:
    raise ValueError("S4B readiness source identity mismatch")
  if readiness.get("reasons") not in ([], None):
    raise ValueError("S4B readiness contains HOLD reasons")
  if readiness.get("nextGate") != "S4B_PARKED_SHADOW_LOAD_PROBE_PLAN_ONLY":
    raise ValueError("S4B readiness nextGate mismatch")
  auth = readiness.get("authorizations", {})
  for name in (
    "shadowExecutionAuthorization", "observerEnableAuthorization", "telemetryEnableAuthorization",
    "rebootAuthorization", "publicRoadAuthorization", "controlAuthorization",
  ):
    if auth.get(name) is not False:
      raise ValueError(f"S4B readiness authorization boundary violated: {name}")


def validate_entry(readiness_path: Path, *, expected_head: str,
                   expected_branch: str = EXPECTED_BRANCH,
                   repo: Path | None = None) -> tuple[dict[str, Any], str]:
  readiness = json.loads(readiness_path.read_text(encoding="utf-8"))
  validate_readiness(readiness, expected_head=expected_head, expected_branch=expected_branch)
  repo = repo or Path(__file__).resolve().parents[1]
  actual_head, actual_branch = git_identity(repo)
  if actual_head != expected_head or actual_branch != expected_branch:
    raise ValueError(
      f"running source identity mismatch: head={actual_head} branch={actual_branch} "
      f"expectedHead={expected_head} expectedBranch={expected_branch}"
    )
  return readiness, sha256_file(readiness_path)


def main() -> int:
  ap = argparse.ArgumentParser()
  ap.add_argument("--readiness", type=Path, required=True)
  ap.add_argument("--expected-head", required=True)
  ap.add_argument("--expected-branch", default=EXPECTED_BRANCH)
  ap.add_argument("--tap-socket", default="/tmp/egpu_integrated_shadow_input.sock")
  ap.add_argument("--output", default="/tmp/egpu_integrated_s4b_shadow.jsonl")
  ap.add_argument("--max-hz", type=float, default=5.0)
  ap.add_argument("--duration", type=float, default=60.0)
  ap.add_argument("--nice", type=int, default=10)
  ap.add_argument("--cpu-affinity", default=None)
  ap.add_argument("--profile", action=argparse.BooleanOptionalAction, default=True)
  args = ap.parse_args()
  if not 0 < args.max_hz <= MAX_S4B_HZ:
    raise SystemExit("S4B --max-hz must be >0 and <=5")
  if not 0 < args.duration <= 300:
    raise SystemExit("S4B --duration must be >0 and <=300 seconds")

  # Critical entry gate: no camera or QCOM initialization before this succeeds.
  try:
    _, readiness_sha256 = validate_entry(
      args.readiness.resolve(), expected_head=args.expected_head, expected_branch=args.expected_branch,
    )
  except (ValueError, OSError, json.JSONDecodeError) as exc:
    print(json.dumps({
      "status": "HOLD",
      "stage": "S4B_ENTRY_GATE",
      "reason": str(exc),
      "shadowExecutionAuthorization": False,
      "controlAuthorization": False,
      "publicRoadAuthorization": False,
    }, indent=2))
    return 4

  if args.profile:
    os.environ.setdefault("PROFILE", "1")

  import numpy as np
  try:
    from openpilot.cereal.visionipc import VisionStreamType
  except ImportError:
    from msgq.visionipc import VisionStreamType
  from msgq.visionipc import VisionIpcClient
  from openpilot.common.params import Params
  from openpilot.selfdrive.modeld.modeld import FrameMeta, ModelState
  from openpilot.selfdrive.modeld.egpu_integrated_shadow_tap import IntegratedShadowReceiver

  isolation = resource_isolation(args.nice, args.cpu_affinity)
  params = Params()
  road = VisionStreamType.VISION_STREAM_ROAD
  wide = VisionStreamType.VISION_STREAM_WIDE_ROAD

  while True:
    available = VisionIpcClient.available_streams("camerad", block=False)
    use_wide_camera = bool(params.get("UseWideCamera", return_default=True))
    if road in available:
      main_stream, use_extra = road, use_wide_camera and wide in available
      break
    if use_wide_camera and wide in available:
      main_stream, use_extra = wide, False
      break
    time.sleep(0.1)

  def connect(stream):
    client = VisionIpcClient("camerad", stream, True)
    while not client.connect(False): time.sleep(0.1)
    return client

  def recv_exact(client, frame_id: int, max_reads: int = 8):
    last = None
    for _ in range(max_reads):
      buf = client.recv()
      if buf is None: return None, last, "no_frame"
      meta = FrameMeta(client); last = meta
      if meta.frame_id == frame_id: return buf, meta, None
      if meta.frame_id > frame_id: return None, meta, "camera_advanced"
    return None, last, "target_not_reached"

  main_client = connect(main_stream); extra_client = connect(wide) if use_extra else None
  out_path = Path(args.output); out_path.parent.mkdir(parents=True, exist_ok=True)
  min_period_ns = int(1e9 / args.max_hz)
  last_run_ns = None
  start_deadline = time.monotonic() + args.duration
  model = None
  runs = skips = guard_stops = 0

  with out_path.open("a", encoding="utf-8", buffering=1) as out:
    receiver = IntegratedShadowReceiver(args.tap_socket)
    try:
      write_event(out, {
        "type": "startup", "maxHz": args.max_hz, "durationS": args.duration,
        "PROFILE": os.getenv("PROFILE"), "resourceIsolation": isolation,
        "manualStartOnly": True, "managerAutostart": False,
        "purpose": "active-path-interference-only",
        "sourceHead": args.expected_head, "sourceBranch": args.expected_branch,
        "s4bReadinessSha256": readiness_sha256,
      }, flush=True)

      while time.monotonic() < start_deadline:
        snap = receiver.recv_latest()
        if snap is None:
          time.sleep(0.001); continue
        if not snap.stationary_guard_ok:
          guard_stops += 1
          write_event(out, {"type": "guard_stop", "frameId": snap.frame_id}, flush=True)
          return 3
        if snap.active_backend != "egpu":
          skips += 1
          write_event(out, {"type": "skip", "frameId": snap.frame_id, "reason": "active_not_egpu"})
          continue
        now_ns = time.monotonic_ns()
        if last_run_ns is not None and now_ns - last_run_ns < min_period_ns:
          skips += 1; continue
        last_run_ns = now_ns

        if model is None:
          load_start = time.monotonic_ns()
          model = ModelState(main_client.width, main_client.height, False)
          write_event(out, {"type": "model_loaded", "backend": "qcom", "loadMs": (time.monotonic_ns()-load_start)/1e6}, flush=True)
          continue

        buf_main, meta_main, miss = recv_exact(main_client, snap.frame_id)
        if miss or buf_main is None:
          skips += 1; write_event(out, {"type": "skip", "frameId": snap.frame_id, "reason": f"main_{miss}"}); continue
        if use_extra:
          assert extra_client is not None
          buf_extra, meta_extra, miss2 = recv_exact(extra_client, snap.frame_id_extra)
          if miss2 or buf_extra is None:
            skips += 1; write_event(out, {"type": "skip", "frameId": snap.frame_id, "reason": f"extra_{miss2}"}); continue
        else:
          buf_extra, meta_extra = buf_main, meta_main

        main_tfm = np.asarray(snap.main_transform, dtype=np.float32).reshape(3, 3)
        extra_tfm = np.asarray(snap.extra_transform, dtype=np.float32).reshape(3, 3)
        bufs = {name: buf_extra if "big" in name else buf_main for name in model.vision_input_names}
        transforms = {name: extra_tfm if "big" in name else main_tfm for name in model.vision_input_names}
        inputs = {
          "desire_pulse": np.asarray(snap.desire_pulse, dtype=np.float32).copy(),
          "traffic_convention": np.asarray(snap.traffic_convention, dtype=np.float32).copy(),
          "action_t": np.asarray(snap.action_t, dtype=np.float32).copy(),
        }

        call_start = time.monotonic_ns()
        try:
          output = model.run(bufs, transforms, inputs, False)
          if output is None: raise RuntimeError("QCOM shadow returned no output")
        except Exception as exc:
          done = time.monotonic_ns()
          write_event(out, {"type": "shadow_error", "frameId": snap.frame_id, "error": type(exc).__name__, "modelCallMs": (done-call_start)/1e6}, flush=True)
          return 2
        done = time.monotonic_ns(); runs += 1
        write_event(out, {
          "type": "load_probe", "frameId": snap.frame_id, "stateFrameId": snap.state_frame_id,
          "frameAge": max(0, snap.state_frame_id-snap.frame_id), "activeBackend": "egpu", "shadowBackend": "qcom",
          "modelCallMs": (done-call_start)/1e6, "cameraEofToDoneMs": (done-snap.camera_eof_ns)/1e6,
          "runs": runs, "skips": skips,
        }, flush=(runs % 10 == 0))

      write_event(out, {"type": "complete", "runs": runs, "skips": skips, "guardStops": guard_stops,
                        "receiver": {"received": receiver.received, "superseded": receiver.superseded,
                                     "decodeErrors": receiver.decode_errors, "guardRejected": receiver.guard_rejected}}, flush=True)
    finally:
      receiver.close()

  return 0


if __name__ == "__main__":
  try: raise SystemExit(main())
  except KeyboardInterrupt: raise SystemExit(130)
