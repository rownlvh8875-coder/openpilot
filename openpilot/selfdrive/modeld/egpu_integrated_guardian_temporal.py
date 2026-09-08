"""Temporal/context Guardian wrapper for offline replay and shadow evidence review.

This module extends the observe-only Guardian with scene context and temporal
transitions. It never selects a model, publishes a cereal service, or authorizes
vehicle controls. All thresholds in TemporalHeuristicPolicy are research/review
heuristics, not comma safety limits.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
import math
from typing import Any

from openpilot.selfdrive.modeld.egpu_integrated_guardian import (
  ActionSnapshot,
  GuardianPolicy,
  assess_guardian,
  assessment_to_dict,
)


@dataclass(frozen=True)
class SceneContext:
  frame_id: int
  timestamp_mono_s: float
  speed_mps: float
  standstill: bool
  lead_present: bool | None = None
  lead_distance_m: float | None = None
  lead_rel_speed_mps: float | None = None
  lane_change_state: str | None = None
  lead_cutout_time_s: float | None = None
  lead_cutout_confidence: float | None = None


@dataclass(frozen=True)
class TemporalHeuristicPolicy:
  close_lead_m: float = 15.0
  close_acquisition_m: float = 30.0
  closing_fast_mps: float = -5.0
  cutin_candidate_rel_speed_mps: float = -1.0
  rapid_range_closure_mps: float = -5.0
  max_range_rate_dt_s: float = 1.0
  max_scene_gap_frames: int = 2
  max_action_timestamp_skew_s: float | None = None
  standstill_speed_mps: float = 0.3
  creep_speed_mps: float = 3.0

  def validate(self) -> None:
    if any(not math.isfinite(value) for value in asdict(self).values() if value is not None):
      raise ValueError("temporal heuristics must be finite")
    if type(self.max_scene_gap_frames) is not int:
      raise ValueError("max_scene_gap_frames must be an integer")
    if not 0 < self.standstill_speed_mps < self.creep_speed_mps:
      raise ValueError("speed heuristics must satisfy 0 < standstill < creep")
    if self.close_lead_m <= 0 or self.close_acquisition_m <= 0:
      raise ValueError("lead-distance heuristics must be positive")
    if self.close_acquisition_m < self.close_lead_m:
      raise ValueError("close_acquisition_m must be >= close_lead_m")
    if self.closing_fast_mps >= 0 or self.cutin_candidate_rel_speed_mps >= 0 or self.rapid_range_closure_mps >= 0:
      raise ValueError("closing/range-rate heuristics must be negative")
    if self.max_range_rate_dt_s <= 0:
      raise ValueError("max_range_rate_dt_s must be positive")
    if self.max_scene_gap_frames < 0:
      raise ValueError("max_scene_gap_frames must be non-negative")
    if self.max_action_timestamp_skew_s is not None and self.max_action_timestamp_skew_s <= 0:
      raise ValueError("max_action_timestamp_skew_s must be positive when set")


def _cutout_active(scene: SceneContext) -> bool:
  return (
    scene.lead_present is True
    and scene.lead_cutout_time_s is not None
    and scene.lead_cutout_confidence is not None
    and math.isfinite(scene.lead_cutout_time_s)
    and math.isfinite(scene.lead_cutout_confidence)
    and scene.lead_cutout_time_s > 0.0
    and 0.0 < scene.lead_cutout_confidence <= 1.0
  )


def _scene_integrity(scene: SceneContext) -> list[str]:
  issues: list[str] = []
  if type(scene.frame_id) is not int or scene.frame_id < 0:
    issues.append("scene_frame_invalid")
  if not math.isfinite(scene.timestamp_mono_s):
    issues.append("scene_timestamp_nonfinite")
  if not math.isfinite(scene.speed_mps) or scene.speed_mps < 0:
    issues.append("scene_speed_invalid")
  for name, value in (
    ("lead_distance", scene.lead_distance_m),
    ("lead_rel_speed", scene.lead_rel_speed_mps),
    ("lead_cutout_time", scene.lead_cutout_time_s),
    ("lead_cutout_confidence", scene.lead_cutout_confidence),
  ):
    if value is not None and not math.isfinite(value):
      issues.append(f"{name}_nonfinite")
  if scene.lead_cutout_time_s is not None and scene.lead_cutout_time_s < 0:
    issues.append("lead_cutout_time_negative")
  if scene.lead_cutout_confidence is not None and not 0.0 <= scene.lead_cutout_confidence <= 1.0:
    issues.append("lead_cutout_confidence_out_of_range")
  if scene.lead_distance_m is not None and scene.lead_distance_m < 0:
    issues.append("lead_distance_negative")
  if (scene.lead_cutout_time_s is None) != (scene.lead_cutout_confidence is None):
    issues.append("lead_cutout_metadata_incomplete")
  if (
    scene.lead_present is False
    and scene.lead_cutout_time_s is not None and scene.lead_cutout_time_s > 0
    and scene.lead_cutout_confidence is not None and scene.lead_cutout_confidence > 0
  ):
    issues.append("lead_cutout_active_without_lead")
  return list(dict.fromkeys(issues))


def _static_scene_tags(scene: SceneContext, policy: TemporalHeuristicPolicy) -> list[str]:
  tags: list[str] = []
  if scene.standstill or scene.speed_mps < policy.standstill_speed_mps:
    tags.append("standstill")
  elif scene.speed_mps < policy.creep_speed_mps:
    tags.append("creep")

  if scene.lead_present is True:
    tags.append("lead_present")
    if scene.lead_distance_m is not None and scene.lead_distance_m < policy.close_lead_m:
      tags.append("close_lead")
    if scene.lead_rel_speed_mps is not None and scene.lead_rel_speed_mps < policy.closing_fast_mps:
      tags.append("closing_fast")
  elif scene.lead_present is False:
    tags.append("no_lead")

  if _cutout_active(scene):
    tags.append("lead_cutout_predicted")

  if scene.lane_change_state:
    value = str(scene.lane_change_state).lower()
    if value not in {"off", "none", "0"}:
      tags.append("lane_change_context")
  return tags or ["cruise"]


def _review_fingerprint(payload: dict[str, Any]) -> str:
  canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
  return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


class GuardianTemporalTracker:
  """Track scene transitions across every paired frame, not only disagreements."""
  def __init__(self, policy: TemporalHeuristicPolicy | None = None):
    self.policy = policy or TemporalHeuristicPolicy()
    self.policy.validate()
    self.prev_scene: SceneContext | None = None
    self.prev_stop_mismatch: bool | None = None
    self.prev_action_timestamps: dict[str, float] = {}

  def reset(self) -> None:
    self.prev_scene = None
    self.prev_stop_mismatch = None
    self.prev_action_timestamps = {}

  def _temporal_tags(self, scene: SceneContext, *, stop_mismatch: bool) -> tuple[list[str], list[str]]:
    tags: list[str] = []
    integrity: list[str] = []
    prev = self.prev_scene
    prev_stop_mismatch = self.prev_stop_mismatch
    if _scene_integrity(scene):
      self.reset()
      return tags, integrity
    self.prev_scene = scene
    self.prev_stop_mismatch = stop_mismatch
    if prev is None:
      return tags, integrity

    if scene.frame_id <= prev.frame_id:
      integrity.append("scene_frame_not_monotonic")
    elif scene.frame_id > prev.frame_id + 1:
      gap = scene.frame_id - prev.frame_id - 1
      tags.append("scene_frame_gap")
      if gap > self.policy.max_scene_gap_frames:
        integrity.append("scene_frame_gap_over_policy")

    dt = scene.timestamp_mono_s - prev.timestamp_mono_s
    if dt <= 0:
      integrity.append("scene_timestamp_not_monotonic")
    # A gap/regression cannot establish an onset or a resolution between paired
    # frames. Start a fresh baseline while retaining the continuity diagnosis.
    if integrity:
      self.reset()
      return tags, integrity
    if "scene_frame_gap" in tags:
      return tags, integrity

    if prev.lead_present is False and scene.lead_present is True:
      tags.append("lead_acquired")
      if scene.lead_distance_m is not None and scene.lead_distance_m < self.policy.close_acquisition_m:
        tags.append("close_lead_acquisition")
      if (
        scene.lead_distance_m is not None
        and scene.lead_distance_m < self.policy.close_acquisition_m
        and scene.lead_rel_speed_mps is not None
        and scene.lead_rel_speed_mps < self.policy.cutin_candidate_rel_speed_mps
      ):
        tags.append("cut_in_candidate_heuristic")

    if prev.lead_present is True and scene.lead_present is False:
      tags.append("lead_lost")

    if scene.lead_present is True and prev.lead_present is True:
      if (
        prev.lead_distance_m is not None
        and scene.lead_distance_m is not None
        and prev.lead_distance_m >= self.policy.close_lead_m > scene.lead_distance_m
      ):
        tags.append("close_lead_entry")
      if (
        0 < dt <= self.policy.max_range_rate_dt_s
        and prev.lead_distance_m is not None
        and scene.lead_distance_m is not None
      ):
        range_rate = (scene.lead_distance_m - prev.lead_distance_m) / dt
        if range_rate <= self.policy.rapid_range_closure_mps:
          tags.append("rapid_range_closure")
      if (
        prev.lead_rel_speed_mps is not None
        and scene.lead_rel_speed_mps is not None
        and prev.lead_rel_speed_mps >= self.policy.closing_fast_mps > scene.lead_rel_speed_mps
      ):
        tags.append("closing_fast_onset")

    cutout_prev = _cutout_active(prev)
    cutout_now = _cutout_active(scene)
    if all(value is not None for value in (
      prev.lead_cutout_time_s, prev.lead_cutout_confidence, scene.lead_cutout_time_s, scene.lead_cutout_confidence,
    )):
      if not cutout_prev and cutout_now:
        tags.append("lead_cutout_prediction_onset")
      elif cutout_prev and not cutout_now:
        tags.append("lead_cutout_prediction_resolved")

    if not prev.standstill and scene.standstill:
      tags.append("standstill_entry")
    elif prev.standstill and not scene.standstill:
      tags.append("standstill_exit")

    if prev_stop_mismatch is False and stop_mismatch:
      tags.append("stop_disagreement_onset")
    elif prev_stop_mismatch is True and not stop_mismatch:
      tags.append("stop_disagreement_resolved")

    return list(dict.fromkeys(tags)), list(dict.fromkeys(integrity))

  def observe(
    self,
    *,
    active: ActionSnapshot,
    shadow: ActionSnapshot,
    scene: SceneContext,
    hardware: dict[str, Any] | None = None,
    guardian_policy: GuardianPolicy | None = None,
  ) -> dict[str, Any]:
    effective_guardian_policy = guardian_policy or GuardianPolicy()
    if any(not math.isfinite(value) for value in asdict(effective_guardian_policy).values() if value is not None):
      raise ValueError("Guardian heuristics must be finite")
    assessment = assess_guardian(active=active, shadow=shadow, hardware=hardware, policy=effective_guardian_policy)
    base = assessment_to_dict(assessment)
    hard = list(base["hardIssues"])
    review = list(base["reviewIssues"])
    if str(active.backend).lower() == str(shadow.backend).lower() and "same_backend_comparison" not in review:
      review.append("same_backend_comparison")
    action_integrity: list[str] = []
    for role, action in (("active", active), ("shadow", shadow)):
      if str(action.backend).lower() not in {"egpu", "qcom"}:
        action_integrity.append(f"{role}_backend_unknown")
      if type(action.frame_id) is not int or action.frame_id < 0:
        action_integrity.append(f"{role}_frame_invalid")
      if type(action.frame_age) is not int or action.frame_age < 0:
        action_integrity.append(f"{role}_frame_age_invalid")
      if action.model_execution_ms < 0:
        action_integrity.append(f"{role}_execution_invalid")
      timestamp = action.timestamp_mono_s
      previous_timestamp = self.prev_action_timestamps.get(role)
      if timestamp is not None and math.isfinite(timestamp):
        if previous_timestamp is not None and timestamp <= previous_timestamp:
          action_integrity.append(f"{role}_timestamp_not_monotonic")
        self.prev_action_timestamps[role] = timestamp
      else:
        self.prev_action_timestamps.pop(role, None)
    hard.extend(action_integrity)

    if scene.frame_id != active.frame_id or scene.frame_id != shadow.frame_id:
      hard.append("scene_action_frame_mismatch")
    hard.extend(_scene_integrity(scene))

    if self.policy.max_action_timestamp_skew_s is not None:
      if active.timestamp_mono_s is None or shadow.timestamp_mono_s is None:
        review.append("action_timestamp_skew_unavailable")
      elif abs(active.timestamp_mono_s - shadow.timestamp_mono_s) > self.policy.max_action_timestamp_skew_s:
        hard.append("action_timestamp_skew_over_policy")
      for role, action in (("active", active), ("shadow", shadow)):
        if action.timestamp_mono_s is not None and abs(action.timestamp_mono_s - scene.timestamp_mono_s) > self.policy.max_action_timestamp_skew_s:
          hard.append(f"{role}_scene_timestamp_skew_over_policy")

    if hardware is not None:
      hardware_timestamp = hardware.get("timestampMonoS")
      if not isinstance(hardware_timestamp, (int, float)) or isinstance(hardware_timestamp, bool) or not math.isfinite(hardware_timestamp):
        review.append("hardware_timestamp_unavailable")
      elif active.timestamp_mono_s is not None and hardware_timestamp > active.timestamp_mono_s:
        hard.append("hardware_timestamp_in_future")
      if effective_guardian_policy.max_hardware_age_s is not None and (
        "hardware_timestamp_unavailable" in review or active.timestamp_mono_s is None
      ):
        review.append("hardware_age_unavailable")
    if base["disagreement"] is not None and any(
      not math.isfinite(value) for value in base["disagreement"].values()
    ):
      hard.append("disagreement_nonfinite")
      base["disagreement"] = None

    stop_mismatch = bool(active.should_stop) != bool(shadow.should_stop)
    temporal_tags, integrity = self._temporal_tags(scene, stop_mismatch=stop_mismatch)
    if action_integrity or any(issue in hard for issue in ("scene_action_frame_mismatch", "active_nonfinite", "shadow_nonfinite")):
      self.reset()
      temporal_tags = []
    hard.extend(integrity)
    scene_tags = [] if _scene_integrity(scene) else _static_scene_tags(scene, self.policy)

    hard = list(dict.fromkeys(hard))
    review = list(dict.fromkeys(review))
    priority_contexts = {
      "close_lead", "close_lead_acquisition", "rapid_range_closure",
      "cut_in_candidate_heuristic", "lead_cutout_predicted", "lead_cutout_prediction_onset",
    }
    if hard:
      review_bucket = "ROOT_CAUSE"
    elif stop_mismatch and any(t in temporal_tags or t in scene_tags for t in priority_contexts):
      review_bucket = "PRIORITY_REVIEW"
    elif review or temporal_tags:
      review_bucket = "REVIEW"
    else:
      review_bucket = "OBSERVE"

    fingerprint_payload = {
      "hard": sorted(hard),
      "review": sorted(review),
      "scene": sorted(scene_tags),
      "temporal": sorted(temporal_tags),
      "stopMismatch": stop_mismatch,
      "bucket": review_bucket,
      "activeBackend": str(active.backend).lower(),
      "shadowBackend": str(shadow.backend).lower(),
    }

    base.update({
      "stage": "S4A-temporal-observe-only",
      "hardIssues": hard,
      "reviewIssues": review,
      "evidenceEligible": not hard,
      "analysisStatus": "HOLD" if hard else ("REVIEW" if review or temporal_tags else "OBSERVE"),
      "sceneTags": scene_tags,
      "temporalTags": temporal_tags,
      "reviewBucket": review_bucket,
      "reviewFingerprint": _review_fingerprint(fingerprint_payload),
      "carrotCutoutContext": {
        "timeS": scene.lead_cutout_time_s if scene.lead_cutout_time_s is not None and math.isfinite(scene.lead_cutout_time_s) else None,
        "confidence": scene.lead_cutout_confidence if scene.lead_cutout_confidence is not None and math.isfinite(scene.lead_cutout_confidence) else None,
        "active": _cutout_active(scene),
        "valid": not any(issue.startswith("lead_cutout") for issue in hard),
      },
      "temporalPolicy": asdict(self.policy),
      "guardianPolicy": asdict(effective_guardian_policy),
      "temporalPolicyProvenance": "research-review-heuristics-not-official-comma-safety-thresholds",
      "controlAuthorization": False,
      "shadowPublishToControls": False,
      "publicRoadAuthorization": False,
    })
    return base
