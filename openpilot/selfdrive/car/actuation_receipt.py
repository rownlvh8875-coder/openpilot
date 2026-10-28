"""Memory-only, fail-unavailable evidence captured around the unchanged apply call."""

import math
import time

from openpilot.cereal import car, log
from openpilot.cereal.services import SERVICE_LIST
from openpilot.selfdrive.controls.lib.lateral_observation import controller_kind, output_limit_state


def _usable_timestamp(stamp, now_ns):
  return type(stamp) is int and 0 < stamp <= now_ns


def safety_observation_clock_ns():
  """Observe the C++ publication clock; unsupported clocks stay unavailable."""
  try:
    return time.clock_gettime_ns(time.CLOCK_BOOTTIME)
  except Exception:
    return None


def _healthy(sm, topic, now_ns, *, publication_now_ns=None):
  # Same ten-period envelope used for SubMaster.alive, applied to both source
  # publication and local receive observations. This is telemetry software policy.
  for name in ('ignore_alive', 'ignore_valid', 'ignore_average_freq'):
    if topic in getattr(sm, name):
      return False
  if not all(getattr(sm, name).get(topic) is True for name in ('seen', 'alive', 'valid', 'freq_ok')):
    return False
  stamp = sm.logMonoTime.get(topic)
  received = sm.recv_time.get(topic)
  publication_now_ns = now_ns if publication_now_ns is None else publication_now_ns
  if not _usable_timestamp(stamp, publication_now_ns) or isinstance(received, bool) or not math.isfinite(received):
    return False
  max_age_s = 10.0 / SERVICE_LIST[topic].frequency
  return 0 <= publication_now_ns - stamp <= max_age_s * 1e9 and 0 <= now_ns / 1e9 - received <= max_age_s


def _finite_actuators(value):
  return all(math.isfinite(number) for key, number in value.to_dict().items() if key != 'longControlState')


def _safety_permission(sm, CP, now_ns, replay, safety_now_ns):
  if not _usable_timestamp(safety_now_ns, safety_now_ns):
    return 'unavailable'
  if replay is not False or sm.simulation is not False or not _healthy(sm, 'pandaStates', now_ns, publication_now_ns=safety_now_ns):
    return 'unavailable'
  pandas = sm['pandaStates']
  configs = CP.safetyConfigs
  if not pandas or len(pandas) != len(configs):
    return 'unavailable'
  for state, config in zip(pandas, configs, strict=True):
    if (
      state.pandaType == 'unknown'
      or str(state.pandaType) not in log.PandaState.PandaType.schema.enumerants
      or str(state.safetyModel) not in car.CarParams.SafetyModel.schema.enumerants
      or state.safetyModel != config.safetyModel
      or state.safetyParam != config.safetyParam
      or state.faultStatus != 'none'
      or len(state.faults)
      or state.heartbeatLost
      or state.safetyRxChecksInvalid
    ):
      return 'unavailable'
  allowed = [state.controlsAllowed for state in pandas]
  if all(allowed):
    return 'allowed'
  return 'disallowed' if not any(allowed) else 'unavailable'


def capture_actuation_receipt(sm, CP, CC, host_apply_ns, replay, *, safety_now_ns=None):
  """Copy the final CC before apply. Never gate, mutate or replace its arguments."""
  unavailable = car.ActuationReceipt.new_message()
  try:
    if type(host_apply_ns) is not int or host_apply_ns <= 0:
      return unavailable
    receipt = car.ActuationReceipt.new_message(receiptVersion=1)
    receipt.hostApplyMonoTime.value = host_apply_ns
    receipt.hostApplyMonoTime.clockDomain = 'monotonic'
    if _usable_timestamp(safety_now_ns, safety_now_ns):
      receipt.safetyObservationMonoTime.value = safety_now_ns
      receipt.safetyObservationMonoTime.clockDomain = 'bootTime'
    stamp = sm.logMonoTime.get('carControl')
    if _usable_timestamp(stamp, host_apply_ns):
      receipt.sourceCarControlMonoTime.value = stamp
      receipt.sourceCarControlMonoTime.clockDomain = 'monotonic'
    finite_request = _finite_actuators(CC.actuators)
    if finite_request:
      receipt.finalRequestedActuators = CC.actuators
    if finite_request and _healthy(sm, 'carControl', host_apply_ns):
      receipt.hostLateralActivityState = 'active' if CC.latActive else 'inactive'
      observed = CC.lateralControlObservation
      kind = controller_kind(CP)
      if observed.version == 1 and kind != 'unknown' and observed.controllerKind == kind:
        receipt.controllerKind = kind
        state = str(observed.instantaneousLimitState)
        if state in ('unavailable', 'notLimited', 'limited'):
          receipt.instantaneousControllerLimitState = state
    receipt.safetyPermissionState = _safety_permission(sm, CP, host_apply_ns, replay, safety_now_ns)
    if receipt.safetyPermissionState != 'unavailable':
      receipt.safetyStateMonoTime.value = sm.logMonoTime['pandaStates']
      receipt.safetyStateMonoTime.clockDomain = 'bootTime'
    return receipt
  except Exception:
    # Only this optional telemetry boundary is guarded. CI.apply exceptions are
    # neither intercepted nor hidden by observability.
    return unavailable


def complete_actuation_receipt(receipt, returned, speed_mps):
  """Bind the output from that same apply; return an isolated read-only value."""
  try:
    if receipt.receiptVersion != 1:
      return car.ActuationReceipt.new_message().as_reader()
    receipt.controllerOutputReturned = True
    if _finite_actuators(returned):
      receipt.controllerOutputActuators = returned
      if receipt._has('finalRequestedActuators'):
        receipt.outputLimitState = output_limit_state(str(receipt.controllerKind), receipt.finalRequestedActuators, returned, speed_mps)
    return receipt.as_reader()
  except Exception:
    return car.ActuationReceipt.new_message().as_reader()
