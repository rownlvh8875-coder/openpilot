import unittest

from tools.egpu_integrated_live_provenance import compare_snapshots
from tools.egpu_integrated_transition_plan import build_plan


class TestLiveProvenance(unittest.TestCase):
  def snapshot(self):
    return {
      "head": "abc",
      "branch": "carrot-wip",
      "trackedDiffSha256": "d" * 64,
      "sourceFiles": {"a.py": "1" * 64, "b.dbc": "2" * 64},
    }

  def test_exact_match(self):
    saved = self.snapshot()
    result = compare_snapshots(saved, dict(saved))
    self.assertTrue(result["match"])
    self.assertEqual(result["mismatches"], [])

  def test_generated_files_are_not_part_of_snapshot_comparison(self):
    saved = self.snapshot()
    current = dict(saved)
    current["generatedNoise"] = {"services.h": "changed"}
    self.assertTrue(compare_snapshots(saved, current)["match"])

  def test_source_change_holds(self):
    saved = self.snapshot()
    current = self.snapshot()
    current["sourceFiles"] = dict(current["sourceFiles"])
    current["sourceFiles"]["a.py"] = "3" * 64
    result = compare_snapshots(saved, current)
    self.assertFalse(result["match"])
    self.assertIn("sourceFiles:a.py", result["mismatches"])

  def test_tracked_diff_change_holds(self):
    saved = self.snapshot()
    current = self.snapshot()
    current["trackedDiffSha256"] = "e" * 64
    result = compare_snapshots(saved, current)
    self.assertFalse(result["match"])
    self.assertIn("trackedDiffSha256", result["mismatches"])


class TestTransitionPlan(unittest.TestCase):
  def preflight(self):
    return {
      "status": "PASS",
      "failedChecks": [],
      "targetHead": "target123",
      "liveHead": "live123",
      "liveBranch": "carrot-wip",
      "target": "/data/target",
      "live": "/data/openpilot",
      "backup": "/data/backup",
    }

  def test_pass_preflight_only_generates_plan(self):
    plan = build_plan(self.preflight(), expected_target_head="target123")
    self.assertEqual(plan["stage"], "INSTALL_PLAN_ONLY")
    self.assertTrue(plan["manualAuthorizationRequired"])
    self.assertFalse(plan["authorizations"]["installAuthorization"])
    self.assertFalse(plan["authorizations"]["rebootAuthorization"])
    self.assertFalse(plan["authorizations"]["controlAuthorization"])

  def test_hold_preflight_rejected(self):
    value = self.preflight()
    value["status"] = "HOLD"
    with self.assertRaises(ValueError):
      build_plan(value, expected_target_head="target123")

  def test_target_head_mismatch_rejected(self):
    with self.assertRaises(ValueError):
      build_plan(self.preflight(), expected_target_head="other")

  def test_failed_check_rejected_even_if_status_pass(self):
    value = self.preflight()
    value["failedChecks"] = ["unexpected"]
    with self.assertRaises(ValueError):
      build_plan(value, expected_target_head="target123")


if __name__ == "__main__":
  unittest.main()
