"""Append-only wire compatibility, including absent receipts in old messages."""

import json
from pathlib import Path

from openpilot.cereal import car, log

BASELINE = json.loads((Path(__file__).parent / 'fixtures/actuation_schema_baseline.json').read_text())


def test_old_output_decodes_without_fabricating_available_evidence():
  with car.CarOutput.from_bytes(bytes.fromhex(BASELINE['legacy_output_hex'])) as output:
    assert output.actuatorsOutput.torque == 0.25
    receipt = output.actuationReceipt
    assert receipt.receiptVersion == 0
    assert receipt.hostLateralActivityState == 'unavailable'
    assert receipt.safetyPermissionState == 'unavailable'
    assert receipt.instantaneousControllerLimitState == 'unavailable'
    assert receipt.outputLimitState == 'unavailable'
    assert receipt.controllerKind == 'unknown'
    assert receipt.sourceCarControlMonoTime.which() == 'unavailable'
    assert receipt.safetyStateMonoTime.which() == 'unavailable'
    assert receipt.hostApplyMonoTime.which() == 'unavailable'
    assert not receipt._has('finalRequestedActuators')
    assert not receipt._has('controllerOutputActuators')
    assert not receipt.controllerOutputReturned


def test_old_field_ordinals_and_types_are_unchanged():
  structs = {
    'CarControl': car.CarControl,
    'CarOutput': car.CarOutput,
    'LateralPIDState': log.ControlsState.LateralPIDState,
    'LateralTorqueState': log.ControlsState.LateralTorqueState,
    'LateralAngleState': log.ControlsState.LateralAngleState,
  }
  for name, expected in BASELINE['fields'].items():
    fields = structs[name].schema.fields
    for field, metadata in expected.items():
      assert fields[field].proto.ordinal.explicit == metadata['ordinal']
      assert fields[field].proto.slot.type.which() == metadata['type']
    assert len({f.proto.ordinal.explicit for f in fields.values()}) == len(fields)
  assert car.CarControl.schema.fields['lateralControlObservation'].proto.ordinal.explicit == 18
  assert car.CarOutput.schema.fields['actuationReceipt'].proto.ordinal.explicit == 1


def test_default_calculation_states_are_unavailable_not_false():
  for schema in (log.ControlsState.LateralPIDState, log.ControlsState.LateralTorqueState, log.ControlsState.LateralAngleState):
    assert schema.new_message().instantaneousLimitState == 'unavailable'
  observation = car.CarControl.new_message().lateralControlObservation
  assert observation.version == 0
  assert observation.controllerKind == 'unknown'
  assert observation.instantaneousLimitState == 'unavailable'


def test_legacy_reader_ignores_appended_receipt(tmp_path):
  # A legacy root with the original single pointer slot. The actual actuator
  # layout is separately locked to the pinned baseline above.
  import capnp

  path = tmp_path / 'legacy.capnp'
  path.write_text('@0xe8b841213fd0bd71; using C = import "/car.capnp"; struct LegacyOutput { actuatorsOutput @0 :C.CarControl.Actuators; }')
  parser = capnp.SchemaParser()
  imports = [str(Path(__file__).resolve().parents[4] / 'opendbc_repo/opendbc/car')]
  legacy = parser.load(str(path), imports=imports).LegacyOutput
  output = car.CarOutput.new_message()
  output.actuatorsOutput.torque = 0.25
  output.actuationReceipt.receiptVersion = 1
  with legacy.from_bytes(output.to_bytes()) as decoded:
    assert decoded.actuatorsOutput.torque == 0.25
    assert not hasattr(decoded, 'actuationReceipt')
