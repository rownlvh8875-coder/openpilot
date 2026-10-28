"""Synthetic receipt boundary tests; no vehicle settings, IPC or CAN device."""

import importlib
import math

import pytest

from openpilot.cereal import car, log

NOW = 10_000_000_000


class Messages(dict):
  def __init__(self):
    super().__init__(pandaStates=[log.PandaState.new_message(pandaType='blackPanda', controlsAllowed=True, safetyModel='hyundai').as_reader()])
    self.seen = self.alive = self.valid = self.freq_ok = None
    for key in ('seen', 'alive', 'valid', 'freq_ok'):
      setattr(self, key, dict.fromkeys(('carControl', 'pandaStates'), True))
    self.recv_time = dict.fromkeys(('carControl', 'pandaStates'), 9.995)
    self.logMonoTime = {'carControl': 9_990_000_000, 'pandaStates': 9_980_000_000}
    self.ignore_alive = []
    self.ignore_valid = []
    self.ignore_average_freq = []
    self.simulation = False


def source(kind='torque', active=True):
  sm = Messages()
  cp = car.CarParams.new_message(brand='hyundai', safetyConfigs=[{'safetyModel': 'hyundai'}])
  cp.lateralTuning.init('pid' if kind == 'pid' else 'torque')
  if kind in ('angle', 'mebCurvature'):
    cp.steerControlType = 'angle'
  if kind == 'mebCurvature':
    from opendbc.car.volkswagen.values import VolkswagenFlags

    cp.brand = 'volkswagen'
    cp.flags = int(VolkswagenFlags.MEB)
  cc = car.CarControl.new_message(latActive=active)
  cc.actuators.torque = 0.25
  cc.lateralControlObservation.version = 1
  cc.lateralControlObservation.controllerKind = kind
  cc.lateralControlObservation.instantaneousLimitState = 'notLimited'
  return sm, cp.as_reader(), cc


def receipt(sm=None, cp=None, cc=None, output=None, replay=False, now=NOW):
  module = importlib.import_module('openpilot.selfdrive.car.actuation_receipt')
  if sm is None:
    sm, cp, cc = source()
  pending = module.capture_actuation_receipt(sm, cp, cc.as_reader(), now, replay, safety_now_ns=now)
  if output is None:
    output = car.CarControl.Actuators.new_message(torque=0.25)
  return module.complete_actuation_receipt(pending, output.as_reader(), 4.0)


def test_receipt_binds_source_final_request_output_and_publication_metadata():
  sm, cp, cc = source()
  result = receipt(sm, cp, cc)
  assert result.receiptVersion == 1
  assert result.sourceCarControlMonoTime.value == 9_990_000_000
  assert result.hostApplyMonoTime.value == NOW
  assert result.hostLateralActivityState == 'active'
  assert result.safetyPermissionState == 'allowed'
  assert result.safetyStateMonoTime.value == 9_980_000_000
  assert result.controllerKind == 'torque'
  assert result.instantaneousControllerLimitState == 'notLimited'
  assert result.finalRequestedActuators.torque == 0.25
  assert result.controllerOutputActuators.torque == 0.25
  assert result.outputLimitState == 'notLimited'
  assert result.controllerOutputReturned == 'returned'


def test_snapshot_copies_survive_request_and_returned_output_mutation():
  module = importlib.import_module('openpilot.selfdrive.car.actuation_receipt')
  sm, cp, cc = source()
  pending = module.capture_actuation_receipt(sm, cp, cc.as_reader(), NOW, False, safety_now_ns=NOW)
  cc.latActive = False
  cc.actuators.torque = -0.5
  output = car.CarControl.Actuators.new_message(torque=0.125)
  result = module.complete_actuation_receipt(pending, output.as_reader(), 4.0)
  output.torque = -0.75
  assert result.hostLateralActivityState == 'active'
  assert result.finalRequestedActuators.torque == 0.25
  assert result.controllerOutputActuators.torque == 0.125
  assert result.outputLimitState == 'limited'


def test_repeated_command_has_distinct_apply_identity_and_no_current_command_mix():
  sm, cp, cc = source()
  first = receipt(sm, cp, cc)
  second = receipt(sm, cp, cc, now=NOW + 1_000_000)
  cc.actuators.torque = -0.25
  sm.logMonoTime['carControl'] = NOW
  third = receipt(sm, cp, cc, now=NOW + 2_000_000)
  assert first.sourceCarControlMonoTime.value == second.sourceCarControlMonoTime.value
  assert first.hostApplyMonoTime.value < second.hostApplyMonoTime.value < third.hostApplyMonoTime.value
  assert first.finalRequestedActuators.torque == second.finalRequestedActuators.torque == 0.25
  assert third.sourceCarControlMonoTime.value == NOW
  assert third.finalRequestedActuators.torque == -0.25


@pytest.mark.parametrize('active,state', [(True, 'active'), (False, 'inactive')])
def test_explicit_final_host_activity(active, state):
  sm, cp, cc = source(active=active)
  assert receipt(sm, cp, cc).hostLateralActivityState == state


