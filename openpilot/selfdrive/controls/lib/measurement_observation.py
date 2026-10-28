"""Copy existing VM calculation evidence; never calculate or change controls."""

from dataclasses import dataclass
import math
from numbers import Real
import time

from openpilot.cereal import car
from openpilot.selfdrive.controls.lib.lateral_readiness import NominalLateralParameters

NUMERIC_FIELDS = (
  'measuredCurvatureInvM',
  'inputSpeedMps',
  'inputSteeringAngleDeg',
  'effectiveStiffnessFactor',
  'effectiveSteerRatio',
  'effectiveAngleOffsetDeg',
  'effectiveRollRad',
)
STATES = ('unavailable', 'invalid', 'valid')


@dataclass(frozen=True)
class VMMeasurement:
  mode: str = 'unavailable'
  validity: str = 'unavailable'
  car_state_ns: int | None = None
  live_parameters_ns: int | None = None
  calculation_ns: int | None = None
  car_state_health: str = 'unavailable'
  live_parameters_health: str = 'unavailable'
  values: tuple[float, ...] = ()


def _number(value):
  if not isinstance(value, Real) or isinstance(value, bool):
    return None
  try:
    value = float(value)
    return value if math.isfinite(value) else None
  except (ValueError, OverflowError):
    return None


def _stamp(value):
  return value if type(value) is int and value > 0 else None


def _topic_health(sm, topic):
  if sm.simulation is not False or any(topic in getattr(sm, key) for key in ('ignore_alive', 'ignore_valid', 'ignore_average_freq')):
    return 'unavailable'
  flags = [getattr(sm, key).get(topic) for key in ('seen', 'alive', 'valid', 'freq_ok')]
  if any(type(flag) is not bool for flag in flags):
    return 'unavailable'
  return 'valid' if all(flags) else 'invalid'


def capture_vm_measurement(sm, CS, lp, curvature, stiffness_factor, steer_ratio, *, now_ns=None):
  """Freeze exactly the values already used by VM, before subsequent CC creation."""
  try:
    now_ns = _stamp(time.monotonic_ns() if now_ns is None else now_ns)
    mode = 'vehicleModelLive' if lp is sm['liveParameters'] else 'vehicleModelFallback' if isinstance(lp, NominalLateralParameters) else 'unavailable'
    cs_health = _topic_health(sm, 'carState')
    lp_health = _topic_health(sm, 'liveParameters')
    if CS.canValid is not True and cs_health == 'valid':
      cs_health = 'invalid'
    cs_stamp = _stamp(sm.logMonoTime.get('carState'))
    lp_stamp = _stamp(sm.logMonoTime.get('liveParameters')) if mode == 'vehicleModelLive' else None
    numbers = tuple(
      _number(value)
      for value in (
        curvature,
        CS.vEgo,
        CS.steeringAngleDeg,
        stiffness_factor,
        steer_ratio,
        lp.angleOffsetDeg,
        lp.roll,
      )
    )
    validity = 'unavailable'
    if mode != 'unavailable' and now_ns is not None and 'unavailable' not in (cs_health, lp_health):
      validity = 'invalid'
      if mode == 'vehicleModelLive' and cs_stamp is not None and lp_stamp is not None:
        payload_ok = all(getattr(lp, key) is True for key in ('valid', 'sensorValid', 'posenetValid'))
        payload_numbers = (_number(lp.stiffnessFactor), _number(lp.steerRatio))
        if not payload_ok and lp_health == 'valid':
          lp_health = 'invalid'
        if (
          cs_health == lp_health == 'valid'
          and payload_ok
          and None not in numbers
          and None not in payload_numbers
          and all(value > 0 for value in payload_numbers)
          and numbers[1] >= 0
          and numbers[3] > 0
          and numbers[4] > 0
          and cs_stamp <= now_ns
          and lp_stamp <= now_ns
        ):
          validity = 'valid'
      elif mode == 'vehicleModelLive':
        validity = 'unavailable'
    return VMMeasurement(mode, validity, cs_stamp, lp_stamp, now_ns, cs_health, lp_health, numbers if validity == 'valid' else ())
  except Exception:
    # Optional telemetry cannot interfere with the existing calculation path.
    return VMMeasurement()


def capture_desired_target(value):
  """Copy the clipped pre-controller value; no reconstruction from actuators."""
  return _number(value)


def _measurement_message(evidence):
  result = car.LateralMeasurementObservation.new_message()
  if (
    type(evidence) is not VMMeasurement
    or evidence.mode not in ('vehicleModelLive', 'vehicleModelFallback')
    or any(value not in STATES for value in (evidence.validity, evidence.car_state_health, evidence.live_parameters_health))
  ):
    return result
  if evidence.validity == 'valid' and (
    evidence.mode != 'vehicleModelLive'
    or evidence.car_state_health != 'valid'
    or evidence.live_parameters_health != 'valid'
    or any(_stamp(value) is None for value in (evidence.car_state_ns, evidence.live_parameters_ns, evidence.calculation_ns))
    or evidence.car_state_ns > evidence.calculation_ns
    or evidence.live_parameters_ns > evidence.calculation_ns
  ):
    return result
  result.version = 1
  result.measurementMode = evidence.mode
  result.validityState = evidence.validity
  result.carStateHealthState = evidence.car_state_health
  result.liveParametersHealthState = evidence.live_parameters_health
  for name, value in (
    ('carStateMonoTime', evidence.car_state_ns),
    ('liveParametersMonoTime', evidence.live_parameters_ns),
    ('calculationMonoTime', evidence.calculation_ns),
  ):
    if _stamp(value) is not None:
      getattr(result, name).value = value
      getattr(result, name).clockDomain = 'monotonic'
  if evidence.validity == 'valid':
    if len(evidence.values) != len(NUMERIC_FIELDS) or any(_number(value) is None for value in evidence.values):
      return car.LateralMeasurementObservation.new_message()
    for name, value in zip(NUMERIC_FIELDS, evidence.values, strict=True):
      getattr(result, name).value = value
  return result


def record_measurement_and_target(CC, evidence, target):
  """Write only new observation fields; no actuator/controller/Params access."""
  try:
    observation = CC.lateralControlObservation
    observation.measurement = car.LateralMeasurementObservation.new_message()
    observation.desiredTargetState = 'unavailable'
    observation.desiredTargetCurvatureInvM = car.ObservationFloat64.new_message()
    if CC.latActive is not True or observation.version != 2 or str(observation.controllerKind) not in ('pid', 'torque', 'angle', 'mebCurvature'):
      return
    observation.measurement = _measurement_message(evidence)
    target = _number(target)
    if target is not None:
      observation.desiredTargetCurvatureInvM.value = target
      observation.desiredTargetState = 'available'
  except Exception:
    # The old kind/limit fields remain intact if these optional fields fail.
    return
