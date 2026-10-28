"""Synthetic controller observations and differential checks against the source pin."""

import importlib
import ast
import math
import subprocess
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from openpilot.cereal import car, log
from openpilot.common.pid import MultiplicativeUnwindPID
from openpilot.selfdrive.car.tests.test_actuation_receipt import source
from openpilot.selfdrive.controls.lib.latcontrol_angle import LatControlAngle
from openpilot.selfdrive.controls.lib.latcontrol_pid import LatControlPID
from openpilot.selfdrive.controls.lib.latcontrol_torque import LatControlTorque
from openpilot.selfdrive.controls.tests.test_lateral_startup_control import control_fixture

BASE = '604bc0a22733e648ec05b268da1ffaae7db52a69'
ROOT = Path(__file__).resolve().parents[4]


def controller(kind, baseline=False):
  control, _ = control_fixture()
  cp = control.CP.as_builder()
  cp.steerLimitTimer = 0.4  # Synthetic timer for differential tests, not a vehicle default.
  ci = control.CI
  if kind == 'pid':
    tune = cp.lateralTuning.init('pid')
    tune.kpBP, tune.kpV, tune.kiBP, tune.kiV = [0.0], [1.0], [0.0], [0.1]
    ci.get_steer_feedforward_function = lambda: lambda angle, speed: 0.0
  classes = {'pid': LatControlPID, 'torque': LatControlTorque, 'angle': LatControlAngle}
  cls = classes[kind]
  if baseline:
    path = 'openpilot/selfdrive/controls/lib/latcontrol_' + kind + '.py'
    code = subprocess.check_output(['git', 'show', BASE + ':' + path], cwd=ROOT, text=True)
    namespace = {'__name__': 'baseline_' + kind}
    exec(compile(code, path, 'exec'), namespace)
    cls = namespace[cls.__name__]
  return cls(cp.as_reader(), ci), control.VM


def update(controller, vm, *, active=True, desired=0.0, curvature_limited=False, override=False, limited_by_controls=False, speed=15.0):
  state = car.CarState.new_message(vEgo=speed, steeringPressed=override)
  params = log.LiveParametersData.new_message()
  cc = car.CarControl.new_message(latActive=active)
  return controller.update(active, state.as_reader(), vm, params.as_reader(), limited_by_controls, desired, cc.as_reader(), curvature_limited)


@pytest.mark.parametrize('kind', ['pid', 'torque', 'angle'])
@pytest.mark.parametrize('desired,limited', [(0.0, False), (1.0, True)])
def test_instantaneous_limit_precedes_the_unchanged_timer(kind, desired, limited):
  lac, vm = controller(kind)
  _, _, status = update(lac, vm, desired=desired, speed=2.0)
  assert status.instantaneousLimitState == ('limited' if limited else 'notLimited')
  assert not status.saturated  # One sample/low speed cannot satisfy the timer.


@pytest.mark.parametrize('kind', ['pid', 'torque', 'angle'])
@pytest.mark.parametrize('override,controls_limit', [(False, False), (True, False), (False, True)])
def test_curvature_limit_is_visible_without_timer_speed_override_or_rate_exclusions(kind, override, controls_limit):
  lac, vm = controller(kind)
  _, _, status = update(lac, vm, curvature_limited=True, override=override, limited_by_controls=controls_limit, speed=2.0)
  assert status.instantaneousLimitState == 'limited'
  assert not status.saturated


@pytest.mark.parametrize('kind', ['pid', 'torque', 'angle'])
def test_inactive_calculation_is_unavailable(kind):
  lac, vm = controller(kind)
  _, _, status = update(lac, vm, active=False, curvature_limited=True)
  assert status.instantaneousLimitState == 'unavailable'


@pytest.mark.parametrize('kind', ['pid', 'torque', 'angle'])
def test_controller_return_state_and_timer_match_pinned_baseline(kind):
  original, vm = controller(kind, baseline=True)
  current, _ = controller(kind)
  for index in range(80):
    kwargs = {
      'active': index % 6 != 0,
      'desired': (0.0, 1.0, -1.0, 0.001)[index % 4],
      'curvature_limited': index % 5 == 0,
      'override': index % 7 == 0,
      'limited_by_controls': index % 3 == 0,
      'speed': 2.0 if index % 2 else 15.0,
    }
    before = update(original, vm, **kwargs)
    after = update(current, vm, **kwargs)
    assert after[:2] == before[:2]
    old_log, new_log = before[2].to_dict(), after[2].to_dict()
    old_log.pop('instantaneousLimitState')
    new_log.pop('instantaneousLimitState')
    assert new_log == old_log
    assert current.sat_count == original.sat_count
    if hasattr(current, 'pid'):
      assert (current.pid.p, current.pid.i, current.pid.d, current.pid.f) == (original.pid.p, original.pid.i, original.pid.d, original.pid.f)


