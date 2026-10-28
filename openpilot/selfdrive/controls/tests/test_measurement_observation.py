"""Same-calculation metadata, with synthetic clocks and no settings or devices."""

from dataclasses import FrozenInstanceError, replace
import math

import pytest

from openpilot.cereal import car
from openpilot.selfdrive.controls.lib.lateral_readiness import NominalLateralParameters
from openpilot.selfdrive.controls.lib.measurement_observation import (
  capture_desired_target,
  capture_vm_measurement,
  record_measurement_and_target,
)
from openpilot.selfdrive.controls.tests.test_lateral_readiness import Messages

NOW = 10_000_000_000


def fixture():
  sm = Messages()
  sm.simulation = False
  sm.ignore_alive = sm.ignore_valid = sm.ignore_average_freq = []
  sm.logMonoTime = {'carState': NOW - 1_000_000, 'liveParameters': NOW - 2_000_000}
  return sm, sm['carState'], sm['liveParameters']


def capture(sm, cs, lp, **kwargs):
  return capture_vm_measurement(sm, cs, lp, 0.012345678901234567, 0.1, 16.123456789, now_ns=NOW, **kwargs)


def command(evidence, target=0.0, version=2, active=True):
  cc = car.CarControl.new_message(latActive=active)
  cc.lateralControlObservation.version = version
  cc.lateralControlObservation.controllerKind = 'torque'
  cc.lateralControlObservation.instantaneousLimitState = 'limited'
  record_measurement_and_target(cc, evidence, target)
  return cc


def test_live_evidence_preserves_exact_values_stamps_and_independent_health():
  sm, cs, lp = fixture()
  cs.vEgo, cs.steeringAngleDeg = 4.0, -1.25
  lp.angleOffsetDeg, lp.roll = 0.5, 0.03125
  evidence = capture(sm, cs, lp)
  observed = command(evidence).lateralControlObservation.measurement
  assert observed.version == 1
  assert observed.measurementMode == 'vehicleModelLive'
  assert observed.validityState == observed.carStateHealthState == observed.liveParametersHealthState == 'valid'
  assert observed.measuredCurvatureInvM.value == 0.012345678901234567
  assert observed.effectiveStiffnessFactor.value == 0.1
  assert observed.effectiveSteerRatio.value == 16.123456789
  assert observed.inputSpeedMps.value == cs.vEgo
  assert observed.inputSteeringAngleDeg.value == cs.steeringAngleDeg
  assert observed.effectiveAngleOffsetDeg.value == lp.angleOffsetDeg
  assert observed.effectiveRollRad.value == lp.roll
  assert observed.carStateMonoTime.value == sm.logMonoTime['carState']
  assert observed.liveParametersMonoTime.value == sm.logMonoTime['liveParameters']
  assert observed.calculationMonoTime.value == NOW
  assert observed.carStateMonoTime.clockDomain == observed.liveParametersMonoTime.clockDomain == 'monotonic'
  with pytest.raises(FrozenInstanceError):
    evidence.mode = 'vehicleModelFallback'
  cs.vEgo = 100.0
  lp.roll = 1.0
  assert command(evidence).lateralControlObservation.measurement.inputSpeedMps.value == 4.0


@pytest.mark.parametrize('topic', ['carState', 'liveParameters'])
@pytest.mark.parametrize('flag', ['seen', 'alive', 'valid', 'freq_ok'])
def test_current_bad_health_is_invalid_even_if_startup_previously_latched(topic, flag):
  sm, cs, lp = fixture()
  getattr(sm, flag)[topic] = False
  observed = command(capture(sm, cs, lp)).lateralControlObservation.measurement
  assert observed.measurementMode == 'vehicleModelLive'
  assert observed.validityState == 'invalid'
  assert observed.measuredCurvatureInvM.which() == 'unavailable'


@pytest.mark.parametrize('topic', ['carState', 'liveParameters'])
@pytest.mark.parametrize('stamp', [None, 0, True, NOW + 1])
def test_missing_or_future_publication_time_cannot_be_valid(topic, stamp):
  sm, cs, lp = fixture()
  sm.logMonoTime[topic] = stamp
  observed = command(capture(sm, cs, lp)).lateralControlObservation.measurement
  assert observed.validityState != 'valid'
  assert observed.inputSpeedMps.which() == 'unavailable'


def test_fallback_is_named_and_cannot_be_valid_live_measurement():
  sm, cs, _ = fixture()
  observed = command(capture(sm, cs, NominalLateralParameters(12.0))).lateralControlObservation.measurement
  assert observed.measurementMode == 'vehicleModelFallback'
  assert observed.validityState == 'invalid'
  assert observed.liveParametersMonoTime.which() == 'unavailable'
  assert observed.measuredCurvatureInvM.which() == 'unavailable'


@pytest.mark.parametrize('defect', ['simulation', 'unknown_simulation', 'ignore', 'missing_health', 'payload', 'can', 'unknown_mode'])
def test_unknown_relaxed_or_invalid_sources_do_not_supply_optional_numbers(defect):
  sm, cs, lp = fixture()
  if defect == 'simulation':
    sm.simulation = True
  elif defect == 'unknown_simulation':
    sm.simulation = None
  elif defect == 'ignore':
    sm.ignore_valid = ['liveParameters']
  elif defect == 'missing_health':
    del sm.freq_ok['carState']
  elif defect == 'payload':
    lp.sensorValid = False
  elif defect == 'can':
    cs.canValid = False
  else:
    lp = object()
  observed = command(capture(sm, cs, lp)).lateralControlObservation.measurement
  assert observed.validityState != 'valid'
  assert observed.effectiveSteerRatio.which() == 'unavailable'


