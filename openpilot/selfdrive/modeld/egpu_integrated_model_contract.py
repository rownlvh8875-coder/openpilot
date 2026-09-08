"""Source-bound BIG/SMALL model-pair contracts for integrated Carrot-WIP.

This module adds sunnypilot-like model provenance/compatibility concepts without
adding a model catalog or runtime hot-swap. Carrot's existing big_model.py stays
authoritative for download/cache/active+previous model files. This layer only
binds the reviewed source revision, the built-in QCOM LFS model artifact, and one
hashed eGPU BIG artifact into an auditable contract.

No model loading, compiling, Params writes, hardware access, or control
publication occurs here.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
import subprocess
from typing import Any, Mapping

from openpilot.selfdrive.modeld.big_model import BigModelManifest, TGC_MODEL
from openpilot.selfdrive.modeld.egpu_integrated_model_slots import (
  ModelArtifact,
  ModelSlot,
  ModelSlotRegistry,
  SLOT_QCOM,
  egpu_slot_from_carrot_manifest,
  registry_from_dict,
  registry_to_dict,
)

SCHEMA_VERSION = 1
EXPECTED_BRANCH = "carrot-wip-integrated-v6"
DEFAULT_QCOM_POINTER = "openpilot/selfdrive/modeld/models/driving_supercombo.onnx"
INTERFACE_PATHS = (
  "openpilot/selfdrive/modeld/modeld.py",
  "openpilot/selfdrive/modeld/fill_model_msg.py",
  "openpilot/selfdrive/modeld/parse_model_outputs.py",
)
HEAD_RE = re.compile(r"^[0-9a-f]{40}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
CONTRACT_POLICY = {
  "oneBigOneSmallBaseline": True,
  "runtimeHotSwap": False,
  "crossGenerationMix": False,
  "fallbackSlot": SLOT_QCOM,
  "controlAuthorization": False,
  "publicRoadAuthorization": False,
}
REGISTRY_POLICY = {
  "runtimeHotSwap": False,
  "crossSlotFallback": False,
  "controlAuthorization": False,
}
CONTRACT_KEYS = {"schemaVersion", "source", "generation", "registry", "policy", "contractId"}
SOURCE_KEYS = {"head", "branch", "interfaceBlobs", "interfaceFingerprint"}
REGISTRY_KEYS = {"schemaVersion", "fallbackSlot", "slots", "policy"}


@dataclass(frozen=True)
class SourceBinding:
  head: str
  branch: str
  interface_blobs: tuple[tuple[str, str], ...]
  interface_fingerprint: str

  def validate(self) -> None:
    if not HEAD_RE.fullmatch(self.head):
      raise ValueError("source head must be a 40-character lowercase git SHA")
    if not self.branch or any(c.isspace() for c in self.branch):
      raise ValueError("source branch must be a non-empty token")
    paths = [path for path, _ in self.interface_blobs]
    if len(paths) != len(set(paths)) or set(paths) != set(INTERFACE_PATHS):
      raise ValueError("source binding must contain each reviewed interface path exactly once")
    for path, blob in self.interface_blobs:
      if path not in INTERFACE_PATHS or not HEAD_RE.fullmatch(blob):
        raise ValueError("invalid interface blob binding")
    if self.interface_fingerprint != interface_fingerprint(dict(self.interface_blobs)):
      raise ValueError("interface fingerprint mismatch")


@dataclass(frozen=True)
class ModelPairContract:
  source: SourceBinding
  generation: int
  registry: ModelSlotRegistry
  contract_id: str = ""
  schema_version: int = SCHEMA_VERSION

  def validate(self) -> None:
    if type(self.schema_version) is not int or self.schema_version != SCHEMA_VERSION:
      raise ValueError(f"unsupported model contract schema: {self.schema_version}")
    self.source.validate()
    if not isinstance(self.generation, int) or isinstance(self.generation, bool) or self.generation < 0:
      raise ValueError("generation must be a non-negative integer")
    self.registry.validate()
    if self.registry.egpu is None:
      raise ValueError("baseline contract requires one pinned eGPU BIG slot")
    if self.registry.qcom.artifact is None or not self.registry.qcom.builtin:
      raise ValueError("QCOM slot must be builtin and bind the source LFS artifact")
    if self.registry.qcom.source != "carrot-git-lfs":
      raise ValueError("QCOM slot source must be carrot-git-lfs")
    if self.registry.egpu.source != "carrot-big-model-manifest":
      raise ValueError("eGPU slot source must be carrot-big-model-manifest")
    if self.registry.fallback_slot != SLOT_QCOM:
      raise ValueError("fallback slot must remain QCOM")
    if self.registry.qcom.generation != self.generation or self.registry.egpu.generation != self.generation:
      raise ValueError("contract, BIG, and SMALL generations must match exactly")
    if self.registry.qcom.nominal_hz != self.registry.egpu.nominal_hz:
      raise ValueError("BIG and SMALL nominal frequency must match")
    if self.registry.qcom.runner != "tinygrad" or self.registry.egpu.runner != "tinygrad":
      raise ValueError("baseline BIG/SMALL runner family must be tinygrad")
    expected = contract_id(self)
    if self.contract_id and self.contract_id != expected:
      raise ValueError("contract id mismatch")

  @property
  def resolved_contract_id(self) -> str:
    self.validate()
    return self.contract_id or contract_id(self)


def _git(repo: Path, *args: str) -> str:
  p = subprocess.run(["git", "-C", str(repo), *args], text=True, capture_output=True, check=False)
  if p.returncode != 0 or not p.stdout.strip():
    raise ValueError((p.stderr or p.stdout).strip() or f"git command failed: {' '.join(args)}")
  return p.stdout.strip()


def interface_fingerprint(blobs: Mapping[str, str]) -> str:
  if set(blobs) != set(INTERFACE_PATHS):
    raise ValueError("interface fingerprint requires the complete interface path set")
  canonical = json.dumps({path: blobs[path] for path in sorted(blobs)}, sort_keys=True, separators=(",", ":"))
  return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def git_source_binding(repo: str | Path) -> SourceBinding:
  root = Path(repo).resolve()
  head = _git(root, "rev-parse", "HEAD")
  branch = _git(root, "rev-parse", "--abbrev-ref", "HEAD")
  blobs = tuple((path, _git(root, "rev-parse", f"HEAD:{path}")) for path in INTERFACE_PATHS)
  binding = SourceBinding(head=head, branch=branch, interface_blobs=blobs,
                          interface_fingerprint=interface_fingerprint(dict(blobs)))
  binding.validate()
  return binding


def parse_git_lfs_pointer_text(text: str, *, file_name: str) -> ModelArtifact:
  values: dict[str, str] = {}
  lines = [line.strip() for line in text.splitlines() if line.strip()]
  if not lines or lines[0] != "version https://git-lfs.github.com/spec/v1":
    raise ValueError("QCOM model git object is not a Git LFS v1 pointer")
  for line in lines[1:]:
    if line.startswith("oid sha256:"):
      values["sha256"] = line.removeprefix("oid sha256:")
    elif line.startswith("size "):
      values["size"] = line.removeprefix("size ")
  sha = values.get("sha256", "")
  if not SHA256_RE.fullmatch(sha):
    raise ValueError("invalid/missing LFS SHA256")
  try:
    size = int(values.get("size", ""))
  except ValueError:
    raise ValueError("invalid/missing LFS size") from None
  artifact = ModelArtifact(file_name=Path(file_name).name, size=size, sha256=sha)
  artifact.validate()
  return artifact


def git_lfs_artifact(repo: str | Path, *, path: str = DEFAULT_QCOM_POINTER) -> ModelArtifact:
  """Read the committed LFS pointer, independent of worktree smudge/materialization."""
  root = Path(repo).resolve()
  text = _git(root, "show", f"HEAD:{path}")
  return parse_git_lfs_pointer_text(text, file_name=Path(path).name)


def _qcom_slot(artifact: ModelArtifact, *, generation: int, nominal_hz: float) -> ModelSlot:
  slot = ModelSlot(
    slot=SLOT_QCOM,
    model_id=f"carrot-small-{artifact.sha256[:16]}",
    ref=artifact.sha256[:16],
    backend="qcom",
    runner="tinygrad",
    generation=generation,
    nominal_hz=nominal_hz,
    source="carrot-git-lfs",
    artifact=artifact,
    builtin=True,
    control_eligible=False,
  )
  slot.validate()
  return slot


def _default_big_manifest() -> BigModelManifest:
  return BigModelManifest.from_dict(TGC_MODEL)


def build_contract(
  repo: str | Path,
  *,
  generation: int = 0,
  big_manifest: BigModelManifest | Mapping[str, Any] | None = None,
  qcom_path: str = DEFAULT_QCOM_POINTER,
  nominal_hz: float = 20.0,
  expected_branch: str | None = EXPECTED_BRANCH,
) -> ModelPairContract:
  root = Path(repo).resolve()
  source = git_source_binding(root)
  if expected_branch is not None and source.branch != expected_branch:
    raise ValueError(f"unexpected source branch: {source.branch}")
  qcom_artifact = git_lfs_artifact(root, path=qcom_path)
  manifest = big_manifest or _default_big_manifest()
  if not isinstance(manifest, BigModelManifest):
    manifest = BigModelManifest.from_dict(dict(manifest))
  qcom = _qcom_slot(qcom_artifact, generation=generation, nominal_hz=nominal_hz)
  egpu = egpu_slot_from_carrot_manifest(manifest, ref=manifest.sha256[:16], generation=generation, nominal_hz=nominal_hz)
  registry = ModelSlotRegistry(qcom=qcom, egpu=egpu, fallback_slot=SLOT_QCOM)
  draft = ModelPairContract(source=source, generation=generation, registry=registry)
  draft.validate()
  return ModelPairContract(source=source, generation=generation, registry=registry, contract_id=contract_id(draft))


def contract_payload_unchecked(contract: ModelPairContract) -> dict[str, Any]:
  return {
    "schemaVersion": contract.schema_version,
    "source": {
      "head": contract.source.head,
      "branch": contract.source.branch,
      "interfaceBlobs": [{"path": path, "blob": blob} for path, blob in contract.source.interface_blobs],
      "interfaceFingerprint": contract.source.interface_fingerprint,
    },
    "generation": contract.generation,
    "registry": registry_to_dict(contract.registry),
    "policy": dict(CONTRACT_POLICY),
  }


def contract_id(contract: ModelPairContract) -> str:
  canonical = json.dumps(contract_payload_unchecked(contract), sort_keys=True, separators=(",", ":"), ensure_ascii=True)
  return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def contract_payload(contract: ModelPairContract) -> dict[str, Any]:
  contract.validate()
  payload = contract_payload_unchecked(contract)
  payload["contractId"] = contract.contract_id or contract_id(contract)
  return payload


def _matches_serialized_value(raw: Any, canonical: Any) -> bool:
  """Compare the imported document without accepting lossy parser coercions.

  Registry parsing supports legacy aliases/defaults outside this contract. A
  contract identified by its content hash must retain every reviewed field and JSON type;
  only integer/float representations of number-valued frequency are equivalent.
  """
  if isinstance(canonical, dict):
    return (isinstance(raw, dict) and raw.keys() == canonical.keys()
            and all(_matches_serialized_value(raw[key], value) for key, value in canonical.items()))
  if isinstance(canonical, list):
    return (isinstance(raw, list) and len(raw) == len(canonical)
            and all(_matches_serialized_value(one, two) for one, two in zip(raw, canonical)))
  if isinstance(canonical, float):
    return isinstance(raw, (int, float)) and not isinstance(raw, bool) and raw == canonical
  return type(raw) is type(canonical) and raw == canonical


def contract_from_dict(value: Any) -> ModelPairContract:
  if not isinstance(value, dict) or set(value) != CONTRACT_KEYS:
    raise ValueError("contract must contain exactly the reviewed top-level fields")
  if type(value["schemaVersion"]) is not int or type(value["generation"]) is not int:
    raise ValueError("contract schemaVersion and generation must be integers")
  if not isinstance(value["contractId"], str) or not SHA256_RE.fullmatch(value["contractId"]):
    raise ValueError("imported contractId must be a 64-character lowercase SHA256")
  if value.get("policy") != CONTRACT_POLICY:
    raise ValueError("contract policy mismatch")
  source_raw = value.get("source")
  if not isinstance(source_raw, dict) or set(source_raw) != SOURCE_KEYS:
    raise ValueError("contract source fields mismatch")
  rows = source_raw.get("interfaceBlobs")
  if not isinstance(rows, list):
    raise ValueError("interfaceBlobs must be a list")
  blobs: list[tuple[str, str]] = []
  for row in rows:
    if not isinstance(row, dict) or set(row) != {"path", "blob"}:
      raise ValueError("interfaceBlobs row fields mismatch")
    blobs.append((str(row["path"]), str(row["blob"])))
  registry_raw = value.get("registry")
  if not isinstance(registry_raw, dict) or set(registry_raw) != REGISTRY_KEYS:
    raise ValueError("registry contract fields mismatch")
  if registry_raw.get("policy") != REGISTRY_POLICY:
    raise ValueError("registry policy mismatch")
  slots = registry_raw.get("slots")
  if not isinstance(slots, dict) or set(slots) != {"qcom", "egpu"}:
    raise ValueError("registry must contain exactly qcom and egpu slots")
  source = SourceBinding(
    head=str(source_raw["head"]),
    branch=str(source_raw["branch"]),
    interface_blobs=tuple(blobs),
    interface_fingerprint=str(source_raw["interfaceFingerprint"]),
  )
  try:
    registry = registry_from_dict(registry_raw)
  except (TypeError, ValueError, OverflowError) as e:
    raise ValueError("invalid registry contract value") from e
  contract = ModelPairContract(
    source=source,
    generation=value["generation"],
    registry=registry,
    contract_id=value["contractId"],
    schema_version=value["schemaVersion"],
  )
  contract.validate()
  if not _matches_serialized_value(value, contract_payload(contract)):
    raise ValueError("contract fields and types must match the reviewed serialization exactly")
  return contract


def compare_candidate(current: ModelPairContract, candidate: ModelPairContract) -> dict[str, Any]:
  current.validate(); candidate.validate()
  reasons: list[str] = []
  if candidate.source.head != current.source.head or candidate.source.branch != current.source.branch:
    reasons.append("source_identity_changed")
  if candidate.source.interface_fingerprint != current.source.interface_fingerprint:
    reasons.append("model_interface_changed")
  if candidate.registry.qcom.artifact != current.registry.qcom.artifact:
    reasons.append("qcom_small_changed")
  if candidate.generation <= current.generation:
    reasons.append("generation_not_increased")
  if candidate.registry.egpu is None or current.registry.egpu is None:
    reasons.append("egpu_slot_missing")
  elif candidate.registry.egpu.artifact == current.registry.egpu.artifact:
    reasons.append("egpu_big_unchanged")
  return {
    "schemaVersion": 1,
    "stage": "MODEL_CONTRACT_CANDIDATE_REVIEW",
    "status": "ELIGIBLE_FOR_OFFLINE_REVIEW" if not reasons else "HOLD",
    "reasons": reasons,
    "currentContractId": current.resolved_contract_id,
    "candidateContractId": candidate.resolved_contract_id,
    "runtimeActivationAuthorization": False,
    "controlAuthorization": False,
    "publicRoadAuthorization": False,
  }


def rollback_plan(current: ModelPairContract, previous: ModelPairContract) -> dict[str, Any]:
  current.validate(); previous.validate()
  reasons: list[str] = []
  if previous.source.head != current.source.head or previous.source.branch != current.source.branch:
    reasons.append("source_identity_changed")
  if previous.source.interface_fingerprint != current.source.interface_fingerprint:
    reasons.append("model_interface_changed")
  if previous.registry.qcom.artifact != current.registry.qcom.artifact:
    reasons.append("qcom_small_changed")
  if previous.generation >= current.generation:
    reasons.append("previous_generation_not_older")
  if previous.registry.egpu is None or current.registry.egpu is None:
    reasons.append("egpu_slot_missing")
  elif previous.registry.egpu.artifact == current.registry.egpu.artifact:
    reasons.append("egpu_big_unchanged")
  return {
    "schemaVersion": 1,
    "stage": "MODEL_CONTRACT_ROLLBACK_PLAN_ONLY",
    "status": "PLAN_ELIGIBLE" if not reasons else "HOLD",
    "reasons": reasons,
    "currentContractId": current.resolved_contract_id,
    "previousContractId": previous.resolved_contract_id,
    "rollbackExecutionAuthorization": False,
    "runtimeHotSwapAuthorization": False,
    "controlAuthorization": False,
    "publicRoadAuthorization": False,
  }
