import unittest

from tools.egpu_integrated_s1_build_evidence import assemble


class TestS1EvidenceAssembly(unittest.TestCase):
  def summary(self, leg):
    return {
      "leg": leg,
      "sourceHead": "abc123",
      "sourceBranch": "carrot-wip-integrated-v6",
      "samples": 100,
      "p50ModelExecutionMs": 35.0,
      "p95ModelExecutionMs": 40.0,
      "p99ModelExecutionMs": 45.0,
      "maxModelExecutionMs": 50.0,
      "frameGapCount": 0,
      "fallbackCount": 0,
      "guardViolationCount": 0,
      "observerWriteErrors": 0,
      "observerStateFresh": True if leg == "S1_ON" else None,
    }

  def test_assemble_orders_all_legs(self):
    evidence = assemble([
      self.summary("S1_ON"),
      self.summary("S1_OFF_AFTER"),
      self.summary("S1_OFF_BEFORE"),
    ])
    self.assertEqual(list(evidence["legs"]), ["S1_OFF_BEFORE", "S1_ON", "S1_OFF_AFTER"])
    self.assertEqual(evidence["sourceHead"], "abc123")
    self.assertEqual(evidence["sourceBranch"], "carrot-wip-integrated-v6")
    self.assertFalse(evidence["controlAuthorization"])

  def test_missing_leg_rejected(self):
    with self.assertRaises(ValueError):
      assemble([self.summary("S1_OFF_BEFORE"), self.summary("S1_ON")])

  def test_duplicate_leg_rejected(self):
    with self.assertRaises(ValueError):
      assemble([
        self.summary("S1_OFF_BEFORE"),
        self.summary("S1_ON"),
        self.summary("S1_ON"),
      ])


if __name__ == "__main__":
  unittest.main()
