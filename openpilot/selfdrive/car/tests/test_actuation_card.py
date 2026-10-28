"""Execute complete card methods, isolating only hardware/IPC/settings boundaries."""

import ast
import struct
import subprocess
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from openpilot.cereal import car, log
from openpilot.selfdrive.car.actuation_receipt import capture_actuation_receipt, complete_actuation_receipt
from openpilot.selfdrive.car.tests.test_actuation_receipt import NOW, source
from openpilot.selfdrive.controls.lib.lateral_readiness import LateralStartupGate

BASE = '604bc0a22733e648ec05b268da1ffaae7db52a69'
ROOT = Path(__file__).resolve().parents[4]


def card_methods(baseline=False):
  path = ROOT / 'openpilot/selfdrive/car/card.py'
  text = subprocess.check_output(['git', 'show', BASE + ':openpilot/selfdrive/car/card.py'], cwd=ROOT, text=True) if baseline else path.read_text()
  tree = ast.parse(text)
  cls = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == 'Car')
  nodes = [node for node in cls.body if isinstance(node, ast.FunctionDef) and node.name in ('controls_update', 'state_publish', 'step')]
  clock = NS(now=NOW)

  def encode(frames, msgtype, valid):
    event = log.Event.new_message(logMonoTime=clock.now, valid=valid)
    messages = event.init(msgtype, len(frames))
    for item, (address, payload, bus) in zip(messages, frames, strict=True):
      item.address, item.dat, item.src = address, payload, bus
    return event.to_bytes()

  ns = {
    'car': car,
    'REPLAY': False,
    'DT_CTRL': 0.01,
    'messaging': NS(new_message=lambda topic: new_message(topic, clock.now)),
    'time': NS(monotonic_ns=lambda: clock.now, monotonic=lambda: clock.now / 1e9, thread_time=lambda: 0.0),
    'capture_actuation_receipt': capture_actuation_receipt,
    'safety_observation_clock_ns': lambda: clock.now,
    'complete_actuation_receipt': complete_actuation_receipt,
    'can_list_to_can_capnp': encode,
    'CAN_BACKEND': 'synthetic',
    'EventName': log.OnroadEvent.EventName,
    'XIAOGE_SOURCE_HEARTBEAT_NS': 1_000_000_000,
  }
  exec(compile(ast.Module(body=nodes, type_ignores=[]), str(path), 'exec'), ns)
  return ns, clock


def new_message(topic, stamp):
  event = log.Event.new_message(logMonoTime=stamp, valid=False)
  event.init(topic)
  return event


def fixture(gate='ready', baseline=False):
  ns, clock = card_methods(baseline)
  sm, cp, cc = source()
  cs = car.CarState.new_message(canValid=True, vEgo=4.0, steeringAngleDeg=2.5)
  sm.update(carControl=cc.as_reader(), onroadEvents=[], modelV2=NS(identity='model'), radarState=NS(identity='radar'))
  sm.frame = 1
  sm.seen['onroadEvents'] = True
  sm.seen['modelV2'] = False  # Force the real startup gate to wait in the readiness case.
  sm.valid['modelV2'] = sm.alive['modelV2'] = sm.valid['radarState'] = sm.alive['radarState'] = True
  sm.all_alive = lambda topics: all(sm.alive.get(topic, False) for topic in topics)
  sm.all_checks = lambda topics: all(all(getattr(sm, name).get(topic, False) for name in ('seen', 'valid', 'alive', 'freq_ok')) for topic in topics)
  calls, publications, order = [], [], []

  def apply(command, now, model, radar):
    order.append('apply')
    calls.append((command.to_dict(), now, model, radar))
    returned = command.actuators.as_builder()
    returned.torque *= 0.5
    # Synthetic payload depends on actual apply arguments; no vehicle CAN device.
    frames = [(0x123, struct.pack('<f?', command.actuators.torque, command.latActive), 0), (0x124, struct.pack('<f', command.actuators.curvature), 0)]
    return returned.as_reader(), frames

  def send(topic, message):
    order.append('send:' + topic)
    publications.append((topic, message if isinstance(message, bytes) else message.to_bytes()))

  stages = ('apply', 'sendcan', 'total')
  card = NS(
    CP=cp,
    sm=sm,
    params=NS(get_bool=lambda key: gate == 'reboot'),
    spas_signals=NS(enabled=False),
    tesla_signals=NS(configured=False),
    initialized_prev=True,
    lateral_startup=LateralStartupGate(ready=gate != 'readiness'),
    CI=NS(apply=apply),
    pm=NS(send=send),
    last_actuators_output=car.CarControl.Actuators.new_message(),
    last_actuation_receipt=None,
    card_diag_recv_ns=NOW,
    card_diag_stage_names=stages,
    card_diag_stage_current=dict.fromkeys(stages, 0),
    card_diag_stage_sum_us=dict.fromkeys(stages, 0),
    card_diag_stage_max_us=dict.fromkeys(stages, 0),
    card_diag_process_max_us=0,
    card_diag_slow_process=0,
    card_diag_frames=0,
    xiaoge_blindspot_sources=(False,) * 4,
    xiaoge_blindspot_sources_published=(False,) * 4,
    xiaoge_blindspot_sources_published_ns=NOW,
    can_rcv_cum_timeout_counter=0,
    rk=NS(remaining=0),
    cruise_main_toggle=NS(update=lambda *args: False),
    runtime_diagnostics=NS(record=lambda **kwargs: None),
  )
  card.state_update = lambda: cs
  card.state_publish = lambda state: ns['state_publish'](card, state)
  card.controls_update = lambda state, command: ns['controls_update'](card, state, command)
  if gate == 'inactive':
    cc.latActive = False
  if gate == 'dead':
    sm.alive['carControl'] = False
  if gate == 'invalid':
    sm.valid['carControl'] = False
  return ns, clock, card, cs, cc, calls, publications, order


