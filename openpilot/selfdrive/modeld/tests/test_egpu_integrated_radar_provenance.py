"""Hardware-free regression for the upstream a92d3a source/bundle contract."""
import ast
import hashlib
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from tools.carrot_route_vault import build_bundle

ROOT = Path(__file__).resolve().parents[4]
EXPORT = "openpilot/selfdrive/carrot/radar/tools/radar_web_export.py"
DBC = "opendbc_repo/opendbc/dbc/generator/hyundai/hyundai_canfd_radar.dbc"


def source_hash(root):
  module = ast.parse((ROOT / EXPORT).read_text(encoding="utf-8"))
  function = next(node for node in module.body if isinstance(node, ast.FunctionDef) and node.name == "source_version")
  namespace = {"Path": Path, "hashlib": hashlib, "__file__": str(root / EXPORT),
               "replay": SimpleNamespace(REPO_ROOT=root, CARROT_ROOT=root / "openpilot/selfdrive/carrot",
                                         __file__=str(root / "openpilot/selfdrive/carrot/radar/tools/radar_validation_replay.py"))}
  exec(compile(ast.Module(body=[function], type_ignores=[]), EXPORT, "exec"), namespace)
  return namespace["source_version"]()


class TestRadarSourceProvenance(unittest.TestCase):
  def test_source_hash_includes_nested_opendbc_source_and_hyundai_dbc(self):
    # Execute the production hash function without importing the hardware/log
    # replay stack, against a synthetic filesystem with no vehicle evidence.
    module = ast.parse((ROOT / EXPORT).read_text(encoding="utf-8"))
    function = next(node for node in module.body if isinstance(node, ast.FunctionDef) and node.name == "source_version")
    with tempfile.TemporaryDirectory() as directory:
      root = Path(directory)
      paths = [EXPORT, DBC, "openpilot/selfdrive/carrot/radar/tools/radar_validation_replay.py",
               "opendbc_repo/opendbc/car/hyundai/radar_interface.py", "opendbc_repo/opendbc/car/car.capnp"]
      for name in paths:
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(name + "\n", encoding="utf-8")
      namespace = {"Path": Path, "hashlib": hashlib, "__file__": str(root / EXPORT),
                   "replay": SimpleNamespace(REPO_ROOT=root, CARROT_ROOT=root / "openpilot/selfdrive/carrot",
                                             __file__=str(root / paths[2]))}
      exec(compile(ast.Module(body=[function], type_ignores=[]), EXPORT, "exec"), namespace)
      source_version = namespace["source_version"]
      baseline = source_version()
      digest = hashlib.sha256()
      for path in sorted(root / name for name in paths):
        digest.update(path.relative_to(root).as_posix().encode())
        digest.update(path.read_bytes())
      self.assertEqual(baseline, digest.hexdigest()[:20])
      for name in (DBC, paths[3], paths[4]):
        with self.subTest(path=name):
          path = root / name
          old = path.read_bytes()
          path.write_bytes(old + b"source change\n")
          self.assertNotEqual(source_version(), baseline)
          path.write_bytes(old)
          self.assertEqual(source_version(), baseline)

  def test_bundle_copies_committed_hyundai_dbc_and_bindings(self):
    with tempfile.TemporaryDirectory() as directory:
      root = Path(directory) / "repo"
      root.mkdir()
      def git(*args):
        return subprocess.check_output(["git", "-C", str(root), *args], text=True).strip()
      git("init", "--quiet")
      git("config", "core.autocrlf", "false")
      sources = {DBC: b"committed Hyundai DBC\n", "opendbc_repo/opendbc/__init__.py": b"# package\n",
                 "opendbc_repo/opendbc/car/hyundai/radar_interface.py": b"# Python binding\n",
                 "opendbc_repo/opendbc/car/car.capnp": b"# capnp binding\n",
                 "opendbc_repo/opendbc/can/parser.py": b"# CAN parser\n",
                 "openpilot/selfdrive/controls/lib/longitudinal_cutout.py": b"# control source identity only\n",
                 "opendbc_repo/opendbc/car/ignored.bin": b"excluded binary\n",
                 EXPORT: b"# exporter source\n",
                 "openpilot/selfdrive/carrot/radar/tools/radar_validation_replay.py": b"# replay source\n"}
      for name, data in sources.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
      git("add", ".")
      git("-c", "user.name=Offline fixture", "-c", "user.email=fixture@example.invalid", "commit", "--quiet", "-m", "fixture")
      commit = git("rev-parse", "HEAD")
      (root / DBC).write_bytes(b"uncommitted edit must not enter bundle\n")
      destination = Path(directory) / "bundle"
      with patch.object(build_bundle, "ROOT", root):
        build_bundle.build(destination, commit)
      for name, data in sources.items():
        if name.endswith(".bin"):
          self.assertFalse((destination / name).exists())
        else:
          self.assertEqual((destination / name).read_bytes(), data)
      self.assertEqual((destination / "SOURCE_COMMIT").read_text().strip(), commit)
      self.assertNotEqual(source_hash(root), source_hash(destination))  # dirty DBC remains detectable
      (root / DBC).write_bytes(sources[DBC])
      self.assertEqual(source_hash(root), source_hash(destination))

  def test_real_committed_hyundai_dbc_is_selected_by_bundle(self):
    paths = subprocess.check_output(["git", "ls-tree", "-r", "--name-only", "HEAD", "--", *build_bundle.SOURCES],
                                    cwd=ROOT, text=True).splitlines()
    self.assertIn(DBC, paths)


if __name__ == "__main__":
  unittest.main()
