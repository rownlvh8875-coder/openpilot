import unittest

from tools.egpu_integrated_s4b_shadow_probe import validate_readiness, write_event

HEAD = "a" * 40
BRANCH = "carrot-wip-integrated-v6"


def readiness():
  return {
    "schemaVersion": 1,
    "stage": "S4B_REVIEW_READINESS",
    "status": "PASS",
    "sourceHead": HEAD,
    "sourceBranch": BRANCH,
    "reasons": [],
    "nextGate": "S4B_PARKED_SHADOW_LOAD_PROBE_PLAN_ONLY",
    "authorizations": {
      "shadowExecutionAuthorization": False,
      "observerEnableAuthorization": False,
      "telemetryEnableAuthorization": False,
      "rebootAuthorization": False,
      "publicRoadAuthorization": False,
      "controlAuthorization": False,
    },
  }


class TestS4BReadinessEntryGate(unittest.TestCase):
  def test_valid_readiness_passes_validation(self):
    validate_readiness(readiness(), expected_head=HEAD)

  def test_hold_readiness_rejected(self):
    value = readiness(); value["status"] = "HOLD"
    with self.assertRaises(ValueError):
      validate_readiness(value, expected_head=HEAD)

  def test_source_head_mismatch_rejected(self):
    with self.assertRaises(ValueError):
      validate_readiness(readiness(), expected_head="b" * 40)

  def test_source_branch_mismatch_rejected(self):
    with self.assertRaises(ValueError):
      validate_readiness(readiness(), expected_head=HEAD, expected_branch="other")

  def test_next_gate_mismatch_rejected(self):
    value = readiness(); value["nextGate"] = "OTHER"
    with self.assertRaises(ValueError):
      validate_readiness(value, expected_head=HEAD)

  def test_any_execution_or_control_authorization_rejected(self):
    for name in readiness()["authorizations"]:
      value = readiness(); value["authorizations"][name] = True
      with self.subTest(name=name), self.assertRaises(ValueError):
        validate_readiness(value, expected_head=HEAD)

  def test_event_defaults_never_mark_control_or_quality_eligible(self):
    class Sink:
      def __init__(self): self.text = ""
      def write(self, value): self.text += value
      def flush(self): pass
    sink = Sink()
    write_event(sink, {"type": "test"})
    self.assertIn('"controlEligible":false', sink.text)
    self.assertIn('"qualityComparisonEligible":false', sink.text)
    self.assertIn('"shadowOnly":true', sink.text)


if __name__ == "__main__":
  unittest.main()
