import unittest

from tools.egpu_integrated_s2a_build_evidence import assemble


class TestS2AEvidenceAssembly(unittest.TestCase):
  def summary(self, leg, head="abc123", branch="carrot-wip-integrated-v6"):
    return {
      "leg": leg,
      "sourceHead": head,
      "sourceBranch": branch,
      "samples": 100,
      "p50ModelExecutionMs": 35.0,
      "p95ModelExecutionMs": 40.0,
      "p99ModelExecutionMs": 45.0,
      "maxModelExecutionMs": 50.0,
      "frameGapCount": 0,
      "fallbackCount": 0,
      "guardViolationCount": 0,
      "telemetryStateFresh": True if leg == "S2A_TELEMETRY_ON" else None,
      "hardwareSamples": 5 if leg == "S2A_TELEMETRY_ON" else 0,
      "hardwareValidSamples": 5 if leg == "S2A_TELEMETRY_ON" else 0,
      "hardwareErrorSamples": 0,
      "supplyFaultSamples": 0,
      "p95HardwareSampleDurationMs": 50.0 if leg == "S2A_TELEMETRY_ON" else 0.0,
      "maxHardwareSampleDurationMs": 80.0 if leg == "S2A_TELEMETRY_ON" else 0.0,
      "maxGpuTempC": 60.0 if leg == "S2A_TELEMETRY_ON" else None,
      "maxMemoryTempC": 65.0 if leg == "S2A_TELEMETRY_ON" else None,
      "maxPowerDrawW": 100.0 if leg == "S2A_TELEMETRY_ON" else None,
    }

  def test_assemble_orders_all_legs(self):
    evidence = assemble([
      self.summary("S2A_TELEMETRY_ON"),
      self.summary("S2A_OFF_AFTER"),
      self.summary("S2A_OFF_BEFORE"),
    ])
    self.assertEqual(list(evidence["legs"]), ["S2A_OFF_BEFORE", "S2A_TELEMETRY_ON", "S2A_OFF_AFTER"])
    self.assertEqual(evidence["sourceHead"], "abc123")
    self.assertEqual(evidence["sourceBranch"], "carrot-wip-integrated-v6")
    self.assertFalse(evidence["controlAuthorization"])
    self.assertFalse(evidence["shadowAuthorization"])

  def test_missing_leg_rejected(self):
    with self.assertRaises(ValueError):
      assemble([self.summary("S2A_OFF_BEFORE"), self.summary("S2A_TELEMETRY_ON")])

  def test_duplicate_leg_rejected(self):
    with self.assertRaises(ValueError):
      assemble([
        self.summary("S2A_OFF_BEFORE"),
        self.summary("S2A_TELEMETRY_ON"),
        self.summary("S2A_TELEMETRY_ON"),
      ])

  def test_source_head_mismatch_rejected(self):
    with self.assertRaises(ValueError):
      assemble([
        self.summary("S2A_OFF_BEFORE"),
        self.summary("S2A_TELEMETRY_ON", head="different"),
        self.summary("S2A_OFF_AFTER"),
      ])

  def test_source_branch_mismatch_rejected(self):
    with self.assertRaises(ValueError):
      assemble([
        self.summary("S2A_OFF_BEFORE"),
        self.summary("S2A_TELEMETRY_ON", branch="other"),
        self.summary("S2A_OFF_AFTER"),
      ])


if __name__ == "__main__":
  unittest.main()
