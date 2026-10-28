"""Observation-v2 wire compatibility and explicit optional-number defaults."""

from pathlib import Path

import pytest

from openpilot.cereal import car


def test_optional_numeric_distinguishes_unavailable_from_genuine_zero():
  value = car.ObservationFloat64.new_message()
  assert value.which() == 'unavailable'
  value.value = 0.0
  assert value.which() == 'value' and value.value == 0.0


def test_v2_schema_appends_fields_without_reusing_v1_ordinals():
  fields = car.LateralControlObservation.schema.fields
  expected = {'version': 0, 'controllerKind': 1, 'instantaneousLimitState': 2, 'measurement': 3, 'desiredTargetState': 4, 'desiredTargetCurvatureInvM': 5}
  assert {name: field.proto.ordinal.explicit for name, field in fields.items()} == expected
  measurement = car.LateralMeasurementObservation.schema.fields
  assert [field.proto.ordinal.explicit for field in measurement.values()] == list(range(15))


@pytest.mark.parametrize('version', [0, 1])
def test_legacy_observation_bytes_decode_new_evidence_as_unavailable(tmp_path, version):
  import capnp

  path = tmp_path / 'legacy_observation.capnp'
  path.write_text('''@0xeaa21c7e57acfb81;
using C = import "/car.capnp";
struct LegacyObservation {
  version @0 :UInt16;
  controllerKind @1 :C.LateralControlObservation.ControllerKind;
  instantaneousLimitState @2 :C.LateralControlObservation.LimitState;
}''')
  parser = capnp.SchemaParser()
  imports = [str(Path(__file__).resolve().parents[4] / 'opendbc_repo/opendbc/car')]
  legacy = parser.load(str(path), imports=imports).LegacyObservation
  data = legacy.new_message(version=version, controllerKind='torque', instantaneousLimitState='notLimited').to_bytes()
  with car.LateralControlObservation.from_bytes(data) as observed:
    assert observed.version == version
    assert observed.controllerKind == 'torque'
    assert observed.measurement.version == 0
    assert observed.measurement.measurementMode == 'unavailable'
    assert observed.measurement.validityState == 'unavailable'
    assert observed.measurement.measuredCurvatureInvM.which() == 'unavailable'
    assert observed.desiredTargetState == 'unavailable'
    assert observed.desiredTargetCurvatureInvM.which() == 'unavailable'


def test_float64_preserves_the_actual_python_calculation_without_rounding():
  value = 0.012345678901234567
  observed = car.LateralMeasurementObservation.new_message(version=1)
  observed.measuredCurvatureInvM.value = value
  with car.LateralMeasurementObservation.from_bytes(observed.to_bytes()) as decoded:
    assert decoded.measuredCurvatureInvM.value == value


def test_measurement_default_health_and_timestamps_are_unavailable():
  observed = car.LateralMeasurementObservation.new_message()
  for name in ('validityState', 'carStateHealthState', 'liveParametersHealthState'):
    assert getattr(observed, name) == 'unavailable'
  for name in ('carStateMonoTime', 'liveParametersMonoTime', 'calculationMonoTime'):
    assert getattr(observed, name).which() == 'unavailable'
