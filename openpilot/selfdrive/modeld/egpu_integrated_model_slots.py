"""Validated model-slot metadata for the Carrot-WIP integrated branch.

This Stage-3 module deliberately does not load, unload, switch, compile, or run
models. It only validates and persists metadata for two explicit slots:

  qcom  - the internal fallback/default model
  egpu  - the external AMD/USB big driving model

The existing Carrot big-model download/compile path remains authoritative until
a later reviewed integration stage wires these metadata slots into startup.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
from typing import Any

SCHEMA_VERSION = 1
SLOT_QCOM = "qcom"
SLOT_EGPU = "egpu"
VALID_SLOTS = {SLOT_QCOM, SLOT_EGPU}
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
NAME_RE = re.compile(r"^[A-Za-z0-9._:+-]+$")
DEFAULT_REGISTRY_PATH = Path("/data/egpu_integrated/model_slots.json")


@dataclass(frozen=True)
class ModelArtifact:
  file_name: str
  size: int
  sha256: str

  def validate(self) -> None:
    if not self.file_name or Path(self.file_name).name != self.file_name or not NAME_RE.fullmatch(self.file_name):
      raise ValueError("artifact file_name must be a safe basename")
    if not isinstance(self.size, int) or isinstance(self.size, bool) or self.size <= 0:
      raise ValueError("artifact size must be a positive integer")
    if not isinstance(self.sha256, str) or not SHA256_RE.fullmatch(self.sha256.lower()):
      raise ValueError("artifact sha256 must be 64 lowercase hex characters")


@dataclass(frozen=True)
class ModelSlot:
  slot: str
  model_id: str
  ref: str
  backend: str
  runner: str
  generation: int
  nominal_hz: float
  source: str
  artifact: ModelArtifact | None = None
  builtin: bool = False
  control_eligible: bool = False

  def validate(self) -> None:
    if self.slot not in VALID_SLOTS:
      raise ValueError(f"unsupported slot: {self.slot}")
    for name, value in (("model_id", self.model_id), ("ref", self.ref), ("backend", self.backend), ("runner", self.runner), ("source", self.source)):
      if not isinstance(value, str) or not value or not NAME_RE.fullmatch(value):
        raise ValueError(f"{name} must be a non-empty safe token")
    if not isinstance(self.generation, int) or isinstance(self.generation, bool) or self.generation < 0:
      raise ValueError("generation must be a non-negative integer")
    try:
      hz = float(self.nominal_hz)
    except (TypeError, ValueError):
      raise ValueError("nominal_hz must be numeric") from None
    if not 0 < hz <= 100:
      raise ValueError("nominal_hz must be >0 and <=100")
    if self.slot == SLOT_QCOM and self.backend != "qcom":
      raise ValueError("qcom slot must use qcom backend")
    if self.slot == SLOT_EGPU and self.backend not in {"amd-usb", "egpu"}:
      raise ValueError("egpu slot must use amd-usb/egpu backend")
    if self.builtin:
      if self.slot != SLOT_QCOM:
        raise ValueError("only qcom slot may be builtin in Stage 3")
      if self.artifact is not None:
        self.artifact.validate()
    else:
      if self.artifact is None:
        raise ValueError("non-builtin model slot requires a hashed artifact")
      self.artifact.validate()
    if self.control_eligible:
      raise ValueError("Stage-3 registry cannot mark a slot control_eligible")


@dataclass(frozen=True)
class ModelSlotRegistry:
  qcom: ModelSlot
  egpu: ModelSlot | None
  fallback_slot: str = SLOT_QCOM
  schema_version: int = SCHEMA_VERSION

  def validate(self) -> None:
    if self.schema_version != SCHEMA_VERSION:
      raise ValueError(f"unsupported registry schema: {self.schema_version}")
    self.qcom.validate()
    if self.qcom.slot != SLOT_QCOM:
      raise ValueError("qcom registry entry must use qcom slot")
    if self.egpu is not None:
      self.egpu.validate()
      if self.egpu.slot != SLOT_EGPU:
        raise ValueError("egpu registry entry must use egpu slot")
    if self.fallback_slot != SLOT_QCOM:
      raise ValueError("Stage-3 fallback_slot is fixed to qcom; cross-slot fallback is prohibited")

  def startup_preference(self, *, egpu_ready: bool) -> str:
    self.validate()
    return SLOT_EGPU if egpu_ready and self.egpu is not None else SLOT_QCOM


def builtin_qcom_slot(*, model_id: str = "carrot-builtin-small", ref: str = "carrot-source-default",
                      generation: int = 0, nominal_hz: float = 20.0) -> ModelSlot:
  slot = ModelSlot(slot=SLOT_QCOM, model_id=model_id, ref=ref, backend="qcom", runner="tinygrad",
                   generation=generation, nominal_hz=nominal_hz, source="carrot-builtin",
                   artifact=None, builtin=True, control_eligible=False)
  slot.validate()
  return slot


def egpu_slot_from_carrot_manifest(manifest: Any, *, ref: str | None = None, generation: int = 0,
                                   nominal_hz: float = 20.0) -> ModelSlot:
  get = (lambda key: manifest.get(key)) if isinstance(manifest, dict) else (lambda key: getattr(manifest, key))
  model_id = str(get("model_id"))
  artifact = ModelArtifact(str(get("filename")), int(get("size")), str(get("sha256")).lower())
  slot = ModelSlot(slot=SLOT_EGPU, model_id=model_id, ref=ref or model_id, backend="amd-usb", runner="tinygrad",
                   generation=generation, nominal_hz=nominal_hz, source="carrot-big-model-manifest",
                   artifact=artifact, builtin=False, control_eligible=False)
  slot.validate()
  return slot


def registry_to_dict(registry: ModelSlotRegistry) -> dict[str, Any]:
  registry.validate()
  return {
    "schemaVersion": registry.schema_version,
    "fallbackSlot": registry.fallback_slot,
    "slots": {SLOT_QCOM: asdict(registry.qcom), SLOT_EGPU: asdict(registry.egpu) if registry.egpu is not None else None},
    "policy": {"runtimeHotSwap": False, "crossSlotFallback": False, "controlAuthorization": False},
  }


def _artifact_from_dict(value: Any) -> ModelArtifact | None:
  if value is None:
    return None
  if not isinstance(value, dict):
    raise ValueError("artifact must be an object or null")
  return ModelArtifact(file_name=str(value.get("file_name", value.get("fileName", ""))),
                       size=int(value.get("size", 0)), sha256=str(value.get("sha256", "")).lower())


def _slot_from_dict(value: Any) -> ModelSlot:
  if not isinstance(value, dict):
    raise ValueError("slot must be an object")
  slot = ModelSlot(slot=str(value.get("slot", "")), model_id=str(value.get("model_id", value.get("modelId", ""))),
                   ref=str(value.get("ref", "")), backend=str(value.get("backend", "")), runner=str(value.get("runner", "")),
                   generation=int(value.get("generation", 0)), nominal_hz=float(value.get("nominal_hz", value.get("nominalHz", 0.0))),
                   source=str(value.get("source", "")), artifact=_artifact_from_dict(value.get("artifact")),
                   builtin=bool(value.get("builtin", False)), control_eligible=bool(value.get("control_eligible", value.get("controlEligible", False))))
  slot.validate()
  return slot


def registry_from_dict(value: Any) -> ModelSlotRegistry:
  if not isinstance(value, dict):
    raise ValueError("registry must be an object")
  slots = value.get("slots")
  if not isinstance(slots, dict):
    raise ValueError("registry slots must be an object")
  registry = ModelSlotRegistry(qcom=_slot_from_dict(slots.get(SLOT_QCOM)),
                               egpu=_slot_from_dict(slots.get(SLOT_EGPU)) if slots.get(SLOT_EGPU) is not None else None,
                               fallback_slot=str(value.get("fallbackSlot", SLOT_QCOM)), schema_version=int(value.get("schemaVersion", 0)))
  registry.validate()
  return registry


def write_registry(path: str | Path, registry: ModelSlotRegistry) -> None:
  target = Path(path)
  payload = registry_to_dict(registry)
  target.parent.mkdir(parents=True, exist_ok=True)
  fd, tmp_name = tempfile.mkstemp(prefix=".model-slots-", suffix=".json", dir=target.parent)
  try:
    with os.fdopen(fd, "w", encoding="utf-8") as f:
      json.dump(payload, f, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False)
      f.write("\n")
      f.flush()
      os.fsync(f.fileno())
    os.replace(tmp_name, target)
  finally:
    try:
      os.unlink(tmp_name)
    except FileNotFoundError:
      pass


def load_registry(path: str | Path = DEFAULT_REGISTRY_PATH) -> ModelSlotRegistry:
  with Path(path).open(encoding="utf-8") as f:
    return registry_from_dict(json.load(f))


def verify_artifact(path: str | Path, artifact: ModelArtifact, *, chunk_size: int = 4 * 1024 * 1024) -> bool:
  artifact.validate()
  file_path = Path(path)
  try:
    if file_path.stat().st_size != artifact.size:
      return False
  except OSError:
    return False
  digest = hashlib.sha256()
  try:
    with file_path.open("rb") as f:
      while chunk := f.read(max(4096, int(chunk_size))):
        digest.update(chunk)
  except OSError:
    return False
  return digest.hexdigest() == artifact.sha256
