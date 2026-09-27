from types import SimpleNamespace

import pytest

from openpilot.cereal import car
from openpilot.selfdrive.controls.lib import latcontrol_torque


class DictParams:
  def __init__(self, mode):
    self.values = {
      "LateralTorqueCustom": mode,
      "LateralTorqueAccelFactor": 3200,
      "LateralTorqueFriction": 220,
      "LateralTorqueKpV": 90,
      "LateralTorqueKiV": 30,
      "LateralTorqueKf": 80,
      "LateralTorqueKd": 25,
    }

  def get_int(self, key):
    return int(self.values[key])

  def get_float(self, key):
    return float(self.values[key])


def make_controller(monkeypatch, mode=0):
  cp = car.CarParams.new_message()
  cp.steerLimitTimer = 0.4
  cp.lateralTuning.init("torque")
  tuning = cp.lateralTuning.torque
  tuning.kp, tuning.ki, tuning.kf = 0.7, 0.2, 0.6
  tuning.latAccelFactor, tuning.latAccelOffset, tuning.friction = 2.5, 0.07, 0.12
  params = DictParams(mode)
  monkeypatch.setattr(latcontrol_torque, "Params", lambda: params)
  # This fixture exercises settings polling while inactive; no vehicle torque
  # callback is needed. The actual controller, Cap'n Proto messages and PID run.
  ci = SimpleNamespace(use_nnff=False, use_nnff_lite=False, torque_from_lateral_accel=lambda: None)
  return latcontrol_torque.LatControlTorque(cp.as_reader(), ci), params, cp


def advance(control, frames=10):
  cs = car.CarState.new_message()
  for _ in range(frames):
    output, _, state = control.update(False, cs, None, None, False, 0.0, None, False)
    assert output == 0.0
    assert not state.active


def torque_values(control):
  return (control.torque_params.latAccelFactor, control.torque_params.latAccelOffset, control.torque_params.friction)


def gains(control):
  return (control.pid.k_p, control.pid.k_i, control.pid.k_f, control.pid.k_d)


@pytest.mark.parametrize("mode", [1, 2])
def test_disabling_custom_restores_vehicle_torque_values(monkeypatch, mode):
  # UI mode 1 and legacy positive values must both restore defaults.
  control, params, cp = make_controller(monkeypatch)
  original_cp = cp.to_dict()
  control.update_live_torque_params(2.8, 0.4, 0.15)
  params.values["LateralTorqueCustom"] = mode
  advance(control)
  assert torque_values(control) == pytest.approx((3.2, 0.07, 0.22))

  params.values["LateralTorqueCustom"] = 0
  advance(control)
  assert torque_values(control) == pytest.approx((2.5, 0.07, 0.12))
  assert cp.to_dict() == original_cp


@pytest.mark.parametrize("mode", [1, 2])
def test_disabling_custom_restores_pid_response_and_preserves_integral(monkeypatch, mode):
  control, params, _ = make_controller(monkeypatch, mode)
  advance(control)
  assert gains(control) == pytest.approx((0.9, 0.3, 0.8, 0.25))
  control.pid.i = 0.05

  params.values["LateralTorqueCustom"] = 0
  advance(control)
  assert gains(control) == pytest.approx((0.7, 0.2, 0.6, 0.0))
  assert control.pid.i == pytest.approx(0.05)
  # Hand-derived response: P = .2*.7, F = .1*.6, I = .05, D = 0.
  output = control.pid.update(0.2, error_rate=0.4, feedforward=0.1, freeze_integrator=True)
  assert output == pytest.approx(0.25)


def test_live_values_resume_after_off_and_survive_later_polls(monkeypatch):
  control, params, _ = make_controller(monkeypatch, 1)
  advance(control)
  params.values["LateralTorqueCustom"] = 0
  advance(control, 9)
  # controlsd supplies live parameters before the controller polls settings.
  control.update_live_torque_params(2.8, 0.4, 0.15)
  assert torque_values(control) == pytest.approx((3.2, 0.07, 0.22))
  advance(control, 1)
  assert torque_values(control) == pytest.approx((2.5, 0.07, 0.12))

  control.update_live_torque_params(2.8, 0.4, 0.15)
  advance(control, 20)
  assert torque_values(control) == pytest.approx((2.8, 0.4, 0.15))
  assert gains(control) == pytest.approx((0.7, 0.2, 0.6, 0.0))


def test_custom_polling_cadence_and_repeated_toggle(monkeypatch):
  control, params, _ = make_controller(monkeypatch)
  for _ in range(2):
    params.values["LateralTorqueCustom"] = 1
    advance(control, 9)
    assert gains(control) == pytest.approx((0.7, 0.2, 0.6, 0.0))
    advance(control, 1)
    assert gains(control) == pytest.approx((0.9, 0.3, 0.8, 0.25))
    control.update_live_torque_params(2.8, 0.4, 0.15)
    assert torque_values(control) == pytest.approx((3.2, 0.07, 0.22))
    params.values["LateralTorqueCustom"] = 0
    advance(control)
    assert gains(control) == pytest.approx((0.7, 0.2, 0.6, 0.0))


def test_default_mode_keeps_live_values(monkeypatch):
  control, _, _ = make_controller(monkeypatch)
  control.update_live_torque_params(2.8, 0.4, 0.15)
  advance(control, 20)
  assert torque_values(control) == pytest.approx((2.8, 0.4, 0.15))
  assert gains(control) == pytest.approx((0.7, 0.2, 0.6, 0.0))
