"""Numerical and anti-leakage tests for offline response diagnostics."""
import json
import unittest  # noqa: TID251 -- standalone diagnostics avoid native pytest dependencies

import numpy as np

from tools.steering_response import model  # noqa: TID251 -- runnable from an unbuilt checkout


def synthetic(seed=1, count=12, command=True):
  rng = np.random.default_rng(seed)
  u = rng.normal(size=(count, 121))
  if not command:
    u[:] = 0
  v = rng.uniform(10, 35, size=u.shape)
  roll = rng.normal(0, 0.03, size=u.shape)
  y = np.zeros((count, 101))
  y[:, 0] = rng.normal(size=count)
  # Independent simulated plant: 300 ms relaxation, 150 ms recorded-input lag.
  alpha = np.exp(-0.05 / 0.3)
  for k in range(1, 101):
    j = 10 + k - 3
    target = 2.5 * u[:, j] + (0.4 * u[:, j] + 0.7) * (v[:, j] - 20) / 10 + 3 * roll[:, j] + 0.2
    y[:, k] = alpha * y[:, k - 1] + (1 - alpha) * target
  return dict(y=y, u=u, v=v, roll=roll, sg=np.arange(count), t=np.arange(count, dtype=float))


class TestModel(unittest.TestCase):
  @classmethod
  def setUpClass(cls):
    cls.train, cls.validation = synthetic(1), synthetic(2, 5)
    cls.fitted, cls.search = model.fit_candidates(cls.train, cls.validation)

  def test_recovers_known_response_and_scores_future_steps(self):
    self.assertEqual(self.fitted['delay_steps'], 3)
    self.assertAlmostEqual(self.fitted['tau'], 0.3)
    np.testing.assert_allclose(self.fitted['theta'], [2.5, 0.4, 0.7, 3, 0.2], atol=1e-5)
    metrics = model.evaluate(self.fitted, synthetic(3, 4))
    self.assertLess(metrics['rmse_deg'], 1e-6)
    self.assertEqual(set(metrics['horizon_rmse_deg']), {'0.1', '0.25', '0.5', '1', '3', '5'})
    self.assertEqual(len(self.search), 56)
    json.dumps((self.fitted, self.search, metrics), allow_nan=False)

  def test_predictions_do_not_recur_on_future_observations(self):
    original = model.predict(self.fitted, self.validation)
    changed = {**self.validation, 'y': self.validation['y'].copy()}
    changed['y'][:, 1:] = 10000
    np.testing.assert_array_equal(original, model.predict(self.fitted, changed))
    np.testing.assert_array_equal(original[:, 0], self.validation['y'][:, 0])

  def test_future_command_cannot_change_earlier_prediction(self):
    original = model.predict(self.fitted, self.validation)
    changed = {**self.validation, 'u': self.validation['u'].copy()}
    changed['u'][:, 61:] += 10000
    # At step 50, even a zero-delay model may use only input index 60.
    np.testing.assert_array_equal(original[:, :51], model.predict(self.fitted, changed)[:, :51])
    changed['u'][:, :111] = self.validation['u'][:, :111]
    np.testing.assert_array_equal(original, model.predict(self.fitted, changed))
    for delay in [-1, 11, 1.5]:
      with self.subTest(delay=delay), self.assertRaises(ValueError):
        model.predict({**self.fitted, 'delay_steps': delay}, self.validation)

  def test_validation_selects_but_does_not_fit_coefficients(self):
    changed = {**self.validation, 'y': self.validation['y'] * 3}
    _, search = model.fit_candidates(self.train, changed)
    for original, other in zip(self.search, search, strict=True):
      np.testing.assert_array_equal(original['theta'], other['theta'])

  def test_no_command_predictions_ignore_command(self):
    fitted, _ = model.fit_candidates(self.train, self.validation, input_mode='no_command')
    changed = {**self.validation, 'u': self.validation['u'] * 100}
    np.testing.assert_array_equal(model.predict(fitted, self.validation), model.predict(fitted, changed))
    self.assertGreater(model.evaluate(fitted, self.validation)['rmse_deg'], 0.1)

  def test_no_command_can_recover_lagged_speed_response_for_fair_comparison(self):
    train, validation = synthetic(5, command=False), synthetic(6, command=False)
    fitted, _ = model.fit_candidates(train, validation, input_mode='no_command')
    self.assertEqual(fitted['delay_steps'], 3)
    self.assertLess(model.evaluate(fitted, validation)['rmse_deg'], 1e-6)

  def test_baselines_use_only_initial_angle_and_optional_previous_angle(self):
    windows = synthetic(4, 2)
    windows['y'][:] = np.arange(101) * 0.05
    windows['yprev'] = np.full(2, -0.05)
    metrics = model.baseline_metrics(windows)
    self.assertAlmostEqual(metrics['initial_slope']['rmse_deg'], 0)
    self.assertAlmostEqual(metrics['persistence']['horizon_rmse_deg']['5'], 5)
    self.assertAlmostEqual(metrics['persistence']['mae_deg'], 2.525)
    del windows['yprev']
    self.assertEqual(set(model.baseline_metrics(windows)), {'persistence'})

  def test_rejects_empty_malformed_nonfinite_and_invalid_models(self):
    for key, value in [('y', np.empty((0, 101))), ('u', np.zeros((5, 120))),
                       ('roll', np.full((5, 121), np.nan)), ('t', np.zeros(4)),
                       ('yprev', np.full(5, np.inf))]:
      with self.subTest(key=key), self.assertRaises(ValueError):
        model.predict(self.fitted, {**self.validation, key: value})
    for tau in [0, -1, np.inf, np.nan]:
      with self.subTest(tau=tau), self.assertRaises(ValueError):
        model.predict({**self.fitted, 'tau': tau}, self.validation)
    for windows in [synthetic(1, 0), synthetic(1, 1)]:
      with self.assertRaises(ValueError):
        model.fit_candidates(windows, self.validation)
      with self.assertRaises(ValueError):
        model.fit_candidates(self.train, windows)
    for dt in [0, -0.05, np.nan]:
      with self.assertRaises(ValueError):
        model.predict(self.fitted, self.validation, dt=dt)


if __name__ == '__main__':
  unittest.main()