@pytest.mark.parametrize('limited,want', [(False, 'notLimited'), (True, 'limited')])
def test_same_carcontrol_carries_actual_calculation_evidence(limited, want):
  module = importlib.import_module('openpilot.selfdrive.controls.lib.lateral_observation')
  _, cp, cc = source()
  status = log.ControlsState.LateralTorqueState.new_message(active=True, instantaneousLimitState=want)
  original = cc.actuators.to_dict()
  module.record_lateral_observation(cc, cp, status, is_meb=False, meb_pid=None, curvature_limited=False)
  assert cc.lateralControlObservation.version == 2
  assert cc.lateralControlObservation.controllerKind == 'torque'
  assert cc.lateralControlObservation.instantaneousLimitState == want
  assert cc.actuators.to_dict() == original


@pytest.mark.parametrize('error,curvature_limited,want', [(0.0, False, 'notLimited'), (1.0, False, 'limited'), (0.0, True, 'limited')])
def test_meb_uses_real_pid_evidence_and_never_dummy_angle_state(error, curvature_limited, want):
  module = importlib.import_module('openpilot.selfdrive.controls.lib.lateral_observation')
  _, cp, cc = source('mebCurvature')
  pid = MultiplicativeUnwindPID(1.0, 0.1, pos_limit=0.25, neg_limit=-0.25)  # Synthetic only.
  pid.update(error)
  dummy = log.ControlsState.LateralAngleState.new_message(active=True, instantaneousLimitState='limited')
  before = (pid.control, pid.i, cc.actuators.to_dict())
  module.record_lateral_observation(cc, cp, dummy, is_meb=True, meb_pid=pid, curvature_limited=curvature_limited)
  assert cc.lateralControlObservation.controllerKind == 'mebCurvature'
  assert cc.lateralControlObservation.instantaneousLimitState == want
  assert dummy.instantaneousLimitState == 'unavailable'
  assert before == (pid.control, pid.i, cc.actuators.to_dict())


@pytest.mark.parametrize('pid', [None, NS(control=0.0), NS(control=math.nan, pos_limit=1.0, neg_limit=-1.0)])
def test_missing_or_nonfinite_meb_pid_evidence_is_unavailable(pid):
  module = importlib.import_module('openpilot.selfdrive.controls.lib.lateral_observation')
  _, cp, cc = source('mebCurvature')
  dummy = log.ControlsState.LateralAngleState.new_message(active=True, instantaneousLimitState='notLimited')
  module.record_lateral_observation(cc, cp, dummy, is_meb=True, meb_pid=pid, curvature_limited=False)
  assert cc.lateralControlObservation.instantaneousLimitState == 'unavailable'


def test_unknown_calculation_wire_enum_is_unavailable_without_actuator_mutation():
  module = importlib.import_module('openpilot.selfdrive.controls.lib.lateral_observation')
  _, cp, cc = source()
  status = log.ControlsState.LateralTorqueState.new_message(active=True)
  status.instantaneousLimitState = 65535
  before = cc.actuators.to_dict()
  module.record_lateral_observation(cc, cp, status, is_meb=False, meb_pid=None, curvature_limited=False)
  assert cc.lateralControlObservation.instantaneousLimitState == 'unavailable'
  assert cc.actuators.to_dict() == before


@pytest.mark.parametrize(
  'active,raw,curvature,want',
  [(True, True, False, 'limited'), (True, False, False, 'notLimited'), (False, True, True, 'unavailable'), (True, None, False, 'unavailable')],
)
def test_unknown_instantaneous_inputs_never_become_false(active, raw, curvature, want):
  module = importlib.import_module('openpilot.selfdrive.controls.lib.lateral_observation')
  assert module.instantaneous_limit_state(active, raw, curvature) == want


