"""Execute actual old/new state_control; replace only offline construction/IO."""

import ast
from pathlib import Path
import subprocess

import numpy as np
import pytest

from openpilot.cereal import car
from openpilot.common.pid import MultiplicativeUnwindPID
from openpilot.selfdrive.controls.lib.latcontrol_angle import LatControlAngle
from openpilot.selfdrive.controls.lib.latcontrol_pid import LatControlPID
from openpilot.selfdrive.controls.tests.test_lateral_startup_control import control_fixture

START = '4d89f044fc74138b0938f309ff8ea88d90faddcd'
ROOT = Path(__file__).resolve().parents[4]
NOW = 10_000_000_000


def fixture(kind='torque', baseline=False):
  control, step = control_fixture()
  control.lateral_startup.ready = True
  sm = control.sm
  sm.simulation = False
  sm.ignore_alive = sm.ignore_valid = sm.ignore_average_freq = []
  sm.logMonoTime = {'carState': NOW - 1_000_000, 'liveParameters': NOW - 2_000_000}
  sm['modelV2'].action.desiredCurvature = 0.002
  sm['liveParameters'].stiffnessFactor = 0.05  # Synthetic clamp input.
  sm['liveParameters'].angleOffsetDeg = 0.5
  sm['liveParameters'].roll = 0.03125
  reads = []
  original = control.params

  class Settings:
    def get_float(self, key):
      reads.append(('float', key))
      return {'SteerRatioRate': 137.0, 'CustomSR': 173.0}.get(key, original.get_float(key))

    def get_int(self, key):
      reads.append(('int', key))
      return original.get_int(key)

    def get_bool(self, key):
      reads.append(('bool', key))
      return original.get_bool(key)

  control.params = Settings()
  cp = control.CP.as_builder()
  if kind == 'pid':
    tuning = cp.lateralTuning.init('pid')
    tuning.kpBP, tuning.kpV, tuning.kiBP, tuning.kiV = [0.0], [1.0], [0.0], [0.1]
    control.CI.get_steer_feedforward_function = lambda: lambda angle, speed: 0.0
    control.LaC = LatControlPID(cp.as_reader(), control.CI)
  elif kind in ('angle', 'mebCurvature'):
    cp.steerControlType = 'angle'
    if kind == 'mebCurvature':
      from opendbc.car.volkswagen.values import VolkswagenFlags

      cp.brand, cp.flags = 'volkswagen', int(VolkswagenFlags.MEB)
      control.is_vw_meb = True
      control.meb_curvature_pid = MultiplicativeUnwindPID(1.0, 0.1, k_f=0.4, pos_limit=0.25, neg_limit=-0.25)
      control.calibrated_pose = None
      sm['carState'].steeringCurvature = 0.02
    control.LaC = LatControlAngle(cp.as_reader(), control.CI)
  control.CP = cp.as_reader()
  if baseline:
    namespace = next(cell.cell_contents for cell in step.__closure__ if isinstance(cell.cell_contents, dict))
    namespace = dict(namespace)
    source = subprocess.check_output(['git', 'show', START + ':openpilot/selfdrive/controls/controlsd.py'], cwd=ROOT, text=True)
    tree = ast.parse(source)
    node = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == 'state_control')
    exec(compile(ast.Module(body=[node], type_ignores=[]), '<pinned-state-control>', 'exec'), namespace)

    def baseline_step():
      return namespace['state_control'](control)

    step = baseline_step
  return control, step, reads


