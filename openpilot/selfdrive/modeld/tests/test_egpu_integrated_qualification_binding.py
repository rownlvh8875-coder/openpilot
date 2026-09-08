import json
from pathlib import Path
import tempfile
import unittest

from tools.egpu_integrated_qualification_binding import build_binding, recompute

HEAD = "a" * 40
BRANCH = "carrot-wip-integrated-v6"


class TestQualificationBinding(unittest.TestCase):
  def write_triplet(self, evidence, policy, qualification):
    td = tempfile.TemporaryDirectory()
    root = Path(td.name)
    e = root / "evidence.json"; p = root / "policy.json"; q = root / "qualification.json"
    e.write_text(json.dumps(evidence, indent=2) + "\n")
    p.write_text(json.dumps(policy, indent=2) + "\n")
    q.write_text(json.dumps(qualification, indent=2) + "\n")
    return td, e, p, q

  def s1_evidence(self):
    def leg(name, on=False):
      return {
        "leg": name, "sourceHead": HEAD, "sourceBranch": BRANCH, "samples": 200,
        "p50ModelExecutionMs": 35.0, "p95ModelExecutionMs": 40.0,
        "p99ModelExecutionMs": 45.0, "maxModelExecutionMs": 50.0,
        "frameGapCount": 0, "fallbackCount": 0, "guardViolationCount": 0,
        "observerWriteErrors": 0, "observerStateFresh": True if on else None,
      }
    return {
      "schemaVersion": 2, "stage": "S1_OBSERVER_EVIDENCE", "sourceHead": HEAD, "sourceBranch": BRANCH,
      "legs": {
        "S1_OFF_BEFORE": leg("S1_OFF_BEFORE"),
        "S1_ON": leg("S1_ON", True),
        "S1_OFF_AFTER": leg("S1_OFF_AFTER"),
      },
      "controlAuthorization": False, "publicRoadAuthorization": False,
    }

  def s1_policy(self):
    return {
      "minSamplesPerLeg": 100,
      "maxBaselineP95DriftMs": 2.0,
      "maxBaselineP99DriftMs": 3.0,
      "maxObserverP50IncreaseMs": 1.0,
      "maxObserverP95IncreaseMs": 2.0,
      "maxObserverP99IncreaseMs": 3.0,
      "maxAdditionalFrameGaps": 0,
      "maxFallbacksPerLeg": 0,
      "maxGuardViolationsPerLeg": 0,
      "maxObserverWriteErrors": 0,
    }

  def s2a_evidence(self):
    def leg(name, on=False):
      return {
        "leg": name, "sourceHead": HEAD, "sourceBranch": BRANCH, "samples": 200,
        "p50ModelExecutionMs": 35.0, "p95ModelExecutionMs": 40.0,
        "p99ModelExecutionMs": 45.0, "maxModelExecutionMs": 50.0,
        "frameGapCount": 0, "fallbackCount": 0, "guardViolationCount": 0,
        "telemetryStateFresh": True if on else None,
        "hardwareSamples": 10 if on else 0,
        "hardwareValidSamples": 10 if on else 0,
        "hardwareErrorSamples": 0, "supplyFaultSamples": 0,
        "p95HardwareSampleDurationMs": 50.0 if on else 0.0,
        "maxHardwareSampleDurationMs": 80.0 if on else 0.0,
        "maxGpuTempC": 60.0 if on else None,
        "maxMemoryTempC": 65.0 if on else None,
        "maxPowerDrawW": 100.0 if on else None,
      }
    return {
      "schemaVersion": 2, "stage": "S2A_TELEMETRY_ONLY_EVIDENCE", "sourceHead": HEAD, "sourceBranch": BRANCH,
      "legs": {
        "S2A_OFF_BEFORE": leg("S2A_OFF_BEFORE"),
        "S2A_TELEMETRY_ON": leg("S2A_TELEMETRY_ON", True),
        "S2A_OFF_AFTER": leg("S2A_OFF_AFTER"),
      },
      "observerAuthorization": False, "shadowAuthorization": False,
      "controlAuthorization": False, "publicRoadAuthorization": False,
    }

  def s2a_policy(self):
    return {
      "minModelSamplesPerLeg": 100, "minHardwareSamplesOnLeg": 5, "minHardwareValidFraction": 0.8,
      "maxBaselineP95DriftMs": 2.0, "maxBaselineP99DriftMs": 3.0,
      "maxTelemetryP50IncreaseMs": 1.0, "maxTelemetryP95IncreaseMs": 2.0, "maxTelemetryP99IncreaseMs": 3.0,
      "maxAdditionalFrameGaps": 0, "maxFallbacksPerLeg": 0, "maxGuardViolationsPerLeg": 0,
      "maxHardwareErrorSamples": 0, "maxSupplyFaultSamples": 0, "maxP95HardwareSampleDurationMs": 200.0,
      "maxGpuTempC": 90.0, "maxMemoryTempC": 90.0, "maxPowerDrawW": 150.0,
    }

  def test_s1_exact_recompute_binds(self):
    evidence = self.s1_evidence(); policy = self.s1_policy()
    qualification = recompute(evidence, policy, "S1_OBSERVER_QUALIFICATION")
    td, e, p, q = self.write_triplet(evidence, policy, qualification)
    try:
      result = build_binding(e, p, q)
      self.assertTrue(result["recomputedExactMatch"])
      self.assertEqual(result["status"], "PASS")
      self.assertEqual(result["sourceHead"], HEAD)
      self.assertEqual(len(result["evidenceSha256"]), 64)
    finally:
      td.cleanup()

  def test_tampered_qualification_rejected(self):
    evidence = self.s1_evidence(); policy = self.s1_policy()
    qualification = recompute(evidence, policy, "S1_OBSERVER_QUALIFICATION")
    qualification["status"] = "FAIL"
    td, e, p, q = self.write_triplet(evidence, policy, qualification)
    try:
      with self.assertRaises(ValueError):
        build_binding(e, p, q)
    finally:
      td.cleanup()

  def test_s2a_exact_recompute_binds(self):
    evidence = self.s2a_evidence(); policy = self.s2a_policy()
    qualification = recompute(evidence, policy, "S2A_TELEMETRY_ONLY_QUALIFICATION")
    td, e, p, q = self.write_triplet(evidence, policy, qualification)
    try:
      result = build_binding(e, p, q)
      self.assertEqual(result["qualificationStage"], "S2A_TELEMETRY_ONLY_QUALIFICATION")
      self.assertEqual(result["status"], "PASS")
      self.assertEqual(result["sourceBranch"], BRANCH)
    finally:
      td.cleanup()


if __name__ == "__main__":
  unittest.main()