@pytest.mark.parametrize(
  'kind,field,delta,speed,want',
  [
    ('torque', 'torque', 0.0, 2.0, False),
    ('pid', 'torque', 0.02, 2.0, True),
    ('angle', 'steeringAngleDeg', 3.0, 2.0, False),
    ('angle', 'steeringAngleDeg', 4.0, 2.0, True),
    ('mebCurvature', 'curvature', 0.0, 4.0, False),
    ('mebCurvature', 'curvature', 0.02, 4.0, True),
    ('torque', 'torque', math.inf, 2.0, True),
    ('torque', 'torque', math.nan, 2.0, False),
  ],
)
def test_existing_output_comparison_keeps_arithmetic_even_for_nonfinite_old_inputs(kind, field, delta, speed, want):
  from openpilot.selfdrive.controls.lib.lateral_observation import lateral_output_limited

  requested, returned = car.CarControl.Actuators.new_message(), car.CarControl.Actuators.new_message()
  setattr(requested, field, delta)
  assert lateral_output_limited(kind, requested, returned, speed) == want


@pytest.mark.parametrize('meb', [False, True])
def test_complete_state_control_attaches_the_same_calculation_to_carcontrol(meb):
  control, step = control_fixture()
  control.lateral_startup.ready = True
  if meb:
    from opendbc.car.volkswagen.values import VolkswagenFlags

    cp = control.CP.as_builder()
    cp.brand, cp.flags, cp.steerControlType = 'volkswagen', int(VolkswagenFlags.MEB), 'angle'
    control.CP = cp.as_reader()
    control.is_vw_meb = True
    control.LaC = LatControlAngle(control.CP, control.CI)
    control.meb_curvature_pid = MultiplicativeUnwindPID(1.0, 0.1, pos_limit=0.25, neg_limit=-0.25)
    control.calibrated_pose = None
    control.sm['carState'].steeringAngleDeg = 0.0
    control.sm['liveParameters'].angleOffsetDeg = 0.0
  cc, status = step()
  assert cc.latActive
  assert cc.lateralControlObservation.version == 2
  assert cc.lateralControlObservation.controllerKind == ('mebCurvature' if meb else 'torque')
  if meb:
    assert cc.lateralControlObservation.instantaneousLimitState == 'notLimited'
    assert status.instantaneousLimitState == 'unavailable'
  else:
    assert cc.lateralControlObservation.instantaneousLimitState == status.instantaneousLimitState


@pytest.mark.parametrize('kind', ['torque', 'angle', 'mebCurvature'])
@pytest.mark.parametrize('delta', [0.0, 0.02, 4.0, math.nan, math.inf])
@pytest.mark.parametrize('active', [False, True])
def test_complete_existing_output_comparison_block_matches_pinned_base(kind, delta, active):
  from openpilot.selfdrive.controls.lib.lateral_observation import lateral_output_limited

  path = 'openpilot/selfdrive/controls/controlsd.py'
  outcomes = []
  for baseline in (True, False):
    code = subprocess.check_output(['git', 'show', BASE + ':' + path], cwd=ROOT, text=True) if baseline else (ROOT / path).read_text()
    tree = ast.parse(code)
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'Controls')
    publish = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == 'publish')
    block = next(n for n in publish.body if isinstance(n, ast.If) and ast.unparse(n.test) == "self.sm['selfdriveState'].active")
    requested, returned = car.CarControl.new_message(), car.CarControl.Actuators.new_message()
    field = {'torque': 'torque', 'angle': 'steeringAngleDeg', 'mebCurvature': 'curvature'}[kind]
    setattr(requested.actuators, field, delta)
    cp = car.CarParams.new_message(steerControlType='angle' if kind == 'angle' else 'torque')
    owner = NS(
      CP=cp, is_vw_meb=kind == 'mebCurvature', steer_limited_by_safety=True, sm={'selfdriveState': NS(active=active), 'carOutput': NS(actuatorsOutput=returned)}
    )
    namespace = {
      'self': owner,
      'CC': requested,
      'CS': NS(vEgo=4.0),
      'car': car,
      'LAT_CURVATURE_SATURATION_ACCEL': 0.1,
      'STEER_ANGLE_SATURATION_THRESHOLD': 3.0,
      'lateral_output_limited': lateral_output_limited,
    }
    exec(compile(ast.Module(body=[block], type_ignores=[]), path, 'exec'), namespace)
    outcomes.append(owner.steer_limited_by_safety)
  assert outcomes[1] == outcomes[0]
