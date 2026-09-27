import sys
import unittest
import io
import json
import tempfile
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch
import numpy as np
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import assess
from tools.steering_response.tests.test_model import anchored_synthetic


def anchored_rows():
  windows = anchored_synthetic(25, 18)
  blocks = []
  for index in range(18):
    rows = np.zeros((121, 8))
    rows[:, 0] = index * 7 + np.arange(121) * .05
    rows[:, 1] = index
    rows[:, 2] = windows['v'][index]
    rows[:, 3] = windows['y'][index, 0]
    rows[10:111, 3] = windows['y'][index]
    rows[:, 4] = windows['u'][index]
    rows[:, 5] = windows['roll'][index]
    rows[:, 6] = 1
    blocks.append(rows)
  return np.concatenate(blocks)


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

  def test_anchored_coverage_includes_anchor_but_absolute_does_not(self):
    for delay in (0, 3, 10):
      with self.subTest(delay=delay):
        train = {'v': np.full((2, 121), 15.), 'u': np.zeros((2, 121))}
        test = {'v': np.full((2, 121), 15.), 'u': np.zeros((2, 121))}
        test['v'][:, 10 - delay] = 30
        test['u'][:, 10 - delay] = .8
        absolute = assess.input_coverage(train, test, delay)
        anchored = assess.input_coverage(train, test, delay, response_mode='anchored')
        self.assertEqual(absolute['outside_training_speed_fraction'], 0)
        self.assertEqual(anchored['speed_range_mps'], [15., 30.])
        self.assertEqual(anchored['command_range'], [0., .8])
        self.assertAlmostEqual(anchored['outside_training_speed_fraction'], 1 / 101)
        self.assertAlmostEqual(anchored['outside_training_command_fraction'], 1 / 101)
        test['v'][:, 111 - delay:] = 40
        self.assertEqual(anchored, assess.input_coverage(train, test, delay, response_mode='anchored'))

  def test_assessment_uses_anchored_mode_for_command_and_ablation(self):
    result = assess.assess_rows(anchored_rows(), 5, 11, response_mode='anchored')
    self.assertEqual(result['response_mode'], 'anchored')
    self.assertEqual(result['model']['response_mode'], 'anchored')
    self.assertEqual(result['no_command_model']['response_mode'], 'anchored')
    self.assertLess(result['metrics']['test']['command']['rmse_deg'], 1e-6)
    self.assertGreater(result['metrics']['test']['no_command']['rmse_deg'], .1)
    self.assertEqual(result['controller_comparison'], 'NOT_VALIDATED')

  def test_insufficient_data_records_mode_and_unknown_mode_is_rejected(self):
    result = assess.assess_rows(np.empty((0, 8)), 5, 11, response_mode='anchored')
    self.assertEqual(result['response_mode'], 'anchored')
    self.assertEqual(result['status'], 'INSUFFICIENT_WINDOWS')
    with self.assertRaisesRegex(ValueError, 'response_mode'):
      assess.assess_rows(np.empty((0, 8)), 5, 11, response_mode='unknown')

  def test_cli_serializes_selected_response_mode(self):
    # Only decoding private logs is replaced; extraction into windows, fitting,
    # assessment and the JSON-writing CLI all execute normally.
    with tempfile.TemporaryDirectory() as directory:
      output = Path(directory) / 'result.json'
      argv = ['assess.py', '--logs', directory, '--source', directory, '--output', str(output),
              '--train-end', '5', '--validation-end', '11', '--response-mode', 'anchored']
      with patch.object(sys, 'argv', argv), patch.object(assess, 'read_segments', return_value=(anchored_rows(), [])), redirect_stdout(io.StringIO()):
        assess.main()
      report = json.loads(output.read_text())
      self.assertEqual(report['response_mode'], 'anchored')
      self.assertEqual(len(report['assessments']), 3)
      for result in report['assessments']:
        self.assertEqual(result['model']['response_mode'], 'anchored')
        self.assertEqual(result['no_command_model']['response_mode'], 'anchored')
        self.assertLess(result['metrics']['test']['command']['rmse_deg'], 1e-6)


if __name__ == '__main__':
  unittest.main()