@pytest.mark.parametrize(
  'gate,activity', [('ready', 'active'), ('inactive', 'inactive'), ('reboot', 'inactive'), ('readiness', 'inactive'), ('invalid', 'unavailable')]
)
def test_receipt_records_the_final_command_actually_passed_to_apply(gate, activity):
  _, _, card, cs, cc, calls, _, _ = fixture(gate)
  card.controls_update(cs, cc.as_reader())
  assert len(calls) == 1
  receipt = card.last_actuation_receipt
  assert receipt is not None
  assert receipt.hostLateralActivityState == activity
  assert receipt.sourceCarControlMonoTime.value == 9_990_000_000
  assert receipt.finalRequestedActuators.to_dict() == calls[0][0]['actuators']
  assert receipt.controllerOutputActuators.to_dict() == card.last_actuators_output.to_dict()
  assert calls[0][0]['latActive'] == (gate not in ('inactive', 'reboot', 'readiness'))
  assert cc.latActive == (gate != 'inactive')  # Existing suppression must not mutate the subscription.


def test_skipped_dead_command_clears_previous_receipt_without_applying():
  _, _, card, cs, cc, calls, _, _ = fixture()
  card.controls_update(cs, cc.as_reader())
  assert card.last_actuation_receipt is not None
  card.sm.alive['carControl'] = False
  card.controls_update(cs, cc.as_reader())
  assert len(calls) == 1
  assert card.last_actuation_receipt is None


def test_publication_keeps_previous_cohort_and_existing_loop_order():
  ns, clock, card, cs, cc, calls, publications, order = fixture()
  ns['step'](card)
  old = [data for topic, data in publications if topic == 'carOutput'][0]
  with log.Event.from_bytes(old) as event:
    assert event.carOutput.actuationReceipt.receiptVersion == 0
  clock.now += 10_000_000
  card.sm.logMonoTime['carControl'] = NOW + 5_000_000
  cc.actuators.torque = -0.25
  ns['step'](card)
  outputs = [data for topic, data in publications if topic == 'carOutput']
  with log.Event.from_bytes(outputs[-1]) as event:
    receipt = event.carOutput.actuationReceipt
    assert receipt.sourceCarControlMonoTime.value == 9_990_000_000
    assert receipt.hostApplyMonoTime.value == NOW
    assert receipt.finalRequestedActuators.torque == 0.25
    assert receipt.controllerOutputActuators.torque == 0.125
    assert event.carOutput.actuatorsOutput.torque == 0.125
  assert order == ['send:carOutput', 'send:carState', 'apply', 'send:sendcan'] * 2
  assert len(calls) == 2


@pytest.mark.parametrize('gate', ['ready', 'inactive', 'reboot', 'readiness', 'dead', 'invalid'])
def test_apply_arguments_returned_outputs_and_synthetic_can_are_identical_to_pinned_base(gate):
  outcomes = []
  for baseline in (True, False):
    _, _, card, cs, cc, calls, publications, order = fixture(gate, baseline)
    card.controls_update(cs, cc.as_reader())
    outcomes.append((calls, card.last_actuators_output.to_dict(), publications, order, cc.to_dict()))
  assert outcomes[0] == outcomes[1]


def test_receipt_failure_leaves_original_apply_and_can_path_running(monkeypatch):
  ns, _, card, cs, cc, calls, publications, _ = fixture()
  # The production boundary itself handles malformed observability inputs.
  card.sm.ignore_valid = None
  card.controls_update(cs, cc.as_reader())
  assert len(calls) == 1
  assert [topic for topic, _ in publications] == ['sendcan']
  assert card.last_actuation_receipt is not None
  assert card.last_actuation_receipt.hostLateralActivityState == 'unavailable'


def test_malformed_stored_receipt_cannot_break_existing_publication():
  _, _, card, cs, _, _, publications, _ = fixture()
  card.last_actuation_receipt = 'synthetic malformed receipt'
  card.state_publish(cs)
  assert [topic for topic, _ in publications] == ['carOutput', 'carState']
  with log.Event.from_bytes(publications[0][1]) as event:
    assert event.carOutput.actuationReceipt.receiptVersion == 0
