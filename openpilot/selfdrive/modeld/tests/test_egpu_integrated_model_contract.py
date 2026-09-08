from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import unittest

from openpilot.selfdrive.modeld.big_model import BigModelManifest, TGC_MODEL
from openpilot.selfdrive.modeld.egpu_integrated_model_contract import (
  CONTRACT_POLICY,
  EXPECTED_BRANCH,
  INTERFACE_PATHS,
  ModelPairContract,
  SourceBinding,
  build_contract,
  compare_candidate,
  contract_from_dict,
  contract_payload,
  git_lfs_artifact,
  git_source_binding,
  interface_fingerprint,
  rollback_plan,
)
from openpilot.selfdrive.modeld.egpu_integrated_model_slots import ModelArtifact, ModelSlotRegistry


ROOT = Path(__file__).resolve().parents[4]
QCOM_SHA = "f73a9e535523d5e9acb9e642c64e33d631825dc8ba74123757d107cedd047bb5"
QCOM_SIZE = 60_792_584
BIG_SHA = "1791d5940b2c048d0639813426dd2cf1d6f2a6727ed51e17c8bcea8bbe754123"


def candidate_manifest(seed: str = "2") -> BigModelManifest:
  return BigModelManifest(
    model_id=f"candidate-big-{seed}",
    filename=f"candidate_big_{seed}.onnx",
    size=123_456_789,
    sha256=seed * 64,
    url=f"https://example.com/candidate-{seed}.onnx",
  )


def rebuild(contract: ModelPairContract, *, source=None, generation=None, registry=None) -> ModelPairContract:
  return ModelPairContract(
    source=source or contract.source,
    generation=contract.generation if generation is None else generation,
    registry=registry or contract.registry,
  )


class TestLfsAndSourceBinding(unittest.TestCase):
  def test_qcom_git_lfs_artifact_is_exact(self):
    artifact = git_lfs_artifact(ROOT)
    self.assertEqual(artifact.file_name, "driving_supercombo.onnx")
    self.assertEqual(artifact.sha256, QCOM_SHA)
    self.assertEqual(artifact.size, QCOM_SIZE)

  def test_source_binding_contains_exact_interface_set(self):
    binding = git_source_binding(ROOT)
    self.assertEqual({p for p, _ in binding.interface_blobs}, set(INTERFACE_PATHS))
    self.assertEqual(binding.interface_fingerprint, interface_fingerprint(dict(binding.interface_blobs)))
    self.assertEqual(len(binding.interface_fingerprint), 64)