@pytest.mark.parametrize('flag', ['seen', 'alive', 'valid', 'freq_ok'])
def test_unhealthy_command_never_claims_host_activity(flag):
  sm, cp, cc = source()
  getattr(sm, flag)['carControl'] = False
  assert receipt(sm, cp, cc).hostLateralActivityState == 'unavailable'


@pytest.mark.parametrize('stamp', [None, 0, -1, NOW + 1, NOW - 101_000_000, True])
def test_missing_future_or_stale_command_stamp_is_not_available(stamp):
  sm, cp, cc = source()
  sm.logMonoTime['carControl'] = stamp
  assert receipt(sm, cp, cc).hostLateralActivityState == 'unavailable'


@pytest.mark.parametrize(
  'flags,want',
  [
    ([True], 'allowed'),
    ([False], 'disallowed'),
    ([True, True], 'allowed'),
    ([False, False], 'disallowed'),
    ([True, False], 'unavailable'),
    ([], 'unavailable'),
  ],
)
def test_permission_requires_known_unanimous_configuration_correlated_pandas(flags, want):
  sm, cp, cc = source()
  sm['pandaStates'] = [log.PandaState.new_message(pandaType='blackPanda', safetyModel='hyundai', controlsAllowed=x) for x in flags]
  cp = cp.as_builder()
  cp.safetyConfigs = [{'safetyModel': 'hyundai'}] * len(flags)
  result = receipt(sm, cp.as_reader(), cc)
  assert result.safetyPermissionState == want
  assert result.safetyStateMonoTime.which() == ('unavailable' if want == 'unavailable' else 'value')


@pytest.mark.parametrize(
  'defect',
  [
    'stale_publication',
    'future',
    'stale_receipt',
    'invalid',
    'dead',
    'unseen',
    'frequency',
    'bypass',
    'simulation',
    'unknown_simulation',
    'replay',
    'unknown_type',
    'fault',
    'fault_status',
    'heartbeat',
    'rx_checks',
    'configuration_mismatch',
  ],
)
def test_insufficient_panda_evidence_is_unavailable(defect):
  sm, cp, cc = source()
  if defect == 'stale_publication':
    sm.logMonoTime['pandaStates'] = NOW - 1_000_000_001
  elif defect == 'future':
    sm.logMonoTime['pandaStates'] = NOW + 1
  elif defect == 'stale_receipt':
    sm.recv_time['pandaStates'] = 8.999
  elif defect in ('invalid', 'dead', 'unseen', 'frequency'):
    getattr(sm, {'invalid': 'valid', 'dead': 'alive', 'unseen': 'seen', 'frequency': 'freq_ok'}[defect])['pandaStates'] = False
  elif defect == 'bypass':
    sm.ignore_valid = ['pandaStates']
  elif defect == 'simulation':
    sm.simulation = True
  elif defect == 'unknown_simulation':
    sm.simulation = None
  else:
    state = sm['pandaStates'][0].as_builder()
    if defect == 'unknown_type':
      state.pandaType = 'unknown'
    elif defect == 'fault':
      state.faults = ['relayMalfunction']
    elif defect == 'fault_status':
      state.faultStatus = 'faultPerm'
    elif defect == 'heartbeat':
      state.heartbeatLost = True
    elif defect == 'rx_checks':
      state.safetyRxChecksInvalid = True
    elif defect == 'configuration_mismatch':
      state.safetyParam = 1
    sm['pandaStates'] = [state.as_reader()]
  result = receipt(sm, cp, cc, replay=defect == 'replay')
  assert result.safetyPermissionState == 'unavailable'
  assert result.safetyStateMonoTime.which() == 'unavailable'


@pytest.mark.parametrize(
  'kind,field,requested_value,output,speed,want',
  [
    ('pid', 'torque', 0.25, 0.25, 4.0, 'notLimited'),
    ('torque', 'torque', 0.25, 0.125, 4.0, 'limited'),
    ('angle', 'steeringAngleDeg', 10.0, 2.0, 4.0, 'limited'),
    ('angle', 'steeringAngleDeg', 2.0, 2.0, 4.0, 'notLimited'),
    ('mebCurvature', 'curvature', 0.02, 0.0, 4.0, 'limited'),
    ('mebCurvature', 'curvature', 0.0, 0.0, 4.0, 'notLimited'),
  ],
)
def test_cohort_output_comparison_is_controller_aware(kind, field, requested_value, output, speed, want):
  module = importlib.import_module('openpilot.selfdrive.car.actuation_receipt')
  sm, cp, cc = source(kind)
  setattr(cc.actuators, field, requested_value)
  returned = car.CarControl.Actuators.new_message()
  setattr(returned, field, output)
  pending = module.capture_actuation_receipt(sm, cp, cc.as_reader(), NOW, False, safety_now_ns=NOW)
  assert module.complete_actuation_receipt(pending, returned.as_reader(), speed).outputLimitState == want


