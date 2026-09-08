"""Hardware-free adversarial checks for the provided paired-input adapter."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from openpilot.selfdrive.modeld.egpu_integrated_guardian import GuardianPolicy
from openpilot.selfdrive.modeld.egpu_integrated_guardian_temporal import TemporalHeuristicPolicy
from openpilot.selfdrive.modeld.egpu_integrated_model_contract import build_contract, contract_payload
from tools import egpu_integrated_paired_input_adapter as adapter
from tools import egpu_integrated_review_bundle as bundle_tool

ROOT = Path(__file__).resolve().parents[4]


def write_json(path: Path, value) -> None:
  path.write_text(json.dumps(value, sort_keys=True, indent=2, allow_nan=False) + "\n", encoding="utf-8")


class TestPairedInputAdapter(unittest.TestCase):
  @classmethod
  def setUpClass(cls):
    cls.contract = build_contract(ROOT, expected_branch=None)

  def setUp(self):
    self.temporary = tempfile.TemporaryDirectory()
    self.addCleanup(self.temporary.cleanup)
    self.directory = Path(self.temporary.name)
    self.small_path = self.directory / "small.jsonl"
    self.big_path = self.directory / "big.jsonl"
    self.producer_source_path = self.directory / "extract_model_actions_from_log.py"
    self.provenance_path = self.directory / "provenance.json"
    self.contract_path = self.directory / "contract.json"
    self.policy_path = self.directory / "policy.json"
    self.producer_source = b"# synthetic retained extractor source for adapter tests\n"
    self.producer_source_path.write_bytes(self.producer_source)
    self.small_rows = [self.extractor_row(frame, "small") for frame in (1, 2, 3)]
    self.big_rows = [self.extractor_row(frame, "big") for frame in (1, 3)]
    self.write_inputs()
    write_json(self.contract_path, contract_payload(self.contract))
    self.provenance = self.make_provenance()
    write_json(self.provenance_path, self.provenance)
    write_json(self.policy_path, {
      "schemaVersion": 1,
      "guardian": asdict(GuardianPolicy()),
      "temporal": asdict(TemporalHeuristicPolicy()),
    })
    self.adapter_identity = {
      "head": self.contract.source.head,
      "branch": self.contract.source.branch,
      "path": adapter.TOOL_PATH,
      "blob": "b" * 40,
      "sha256": "c" * 64,
    }
    self.identity_patch = patch.object(adapter, "_adapter_identity", return_value=deepcopy(self.adapter_identity))
    self.identity_patch.start()
    self.addCleanup(self.identity_patch.stop)
    self.verify_identity_patch = patch.object(adapter, "_verify_adapter_identity")
    self.verify_identity_patch.start()
    self.addCleanup(self.verify_identity_patch.stop)

  def extractor_row(self, frame: int, side: str) -> dict:
    mono_ns = 1_000_000_000 + frame * 50_000_000
    big = side == "big"
    return {
      "source": f"{side}-run",
      "logMonoTimeNs": mono_ns,
      "logMonoTimeS": mono_ns / 1e9,
      "frameId": frame,
      "frameIdExtra": 1000 + frame,
      "frameAge": 1 if big else 0,
      "modelExecutionTimeS": 0.031 if big else 0.019,
      "big": big,
      "observedModelBig": big,
      "backendLabel": side,
      "backendLabelSource": "forced_big" if big else "forced_small",
      "desiredCurvature": 0.002 if big else 0.001,
      "desiredAcceleration": -0.3 if big else -0.2,
      "shouldStop": big and frame == 3,
      "speedMps": 10.0,
      "vehicleAccelMps2": 0.0,
      "standstill": False,
      "leadPresent": True,
      "leadDistanceM": 25.0,
      "leadRelSpeedMps": -0.5,
      "leadModelProb": 0.9,
      "leadRadarMatched": True,
    }

  def write_inputs(self) -> None:
    self.small_path.write_text("".join(json.dumps(row, allow_nan=False) + "\n" for row in self.small_rows), encoding="utf-8")
    self.big_path.write_text("".join(json.dumps(row, allow_nan=False) + "\n" for row in self.big_rows), encoding="utf-8")

  def make_provenance(self) -> dict:
    small = self.small_path.read_bytes()
    big = self.big_path.read_bytes()
    assert self.contract.registry.egpu is not None
    return {
      "schemaVersion": 1,
      "source": {"head": self.contract.source.head, "branch": self.contract.source.branch},
      "producer": {
        "repository": "rownlvh8875-coder/EGPU-Future",
        "head": "7" * 40,
        "extractorPath": "tools/extract_model_actions_from_log.py",
        "extractorSha256": hashlib.sha256(self.producer_source).hexdigest(),
        "extractorSize": len(self.producer_source),
        "sameCameraInputId": "synthetic-route/segment-0/camera-input",
      },
      "inputs": {
        "small": {
          "sha256": hashlib.sha256(small).hexdigest(), "size": len(small), "sourceLabel": "small-run",
          "modelId": self.contract.registry.qcom.model_id, "backend": "qcom",
        },
        "big": {
          "sha256": hashlib.sha256(big).hexdigest(), "size": len(big), "sourceLabel": "big-run",
          "modelId": self.contract.registry.egpu.model_id, "backend": "egpu",
        },
      },
      "modelContractId": self.contract.resolved_contract_id,
      "mapping": {"activeInput": "big", "shadowInput": "small"},
      "pairing": {"method": "frameId", "timestampFallback": False, "sameCameraInputAsserted": True},
      "policy": dict(adapter.POLICY),
    }

  def refresh_provenance(self) -> None:
    self.provenance = self.make_provenance()
    write_json(self.provenance_path, self.provenance)

  def build(self, name: str = "adapter-output"):
    output = self.directory / name
    receipt = adapter.build_adapter_output(
      self.small_path, self.big_path, self.producer_source_path, self.provenance_path, self.contract_path, output,
      expected_source_head=self.contract.source.head, expected_source_branch=self.contract.source.branch,
    )
    return output, receipt

  def test_valid_exact_frame_pairing_maps_contract_backends_without_inference(self):
    output, receipt = self.build()
    rows = [json.loads(line) for line in (output / "rows.jsonl").read_text(encoding="utf-8").splitlines()]
    self.assertEqual([row["scene"]["frameId"] for row in rows], [1, 3])
    self.assertEqual(receipt["pairing"]["pairs"], 2)
    self.assertEqual(receipt["pairing"]["smallUnmatched"], 1)
    self.assertEqual(receipt["pairing"]["bigUnmatched"], 0)
    self.assertEqual(rows[0]["active"]["backend"], "egpu")
    self.assertEqual(rows[0]["shadow"]["backend"], "qcom")
    self.assertEqual(rows[0]["active"]["modelExecutionMs"], 31.0)
    self.assertEqual(rows[0]["shadow"]["modelExecutionMs"], 19.0)
    self.assertEqual(set(rows[0]), {"sourceHead", "sourceBranch", "active", "shadow", "scene"})
    verified = adapter.verify_adapter_output(
      output,
      expected_source_head=self.contract.source.head, expected_source_branch=self.contract.source.branch,
    )
    self.assertEqual(verified["status"], "VERIFIED_OFFLINE_CONSISTENCY")
    for key, value in adapter.POLICY.items():
      self.assertIs(verified[key], value)

  def test_rejects_backend_inference_instead_of_explicit_forced_labels(self):
    self.small_rows[0]["backendLabel"] = "auto"
    self.small_rows[0]["backendLabelSource"] = "modelV2.big"
    self.write_inputs(); self.refresh_provenance()
    with self.assertRaisesRegex(ValueError, "explicitly forced"):
      self.build()

  def test_rejects_duplicate_or_nonmonotonic_frame_ids(self):
    self.small_rows.insert(1, deepcopy(self.small_rows[0]))
    self.write_inputs(); self.refresh_provenance()
    with self.assertRaisesRegex(ValueError, "strictly increasing"):
      self.build()

  def test_rejects_same_frame_with_different_scene_or_timestamp(self):
    for key, value in (("speedMps", 11.0), ("logMonoTimeNs", 9_999_999_999)):
      with self.subTest(key=key):
        original = deepcopy(self.big_rows)
        self.big_rows[0][key] = value
        if key == "logMonoTimeNs":
          self.big_rows[0]["logMonoTimeS"] = value / 1e9
        self.write_inputs(); self.refresh_provenance()
        with self.assertRaisesRegex(ValueError, "scene evidence differs"):
          self.build(name=f"output-{key}")
        self.big_rows = original

  def test_rejects_changed_input_after_retained_hash_was_declared(self):
    self.small_path.write_bytes(self.small_path.read_bytes() + b"\n")
    with self.assertRaisesRegex(ValueError, "hash/size mismatch"):
      self.build()

  def test_rejects_model_contract_or_source_mismatch(self):
    wrong = deepcopy(self.provenance)
    wrong["inputs"]["big"]["modelId"] = "wrong-model"
    write_json(self.provenance_path, wrong)
    with self.assertRaisesRegex(ValueError, "contract slot"):
      self.build("wrong-model")
    wrong = deepcopy(self.provenance)
    wrong["source"]["head"] = "f" * 40
    write_json(self.provenance_path, wrong)
    with self.assertRaisesRegex(ValueError, "source"):
      self.build("wrong-source")

  def test_rejects_receipt_or_output_tampering(self):
    output, receipt = self.build()
    rows = output / "rows.jsonl"
    rows.write_bytes(rows.read_bytes() + b" ")
    with self.assertRaisesRegex(ValueError, "recompute|bind"):
      adapter.verify_adapter_output(output,
                                    expected_source_head=self.contract.source.head,
                                    expected_source_branch=self.contract.source.branch)
    output2, _ = self.build("adapter-output-2")
    value = json.loads((output2 / "input-provenance.json").read_text(encoding="utf-8"))
    value["pairing"]["smallUnmatched"] = 99
    write_json(output2 / "input-provenance.json", value)
    with self.assertRaises(ValueError):
      adapter.verify_adapter_output(output2,
                                    expected_source_head=self.contract.source.head,
                                    expected_source_branch=self.contract.source.branch)


class TestPairedInputReviewBundleIntegration(TestPairedInputAdapter):
  def bundle_identity(self) -> dict:
    hashes = {name: "d" * 64 for name in bundle_tool.ANALYZER_PATHS}
    return {
      "head": self.contract.source.head,
      "branch": self.contract.source.branch,
      "sourceNormalization": "utf8-lf",
      "sourceHashes": hashes,
      "sourceFingerprint": hashlib.sha256(bundle_tool._json_bytes(hashes)).hexdigest(),
    }

  def build_review_bundle(self, adapter_output: Path, name: str = "review-bundle"):
    output = self.directory / name
    identity = self.bundle_identity()
    with patch.object(bundle_tool, "_capture_analyzer_identity", side_effect=lambda: deepcopy(identity)), \
         patch.object(bundle_tool, "_verify_analyzer_commit"):
      manifest = bundle_tool.build_review_bundle(
        adapter_output / "rows.jsonl", self.contract_path, self.policy_path, output,
        expected_source_head=self.contract.source.head, expected_source_branch=self.contract.source.branch,
        evidence_kind="provided_paired_offline", paired_evidence_dir=adapter_output,
      )
      verified = bundle_tool.verify_review_bundle(
        output, expected_bundle_id=manifest["bundleId"], expected_source_head=self.contract.source.head,
        expected_source_branch=self.contract.source.branch,
      )
    return output, manifest, verified

  def test_provided_paired_offline_binds_adapter_receipt_inside_review_bundle(self):
    adapter_output, receipt = self.build("paired-adapter-output")
    output, manifest, verified = self.build_review_bundle(adapter_output)
    self.assertEqual(verified["status"], "VERIFIED_OFFLINE_CONSISTENCY")
    self.assertEqual(verified["evidenceKind"], "provided_paired_offline")
    self.assertIn("input-provenance.json", manifest["files"])
    self.assertEqual((output / "input-provenance.json").read_bytes(), (adapter_output / "input-provenance.json").read_bytes())
    self.assertEqual(json.loads((output / "input-provenance.json").read_text())["receiptId"], receipt["receiptId"])
    for key in ("controlAuthorization", "publicRoadAuthorization", "modelExecutionVerified", "producerAssertionsVerified"):
      self.assertIs(verified[key], False)

  def test_review_bundle_rejects_missing_or_misapplied_paired_provenance(self):
    adapter_output, _ = self.build("paired-adapter-output")
    with self.assertRaisesRegex(ValueError, "paired-evidence-dir"):
      bundle_tool.build_review_bundle(
        adapter_output / "rows.jsonl", self.contract_path, self.policy_path, self.directory / "missing-provenance",
        expected_source_head=self.contract.source.head, expected_source_branch=self.contract.source.branch,
        evidence_kind="provided_paired_offline",
      )
    with self.assertRaisesRegex(ValueError, "only valid"):
      bundle_tool.build_review_bundle(
        adapter_output / "rows.jsonl", self.contract_path, self.policy_path, self.directory / "wrong-kind",
        expected_source_head=self.contract.source.head, expected_source_branch=self.contract.source.branch,
        evidence_kind="provided_offline", paired_evidence_dir=adapter_output,
      )

  def test_rehashed_bundle_cannot_hide_tampered_adapter_receipt(self):
    adapter_output, _ = self.build("paired-adapter-output")
    output, manifest, _ = self.build_review_bundle(adapter_output)
    receipt_path = output / "input-provenance.json"
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    receipt["pairing"]["smallUnmatched"] = 77
    write_json(receipt_path, receipt)
    content = receipt_path.read_bytes()
    forged = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    forged["files"]["input-provenance.json"] = {"sha256": hashlib.sha256(content).hexdigest(), "size": len(content)}
    forged["bundleId"] = bundle_tool._bundle_id(forged)
    (output / "manifest.json").write_bytes(bundle_tool._json_bytes(forged))
    identity = self.bundle_identity()
    with patch.object(bundle_tool, "_capture_analyzer_identity", side_effect=lambda: deepcopy(identity)), \
         patch.object(bundle_tool, "_verify_analyzer_commit"), self.assertRaises(ValueError):
      bundle_tool.verify_review_bundle(
        output, expected_bundle_id=forged["bundleId"], expected_source_head=self.contract.source.head,
        expected_source_branch=self.contract.source.branch,
      )

  def test_paired_provenance_schema_matches_fail_closed_contract(self):
    schema = json.loads((ROOT / "config/egpu_integrated_paired_input_provenance.schema.json").read_text(encoding="utf-8"))
    expected = {"schemaVersion", "source", "producer", "inputs", "modelContractId", "mapping", "pairing", "policy"}
    self.assertEqual(set(schema["required"]), expected)
    self.assertEqual(set(schema["properties"]), expected)
    self.assertIs(schema["additionalProperties"], False)
    pairing = schema["properties"]["pairing"]["properties"]
    self.assertEqual(pairing["method"]["const"], "frameId")
    self.assertIs(pairing["timestampFallback"]["const"], False)
    self.assertIs(pairing["sameCameraInputAsserted"]["const"], True)
    policy = schema["properties"]["policy"]["properties"]
    for key in adapter.POLICY:
      self.assertIs(policy[key]["const"], False)
    small = schema["$defs"]["input"]["properties"]["backend"]["enum"]
    self.assertEqual(set(small), {"qcom", "egpu"})

  def test_verification_allows_new_merge_head_only_when_adapter_source_bytes_match(self):
    output, _ = self.build("cross-head-output")
    same_source_new_head = {
      **self.adapter_identity,
      "head": "e" * 40,
      "branch": "carrot-wip-integrated-v6",
    }
    with patch.object(adapter, "_adapter_identity", return_value=same_source_new_head):
      verified = adapter.verify_adapter_output(
        output, expected_source_head=self.contract.source.head, expected_source_branch=self.contract.source.branch)
    self.assertEqual(verified["status"], "VERIFIED_OFFLINE_CONSISTENCY")

    changed_source_new_head = {**same_source_new_head, "sha256": "f" * 64}
    with patch.object(adapter, "_adapter_identity", return_value=changed_source_new_head), \
         self.assertRaisesRegex(ValueError, "current adapter source differs"):
      adapter.verify_adapter_output(
        output, expected_source_head=self.contract.source.head, expected_source_branch=self.contract.source.branch)
