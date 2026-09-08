from copy import deepcopy
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from tools import egpu_integrated_source_compat as compat

from tools.egpu_integrated_source_compat import (
  CRITICAL_PATHS, PROVENANCE_PATHS, REVIEWED_HEAD, UPSTREAM_ABSENT_LOCAL_PATHS, V6_MIGRATION_PATHS, blob, classify,
)


class TestEgpuIntegratedSourceCompat(unittest.TestCase):
  def test_exact_reviewed(self):
    reviewed = {"a": "1", "b": "2"}
    status, changed = classify("head", "head", reviewed, dict(reviewed))
    self.assertEqual(status, "EXACT_REVIEWED")
    self.assertEqual(changed, [])

  def test_every_model_contract_interface_is_watched(self):
    from openpilot.selfdrive.modeld.egpu_integrated_model_contract import INTERFACE_PATHS
    self.assertTrue(set(INTERFACE_PATHS).issubset(CRITICAL_PATHS))

  def test_code_equivalent_head_drift(self):
    reviewed = {"a": "1", "b": "2"}
    status, changed = classify("old", "new", reviewed, dict(reviewed))
    self.assertEqual(status, "CODE_EQUIVALENT_HEAD_DRIFT")
    self.assertEqual(changed, [])

  def test_critical_change_requires_review(self):
    reviewed = {"a": "1", "b": "2"}
    upstream = {"a": "1", "b": "3"}
    status, changed = classify("old", "new", reviewed, upstream)
    self.assertEqual(status, "REVIEW_REQUIRED")
    self.assertEqual(changed, ["b"])

  def test_missing_critical_file_requires_review(self):
    reviewed = {"a": "1"}
    status, changed = classify("old", "new", reviewed, {"a": None})
    self.assertEqual(status, "REVIEW_REQUIRED")
    self.assertEqual(changed, ["a"])

  def test_missing_both_is_not_evidence_of_compatibility(self):
    status, changed = classify("head", "head", {"required": None}, {"required": None})
    self.assertEqual((status, changed), ("REVIEW_REQUIRED", ["required"]))

  def test_all_watched_surfaces_require_review_for_change_or_deletion(self):
    paths = CRITICAL_PATHS + V6_MIGRATION_PATHS + PROVENANCE_PATHS
    reviewed = dict.fromkeys(paths, "original-object")
    for path in paths:
      for replacement in ("new-object", None):
        with self.subTest(path=path, replacement=replacement):
          upstream = dict(reviewed, **{path: replacement})
          self.assertEqual(classify("old", "new", reviewed, upstream), ("REVIEW_REQUIRED", [path]))

  def test_deliberately_local_paths_still_detect_upstream_addition(self):
    reviewed = dict.fromkeys(UPSTREAM_ABSENT_LOCAL_PATHS)
    self.assertEqual(classify("old", "new", reviewed, reviewed, allowed_absent=UPSTREAM_ABSENT_LOCAL_PATHS)[0],
                     "CODE_EQUIVALENT_HEAD_DRIFT")
    for path in reviewed:
      upstream = dict(reviewed, **{path: "new-upstream-file"})
      self.assertEqual(classify("old", "new", reviewed, upstream, allowed_absent=UPSTREAM_ABSENT_LOCAL_PATHS),
                       ("REVIEW_REQUIRED", [path]))

  def test_unexpected_upstream_map_entry_requires_review(self):
    self.assertEqual(classify("old", "new", {"a": "1"}, {"a": "1", "b": "2"}),
                     ("REVIEW_REQUIRED", ["b"]))

  def test_bad_revision_is_not_silently_missing(self):
    with self.assertRaises(RuntimeError):
      blob("invalid-reviewed-ref-that-does-not-exist", CRITICAL_PATHS[0])

  def test_new_files_in_amd_tree_require_review(self):
    import subprocess
    import tempfile
    from pathlib import Path
    with tempfile.TemporaryDirectory() as directory:
      root = Path(directory)
      def git(*args):
        return subprocess.check_output(["git", "-C", directory, *args], text=True).strip()
      git("init", "--quiet")
      folder = root / "tinygrad_repo/tinygrad/runtime/support/am"
      folder.mkdir(parents=True)
      (folder / "amdev.py").write_text("old implementation\n")
      git("add", ".")
      tree = git("write-tree")
      path = "tinygrad_repo/tinygrad/runtime/support/am"
      before = git("rev-parse", f"{tree}:{path}")
      (folder / "new_transport.py").write_text("new transport\n")
      git("add", ".")
      tree = git("write-tree")
      after = git("rev-parse", f"{tree}:{path}")
      self.assertIn(path, CRITICAL_PATHS)
      self.assertEqual(classify("old", "new", {path: before}, {path: after}), ("REVIEW_REQUIRED", [path]))

  def test_v6_no_path_drift_uses_distinct_label(self):
    reviewed = {"planner": "1"}
    status, changed = classify(
      "old", "new", reviewed, dict(reviewed),
      equivalent_label="NO_V6_PATH_DRIFT",
      review_label="V6_REBASE_REVIEW_REQUIRED",
    )
    self.assertEqual(status, "NO_V6_PATH_DRIFT")
    self.assertEqual(changed, [])

  def test_v6_path_drift_requires_rebase_review(self):
    reviewed = {"planner": "1"}
    status, changed = classify(
      "old", "new", reviewed, {"planner": "2"},
      equivalent_label="NO_V6_PATH_DRIFT",
      review_label="V6_REBASE_REVIEW_REQUIRED",
    )
    self.assertEqual(status, "V6_REBASE_REVIEW_REQUIRED")
    self.assertEqual(changed, ["planner"])

  def test_reviewed_head_is_latest_explicitly_reviewed_upstream(self):
    self.assertEqual(REVIEWED_HEAD, "b7ab68addc29c3b9dc8f02023de36dba7109ed8c")

  def test_radar_cutout_behavior_surfaces_are_watched(self):
    required = {
      "openpilot/selfdrive/carrot/radar/radard_dpath.py",
      "openpilot/selfdrive/carrot/radar_motion/controller.py",
      "openpilot/selfdrive/carrot/radar_motion/trajectory_cutout.py",
      "openpilot/selfdrive/controls/lib/longitudinal_cutout.py",
      "openpilot/selfdrive/controls/lib/longitudinal_mpc_lib/long_mpc.py",
      "openpilot/selfdrive/controls/lib/longitudinal_planner.py",
      "openpilot/cereal/log.capnp",
    }
    self.assertTrue(required.issubset(set(V6_MIGRATION_PATHS)))

  def test_retained_changes_remain_watched_for_future_additions_and_deletions(self):
    self.assertTrue({"openpilot/selfdrive/controls", "panda/board"}.issubset(CRITICAL_PATHS))
    self.assertTrue({"tools/carrot_route_vault", "opendbc_repo/opendbc"}.issubset(PROVENANCE_PATHS))
    self.assertEqual(len(compat.RETAINED_TESLA_PATHS), 14)
    self.assertEqual(len(compat.SELECTED_RADAR_PATHS), 6)
    for path in ("openpilot/selfdrive/controls", "panda/board", "tools/carrot_route_vault", "opendbc_repo/opendbc"):
      for changed in ("tree-with-new-file", "tree-with-deleted-file", None):
        with self.subTest(path=path, changed=changed):
          self.assertEqual(classify("old", "new", {path: "old-tree"}, {path: changed}), ("REVIEW_REQUIRED", [path]))


