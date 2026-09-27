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


def anchored_synthetic(seed=1, count=12, command=True):
  windows = synthetic(seed, count, command)
  u, v, roll, y = (windows[k] for k in ('u', 'v', 'roll', 'y'))
  # Generate increments about each window's initial delayed input, keeping
  # independent starting angles rather than forcing a common equilibrium.
  y[:, 0] += np.arange(count) * 3
  speed = (v - 20) / 10
  equilibrium = 2.5 * u + 0.4 * u * speed + 0.7 * speed + 3 * roll + 0.2
  alpha = np.exp(-0.05 / 0.3)
  for k in range(1, 101):
    y[:, k] = y[:, 0] + alpha * (y[:, k - 1] - y[:, 0]) + (1 - alpha) * (equilibrium[:, 10 + k - 3] - equilibrium[:, 7])
  return windows


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


class TestAnchoredModel(unittest.TestCase):
  def test_delayed_step_has_analytic_response_and_subtracts_cross_feature(self):
    windows = synthetic(7, 2)
    windows['y'][:, 0] = [4.5, -3.0]
    windows['u'][:] = 0.2
    windows['v'][:] = 20
    windows['roll'][:] = 0.01
    windows['u'][:, 12:] = 0.6
    windows['v'][:, 12:] = 30
    windows['roll'][:, 12:] = 0.03
    fitted = dict(tau=0.3, delay_steps=3, dt=0.05, input_mode='command',
                  response_mode='anchored', theta=[2, 3, 5, 7, 11])
    # The step first affects output k=5. Its equilibrium change is
    # 2*(.6-.2) + 3*(.6*1-.2*0) + 5*(1-0) + 7*(.03-.01) = 7.74.
    steps = np.maximum(np.arange(101) - 4, 0)
    expected = windows['y'][:, :1] + 7.74 * (1 - np.exp(-0.05 / 0.3) ** steps)
    np.testing.assert_allclose(model.predict(fitted, windows), expected, atol=1e-12)

  def test_constant_inputs_preserve_each_initial_angle(self):
    windows = synthetic(8, 3)
    for key in ('u', 'v', 'roll'):
      windows[key][:] = windows[key][:, :1]
    for mode, theta in [('command', [2, 3, 5, 7, 11]), ('no_command', [5, 7, 11])]:
      with self.subTest(mode=mode):
        fitted = dict(tau=0.3, delay_steps=3, dt=0.05, input_mode=mode,
                      response_mode='anchored', theta=theta)
        expected = np.repeat(windows['y'][:, :1], 101, axis=1)
        np.testing.assert_array_equal(model.predict(fitted, windows), expected)

  def test_predictions_translate_with_each_window_initial_angle(self):
    windows = anchored_synthetic(9, 3)
    fitted = dict(tau=0.3, delay_steps=3, dt=0.05, input_mode='command',
                  response_mode='anchored', theta=[2.5, 0.4, 0.7, 3, 0])
    original = model.predict(fitted, windows)
    shift = np.array([100., -20., 7.])[:, None]
    shifted = {**windows, 'y': windows['y'] + shift}
    np.testing.assert_allclose(model.predict(fitted, shifted), original + shift, atol=1e-12)

  def test_future_angles_and_previous_angle_do_not_enter_prediction(self):
    windows = anchored_synthetic(10, 3)
    fitted = dict(tau=0.3, delay_steps=3, dt=0.05, input_mode='command',
                  response_mode='anchored', theta=[2.5, 0.4, 0.7, 3, 0])
    original = model.predict(fitted, windows)
    changed = {**windows, 'y': windows['y'].copy(), 'yprev': np.full(3, 10000.)}
    changed['y'][:, 1:] = -10000
    np.testing.assert_array_equal(model.predict(fitted, changed), original)

  def test_future_inputs_cannot_change_earlier_predictions(self):
    windows = anchored_synthetic(11, 3)
    for delay in (0, 3, 10):
      with self.subTest(delay=delay):
        fitted = dict(tau=0.3, delay_steps=delay, dt=0.05, input_mode='command',
                      response_mode='anchored', theta=[2.5, 0.4, 0.7, 3, 0])
        original = model.predict(fitted, windows)
        changed = {k: v.copy() for k, v in windows.items()}
        for key in ('u', 'v', 'roll'):
          changed[key][:, 61:] += 10000
        np.testing.assert_array_equal(model.predict(fitted, changed)[:, :51], original[:, :51])
        for key in ('u', 'v', 'roll'):
          changed[key][:, :111] = windows[key][:, :111]
        np.testing.assert_array_equal(model.predict(fitted, changed), original)

  def test_recovers_anchored_coefficients_and_lag(self):
    fitted, search = model.fit_candidates(anchored_synthetic(12), anchored_synthetic(13, 5), response_mode='anchored')
    self.assertEqual(fitted['response_mode'], 'anchored')
    self.assertEqual(fitted['delay_steps'], 3)
    self.assertEqual(fitted['tau'], 0.3)
    np.testing.assert_allclose(fitted['theta'], [2.5, 0.4, 0.7, 3, 0], atol=1e-5)
    self.assertTrue(all(row['response_mode'] == 'anchored' for row in search))
    self.assertLess(model.evaluate(fitted, anchored_synthetic(14, 4))['rmse_deg'], 1e-6)

  def test_validation_outcomes_do_not_fit_anchored_coefficients(self):
    train, validation = anchored_synthetic(15), anchored_synthetic(16, 5)
    _, original = model.fit_candidates(train, validation, response_mode='anchored')
    _, changed = model.fit_candidates(train, {**validation, 'y': validation['y'] * 3}, response_mode='anchored')
    for a, b in zip(original, changed, strict=True):
      np.testing.assert_array_equal(a['theta'], b['theta'])

  def test_window_angle_offsets_do_not_change_fitted_response(self):
    train, validation = anchored_synthetic(23), anchored_synthetic(24, 5)
    _, original = model.fit_candidates(train, validation, response_mode='anchored')
    shifted_train = {**train, 'y': train['y'] + np.arange(12)[:, None] * 10}
    shifted_validation = {**validation, 'y': validation['y'] - np.arange(5)[:, None] * 20}
    _, shifted = model.fit_candidates(shifted_train, shifted_validation, response_mode='anchored')
    for a, b in zip(original, shifted, strict=True):
      np.testing.assert_allclose(a['theta'], b['theta'], atol=1e-10)
      self.assertAlmostEqual(a['validation_rmse_deg'], b['validation_rmse_deg'], places=10)

  def test_no_command_anchored_model_recovers_speed_and_roll_response(self):
    train, validation = anchored_synthetic(17, command=False), anchored_synthetic(18, 5, command=False)
    fitted, _ = model.fit_candidates(train, validation, input_mode='no_command', response_mode='anchored')
    self.assertEqual(fitted['delay_steps'], 3)
    np.testing.assert_allclose(fitted['theta'], [0.7, 3, 0], atol=1e-5)
    self.assertLess(model.evaluate(fitted, validation)['rmse_deg'], 1e-6)
    changed = {**validation, 'u': validation['u'] + 100}
    np.testing.assert_array_equal(model.predict(fitted, validation), model.predict(fitted, changed))

  def test_rejects_unknown_response_mode(self):
    train, validation = synthetic(19), synthetic(20, 5)
    fitted = dict(tau=0.3, delay_steps=3, dt=0.05, input_mode='command', theta=[2.5, 0.4, 0.7, 3, 0.2])
    with self.assertRaisesRegex(ValueError, 'response_mode'):
      model.predict({**fitted, 'response_mode': 'unknown'}, validation)
    with self.assertRaisesRegex(ValueError, 'response_mode'):
      model.fit_candidates(train, validation, response_mode='unknown')

  def test_default_explicit_and_legacy_absolute_models_are_numerically_identical(self):
    train, validation = synthetic(21), synthetic(22, 5)
    default, default_search = model.fit_candidates(train, validation)
    explicit, explicit_search = model.fit_candidates(train, validation, response_mode='absolute')
    self.assertEqual(default_search, explicit_search)
    legacy = {k: v for k, v in default.items() if k != 'response_mode'}
    np.testing.assert_array_equal(model.predict(default, validation), model.predict(explicit, validation))
    np.testing.assert_array_equal(model.predict(default, validation), model.predict(legacy, validation))
    self.assertLess(model.evaluate(legacy, validation)['rmse_deg'], 1e-6)


if __name__ == '__main__':
  unittest.main()