@pytest.mark.parametrize('kind', ['pid', 'torque', 'angle', 'mebCurvature'])
def test_same_call_vm_result_effective_configuration_and_precontroller_target(kind, monkeypatch):
  import openpilot.selfdrive.controls.lib.measurement_observation as module

  monkeypatch.setattr(module.time, 'monotonic_ns', lambda: NOW)
  control, step, _ = fixture(kind)
  cc, _ = step()
  assert cc.latActive
  observed = cc.lateralControlObservation
  assert observed.version == 2
  measurement = observed.measurement
  assert measurement.validityState == 'valid'
  assert measurement.measuredCurvatureInvM.value == control.curvature
  assert measurement.effectiveStiffnessFactor.value == 0.1
  assert measurement.effectiveSteerRatio.value == control.VM.sR == 17.3
  assert measurement.effectiveAngleOffsetDeg.value == control.sm['liveParameters'].angleOffsetDeg
  assert measurement.effectiveRollRad.value == control.sm['liveParameters'].roll
  assert measurement.carStateMonoTime.value == control.sm.logMonoTime['carState']
  assert measurement.liveParametersMonoTime.value == control.sm.logMonoTime['liveParameters']
  assert observed.desiredTargetCurvatureInvM.value == control.desired_curvature
  if kind == 'mebCurvature':
    assert observed.desiredTargetCurvatureInvM.value != cc.actuators.curvature
  else:
    assert float(np.float32(observed.desiredTargetCurvatureInvM.value)) == cc.actuators.curvature


def test_current_bad_live_health_is_not_hidden_by_sticky_startup(monkeypatch):
  import openpilot.selfdrive.controls.lib.measurement_observation as module

  monkeypatch.setattr(module.time, 'monotonic_ns', lambda: NOW)
  control, step, _ = fixture()
  control.sm.alive['liveParameters'] = False
  cc, _ = step()
  assert control.lateral_startup.ready and cc.latActive
  assert cc.lateralControlObservation.measurement.validityState == 'invalid'


@pytest.mark.parametrize('kind', ['pid', 'torque', 'angle', 'mebCurvature'])
def test_complete_control_values_vm_state_pid_and_params_reads_match_starting_head(kind):
  outcomes = []
  for baseline in (True, False):
    control, step, reads = fixture(kind, baseline)
    stream = []
    for index in range(16):
      control.sm['carState'].steeringPressed = index % 4 == 0
      control.sm['modelV2'].action.desiredCurvature = (-0.002, 0.002)[index % 2]
      cc, status = step()
      values = cc.to_dict()
      observation = values.pop('lateralControlObservation')
      stream.append(
        (
          values,
          status.to_dict(),
          observation['controllerKind'],
          observation['instantaneousLimitState'],
          control.curvature,
          control.desired_curvature,
          control.VM.sR,
          control.VM.cF,
          control.LaC.sat_count,
          None if control.meb_curvature_pid is None else (control.meb_curvature_pid.control, control.meb_curvature_pid.i),
        )
      )
    outcomes.append((stream, reads))
  assert outcomes[0] == outcomes[1]


def test_scaled_live_ratio_is_recorded_without_an_additional_settings_read(monkeypatch):
  import openpilot.selfdrive.controls.lib.measurement_observation as module

  monkeypatch.setattr(module.time, 'monotonic_ns', lambda: NOW)
  control, step, reads = fixture()
  original = control.params.get_float
  control.params.get_float = lambda key: 0.0 if key == 'CustomSR' else original(key)
  cc, _ = step()
  assert cc.lateralControlObservation.measurement.effectiveSteerRatio.value == control.VM.sR
  assert control.VM.sR == control.sm['liveParameters'].steerRatio * 137.0 * 0.01
  assert reads.count(('float', 'SteerRatioRate')) == 1


@pytest.mark.parametrize('kind', ['pid', 'torque', 'angle'])
def test_exactly_representable_normal_target_equals_existing_request(kind, monkeypatch):
  import openpilot.selfdrive.controls.lib.measurement_observation as module

  monkeypatch.setattr(module.time, 'monotonic_ns', lambda: NOW)
  control, step, _ = fixture(kind)
  control.sm['carState'].steeringAngleDeg = 0
  control.sm['liveParameters'].angleOffsetDeg = control.sm['liveParameters'].roll = 0
  control.sm['modelV2'].action.desiredCurvature = 0
  cc, _ = step()
  assert cc.lateralControlObservation.desiredTargetCurvatureInvM.value == cc.actuators.curvature == 0


