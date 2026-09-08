import json
import math
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
import unittest

import numpy as np

from openpilot.selfdrive.carrot.radar_motion.primary import RadarPointSnapshot, VisionLead
from openpilot.selfdrive.carrot.radar_motion.trajectory_cutout import TrajectoryCutOutTracker
from openpilot.selfdrive.controls.lib.longitudinal_cutout import cutout_obstacle_relief


PATH = ((0.0, 0.0), (100.0, 0.0))
POINT = RadarPointSnapshot(50, "frontRadar", 25.0, 0.0, -1.0, 0.0, 0.0, 14.0, -0.5, 0.0, True, 2)
VISION = VisionLead(0.99, 25.0, 0.0, 14.0, 1.0, 0.2, 1.0)
FIXTURE = Path(__file__).resolve().parents[2] / "carrot/tests/fixtures/cutout_departure.json"


def step(tracker, t, y, *, side=1, vision_far=True, point_changes=None, **kwargs):
  p = replace(POINT, d_rel=25.0-t, y_rel=side*y, **(point_changes or {}))
  v = replace(VISION, d_rel=60.0 if vision_far else p.d_rel, y_rel=side*y)
  return tracker.update(t, p, p, kwargs.pop("vision", v), kwargs.pop("path", PATH),
                        kwargs.pop("v_ego", 15.0), kwargs.pop("yaw_rate", 0.0))


def seed(tracker, side=1):
  for i in range(11):
    if step(tracker, i*.05, 0.0, side=side, vision_far=False).confidence != 0.0:
      raise AssertionError("seed unexpectedly activated cut-out")


def depart(tracker, side=1, **kwargs):
  result = None
  for i in range(1, 17):
    result = step(tracker, .5+i*.05, i*.075, side=side, **kwargs)
  return result


def lead(**changes):
  values = {"status": True, "radar": True, "radarTrackId": 50, "dRel": 25.0, "vRel": -1.0,
            "vLead": 14.0, "aLeadK": -.5, "cutOutTime": 1.0, "cutOutConfidence": 1.0}
  return SimpleNamespace(**(values | changes))


