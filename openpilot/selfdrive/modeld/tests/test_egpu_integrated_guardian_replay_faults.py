#!/usr/bin/env python3
from __future__ import annotations

import json
import unittest

from openpilot.selfdrive.modeld.egpu_integrated_fault_state import FaultObservation, FaultSequenceTracker, FaultState, assess_fault_observation
from openpilot.selfdrive.modeld.egpu_integrated_guardian import ActionSnapshot
from openpilot.selfdrive.modeld.egpu_integrated_guardian_temporal import GuardianTemporalTracker, SceneContext
from tools.egpu_integrated_fault_injection import SCENARIOS, TEST_GUARDIAN_POLICY, run_scenario
from tools.egpu_integrated_guardian_replay import analyze_rows


HEAD = "a" * 40
BRANCH = "carrot-wip-integrated-v6"


def action(frame: int, backend: str, *, stop: bool = False, curv: float = 0.001,
           accel: float = -0.2, t: float = 10.0) -> ActionSnapshot:
  return ActionSnapshot(
    frame_id=frame,
    frame_age=0,
    model_execution_ms=30.0,
    curvature=curv,
    acceleration=accel,
    should_stop=stop,
    backend=backend,
    timestamp_mono_s=t,
  )


def scene(frame: int, t: float, *, lead=False, distance=None, rel=None, standstill=False,
          cutout_time=None, cutout_confidence=None) -> SceneContext:
  return SceneContext(
    frame_id=frame,
    timestamp_mono_s=t,
    speed_mps=0.0 if standstill else 10.0,
    standstill=standstill,
    lead_present=lead,
    lead_distance_m=distance,
    lead_rel_speed_mps=rel,
    lead_cutout_time_s=cutout_time,
    lead_cutout_confidence=cutout_confidence,
  )


