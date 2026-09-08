"""Temporal/context Guardian wrapper for offline replay and shadow evidence review.

This module extends the observe-only Guardian with scene context and temporal
transitions. It never selects a model, publishes a cereal service, or authorizes
vehicle controls. All thresholds in TemporalHeuristicPolicy are research/review
heuristics, not comma safety limits.
"""
from __future__ import annotations

from dataclasses import dataclass
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

  def validate(self) -> None:
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
    scene.lead_cutout_time_s is not None
    and scene.lead_cutout_confidence is not None
    and math.isfinite(scene.lead_cutout_time_s)
    and math.isfinite(scene.lead_cutout_confidence)
    and scene.lead_cutout_time_s > 0.0
    and scene.lead_cutout_confidence > 0.0
  )


def _scene_integrity(scene: SceneContext) -> list[str]:
  issues: list[str] = []
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
  if scene.lead_present is False and _cutout_active(scene):
    issues.append("lead_cutout_active_without_lead")
  return list(dict.fromkeys(issues))


def _static_scene_tags(scene: SceneContext, policy: TemporalHeuristicPolicy) -> list[str]:
  tags: list[str] = []
  if scene.standstill or scene.speed_mps < 0.3:
    tags.append("standstill")
  elif scene.speed_mps < 3.0:
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

  def reset(self) -> None:
    self.prev_scene = None
    self.prev_stop_mismatch = None

  def _temporal_tags(self, scene: SceneContext, *, stop_mismatch: bool) -> tuple[list[str], list[str]]:
    tags: list[str] = []
    integrity: list[str] = []
    prev = self.prev_scene
    prev_stop_mismatch = self.prev_stop_mismatch
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
    assessment = assess_guardian(active=active, shadow=shadow, hardware=hardware, policy=guardian_policy)
    base = assessment_to_dict(assessment)
    hard = list(base["hardIssues"])
    review = list(base["reviewIssues"])

    if scene.frame_id != active.frame_id or scene.frame_id != shadow.frame_id:
      hard.append("scene_action_frame_mismatch")
    hard.extend(_scene_integrity(scene))

    if self.policy.max_action_timestamp_skew_s is not None:
      if active.timestamp_mono_s is None or shadow.timestamp_mono_s is None:
        review.append("action_timestamp_skew_unavailable")
      elif abs(active.timestamp_mono_s - shadow.timestamp_mono_s) > self.policy.max_action_timestamp_skew_s:
        hard.append("action_timestamp_skew_over_policy")

    stop_mismatch = bool(active.should_stop) != bool(shadow.should_stop)
    temporal_tags, integrity = self._temporal_tags(scene, stop_mismatch=stop_mismatch)
    hard.extend(integrity)
    scene_tags = _static_scene_tags(scene, self.policy)

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
        "timeS": scene.lead_cutout_time_s,
        "confidence": scene.lead_cutout_confidence,
        "active": _cutout_active(scene),
      },
      "temporalPolicyProvenance": "research-review-heuristics-not-official-comma-safety-thresholds",
      "controlAuthorization": False,
      "shadowPublishToControls": False,
      "publicRoadAuthorization": False,
    })
    return base