class TestCarrotCutoutPurePython(unittest.TestCase):
  def test_confirmed_departure_both_sides(self):
    for side in (-1, 1):
      with self.subTest(side=side):
        tracker = TrajectoryCutOutTracker()
        seed(tracker, side)
        prediction = depart(tracker, side)
        self.assertAlmostEqual(prediction.confidence, 1.0, places=7)
        self.assertAlmostEqual(prediction.time_s, (2.15-1.2)/1.5, places=7)

  def test_departure_requires_independent_evidence(self):
    cases = {
      "unseeded": {},
      "vision_stays": {"vision_far": False},
      "weak_vision": {"vision": replace(VISION, probability=.1, d_rel=60)},
      "nearer_vision": {"vision": replace(VISION, d_rel=10)},
      "uncertain_range": {"vision": replace(VISION, d_rel=30, x_std=20)},
      "turn": {"yaw_rate": .03},
      "path_shift": {"path": ((0.0, 1.0), (100.0, 1.0))},
      "unmeasured": {"point_changes": {"measured": False}},
      "tentative": {"point_changes": {"radar_track_state": 1}},
      "stopped": {"point_changes": {"v_lead": 0.0}},
      "low_ego": {"v_ego": 3.0},
      "scc": {"point_changes": {"source": "scc"}},
    }
    for name, kwargs in cases.items():
      with self.subTest(name=name):
        tracker = TrajectoryCutOutTracker()
        if name != "unseeded":
          seed(tracker)
        self.assertEqual(depart(tracker, **kwargs).confidence, 0.0)

  def test_active_departure_revokes_on_changed_evidence(self):
    for change in ("new_id", "gap", "reverse_time", "jump", "reentry", "stopped_lateral", "vision_returns", "invalid_path"):
      with self.subTest(change=change):
        tracker = TrajectoryCutOutTracker()
        seed(tracker)
        self.assertGreater(depart(tracker).confidence, 0.0)
        kwargs = {}
        t, y = 1.35, 1.275
        if change == "new_id": kwargs = {"point_changes": {"track_id": 51}}
        elif change == "gap": t = 1.6
        elif change == "reverse_time": t = 1.3
        elif change == "jump": y = 2.0
        elif change == "reentry": y = 1.1
        elif change == "vision_returns": kwargs = {"vision_far": False}
        elif change == "invalid_path": kwargs = {"path": ()}
        if change == "stopped_lateral":
          result = None
          for i in range(1, 5): result = step(tracker, 1.3+i*.05, 1.2)
        else:
          result = step(tracker, t, y, **kwargs)
        self.assertEqual(result.confidence, 0.0)

  def test_jitter_and_path_motion_alone_do_not_trigger(self):
    tracker = TrajectoryCutOutTracker(); seed(tracker)
    for i in range(1, 21):
      self.assertEqual(step(tracker, .5+i*.05, .5 + .12*(-1)**i).confidence, 0.0)
    tracker.reset(); seed(tracker)
    for i in range(1, 21):
      offset = -i*.05
      self.assertEqual(step(tracker, .5+i*.05, 0, path=((0., offset), (100., offset))).confidence, 0.0)

  def test_relief_preserves_preclearance_caps_and_input(self):
    times = np.array([0., .5, 1., 1.3, 1.5, 1.8, 3., 10.])
    target = lead(); before = vars(target).copy()
    relief = cutout_obstacle_relief(target, 15., times, 1.45, 6.)
    np.testing.assert_array_equal(relief[:4], np.zeros(4))
    self.assertAlmostEqual(float(relief[4]), 3.0, places=7)
    self.assertAlmostEqual(float(relief[-1]), 7.5, places=7)
    self.assertEqual(vars(target), before)
    self.assertAlmostEqual(float(cutout_obstacle_relief(target, 15., times, .4, 6.)[-1]), 3.0, places=7)
    np.testing.assert_allclose(cutout_obstacle_relief(lead(cutOutConfidence=.25), 15., times, 1.45, 6.), relief*.25)

  def test_relief_rejects_invalid_or_closing_risk_and_old_messages(self):
    invalid = (
      {"status": False}, {"radar": False}, {"radarTrackId": -1}, {"cutOutTime": 0.}, {"cutOutTime": 3.},
      {"cutOutTime": float("nan")}, {"cutOutConfidence": float("inf")}, {"cutOutConfidence": -1.},
      {"dRel": 8., "vRel": -4.}, {"vLead": 0.}, {"aLeadK": -8.},
    )
    for changes in invalid:
      with self.subTest(changes=changes):
        np.testing.assert_array_equal(cutout_obstacle_relief(lead(**changes), 15., np.array([0., 2., 5.]), 1.45, 6.), 0.)
    old = lead(); del old.cutOutTime; del old.cutOutConfidence
    for target, stopping in ((old, 6.), (lead(), 25.)):
      self.assertFalse(cutout_obstacle_relief(target, 15., np.array([0., 3., 5.]), 1.45, stopping).any())

  def test_corner_lateral_identity_and_measured_fixture(self):
    tracker = TrajectoryCutOutTracker(); results = []
    for i in range(31):
      t = i*.05; y = max(0., t-.5)*1.2
      p = replace(POINT, d_rel=25-t, y_rel=round(y/.3)*.3)
      corner = replace(p, source="corner235", track_id=1356, y_rel=y)
      v = replace(VISION, d_rel=p.d_rel if t <= .5 else 60.)
      results.append(tracker.update(t, p, corner, v, PATH, 15., 0.))
    self.assertAlmostEqual(results[-1].confidence, 1.0, places=7)
    self.assertEqual(tracker.update(1.55, p, p, v, PATH, 15., 0.).confidence, 0.0)

    tracker = TrajectoryCutOutTracker(); active = []
    for sample in json.loads(FIXTURE.read_text(encoding="utf-8"))["samples"]:
      sample["point"] = RadarPointSnapshot(**sample["point"]) if sample["point"] else None
      sample["lateral"] = RadarPointSnapshot(**sample["lateral"]) if sample["lateral"] else None
      sample["vision"] = VisionLead(**sample["vision"]) if sample["vision"] else None
      output = tracker.update(**sample)
      if output.confidence:
        active.append((sample["time_s"], output))
    self.assertTrue(active)
    self.assertTrue(math.isclose(active[0][0], 11.40344, abs_tol=.001))
    self.assertTrue(math.isclose(active[0][1].time_s, .688, abs_tol=.01))
    self.assertGreater(active[0][1].confidence, .1)
    self.assertLess(active[0][1].confidence, .2)


if __name__ == "__main__":
  unittest.main()