class TestFaultState(unittest.TestCase):
  def test_reviewed_same_frame_fallback_semantics(self):
    tracker = FaultSequenceTracker()
    first = tracker.observe(FaultObservation(10, "egpu", "egpu", True, False))
    second = tracker.observe(FaultObservation(
      11, "egpu", "qcom", False, True, fallback_observed=True, model_output_present=True,
      output_frame_id=11, model_output_finite=True,
    ))
    third = tracker.observe(FaultObservation(12, "qcom", "qcom", False, True))
    self.assertEqual(first["state"], FaultState.BIG_ACTIVE.value)
    self.assertEqual(second["state"], FaultState.SAME_FRAME_FALLBACK.value)
    self.assertTrue(second["sameFrameOutputPreserved"])
    self.assertIn("runtime_fallback_onset", second["temporalTags"])
    self.assertEqual(third["state"], FaultState.SMALL_LATCHED.value)
    self.assertIn("small_latch_confirmed", third["temporalTags"])
    self.assertFalse(third["retryExpectedThisIgnition"])

  def test_missing_same_frame_output_is_inconsistent(self):
    result = assess_fault_observation(FaultObservation(
      20, "egpu", "qcom", False, True, fallback_observed=True, model_output_present=False,
    ))
    self.assertEqual(result.state, FaultState.INCONSISTENT)
    self.assertIn("same_frame_output_missing", result.hard_issues)

  def test_big_reentry_without_reset_is_hard_issue(self):
    tracker = FaultSequenceTracker()
    tracker.observe(FaultObservation(30, "egpu", "egpu", True, False))
    tracker.observe(FaultObservation(31, "egpu", "qcom", False, True, fallback_observed=True))
    tracker.observe(FaultObservation(32, "qcom", "qcom", False, True))
    result = tracker.observe(FaultObservation(33, "egpu", "egpu", True, False))
    self.assertIn("unexpected_big_reentry_without_restart_boundary", result["hardIssues"])

  def test_latched_small_reason_does_not_overattribute_origin(self):
    result = assess_fault_observation(FaultObservation(40, "qcom", "qcom", False, True))
    self.assertEqual(result.state, FaultState.SMALL_LATCHED)
    self.assertEqual(result.reason, "startup_failed_latched_small")

  def test_fault_injection_suite_core_scenarios(self):
    for name in SCENARIOS:
      with self.subTest(name=name):
        result = run_scenario(name)
        self.assertEqual(result["status"], "PASS", result["reasons"])
        # Replay output containing invalid model/CUT-OUT input must still be
        # suitable for the analyzer's strict JSON evidence writer.
        json.dumps(result, allow_nan=False)
        self.assertFalse(result["hardwareTouched"])
        self.assertFalse(result["controlAuthorization"])
        self.assertFalse(result["publicRoadAuthorization"])

  def test_hardware_degradation_records_review_without_inventing_runtime_switch(self):
    result = run_scenario("usb_pcie_degraded_big_returns")
    self.assertEqual(result["states"], ["BIG_ACTIVE", "BIG_ACTIVE"])
    self.assertEqual(result["hardIssues"], [])
    self.assertIn("pcie_not_ready", result["reviewIssues"])
    self.assertIn("usb_below_superspeed_5g", result["reviewIssues"])
    self.assertFalse(result["events"][-1]["retryExpectedThisIgnition"])

  def test_disconnect_preserves_explicit_same_frame_proof_then_latches(self):
    result = run_scenario("egpu_disconnect_while_active")
    self.assertTrue(result["events"][1]["sameFrameOutputPreserved"])
    self.assertEqual(result["states"][-1], "SMALL_LATCHED")
    self.assertFalse(result["events"][-1]["retryExpectedThisIgnition"])

  def test_stale_mismatched_and_missing_fallback_never_claim_output_preserved(self):
    for name in ("fallback_output_stale_frame", "fallback_output_frame_id_mismatch", "fallback_output_nonfinite", "small_fallback_itself_fails"):
      with self.subTest(name=name):
        result = run_scenario(name)
        failures = [event for event in result["events"] if event["state"] == "INCONSISTENT"]
        self.assertTrue(failures)
        for failure in failures:
          self.assertIsNot(failure["sameFrameOutputPreserved"], True)

  def test_repeated_exception_is_a_latch_violation_without_retry_authority(self):
    result = run_scenario("repeated_big_exception")
    self.assertIn("repeated_big_attempt_while_small_latched", result["events"][-1]["hardIssues"])
    self.assertTrue(all(not event["retryExpectedThisIgnition"] for event in result["events"]))

  def test_clean_process_boundary_allows_big_and_resets_frame_clock_continuity(self):
    result = run_scenario("restart_boundary_big_returns")
    self.assertEqual(result["hardIssues"], [])
    self.assertEqual(result["states"], ["SAME_FRAME_FALLBACK", "SMALL_LATCHED", "BIG_ACTIVE"])
    self.assertTrue(result["events"][-1]["restartBoundary"])
    self.assertNotEqual(result["events"][0]["processId"], result["events"][-1]["processId"])

  def test_replay_fault_classifications_do_not_authorize_controls(self):
    for name in ("active_nan_output", "shadow_inf_output", "stale_hardware_telemetry", "supply_fault_big_output_normal"):
      with self.subTest(name=name):
        result = run_scenario(name)
        event = result["events"][0]
        self.assertEqual(event["combinedReviewBucket"], "ROOT_CAUSE")
        self.assertFalse(event["guardian"]["evidenceEligible"])
        self.assertFalse(event["guardian"]["controlAuthorization"])
        self.assertFalse(event["guardian"]["shadowPublishToControls"])
        self.assertFalse(event["publicRoadAuthorization"])
    self.assertEqual(run_scenario("supply_fault_big_output_normal")["states"], ["BIG_ACTIVE"])

  def test_source_identity_and_observer_backend_mismatches_are_distinct(self):
    for name in ("replay_sourceHead_mismatch", "replay_sourceBranch_mismatch"):
      with self.subTest(name=name):
        result = run_scenario(name)
        self.assertIn("source identity mismatch", result["rejection"])
        self.assertEqual(result["events"], [])
    result = run_scenario("observer_replay_active_backend_mismatch")
    self.assertIsNone(result["rejection"])
    self.assertFalse(result["events"][0]["fault"]["evidenceCoherent"])
    self.assertEqual(result["reviewBuckets"], ["ROOT_CAUSE"])

  def test_cutout_invalid_inputs_have_no_usable_prediction_or_transition(self):
    for name in ("cutout_metadata_invalid", "cutout_metadata_nan_time", "cutout_metadata_nan_confidence", "cutout_metadata_inf_time"):
      with self.subTest(name=name):
        result = run_scenario(name)
        guardian = result["events"][0]["guardian"]
        self.assertFalse(guardian["carrotCutoutContext"]["active"])
        self.assertFalse(guardian["evidenceEligible"])
        self.assertNotIn("lead_cutout_prediction_onset", guardian["temporalTags"])
        self.assertNotIn("lead_cutout_prediction_resolved", guardian["temporalTags"])

  def test_cutout_and_stop_onsets_on_same_frame_are_both_retained(self):
    result = run_scenario("cutout_onset_with_stop_disagreement")
    guardian = result["events"][-1]["guardian"]
    self.assertIn("lead_cutout_prediction_onset", guardian["temporalTags"])
    self.assertIn("stop_disagreement_onset", guardian["temporalTags"])
    self.assertEqual(guardian["reviewBucket"], "PRIORITY_REVIEW")

  def test_replay_policy_thresholds_are_explicit_existing_test_policy(self):
    result = run_scenario("stale_hardware_telemetry")
    self.assertEqual(result["testGuardianPolicy"]["max_hardware_age_s"], TEST_GUARDIAN_POLICY.max_hardware_age_s)
    self.assertEqual(result["policyProvenance"], "existing-research-review-policy-not-vehicle-safety-limits")


