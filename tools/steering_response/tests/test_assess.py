import sys
import unittest
from pathlib import Path
import numpy as np
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import assess


class AssessTests(unittest.TestCase):
  def test_chronological_disjoint_split(self):
    w = {'sg': np.array([0, 45, 46, 68, 69, 104]), 't': np.arange(6)}
    split = assess.split_windows(w, 45, 68)
    self.assertEqual(split['train']['sg'].tolist(), [0, 45])
    self.assertEqual(split['validation']['sg'].tolist(), [46, 68])
    self.assertEqual(split['test']['sg'].tolist(), [69, 104])
    with self.assertRaises(ValueError):
      assess.split_windows(w, 68, 45)

  def test_constant_training_range_detects_out_of_support(self):
    self.assertEqual(assess.outside_range(np.array([10, 10]), np.array([9, 10, 11])), 2 / 3)

  def test_unused_input_margins_do_not_expand_training_support(self):
    train = {'v': np.full((2, 121), 15.), 'u': np.zeros((2, 121))}
    test = {'v': np.full((2, 121), 30.), 'u': np.zeros((2, 121))}
    train['v'][:, 111:] = 40
    a = assess.input_coverage(train, test, 0)
    train['v'][:, 111:] = 15
    b = assess.input_coverage(train, test, 0)
    self.assertEqual(a, b)
    self.assertEqual(a['outside_training_speed_fraction'], 1)


if __name__ == '__main__':
  unittest.main()
