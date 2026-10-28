"""Explicit developer checks; never imported by a vehicle process."""

import argparse
from collections import Counter
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
BASE = '604bc0a22733e648ec05b268da1ffaae7db52a69'
TESTS = [
  'openpilot/selfdrive/car/tests/test_actuation_receipt_schema.py',
  'openpilot/selfdrive/car/tests/test_actuation_receipt.py',
  'openpilot/selfdrive/car/tests/test_actuation_card.py',
  'openpilot/selfdrive/car/tests/test_actuation_can.py',
  'openpilot/selfdrive/controls/tests/test_lateral_observation.py',
  'openpilot/selfdrive/car/tests/test_card_diagnostics.py',
  'openpilot/selfdrive/car/tests/test_tesla_signal_card.py',
  'openpilot/selfdrive/controls/tests/test_angle_saturation.py',
  'openpilot/selfdrive/controls/tests/test_lateral_readiness.py',
  'openpilot/selfdrive/controls/tests/test_lateral_startup_control.py',
  'openpilot/selfdrive/controls/tests/test_lateral_event_readiness.py',
]


def lint():
  names = subprocess.check_output(['git', 'diff', '--name-only', BASE], cwd=ROOT, text=True).splitlines()
  names += subprocess.check_output(['git', 'ls-files', '--others', '--exclude-standard'], cwd=ROOT, text=True).splitlines()
  new_files = []
  baseline_count = current_count = 0
  for name in sorted(set(names)):
    if not name.endswith('.py'):
      continue
    path = ROOT / name
    if not path.is_file():
      continue
    old = subprocess.run(['git', 'show', BASE + ':' + name], cwd=ROOT, capture_output=True)
    if old.returncode:
      new_files.append(name)

    def diagnostics(data, name=name):
      result = subprocess.run(
        [sys.executable, '-m', 'ruff', 'check', '--output-format=json', '--stdin-filename', name, '-'],
        cwd=ROOT,
        input=data,
        stdout=subprocess.PIPE,
        check=False,
      )
      if result.returncode not in (0, 1):
        raise RuntimeError('ruff failed to execute')
      lines = data.decode().splitlines()
      return Counter((item['code'], item['message'], lines[item['location']['row'] - 1].strip()) for item in json.loads(result.stdout))

    before, after = diagnostics(old.stdout if old.returncode == 0 else b''), diagnostics(path.read_bytes())
    baseline_count += sum(before.values())
    current_count += sum(after.values())
    if after - before:
      raise RuntimeError(f'new lint diagnostics in {name}: {list((after - before).elements())}')
  if new_files:
    subprocess.run([sys.executable, '-m', 'ruff', 'format', '--check', *new_files], cwd=ROOT, check=True)
  print(json.dumps({'baseline_lint': baseline_count, 'current_lint': current_count, 'new_lint': 0, 'new_python_files': len(new_files)}, sort_keys=True))


def schema(include):
  env = dict(os.environ)
  env.pop('PWD', None)
  car = ROOT / 'opendbc_repo/opendbc/car'
  cereal = ROOT / 'openpilot/cereal'
  with tempfile.TemporaryDirectory(prefix='actuation-schema-') as output:
    command = ['capnp', 'compile', '-I' + str(car), '-I' + str(cereal), '-I' + include, '-oc++:' + output]
    subprocess.run([*command, 'car.capnp'], cwd=car, env=env, check=True)
    subprocess.run([*command, *sorted(p.name for p in cereal.glob('*.capnp'))], cwd=cereal, env=env, check=True)
    generated = sorted(str(p) for p in Path(output).glob('*.c++'))
    subprocess.run(['g++', '-std=c++17', '-fsyntax-only', '-I' + output, '-I' + include, *generated], cwd=ROOT, check=True)
    print(f'Generated and syntax-compiled {len(generated)} CapnProto C++ units')


def tests():
  env = dict(os.environ)
  env['PYTHONPATH'] = str(ROOT)
  # The upstream create_all() overwrites two tracked radar DBCs. Generate in
  # its existing isolated API instead and materialize only missing fixtures.
  fixture_code = '''
from pathlib import Path
from opendbc.dbc.generator.generator import generate_all
output = Path('opendbc_repo/opendbc/dbc')
for name, content in generate_all().items():
  target = output / (name + '.dbc')
  if not target.exists():
    target.write_text(content, encoding='utf-8')
'''
  subprocess.run([sys.executable, '-c', fixture_code], cwd=ROOT, env=env, check=True)
  subprocess.run(
    [
      sys.executable,
      '-m',
      'pytest',
      '-c',
      '/dev/null',
      '--rootdir=.',
      '--noconftest',
      '-q',
      '-p',
      'openpilot.selfdrive.car.tests.observability_support',
      '--cov=openpilot.selfdrive.car.actuation_receipt',
      '--cov=openpilot.selfdrive.controls.lib.lateral_observation',
      '--cov-branch',
      '--cov-report=term-missing:skip-covered',
      '--cov-fail-under=90',
      *TESTS,
    ],
    cwd=ROOT,
    env=env,
    check=True,
  )


if __name__ == '__main__':
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument('check', choices=['lint', 'schema', 'test'])
  parser.add_argument('--capnp-include', default='/usr/include')
  args = parser.parse_args()
  if args.check == 'lint':
    lint()
  elif args.check == 'schema':
    schema(args.capnp_include)
  else:
    tests()
