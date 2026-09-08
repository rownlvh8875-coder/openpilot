#!/usr/bin/env python3
"""Offline Guardian + fault-state replay analyzer for integrated Carrot-WIP.

Input is source-bound JSONL produced by a future paired-replay exporter. This
analyzer does not open cameras, tinygrad, Params, cereal publishers, or vehicle
interfaces. It produces descriptive review evidence only.
"""
from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
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
  return float(value) if value is not None else None


def _action(value: dict[str, Any]) -> ActionSnapshot:
  return ActionSnapshot(
    frame_id=int(value["frameId"]),
    frame_age=int(value.get("frameAge", 0)),
    model_execution_ms=float(value["modelExecutionMs"]),
    curvature=float(value["curvature"]),
    acceleration=float(value["acceleration"]),
    should_stop=bool(value["shouldStop"]),
    backend=str(value["backend"]),
    timestamp_mono_s=_optional_float(value.get("timestampMonoS")),
  )


def _scene(value: dict[str, Any]) -> SceneContext:
  return SceneContext(
    frame_id=int(value["frameId"]),
    timestamp_mono_s=float(value["timestampMonoS"]),
    speed_mps=float(value["speedMps"]),
    standstill=bool(value["standstill"]),
    lead_present=value.get("leadPresent") if isinstance(value.get("leadPresent"), bool) else None,
    lead_distance_m=_optional_float(value.get("leadDistanceM")),
    lead_rel_speed_mps=_optional_float(value.get("leadRelSpeedMps")),
    lane_change_state=str(value["laneChangeState"]) if value.get("laneChangeState") is not None else None,
    lead_cutout_time_s=_optional_float(value.get("leadCutOutTimeS")),
    lead_cutout_confidence=_optional_float(value.get("leadCutOutConfidence")),
  )


def _fault(value: dict[str, Any]) -> FaultObservation:
  return FaultObservation(
    frame_id=int(value["frameId"]),
    attempted_backend=str(value["attemptedBackend"]),
    active_backend=str(value["activeBackend"]),
    usbgpu_active=bool(value["usbGpuActive"]),
    startup_failed=bool(value["startupFailed"]),
    fallback_observed=bool(value.get("fallbackObserved", False)),
    model_output_present=bool(value.get("modelOutputPresent", True)),
    hardware_present=value.get("hardwarePresent") if isinstance(value.get("hardwarePresent"), bool) else None,
    compiled_big=value.get("compiledBig") if isinstance(value.get("compiledBig"), bool) else None,
    telemetry_valid=value.get("telemetryValid") if isinstance(value.get("telemetryValid"), bool) else None,
    telemetry_fresh=value.get("telemetryFresh") if isinstance(value.get("telemetryFresh"), bool) else None,
    supply_fault=bool(value.get("supplyFault", False)),
    pcie_ready=value.get("pcieReady") if isinstance(value.get("pcieReady"), bool) else None,
    usb_speed_mbps=int(value["usbSpeedMbps"]) if value.get("usbSpeedMbps") is not None else None,
    frame_age=int(value["frameAge"]) if value.get("frameAge") is not None else None,
    model_execution_ms=_optional_float(value.get("modelExecutionMs")),
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

  for index, row in enumerate(rows):
    source_head = str(row.get("sourceHead") or "")
    source_branch = str(row.get("sourceBranch") or "")
    if source_head != expected_source_head or source_branch != expected_source_branch:
      raise ValueError(f"row {index}: source identity mismatch")

    active = _action(row["active"])
    shadow = _action(row["shadow"])
    scene = _scene(row["scene"])
    hardware = row.get("hardware") if isinstance(row.get("hardware"), dict) else None
    guardian = temporal.observe(active=active, shadow=shadow, scene=scene, hardware=hardware, guardian_policy=guardian_policy)

    fault_result = None
    if isinstance(row.get("fault"), dict):
      fault_obs = _fault(row["fault"])
      fault_result = faults.observe(fault_obs)
      coherence_issues: list[str] = []
      if fault_obs.frame_id != active.frame_id:
        coherence_issues.append("fault_guardian_frame_mismatch")
      if str(fault_obs.active_backend).lower() != str(active.backend).lower():
        coherence_issues.append("fault_guardian_active_backend_mismatch")
      if coherence_issues:
        fault_result["hardIssues"] = list(dict.fromkeys(list(fault_result.get("hardIssues", [])) + coherence_issues))
        fault_result["evidenceCoherent"] = False
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
