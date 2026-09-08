"""Adversarial, hardware-free checks of portable offline replay review evidence."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, replace
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from openpilot.selfdrive.modeld.egpu_integrated_guardian import GuardianPolicy
from openpilot.selfdrive.modeld.egpu_integrated_guardian_temporal import TemporalHeuristicPolicy
from openpilot.selfdrive.modeld.egpu_integrated_model_contract import build_contract, contract_payload, interface_fingerprint
from tools.egpu_integrated_fault_injection import fallback_fault, replay_row
from tools import egpu_integrated_review_bundle as bundle_tool


ROOT = Path(__file__).resolve().parents[4]
MEMBERS = {"rows.jsonl", "model-contract.json", "policy.json", "events.jsonl", "summary.json", "review-queue.json"}
FALSE_BOUNDARIES = {
  "controlAuthorization", "publicRoadAuthorization", "hardwareValidationPerformed", "commissioningEligible",
  "modelExecutionVerified", "producerAssertionsVerified",
}


def write_json(path: Path, value) -> None:
  path.write_text(json.dumps(value, sort_keys=True, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def read_json(path: Path):
  return json.loads(path.read_text(encoding="utf-8"))


def rehash_member_and_manifest(directory: Path, name: str) -> dict:
  """Simulate an editor who can rewrite every in-bundle hash, but no trusted receipt."""
  manifest = read_json(directory / "manifest.json")
  content = (directory / name).read_bytes()
  manifest["files"][name] = {"sha256": hashlib.sha256(content).hexdigest(), "size": len(content)}
  manifest["bundleId"] = bundle_tool._bundle_id(manifest)
  (directory / "manifest.json").write_bytes(bundle_tool._json_bytes(manifest))
  return manifest


class TestOfflineReviewBundle(unittest.TestCase):
  @classmethod
  def setUpClass(cls):
    # Bind fixtures to committed local interfaces/LFS metadata without executing a
    # model. The development/CI branch is supplied explicitly in each operation.
    cls.contract = build_contract(ROOT, expected_branch=None)
    hashes = {name: "b" * 64 for name in bundle_tool.ANALYZER_PATHS}
    cls.analyzer_identity = {
      "head": cls.contract.source.head,
      "branch": cls.contract.source.branch,
      "sourceNormalization": "utf8-lf",
      "sourceHashes": hashes,
      "sourceFingerprint": hashlib.sha256(bundle_tool._json_bytes(hashes)).hexdigest(),
    }

  def setUp(self):
    self.temporary = tempfile.TemporaryDirectory()
    self.addCleanup(self.temporary.cleanup)
    self.directory = Path(self.temporary.name)
    self.rows_path = self.directory / "provided-rows.jsonl"
    self.contract_path = self.directory / "provided-contract.json"
    self.policy_path = self.directory / "provided-policy.json"
    self.head = self.contract.source.head
    self.branch = self.contract.source.branch
    self.output_count = 0
    self.rows = [
      replay_row(1, cutout_time=0.0, cutout_confidence=0.0),
      replay_row(2, stop_shadow=True, cutout_time=0.7, cutout_confidence=0.4),
      replay_row(3, active_backend="qcom", fault=fallback_fault(3)),
      replay_row(4, active_backend="qcom", fault=fallback_fault(4, outputFrameId=3)),
    ]
    for row in self.rows:
      row.update(sourceHead=self.head, sourceBranch=self.branch)
    self.policy = {"schemaVersion": 1, "guardian": asdict(GuardianPolicy()), "temporal": asdict(TemporalHeuristicPolicy())}
    self.write_rows(self.rows)
    write_json(self.contract_path, contract_payload(self.contract))
    write_json(self.policy_path, self.policy)
    # New/edited analyzer files are necessarily uncommitted during unit tests.
    # Real source-object binding remains active; only analyzer identity is fixed.
    self.identity_patch = patch.object(bundle_tool, "_capture_analyzer_identity", side_effect=lambda: deepcopy(self.analyzer_identity))
    self.identity_patch.start()
    self.addCleanup(self.identity_patch.stop)
    self.analyzer_commit_patch = patch.object(bundle_tool, "_verify_analyzer_commit")
    self.analyzer_commit_patch.start()
    self.addCleanup(self.analyzer_commit_patch.stop)

  def write_rows(self, rows) -> None:
    self.rows_path.write_text("".join(json.dumps(row, allow_nan=False) + "\n" for row in rows), encoding="utf-8")

  def build(self, **changes):
    self.output_count += 1
    output = self.directory / f"bundle-{self.output_count}"
    kwargs = {
      "expected_source_head": self.head,
      "expected_source_branch": self.branch,
      "evidence_kind": "synthetic",
    }
    kwargs.update(changes)
    result = bundle_tool.build_review_bundle(self.rows_path, self.contract_path, self.policy_path, output, **kwargs)
    return output, result

  def verify(self, output, manifest, **changes):
    kwargs = {
      "expected_bundle_id": manifest["bundleId"],
      "expected_source_head": self.head,
      "expected_source_branch": self.branch,
    }
    kwargs.update(changes)
    return bundle_tool.verify_review_bundle(output, **kwargs)

  def assert_no_authorization(self, value):
    if isinstance(value, dict):
      for key, child in value.items():
        if key.endswith("Authorization") or key in FALSE_BOUNDARIES or key == "shadowPublishToControls":
          self.assertIs(child, False, key)
        self.assert_no_authorization(child)
    elif isinstance(value, list):
      for child in value:
        self.assert_no_authorization(child)

  def test_repeated_builds_are_identical_and_bind_exact_input_bytes(self):
    originals = {path: path.read_bytes() for path in (self.rows_path, self.contract_path, self.policy_path)}
    first, first_manifest = self.build()
    second, second_manifest = self.build()
    self.assertEqual(first_manifest, second_manifest)
    self.assertEqual(set(first_manifest["files"]), MEMBERS)
    self.assertEqual({p.name for p in first.iterdir()}, MEMBERS | {"manifest.json"})
    for name in MEMBERS | {"manifest.json"}:
      self.assertEqual((first / name).read_bytes(), (second / name).read_bytes(), name)
    for name, path in (("rows.jsonl", self.rows_path), ("model-contract.json", self.contract_path), ("policy.json", self.policy_path)):
      self.assertEqual((first / name).read_bytes(), originals[path])
    for name, entry in first_manifest["files"].items():
      content = (first / name).read_bytes()
      self.assertEqual(entry, {"size": len(content), "sha256": hashlib.sha256(content).hexdigest()})
    self.assertEqual({path: path.read_bytes() for path in originals}, originals)
    self.assertEqual(self.verify(first, first_manifest)["status"], "VERIFIED_OFFLINE_CONSISTENCY")

  def test_real_replay_fixture_preserves_fault_and_cutout_review_outcomes(self):
    output, manifest = self.build()
    events = [json.loads(line) for line in (output / "events.jsonl").read_text(encoding="utf-8").splitlines()]
    self.assertEqual(len(events), 4)
    self.assertEqual(events[1]["combinedReviewBucket"], "PRIORITY_REVIEW")
    self.assertIn("lead_cutout_prediction_onset", events[1]["guardian"]["temporalTags"])
    self.assertIn("stop_disagreement_onset", events[1]["guardian"]["temporalTags"])
    self.assertTrue(events[2]["fault"]["sameFrameOutputPreserved"])
    self.assertEqual(events[3]["combinedReviewBucket"], "ROOT_CAUSE")
    self.assertIsNot(events[3]["fault"]["sameFrameOutputPreserved"], True)
    self.assertEqual(read_json(output / "summary.json")["rows"], 4)
    queue = read_json(output / "review-queue.json")
    self.assertEqual(queue["queue"][0]["bucket"], "ROOT_CAUSE")
    self.assertEqual(queue["eventsRepresented"], 4)
    self.assertEqual(manifest["declaredModelContractId"], self.contract.resolved_contract_id)

  def test_every_supported_evidence_kind_remains_descriptive(self):
    for evidence_kind in ("synthetic", "provided_offline"):
      with self.subTest(evidence_kind=evidence_kind):
        output, manifest = self.build(evidence_kind=evidence_kind)
        verified = self.verify(output, manifest)
        self.assertEqual(manifest["evidenceKind"], evidence_kind)
        self.assertEqual(verified["evidenceKind"], evidence_kind)
        self.assertTrue(verified["analyzerSourceMatch"])
        for key in FALSE_BOUNDARIES:
          self.assertIs(manifest["policy"][key], False, key)
          self.assertIs(verified[key], False, key)
        for path in output.iterdir():
          values = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()] if path.suffix == ".jsonl" else [read_json(path)]
          for value in values:
            self.assert_no_authorization(value)
            json.dumps(value, allow_nan=False)

  def test_rejects_unknown_evidence_kind(self):
    for kind in ("hardware", "commissioning_pass", "provided-offline", "", None, True):
      with self.subTest(kind=kind), self.assertRaises((ValueError, TypeError)):
        self.build(evidence_kind=kind)

  def test_member_tampering_is_detected_before_review(self):
    for name in MEMBERS:
      with self.subTest(name=name):
        output, manifest = self.build()
        with (output / name).open("ab") as member:
          member.write(b" ")
        with self.assertRaises(ValueError):
          self.verify(output, manifest)

  def test_rewritten_member_hashes_cannot_replace_external_bundle_id(self):
    output, original = self.build()
    summary = read_json(output / "summary.json")
    summary["rows"] = 0
    write_json(output / "summary.json", summary)
    forged = rehash_member_and_manifest(output, "summary.json")
    self.assertNotEqual(forged["bundleId"], original["bundleId"])
    with self.assertRaises(ValueError):
      self.verify(output, original)

  def test_recomputed_manifest_cannot_forge_derived_outcomes(self):
    for name in ("events.jsonl", "summary.json", "review-queue.json"):
      with self.subTest(name=name):
        output, _ = self.build()
        if name == "events.jsonl":
          events = [json.loads(line) for line in (output / name).read_text(encoding="utf-8").splitlines()]
          events[-1]["combinedReviewBucket"] = "OBSERVE"
          (output / name).write_text("".join(json.dumps(event) + "\n" for event in events), encoding="utf-8")
        else:
          value = read_json(output / name)
          value["rows" if name == "summary.json" else "eventsRepresented"] = 0
          write_json(output / name, value)
        forged = rehash_member_and_manifest(output, name)
        # Even someone supplying the new content ID cannot conceal outcomes
        # which no longer follow from the enclosed rows/contract/policy.
        with self.assertRaises(ValueError):
          self.verify(output, forged)

  def test_recomputed_policy_hash_requires_outcomes_from_that_policy(self):
    output, _ = self.build()
    policy = read_json(output / "policy.json")
    policy["guardian"]["max_execution_ms"] = 1.0
    write_json(output / "policy.json", policy)
    forged = rehash_member_and_manifest(output, "policy.json")
    with self.assertRaises(ValueError):
      self.verify(output, forged)

  def test_recomputed_rows_hash_requires_outcomes_from_those_rows(self):
    output, _ = self.build()
    rows = deepcopy(self.rows)
    rows[0]["active"]["acceleration"] = 20.0
    (output / "rows.jsonl").write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    forged = rehash_member_and_manifest(output, "rows.jsonl")
    with self.assertRaises(ValueError):
      self.verify(output, forged)

  def test_empty_and_nonobject_rows_leave_no_bundle(self):
    for text in ("", " \n\n", "[]\n", "null\n", "true\n"):
      with self.subTest(text=text):
        self.rows_path.write_text(text, encoding="utf-8")
        with self.assertRaises(ValueError):
          self.build()
        self.assertFalse((self.directory / f"bundle-{self.output_count}").exists())

  def test_duplicate_json_keys_are_rejected_in_every_input(self):
    for path in (self.rows_path, self.contract_path, self.policy_path):
      with self.subTest(input=path.name):
        original = path.read_text(encoding="utf-8")
        duplicate = '"sourceHead": "' + self.head + '",' if path == self.rows_path else '"schemaVersion": 1,'
        path.write_text(original.replace("{", "{" + duplicate, 1), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "(?i)duplicate"):
          self.build()
        path.write_text(original, encoding="utf-8")

  def test_duplicate_nested_row_keys_are_rejected(self):
    original = self.rows_path.read_text(encoding="utf-8")
    self.rows_path.write_text(original.replace('"frameAge": 0', '"frameAge": 0, "frameAge": 1', 1), encoding="utf-8")
    with self.assertRaisesRegex(ValueError, "(?i)duplicate"):
      self.build()

  def test_nonfinite_json_or_overflow_cannot_enter_a_bundle(self):
    for constant in ("NaN", "Infinity", "-Infinity", "1e999"):
      with self.subTest(constant=constant):
        original = json.dumps(self.rows[0])
        self.rows_path.write_text(original.replace('"curvature": 0.001', '"curvature": ' + constant) + "\n", encoding="utf-8")
        with self.assertRaises(ValueError):
          self.build()

  def test_policy_requires_all_and_only_reviewed_fields(self):
    variants = []
    for section in ("guardian", "temporal"):
      for key in self.policy[section]:
        value = deepcopy(self.policy)
        del value[section][key]
        variants.append((f"missing:{section}.{key}", value))
      value = deepcopy(self.policy)
      value[section]["unreviewed_threshold"] = 1
      variants.append((f"extra:{section}", value))
    for key in self.policy:
      value = deepcopy(self.policy)
      del value[key]
      variants.append((f"missing:{key}", value))
    variants.append(("extra:top", {**self.policy, "controlAuthorization": False}))
    for label, value in variants:
      with self.subTest(label=label):
        write_json(self.policy_path, value)
        with self.assertRaises(ValueError):
          self.build()

  def test_committed_policy_example_and_schema_match_dataclass_contract(self):
    example_path = ROOT / "config/egpu_integrated_review_policy.example.json"
    schema = read_json(ROOT / "config/egpu_integrated_review_policy.schema.json")
    self.assertEqual(read_json(example_path), self.policy)
    self.assertEqual(set(schema["required"]), set(self.policy))
    self.assertEqual(set(schema["properties"]), set(self.policy))
    self.assertIs(schema["additionalProperties"], False)
    self.assertEqual(schema["properties"]["schemaVersion"], {"type": "integer", "const": 1})
    for section in ("guardian", "temporal"):
      section_schema = schema["properties"][section]
      self.assertEqual(set(section_schema["required"]), set(self.policy[section]))
      self.assertEqual(set(section_schema["properties"]), set(self.policy[section]))
      self.assertIs(section_schema["additionalProperties"], False)
      for field, value in self.policy[section].items():
        field_type = section_schema["properties"][field]["type"]
        if value is None:
          self.assertEqual(field_type, ["number", "null"])
        else:
          expected = "boolean" if type(value) is bool else "integer" if type(value) is int else "number"
          self.assertIn(expected, field_type if isinstance(field_type, list) else [field_type])
    self.policy_path = example_path
    output, manifest = self.build()
    self.assertEqual(self.verify(output, manifest)["status"], "VERIFIED_OFFLINE_CONSISTENCY")

  def test_policy_rejects_nonfinite_lossy_and_wrong_boolean_types(self):
    cases = (
      ("guardian", "max_frame_age", True), ("guardian", "max_frame_age", 1.5),
      ("guardian", "max_frame_age", "1"), ("guardian", "reject_supply_fault", 1),
      ("guardian", "max_execution_ms", False), ("guardian", "curvature_abs", True),
      ("guardian", "accel_abs", float("nan")), ("guardian", "max_hardware_age_s", float("inf")),
      ("temporal", "max_scene_gap_frames", True), ("temporal", "close_lead_m", False),
      ("temporal", "max_action_timestamp_skew_s", float("nan")),
      ("temporal", "closing_fast_mps", float("-inf")),
    )
    for section, key, value in cases:
      with self.subTest(section=section, key=key, value=value):
        policy = deepcopy(self.policy)
        policy[section][key] = value
        self.policy_path.write_text(json.dumps(policy) + "\n", encoding="utf-8")
        with self.assertRaises(ValueError):
          self.build()
    for version in (True, "1", 1.0):
      with self.subTest(version=version):
        write_json(self.policy_path, {**self.policy, "schemaVersion": version})
        with self.assertRaises(ValueError):
          self.build()

  def test_rows_and_declared_contract_must_match_expected_source(self):
    for field, wrong in (("sourceHead", "f" * 40), ("sourceBranch", "unexpected-branch")):
      with self.subTest(field=field):
        rows = deepcopy(self.rows)
        rows[-1][field] = wrong
        self.write_rows(rows)
        with self.assertRaises(ValueError):
          self.build()
    self.write_rows(self.rows)
    for changes in ({"expected_source_head": "f" * 40}, {"expected_source_branch": "unexpected-branch"}):
      with self.subTest(changes=changes), self.assertRaises(ValueError):
        self.build(**changes)

  def test_validly_resigned_interface_forgery_fails_committed_source_binding(self):
    blobs = dict(self.contract.source.interface_blobs)
    blobs[next(iter(blobs))] = "f" * 40
    source = replace(self.contract.source, interface_blobs=tuple(blobs.items()), interface_fingerprint=interface_fingerprint(blobs))
    forged = replace(self.contract, source=source, contract_id="")
    write_json(self.contract_path, contract_payload(forged))
    with self.assertRaises(ValueError):
      self.build()

  def test_source_tree_oid_cannot_impersonate_a_commit(self):
    result = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "HEAD^{tree}"], capture_output=True, text=True, check=True)
    tree_oid = result.stdout.strip()
    self.assertNotEqual(tree_oid, self.head)
    source = replace(self.contract.source, head=tree_oid)
    forged = replace(self.contract, source=source, contract_id="")
    write_json(self.contract_path, contract_payload(forged))
    for row in self.rows:
      row["sourceHead"] = tree_oid
    self.write_rows(self.rows)
    with self.assertRaises(ValueError):
      self.build(expected_source_head=tree_oid)

  def test_validly_resigned_qcom_forgery_fails_committed_lfs_binding(self):
    qcom = self.contract.registry.qcom
    artifact = replace(qcom.artifact, sha256="f" * 64)
    forged = replace(self.contract, registry=replace(self.contract.registry, qcom=replace(qcom, artifact=artifact)), contract_id="")
    write_json(self.contract_path, contract_payload(forged))
    with self.assertRaises(ValueError):
      self.build()

  def test_existing_output_is_never_replaced_or_modified(self):
    output = self.directory / "bundle-1"
    output.mkdir()
    sentinel = output / "existing-evidence.txt"
    sentinel.write_bytes(b"preserve reviewed evidence\n")
    with self.assertRaises((ValueError, FileExistsError)):
      self.build()
    self.assertEqual({path.name for path in output.iterdir()}, {sentinel.name})
    self.assertEqual(sentinel.read_bytes(), b"preserve reviewed evidence\n")

  def test_missing_or_extra_bundle_members_are_rejected(self):
    for name in MEMBERS | {"manifest.json"}:
      with self.subTest(missing=name):
        output, manifest = self.build()
        (output / name).unlink()
        with self.assertRaises((ValueError, OSError)):
          self.verify(output, manifest)
    for name in ("unexpected.json", "nested-directory"):
      with self.subTest(extra=name):
        output, manifest = self.build()
        extra = output / name
        extra.mkdir() if name == "nested-directory" else extra.write_text("{}", encoding="utf-8")
        with self.assertRaises(ValueError):
          self.verify(output, manifest)

  def test_external_receipt_source_and_id_are_mandatory(self):
    output, manifest = self.build()
    cases = (
      {"expected_bundle_id": ""}, {"expected_bundle_id": "a" * 64},
      {"expected_source_head": "f" * 40}, {"expected_source_branch": "different-branch"},
    )
    for changes in cases:
      with self.subTest(changes=changes), self.assertRaises(ValueError):
        self.verify(output, manifest, **changes)

  def test_resigned_authorization_and_analyzer_changes_are_rejected(self):
    for change in ("authorization", "analyzer"):
      with self.subTest(change=change):
        output, manifest = self.build()
        if change == "authorization":
          manifest["policy"]["controlAuthorization"] = True
        else:
          manifest["analyzer"]["sourceHashes"][bundle_tool.ANALYZER_PATHS[0]] = "c" * 64
          manifest["analyzer"]["sourceFingerprint"] = hashlib.sha256(bundle_tool._json_bytes(manifest["analyzer"]["sourceHashes"])).hexdigest()
        manifest["bundleId"] = bundle_tool._bundle_id(manifest)
        (output / "manifest.json").write_bytes(bundle_tool._json_bytes(manifest))
        with self.assertRaises(ValueError):
          self.verify(output, manifest)

  def test_symlink_input_and_member_are_rejected_where_supported(self):
    linked_input = self.directory / "linked-rows.jsonl"
    try:
      linked_input.symlink_to(self.rows_path)
    except (OSError, NotImplementedError) as exc:
      self.skipTest(f"symlink creation unavailable: {exc}")
    original_path = self.rows_path
    self.rows_path = linked_input
    with self.assertRaises(ValueError):
      self.build()
    self.rows_path = original_path
    output, manifest = self.build()
    copied_rows = output / "rows.jsonl"
    copied_rows.unlink()
    copied_rows.symlink_to(original_path)
    with self.assertRaises(ValueError):
      self.verify(output, manifest)


class TestAnalyzerLoadedSources(unittest.TestCase):
  def test_module_loaded_from_another_path_is_rejected(self):
    with patch.object(bundle_tool, "__file__", str(ROOT / "different-checkout.py")):
      with self.assertRaisesRegex(ValueError, "loaded outside"):
        bundle_tool._loaded_source_hashes()

  def test_source_changed_after_import_requires_a_fresh_process(self):
    original_read = bundle_tool._read_file
    changed_path = ROOT / bundle_tool.ANALYZER_PATHS[0]

    def read_changed(path):
      content = original_read(path)
      return content + b"\n# changed after import\n" if path == changed_path else content

    with patch.object(bundle_tool, "_read_file", side_effect=read_changed):
      with self.assertRaisesRegex(ValueError, "changed after import"):
        bundle_tool._capture_analyzer_identity()


class TestAnalyzerCommitBinding(unittest.TestCase):
  @classmethod
  def setUpClass(cls):
    cls.temporary = tempfile.TemporaryDirectory()
    cls.addClassCleanup(cls.temporary.cleanup)
    cls.repo = Path(cls.temporary.name)
    cls.git("init", "--quiet")
    cls.git("config", "core.autocrlf", "false")
    hashes = {}
    for name in bundle_tool.ANALYZER_PATHS:
      path = cls.repo / name
      path.parent.mkdir(parents=True, exist_ok=True)
      # Include CRLF in committed source to check the declared normalization.
      content = f'# Offline analyzer fixture: {name}\r\nVALUE = "fixture"\r\n'.encode("utf-8")
      path.write_bytes(content)
      hashes[name] = hashlib.sha256(content.replace(b"\r\n", b"\n")).hexdigest()
    cls.git("add", "--", *bundle_tool.ANALYZER_PATHS)
    cls.git("-c", "user.name=Offline fixture", "-c", "user.email=fixture@example.invalid", "commit", "--quiet", "-m", "fixture")
    cls.identity = {
      "head": cls.git("rev-parse", "HEAD"),
      "branch": cls.git("rev-parse", "--abbrev-ref", "HEAD"),
      "sourceNormalization": "utf8-lf",
      "sourceHashes": hashes,
      "sourceFingerprint": hashlib.sha256(bundle_tool._json_bytes(hashes)).hexdigest(),
    }

  @classmethod
  def git(cls, *args):
    return subprocess.run(["git", "-C", str(cls.repo), *args], capture_output=True, text=True, check=True).stdout.strip()

  def test_committed_analyzer_identity_with_declared_normalization_is_accepted(self):
    with patch.object(bundle_tool, "ROOT", self.repo):
      bundle_tool._verify_analyzer_commit(deepcopy(self.identity))

  def test_forged_source_hash_rejected_even_with_recomputed_fingerprint(self):
    identity = deepcopy(self.identity)
    identity["sourceHashes"][bundle_tool.ANALYZER_PATHS[0]] = "f" * 64
    identity["sourceFingerprint"] = hashlib.sha256(bundle_tool._json_bytes(identity["sourceHashes"])).hexdigest()
    with patch.object(bundle_tool, "ROOT", self.repo), self.assertRaises(ValueError):
      bundle_tool._verify_analyzer_commit(identity)

  def test_unknown_analyzer_commit_is_rejected(self):
    identity = {**self.identity, "head": "f" * 40}
    with patch.object(bundle_tool, "ROOT", self.repo), self.assertRaises(ValueError):
      bundle_tool._verify_analyzer_commit(identity)

  def test_analyzer_tree_oid_cannot_impersonate_a_commit(self):
    identity = {**self.identity, "head": self.git("rev-parse", "HEAD^{tree}")}
    with patch.object(bundle_tool, "ROOT", self.repo), self.assertRaises(ValueError):
      bundle_tool._verify_analyzer_commit(identity)


if __name__ == "__main__":
  unittest.main()
