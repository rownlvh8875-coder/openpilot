import unittest

from tools.egpu_integrated_s2a_plan import build_s2a_plan
from tools.egpu_integrated_s2a_qualification import S2AMetrics, S2APolicy, qualify
from tools.egpu_integrated_s2b_plan import build_s2b_plan

BRANCH = "carrot-wip-integrated-v6"
HEAD = "abc"


class TestS2APlan(unittest.TestCase):
  def s1(self):
    return {
      "status": "PASS",
      "nextGate": "S2_TELEMETRY_PLAN_ONLY",
      "controlAuthorization": False,
      "sourceHead": HEAD,
      "sourceBranch": BRANCH,
    }

  def test_s1_pass_opens_s2a_plan_only(self):
    plan = build_s2a_plan(self.s1(), expected_head=HEAD)
    self.assertEqual(plan["stage"], "S2A_TELEMETRY_ONLY_PLAN")
    self.assertEqual(plan["sourceHead"], HEAD)
    self.assertEqual(plan["sourceBranch"], BRANCH)
    self.assertEqual(
      [x["id"] for x in plan["sequence"]],
      ["S2A_OFF_BEFORE", "S2A_TELEMETRY_ON", "S2A_OFF_AFTER"],
    )
    self.assertTrue(plan["sequence"][1]["telemetry"])
    self.assertFalse(plan["sequence"][1]["observer"])
    self.assertFalse(plan["authorizations"]["telemetryEnableAuthorization"])

  def test_s1_hold_rejected(self):
    value = self.s1(); value["status"] = "HOLD"
    with self.assertRaises(ValueError):
      build_s2a_plan(value, expected_head=HEAD)

  def test_s1_head_mismatch_rejected(self):
    value = self.s1(); value["sourceHead"] = "different"
    with self.assertRaises(ValueError):
      build_s2a_plan(value, expected_head=HEAD)

  def test_s1_branch_mismatch_rejected(self):
    value = self.s1(); value["sourceBranch"] = "other"
    with self.assertRaises(ValueError):
      build_s2a_plan(value, expected_head=HEAD)


class TestS2AQualification(unittest.TestCase):
  def policy(self):
    return S2APolicy(
      min_model_samples_per_leg=100,
      min_hardware_samples_on_leg=5,
      min_hardware_valid_fraction=0.8,
      max_baseline_p95_drift_ms=2.0,
      max_baseline_p99_drift_ms=3.0,
      max_telemetry_p50_increase_ms=1.0,
      max_telemetry_p95_increase_ms=2.0,
      max_telemetry_p99_increase_ms=3.0,
      max_additional_frame_gaps=0,
      max_fallbacks_per_leg=0,
      max_guard_violations_per_leg=0,
      max_hardware_error_samples=0,
      max_supply_fault_samples=0,
      max_p95_hardware_sample_duration_ms=200.0,
      max_gpu_temp_c=90.0,
      max_memory_temp_c=90.0,
      max_power_draw_w=150.0,
    )

  def metric(self, leg, p50=35.0, p95=40.0, p99=45.0, max_ms=50.0,
             samples=200, gaps=0, fallbacks=0, guard=0,
             telemetry_fresh=None, hw_samples=0, hw_valid=0, hw_errors=0,
             faults=0, hw_p95=0.0, hw_max=0.0, temp=None, mem_temp=None, power=None):
    if leg == "S2A_TELEMETRY_ON":
      if telemetry_fresh is None: telemetry_fresh = True
      if hw_samples == 0: hw_samples = 10
      if hw_valid == 0: hw_valid = 10
      if hw_p95 == 0.0: hw_p95 = 50.0
      if hw_max == 0.0: hw_max = 80.0
      if temp is None: temp = 60.0
      if mem_temp is None: mem_temp = 65.0
      if power is None: power = 100.0
    return S2AMetrics(
      leg=leg, samples=samples, p50_ms=p50, p95_ms=p95, p99_ms=p99, max_ms=max_ms,
      frame_gaps=gaps, fallbacks=fallbacks, guard_violations=guard,
      telemetry_state_fresh=telemetry_fresh,
      hardware_samples=hw_samples, hardware_valid_samples=hw_valid,
      hardware_error_samples=hw_errors, supply_fault_samples=faults,
      p95_hardware_sample_duration_ms=hw_p95, max_hardware_sample_duration_ms=hw_max,
      max_gpu_temp_c=temp, max_memory_temp_c=mem_temp, max_power_draw_w=power,
    )

  def values(self, on=None, after=None):
    return {
      "S2A_OFF_BEFORE": self.metric("S2A_OFF_BEFORE"),
      "S2A_TELEMETRY_ON": on or self.metric("S2A_TELEMETRY_ON", p50=35.3, p95=40.7, p99=45.8, max_ms=51.0),
      "S2A_OFF_AFTER": after or self.metric("S2A_OFF_AFTER", p50=35.2, p95=40.4, p99=45.4, max_ms=50.5),
    }

  def test_pass(self):
    result = qualify(self.values(), self.policy())
    self.assertEqual(result["status"], "PASS")
    self.assertEqual(result["nextGate"], "S2B_OBSERVER_TELEMETRY_COEXISTENCE_PLAN_ONLY")
    self.assertFalse(result["controlAuthorization"])

  def test_baseline_drift_is_hold(self):
    after = self.metric("S2A_OFF_AFTER", p95=44.0, p99=50.0, max_ms=55.0)
    result = qualify(self.values(after=after), self.policy())
    self.assertEqual(result["status"], "HOLD")
    self.assertIn("baseline_p95_drift", result["holdReasons"])

  def test_telemetry_latency_regression_is_fail_when_baseline_stable(self):
    on = self.metric("S2A_TELEMETRY_ON", p50=37.0, p95=44.0, p99=50.0, max_ms=55.0)
    result = qualify(self.values(on=on), self.policy())
    self.assertEqual(result["status"], "FAIL")
    self.assertIn("telemetry_p95_increase", result["failReasons"])

  def test_hardware_sample_shortage_is_hold(self):
    on = self.metric("S2A_TELEMETRY_ON", hw_samples=1, hw_valid=1)
    result = qualify(self.values(on=on), self.policy())
    self.assertEqual(result["status"], "HOLD")
    self.assertIn("hardware_sample_shortage", result["holdReasons"])

  def test_low_hardware_valid_fraction_is_fail(self):
    on = self.metric("S2A_TELEMETRY_ON", hw_samples=10, hw_valid=5)
    result = qualify(self.values(on=on), self.policy())
    self.assertEqual(result["status"], "FAIL")
    self.assertIn("hardware_valid_fraction", result["failReasons"])

  def test_supply_fault_is_fail(self):
    on = self.metric("S2A_TELEMETRY_ON", faults=1)
    result = qualify(self.values(on=on), self.policy())
    self.assertEqual(result["status"], "FAIL")
    self.assertIn("supply_fault_samples", result["failReasons"])

  def test_thermal_evidence_missing_is_hold_when_policy_gates_it(self):
    on = self.metric("S2A_TELEMETRY_ON")
    on = S2AMetrics(**{**on.__dict__, "max_gpu_temp_c": None})
    result = qualify(self.values(on=on), self.policy())
    self.assertEqual(result["status"], "HOLD")
    self.assertIn("gpu_temp_evidence_missing", result["holdReasons"])

  def test_null_thermal_policy_explicitly_disables_that_gate(self):
    policy = self.policy()
    policy = S2APolicy(**{**policy.__dict__, "max_gpu_temp_c": None, "max_memory_temp_c": None, "max_power_draw_w": None})
    result = qualify(self.values(), policy)
    self.assertEqual(result["status"], "PASS")