@pytest.mark.parametrize('defect', ['missing_observation', 'wrong_version', 'wrong_kind', 'nonfinite_request', 'nonfinite_output'])
def test_malformed_or_unknown_calculation_evidence_never_becomes_not_limited(defect):
  sm, cp, cc = source()
  output = car.CarControl.Actuators.new_message(torque=0.25)
  if defect == 'missing_observation':
    cc = car.CarControl.new_message(latActive=True)
  elif defect == 'wrong_version':
    cc.lateralControlObservation.version = 2
  elif defect == 'wrong_kind':
    cc.lateralControlObservation.controllerKind = 'angle'
  elif defect == 'nonfinite_request':
    cc.actuators.torque = math.nan
  elif defect == 'nonfinite_output':
    output.torque = math.inf
  result = receipt(sm, cp, cc, output)
  assert result.outputLimitState == 'unavailable'


def test_internal_observability_error_cannot_escape_control_boundary():
  class Broken:
    def __getattribute__(self, name):
      raise RuntimeError('synthetic malformed input')

  module = importlib.import_module('openpilot.selfdrive.car.actuation_receipt')
  pending = module.capture_actuation_receipt(Broken(), None, Broken(), NOW, False, safety_now_ns=NOW)
  result = module.complete_actuation_receipt(pending, Broken(), 4.0)
  assert result.hostLateralActivityState == 'unavailable'
  assert result.outputLimitState == 'unavailable'


def test_receipt_calls_have_no_file_network_thread_or_process_side_effect(monkeypatch):
  module = importlib.import_module('openpilot.selfdrive.car.actuation_receipt')
  sm, cp, cc = source()
  output = car.CarControl.Actuators.new_message()
  import builtins
  import socket
  import subprocess
  import threading

  def forbidden(*args, **kwargs):
    pytest.fail('unexpected observability side effect')

  monkeypatch.setattr(builtins, 'open', forbidden)
  monkeypatch.setattr(socket, 'socket', forbidden)
  monkeypatch.setattr(subprocess, 'Popen', forbidden)
  monkeypatch.setattr(threading.Thread, 'start', forbidden)
  pending = module.capture_actuation_receipt(sm, cp, cc.as_reader(), NOW, False, safety_now_ns=NOW)
  assert module.complete_actuation_receipt(pending, output.as_reader(), 4.0).controllerOutputReturned == 'returned'


@pytest.mark.parametrize('boot_now,panda_stamp,want', [(1_010_000_000_000, 1_009_980_000_000, 'allowed'), (10_500_000_000, 9_490_000_000, 'unavailable')])
def test_panda_boot_clock_and_monotonic_receive_clock_are_not_conflated(boot_now, panda_stamp, want):
  module = importlib.import_module('openpilot.selfdrive.car.actuation_receipt')
  sm, cp, cc = source()
  sm.logMonoTime['pandaStates'] = panda_stamp
  pending = module.capture_actuation_receipt(sm, cp, cc.as_reader(), NOW, False, safety_now_ns=boot_now)
  result = module.complete_actuation_receipt(pending, cc.actuators.as_reader(), 4.0)
  assert result.hostLateralActivityState == 'active'
  assert result.safetyPermissionState == want
  assert result.sourceCarControlMonoTime.clockDomain == 'monotonic'
  assert result.hostApplyMonoTime.clockDomain == 'monotonic'
  assert result.safetyObservationMonoTime.value == boot_now
  assert result.safetyObservationMonoTime.clockDomain == 'bootTime'
  if want == 'allowed':
    assert result.safetyStateMonoTime.value == panda_stamp
    assert result.safetyStateMonoTime.clockDomain == 'bootTime'


def test_unknown_boot_clock_cannot_assert_fresh_safety_permission():
  module = importlib.import_module('openpilot.selfdrive.car.actuation_receipt')
  sm, cp, cc = source()
  pending = module.capture_actuation_receipt(sm, cp, cc.as_reader(), NOW, False, safety_now_ns=None)
  assert module.complete_actuation_receipt(pending, cc.actuators.as_reader(), 4.0).safetyPermissionState == 'unavailable'


@pytest.mark.parametrize('field', ['pandaType', 'safetyModel'])
def test_unrecognized_wire_enum_is_not_a_known_panda_or_safety_configuration(field):
  sm, cp, cc = source()
  state = sm['pandaStates'][0].as_builder()
  setattr(state, field, 65535)
  if field == 'safetyModel':
    cp = cp.as_builder()
    cp.safetyConfigs[0].safetyModel = 65535
  sm['pandaStates'] = [state.as_reader()]
  assert receipt(sm, cp, cc).safetyPermissionState == 'unavailable'


def test_boot_clock_query_failure_is_unavailable(monkeypatch):
  module = importlib.import_module('openpilot.selfdrive.car.actuation_receipt')

  def unavailable(clock):
    raise OSError('synthetic unavailable clock')

  monkeypatch.setattr(module.time, 'clock_gettime_ns', unavailable)
  assert module.safety_observation_clock_ns() is None
