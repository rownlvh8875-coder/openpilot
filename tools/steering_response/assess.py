"""Offline response assessment. Writes private JSON; never sends vehicle commands."""
import argparse
import json
from pathlib import Path
import numpy as np
from extract import make_windows, read_segments
from model import RESPONSE_MODES, fit_candidates, evaluate, baseline_metrics, predict


def subset(windows, mask):
  return {k: v[mask] for k, v in windows.items()}


def split_windows(windows, train_end, validation_end):
  if train_end >= validation_end:
    raise ValueError('train_end must precede validation_end')
  sg = windows['sg']
  return {'train': subset(windows, sg <= train_end),
          'validation': subset(windows, (sg > train_end) & (sg <= validation_end)),
          'test': subset(windows, sg > validation_end)}


def outside_range(training, testing):
  return float(np.mean((testing < np.min(training)) | (testing > np.max(training))))


def input_coverage(training, testing, delay_steps, response_mode='absolute'):
  """Use only the feature samples consumed by the selected command model."""
  if response_mode not in RESPONSE_MODES:
    raise ValueError('response_mode must be absolute or anchored')
  start = 10 if response_mode == 'anchored' else 11
  used = slice(start - delay_steps, 111 - delay_steps)
  train_v, test_v = training['v'][:, used], testing['v'][:, used]
  train_u, test_u = training['u'][:, used], testing['u'][:, used]
  return {'speed_range_mps': [float(test_v.min()), float(test_v.max())],
          'command_range': [float(test_u.min()), float(test_u.max())],
          'outside_training_speed_fraction': outside_range(train_v, test_v),
          'outside_training_command_fraction': outside_range(train_u, test_u)}


def assess_rows(rows, train_end, validation_end, driver_limit=50, response_mode='absolute'):
  if response_mode not in RESPONSE_MODES:
    raise ValueError('response_mode must be absolute or anchored')
  selected = rows.copy()
  selected[:, 6] *= selected[:, 7] <= driver_limit
  windows = make_windows(selected)
  splits = split_windows(windows, train_end, validation_end)
  result = {'driver_limit': driver_limit, 'response_mode': response_mode, 'eligible_grid_rows': int(selected[:, 6].sum()),
            'windows': {k: len(v['y']) for k, v in splits.items()},
            'controller_comparison': 'NOT_VALIDATED',
            'limitation': 'Observed future command/speed/roll conditioning is not causal validation of a changed controller.'}
  if any(len(w['y']) < 2 for w in splits.values()):
    result['status'] = 'INSUFFICIENT_WINDOWS'
    return result
  command, search = fit_candidates(splits['train'], splits['validation'], response_mode=response_mode)
  no_command, _ = fit_candidates(splits['train'], splits['validation'], input_mode='no_command', response_mode=response_mode)
  result.update(model=command, no_command_model=no_command, candidate_search=search, metrics={}, coverage={}, groups={})
  for name, w in splits.items():
    result['metrics'][name] = {'command': evaluate(command, w), 'no_command': evaluate(no_command, w), **baseline_metrics(w)}
    result['coverage'][name] = {
      'segments': sorted(set(w['sg'].astype(int).tolist())),
      **input_coverage(splits['train'], w, command['delay_steps'], response_mode),
      'outcome_speed_range_mps': [float(w['v'][:, 10:111].min()), float(w['v'][:, 10:111].max())],
      'angle_range_deg': [float(w['y'].min()), float(w['y'].max())],
    }
  w = splits['test']
  angle = np.mean(w['y'], axis=1)
  speed = np.mean(w['v'][:, 10:111], axis=1)
  for name, mask in {'angle_negative': angle < -2, 'angle_positive': angle > 2,
                     'speed_below_15': speed < 15, 'speed_at_least_15': speed >= 15}.items():
    if np.any(mask):
      group = subset(w, mask)
      result['groups'][name] = {'command': evaluate(command, group), 'no_command': evaluate(no_command, group)}
  # A predeclared heuristic screen, not a safety/causality certification.
  reasons = []
  for name in ('validation', 'test'):
    metrics = result['metrics'][name]
    if metrics['command']['rmse_deg'] > .8 * metrics['no_command']['rmse_deg']:
      reasons.append(f'{name}: less than 20% improvement over no-command baseline')
    for horizon in ('0.25', '0.5', '1'):
      error = metrics['command']['horizon_rmse_deg'][horizon]
      if error >= min(metrics[k]['horizon_rmse_deg'][horizon] for k in ('no_command', 'persistence')):
        reasons.append(f'{name}: command prediction fails short-horizon baseline at {horizon}s')
    if result['coverage'][name]['outside_training_speed_fraction'] > .05:
      reasons.append(f'{name}: more than 5% of speed samples outside training range')
  result['status'] = 'PREDICTION_SCREEN_FAILED' if reasons else 'PREDICTION_SCREEN_PASSED_CAUSALITY_UNVERIFIED'
  result['screen_reasons'] = reasons
  prediction = predict(command, w)
  # Worst three test windows, selected solely for review; never used for tuning.
  rank = np.argsort(np.mean((prediction[:, 1:] - w['y'][:, 1:]) ** 2, axis=1))[-3:][::-1]
  result['review_traces'] = [{'segment': int(w['sg'][i]), 'start_mono_time': float(w['t'][i]),
                              'observed': w['y'][i].tolist(), 'predicted': prediction[i].tolist(),
                              'command': w['u'][i, 10:111].tolist()} for i in rank]
  return result


def main():
  cli = argparse.ArgumentParser(description=__doc__)
  cli.add_argument('--logs', type=Path, required=True)
  cli.add_argument('--source', type=Path, required=True, help='Checkout with full cereal and opendbc schemas')
  cli.add_argument('--output', type=Path, required=True, help='Private local JSON path; do not commit')
  cli.add_argument('--train-end', type=int, required=True)
  cli.add_argument('--validation-end', type=int, required=True)
  cli.add_argument('--input-mode', choices=['mean', 'last'], default='mean')
  cli.add_argument('--response-mode', choices=RESPONSE_MODES, default='absolute')
  args = cli.parse_args()
  if args.train_end >= args.validation_end:
    cli.error('--train-end must precede --validation-end')
  rows, inventory = read_segments(args.logs, args.source, driver_limit=100, input_mode=args.input_mode,
                                 progress=lambda row: print(row['segment'], row['status'], flush=True))
  results = [assess_rows(rows, args.train_end, args.validation_end, limit, args.response_mode) for limit in (50, 30, 100)]
  report = {'format_version': 1, 'dt_seconds': .05, 'input_mode': args.input_mode, 'response_mode': args.response_mode,
            'train_end': args.train_end, 'validation_end': args.validation_end,
            'inventory': inventory, 'assessments': results}
  args.output.parent.mkdir(parents=True, exist_ok=True)
  args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')
  print(json.dumps([{'driver_limit': r['driver_limit'], 'status': r['status'], 'windows': r['windows']} for r in results]))


if __name__ == '__main__':
  main()
