import unittest

from tools.egpu_integrated_source_compat import classify


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


if __name__ == "__main__":
  unittest.main()
