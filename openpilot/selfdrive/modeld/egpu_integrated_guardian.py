"""Observe-only Guardian contract for the Carrot-WIP integrated eGPU branch.

Stage 4A evaluates evidence quality and active-vs-shadow disagreement. It never
returns steering, acceleration, a model selection command, or control
authorization. A later reviewed stage may consume Guardian evidence, but this
module is intentionally incapable of doing so.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import math
from typing import Any


@dataclass(frozen=True)
class ActionSnapshot:
  frame_id: int
  frame_age: int
  model_execution_ms: float
  curvature: float
  acceleration: float
  should_stop: bool
  backend: str
  timestamp_mono_s: float | None = None


@dataclass(frozen=True)
class GuardianPolicy:
  max_frame_age: int = 1
  max_execution_ms: float | None = None
  curvature_abs: float = 0.003
  curvature_rel: float = 0.25
  accel_abs: float = 0.50
  max_hardware_age_s: float | None = 5.0
  reject_supply_fault: bool = True

  def validate(self) -> None:
    if self.max_frame_age < 0:
      raise ValueError("max_frame_age must be non-negative")
    if self.max_execution_ms is not None and self.max_execution_ms <= 0:
      raise ValueError("max_execution_ms must be positive when set")
    if self.curvature_abs <= 0 or self.curvature_rel <= 0 or self.accel_abs <= 0:
      raise ValueError("disagreement thresholds must be positive")
    if self.max_hardware_age_s is not None and self.max_hardware_age_s <= 0:
      raise ValueError("max_hardware_age_s must be positive when set")


@dataclass(frozen=True)
class DisagreementMetrics:
  curvature_abs: float
  curvature_rel: float
  accel_abs: float
  stop_mismatch: bool


@dataclass(frozen=True)
class GuardianAssessment:
  stage: str
  active_backend: str
  shadow_backend: str
  frame_id: int | None
  hard_issues: tuple[str, ...]
  review_issues: tuple[str, ...]
  disagreement: DisagreementMetrics | None
  evidence_eligible: bool
  analysis_status: str
  control_authorization: bool = False
  shadow_publish_to_controls: bool = False


def _finite(value: Any) -> bool:
  try:
    return math.isfinite(float(value))
  except (TypeError, ValueError):
    return False


def _snapshot_finite(s: ActionSnapshot) -> bool:
  return all(_finite(v) for v in (s.model_execution_ms, s.curvature, s.acceleration)) and (
    s.timestamp_mono_s is None or _finite(s.timestamp_mono_s)
  )


def disagreement(active: ActionSnapshot, shadow: ActionSnapshot) -> DisagreementMetrics:
  curv_abs = abs(float(active.curvature) - float(shadow.curvature))
  denom = max(abs(float(active.curvature)), abs(float(shadow.curvature)), 1e-6)
  curv_rel = curv_abs / denom
  accel_abs = abs(float(active.acceleration) - float(shadow.acceleration))
  return DisagreementMetrics(curv_abs, curv_rel, accel_abs, bool(active.should_stop) != bool(shadow.should_stop))


def _hardware_age_s(action: ActionSnapshot, hardware: dict[str, Any]) -> float | None:
  if action.timestamp_mono_s is None:
    return None
  hw_t = hardware.get("timestampMonoS")
  if not _finite(hw_t):
    return None
  return max(0.0, float(action.timestamp_mono_s) - float(hw_t))


def assess_guardian(*, active: ActionSnapshot, shadow: ActionSnapshot, hardware: dict[str, Any] | None = None,
                    policy: GuardianPolicy | None = None) -> GuardianAssessment:
  """Assess research evidence only. The return type cannot authorize controls."""
  policy = policy or GuardianPolicy()
  policy.validate()
  hard: list[str] = []
  review: list[str] = []

  if int(active.frame_id) != int(shadow.frame_id):
    hard.append("frame_mismatch")
  if int(active.frame_age) > policy.max_frame_age:
    hard.append("active_frame_stale")
  if int(shadow.frame_age) > policy.max_frame_age:
    hard.append("shadow_frame_stale")
  if not _snapshot_finite(active):
    hard.append("active_nonfinite")
  if not _snapshot_finite(shadow):
    hard.append("shadow_nonfinite")

  if policy.max_execution_ms is not None:
    if _finite(active.model_execution_ms) and active.model_execution_ms > policy.max_execution_ms:
      hard.append("active_execution_over_policy")
    if _finite(shadow.model_execution_ms) and shadow.model_execution_ms > policy.max_execution_ms:
      hard.append("shadow_execution_over_policy")

  metrics = None
  if "frame_mismatch" not in hard and _snapshot_finite(active) and _snapshot_finite(shadow):
    metrics = disagreement(active, shadow)
    if metrics.curvature_abs >= policy.curvature_abs and metrics.curvature_rel >= policy.curvature_rel:
      review.append("curvature_disagreement")
    if metrics.accel_abs >= policy.accel_abs:
      review.append("acceleration_disagreement")
    if metrics.stop_mismatch:
      review.append("stop_decision_mismatch")

  if active.backend == shadow.backend:
    review.append("same_backend_comparison")

  if hardware is None:
    review.append("hardware_evidence_missing")
  else:
    if hardware.get("valid") is False:
      review.append("hardware_telemetry_invalid")
    if policy.reject_supply_fault and bool(hardware.get("supplyFault", False)):
      hard.append("egpu_supply_fault")
    if policy.max_hardware_age_s is not None:
      age = _hardware_age_s(active, hardware)
      if age is not None and age > policy.max_hardware_age_s:
        hard.append("hardware_evidence_stale")

  hard = list(dict.fromkeys(hard))
  review = list(dict.fromkeys(review))
  eligible = not hard
  status = "HOLD" if hard else ("REVIEW" if review else "OBSERVE")
  frame_id = int(active.frame_id) if int(active.frame_id) == int(shadow.frame_id) else None
  return GuardianAssessment(
    stage="S4A-observe-only",
    active_backend=str(active.backend),
    shadow_backend=str(shadow.backend),
    frame_id=frame_id,
    hard_issues=tuple(hard),
    review_issues=tuple(review),
    disagreement=metrics,
    evidence_eligible=eligible,
    analysis_status=status,
    control_authorization=False,
    shadow_publish_to_controls=False,
  )


def assessment_to_dict(result: GuardianAssessment) -> dict[str, Any]:
  return {
    "stage": result.stage,
    "activeBackend": result.active_backend,
    "shadowBackend": result.shadow_backend,
    "frameId": result.frame_id,
    "hardIssues": list(result.hard_issues),
    "reviewIssues": list(result.review_issues),
    "disagreement": asdict(result.disagreement) if result.disagreement is not None else None,
    "evidenceEligible": result.evidence_eligible,
    "analysisStatus": result.analysis_status,
    "controlAuthorization": False,
    "shadowPublishToControls": False,
  }
