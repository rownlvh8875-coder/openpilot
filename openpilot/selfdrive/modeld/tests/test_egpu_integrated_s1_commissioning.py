import unittest

from tools.egpu_integrated_s1_plan import build_s1_plan
from tools.egpu_integrated_s1_qualification import S1Metrics, S1Policy, qualify


class TestS1Plan(unittest.TestCase):
  def postboot(self):
    return {
      "status": "PASS",
      "head": "abc123",
      "nextGate": "S1_OBSERVER_ONLY",
    }

  def test_pass_postboot_opens_plan_only(self):
    plan = build_s1_plan(self.postboot(), expected_head="abc123")
    self.assertEqual(plan["stage"], "S1_OBSERVER_PLAN_ONLY")
    self.assertEqual([x["id"] for x in plan["sequence"]], ["S1_OFF_BEFORE", "S1_ON", "S1_OFF_AFTER"])
    self.assertFalse(plan["authorizations"]["observerEnableAuthorization"])
    self.assertFalse(plan["authorizations"]["rebootAuthorization"])
    self.assertFalse(plan["authorizations"]["controlAuthorization"])

  def test_postboot_hold_rejected(self):
    value = self.postboot()
    value["status"] = "HOLD"
    with self.assertRaises(ValueError):
      build_s1_plan(value, expected_head="abc123")

  def test_wrong_head_rejected(self):
    with self.assertRaises(ValueError):
      build_s1_plan(self.postboot(), expected_head="other")


class TestS1Qualification(unittest.TestCase):
  def policy(self):
    return S1Policy(
      min_samples_per_leg=100,
      max_baseline_p95_drift_ms=2.0,
      max_baseline_p99_drift_ms=3.0,
      max_observer_p50_increase_ms=1.0,
      max_observer_p95_increase_ms=2.0,
      max_observer_p99_increase_ms=3.0,
      max_additional_frame_gaps=0,
      max_fallbacks_per_leg=0,
      max_guard_violations_per_leg=0,
      max_observer_write_errors=0,
    )

  def metric(self, leg, p50=35.0, p95=40.0, p99=45.0, max_ms=50.0,
             samples=200, gaps=0, fallbacks=0, guard=0, write_errors=0):
    return S1Metrics(
      leg=leg, samples=samples, p50_ms=p50, p95_ms=p95, p99_ms=p99, max_ms=max_ms,
      frame_gaps=gaps, fallbacks=fallbacks, guard_violations=guard,
      observer_write_errors=write_errors,
    )

  def test_pass(self):
    values = {
      "S1_OFF_BEFORE": self.metric("S1_OFF_BEFORE"),
      "S1_ON": self.metric("S1_ON", p50=35.4, p95=40.7, p99=46.0, max_ms=51.0),
      "S1_OFF_AFTER": self.metric("S1_OFF_AFTER", p50=35.2, p95=40.5, p99=45.5, max_ms=50.5),
    }
    result = qualify(values, self.policy())
    self.assertEqual(result["status"], "PASS")
    self.assertEqual(result["nextGate"], "S2_TELEMETRY_PLAN_ONLY")
    self.assertFalse(result["controlAuthorization"])

  def test_baseline_drift_is_hold(self):
    values = {
      "S1_OFF_BEFORE": self.metric("S1_OFF_BEFORE", p95=40.0, p99=45.0),
      "S1_ON": self.metric("S1_ON", p95=41.0, p99=46.0),
      "S1_OFF_AFTER": self.metric("S1_OFF_AFTER", p95=44.0, p99=50.0),
    }
    result = qualify(values, self.policy())
    self.assertEqual(result["status"], "HOLD")
    self.assertIn("baseline_p95_drift", result["holdReasons"])

  def test_observer_latency_regression_is_fail(self):
    values = {
      "S1_OFF_BEFORE": self.metric("S1_OFF_BEFORE"),
      "S1_ON": self.metric("S1_ON", p50=37.0, p95=44.0, p99=50.0, max_ms=55.0),
      "S1_OFF_AFTER": self.metric("S1_OFF_AFTER"),
    }
    result = qualify(values, self.policy())
    self.assertEqual(result["status"], "FAIL")
    self.assertIn("observer_p95_increase", result["failReasons"])

  def test_sample_shortage_is_hold(self):
    values = {
      "S1_OFF_BEFORE": self.metric("S1_OFF_BEFORE", samples=10),
      "S1_ON": self.metric("S1_ON"),
      "S1_OFF_AFTER": self.metric("S1_OFF_AFTER"),
    }
    result = qualify(values, self.policy())
    self.assertEqual(result["status"], "HOLD")

  def test_guard_violation_is_fail(self):
    values = {
      "S1_OFF_BEFORE": self.metric("S1_OFF_BEFORE"),
      "S1_ON": self.metric("S1_ON", guard=1),
      "S1_OFF_AFTER": self.metric("S1_OFF_AFTER"),
    }
    result = qualify(values, self.policy())
    self.assertEqual(result["status"], "FAIL")
    self.assertIn("S1_ON:guard_violations", result["failReasons"])

  def test_missing_leg_is_hold(self):
    values = {
      "S1_OFF_BEFORE": self.metric("S1_OFF_BEFORE"),
      "S1_ON": self.metric("S1_ON"),
    }
    result = qualify(values, self.policy())
    self.assertEqual(result["status"], "HOLD")


if __name__ == "__main__":
  unittest.main()
