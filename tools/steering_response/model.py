"""Offline, conditional response fits; these are not deployment vehicle models.

Predictions condition on the recorded command, speed and roll trajectory. The
fitted delay is an effective recorded-signal lag, not an EPS actuation delay.
Held-out predictive benefit from command inputs does not establish causality.
"""

import numpy as np

TAUS = (0.1, 0.2, 0.3, 0.5, 0.8, 1.2, 2.0)
DELAYS = (0, 1, 2, 3, 4, 6, 8, 10)
HORIZONS = (0.1, 0.25, 0.5, 1.0, 3.0, 5.0)
FEATURE_NAMES = {
  'command': ['command', 'command_speed_centered', 'speed_centered', 'roll', 'intercept'],
  'no_command': ['speed_centered', 'roll', 'intercept'],
}


def _check_dt(dt):
  if not np.isfinite(dt) or dt <= 0:
    raise ValueError('dt must be finite and positive')


def _windows(windows, minimum=1):
  """Reject malformed input instead of silently dropping or imputing samples."""
  arrays = {}
  try:
    for name in ('y', 'u', 'v', 'roll', 't'):
      arrays[name] = np.asarray(windows[name], dtype=float)
    arrays['sg'] = np.asarray(windows['sg'])
    if 'yprev' in windows:
      arrays['yprev'] = np.asarray(windows['yprev'], dtype=float)
  except (KeyError, TypeError, ValueError) as exc:
    raise ValueError('windows require numeric y, u, v, roll, t and segment labels sg') from exc
  y = arrays['y']
  if y.ndim != 2 or y.shape[1] != 101 or y.shape[0] < minimum:
    raise ValueError(f'y must have shape (W, 101), with at least {minimum} windows')
  for name, values in arrays.items():
    shape = (len(y), 121) if name in ('u', 'v', 'roll') else (len(y),)
    if name != 'y' and values.shape != shape:
      raise ValueError(f'{name} must have shape {shape}')
    if values.dtype.kind in 'fci' and not np.isfinite(values).all():
      raise ValueError(f'{name} contains nonfinite values')
  return arrays


def _features(windows, input_mode):
  if input_mode not in FEATURE_NAMES:
    raise ValueError('input_mode must be command or no_command')
  speed = (windows['v'] - 20.0) / 10.0
  columns = [speed, windows['roll'], np.ones_like(speed)]
  if input_mode == 'command':
    columns = [windows['u'], windows['u'] * speed] + columns
  return np.stack(columns, axis=-1)


def _filtered(features, alpha, delay):
  """Free recurrence: the initial observed angle is added separately."""
  filtered = np.empty((features.shape[0], 100, features.shape[2]))
  state = np.zeros((features.shape[0], features.shape[2]))
  for k in range(1, 101):
    state = alpha * state + (1.0 - alpha) * features[:, 10 + k - delay]
    filtered[:, k - 1] = state
  return filtered


def _predict(model, windows, dt):
  alpha = np.exp(-dt / model['tau'])
  filtered = _filtered(_features(windows, model['input_mode']), alpha, model['delay_steps'])
  decay = alpha ** np.arange(1, 101)
  future = filtered @ np.asarray(model['theta']) + windows['y'][:, :1] * decay
  return np.column_stack((windows['y'][:, 0], future))


def predict(model, windows, dt=0.05):
  """Return W x 101 angles, using only y[:, 0] and causal recorded inputs."""
  _check_dt(dt)
  arrays = _windows(windows)
  try:
    tau, delay = float(model['tau']), float(model['delay_steps'])
    mode = model['input_mode']
    theta = np.asarray(model['theta'], dtype=float)
    model_dt = float(model['dt'])
  except (KeyError, TypeError, ValueError) as exc:
    raise ValueError('invalid model fields') from exc
  if not np.isfinite(tau) or tau <= 0:
    raise ValueError('tau must be finite and positive')
  if not np.isfinite(delay) or delay != int(delay) or not 0 <= delay <= 10:
    raise ValueError('delay_steps must be an integer from 0 through 10')
  if mode not in FEATURE_NAMES or theta.shape != (len(FEATURE_NAMES[mode]),) or not np.isfinite(theta).all():
    raise ValueError('theta does not match the finite input features')
  if not np.isfinite(model_dt) or not np.isclose(model_dt, dt, rtol=1e-10, atol=0):
    raise ValueError('dt must match the fitted model sampling interval')
  return _predict({**model, 'tau': tau, 'delay_steps': int(delay), 'theta': theta}, arrays, dt)


