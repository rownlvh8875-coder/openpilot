"""Read-only lateral evidence; thresholds are the existing controlsd comparisons."""

import math
import numpy as np

from openpilot.cereal import car
from openpilot.selfdrive.controls.lib.drive_helpers import is_volkswagen_meb

STEER_ANGLE_SATURATION_THRESHOLD = 3.0
LAT_CURVATURE_SATURATION_ACCEL = 0.1


def instantaneous_limit_state(active, raw_limited, curvature_limited):
  if not all(isinstance(value, (bool, np.bool_)) for value in (active, raw_limited, curvature_limited)) or not active:
    return 'unavailable'
  return 'limited' if raw_limited or curvature_limited else 'notLimited'


def _meb_limit_state(active, pid, curvature_limited):
  try:
    value, positive, negative = pid.control, pid.pos_limit, pid.neg_limit
    if not all(math.isfinite(x) for x in (value, positive, negative)) or not negative < 0 < positive:
      return 'unavailable'
    return instantaneous_limit_state(active, bool(value >= positive or value <= negative), curvature_limited)
  except (AttributeError, TypeError, ValueError, OverflowError):
    return 'unavailable'


def record_lateral_observation(CC, CP, controller_log, *, is_meb, meb_pid, curvature_limited):
  """Attach only diagnostic fields to the exact command being published."""
  try:
    observation = CC.lateralControlObservation
    observation.version = 1
    observation.controllerKind = 'mebCurvature' if is_meb else controller_kind(CP)
    if is_meb:
      # The existing angle log is dummy for MEB. Do not publish its new raw flag
      # as evidence of the curvature controller; leave the old timer untouched.
      controller_log.instantaneousLimitState = 'unavailable'
      observation.instantaneousLimitState = _meb_limit_state(CC.latActive, meb_pid, curvature_limited)
    elif observation.controllerKind != 'unknown' and CC.latActive and controller_log.active:
      state = str(controller_log.instantaneousLimitState)
      observation.instantaneousLimitState = state if state in ('unavailable', 'notLimited', 'limited') else 'unavailable'
    else:
      observation.instantaneousLimitState = 'unavailable'
  except Exception:
    CC.lateralControlObservation = car.LateralControlObservation.new_message()


def controller_kind(CP):
  if is_volkswagen_meb(CP):
    return 'mebCurvature'
  if CP.steerControlType == 'angle':
    return 'angle'
  kind = CP.lateralTuning.which()
  return kind if kind in ('pid', 'torque') else 'unknown'


def lateral_output_limited(kind, requested, returned, speed_mps):
  """Exact existing arithmetic; callers requiring finite evidence validate first."""
  if kind == 'mebCurvature':
    return abs(requested.curvature - returned.curvature) * speed_mps**2 > LAT_CURVATURE_SATURATION_ACCEL
  if kind == 'angle':
    return abs(requested.steeringAngleDeg - returned.steeringAngleDeg) > STEER_ANGLE_SATURATION_THRESHOLD
  if kind in ('torque', 'pid'):
    return abs(requested.torque - returned.torque) > 1e-2
  return None


def output_limit_state(kind, requested, returned, speed_mps):
  field = {'mebCurvature': 'curvature', 'angle': 'steeringAngleDeg', 'torque': 'torque', 'pid': 'torque'}.get(kind)
  if field is None or not all(math.isfinite(getattr(value, field)) for value in (requested, returned)):
    return 'unavailable'
  if kind == 'mebCurvature' and not math.isfinite(speed_mps):
    return 'unavailable'
  return 'limited' if lateral_output_limited(kind, requested, returned, speed_mps) else 'notLimited'
