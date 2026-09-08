#!/usr/bin/env python3
"""Offline Guardian + fault-state replay analyzer for integrated Carrot-WIP.

Input is source-bound JSONL produced by a future paired-replay exporter. This
analyzer does not open cameras, tinygrad, Params, cereal publishers, or vehicle
interfaces. It produces descriptive review evidence only.
"""
from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import replace
import json
import math
from pathlib import Path
import re
import subprocess
from typing import Any

from openpilot.selfdrive.modeld.egpu_integrated_fault_state import FaultObservation, FaultSequenceTracker
from openpilot.selfdrive.modeld.egpu_integrated_guardian import ActionSnapshot, GuardianPolicy
from openpilot.selfdrive.modeld.egpu_integrated_guardian_temporal import GuardianTemporalTracker, SceneContext, TemporalHeuristicPolicy

EXPECTED_BRANCH = "carrot-wip-integrated-v6"


def git_identity(repo: Path) -> tuple[str | None, str | None]:
  def read(*args: str) -> str | None:
    p = subprocess.run(["git", "-C", str(repo), *args], text=True, capture_output=True, check=False)
    return p.stdout.strip() if p.returncode == 0 and p.stdout.strip() else None
  return read("rev-parse", "HEAD"), read("rev-parse", "--abbrev-ref", "HEAD")


def _optional_float(value: Any) -> float | None:
  if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float))):
    raise ValueError("numeric replay value required")
  return float(value) if value is not None else None


def _integer(value: Any) -> int:
  if type(value) is not int:
    raise ValueError("integer replay value required")
  return value


def _boolean(value: Any) -> bool:
  if type(value) is not bool:
    raise ValueError("boolean replay value required")
  return value


def _optional_bool(value: Any) -> bool | None:
  return _boolean(value) if value is not None else None


def _number(value: Any) -> float:
  number = _optional_float(value)
  if number is None:
    raise ValueError("numeric replay value required")
  return number


def _fields(value: Any, *, required: set[str], optional: set[str], label: str) -> None:
  if not isinstance(value, dict):
    raise ValueError(f"{label}: object required")
  missing = required - value.keys()
  unknown = value.keys() - required - optional
  if missing or unknown:
    raise ValueError(f"{label}: missing fields {sorted(missing)!r}; unknown fields {sorted(str(key) for key in unknown)!r}")


def _action(value: dict[str, Any]) -> ActionSnapshot:
  _fields(value, required={"frameId", "frameAge", "modelExecutionMs", "curvature", "acceleration", "shouldStop", "backend", "timestampMonoS"},
          optional=set(), label="action")
  return ActionSnapshot(
    frame_id=_integer(value["frameId"]),
    frame_age=_integer(value["frameAge"]),
    model_execution_ms=_number(value["modelExecutionMs"]),
    curvature=_number(value["curvature"]),
    acceleration=_number(value["acceleration"]),
    should_stop=_boolean(value["shouldStop"]),
    backend=str(value["backend"]),
    timestamp_mono_s=_optional_float(value.get("timestampMonoS")),
  )


def _scene(value: dict[str, Any]) -> SceneContext:
  _fields(value, required={"frameId", "timestampMonoS", "speedMps", "standstill", "leadPresent", "leadDistanceM", "leadRelSpeedMps"},
          optional={"laneChangeState", "leadCutOutTimeS", "leadCutOutConfidence"}, label="scene")
  return SceneContext(
    frame_id=_integer(value["frameId"]),
    timestamp_mono_s=_number(value["timestampMonoS"]),
    speed_mps=_number(value["speedMps"]),
    standstill=_boolean(value["standstill"]),
    lead_present=_optional_bool(value.get("leadPresent")),
    lead_distance_m=_optional_float(value.get("leadDistanceM")),
    lead_rel_speed_mps=_optional_float(value.get("leadRelSpeedMps")),
    lane_change_state=str(value["laneChangeState"]) if value.get("laneChangeState") is not None else None,
    lead_cutout_time_s=_optional_float(value.get("leadCutOutTimeS")),
    lead_cutout_confidence=_optional_float(value.get("leadCutOutConfidence")),
  )


