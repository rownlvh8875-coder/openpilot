#!/usr/bin/env python3
from __future__ import annotations

import unittest

from tools.egpu_integrated_review_queue import build_review_queue


HEAD = "a" * 40
BRANCH = "carrot-wip-integrated-v6"


def event(frame: int, bucket: str, fingerprint: str, *, temporal=None, hard=None, cutout_active=False):
  return {
    "stage": "OFFLINE_GUARDIAN_REPLAY",
    "sourceHead": HEAD,
    "sourceBranch": BRANCH,
    "frameId": frame,
    "combinedReviewBucket": bucket,
    "guardian": {
      "reviewFingerprint": fingerprint,
      "hardIssues": hard or [],
      "reviewIssues": [],
      "sceneTags": ["lead_present"] + (["lead_cutout_predicted"] if cutout_active else []),
      "temporalTags": temporal or [],
      "carrotCutoutContext": {"active": cutout_active, "timeS": 0.7 if cutout_active else 0.0, "confidence": 0.4 if cutout_active else 0.0},
    },
    "fault": None,
  }


class TestReviewQueue(unittest.TestCase):
  def test_groups_same_fingerprint_and_orders_by_bucket(self):
    report = build_review_queue([
      event(20, "REVIEW", "bbb"),
      event(10, "ROOT_CAUSE", "aaa", hard=["frame_mismatch"]),
      event(11, "ROOT_CAUSE", "aaa", hard=["frame_mismatch"]),
      event(30, "PRIORITY_REVIEW", "ccc", temporal=["lead_cutout_prediction_onset"], cutout_active=True),
      event(40, "OBSERVE", "ddd"),
    ])
    self.assertEqual([x["bucket"] for x in report["queue"]], ["ROOT_CAUSE", "PRIORITY_REVIEW", "REVIEW"])
    self.assertEqual(report["queue"][0]["count"], 2)
    self.assertEqual(report["queue"][0]["firstFrameId"], 10)
    self.assertEqual(report["queue"][0]["lastFrameId"], 11)
    self.assertEqual(report["queue"][1]["carrotCutoutActiveEvents"], 1)
    self.assertFalse(report["controlAuthorization"])

  def test_mixed_source_is_rejected(self):
    first = event(1, "REVIEW", "x")
    second = event(2, "REVIEW", "y")
    second["sourceHead"] = "b" * 40
    with self.assertRaises(ValueError):
      build_review_queue([first, second])

  def test_observe_can_be_included_explicitly(self):
    report = build_review_queue([event(1, "OBSERVE", "x")], include_observe=True)
    self.assertEqual(report["groups"], 1)
    self.assertEqual(report["queue"][0]["bucket"], "OBSERVE")
    self.assertFalse(report["queue"][0]["humanReviewRequired"])


if __name__ == "__main__":
  unittest.main()