class TestReviewedDisposition(unittest.TestCase):
  def setUp(self):
    # A fixed synthetic Git-object map tests the policy parser without changing
    # the user's checkout or making each malformed-row case invoke dozens of Git
    # subprocesses. One separate test checks the real reviewed objects.
    self.record = json.loads((compat.ROOT / compat.DISPOSITION_PATH).read_text(encoding="utf-8"))
    self.record["selectedSourceHead"] = compat.SELECTED_SOURCE_HEAD
    self.objects = {}
    for row in self.record["paths"]:
      self.objects[(compat.PREVIOUS_REVIEWED_HEAD, row["path"])] = row["previousUpstreamObject"]
      self.objects[(compat.REVIEWED_HEAD, row["path"])] = row["reviewedUpstreamObject"]
      ref = compat.SELECTED_SOURCE_HEAD if row["path"] in compat.SELECTED_RADAR_PATHS else compat.RETAINED_SOURCE_HEAD
      self.objects[(ref, row["path"])] = row["expectedIntegratedObject"]
    for row in self.record["localProtectedTrees"]:
      self.objects[(compat.RETAINED_SOURCE_HEAD, row["path"])] = row["expectedIntegratedObject"]
    self.git_patch = patch.object(compat, "git_object", side_effect=lambda ref, path: deepcopy(self.objects[(ref, path)]))
    self.run_patch = patch.object(compat, "run", side_effect=self.fake_run)
    self.git_patch.start()
    self.run_patch.start()
    self.addCleanup(self.git_patch.stop)
    self.addCleanup(self.run_patch.stop)

  def fake_run(self, *args):
    if args[:3] == ("git", "cat-file", "-t"):
      return "commit"
    if args[:4] == ("git", "diff", "--name-only", "--no-renames"):
      return "\n".join(sorted(compat.SELECTED_RADAR_PATHS | compat.RETAINED_TESLA_PATHS | {"AGENTS.md"}))
    if args == ("git", "rev-parse", "HEAD"):
      return "1" * 40
    raise AssertionError(f"unexpected fixture Git command: {args}")

  def test_exact_record_covers_every_reviewed_change_without_runtime_permission(self):
    compat.validate_disposition(deepcopy(self.record))
    decisions = [row["disposition"] for row in self.record["paths"]]
    self.assertEqual(decisions.count("SELECTIVE_INTEGRATION"), 6)
    self.assertEqual(decisions.count("RETAIN_INTEGRATED"), 14)
    self.assertEqual(decisions.count("RETAIN_USER_INSTRUCTIONS"), 1)
    self.assertEqual({row["path"] for row in self.record["paths"] if row["expectedIntegratedObject"] is None}, compat.RETAINED_ABSENT_PATHS)

  def test_missing_extra_duplicate_or_changed_decision_cannot_expand_exclusion(self):
    variants = []
    value = deepcopy(self.record)
    value["paths"].pop()
    variants.append(value)
    value = deepcopy(self.record)
    value["paths"].append(deepcopy(value["paths"][0]))
    variants.append(value)
    value = deepcopy(self.record)
    value["paths"][0]["path"] = "unreviewed/new-control.py"
    variants.append(value)
    value = deepcopy(self.record)
    next(row for row in value["paths"] if row["path"] in compat.RETAINED_TESLA_PATHS)["disposition"] = "SELECTIVE_INTEGRATION"
    variants.append(value)
    for index, value in enumerate(variants):
      with self.subTest(index=index), self.assertRaises(ValueError):
        compat.validate_disposition(value)

  def test_forged_remote_retained_selected_and_tree_objects_are_rejected(self):
    for field, path in (("previousUpstreamObject", "AGENTS.md"), ("reviewedUpstreamObject", "AGENTS.md"),
                        ("expectedIntegratedObject", "AGENTS.md"),
                        ("expectedIntegratedObject", "tools/carrot_route_vault/radar_view.js")):
      with self.subTest(field=field, path=path):
        value = deepcopy(self.record)
        next(row for row in value["paths"] if row["path"] == path)[field]["oid"] = "f" * 40
        with self.assertRaises(ValueError):
          compat.validate_disposition(value)
    value = deepcopy(self.record)
    value["localProtectedTrees"][0]["expectedIntegratedObject"]["oid"] = "f" * 40
    with self.assertRaises(ValueError):
      compat.validate_disposition(value)

  def test_strict_types_modes_absence_and_source_heads(self):
    for field, bad in (("mode", "120000"), ("mode", "100755"), ("type", "tree"), ("oid", "abc")):
      with self.subTest(field=field, bad=bad):
        value = deepcopy(self.record)
        value["paths"][0]["reviewedUpstreamObject"][field] = bad
        with self.assertRaises(ValueError):
          compat.validate_disposition(value)
    for key in ("previousReviewedHead", "reviewedUpstreamHead", "retainedSourceHead", "selectedSourceHead"):
      value = deepcopy(self.record)
      value[key] = "f" * 40
      with self.subTest(key=key), self.assertRaises(ValueError):
        compat.validate_disposition(value)
    for version in (True, "1", 1.0):
      with self.subTest(version=version), self.assertRaises(ValueError):
        compat.validate_disposition({**self.record, "schemaVersion": version})
    value = deepcopy(self.record)
    value["paths"][0]["expectedIntegratedObject"] = None
    with self.assertRaises(ValueError):
      compat.validate_disposition(value)

  def test_complete_delta_and_protected_tree_coverage_are_mandatory(self):
    original_run = self.fake_run
    def changed_delta(*args):
      result = original_run(*args)
      return result + "\nunreviewed-new.py" if args[:2] == ("git", "diff") else result
    with patch.object(compat, "run", side_effect=changed_delta), self.assertRaises(ValueError):
      compat.validate_disposition(self.record)
    value = deepcopy(self.record)
    value["localProtectedTrees"].pop()
    with self.assertRaises(ValueError):
      compat.validate_disposition(value)

  def test_commit_type_cannot_be_replaced_by_a_tree(self):
    with patch.object(compat, "run", return_value="tree"), self.assertRaises(ValueError):
      compat.validate_disposition(self.record)

  def test_reviewed_retention_is_not_a_future_remote_or_local_exemption(self):
    with patch.object(compat, "local_path_issues", return_value=[]):
      report = compat.disposition_report(self.record, compat.REVIEWED_HEAD)
      self.assertEqual(report["status"], "REVIEWED_WITH_EXCLUSIONS")
      self.assertFalse(report["upstreamFullyIntegrated"])
      self.assertEqual(report["verificationScope"], "WATCHED_BOUNDARIES_ONLY")
      future = "d" * 40
      for row in self.record["paths"]:
        self.objects[(future, row["path"])] = row["reviewedUpstreamObject"]
      for changed in (None, {"mode": "100644", "type": "blob", "oid": "e" * 40}):
        with self.subTest(changed=changed):
          self.objects[(future, "AGENTS.md")] = changed
          report = compat.disposition_report(self.record, future)
          self.assertEqual(report["status"], "REVIEW_REQUIRED")
          self.assertEqual(report["changedRemotePaths"], ["AGENTS.md"])
    with patch.object(compat, "local_path_issues", return_value=["worktree_content_mismatch"]):
      report = compat.disposition_report(self.record, compat.REVIEWED_HEAD)
      self.assertEqual(report["status"], "REVIEW_REQUIRED")
      self.assertTrue(report["changedLocalPaths"])