class TestS2BPlan(unittest.TestCase):
  def s1(self):
    return {
      "status": "PASS", "nextGate": "S2_TELEMETRY_PLAN_ONLY", "controlAuthorization": False,
      "sourceHead": HEAD, "sourceBranch": BRANCH,
    }

  def s2a(self):
    return {
      "status": "PASS",
      "nextGate": "S2B_OBSERVER_TELEMETRY_COEXISTENCE_PLAN_ONLY",
      "controlAuthorization": False,
    }

  def evidence(self):
    return {
      "stage": "S2A_TELEMETRY_ONLY_EVIDENCE",
      "sourceHead": HEAD,
      "sourceBranch": BRANCH,
      "controlAuthorization": False,
    }

  def test_both_pass_open_plan_only(self):
    plan = build_s2b_plan(self.s1(), self.s2a(), self.evidence(), expected_head=HEAD)
    self.assertEqual(plan["stage"], "S2B_OBSERVER_TELEMETRY_COEXISTENCE_PLAN_ONLY")
    self.assertEqual(plan["sourceHead"], HEAD)
    self.assertTrue(all(x["observer"] for x in plan["sequence"]))
    self.assertFalse(plan["developmentBoundary"]["recorderImplemented"])
    self.assertFalse(plan["authorizations"]["telemetryEnableAuthorization"])

  def test_s2a_hold_rejected(self):
    value = self.s2a(); value["status"] = "HOLD"
    with self.assertRaises(ValueError):
      build_s2b_plan(self.s1(), value, self.evidence(), expected_head=HEAD)

  def test_s1_source_mismatch_rejected(self):
    value = self.s1(); value["sourceHead"] = "different"
    with self.assertRaises(ValueError):
      build_s2b_plan(value, self.s2a(), self.evidence(), expected_head=HEAD)

  def test_s2a_evidence_source_mismatch_rejected(self):
    evidence = self.evidence(); evidence["sourceHead"] = "different"
    with self.assertRaises(ValueError):
      build_s2b_plan(self.s1(), self.s2a(), evidence, expected_head=HEAD)


if __name__ == "__main__":
  unittest.main()
