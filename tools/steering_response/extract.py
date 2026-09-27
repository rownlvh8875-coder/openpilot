"""Full-cereal, private local extraction. No CAN transmit or vehicle imports.

Rows: time, segment, speed_mps, measured_angle_deg, command, roll_rad,
eligible, max_abs_driver_torque_since_previous_grid. No yawRate unit assumptions are made.
"""
from pathlib import Path
from collections import Counter
import numpy as np

DT = .05
SERVICES = ('carState', 'carOutput', 'carControl', 'liveParameters')


def align_segment(records, segment, driver_limit=50, input_mode='last'):
  """Past-only joins, including any brief invalid event since the previous grid.

  mean uses a time-weighted integral of zero-order-held carOutput in (t-DT,t].
  Its timestamps are publication times, not measured EPS actuation times.
  """
  if input_mode not in ('last', 'mean') or driver_limit < 0:
    raise ValueError('Unsupported input mode or driver limit')
  if any(len(records.get(k, [])) == 0 for k in SERVICES):
    return np.empty((0, 8))
  rec = {k: np.asarray(records[k], dtype=float) for k in SERVICES}
  for k, a in rec.items():
    if not np.isfinite(a).all() or np.any(np.diff(a[:, 0]) <= 0):
      raise ValueError(f'Nonfinite or nonincreasing {k} samples')
  cs = rec['carState']
  times = np.arange(np.ceil(cs[0, 0] / DT) * DT, cs[-1, 0], DT)
  joined, good = {}, np.ones(len(times), dtype=bool)
  for k, a in rec.items():
    idx = np.searchsorted(a[:, 0], times, side='right') - 1
    prev = np.searchsorted(a[:, 0], times - DT, side='right') - 1
    safe = np.maximum(idx, 0)
    row = a[safe]
    bad = (a[:, 1] == 0)
    if k == 'carState':
      bad |= (a[:, 2] < 8) | (a[:, 2] > 40) | (np.abs(a[:, 4]) > driver_limit) | (a[:, 5] != 0) | (a[:, 6] != 0)
    if k == 'carOutput':
      bad |= np.abs(a[:, 2]) >= .98
    count = np.r_[0, np.cumsum(bad)]
    interval_bad = count[safe + 1] - count[np.maximum(prev + 1, 0)]
    max_age = .15 if k == 'liveParameters' else .075
    good &= (idx >= 0) & (times - row[:, 0] <= max_age) & ~bad[safe] & (interval_bad == 0)
    # A missing interval may recover before the next grid endpoint. Reject
    # the entire grid interval if any of it intersects an overdue signal.
    gap = np.flatnonzero(np.diff(a[:, 0]) > max_age)
    starts = np.searchsorted(times, a[gap, 0] + max_age, side='right')
    ends = np.searchsorted(times, a[gap + 1, 0] + DT, side='left')
    stale = np.zeros(len(times) + 1, dtype=int)
    np.add.at(stale, starts, 1)
    np.add.at(stale, ends, -1)
    good &= np.cumsum(stale[:-1]) == 0
    joined[k] = row
  command = joined['carOutput'][:, 2]
  if input_mode == 'mean':
    co = rec['carOutput']
    integral = np.r_[0, np.cumsum(np.diff(co[:, 0]) * co[:-1, 2])]

    def area(t):
      i = np.maximum(np.searchsorted(co[:, 0], t, side='right') - 1, 0)
      return integral[i] + (t - co[i, 0]) * co[i, 2]

    command = (area(times) - area(times - DT)) / DT
    good &= times - DT >= co[0, 0]
  driver_peak = np.abs(joined['carState'][:, 4]).copy()
  bins = np.searchsorted(times, cs[:, 0], side='left')
  included = bins < len(times)
  np.maximum.at(driver_peak, bins[included], np.abs(cs[included, 4]))
  return np.column_stack((times, np.full(len(times), segment), joined['carState'][:, 2],
                          joined['carState'][:, 3], command, joined['liveParameters'][:, 2],
                          good, driver_peak))


