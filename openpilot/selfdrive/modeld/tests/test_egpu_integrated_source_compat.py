import unittest

from tools.egpu_integrated_source_compat import (
  CRITICAL_PATHS, PROVENANCE_PATHS, REVIEWED_HEAD, UPSTREAM_ABSENT_LOCAL_PATHS, V6_MIGRATION_PATHS, blob, classify,
)


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

  def test_missing_both_is_not_evidence_of_compatibility(self):
    status, changed = classify("head", "head", {"required": None}, {"required": None})
    self.assertEqual((status, changed), ("REVIEW_REQUIRED", ["required"]))

  def test_all_watched_surfaces_require_review_for_change_or_deletion(self):
    paths = CRITICAL_PATHS + V6_MIGRATION_PATHS + PROVENANCE_PATHS
    reviewed = dict.fromkeys(paths, "original-object")
    for path in paths:
      for replacement in ("new-object", None):
        with self.subTest(path=path, replacement=replacement):
          upstream = dict(reviewed, **{path: replacement})
          self.assertEqual(classify("old", "new", reviewed, upstream), ("REVIEW_REQUIRED", [path]))

  def test_deliberately_local_paths_still_detect_upstream_addition(self):
    reviewed = dict.fromkeys(UPSTREAM_ABSENT_LOCAL_PATHS)
    self.assertEqual(classify("old", "new", reviewed, reviewed, allowed_absent=UPSTREAM_ABSENT_LOCAL_PATHS)[0],
                     "CODE_EQUIVALENT_HEAD_DRIFT")
    for path in reviewed:
      upstream = dict(reviewed, **{path: "new-upstream-file"})
      self.assertEqual(classify("old", "new", reviewed, upstream, allowed_absent=UPSTREAM_ABSENT_LOCAL_PATHS),
                       ("REVIEW_REQUIRED", [path]))

  def test_unexpected_upstream_map_entry_requires_review(self):
    self.assertEqual(classify("old", "new", {"a": "1"}, {"a": "1", "b": "2"}),
                     ("REVIEW_REQUIRED", ["b"]))

  def test_bad_revision_is_not_silently_missing(self):
    with self.assertRaises(RuntimeError):
      blob("invalid-reviewed-ref-that-does-not-exist", CRITICAL_PATHS[0])

  def test_new_files_in_amd_tree_require_review(self):
    import subprocess
    import tempfile
    from pathlib import Path
    with tempfile.TemporaryDirectory() as directory:
      root = Path(directory)
      def git(*args):
        return subprocess.check_output(["git", "-C", directory, *args], text=True).strip()
      git("init", "--quiet")
      folder = root / "tinygrad_repo/tinygrad/runtime/support/am"
      folder.mkdir(parents=True)
      (folder / "amdev.py").write_text("old implementation\n")
      git("add", ".")
      tree = git("write-tree")
      path = "tinygrad_repo/tinygrad/runtime/support/am"
      before = git("rev-parse", f"{tree}:{path}")
      (folder / "new_transport.py").write_text("new transport\n")
      git("add", ".")
      tree = git("write-tree")
      after = git("rev-parse", f"{tree}:{path}")
      self.assertIn(path, CRITICAL_PATHS)
      self.assertEqual(classify("old", "new", {path: before}, {path: after}), ("REVIEW_REQUIRED", [path]))

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
