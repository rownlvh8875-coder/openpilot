import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]


class TestEgpuIntegratedV6Migration(unittest.TestCase):
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


if __name__ == "__main__":
  unittest.main()