@pytest.mark.parametrize('field', ['vEgo', 'steeringAngleDeg', 'angleOffsetDeg', 'roll'])
@pytest.mark.parametrize('value', [math.nan, math.inf, -math.inf])
def test_nonfinite_actual_input_is_not_serialized_as_evidence(field, value):
  sm, cs, lp = fixture()
  setattr(cs if field in ('vEgo', 'steeringAngleDeg') else lp, field, value)
  observed = command(capture(sm, cs, lp)).lateralControlObservation.measurement
  assert observed.validityState == 'invalid'
  assert observed.measuredCurvatureInvM.which() == 'unavailable'


@pytest.mark.parametrize('values', [(math.nan, 1, 12), (0.0, 0, 12), (0.0, 1, 0), (0.0, 1, math.inf)])
def test_nonfinite_or_unrepresentable_effective_configuration_is_invalid(values):
  sm, cs, lp = fixture()
  evidence = capture_vm_measurement(sm, cs, lp, *values, now_ns=NOW)
  assert command(evidence).lateralControlObservation.measurement.validityState == 'invalid'


@pytest.mark.parametrize('active,version', [(False, 2), (True, 0), (True, 1), (True, 3)])
def test_inactive_or_unsupported_calculation_has_no_target_or_measurement(active, version):
  observed = command(capture(*fixture()), version=version, active=active).lateralControlObservation
  assert observed.measurement.version == 0
  assert observed.desiredTargetState == 'unavailable'
  assert observed.desiredTargetCurvatureInvM.which() == 'unavailable'


@pytest.mark.parametrize(
  'target,want', [(0.0, 0.0), (0.012345678901234567, 0.012345678901234567), (math.nan, None), (math.inf, None), (None, None), (True, None)]
)
def test_target_capture_does_not_replace_unavailable_with_zero(target, want):
  captured = capture_desired_target(target)
  assert captured == want
  observed = command(capture(*fixture()), captured).lateralControlObservation
  assert observed.desiredTargetState == ('unavailable' if want is None else 'available')
  assert observed.desiredTargetCurvatureInvM.which() == ('unavailable' if want is None else 'value')


def test_unknown_evidence_enum_is_unavailable_without_touching_v1_limit():
  evidence = replace(capture(*fixture()), mode='unknown-wire')
  observed = command(evidence).lateralControlObservation
  assert observed.measurement.measurementMode == 'unavailable'
  assert observed.measurement.validityState == 'unavailable'
  assert observed.instantaneousLimitState == 'limited'


def test_corrupted_frozen_copy_does_not_serialize_nonfinite_values():
  evidence = replace(capture(*fixture()), values=(math.nan,))
  cc = command(evidence)
  assert cc.lateralControlObservation.measurement.version == 0
  assert cc.lateralControlObservation.instantaneousLimitState == 'limited'


@pytest.mark.parametrize(
  'changes',
  [
    {'mode': 'vehicleModelFallback'},
    {'car_state_health': 'unavailable'},
    {'live_parameters_health': 'invalid'},
    {'car_state_ns': None},
    {'live_parameters_ns': NOW + 1},
    {'calculation_ns': None},
  ],
)
def test_inconsistent_valid_copy_cannot_publish_available_numbers(changes):
  evidence = replace(capture(*fixture()), **changes)
  observed = command(evidence).lateralControlObservation.measurement
  assert observed.validityState != 'valid'
  assert observed.measuredCurvatureInvM.which() == 'unavailable'


def test_unrepresentable_number_is_not_changed_to_a_numeric_zero():
  assert capture_desired_target(10**1000) is None


def test_attachment_failure_cannot_change_actuators_or_v1_fields(monkeypatch):
  import openpilot.selfdrive.controls.lib.measurement_observation as module

  evidence = capture(*fixture())
  cc = car.CarControl.new_message(latActive=True)
  cc.actuators.curvature = 0.001
  cc.lateralControlObservation.version = 2
  cc.lateralControlObservation.controllerKind = 'torque'
  cc.lateralControlObservation.instantaneousLimitState = 'limited'
  before = cc.actuators.to_dict()

  def broken(evidence):
    raise RuntimeError('synthetic attachment failure')

  monkeypatch.setattr(module, '_measurement_message', broken)
  record_measurement_and_target(cc, evidence, 0.001)
  assert cc.actuators.to_dict() == before
  assert cc.lateralControlObservation.instantaneousLimitState == 'limited'
  assert cc.lateralControlObservation.measurement.version == 0


def test_telemetry_exceptions_and_io_prohibition_do_not_escape(monkeypatch):
  import builtins
  import socket
  import subprocess
  import threading

  def forbidden(*args, **kwargs):
    raise AssertionError('unexpected IO')

  sm, cs, lp = fixture()
  for owner, name in ((builtins, 'open'), (socket, 'socket'), (subprocess, 'Popen'), (threading.Thread, 'start')):
    monkeypatch.setattr(owner, name, forbidden)
  assert command(capture(sm, cs, lp)).lateralControlObservation.measurement.validityState == 'valid'
  sm.seen = None
  assert command(capture(sm, cs, lp)).lateralControlObservation.measurement.validityState != 'valid'
