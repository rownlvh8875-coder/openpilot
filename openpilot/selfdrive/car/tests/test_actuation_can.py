"""Real offline CarInterface/CarController/CAN packing with synthetic inputs only."""

import importlib
import subprocess
import sys
from pathlib import Path

import pytest

from openpilot.cereal import car, log
from openpilot.selfdrive.car.actuation_receipt import capture_actuation_receipt, complete_actuation_receipt
from openpilot.selfdrive.car.tests.observability_support import SyntheticReadOnlySettings
from openpilot.selfdrive.car.tests.test_actuation_receipt import NOW, Messages
from openpilot.selfdrive.controls.lib.lateral_observation import controller_kind

ROOT = Path(__file__).resolve().parents[4]
BASE = '604bc0a22733e648ec05b268da1ffaae7db52a69'


@pytest.mark.parametrize('brand,model', [('honda', 'HONDA_CIVIC'), ('toyota', 'TOYOTA_RAV4'), ('nissan', 'NISSAN_LEAF'), ('volkswagen', 'VOLKSWAGEN_ID4_MK1')])
@pytest.mark.parametrize('observation_version', [1, 2])
def test_real_controller_outputs_and_can_bytes_count_order_match_base_with_receipts(brand, model, observation_version, monkeypatch, request):
  # Zero/false here deliberately disables optional settings in a synthetic test;
  # it is not a source-signal mapping, vehicle tuning value or production default.
  operations = []

  class Settings:
    def get_bool(self, key):
      assert key in {'NNFF', 'NNFFLite', 'DisableMinSteerSpeed'}, key
      operations.append(('bool', key))
      return False

    def get_int(self, key):
      assert key in {'LateralTorqueCustom', 'CustomSteerMax', 'CustomSteerDeltaUp', 'CustomSteerDeltaDown', 'PCMMode'}, key
      operations.append(('int', key))
      return 0

    def get_float(self, key):
      assert key == 'SteerActuatorDelay', key
      operations.append(('float', key))
      return 0.0

    def put_int(self, key, value):
      # Existing interface construction writes this preference. Observe it in
      # memory only; disk Params is never constructed or written in this test.
      assert (key, value) == ('LongitudinalPersonalityMax', 3)
      operations.append(('put_int', key, value))

  import openpilot.common.params as settings_module

  def restore_new_module_aliases():
    # Modules first imported during this test also capture the temporary alias.
    for module in tuple(sys.modules.values()):
      if module is not None and getattr(module, '__name__', '').startswith(('opendbc.', 'openpilot.')):
        if getattr(module, 'Params', None) is Settings:
          module.Params = SyntheticReadOnlySettings

  request.addfinalizer(restore_new_module_aliases)
  monkeypatch.setattr(settings_module, 'Params', Settings)
  for module in tuple(sys.modules.values()):
    if module is not None and getattr(module, '__name__', '').startswith(('opendbc.', 'openpilot.')):
      if getattr(module, 'Params', None) is SyntheticReadOnlySettings:
        monkeypatch.setattr(module, 'Params', Settings)
  interface_module = importlib.import_module(f'opendbc.car.{brand}.interface')
  controller_module = importlib.import_module(f'opendbc.car.{brand}.carcontroller')
  values = importlib.import_module(f'opendbc.car.{brand}.values')
  interface = interface_module.CarInterface
  outcomes = []
  for observed in (False, True):
    operations.clear()
    cp = interface.get_non_essential_params(getattr(values.CAR, model))
    ci = interface(cp)
    if not observed:
      path = f'opendbc_repo/opendbc/car/{brand}/carcontroller.py'
      original = subprocess.check_output(['git', 'show', BASE + ':' + path], cwd=ROOT, text=True)
      namespace = {'__name__': 'baseline_' + brand}
      exec(compile(original, path, 'exec'), namespace)
      # Keep the real, initialized state/packers/configuration and execute the
      # pinned controller's update method on it. No algorithm or packer is mocked.
      ci.CC.update = namespace['CarController'].update.__get__(ci.CC, controller_module.CarController)
    sm = Messages()
    sm['pandaStates'] = [
      log.PandaState.new_message(pandaType='blackPanda', safetyModel=c.safetyModel, safetyParam=c.safetyParam, controlsAllowed=True) for c in cp.safetyConfigs
    ]
    trace = []
    for index in range(40):
      ci.update([])  # Empty synthetic input, never a CAN socket or real log.
      state = car.CarState.new_message(**ci.CS.out.to_dict())
      state.vEgo, state.standstill = 15.0, False
      ci.CS.out = state.as_reader()
      cc = car.CarControl.new_message(enabled=True, latActive=index % 5 != 0)
      cc.actuators.torque = (0.25, -0.25)[index % 2]
      cc.actuators.steeringAngleDeg = (5.0, -5.0)[index % 2]
      cc.actuators.curvature = (0.001, -0.001)[index % 2]
      now = NOW + index * 10_000_000
      pending = None
      if observed:
        cc.lateralControlObservation.version = observation_version
        cc.lateralControlObservation.controllerKind = controller_kind(cp)
        cc.lateralControlObservation.instantaneousLimitState = 'notLimited'
        if observation_version == 2:
          # Synthetic metadata exercises the real packers' unchanged argument path.
          cc.lateralControlObservation.measurement.version = 1
          cc.lateralControlObservation.measurement.measuredCurvatureInvM.value = 0.0
          cc.lateralControlObservation.desiredTargetState = 'available'
          cc.lateralControlObservation.desiredTargetCurvatureInvM.value = cc.actuators.curvature
        sm.logMonoTime = {'carControl': now - 1_000_000, 'pandaStates': now - 20_000_000}
        sm.recv_time = dict.fromkeys(sm.logMonoTime, (now - 500_000) / 1e9)
        pending = capture_actuation_receipt(sm, cp, cc.as_reader(), now, False, safety_now_ns=now)
      returned, frames = ci.apply(cc.as_reader(), now)
      trace.append((returned.to_dict(), [(int(address), bytes(payload), int(bus)) for address, payload, bus in frames]))
      if observed:
        receipt = complete_actuation_receipt(pending, returned, ci.CS.out.vEgo)
        assert receipt.controllerOutputReturned == 'returned'
        assert receipt.sourceCarControlMonoTime.value == now - 1_000_000
        assert receipt.controllerOutputActuators.to_dict() == returned.to_dict()
    outcomes.append((trace, tuple(operations)))
  assert outcomes[1] == outcomes[0]
  assert sum(len(frames) for _, frames in outcomes[0][0]) > 0


def test_offline_fixture_generation_does_not_replace_tracked_dbc_sources():
  changed = subprocess.check_output(['git', 'diff', '--name-only', 'HEAD', '--', 'opendbc_repo/opendbc/dbc'], cwd=ROOT, text=True)
  assert changed.splitlines() == []