def lookup_exact_source(receipt, history):
  """Test-only lookup contract, never an adapter or live subscription."""
  if receipt.sourceCarControlMonoTime.which() != 'value':
    raise LookupError('missing source identity')
  stamp = receipt.sourceCarControlMonoTime.value
  if stamp not in history:
    raise LookupError('exact source unavailable')
  return history[stamp]


def test_delayed_receipt_looks_up_v2_command_and_never_uses_current_evidence(monkeypatch):
  import openpilot.selfdrive.controls.lib.measurement_observation as module
  from openpilot.selfdrive.car.tests.test_actuation_card import fixture as card_fixture

  calculation_ns = NOW - 20_000_000
  monkeypatch.setattr(module.time, 'monotonic_ns', lambda: calculation_ns)
  control, step, _ = fixture()
  control.sm.logMonoTime = {'carState': calculation_ns - 1_000_000, 'liveParameters': calculation_ns - 2_000_000}
  cc, _ = step()
  ns, clock, card, _, _, _, publications, _ = card_fixture()
  card.sm['carControl'] = cc.as_reader()
  source_stamp = card.sm.logMonoTime['carControl']
  history = {source_stamp: cc.to_bytes()}
  ns['step'](card)
  clock.now += 10_000_000
  card.sm.logMonoTime['carControl'] = NOW + 5_000_000
  cc.lateralControlObservation.desiredTargetCurvatureInvM.value = 0.1
  ns['step'](card)
  from openpilot.cereal import log

  with log.Event.from_bytes([payload for topic, payload in publications if topic == 'carOutput'][-1]) as event:
    receipt = event.carOutput.actuationReceipt
    assert receipt.controllerKind == 'torque' and receipt.hostApplyMonoTime.value == NOW
    assert receipt.sourceCarControlMonoTime.value == source_stamp
    with car.CarControl.from_bytes(lookup_exact_source(receipt, history)) as original:
      assert original.lateralControlObservation.version == 2
      assert original.lateralControlObservation.measurement.measuredCurvatureInvM.value == control.curvature
      assert original.lateralControlObservation.desiredTargetCurvatureInvM.value != 0.1
      assert original.lateralControlObservation.measurement.calculationMonoTime.value < source_stamp
    with pytest.raises(LookupError):
      lookup_exact_source(receipt, {NOW + 5_000_000: car.CarControl.new_message(**cc.to_dict()).to_bytes()})


@pytest.mark.parametrize('version', [1, 2])
def test_receipt_old_fields_are_identical_for_v1_and_v2_source_command(version):
  from openpilot.selfdrive.car.tests.test_actuation_receipt import receipt, source

  sm, cp, cc = source()
  old = receipt(sm, cp, cc).to_dict()
  cc.lateralControlObservation.version = version
  cc.lateralControlObservation.measurement.measuredCurvatureInvM.value = 0.012345678901234567
  cc.lateralControlObservation.desiredTargetCurvatureInvM.value = 0.001
  assert receipt(sm, cp, cc).to_dict() == old


def test_all_original_control_operations_and_their_order_are_preserved():
  path = 'openpilot/selfdrive/controls/controlsd.py'
  original = ast.parse(subprocess.check_output(['git', 'show', START + ':' + path], cwd=ROOT, text=True))
  current = ast.parse((ROOT / path).read_text())

  def method(tree):
    return next(node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef) and node.name == 'state_control')

  before, after = method(original), method(current)
  added_assignments = {'measurement_observation', 'desired_target_observation'}
  after.body = [
    node
    for node in after.body
    if not (isinstance(node, ast.Assign) and any(isinstance(target, ast.Name) and target.id in added_assignments for target in node.targets))
    and not (
      isinstance(node, ast.Expr)
      and isinstance(node.value, ast.Call)
      and isinstance(node.value.func, ast.Name)
      and node.value.func.id == 'record_measurement_and_target'
    )
  ]
  # Removing only the three permitted observation statements leaves the complete
  # pinned function identical: branches, Params calls and arithmetic order.
  assert ast.dump(before, include_attributes=False) == ast.dump(after, include_attributes=False)