def make_windows(rows):
  """Disjoint 6.05 second blocks: .5s history, 5s outcome, .5s margin.

  No stitching across segments, ineligible samples, or >75ms time gaps.
  """
  rows = np.asarray(rows)
  result = {k: [] for k in ('y', 'u', 'v', 'roll', 'sg', 't', 'yprev')}
  start = 0
  while start < len(rows):
    if not rows[start, 6]:
      start += 1
      continue
    end = start + 1
    while end < len(rows) and rows[end, 6] and rows[end, 1] == rows[start, 1] and 0 < rows[end, 0] - rows[end - 1, 0] < .075:
      end += 1
    for p in range(start, end - 120, 121):
      block = rows[p:p + 121]
      result['y'].append(block[10:111, 3])
      for name, col in (('u', 4), ('v', 2), ('roll', 5)):
        result[name].append(block[:, col])
      result['sg'].append(block[10, 1])
      result['t'].append(block[10, 0])
      result['yprev'].append(block[9, 3])
    start = end
  for k in result:
    result[k] = np.asarray(result[k])
  for k, width in (('y', 101), ('u', 121), ('v', 121), ('roll', 121)):
    result[k] = result[k].reshape(-1, width)
  return result


def decompress_complete(compressed):
  """Streaming read() alone can silently accept an incomplete zstd frame."""
  import zstandard as zstd
  if not compressed:
    raise ValueError('Empty compressed segment')
  parts, remaining = [], compressed
  while remaining:
    decoder = zstd.ZstdDecompressor().decompressobj()
    parts.append(decoder.decompress(remaining))
    if not decoder.eof:
      raise ValueError('Incomplete zstd frame; discard entire segment')
    remaining = decoder.unused_data
  return b''.join(parts)


def read_segments(logs, source, driver_limit=50, input_mode='mean', progress=None):
  """Read one route root. A failed segment is discarded in its entirety."""
  import capnp
  logs, source = Path(logs), Path(source)
  parser = capnp.SchemaParser()
  schema = parser.load(str(source / 'openpilot/cereal/log.capnp'),
                      imports=[str(source / 'openpilot/cereal'), str(source / 'opendbc_repo/opendbc/car')])
  paths = sorted(logs.glob('*/rlog.zst'), key=lambda p: int(p.parent.name.rsplit('--', 1)[-1]))
  if not paths:
    raise ValueError('No */rlog.zst files found')
  route_names = {p.parent.name.rsplit('--', 1)[0] for p in paths}
  if len(route_names) != 1:
    raise ValueError('Provide exactly one route; segment numbers cannot identify multiple routes')
  rows, inventory = [], []
  for path in paths:
    sg = int(path.parent.name.rsplit('--', 1)[-1])
    records = {k: [] for k in SERVICES}
    counts = Counter()
    try:
      data = decompress_complete(path.read_bytes())
      for e in schema.Event.read_multiple_bytes(data, traversal_limit_in_words=2**30):
        k = e.which()
        counts[k] += 1
        if k not in records:
          continue
        x, t = getattr(e, k), e.logMonoTime * 1e-9
        valid = bool(e.valid)
        if k == 'carState':
          valid &= x.canValid and not (x.canTimeout or x.steerFaultTemporary or x.steerFaultPermanent or x.vehicleSensorsInvalid or x.espActive or x.steeringPressed)
          records[k].append([t, valid, x.vEgo, x.steeringAngleDeg, x.steeringTorque,
                             x.leftBlinker or x.rightBlinker, x.brakePressed or x.gasPressed])
        elif k == 'carControl':
          records[k].append([t, valid and x.latActive])
        elif k == 'carOutput':
          records[k].append([t, valid, x.actuatorsOutput.torque])
        else:
          records[k].append([t, valid and x.valid and x.sensorValid, x.roll])
      aligned = align_segment(records, sg, driver_limit, input_mode)
      missing = [k for k in SERVICES if not records[k]]
      inventory.append({'segment': sg, 'status': 'missing_services' if missing else 'ok', 'missing_services': missing,
                        'counts': {k: counts[k] for k in SERVICES}, 'grid_rows': len(aligned),
                        'eligible_rows': int(aligned[:, 6].sum())})
      rows.append(aligned)
    except Exception as exc:
      inventory.append({'segment': sg, 'status': 'discarded', 'error': type(exc).__name__ + ': ' + str(exc)[:160]})
    if progress:
      progress(inventory[-1])
  return np.concatenate(rows) if rows else np.empty((0, 8)), inventory
