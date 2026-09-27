"""Tests catch future joins, hidden interventions, and gap bridging."""
import sys
import unittest
from pathlib import Path
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import extract


def records(duration=8):
  t = np.arange(0, duration, .01)
  return {
    'carState': np.array([[x, 1, 20, x, 0, 0, 0] for x in t]),
    'carOutput': np.array([[x, 1, x / 100] for x in t]),
    'carControl': np.array([[x, 1] for x in t]),
    'liveParameters': np.array([[x, 1, 0] for x in t[::5]]),
  }


class ExtractTests(unittest.TestCase):
  def test_joins_only_past_commands(self):
    r = records()
    r['carOutput'][:, 0] += .007
    rows = extract.align_segment(r, 4)
    self.assertTrue(np.all(rows[:, 4] <= rows[:, 0] / 100))
    self.assertAlmostEqual(rows[2, 4], .0009, places=6)

  def test_brief_driver_override_invalidates_window(self):
    r = records()
    r['carState'][202, 1] = 0  # bad for 10 ms between 20 Hz grid points
    rows = extract.align_segment(r, 4)
    self.assertTrue(np.any((rows[:, 0] > 2) & (rows[:, 0] < 2.1) & (rows[:, 6] == 0)))
    self.assertEqual(len(extract.make_windows(rows)['y']), 0)

  def test_stale_output_and_segment_boundaries_not_bridged(self):
    r = records()
    r['carOutput'] = r['carOutput'][(r['carOutput'][:, 0] < 2) | (r['carOutput'][:, 0] > 4)]
    rows = extract.align_segment(r, 4)
    self.assertFalse(np.any(rows[(rows[:, 0] > 2.1) & (rows[:, 0] < 4), 6]))
    self.assertEqual(len(extract.make_windows(rows)['y']), 0)
    joined = np.concatenate([extract.align_segment(records(4), 4), extract.align_segment(records(4), 5)])
    self.assertEqual(len(extract.make_windows(joined)['y']), 0)

  def test_valid_windows_and_empty_input(self):
    w = extract.make_windows(extract.align_segment(records(), 4))
    self.assertEqual(w['y'].shape, (1, 101))
    self.assertEqual(w['u'].shape, (1, 121))
    self.assertAlmostEqual(w['u'][0, 10], w['y'][0, 0] / 100)
    self.assertEqual(extract.make_windows(np.empty((0, 8)))['y'].shape, (0, 101))

  def test_driver_peak_and_time_weighted_ramp_preserved(self):
    r = records()
    r['carState'][202, 4] = 70
    rows = extract.align_segment(r, 4, driver_limit=100)
    self.assertEqual(rows[np.argmin(abs(rows[:, 0] - 2.05)), 7], 70)
    r['carOutput'] = np.array([[0, 1, 0], [.025, 1, .5], [.05, 1, .5], [7.99, 1, .5]])
    mean = extract.align_segment(r, 4, input_mode='mean')
    self.assertAlmostEqual(mean[1, 4], .25)

  def test_nonfinite_signal_rejected(self):
    r = records()
    r['carOutput'][10, 2] = np.nan
    with self.assertRaises(ValueError):
      extract.align_segment(r, 4)

  def test_stale_interval_recovered_between_grid_endpoints(self):
    r = records()
    co = r['carOutput']
    r['carOutput'] = co[(co[:, 0] <= 2) | (co[:, 0] >= 2.1)]
    rows = extract.align_segment(r, 4, input_mode='mean')
    self.assertEqual(len(extract.make_windows(rows)['y']), 0)

  def test_complete_frames_required_and_concatenation_supported(self):
    import zstandard as zstd
    payload = b'complete message boundary' * 100
    compressor = zstd.ZstdCompressor().compressobj()
    truncated = compressor.compress(payload) + compressor.flush(zstd.COMPRESSOBJ_FLUSH_BLOCK)
    with self.assertRaises(ValueError):
      extract.decompress_complete(truncated)
    checksummed = zstd.ZstdCompressor(write_checksum=True).compress(payload)
    with self.assertRaises(ValueError):
      extract.decompress_complete(checksummed[:-4])
    self.assertEqual(extract.decompress_complete(checksummed + checksummed), payload + payload)
    with self.assertRaises(ValueError):
      extract.decompress_complete(b'')


if __name__ == '__main__':
  unittest.main()