class TestTemporalGuardian(unittest.TestCase):
  def test_close_closing_lead_acquisition_with_stop_mismatch_is_priority(self):
    tracker = GuardianTemporalTracker()
    tracker.observe(
      active=action(1, "egpu", stop=False, t=1.0),
      shadow=action(1, "qcom", stop=False, t=1.0),
      scene=scene(1, 1.0, lead=False),
      hardware={"valid": True, "timestampMonoS": 1.0, "supplyFault": False},
    )
    result = tracker.observe(
      active=action(2, "egpu", stop=False, t=1.05),
      shadow=action(2, "qcom", stop=True, t=1.05),
      scene=scene(2, 1.05, lead=True, distance=12.0, rel=-3.0),
      hardware={"valid": True, "timestampMonoS": 1.05, "supplyFault": False},
    )
    self.assertIn("lead_acquired", result["temporalTags"])
    self.assertIn("close_lead_acquisition", result["temporalTags"])
    self.assertIn("cut_in_candidate_heuristic", result["temporalTags"])
    self.assertIn("stop_disagreement_onset", result["temporalTags"])
    self.assertEqual(result["reviewBucket"], "PRIORITY_REVIEW")
    self.assertFalse(result["controlAuthorization"])

  def test_carrot_cutout_onset_with_stop_mismatch_is_priority(self):
    tracker = GuardianTemporalTracker()
    tracker.observe(
      active=action(5, "egpu", stop=False, t=5.0), shadow=action(5, "qcom", stop=False, t=5.0),
      scene=scene(5, 5.0, lead=True, distance=25.0, rel=0.0, cutout_time=0.0, cutout_confidence=0.0), hardware=None,
    )
    result = tracker.observe(
      active=action(6, "egpu", stop=False, t=5.05), shadow=action(6, "qcom", stop=True, t=5.05),
      scene=scene(6, 5.05, lead=True, distance=24.5, rel=0.0, cutout_time=0.7, cutout_confidence=0.35), hardware=None,
    )
    self.assertIn("lead_cutout_predicted", result["sceneTags"])
    self.assertIn("lead_cutout_prediction_onset", result["temporalTags"])
    self.assertEqual(result["carrotCutoutContext"]["timeS"], 0.7)
    self.assertEqual(result["reviewBucket"], "PRIORITY_REVIEW")

  def test_carrot_cutout_resolved_is_recorded(self):
    tracker = GuardianTemporalTracker()
    tracker.observe(
      active=action(7, "egpu", t=7.0), shadow=action(7, "qcom", t=7.0),
      scene=scene(7, 7.0, lead=True, distance=25.0, rel=0.0, cutout_time=0.6, cutout_confidence=0.5), hardware=None,
    )
    result = tracker.observe(
      active=action(8, "egpu", t=7.05), shadow=action(8, "qcom", t=7.05),
      scene=scene(8, 7.05, lead=True, distance=24.8, rel=0.0, cutout_time=0.0, cutout_confidence=0.0), hardware=None,
    )
    self.assertIn("lead_cutout_prediction_resolved", result["temporalTags"])

  def test_invalid_cutout_metadata_is_root_cause(self):
    tracker = GuardianTemporalTracker()
    result = tracker.observe(
      active=action(9, "egpu", t=9.0), shadow=action(9, "qcom", t=9.0),
      scene=scene(9, 9.0, lead=True, distance=20.0, rel=0.0, cutout_time=-0.1, cutout_confidence=1.2), hardware=None,
    )
    self.assertIn("lead_cutout_time_negative", result["hardIssues"])
    self.assertIn("lead_cutout_confidence_out_of_range", result["hardIssues"])
    self.assertEqual(result["reviewBucket"], "ROOT_CAUSE")

  def test_timestamp_regression_holds_evidence(self):
    tracker = GuardianTemporalTracker()
    tracker.observe(
      active=action(10, "egpu", t=10.0), shadow=action(10, "qcom", t=10.0),
      scene=scene(10, 10.0, lead=False), hardware={"valid": True, "timestampMonoS": 10.0, "supplyFault": False},
    )
    result = tracker.observe(
      active=action(11, "egpu", t=9.0), shadow=action(11, "qcom", t=9.0),
      scene=scene(11, 9.0, lead=False), hardware={"valid": True, "timestampMonoS": 9.0, "supplyFault": False},
    )
    self.assertIn("scene_timestamp_not_monotonic", result["hardIssues"])
    self.assertEqual(result["reviewBucket"], "ROOT_CAUSE")

  def test_stop_disagreement_resolved_is_recorded(self):
    tracker = GuardianTemporalTracker()
    tracker.observe(
      active=action(20, "egpu", stop=False, t=20.0), shadow=action(20, "qcom", stop=True, t=20.0),
      scene=scene(20, 20.0, lead=True, distance=20.0, rel=0.0), hardware=None,
    )
    result = tracker.observe(
      active=action(21, "egpu", stop=False, t=20.05), shadow=action(21, "qcom", stop=False, t=20.05),
      scene=scene(21, 20.05, lead=True, distance=19.5, rel=0.0), hardware=None,
    )
    self.assertIn("stop_disagreement_resolved", result["temporalTags"])