class TestModelPairContract(unittest.TestCase):
  def setUp(self):
    # Tests run on a development/PR branch, so branch enforcement is tested
    # separately from contract construction.
    self.current = build_contract(ROOT, generation=0, expected_branch=None)

  def test_default_pair_binds_exact_qcom_and_carrot_big(self):
    payload = contract_payload(self.current)
    self.assertEqual(self.current.registry.qcom.artifact.sha256, QCOM_SHA)
    self.assertEqual(self.current.registry.qcom.artifact.size, QCOM_SIZE)
    self.assertEqual(self.current.registry.egpu.artifact.sha256, BIG_SHA)
    self.assertEqual(self.current.registry.egpu.artifact.size, int(TGC_MODEL["size"]))
    self.assertEqual(payload["policy"], CONTRACT_POLICY)
    self.assertFalse(payload["policy"]["runtimeHotSwap"])
    self.assertFalse(payload["policy"]["crossGenerationMix"])
    self.assertFalse(payload["policy"]["controlAuthorization"])
    self.assertFalse(payload["policy"]["publicRoadAuthorization"])
    self.assertEqual(payload["policy"]["fallbackSlot"], "qcom")

  def test_contract_id_is_deterministic_and_roundtrips(self):
    one = contract_payload(self.current)
    two = contract_payload(build_contract(ROOT, generation=0, expected_branch=None))
    self.assertEqual(one["contractId"], two["contractId"])
    restored = contract_from_dict(one)
    self.assertEqual(restored.resolved_contract_id, self.current.resolved_contract_id)
    self.assertEqual(contract_payload(restored), one)

  def test_corrupted_contract_id_is_rejected(self):
    payload = contract_payload(self.current)
    payload["contractId"] = "0" * 64
    with self.assertRaises(ValueError):
      contract_from_dict(payload)

  def test_policy_tamper_is_rejected_even_if_contract_id_is_unchanged(self):
    payload = contract_payload(self.current)
    payload["policy"]["runtimeHotSwap"] = True
    with self.assertRaises(ValueError):
      contract_from_dict(payload)

  def test_registry_policy_tamper_is_rejected(self):
    payload = contract_payload(self.current)
    payload["registry"]["policy"]["crossSlotFallback"] = True
    with self.assertRaises(ValueError):
      contract_from_dict(payload)

  def test_unknown_top_level_field_is_rejected(self):
    payload = contract_payload(self.current)
    payload["activation"] = "enabled"
    with self.assertRaises(ValueError):
      contract_from_dict(payload)

  def test_slot_generation_must_match_contract_generation(self):
    qcom = replace(self.current.registry.qcom, generation=1)
    registry = ModelSlotRegistry(qcom=qcom, egpu=self.current.registry.egpu, fallback_slot="qcom")
    broken = rebuild(self.current, registry=registry)
    with self.assertRaises(ValueError):
      broken.validate()

  def test_canonical_branch_enforcement_is_explicit(self):
    binding = git_source_binding(ROOT)
    if binding.branch != EXPECTED_BRANCH:
      with self.assertRaises(ValueError):
        build_contract(ROOT)
    else:
      self.assertEqual(build_contract(ROOT).source.branch, EXPECTED_BRANCH)

  def test_candidate_new_big_same_source_qcom_and_next_generation_is_review_eligible(self):
    candidate = build_contract(ROOT, generation=1, big_manifest=candidate_manifest("2"), expected_branch=None)
    result = compare_candidate(self.current, candidate)
    self.assertEqual(result["status"], "ELIGIBLE_FOR_OFFLINE_REVIEW")
    self.assertEqual(result["reasons"], [])
    self.assertFalse(result["runtimeActivationAuthorization"])
    self.assertFalse(result["controlAuthorization"])

  def test_candidate_same_big_is_hold(self):
    candidate = build_contract(ROOT, generation=1, expected_branch=None)
    result = compare_candidate(self.current, candidate)
    self.assertEqual(result["status"], "HOLD")
    self.assertIn("egpu_big_unchanged", result["reasons"])

  def test_candidate_generation_must_increase(self):
    candidate = build_contract(ROOT, generation=0, big_manifest=candidate_manifest("3"), expected_branch=None)
    result = compare_candidate(self.current, candidate)
    self.assertEqual(result["status"], "HOLD")
    self.assertIn("generation_not_increased", result["reasons"])

  def test_candidate_source_change_is_hold(self):
    source = replace(self.current.source, head="b" * 40)
    candidate_big = build_contract(ROOT, generation=1, big_manifest=candidate_manifest("4"), expected_branch=None)
    candidate = rebuild(candidate_big, source=source)
    result = compare_candidate(self.current, candidate)
    self.assertEqual(result["status"], "HOLD")
    self.assertIn("source_identity_changed", result["reasons"])

  def test_candidate_interface_change_is_hold(self):
    rows = dict(self.current.source.interface_blobs)
    rows[INTERFACE_PATHS[0]] = "c" * 40
    source = SourceBinding(
      head=self.current.source.head,
      branch=self.current.source.branch,
      interface_blobs=tuple((p, rows[p]) for p in INTERFACE_PATHS),
      interface_fingerprint=interface_fingerprint(rows),
    )
    candidate_big = build_contract(ROOT, generation=1, big_manifest=candidate_manifest("5"), expected_branch=None)
    candidate = rebuild(candidate_big, source=source)
    result = compare_candidate(self.current, candidate)
    self.assertEqual(result["status"], "HOLD")
    self.assertIn("model_interface_changed", result["reasons"])

  def test_candidate_qcom_change_is_hold(self):
    qcom = replace(
      self.current.registry.qcom,
      generation=1,
      artifact=ModelArtifact("driving_supercombo.onnx", QCOM_SIZE, "d" * 64),
      model_id="carrot-small-dddddddddddddddd",
      ref="dddddddddddddddd",
    )
    candidate_base = build_contract(ROOT, generation=1, big_manifest=candidate_manifest("6"), expected_branch=None)
    registry = ModelSlotRegistry(qcom=qcom, egpu=candidate_base.registry.egpu, fallback_slot="qcom")
    candidate = rebuild(candidate_base, registry=registry)
    result = compare_candidate(self.current, candidate)
    self.assertEqual(result["status"], "HOLD")
    self.assertIn("qcom_small_changed", result["reasons"])

  def test_rollback_plan_is_metadata_only(self):
    newer = build_contract(ROOT, generation=1, big_manifest=candidate_manifest("7"), expected_branch=None)
    result = rollback_plan(newer, self.current)
    self.assertEqual(result["status"], "PLAN_ELIGIBLE")
    self.assertEqual(result["reasons"], [])
    self.assertFalse(result["rollbackExecutionAuthorization"])
    self.assertFalse(result["runtimeHotSwapAuthorization"])
    self.assertFalse(result["controlAuthorization"])

  def test_rollback_across_source_is_hold(self):
    newer = build_contract(ROOT, generation=1, big_manifest=candidate_manifest("8"), expected_branch=None)
    previous = rebuild(self.current, source=replace(self.current.source, head="e" * 40))
    result = rollback_plan(newer, previous)
    self.assertEqual(result["status"], "HOLD")
    self.assertIn("source_identity_changed", result["reasons"])

  def test_rollback_requires_older_generation_and_different_big(self):
    same_generation = build_contract(ROOT, generation=1, big_manifest=candidate_manifest("9"), expected_branch=None)
    current = build_contract(ROOT, generation=1, big_manifest=candidate_manifest("a"), expected_branch=None)
    result = rollback_plan(current, same_generation)
    self.assertEqual(result["status"], "HOLD")
    self.assertIn("previous_generation_not_older", result["reasons"])

    older_same_big = build_contract(ROOT, generation=0, big_manifest=candidate_manifest("a"), expected_branch=None)
    result = rollback_plan(current, older_same_big)
    self.assertEqual(result["status"], "HOLD")
    self.assertIn("egpu_big_unchanged", result["reasons"])


if __name__ == "__main__":
  unittest.main()
