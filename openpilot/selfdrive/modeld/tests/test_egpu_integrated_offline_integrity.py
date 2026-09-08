"""Regression checks for evidence integrity; no hardware or runtime imports."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
import json
import unittest

from openpilot.selfdrive.modeld.egpu_integrated_fault_state import FaultObservation, FaultSequenceTracker, assess_fault_observation
from openpilot.selfdrive.modeld.egpu_integrated_guardian import ActionSnapshot
from openpilot.selfdrive.modeld.egpu_integrated_guardian_temporal import GuardianTemporalTracker, SceneContext, TemporalHeuristicPolicy
from tools.egpu_integrated_guardian_replay import analyze_rows
from tools.egpu_integrated_review_queue import build_review_queue


HEAD = "a" * 40
BRANCH = "carrot-wip-integrated-v6"


def replay_row(frame=1, timestamp=1.0):
  action = {
    "frameId": frame, "frameAge": 0, "modelExecutionMs": 30.0, "curvature": 0.001,
    "acceleration": -0.2, "shouldStop": False, "backend": "egpu", "timestampMonoS": timestamp,
  }
  return {
    "sourceHead": HEAD, "sourceBranch": BRANCH, "active": action,
    "shadow": {**action, "backend": "qcom"},
    "scene": {
      "frameId": frame, "timestampMonoS": timestamp, "speedMps": 10.0, "standstill": False,
      "leadPresent": True, "leadDistanceM": 25.0, "leadRelSpeedMps": 0.0,
      "leadCutOutTimeS": 0.0, "leadCutOutConfidence": 0.0,
    },
    "hardware": {"valid": True, "timestampMonoS": timestamp, "supplyFault": False},
  }


def observe_scene(tracker, scene):
  active = ActionSnapshot(scene.frame_id, 0, 30.0, 0.001, -0.2, False, "egpu", scene.timestamp_mono_s)
  return tracker.observe(active=active, shadow=replace(active, backend="qcom"), scene=scene)


class TestFaultEvidenceIntegrity(unittest.TestCase):
  def test_output_presence_without_identity_is_not_same_frame_proof(self):
    result = assess_fault_observation(FaultObservation(1, "egpu", "qcom", False, True, fallback_observed=True))
    self.assertIsNone(result.same_frame_output_preserved)
    self.assertIn("same_frame_output_identity_unavailable", result.review_issues)
    self.assertIn("same_frame_output_finiteness_unavailable", result.review_issues)

  def test_startup_latch_and_invalid_fallback_both_prevent_reentry(self):
    for initial in (
      FaultObservation(1, "qcom", "qcom", False, True),
      FaultObservation(1, "egpu", "qcom", False, True, fallback_observed=True, model_output_present=False),
      FaultObservation(1, "qcom", "qcom", False, False),
    ):
      with self.subTest(initial=initial):
        tracker = FaultSequenceTracker()
        tracker.observe(initial)
        result = tracker.observe(FaultObservation(2, "egpu", "egpu", True, False))
        self.assertIn("unexpected_big_reentry_without_restart_boundary", result["hardIssues"])
        tracker.reset()
        restarted = tracker.observe(FaultObservation(1, "egpu", "egpu", True, False))
        self.assertEqual(restarted["hardIssues"], [])

  def test_clearing_latch_while_still_qcom_is_detected(self):
    tracker = FaultSequenceTracker()
    tracker.observe(FaultObservation(1, "qcom", "qcom", False, True))
    result = tracker.observe(FaultObservation(2, "qcom", "qcom", False, False))
    self.assertIn("startup_failed_latch_cleared_without_restart", result["hardIssues"])

  def test_invalid_startup_output_is_inconsistent(self):
    result = assess_fault_observation(FaultObservation(1, "qcom", "qcom", False, False, model_output_present=False))
    self.assertEqual(result.state.value, "INCONSISTENT")
    self.assertIn("model_output_missing", result.hard_issues)

  def test_output_proof_does_not_erase_inconsistent_latch(self):
    result = assess_fault_observation(FaultObservation(
      1, "egpu", "qcom", False, False, fallback_observed=True, output_frame_id=1, model_output_finite=True,
    ))
    self.assertIs(result.same_frame_output_preserved, True)
    self.assertEqual(result.state.value, "INCONSISTENT")
    self.assertIn("fallback_without_startup_failed_latch", result.hard_issues)


class TestTemporalEvidenceIntegrity(unittest.TestCase):
  def test_frame_gap_cannot_establish_lead_or_cutout_onset(self):
    tracker = GuardianTemporalTracker()
    previous = SceneContext(1, 1.0, 10.0, False, False, lead_cutout_time_s=0.0, lead_cutout_confidence=0.0)
    observe_scene(tracker, previous)
    current = replace(previous, frame_id=3, timestamp_mono_s=1.1, lead_present=True, lead_distance_m=10.0,
                      lead_cutout_time_s=0.7, lead_cutout_confidence=0.4)
    result = observe_scene(tracker, current)
    self.assertEqual(result["temporalTags"], ["scene_frame_gap"])
    self.assertIn("lead_cutout_predicted", result["sceneTags"])

  def test_invalid_or_missing_cutout_cannot_establish_resolution(self):
    for invalid in (None, float("nan"), float("inf"), -1.0):
      with self.subTest(invalid=invalid):
        tracker = GuardianTemporalTracker()
        previous = SceneContext(1, 1.0, 10.0, False, True, 25.0, 0.0,
                                lead_cutout_time_s=0.7, lead_cutout_confidence=0.4)
        observe_scene(tracker, previous)
        result = observe_scene(tracker, replace(previous, frame_id=2, timestamp_mono_s=1.05, lead_cutout_time_s=invalid))
        self.assertNotIn("lead_cutout_prediction_resolved", result["temporalTags"])
        json.dumps(result, allow_nan=False)

  def test_invalid_scene_does_not_seed_later_transition(self):
    tracker = GuardianTemporalTracker()
    invalid = SceneContext(1, float("nan"), 10.0, False, False)
    observe_scene(tracker, invalid)
    result = observe_scene(tracker, replace(invalid, frame_id=2, timestamp_mono_s=2.0, lead_present=True, lead_distance_m=10.0))
    self.assertNotIn("lead_acquired", result["temporalTags"])

  def test_nonfinite_policy_is_rejected_and_existing_speed_heuristics_are_visible(self):
    with self.assertRaises(ValueError):
      GuardianTemporalTracker(TemporalHeuristicPolicy(close_lead_m=float("nan")))
    events, _ = analyze_rows([replay_row()], expected_source_head=HEAD)
    policy = events[0]["guardian"]["temporalPolicy"]
    self.assertEqual(policy["standstill_speed_mps"], 0.3)
    self.assertEqual(policy["creep_speed_mps"], 3.0)

  def test_action_clock_regression_is_independent_of_scene_clock(self):
    rows = [replay_row(1, 1.0), replay_row(2, 2.0)]
    rows[1]["active"]["timestampMonoS"] = 0.5
    events, _ = analyze_rows(rows, expected_source_head=HEAD)
    self.assertIn("active_timestamp_not_monotonic", events[1]["guardian"]["hardIssues"])

  def test_finite_action_overflow_does_not_break_json_output(self):
    row = replay_row()
    row["active"]["acceleration"] = 1e308
    row["shadow"]["acceleration"] = -1e308
    events, summary = analyze_rows([row], expected_source_head=HEAD)
    self.assertIn("disagreement_nonfinite", events[0]["guardian"]["hardIssues"])
    json.dumps([events, summary], allow_nan=False)


class TestReplayEvidenceIntegrity(unittest.TestCase):
  def test_primitive_coercions_are_rejected(self):
    for section, name, value in (
      ("active", "shouldStop", "false"), ("active", "frameId", 1.5), ("active", "frameId", True),
      ("active", "modelExecutionMs", "30"), ("scene", "leadPresent", "true"),
      ("hardware", "supplyFault", "false"),
    ):
      with self.subTest(section=section, name=name, value=value):
        row = replay_row()
        row[section][name] = value
        with self.assertRaises(ValueError):
          analyze_rows([row], expected_source_head=HEAD)

  def test_empty_source_cannot_authorize_empty_rows(self):
    with self.assertRaises(ValueError):
      analyze_rows([], expected_source_head="", expected_source_branch="")

  def test_unknown_fields_and_missing_required_fields_are_rejected(self):
    for section in (None, "active", "shadow", "scene", "fault"):
      row = replay_row()
      row["fault"] = {
        "frameId": 1, "attemptedBackend": "egpu", "activeBackend": "egpu", "usbGpuActive": True, "startupFailed": False,
      }
      target = row if section is None else row[section]
      target["unboundSourceHead"] = "b" * 40
      with self.subTest(section=section), self.assertRaises(ValueError):
        analyze_rows([row], expected_source_head=HEAD)
    row = replay_row()
    del row["active"]["timestampMonoS"]
    with self.assertRaises(ValueError):
      analyze_rows([row], expected_source_head=HEAD)

  def test_restart_must_have_new_consistent_process_identity(self):
    bad_sequences = []
    for process_id, boundary in ((None, True), ("one", True), ("two", False)):
      first, second = replay_row(1, 1.0), replay_row(2, 2.0)
      first["processId"] = "one"
      second["restartBoundary"] = boundary
      if process_id is not None:
        second["processId"] = process_id
      bad_sequences.append([first, second])
    reused = []
    for index, process_id in enumerate(("one", "two", "one")):
      row = replay_row(1, 1.0)
      row.update(processId=process_id, restartBoundary=index > 0)
      reused.append(row)
    bad_sequences.append(reused)
    for rows in bad_sequences:
      with self.subTest(rows=rows), self.assertRaises(ValueError):
        analyze_rows(rows, expected_source_head=HEAD)

  def test_incoherent_fault_cannot_poison_later_process_history(self):
    first, second = replay_row(1, 1.0), replay_row(2, 2.0)
    first["fault"] = {
      "frameId": 1, "attemptedBackend": "egpu", "activeBackend": "qcom", "usbGpuActive": False,
      "startupFailed": True, "fallbackObserved": True,
    }
    second["fault"] = {
      "frameId": 2, "attemptedBackend": "egpu", "activeBackend": "egpu", "usbGpuActive": True, "startupFailed": False,
    }
    events, _ = analyze_rows([first, second], expected_source_head=HEAD)
    self.assertFalse(events[0]["fault"]["evidenceCoherent"])
    self.assertEqual(events[1]["fault"]["hardIssues"], [])

  def test_stale_active_snapshot_cannot_preserve_fallback_without_fault_age(self):
    row = replay_row()
    row["active"].update(backend="qcom", frameAge=1)
    row["fault"] = {
      "frameId": 1, "attemptedBackend": "egpu", "activeBackend": "qcom", "usbGpuActive": False,
      "startupFailed": True, "fallbackObserved": True, "outputFrameId": 1, "modelOutputFinite": True,
    }
    events, _ = analyze_rows([row], expected_source_head=HEAD)
    self.assertIn("same_frame_output_stale", events[0]["fault"]["hardIssues"])
    self.assertIs(events[0]["fault"]["sameFrameOutputPreserved"], False)


class TestReviewGroupingIntegrity(unittest.TestCase):
  def event(self, frame, fault_issue, process_id=None):
    events, _ = analyze_rows([replay_row(frame, float(frame))], expected_source_head=HEAD)
    event = events[0]
    event.update(combinedReviewBucket="ROOT_CAUSE", processId=process_id)
    event["fault"] = {"state": "INCONSISTENT", "hardIssues": [fault_issue], "reviewIssues": [], "temporalTags": []}
    return event

  def test_same_guardian_fingerprint_does_not_merge_distinct_faults(self):
    report = build_review_queue([self.event(1, "output_missing"), self.event(2, "latch_cleared")])
    self.assertEqual(report["groups"], 2)

  def test_grouping_and_representative_are_input_order_independent(self):
    events = [self.event(2, "missing", "z"), self.event(1, "missing", "b"), self.event(1, "missing", "a")]
    report = build_review_queue(events)
    self.assertEqual(report, build_review_queue(list(reversed(events))))
    self.assertEqual(report["groups"], 1)
    group = report["queue"][0]
    self.assertEqual(group["representativeFrameId"], 1)
    self.assertEqual(group["representativeProcessId"], "a")

  def test_fault_transition_and_guardian_category_are_part_of_fingerprint(self):
    first = self.event(1, "missing")
    second = deepcopy(first)
    second["frameId"] = 2
    second["fault"]["temporalTags"] = ["runtime_fallback_onset"]
    third = deepcopy(first)
    third["frameId"] = 3
    third["guardian"]["hardIssues"] = ["scene_frame_invalid"]
    self.assertEqual(build_review_queue([first, second, third])["groups"], 3)

  def test_missing_or_downgraded_bucket_cannot_hide_hard_fault(self):
    for bucket in (None, "OBSERVE", "REVIEW"):
      event = self.event(1, "missing")
      event["combinedReviewBucket"] = bucket
      with self.subTest(bucket=bucket), self.assertRaises(ValueError):
        build_review_queue([event])


if __name__ == "__main__":
  unittest.main()