class TestGuardianReplay(unittest.TestCase):
  def row(self, frame: int, t: float, *, lead: bool, stop_shadow: bool = False,
          active_backend: str = "egpu", fault: dict | None = None, cutout_time=None, cutout_confidence=None):
    row = {
      "sourceHead": HEAD,
      "sourceBranch": BRANCH,
      "active": {
        "frameId": frame, "frameAge": 0, "modelExecutionMs": 30.0, "curvature": 0.001,
        "acceleration": -0.2, "shouldStop": False, "backend": active_backend, "timestampMonoS": t,
      },
      "shadow": {
        "frameId": frame, "frameAge": 0, "modelExecutionMs": 31.0, "curvature": 0.001,
        "acceleration": -0.2, "shouldStop": stop_shadow, "backend": "qcom", "timestampMonoS": t,
      },
      "scene": {
        "frameId": frame, "timestampMonoS": t, "speedMps": 10.0, "standstill": False,
        "leadPresent": lead, "leadDistanceM": 12.0 if lead else None, "leadRelSpeedMps": -3.0 if lead else None,
        "leadCutOutTimeS": cutout_time, "leadCutOutConfidence": cutout_confidence,
      },
      "hardware": {"valid": True, "timestampMonoS": t, "supplyFault": False},
    }
    if fault is not None:
      row["fault"] = fault
    return row

  def test_source_mismatch_is_rejected(self):
    row = self.row(1, 1.0, lead=False)
    row["sourceHead"] = "b" * 40
    with self.assertRaises(ValueError):
      analyze_rows([row], expected_source_head=HEAD)

  def test_fault_guardian_backend_mismatch_is_root_cause(self):
    row = self.row(1, 1.0, lead=False, active_backend="egpu", fault={
      "frameId": 1, "attemptedBackend": "egpu", "activeBackend": "qcom",
      "usbGpuActive": False, "startupFailed": True, "fallbackObserved": True,
    })
    events, summary = analyze_rows([row], expected_source_head=HEAD)
    self.assertIn("fault_guardian_active_backend_mismatch", events[0]["fault"]["hardIssues"])
    self.assertEqual(events[0]["combinedReviewBucket"], "ROOT_CAUSE")
    self.assertEqual(summary["reviewBuckets"].get("ROOT_CAUSE"), 1)

  def test_summary_surfaces_temporal_cutout_and_fault_state(self):
    rows = [
      self.row(1, 1.0, lead=False, active_backend="egpu", cutout_time=0.0, cutout_confidence=0.0, fault={
        "frameId": 1, "attemptedBackend": "egpu", "activeBackend": "egpu", "usbGpuActive": True, "startupFailed": False,
      }),
      self.row(2, 1.05, lead=True, stop_shadow=True, active_backend="qcom", cutout_time=0.7, cutout_confidence=0.4, fault={
        "frameId": 2, "attemptedBackend": "egpu", "activeBackend": "qcom", "usbGpuActive": False,
        "startupFailed": True, "fallbackObserved": True, "modelOutputPresent": True,
      }),
      self.row(3, 1.10, lead=True, active_backend="qcom", cutout_time=0.0, cutout_confidence=0.0, fault={
        "frameId": 3, "attemptedBackend": "qcom", "activeBackend": "qcom", "usbGpuActive": False, "startupFailed": True,
      }),
    ]
    events, summary = analyze_rows(rows, expected_source_head=HEAD)
    self.assertEqual(len(events), 3)
    self.assertGreaterEqual(summary["reviewBuckets"].get("PRIORITY_REVIEW", 0), 1)
    self.assertEqual(summary["faultStateCounts"].get("SAME_FRAME_FALLBACK"), 1)
    self.assertEqual(summary["faultStateCounts"].get("SMALL_LATCHED"), 1)
    self.assertGreaterEqual(summary["temporalTagCounts"].get("lead_acquired", 0), 1)
    self.assertGreaterEqual(summary["temporalTagCounts"].get("lead_cutout_prediction_onset", 0), 1)
    self.assertGreaterEqual(summary["temporalTagCounts"].get("lead_cutout_prediction_resolved", 0), 1)
    self.assertFalse(summary["controlAuthorization"])


if __name__ == "__main__":
  unittest.main()