def _fault(value: dict[str, Any]) -> FaultObservation:
  _fields(value, required={"frameId", "attemptedBackend", "activeBackend", "usbGpuActive", "startupFailed"}, optional={
    "fallbackObserved", "modelOutputPresent", "hardwarePresent", "compiledBig", "telemetryValid", "telemetryFresh", "supplyFault",
    "pcieReady", "usbSpeedMbps", "frameAge", "modelExecutionMs", "outputFrameId", "modelOutputFinite",
  }, label="fault")
  return FaultObservation(
    frame_id=_integer(value["frameId"]),
    attempted_backend=str(value["attemptedBackend"]),
    active_backend=str(value["activeBackend"]),
    usbgpu_active=_boolean(value["usbGpuActive"]),
    startup_failed=_boolean(value["startupFailed"]),
    fallback_observed=_boolean(value.get("fallbackObserved", False)),
    model_output_present=_boolean(value.get("modelOutputPresent", True)),
    hardware_present=_optional_bool(value.get("hardwarePresent")),
    compiled_big=_optional_bool(value.get("compiledBig")),
    telemetry_valid=_optional_bool(value.get("telemetryValid")),
    telemetry_fresh=_optional_bool(value.get("telemetryFresh")),
    supply_fault=_boolean(value.get("supplyFault", False)),
    pcie_ready=_optional_bool(value.get("pcieReady")),
    usb_speed_mbps=_integer(value["usbSpeedMbps"]) if value.get("usbSpeedMbps") is not None else None,
    frame_age=_integer(value["frameAge"]) if value.get("frameAge") is not None else None,
    model_execution_ms=_optional_float(value.get("modelExecutionMs")),
    output_frame_id=_integer(value["outputFrameId"]) if value.get("outputFrameId") is not None else None,
    model_output_finite=_optional_bool(value.get("modelOutputFinite")),
  )


def _combined_bucket(guardian_bucket: str, fault_result: dict[str, Any] | None) -> str:
  if fault_result and fault_result.get("hardIssues"):
    return "ROOT_CAUSE"
  if guardian_bucket == "ROOT_CAUSE":
    return "ROOT_CAUSE"
  if guardian_bucket == "PRIORITY_REVIEW":
    return "PRIORITY_REVIEW"
  if guardian_bucket == "REVIEW" or (fault_result and (fault_result.get("reviewIssues") or fault_result.get("temporalTags"))):
    return "REVIEW"
  return "OBSERVE"


