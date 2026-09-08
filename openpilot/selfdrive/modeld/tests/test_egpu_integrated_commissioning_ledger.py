import unittest

from tools.egpu_integrated_commissioning_ledger import build_ledger
from tools.egpu_integrated_s4b_readiness import evaluate

HEAD = "a" * 40
BRANCH = "carrot-wip-integrated-v6"
DIGEST = "b" * 64


def postboot():
  return {
    "stage": "POSTBOOT_ALL_FEATURES_OFF",
    "status": "PASS",
    "head": HEAD,
    "branch": BRANCH,
    "checks": [
      {"name": "expected_head", "pass": True, "detail": None},
      {"name": "expected_branch", "pass": True, "detail": None},
      {"name": "integrated_features_off", "pass": True, "detail": None},
    ],
    "failedChecks": [],
    "nextGate": "S1_OBSERVER_ONLY",
    "authorizations": {
      "publicRoadAuthorization": False,
      "controlAuthorization": False,
      "shadowAuthorization": False,
    },
  }


def binding(stage, next_gate, *, head=HEAD, branch=BRANCH):
  return {
    "stage": "COMMISSIONING_QUALIFICATION_BINDING",
    "qualificationStage": stage,
    "status": "PASS",
    "nextGate": next_gate,
    "sourceHead": head,
    "sourceBranch": branch,
    "evidenceSha256": DIGEST,
    "policySha256": DIGEST,
    "qualificationSha256": DIGEST,
    "recomputedExactMatch": True,
    "controlAuthorization": False,
    "publicRoadAuthorization": False,
  }


def s1():
  return binding("S1_OBSERVER_QUALIFICATION", "S2_TELEMETRY_PLAN_ONLY")


def s2a():
  return binding("S2A_TELEMETRY_ONLY_QUALIFICATION", "S2B_OBSERVER_TELEMETRY_COEXISTENCE_PLAN_ONLY")


def s2b():
  return binding("S2B_OBSERVER_TELEMETRY_COEXISTENCE_QUALIFICATION", "S4B_PARKED_SHADOW_LOAD_PROBE_REVIEW")


class TestCommissioningLedger(unittest.TestCase):
  def test_postboot_only_holds_for_s1(self):
    ledger = build_ledger(postboot(), expected_head=HEAD)
    self.assertEqual(ledger["status"], "HOLD_S1_BINDING_REQUIRED")
    self.assertEqual(ledger["missingRequirements"], ["S1_BINDING"])
    self.assertFalse(ledger["s4bReviewEligible"])
    self.assertFalse(ledger["authorizations"]["controlAuthorization"])

  def test_s1_only_holds_for_s2a(self):
    ledger = build_ledger(postboot(), expected_head=HEAD, s1_binding=s1())
    self.assertEqual(ledger["status"], "HOLD_S2A_BINDING_REQUIRED")
    self.assertEqual(ledger["missingRequirements"], ["S2A_BINDING"])

  def test_s1_s2a_hold_for_s2b(self):
    ledger = build_ledger(postboot(), expected_head=HEAD, s1_binding=s1(), s2a_binding=s2a())
    self.assertEqual(ledger["status"], "HOLD_S2B_BINDING_REQUIRED")
    self.assertEqual(ledger["nextGate"], "S2B_OBSERVER_TELEMETRY_COEXISTENCE_PLAN_ONLY")

  def test_complete_chain_is_review_ready_only(self):
    ledger = build_ledger(postboot(), expected_head=HEAD, s1_binding=s1(), s2a_binding=s2a(), s2b_binding=s2b())
    self.assertEqual(ledger["status"], "READY_S4B_REVIEW")
    self.assertTrue(ledger["s4bReviewEligible"])
    self.assertEqual(ledger["missingRequirements"], [])
    self.assertFalse(ledger["authorizations"]["shadowAuthorization"])
    self.assertFalse(ledger["authorizations"]["controlAuthorization"])

  def test_source_mismatch_rejected(self):
    with self.assertRaises(ValueError):
      build_ledger(postboot(), expected_head=HEAD, s1_binding=s1() | {"sourceHead": "c" * 40})

  def test_postboot_authorization_violation_rejected(self):
    value = postboot(); value["authorizations"]["controlAuthorization"] = True
    with self.assertRaises(ValueError):
      build_ledger(value, expected_head=HEAD)

  def test_binding_authorization_violation_rejected(self):
    value = s1(); value["controlAuthorization"] = True
    with self.assertRaises(ValueError):
      build_ledger(postboot(), expected_head=HEAD, s1_binding=value)


class TestS4BReadiness(unittest.TestCase):
  def test_incomplete_ledger_holds(self):
    ledger = build_ledger(postboot(), expected_head=HEAD, s1_binding=s1(), s2a_binding=s2a())
    report = evaluate(ledger, expected_head=HEAD)
    self.assertEqual(report["status"], "HOLD")
    self.assertIsNone(report["nextGate"])
    self.assertFalse(report["authorizations"]["shadowExecutionAuthorization"])

  def test_complete_chain_opens_plan_review_only(self):
    ledger = build_ledger(postboot(), expected_head=HEAD, s1_binding=s1(), s2a_binding=s2a(), s2b_binding=s2b())
    report = evaluate(ledger, expected_head=HEAD)
    self.assertEqual(report["status"], "PASS")
    self.assertEqual(report["nextGate"], "S4B_PARKED_SHADOW_LOAD_PROBE_PLAN_ONLY")
    self.assertFalse(report["authorizations"]["shadowExecutionAuthorization"])
    self.assertFalse(report["authorizations"]["controlAuthorization"])

  def test_readiness_source_mismatch_holds(self):
    ledger = build_ledger(postboot(), expected_head=HEAD, s1_binding=s1(), s2a_binding=s2a(), s2b_binding=s2b())
    report = evaluate(ledger, expected_head="d" * 40)
    self.assertEqual(report["status"], "HOLD")
    self.assertIn("source_identity_mismatch", report["reasons"])


if __name__ == "__main__":
  unittest.main()