def _metrics(observed, predicted, dt):
  if 100 * dt < 5.0 - 1e-10:
    raise ValueError('windows must cover all scoring horizons through 5 seconds')
  error = predicted - observed
  future = error[:, 1:]
  endpoints = {}
  for horizon in HORIZONS:
    position = min(horizon / dt, 100.0)
    lower = int(np.floor(position))
    upper = min(lower + 1, 100)
    endpoint = error[:, lower] + (position - lower) * (error[:, upper] - error[:, lower])
    endpoints[f'{horizon:g}'] = float(np.sqrt(np.mean(endpoint ** 2)))
  return {
    'rmse_deg': float(np.sqrt(np.mean(future ** 2))),
    'mae_deg': float(np.mean(np.abs(future))),
    'p95_abs_deg': float(np.percentile(np.abs(future), 95)),
    'horizon_rmse_deg': endpoints,
    'n_windows': int(len(observed)),
    'n_prediction_steps': int(future.size),
  }


def evaluate(model, windows, dt=0.05):
  """Score all future steps and selected endpoint horizons, excluding y0."""
  prediction = predict(model, windows, dt)
  return _metrics(np.asarray(windows['y']), prediction, dt)


def baseline_metrics(windows, dt=0.05):
  """Persistence and, when available, fixed slope from y0 and yprev only."""
  _check_dt(dt)
  arrays = _windows(windows)
  y = arrays['y']
  prediction = np.repeat(y[:, :1], 101, axis=1)
  result = {'persistence': _metrics(y, prediction, dt)}
  if 'yprev' in arrays:
    slope_per_step = y[:, 0] - arrays['yprev']
    prediction = y[:, :1] + slope_per_step[:, None] * np.arange(101)
    result['initial_slope'] = _metrics(y, prediction, dt)
  return result


def fit_candidates(train, validation, dt=0.05, input_mode='command'):
  """Fit on train only; choose tau/lag using validation trajectory RMSE.

  Supply a distinct, untouched test split only to evaluate(), never here.
  Search rows retain each train-fitted coefficient vector for auditability.
  """
  _check_dt(dt)
  training, validating = _windows(train, 2), _windows(validation, 2)
  features = _features(training, input_mode)
  search = []
  # Speed and roll receive the same lag search in both models for a fair ablation.
  for tau in TAUS:
    alpha = np.exp(-dt / tau)
    target = (training['y'][:, 1:] - training['y'][:, :1] * alpha ** np.arange(1, 101)).ravel()
    for delay in DELAYS:
      design = _filtered(features, alpha, delay).reshape(-1, features.shape[-1])
      scale = np.sqrt(np.mean(design ** 2, axis=0))
      scale[scale < 1e-12] = 1.0
      normalized = design / scale
      ridge = 1e-8 * np.eye(normalized.shape[1])
      theta = np.linalg.solve(normalized.T @ normalized + ridge, normalized.T @ target) / scale
      candidate = {
        'tau': float(tau), 'delay_steps': int(delay), 'delay_seconds': float(delay * dt),
        'delay_interpretation': 'effective recorded-signal lag', 'dt': float(dt),
        'theta': theta.tolist(), 'feature_names': list(FEATURE_NAMES[input_mode]),
        'input_mode': input_mode,
      }
      metrics = _metrics(validating['y'], _predict(candidate, validating, dt), dt)
      candidate['validation_rmse_deg'] = metrics['rmse_deg']
      search.append(candidate)
  selected = min(search, key=lambda row: row['validation_rmse_deg'])
  return dict(selected), search