def analyze_rows(
  rows: list[dict[str, Any]],
  *,
  expected_source_head: str,
  expected_source_branch: str = EXPECTED_BRANCH,
  guardian_policy: GuardianPolicy | None = None,
  temporal_policy: TemporalHeuristicPolicy | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
  if (
    not isinstance(expected_source_head, str) or not re.fullmatch(r"[0-9a-f]{40}", expected_source_head)
    or not isinstance(expected_source_branch, str) or not expected_source_branch.strip()
  ):
    raise ValueError("full source SHA and nonempty branch required")
  temporal = GuardianTemporalTracker(temporal_policy)
  faults = FaultSequenceTracker()
  output: list[dict[str, Any]] = []
  bucket_counts: Counter[str] = Counter()
  hard_counts: Counter[str] = Counter()
  review_counts: Counter[str] = Counter()
  scene_counts: Counter[str] = Counter()
  temporal_counts: Counter[str] = Counter()
  fingerprint_counts: Counter[str] = Counter()
  fault_state_counts: Counter[str] = Counter()
  process_ids: set[str] = set()
  previous_process_id: str | None = None
  process_identity_required = any(isinstance(row, dict) and "processId" in row for row in rows)

  for index, row in enumerate(rows):
    _fields(row, required={"sourceHead", "sourceBranch", "active", "shadow", "scene"},
            optional={"hardware", "fault", "processId", "restartBoundary"}, label=f"row {index}")
    source_head = row.get("sourceHead")
    source_branch = row.get("sourceBranch")
    if source_head != expected_source_head or source_branch != expected_source_branch:
      raise ValueError(f"row {index}: source identity mismatch")

    process_id = row.get("processId")
    restart = _boolean(row.get("restartBoundary", False))
    if process_identity_required and (not isinstance(process_id, str) or not process_id.strip()):
      raise ValueError(f"row {index}: process identity required on every row")
    changed_process = index > 0 and process_id != previous_process_id
    if restart and (not changed_process or process_id is None):
      raise ValueError(f"row {index}: restart requires a new explicit process identity")
    if changed_process:
      if not restart or process_id in process_ids:
        raise ValueError(f"row {index}: process change requires a new restart boundary")
      temporal.reset()
      faults.reset()
    if process_id is not None:
      process_ids.add(process_id)
    previous_process_id = process_id

    active = _action(row["active"])
    shadow = _action(row["shadow"])
    scene = _scene(row["scene"])
    hardware = row.get("hardware")
    if hardware is not None and not isinstance(hardware, dict):
      raise ValueError(f"row {index}: hardware must be an object or null")
    if hardware is not None:
      for key in ("valid", "supplyFault"):
        if key in hardware:
          _boolean(hardware[key])
    guardian = temporal.observe(active=active, shadow=shadow, scene=scene, hardware=hardware, guardian_policy=guardian_policy)

    fault_result = None
    if row.get("fault") is not None:
      if not isinstance(row["fault"], dict):
        raise ValueError(f"row {index}: fault must be an object or null")
      fault_obs = _fault(row["fault"])
      coherence_issues: list[str] = []
      if fault_obs.frame_id != active.frame_id:
        coherence_issues.append("fault_guardian_frame_mismatch")
      if str(fault_obs.active_backend).lower() != str(active.backend).lower():
        coherence_issues.append("fault_guardian_active_backend_mismatch")
      if fault_obs.frame_age is not None and fault_obs.frame_age != active.frame_age:
        coherence_issues.append("fault_guardian_frame_age_mismatch")
      if fault_obs.output_frame_id is not None and fault_obs.output_frame_id != active.frame_id:
        coherence_issues.append("fault_guardian_output_frame_mismatch")
      if fault_obs.model_output_finite is True and not all(math.isfinite(value) for value in (
        active.curvature, active.acceleration, active.model_execution_ms,
      )):
        coherence_issues.append("fault_guardian_output_finiteness_mismatch")
      if not coherence_issues and fault_obs.frame_age is None:
        # The paired active snapshot supplies output age when the fault record
        # omitted it; a stale active output cannot prove a same-frame rerun.
        fault_obs = replace(fault_obs, frame_age=active.frame_age)
      # Mismatched evidence must not mutate the process fault history.
      fault_result = (FaultSequenceTracker() if coherence_issues else faults).observe(fault_obs)
      if coherence_issues:
        fault_result["hardIssues"] = list(dict.fromkeys(list(fault_result.get("hardIssues", [])) + coherence_issues))
        fault_result["evidenceCoherent"] = False
        if fault_result["sameFrameOutputPreserved"] is True:
          fault_result["sameFrameOutputPreserved"] = None
      else:
        fault_result["evidenceCoherent"] = True
      fault_state_counts.update([str(fault_result["state"])])

    bucket = _combined_bucket(str(guardian["reviewBucket"]), fault_result)
    event = {
      "schemaVersion": 1,
      "stage": "OFFLINE_GUARDIAN_REPLAY",
      "sourceHead": source_head,
      "sourceBranch": source_branch,
      "frameId": int(active.frame_id),
      "processId": process_id,
      "restartBoundary": restart,
      "guardian": guardian,
      "fault": fault_result,
      "combinedReviewBucket": bucket,
      "controlAuthorization": False,
      "publicRoadAuthorization": False,
      "shadowPublishToControls": False,
    }
    output.append(event)

    bucket_counts.update([bucket])
    hard_counts.update(str(x) for x in guardian.get("hardIssues", []))
    review_counts.update(str(x) for x in guardian.get("reviewIssues", []))
    scene_counts.update(str(x) for x in guardian.get("sceneTags", []))
    temporal_counts.update(str(x) for x in guardian.get("temporalTags", []))
    fp = guardian.get("reviewFingerprint")
    if fp:
      fingerprint_counts.update([str(fp)])
    if fault_result:
      hard_counts.update("fault:" + str(x) for x in fault_result.get("hardIssues", []))
      review_counts.update("fault:" + str(x) for x in fault_result.get("reviewIssues", []))
      temporal_counts.update("fault:" + str(x) for x in fault_result.get("temporalTags", []))

  summary = {
    "schemaVersion": 1,
    "stage": "OFFLINE_GUARDIAN_REPLAY_SUMMARY",
    "sourceHead": expected_source_head,
    "sourceBranch": expected_source_branch,
    "rows": len(output),
    "reviewBuckets": dict(bucket_counts.most_common()),
    "hardIssueCounts": dict(hard_counts.most_common()),
    "reviewIssueCounts": dict(review_counts.most_common()),
    "sceneTagCounts": dict(scene_counts.most_common()),
    "temporalTagCounts": dict(temporal_counts.most_common()),
    "faultStateCounts": dict(fault_state_counts.most_common()),
    "reviewFingerprints": dict(fingerprint_counts.most_common()),
    "notes": [
      "Descriptive offline review evidence only; not a safety PASS/FAIL decision.",
      "cut_in_candidate_heuristic requires independent video/radar/lane review.",
      "Carrot cut-out context is descriptive and never authorizes control changes.",
      "review buckets prioritize human/root-cause review and never authorize controls.",
      "ROOT_CAUSE requests investigation of evidence faults; it does not identify a physical cause.",
      "Process boundaries are exporter assertions and are not hardware-verified restarts.",
      "sameFrameOutputPreserved describes output proof only; hardIssues separately assess latch and sequence consistency.",
    ],
    "controlAuthorization": False,
    "publicRoadAuthorization": False,
  }
  return output, summary


def main() -> int:
  ap = argparse.ArgumentParser()
  ap.add_argument("--input", type=Path, required=True)
  ap.add_argument("--expected-source-head", required=True)
  ap.add_argument("--expected-source-branch", default=EXPECTED_BRANCH)
  ap.add_argument("--output-events", type=Path, required=True)
  ap.add_argument("--output-summary", type=Path, required=True)
  args = ap.parse_args()

  rows = []
  with args.input.open("r", encoding="utf-8") as f:
    for line_no, line in enumerate(f, 1):
      if not line.strip():
        continue
      value = json.loads(line)
      if not isinstance(value, dict):
        raise SystemExit(f"line {line_no}: JSON object required")
      rows.append(value)

  events, summary = analyze_rows(
    rows,
    expected_source_head=args.expected_source_head,
    expected_source_branch=args.expected_source_branch,
  )

  repo = Path(__file__).resolve().parents[1]
  analyzer_head, analyzer_branch = git_identity(repo)
  summary["analyzerHead"] = analyzer_head
  summary["analyzerBranch"] = analyzer_branch

  args.output_events.parent.mkdir(parents=True, exist_ok=True)
  with args.output_events.open("w", encoding="utf-8") as out:
    for event in events:
      out.write(json.dumps(event, ensure_ascii=False, separators=(",", ":"), allow_nan=False) + "\n")
  args.output_summary.parent.mkdir(parents=True, exist_ok=True)
  args.output_summary.write_text(json.dumps(summary, indent=2, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8")
  print(json.dumps(summary, indent=2, ensure_ascii=False, allow_nan=False))
  return 0


if __name__ == "__main__":
  raise SystemExit(main())
