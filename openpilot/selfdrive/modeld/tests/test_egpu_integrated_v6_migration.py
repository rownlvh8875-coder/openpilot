import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]


class TestEgpuIntegratedV6Migration(unittest.TestCase):
  def test_exact_restore_hash_uses_utf8_bytes_without_platform_newline_translation(self):
    import hashlib
    from tools.apply_egpu_integrated_modeld_patch import git_hash_text
    source = "first line\n# 한글 source\n"
    data = source.encode("utf-8")
    expected = hashlib.sha1(f"blob {len(data)}\0".encode() + data).hexdigest()
    self.assertEqual(git_hash_text(source), expected)

  def test_plannerd_preserves_both_latest_carrot_and_v6_features(self):
    text = (ROOT / "openpilot/selfdrive/controls/plannerd.py").read_text(encoding="utf-8")
    self.assertIn("from openpilot.selfdrive.controls.lib.longitudinal_stopping_lead import StoppingLeadFilter", text)
    self.assertIn("from openpilot.selfdrive.controls.lib.h1_observability import H1Observability", text)
    self.assertNotIn("<<<<<<<", text)
    self.assertNotIn(">>>>>>>", text)

  def test_h1_observability_source_is_present(self):
    self.assertTrue((ROOT / "openpilot/selfdrive/controls/lib/h1_observability.py").is_file())

  def test_hyundai_radar_source_dbc_is_present(self):
    self.assertTrue((ROOT / "opendbc_repo/opendbc/dbc/generator/hyundai/hyundai_canfd_radar.dbc").is_file())

  def test_generated_services_header_is_not_versioned_by_migration(self):
    # services.h may exist in a built checkout, but it must not be a tracked source
    # addition in this migration. The source of truth remains services.py.
    import subprocess
    p = subprocess.run(["git", "ls-files", "--error-unmatch", "openpilot/cereal/services.h"],
                       text=True, capture_output=True, check=False)
    self.assertNotEqual(p.returncode, 0)

  def test_log_schema_preserves_h1_slots_and_latest_carrot_cutout_fields(self):
    text = (ROOT / "openpilot/cereal/log.capnp").read_text(encoding="utf-8")
    self.assertIn("carrotH1ReplayTrace @110 :Custom.CarrotH1ReplayTrace;", text)
    self.assertIn("carrotH1ConfigSnapshot @111 :Custom.CarrotH1ConfigSnapshot;", text)
    self.assertIn("cutOutTime @18 :Float32;", text)
    self.assertIn("cutOutConfidence @19 :Float32;", text)
    self.assertNotIn("<<<<<<<", text)
    self.assertNotIn(">>>>>>>", text)

  def test_longitudinal_planner_preserves_h1_observation_and_cutout_gate(self):
    text = (ROOT / "openpilot/selfdrive/controls/lib/longitudinal_planner.py").read_text(encoding="utf-8")
    for required in (
      "self.h1_consumed_params_raw = {}",
      'self.h1_consumed_params_raw["LongActuatorDelay"]',
      "self.observedLongActuatorDelaySeconds",
      "cutout_relief_enabled=(",
      "not reset_state and not sm['carState'].gasPressed",
      "and not force_slow_decel and not self.output_should_stop",
    ):
      self.assertIn(required, text)
    self.assertNotIn("<<<<<<<", text)
    self.assertNotIn(">>>>>>>", text)

  def test_carrot_cutout_relief_keeps_bounded_and_competing_obstacle_invariants(self):
    cutout = (ROOT / "openpilot/selfdrive/controls/lib/longitudinal_cutout.py").read_text(encoding="utf-8")
    mpc = (ROOT / "openpilot/selfdrive/controls/lib/longitudinal_mpc_lib/long_mpc.py").read_text(encoding="utf-8")

    # Relief is zero before predicted body clearance + 0.30 s, is rejected when
    # the projected remaining gap is too small, and is bounded to half TF,
    # 0.50 s, and 8 m. These are upstream Carrot invariants, not project safety limits.
    self.assertIn("clearance_time = exit_time + 0.30", cutout)
    self.assertIn("remaining_gap <= max(6.0, stop_distance)", cutout)
    self.assertIn("credit = min(8.0, min(0.5, 0.5 * t_follow) * v_ego) * confidence", cutout)
    self.assertIn("np.clip((horizons - clearance_time) / 0.50, 0.0, 1.0)", cutout)

    # Only leadOne's future following obstacle is relaxed. leadTwo, cruise, and
    # traffic-stop obstacles still compete through the same min-obstacle stack.
    self.assertIn("lead_0_follow_obstacle = lead_0_obstacle + cutout_obstacle_relief(", mpc)
    self.assertIn("np.column_stack([lead_0_follow_obstacle, lead_1_obstacle, cruise_obstacle, x2])", mpc)

    # FCW and predicted-danger diagnostics deliberately keep the unrelieved
    # lead trajectory / obstacle.
    self.assertIn("self.update_predicted_danger_margin(radarstate.leadOne, lead_0_obstacle", mpc)
    self.assertIn("lead_xv_0[FCW_IDXS,0] - self.x_sol[FCW_IDXS,0] < CRASH_DISTANCE", mpc)


if __name__ == "__main__":
  unittest.main()