class TestRetainedWorkingSource(unittest.TestCase):
  def setUp(self):
    self.temporary = tempfile.TemporaryDirectory()
    self.addCleanup(self.temporary.cleanup)
    self.root = Path(self.temporary.name)
    self.git("init", "--quiet")
    self.git("config", "core.autocrlf", "false")
    (self.root / "retained.py").write_bytes(b"reviewed source\n")
    (self.root / ".gitignore").write_bytes(b"absent.py\n")
    (self.root / "controls/lib").mkdir(parents=True)
    (self.root / "controls/lib/other_control.py").write_bytes(b"reviewed control\n")
    (self.root / "controls/.gitignore").write_bytes(b"ignored_*\n__pycache__/\n")
    self.git("add", ".")
    self.git("-c", "user.name=Offline fixture", "-c", "user.email=fixture@example.invalid", "commit", "--quiet", "-m", "fixture")
    self.root_patch = patch.object(compat, "ROOT", self.root)
    self.root_patch.start()
    self.addCleanup(self.root_patch.stop)
    self.expected = compat.git_object("HEAD", "retained.py")
    self.expected_tree = compat.git_object("HEAD", "controls")

  def git(self, *args):
    return subprocess.run(["git", "-C", str(self.root), *args], check=True, capture_output=True, text=True).stdout.strip()

  def test_committed_index_and_worktree_match(self):
    self.assertEqual(compat.local_path_issues("retained.py", self.expected), [])
    self.assertEqual(compat.local_path_issues("absent.py", None), [])

  def test_working_bytes_are_checked_despite_assume_unchanged(self):
    self.git("update-index", "--assume-unchanged", "retained.py")
    (self.root / "retained.py").write_bytes(b"unreviewed working source\n")
    self.assertIn("worktree_content_mismatch", compat.local_path_issues("retained.py", self.expected))

  def test_index_content_and_mode_changes_are_rejected(self):
    (self.root / "retained.py").write_bytes(b"unreviewed index source\n")
    self.git("add", "retained.py")
    self.assertIn("index_object_mismatch", compat.local_path_issues("retained.py", self.expected))
    self.git("reset", "--hard", "HEAD")
    self.git("update-index", "--chmod=+x", "retained.py")
    self.assertIn("index_object_mismatch", compat.local_path_issues("retained.py", self.expected))

  def test_expected_absence_detects_even_ignored_file(self):
    (self.root / "absent.py").write_bytes(b"unexpected source\n")
    self.assertIn("expected_absent_path_exists", compat.local_path_issues("absent.py", None))

  def test_changed_committed_object_is_rejected(self):
    (self.root / "retained.py").write_bytes(b"new committed source\n")
    self.git("add", "retained.py")
    self.git("-c", "user.name=Offline fixture", "-c", "user.email=fixture@example.invalid", "commit", "--quiet", "-m", "changed")
    self.assertIn("committed_object_mismatch", compat.local_path_issues("retained.py", self.expected))

  def test_hidden_tracked_control_edit_invalidates_protected_tree(self):
    self.assertEqual(compat.local_path_issues("controls", self.expected_tree), [])
    for flag in ("--assume-unchanged", "--skip-worktree"):
      with self.subTest(flag=flag):
        self.git("update-index", "--no-assume-unchanged", "--no-skip-worktree", "controls/lib/other_control.py")
        (self.root / "controls/lib/other_control.py").write_bytes(b"reviewed control\n")
        self.git("update-index", flag, "controls/lib/other_control.py")
        (self.root / "controls/lib/other_control.py").write_bytes(b"hidden changed control\n")
        self.assertIn("worktree_content_mismatch:controls/lib/other_control.py", compat.local_path_issues("controls", self.expected_tree))

  def test_ignored_new_source_invalidates_tree_but_generated_cache_does_not(self):
    cache = self.root / "controls/__pycache__"
    cache.mkdir()
    (cache / "generated.pyc").write_bytes(b"generated cache")
    self.assertEqual(compat.local_path_issues("controls", self.expected_tree), [])
    (self.root / "controls/ignored_new.py").write_bytes(b"new ignored source\n")
    self.assertIn("unreviewed_worktree_source:controls/ignored_new.py", compat.local_path_issues("controls", self.expected_tree))

  def test_protected_directory_and_parent_junctions_are_rejected(self):
    original = Path.is_junction
    protected = self.root / "controls"
    for link in (protected, protected / "lib"):
      with self.subTest(link=link):
        with patch.object(Path, "is_junction", lambda path: path == link or original(path)):
          issues = compat.local_path_issues("controls", self.expected_tree)
        self.assertTrue(any("nonregular" in issue for issue in issues), issues)

  def test_tree_index_leaf_set_and_mode_must_match(self):
    self.git("update-index", "--chmod=+x", "controls/lib/other_control.py")
    self.assertIn("index_tree_mismatch", compat.local_path_issues("controls", self.expected_tree))
    (self.root / "controls/new_source.py").write_bytes(b"new source\n")
    self.git("add", "controls/new_source.py")
    self.assertIn("index_tree_mismatch", compat.local_path_issues("controls", self.expected_tree))

  def test_bulk_hashing_keeps_byte_exact_legacy_crlf_sources_valid(self):
    legacy = self.root / "controls/lib/legacy.h"
    legacy.write_bytes(b"legacy CRLF source\r\n")
    self.git("add", "controls/lib/legacy.h")
    self.git("-c", "user.name=Offline fixture", "-c", "user.email=fixture@example.invalid", "commit", "--quiet", "-m", "legacy")
    (self.root / ".gitattributes").write_bytes(b"* text=auto\n")
    expected = compat.git_object("HEAD", "controls")
    self.assertEqual(compat.local_path_issues("controls", expected), [])
    legacy.write_bytes(b"changed CRLF source\r\n")
    self.assertIn("worktree_content_mismatch:controls/lib/legacy.h", compat.local_path_issues("controls", expected))


if __name__ == "__main__":
  unittest.main()
