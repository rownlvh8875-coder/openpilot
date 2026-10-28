"""Offline test plugin: isolate settings IO; never replace control algorithms."""

import sys
from types import ModuleType


class SyntheticReadOnlySettings:
  """Only reads used by the synthetic torque fixture; no device Params access."""

  def get_int(self, key):
    assert key == 'LateralTorqueCustom', key
    return 0

  def get_float(self, key):
    assert key == 'SteerActuatorDelay', key
    return 0.0


def pytest_configure(config):
  module = ModuleType('openpilot.common.params')
  module.Params = SyntheticReadOnlySettings
  sys.modules[module.__name__] = module
