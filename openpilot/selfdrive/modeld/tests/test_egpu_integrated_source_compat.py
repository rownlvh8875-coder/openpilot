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


if __name__ == "__main__":
  unittest.main()
