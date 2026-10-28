"""Read-only lateral evidence; thresholds are the existing controlsd comparisons."""

import math

from openpilot.selfdrive.controls.lib.drive_helpers import is_volkswagen_meb

STEER_ANGLE_SATURATION_THRESHOLD = 3.0
LAT_CURVATURE_SATURATION_ACCEL = 0.1


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
    return abs(requested.curvature - returned.curvature) * speed_mps ** 2 > LAT_CURVATURE_SATURATION_ACCEL
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
