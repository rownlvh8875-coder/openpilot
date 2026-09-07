#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest

from openpilot.selfdrive.modeld.egpu_hardware_telemetry import normalize_smu_metrics
from openpilot.selfdrive.modeld.egpu_integrated_guardian import ActionSnapshot, GuardianPolicy, assess_guardian
from openpilot.selfdrive.modeld.egpu_integrated_model_slots import ModelSlot, builtin_qcom_slot
from openpilot.selfdrive.modeld.egpu_integrated_shadow_tap import IntegratedShadowSnapshot, decode_snapshot, encode_snapshot
from openpilot.selfdrive.modeld.egpu_integration_observer import EgpuIntegrationObserver


class TestIntegratedObserver(unittest.TestCase):
  def test_disabled_is_noop(self):
    with tempfile.TemporaryDirectory() as td:
      path = Path(td) / "state.json"
      obs = EgpuIntegrationObserver(enabled=False, state_path=path)
      obs.note_fallback(frame_id=1, reason="ignored")
      obs.observe(frame_id=1, state_frame_id=1, attempted_backend="egpu", active_backend="egpu", model_execution_s=0.03)
      self.assertFalse(path.exists())
      self.assertEqual(obs.fallback_count, 0)

  def test_records_fallback_and_backend(self):
    with tempfile.TemporaryDirectory() as td:
      path = Path(td) / "state.json"
      obs = EgpuIntegrationObserver(enabled=True, state_path=path, publish_interval_s=0.1)
      obs.note_fallback(frame_id=10, reason="runtime_model_execution_failed")
      obs.observe(frame_id=10, state_frame_id=11, attempted_backend="egpu", active_backend="qcom", model_execution_s=0.041)
      data = json.loads(path.read_text(encoding="utf-8"))
      self.assertEqual(data["frameAge"], 1)
      self.assertEqual(data["attemptedBackend"], "egpu")
      self.assertEqual(data["activeBackend"], "qcom")
      self.assertEqual(data["fallbackCount"], 1)


class TestModelSlots(unittest.TestCase):
  def test_qcom_builtin_is_noncontrolling(self):
    slot = builtin_qcom_slot()
    self.assertEqual(slot.slot, "qcom")
    self.assertFalse(slot.control_eligible)

  def test_control_eligible_is_rejected(self):
    slot = ModelSlot(slot="qcom", model_id="x", ref="x", backend="qcom", runner="tinygrad",
                     generation=0, nominal_hz=20.0, source="test", builtin=True, control_eligible=True)
    with self.assertRaises(ValueError):
      slot.validate()


class TestGuardian(unittest.TestCase):
  @staticmethod
  def snap(frame_id: int, backend: str, curvature: float = 0.001) -> ActionSnapshot:
    return ActionSnapshot(frame_id=frame_id, frame_age=0, model_execution_ms=30.0,
                          curvature=curvature, acceleration=-0.2, should_stop=False,
                          backend=backend, timestamp_mono_s=100.0)

  def test_never_authorizes_controls(self):
    result = assess_guardian(active=self.snap(1, "egpu"), shadow=self.snap(1, "qcom"),
                             hardware={"valid": True, "timestampMonoS": 99.0, "supplyFault": False})
    self.assertFalse(result.control_authorization)
    self.assertFalse(result.shadow_publish_to_controls)

  def test_frame_mismatch_holds(self):
    result = assess_guardian(active=self.snap(1, "egpu"), shadow=self.snap(2, "qcom"), hardware=None)
    self.assertIn("frame_mismatch", result.hard_issues)
    self.assertEqual(result.analysis_status, "HOLD")

  def test_supply_fault_holds(self):
    result = assess_guardian(active=self.snap(1, "egpu"), shadow=self.snap(1, "qcom"),
                             hardware={"valid": True, "timestampMonoS": 99.0, "supplyFault": True},
                             policy=GuardianPolicy())
    self.assertIn("egpu_supply_fault", result.hard_issues)


class TestShadowTap(unittest.TestCase):
  @staticmethod
  def snap(gear: str = "park") -> IntegratedShadowSnapshot:
    return IntegratedShadowSnapshot(
      frame_id=10, frame_id_extra=10, state_frame_id=10,
      camera_sof_ns=1, camera_eof_ns=2, active_backend="egpu", v_ego=0.0,
      standstill=True, gear=gear, lat_active=False, long_active=False,
      main_transform=(1.0,) * 9, extra_transform=(1.0,) * 9,
      desire_pulse=(0.0, 1.0), traffic_convention=(1.0, 0.0), action_t=(0.1, 0.2),
      created_mono_ns=3,
    )

  def test_roundtrip_and_stationary_guard(self):
    decoded = decode_snapshot(encode_snapshot(self.snap()))
    self.assertTrue(decoded.stationary_guard_ok)
    self.assertFalse(self.snap("drive").stationary_guard_ok)


class TestTelemetryNormalization(unittest.TestCase):
  def test_consumer_rdna_metrics(self):
    metrics = SimpleNamespace(SmuMetrics=SimpleNamespace(
      AvgTemperature=[70, 80], AverageSocketPower=101, dGPU_W_MAX=132,
      AverageGfxActivity=75, AverageGfxclkFrequencyPreDs=2400,
      AverageGfxclkFrequencyPostDs=500, AvgFanRpm=1200,
    ))
    smu_mod = SimpleNamespace(TEMP_HOTSPOT=0, TEMP_MEM=1)
    out = normalize_smu_metrics(metrics=metrics, smu_mod=smu_mod, ip_version=(14, 0, 2))
    self.assertEqual(out["tempC"], 70.0)
    self.assertEqual(out["memoryTempC"], 80.0)
    self.assertEqual(out["powerDrawW"], 101.0)
    self.assertEqual(out["powerLimitW"], 132.0)
    self.assertEqual(out["gpuClockMhz"], 2400.0)


if __name__ == "__main__":
  unittest.main()
