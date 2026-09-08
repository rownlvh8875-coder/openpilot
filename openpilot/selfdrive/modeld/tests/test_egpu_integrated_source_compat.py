import unittest

from tools.egpu_integrated_source_compat import REVIEWED_HEAD, V6_MIGRATION_PATHS, classify


class TestEgpuIntegratedSourceCompat(unittest.TestCase):
  def test_exact_reviewed(self):
    reviewed = {"a": "1", "b": "2"}
    status, changed = classify("head", "head", reviewed, dict(reviewed))
    self.assertEqual(status, "EXACT_REVIEWED")
    self.assertEqual(changed, [])

  def test_code_equivalent_head_drift(self):
    reviewed = {"a": "1", "b": "2"}
    status, changed = classify("old", "new", reviewed, dict(reviewed))
    self.assertEqual(status, "CODE_EQUIVALENT_HEAD_DRIFT")
    self.assertEqual(changed, [])

  def test_critical_change_requires_review(self):
    reviewed = {"a": "1", "b": "2"}
    upstream = {"a": "1", "b": "3"}
    status, changed = classify("old", "new", reviewed, upstream)
    self.assertEqual(status, "REVIEW_REQUIRED")
    self.assertEqual(changed, ["b"])

  def test_missing_critical_file_requires_review(self):
    reviewed = {"a": "1"}
    status, changed = classify("old", "new", reviewed, {"a": None})
    self.assertEqual(status, "REVIEW_REQUIRED")
    self.assertEqual(changed, ["a"])

  def test_v6_no_path_drift_uses_distinct_label(self):
    reviewed = {"planner": "1"}
    status, changed = classify(
      "old", "new", reviewed, dict(reviewed),
      equivalent_label="NO_V6_PATH_DRIFT",
      review_label="V6_REBASE_REVIEW_REQUIRED",
    )
    self.assertEqual(status, "NO_V6_PATH_DRIFT")
    self.assertEqual(changed, [])

  def test_v6_path_drift_requires_rebase_review(self):
    reviewed = {"planner": "1"}
    status, changed = classify(
      "old", "new", reviewed, {"planner": "2"},
      equivalent_label="NO_V6_PATH_DRIFT",
      review_label="V6_REBASE_REVIEW_REQUIRED",
    )
    self.assertEqual(status, "V6_REBASE_REVIEW_REQUIRED")
    self.assertEqual(changed, ["planner"])

  def test_reviewed_head_is_latest_explicitly_reviewed_upstream(self):
    self.assertEqual(REVIEWED_HEAD, "a92d3a787e84a29949ca2b802f6a78fc9b580e87")

  def test_radar_cutout_behavior_surfaces_are_watched(self):
    required = {
      "openpilot/selfdrive/carrot/radar/radard_dpath.py",
      "openpilot/selfdrive/carrot/radar_motion/controller.py",
      "openpilot/selfdrive/carrot/radar_motion/trajectory_cutout.py",
      "openpilot/selfdrive/controls/lib/longitudinal_cutout.py",
      "openpilot/selfdrive/controls/lib/longitudinal_mpc_lib/long_mpc.py",
      "openpilot/selfdrive/controls/lib/longitudinal_planner.py",
      "openpilot/cereal/log.capnp",
    }
    self.assertTrue(required.issubset(set(V6_MIGRATION_PATHS)))


if __name__ == "__main__":
  unittest.main()