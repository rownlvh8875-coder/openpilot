"""Observe-only fault state model for the integrated Carrot-WIP eGPU path.

This module mirrors the *current* Carrot runtime semantics instead of inventing
a recovery loop that modeld does not implement. In the reviewed source, a
runtime eGPU exception switches to the already-loaded QCOM model for the same
camera frame, sets UsbGpuStartupFailed, and remains on the small model for the
rest of the ignition cycle. Re-entry to the big model therefore requires a
clean process/device restart in the current implementation.

The state model is pure Python, emits no Params writes or control commands, and
exists only for replay, fault-injection, and commissioning evidence review.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import math
from typing import Any

USB_SUPERSPEED_MBPS = 5000


class FaultState(str, Enum):
  STARTUP = "STARTUP"
  BIG_ACTIVE = "BIG_ACTIVE"
  SAME_FRAME_FALLBACK = "SAME_FRAME_FALLBACK"
  SMALL_LATCHED = "SMALL_LATCHED"
  STARTUP_SMALL = "STARTUP_SMALL"
  INCONSISTENT = "INCONSISTENT"


@dataclass(frozen=True)
class FaultObservation:
  frame_id: int
  attempted_backend: str
  active_backend: str
  usbgpu_active: bool
  startup_failed: bool
  fallback_observed: bool = False
  model_output_present: bool = True
  hardware_present: bool | None = None
  compiled_big: bool | None = None
  telemetry_valid: bool | None = None
  telemetry_fresh: bool | None = None
  supply_fault: bool = False
  pcie_ready: bool | None = None
  usb_speed_mbps: int | None = None
  frame_age: int | None = None
  model_execution_ms: float | None = None
  output_frame_id: int | None = None
  model_output_finite: bool | None = None


@dataclass(frozen=True)
class FaultAssessment:
  state: FaultState
  reason: str
  hard_issues: tuple[str, ...]
  review_issues: tuple[str, ...]
  same_frame_output_preserved: bool | None
  expected_runtime_behavior: str
  retry_expected_this_ignition: bool
  control_authorization: bool = False
  public_road_authorization: bool = False


def _dedupe(items: list[str]) -> tuple[str, ...]:
  return tuple(dict.fromkeys(items))


def assess_fault_observation(obs: FaultObservation) -> FaultAssessment:
  """Classify one observation against reviewed Carrot-WIP fallback semantics."""
  hard: list[str] = []
  review: list[str] = []
  attempted = str(obs.attempted_backend).lower()
  active = str(obs.active_backend).lower()

  if attempted not in {"egpu", "qcom"}:
    hard.append("unknown_attempted_backend")
  if active not in {"egpu", "qcom"}:
    hard.append("unknown_active_backend")
  if obs.frame_id < 0:
    hard.append("invalid_frame_id")
  if obs.frame_age is not None and obs.frame_age < 0:
    hard.append("invalid_frame_age")
  if obs.model_execution_ms is not None and (not math.isfinite(obs.model_execution_ms) or obs.model_execution_ms < 0):
    hard.append("invalid_model_execution_ms")
  if obs.model_output_finite is False:
    hard.append("model_output_nonfinite")
  if not obs.model_output_present and not (attempted == "egpu" and active == "qcom"):
    hard.append("model_output_missing")
  if obs.output_frame_id is not None and obs.output_frame_id < 0:
    hard.append("invalid_output_frame_id")
  if obs.supply_fault:
    review.append("supply_fault_observed")
  if obs.telemetry_valid is False:
    review.append("telemetry_invalid")
  if obs.telemetry_fresh is False:
    review.append("telemetry_stale")
  if obs.pcie_ready is False:
    review.append("pcie_not_ready")
  if obs.usb_speed_mbps is not None and obs.usb_speed_mbps < USB_SUPERSPEED_MBPS:
    review.append("usb_below_superspeed_5g")

  if active == "egpu":
    if attempted != "egpu":
      hard.append("egpu_output_without_egpu_attempt")
    if obs.hardware_present is False:
      review.append("hardware_absent_while_big_returns")
    if not obs.usbgpu_active:
      hard.append("egpu_backend_with_usbgpu_inactive")
    if obs.startup_failed:
      hard.append("egpu_backend_with_startup_failed_latched")
    if obs.fallback_observed:
      hard.append("fallback_flag_while_egpu_active")
    state = FaultState.BIG_ACTIVE if not hard else FaultState.INCONSISTENT
    reason = "big_model_active" if state == FaultState.BIG_ACTIVE else "inconsistent_big_state"
    return FaultAssessment(
      state=state,
      reason=reason,
      hard_issues=_dedupe(hard),
      review_issues=_dedupe(review),
      same_frame_output_preserved=None,
      expected_runtime_behavior="continue_big_until_runtime_failure_or_ignition_end",
      retry_expected_this_ignition=False,
    )

  if attempted == "egpu" and active == "qcom":
    if not obs.fallback_observed:
      hard.append("backend_changed_without_fallback_evidence")
    if obs.usbgpu_active:
      hard.append("fallback_but_usbgpu_still_active")
    if not obs.startup_failed:
      hard.append("fallback_without_startup_failed_latch")
    if not obs.model_output_present:
      hard.append("same_frame_output_missing")
    if obs.output_frame_id is not None and obs.output_frame_id != obs.frame_id:
      hard.append("same_frame_output_frame_mismatch")
    if obs.frame_age is not None and obs.frame_age > 0:
      hard.append("same_frame_output_stale")
    if obs.output_frame_id is None:
      review.append("same_frame_output_identity_unavailable")
    if obs.model_output_finite is None:
      review.append("same_frame_output_finiteness_unavailable")
    output_invalid = (
      obs.frame_id < 0 or not obs.model_output_present or obs.model_output_finite is False
      or (obs.output_frame_id is not None and obs.output_frame_id != obs.frame_id)
      or (obs.frame_age is not None and obs.frame_age != 0)
    )
    output_verified = obs.output_frame_id == obs.frame_id and obs.model_output_finite is True
    state = FaultState.SAME_FRAME_FALLBACK if not hard else FaultState.INCONSISTENT
    reason = "runtime_big_failure_same_frame_small_rerun" if state == FaultState.SAME_FRAME_FALLBACK else "inconsistent_fallback_state"
    return FaultAssessment(
      state=state,
      reason=reason,
      hard_issues=_dedupe(hard),
      review_issues=_dedupe(review),
      same_frame_output_preserved=False if output_invalid else (True if output_verified else None),
      expected_runtime_behavior="remain_on_qcom_until_clean_restart",
      retry_expected_this_ignition=False,
    )

  # A single QCOM + startup_failed observation cannot prove whether the latch
  # originated during startup or from a prior runtime fallback. Sequence context
  # adds `small_latch_confirmed` only when SAME_FRAME_FALLBACK was observed just
  # before it, so do not over-attribute the cause here.
  if attempted == "qcom" and active == "qcom" and obs.startup_failed:
    if obs.usbgpu_active:
      hard.append("small_latched_but_usbgpu_active")
    if obs.fallback_observed:
      hard.append("fallback_flag_without_egpu_attempt")
    state = FaultState.SMALL_LATCHED if not hard else FaultState.INCONSISTENT
    return FaultAssessment(
      state=state,
      reason="startup_failed_latched_small" if state == FaultState.SMALL_LATCHED else "inconsistent_small_latch",
      hard_issues=_dedupe(hard),
      review_issues=_dedupe(review),
      same_frame_output_preserved=None,
      expected_runtime_behavior="remain_on_qcom_until_clean_restart",
      retry_expected_this_ignition=False,
    )

  if attempted == "qcom" and active == "qcom" and not obs.usbgpu_active:
    if obs.fallback_observed:
      hard.append("fallback_flag_without_egpu_attempt")
    if obs.hardware_present is True and obs.compiled_big is True and not obs.startup_failed:
      review.append("big_available_but_small_selected")
    return FaultAssessment(
      state=FaultState.INCONSISTENT if hard else FaultState.STARTUP_SMALL,
      reason="inconsistent_startup_small" if hard else "small_model_selected_at_startup",
      hard_issues=_dedupe(hard),
      review_issues=_dedupe(review),
      same_frame_output_preserved=None,
      expected_runtime_behavior="continue_qcom_for_current_process",
      retry_expected_this_ignition=False,
    )

  hard.append("unclassified_backend_state")
  return FaultAssessment(
    state=FaultState.INCONSISTENT,
    reason="unclassified_observation",
    hard_issues=_dedupe(hard),
    review_issues=_dedupe(review),
    same_frame_output_preserved=None,
    expected_runtime_behavior="manual_review_required",
    retry_expected_this_ignition=False,
  )


class FaultSequenceTracker:
  """Check temporal consistency across fault observations; observe-only."""
  def __init__(self):
    self.prev_frame_id: int | None = None
    self.prev_state: FaultState | None = None
    self.fallback_frame_id: int | None = None
    self.startup_failed_seen = False
    self.small_selected = False

  def reset(self) -> None:
    self.prev_frame_id = None
    self.prev_state = None
    self.fallback_frame_id = None
    self.startup_failed_seen = False
    self.small_selected = False

  def observe(self, obs: FaultObservation) -> dict[str, Any]:
    assessment = assess_fault_observation(obs)
    temporal: list[str] = []
    hard = list(assessment.hard_issues)

    if self.prev_frame_id is not None:
      if obs.frame_id <= self.prev_frame_id:
        hard.append("frame_id_not_monotonic")
      elif obs.frame_id > self.prev_frame_id + 1:
        temporal.append("frame_gap_after_previous_observation")

    if self.startup_failed_seen:
      if not obs.startup_failed:
        hard.append("startup_failed_latch_cleared_without_restart")
      if str(obs.attempted_backend).lower() == "egpu":
        hard.append("repeated_big_attempt_while_small_latched")

    if self.small_selected and str(obs.active_backend).lower() == "egpu":
      hard.append("unexpected_big_reentry_without_restart_boundary")

    if assessment.state == FaultState.SAME_FRAME_FALLBACK:
      temporal.append("runtime_fallback_onset")
      self.fallback_frame_id = obs.frame_id
    elif self.prev_state == FaultState.SAME_FRAME_FALLBACK:
      if assessment.state != FaultState.SMALL_LATCHED:
        hard.append("fallback_not_followed_by_small_latch")
      else:
        temporal.append("small_latch_confirmed")

    # Retain latch evidence even when the fallback output itself was invalid.
    self.startup_failed_seen = self.startup_failed_seen or obs.startup_failed or obs.fallback_observed
    self.small_selected = self.small_selected or str(obs.active_backend).lower() == "qcom" or obs.fallback_observed

    self.prev_frame_id = obs.frame_id
    self.prev_state = assessment.state
    return {
      "state": assessment.state.value,
      "reason": assessment.reason,
      "hardIssues": list(_dedupe(hard)),
      "reviewIssues": list(assessment.review_issues),
      "temporalTags": temporal,
      "sameFrameOutputPreserved": assessment.same_frame_output_preserved,
      "expectedRuntimeBehavior": assessment.expected_runtime_behavior,
      "retryExpectedThisIgnition": False,
      "reviewPolicy": {"usbSuperspeedMbps": USB_SUPERSPEED_MBPS, "purpose": "descriptive-link-class-not-a-safety-limit"},
      "controlAuthorization": False,
      "publicRoadAuthorization": False,
    }
