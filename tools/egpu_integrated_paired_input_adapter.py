#!/usr/bin/env python3
"""Fail-closed adapter for externally produced SMALL/BIG offline action logs.

The adapter accepts only the exact EGPU-Future extractor row family, verifies
caller-retained hashes and an explicit source/model contract, pairs by frameId
only, and emits the existing integrated Guardian replay row shape. It does not
execute a model, infer a backend, authenticate producer assertions, or authorize
vehicle/runtime use.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import re
import subprocess
from typing import Any

from openpilot.selfdrive.modeld.egpu_integrated_model_contract import ModelPairContract, contract_from_dict

ROOT = Path(__file__).resolve().parents[1]
TOOL_PATH = "tools/egpu_integrated_paired_input_adapter.py"
SMALL_INPUT_FILE = "small-input.jsonl"
BIG_INPUT_FILE = "big-input.jsonl"
PRODUCER_SOURCE_FILE = "producer-extractor.py"
PROVENANCE_DECLARATION_FILE = "paired-source-provenance.json"
CONTRACT_FILE = "model-contract.json"
ROWS_FILE = "rows.jsonl"
RECEIPT_FILE = "input-provenance.json"
ADAPTER_OUTPUT_FILES = (SMALL_INPUT_FILE, BIG_INPUT_FILE, PRODUCER_SOURCE_FILE, PROVENANCE_DECLARATION_FILE, CONTRACT_FILE, ROWS_FILE, RECEIPT_FILE)
STAGE = "PAIRED_OFFLINE_INPUT_ADAPTER"
SCHEMA_VERSION = 1
HEAD_RE = re.compile(r"^[0-9a-f]{40}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
PROVENANCE_KEYS = {"schemaVersion", "source", "producer", "inputs", "modelContractId", "mapping", "pairing", "policy"}
SOURCE_KEYS = {"head", "branch"}
PRODUCER_KEYS = {"repository", "head", "extractorPath", "extractorSha256", "extractorSize", "sameCameraInputId"}
INPUT_KEYS = {"sha256", "size", "sourceLabel", "modelId", "backend"}
MAPPING_KEYS = {"activeInput", "shadowInput"}
PAIRING_KEYS = {"method", "timestampFallback", "sameCameraInputAsserted"}
POLICY = {
  "producerAssertionsVerified": False,
  "modelExecutionVerified": False,
  "controlAuthorization": False,
  "publicRoadAuthorization": False,
}
ROW_REQUIRED = {
  "source", "logMonoTimeNs", "logMonoTimeS", "frameId", "frameIdExtra", "frameAge", "modelExecutionTimeS",
  "big", "observedModelBig", "backendLabel", "backendLabelSource", "desiredCurvature", "desiredAcceleration",
  "shouldStop", "speedMps", "vehicleAccelMps2", "standstill",
}
ROW_OPTIONAL = {"leadPresent", "leadDistanceM", "leadRelSpeedMps", "leadModelProb", "leadRadarMatched"}
RECEIPT_KEYS = {
  "schemaVersion", "stage", "source", "producer", "inputs", "modelContractId", "mapping", "pairing",
  "outputRows", "adapter", "policy", "receiptId",
}


def _sha256(data: bytes) -> str:
  return hashlib.sha256(data).hexdigest()


def _json_bytes(value: Any) -> bytes:
  return (json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False) + "\n").encode("utf-8")


def _no_duplicate_keys(pairs):
  value = {}
  for key, item in pairs:
    if key in value:
      raise ValueError(f"duplicate JSON key: {key}")
    value[key] = item
  return value


def _finite_tree(value: Any) -> None:
  if isinstance(value, float) and not math.isfinite(value):
    raise ValueError("nonfinite JSON number")
  if isinstance(value, dict):
    for item in value.values():
      _finite_tree(item)
  elif isinstance(value, list):
    for item in value:
      _finite_tree(item)


def _load_json_bytes(raw: bytes) -> Any:
  value = json.loads(raw.decode("utf-8"), object_pairs_hook=_no_duplicate_keys)
  _finite_tree(value)
  return value


def _exact_keys(value: Any, expected: set[str], label: str) -> None:
  if not isinstance(value, dict) or set(value) != expected:
    raise ValueError(f"{label}: exact reviewed fields required")


def _regular_file(path: Path) -> None:
  if path.is_symlink() or path.is_junction() or not path.is_file():
    raise ValueError(f"regular local file required: {path}")


def _read_file(path: Path) -> bytes:
  _regular_file(path)
  return path.read_bytes()


def _source_identity(head: Any, branch: Any) -> dict[str, str]:
  if not isinstance(head, str) or HEAD_RE.fullmatch(head) is None:
    raise ValueError("full lowercase source HEAD required")
  if not isinstance(branch, str) or not branch or any(char.isspace() for char in branch):
    raise ValueError("nonempty source branch token required")
  return {"head": head, "branch": branch}


def _number(value: Any, label: str, *, minimum: float | None = None) -> float:
  if isinstance(value, bool) or not isinstance(value, (int, float)):
    raise ValueError(f"{label}: numeric value required")
  result = float(value)
  if not math.isfinite(result) or (minimum is not None and result < minimum):
    raise ValueError(f"{label}: finite value out of range")
  return result


def _integer(value: Any, label: str, *, minimum: int = 0) -> int:
  if type(value) is not int or value < minimum:
    raise ValueError(f"{label}: integer value out of range")
  return value


def _boolean(value: Any, label: str) -> bool:
  if type(value) is not bool:
    raise ValueError(f"{label}: boolean required")
  return value


def _optional_number(value: Any, label: str) -> float | None:
  return None if value is None else _number(value, label)


def _git_bytes(*args: str) -> bytes:
  env = dict(os.environ, GIT_NO_LAZY_FETCH="1", GIT_TERMINAL_PROMPT="0")
  result = subprocess.run(["git", "-C", str(ROOT), *args], capture_output=True, check=False, env=env)
  if result.returncode:
    raise ValueError("required local Git adapter source object or identity is unavailable")
  return result.stdout


def _normalize_source(raw: bytes) -> bytes:
  return raw.decode("utf-8").replace("\r\n", "\n").encode("utf-8")


def _adapter_identity() -> dict[str, str]:
  head = _git_bytes("rev-parse", "HEAD").decode("ascii").strip()
  branch = _git_bytes("rev-parse", "--abbrev-ref", "HEAD").decode("utf-8").strip()
  _source_identity(head, branch)
  current = _normalize_source(_read_file(ROOT / TOOL_PATH))
  committed = _normalize_source(_git_bytes("cat-file", "blob", f"{head}:{TOOL_PATH}"))
  if current != committed:
    raise ValueError("adapter source must be committed and unchanged")
  blob = _git_bytes("rev-parse", f"{head}:{TOOL_PATH}").decode("ascii").strip()
  if HEAD_RE.fullmatch(blob) is None:
    raise ValueError("invalid adapter source blob identity")
  return {"head": head, "branch": branch, "path": TOOL_PATH, "blob": blob, "sha256": _sha256(current)}


def _verify_adapter_identity(value: Any) -> None:
  _exact_keys(value, {"head", "branch", "path", "blob", "sha256"}, "adapter")
  _source_identity(value["head"], value["branch"])
  if value["path"] != TOOL_PATH or not isinstance(value["blob"], str) or HEAD_RE.fullmatch(value["blob"]) is None:
    raise ValueError("invalid recorded adapter path/blob")
  if not isinstance(value["sha256"], str) or SHA256_RE.fullmatch(value["sha256"]) is None:
    raise ValueError("invalid recorded adapter sha256")
  if _git_bytes("cat-file", "-t", value["head"]).strip() != b"commit":
    raise ValueError("recorded adapter head is not a commit")
  raw = _normalize_source(_git_bytes("cat-file", "blob", f"{value['head']}:{TOOL_PATH}"))
  blob = _git_bytes("rev-parse", f"{value['head']}:{TOOL_PATH}").decode("ascii").strip()
  if blob != value["blob"] or _sha256(raw) != value["sha256"]:
    raise ValueError("recorded adapter source does not match its Git commit")


def _validate_file_entry(value: Any, *, label: str, expected_backend: str, expected_model_id: str,
                         actual: bytes) -> str:
  _exact_keys(value, INPUT_KEYS, label)
  if not isinstance(value["sha256"], str) or SHA256_RE.fullmatch(value["sha256"]) is None:
    raise ValueError(f"{label}: invalid sha256")
  if type(value["size"]) is not int or value["size"] <= 0:
    raise ValueError(f"{label}: positive byte size required")
  if not isinstance(value["sourceLabel"], str) or not value["sourceLabel"].strip():
    raise ValueError(f"{label}: sourceLabel required")
  if value["backend"] != expected_backend or value["modelId"] != expected_model_id:
    raise ValueError(f"{label}: backend/model does not match declared contract slot")
  if value["size"] != len(actual) or value["sha256"] != _sha256(actual):
    raise ValueError(f"{label}: file hash/size mismatch")
  return value["sourceLabel"]


def _validate_provenance(value: Any, *, small: bytes, big: bytes, producer_source: bytes,
                         contract: ModelPairContract, expected_source: dict[str, str]) -> dict[str, Any]:
  _exact_keys(value, PROVENANCE_KEYS, "provenance")
  if type(value["schemaVersion"]) is not int or value["schemaVersion"] != SCHEMA_VERSION:
    raise ValueError("unsupported provenance schema")

  _exact_keys(value["source"], SOURCE_KEYS, "provenance source")
  source = _source_identity(value["source"]["head"], value["source"]["branch"])
  if source != expected_source or source != {"head": contract.source.head, "branch": contract.source.branch}:
    raise ValueError("provenance, expected source, and model contract source must match")

  _exact_keys(value["producer"], PRODUCER_KEYS, "producer")
  producer = value["producer"]
  if not isinstance(producer["repository"], str) or not producer["repository"].strip():
    raise ValueError("producer repository required")
  if not isinstance(producer["head"], str) or HEAD_RE.fullmatch(producer["head"]) is None:
    raise ValueError("producer full lowercase Git HEAD required")
  if producer["extractorPath"] != "tools/extract_model_actions_from_log.py":
    raise ValueError("unsupported producer extractor path")
  if not isinstance(producer["extractorSha256"], str) or SHA256_RE.fullmatch(producer["extractorSha256"]) is None:
    raise ValueError("invalid producer extractor sha256")
  if type(producer["extractorSize"]) is not int or producer["extractorSize"] <= 0:
    raise ValueError("positive producer extractor size required")
  if producer["extractorSize"] != len(producer_source) or producer["extractorSha256"] != _sha256(producer_source):
    raise ValueError("producer extractor bytes do not match retained provenance")
  if not isinstance(producer["sameCameraInputId"], str) or not producer["sameCameraInputId"].strip():
    raise ValueError("sameCameraInputId assertion required")

  _exact_keys(value["inputs"], {"small", "big"}, "inputs")
  small_label = _validate_file_entry(value["inputs"]["small"], label="small input", expected_backend="qcom",
                                     expected_model_id=contract.registry.qcom.model_id, actual=small)
  assert contract.registry.egpu is not None
  big_label = _validate_file_entry(value["inputs"]["big"], label="big input", expected_backend="egpu",
                                   expected_model_id=contract.registry.egpu.model_id, actual=big)

  if value["modelContractId"] != contract.resolved_contract_id:
    raise ValueError("provenance modelContractId mismatch")
  _exact_keys(value["mapping"], MAPPING_KEYS, "mapping")
  active = value["mapping"]["activeInput"]
  shadow = value["mapping"]["shadowInput"]
  if active not in {"small", "big"} or shadow not in {"small", "big"} or active == shadow:
    raise ValueError("activeInput and shadowInput must be distinct small/big sides")
  _exact_keys(value["pairing"], PAIRING_KEYS, "pairing")
  if value["pairing"] != {"method": "frameId", "timestampFallback": False, "sameCameraInputAsserted": True}:
    raise ValueError("only explicit same-camera frameId pairing is accepted")
  if value["policy"] != POLICY:
    raise ValueError("paired-input authorization/evidence policy mismatch")
  return {"smallLabel": small_label, "bigLabel": big_label, "activeInput": active, "shadowInput": shadow}


def _lead_value(row: dict[str, Any], key: str) -> Any:
  return row[key] if key in row else None


def _validate_extractor_row(row: Any, *, label: str, source_label: str, side: str) -> dict[str, Any]:
  if not isinstance(row, dict):
    raise ValueError(f"{label}: JSON object required")
  missing = ROW_REQUIRED - row.keys()
  unknown = row.keys() - ROW_REQUIRED - ROW_OPTIONAL
  if missing or unknown:
    raise ValueError(f"{label}: missing fields {sorted(missing)!r}; unknown fields {sorted(str(x) for x in unknown)!r}")
  if row["source"] != source_label:
    raise ValueError(f"{label}: source label mismatch")
  mono_ns = _integer(row["logMonoTimeNs"], f"{label}.logMonoTimeNs")
  mono_s = _number(row["logMonoTimeS"], f"{label}.logMonoTimeS", minimum=0.0)
  if mono_s != mono_ns / 1e9:
    raise ValueError(f"{label}: logMonoTime seconds/nanoseconds mismatch")
  frame_id = _integer(row["frameId"], f"{label}.frameId", minimum=1)
  _integer(row["frameIdExtra"], f"{label}.frameIdExtra")
  frame_age = _integer(row["frameAge"], f"{label}.frameAge")
  exec_s = _number(row["modelExecutionTimeS"], f"{label}.modelExecutionTimeS", minimum=0.0)
  expected_big = side == "big"
  if _boolean(row["big"], f"{label}.big") is not expected_big:
    raise ValueError(f"{label}: explicit BIG/SMALL row flag mismatch")
  _boolean(row["observedModelBig"], f"{label}.observedModelBig")
  expected_source = "forced_big" if expected_big else "forced_small"
  if row["backendLabel"] != side or row["backendLabelSource"] != expected_source:
    raise ValueError(f"{label}: backend must be explicitly forced by producer; inference is not accepted")
  curvature = _number(row["desiredCurvature"], f"{label}.desiredCurvature")
  acceleration = _number(row["desiredAcceleration"], f"{label}.desiredAcceleration")
  should_stop = _boolean(row["shouldStop"], f"{label}.shouldStop")
  speed = _number(row["speedMps"], f"{label}.speedMps", minimum=0.0)
  vehicle_accel = _optional_number(row["vehicleAccelMps2"], f"{label}.vehicleAccelMps2")
  standstill = _boolean(row["standstill"], f"{label}.standstill")

  lead_keys = ROW_OPTIONAL
  present_keys = lead_keys & row.keys()
  if present_keys and present_keys != lead_keys:
    raise ValueError(f"{label}: lead context must be complete or absent")
  lead_present = None
  lead_distance = None
  lead_rel_speed = None
  lead_model_prob = None
  lead_radar_matched = None
  if present_keys:
    lead_present = _boolean(row["leadPresent"], f"{label}.leadPresent")
    lead_distance = _optional_number(row["leadDistanceM"], f"{label}.leadDistanceM")
    lead_rel_speed = _optional_number(row["leadRelSpeedMps"], f"{label}.leadRelSpeedMps")
    lead_model_prob = _optional_number(row["leadModelProb"], f"{label}.leadModelProb")
    if lead_model_prob is not None and not 0.0 <= lead_model_prob <= 1.0:
      raise ValueError(f"{label}.leadModelProb: value out of range")
    lead_radar_matched = None if row["leadRadarMatched"] is None else _boolean(row["leadRadarMatched"], f"{label}.leadRadarMatched")
    if lead_present and (lead_distance is None or lead_rel_speed is None):
      raise ValueError(f"{label}: present lead requires distance and relative speed")
    if not lead_present and any(value is not None for value in (lead_distance, lead_rel_speed, lead_model_prob, lead_radar_matched)):
      raise ValueError(f"{label}: absent lead cannot carry populated lead measurements")

  return {
    "frameId": frame_id,
    "frameIdExtra": row["frameIdExtra"],
    "logMonoTimeNs": mono_ns,
    "timestampMonoS": mono_s,
    "frameAge": frame_age,
    "modelExecutionMs": exec_s * 1000.0,
    "curvature": curvature,
    "acceleration": acceleration,
    "shouldStop": should_stop,
    "speedMps": speed,
    "vehicleAccelMps2": vehicle_accel,
    "standstill": standstill,
    "leadPresent": lead_present,
    "leadDistanceM": lead_distance,
    "leadRelSpeedMps": lead_rel_speed,
    "leadModelProb": lead_model_prob,
    "leadRadarMatched": lead_radar_matched,
  }


RECEIPT_PAIRING_KEYS = {
  "method", "timestampFallback", "sameCameraInputAsserted", "smallSamples", "bigSamples", "pairs", "smallUnmatched", "bigUnmatched",
}
OUTPUT_ROWS_KEYS = {"sha256", "size", "rowCount"}
ADAPTER_KEYS = {"head", "branch", "path", "blob", "sha256"}


def _load_jsonl(raw: bytes, *, label: str, source_label: str, side: str) -> list[dict[str, Any]]:
  rows: list[dict[str, Any]] = []
  previous = 0
  for line_no, line in enumerate(raw.splitlines(), 1):
    if not line.strip():
      continue
    try:
      value = _load_json_bytes(line)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
      raise ValueError(f"{label}:{line_no}: invalid JSON: {exc}") from exc
    row = _validate_extractor_row(value, label=f"{label}:{line_no}", source_label=source_label, side=side)
    if row["frameId"] <= previous:
      raise ValueError(f"{label}:{line_no}: frameId must be strictly increasing and unique")
    previous = row["frameId"]
    rows.append(row)
  if not rows:
    raise ValueError(f"{label}: nonempty extractor JSONL required")
  return rows


def _scene_fingerprint(row: dict[str, Any]) -> tuple[Any, ...]:
  return (
    row["frameIdExtra"], row["logMonoTimeNs"], row["speedMps"], row["vehicleAccelMps2"], row["standstill"],
    row["leadPresent"], row["leadDistanceM"], row["leadRelSpeedMps"], row["leadModelProb"], row["leadRadarMatched"],
  )


def _action(row: dict[str, Any], backend: str) -> dict[str, Any]:
  return {
    "frameId": row["frameId"],
    "frameAge": row["frameAge"],
    "modelExecutionMs": row["modelExecutionMs"],
    "curvature": row["curvature"],
    "acceleration": row["acceleration"],
    "shouldStop": row["shouldStop"],
    "backend": backend,
    "timestampMonoS": row["timestampMonoS"],
  }


def _pair_rows(small_rows: list[dict[str, Any]], big_rows: list[dict[str, Any]], *, source: dict[str, str],
               active_input: str, shadow_input: str) -> tuple[list[dict[str, Any]], dict[str, int]]:
  small_by_frame = {row["frameId"]: row for row in small_rows}
  big_by_frame = {row["frameId"]: row for row in big_rows}
  paired_ids = sorted(small_by_frame.keys() & big_by_frame.keys())
  if not paired_ids:
    raise ValueError("no exact frameId pairs found")
  outputs: list[dict[str, Any]] = []
  for frame_id in paired_ids:
    small = small_by_frame[frame_id]
    big = big_by_frame[frame_id]
    if _scene_fingerprint(small) != _scene_fingerprint(big):
      raise ValueError(f"frame {frame_id}: SMALL/BIG scene evidence differs; same-input pairing not established")
    sides = {"small": (small, "qcom"), "big": (big, "egpu")}
    active_row, active_backend = sides[active_input]
    shadow_row, shadow_backend = sides[shadow_input]
    outputs.append({
      "sourceHead": source["head"],
      "sourceBranch": source["branch"],
      "active": _action(active_row, active_backend),
      "shadow": _action(shadow_row, shadow_backend),
      "scene": {
        "frameId": frame_id,
        "timestampMonoS": small["timestampMonoS"],
        "speedMps": small["speedMps"],
        "standstill": small["standstill"],
        "leadPresent": small["leadPresent"],
        "leadDistanceM": small["leadDistanceM"],
        "leadRelSpeedMps": small["leadRelSpeedMps"],
      },
    })
  return outputs, {
    "smallSamples": len(small_rows),
    "bigSamples": len(big_rows),
    "pairs": len(outputs),
    "smallUnmatched": len(small_rows) - len(outputs),
    "bigUnmatched": len(big_rows) - len(outputs),
  }


def _receipt_id(value: dict[str, Any]) -> str:
  return _sha256(_json_bytes({key: item for key, item in value.items() if key != "receiptId"}))


def _validate_receipt_file_entry(value: Any, *, label: str, expected_backend: str, expected_model_id: str) -> None:
  _exact_keys(value, INPUT_KEYS, label)
  if not isinstance(value["sha256"], str) or SHA256_RE.fullmatch(value["sha256"]) is None:
    raise ValueError(f"{label}: invalid sha256")
  if type(value["size"]) is not int or value["size"] <= 0:
    raise ValueError(f"{label}: invalid size")
  if not isinstance(value["sourceLabel"], str) or not value["sourceLabel"].strip():
    raise ValueError(f"{label}: invalid sourceLabel")
  if value["backend"] != expected_backend or value["modelId"] != expected_model_id:
    raise ValueError(f"{label}: model/backend mismatch")


def verify_input_provenance_receipt(raw: bytes, *, rows: bytes, contract: ModelPairContract,
                                    expected_source: dict[str, str], verify_adapter_source: bool = True) -> dict[str, Any]:
  value = _load_json_bytes(raw)
  _exact_keys(value, RECEIPT_KEYS, "paired-input receipt")
  if type(value["schemaVersion"]) is not int or value["schemaVersion"] != SCHEMA_VERSION or value["stage"] != STAGE:
    raise ValueError("unsupported paired-input receipt")
  _exact_keys(value["source"], SOURCE_KEYS, "receipt source")
  source = _source_identity(value["source"]["head"], value["source"]["branch"])
  if source != expected_source or source != {"head": contract.source.head, "branch": contract.source.branch}:
    raise ValueError("paired-input receipt source mismatch")
  if value["modelContractId"] != contract.resolved_contract_id:
    raise ValueError("paired-input receipt model contract mismatch")

  _exact_keys(value["producer"], PRODUCER_KEYS, "receipt producer")
  producer = value["producer"]
  if not isinstance(producer["repository"], str) or not producer["repository"].strip():
    raise ValueError("receipt producer repository required")
  if not isinstance(producer["head"], str) or HEAD_RE.fullmatch(producer["head"]) is None:
    raise ValueError("receipt producer head invalid")
  if producer["extractorPath"] != "tools/extract_model_actions_from_log.py":
    raise ValueError("receipt extractor path invalid")
  if not isinstance(producer["extractorSha256"], str) or SHA256_RE.fullmatch(producer["extractorSha256"]) is None:
    raise ValueError("receipt extractor sha256 invalid")
  if type(producer["extractorSize"]) is not int or producer["extractorSize"] <= 0:
    raise ValueError("receipt extractor size invalid")
  if not isinstance(producer["sameCameraInputId"], str) or not producer["sameCameraInputId"].strip():
    raise ValueError("receipt sameCameraInputId required")

  _exact_keys(value["inputs"], {"small", "big"}, "receipt inputs")
  _validate_receipt_file_entry(value["inputs"]["small"], label="receipt small input", expected_backend="qcom",
                               expected_model_id=contract.registry.qcom.model_id)
  assert contract.registry.egpu is not None
  _validate_receipt_file_entry(value["inputs"]["big"], label="receipt big input", expected_backend="egpu",
                               expected_model_id=contract.registry.egpu.model_id)

  _exact_keys(value["mapping"], MAPPING_KEYS, "receipt mapping")
  if {value["mapping"]["activeInput"], value["mapping"]["shadowInput"]} != {"small", "big"}:
    raise ValueError("receipt mapping must contain distinct SMALL/BIG sides")
  _exact_keys(value["pairing"], RECEIPT_PAIRING_KEYS, "receipt pairing")
  pairing = value["pairing"]
  if pairing["method"] != "frameId" or pairing["timestampFallback"] is not False or pairing["sameCameraInputAsserted"] is not True:
    raise ValueError("receipt pairing policy mismatch")
  for key in ("smallSamples", "bigSamples", "pairs", "smallUnmatched", "bigUnmatched"):
    _integer(pairing[key], f"receipt pairing.{key}")
  if pairing["pairs"] <= 0 or pairing["smallSamples"] - pairing["pairs"] != pairing["smallUnmatched"] or pairing["bigSamples"] - pairing["pairs"] != pairing["bigUnmatched"]:
    raise ValueError("receipt pairing counts are inconsistent")

  _exact_keys(value["outputRows"], OUTPUT_ROWS_KEYS, "receipt outputRows")
  output = value["outputRows"]
  if not isinstance(output["sha256"], str) or SHA256_RE.fullmatch(output["sha256"]) is None:
    raise ValueError("receipt output rows sha256 invalid")
  if type(output["size"]) is not int or type(output["rowCount"]) is not int or output["size"] < 0 or output["rowCount"] <= 0:
    raise ValueError("receipt output rows size/count invalid")
  actual_count = sum(1 for line in rows.splitlines() if line.strip())
  if output != {"sha256": _sha256(rows), "size": len(rows), "rowCount": actual_count} or actual_count != pairing["pairs"]:
    raise ValueError("receipt does not bind the supplied integrated rows")

  if value["policy"] != POLICY:
    raise ValueError("receipt authorization/evidence policy mismatch")
  if not isinstance(value["receiptId"], str) or SHA256_RE.fullmatch(value["receiptId"]) is None or value["receiptId"] != _receipt_id(value):
    raise ValueError("receiptId mismatch")
  _exact_keys(value["adapter"], ADAPTER_KEYS, "receipt adapter")
  if verify_adapter_source:
    _verify_adapter_identity(value["adapter"])
  return value


def _derive_adapter_evidence(small: bytes, big: bytes, producer_source: bytes, provenance_raw: bytes,
                             contract: ModelPairContract, source: dict[str, str], *,
                             recorded_adapter: dict[str, str] | None = None) -> tuple[bytes, dict[str, Any]]:
  provenance = _load_json_bytes(provenance_raw)
  context = _validate_provenance(provenance, small=small, big=big, producer_source=producer_source,
                                 contract=contract, expected_source=source)
  small_rows = _load_jsonl(small, label="small input", source_label=context["smallLabel"], side="small")
  big_rows = _load_jsonl(big, label="big input", source_label=context["bigLabel"], side="big")
  rows, counts = _pair_rows(small_rows, big_rows, source=source, active_input=context["activeInput"],
                            shadow_input=context["shadowInput"])
  rows_bytes = b"".join(_json_bytes(row) for row in rows)
  current_adapter = _adapter_identity()
  adapter = current_adapter if recorded_adapter is None else dict(recorded_adapter)
  if recorded_adapter is not None:
    _verify_adapter_identity(adapter)
    if current_adapter["path"] != adapter["path"] or current_adapter["sha256"] != adapter["sha256"]:
      raise ValueError("current adapter source differs from the recorded adapter source")
  receipt = {
    "schemaVersion": SCHEMA_VERSION,
    "stage": STAGE,
    "source": source,
    "producer": provenance["producer"],
    "inputs": provenance["inputs"],
    "modelContractId": contract.resolved_contract_id,
    "mapping": provenance["mapping"],
    "pairing": {**provenance["pairing"], **counts},
    "outputRows": {"sha256": _sha256(rows_bytes), "size": len(rows_bytes), "rowCount": len(rows)},
    "adapter": adapter,
    "policy": dict(POLICY),
  }
  receipt["receiptId"] = _receipt_id(receipt)
  final_adapter = _adapter_identity()
  if final_adapter["path"] != current_adapter["path"] or final_adapter["sha256"] != current_adapter["sha256"]:
    raise ValueError("adapter source changed during conversion")
  verify_input_provenance_receipt(_json_bytes(receipt), rows=rows_bytes, contract=contract,
                                  expected_source=source, verify_adapter_source=True)
  return rows_bytes, receipt


def verify_adapter_evidence_bytes(*, small: bytes, big: bytes, producer_source: bytes, provenance: bytes,
                                  rows: bytes, receipt: bytes, contract: ModelPairContract,
                                  expected_source: dict[str, str]) -> dict[str, Any]:
  recorded_receipt = verify_input_provenance_receipt(receipt, rows=rows, contract=contract,
                                                        expected_source=expected_source, verify_adapter_source=True)
  recomputed_rows, recomputed_receipt = _derive_adapter_evidence(
    small, big, producer_source, provenance, contract, expected_source, recorded_adapter=recorded_receipt["adapter"])
  if rows != recomputed_rows:
    raise ValueError("integrated rows do not recompute from the enclosed SMALL/BIG evidence")
  expected_receipt = _json_bytes(recomputed_receipt)
  if receipt != expected_receipt:
    raise ValueError("input provenance receipt does not recompute from the enclosed source evidence")
  verify_input_provenance_receipt(receipt, rows=rows, contract=contract, expected_source=expected_source,
                                  verify_adapter_source=True)
  return recomputed_receipt


def build_adapter_output(small_path: Path, big_path: Path, producer_source_path: Path, provenance_path: Path,
                         contract_path: Path, output: Path, *, expected_source_head: str,
                         expected_source_branch: str) -> dict[str, Any]:
  source = _source_identity(expected_source_head, expected_source_branch)
  if output.exists() or output.is_symlink() or output.is_junction():
    raise ValueError("output already exists; choose a new directory")
  small = _read_file(small_path)
  big = _read_file(big_path)
  producer_source = _read_file(producer_source_path)
  provenance = _read_file(provenance_path)
  contract_raw = _read_file(contract_path)
  contract = contract_from_dict(_load_json_bytes(contract_raw))
  rows_bytes, receipt = _derive_adapter_evidence(small, big, producer_source, provenance, contract, source)
  receipt_bytes = _json_bytes(receipt)
  verify_adapter_evidence_bytes(small=small, big=big, producer_source=producer_source, provenance=provenance,
                                rows=rows_bytes, receipt=receipt_bytes, contract=contract, expected_source=source)
  members = {
    SMALL_INPUT_FILE: small,
    BIG_INPUT_FILE: big,
    PRODUCER_SOURCE_FILE: producer_source,
    PROVENANCE_DECLARATION_FILE: provenance,
    CONTRACT_FILE: contract_raw,
    ROWS_FILE: rows_bytes,
    RECEIPT_FILE: receipt_bytes,
  }
  output.parent.mkdir(parents=True, exist_ok=True)
  output.mkdir(exist_ok=False)
  for name, data in members.items():
    with (output / name).open("xb") as destination:
      destination.write(data)
  return receipt


def verify_adapter_output(output: Path, *, expected_source_head: str,
                          expected_source_branch: str) -> dict[str, Any]:
  source = _source_identity(expected_source_head, expected_source_branch)
  if output.is_symlink() or output.is_junction() or not output.is_dir():
    raise ValueError("regular adapter output directory required")
  if {path.name for path in output.iterdir()} != set(ADAPTER_OUTPUT_FILES):
    raise ValueError("adapter output has missing or extra members")
  members = {name: _read_file(output / name) for name in ADAPTER_OUTPUT_FILES}
  contract = contract_from_dict(_load_json_bytes(members[CONTRACT_FILE]))
  receipt = verify_adapter_evidence_bytes(
    small=members[SMALL_INPUT_FILE], big=members[BIG_INPUT_FILE], producer_source=members[PRODUCER_SOURCE_FILE],
    provenance=members[PROVENANCE_DECLARATION_FILE], rows=members[ROWS_FILE], receipt=members[RECEIPT_FILE],
    contract=contract, expected_source=source,
  )
  return {
    "schemaVersion": 1,
    "stage": "PAIRED_OFFLINE_INPUT_ADAPTER_VERIFICATION",
    "status": "VERIFIED_OFFLINE_CONSISTENCY",
    "receiptId": receipt["receiptId"],
    "source": source,
    "rows": receipt["outputRows"]["rowCount"],
    "pairing": receipt["pairing"],
    **POLICY,
  }


def main() -> int:
  parser = argparse.ArgumentParser(description=__doc__)
  sub = parser.add_subparsers(dest="command", required=True)
  build = sub.add_parser("build", help="convert exact SMALL/BIG extractor files into source-bound integrated rows")
  build.add_argument("--small", type=Path, required=True)
  build.add_argument("--big", type=Path, required=True)
  build.add_argument("--producer-source", type=Path, required=True)
  build.add_argument("--provenance", type=Path, required=True)
  build.add_argument("--contract", type=Path, required=True)
  build.add_argument("--output", type=Path, required=True)
  verify = sub.add_parser("verify", help="recompute the complete self-contained adapter evidence directory")
  verify.add_argument("--output", type=Path, required=True)
  for command in (build, verify):
    command.add_argument("--expected-source-head", required=True)
    command.add_argument("--expected-source-branch", required=True)
  args = parser.parse_args()
  try:
    if args.command == "build":
      result = build_adapter_output(args.small, args.big, args.producer_source, args.provenance, args.contract, args.output,
                                    expected_source_head=args.expected_source_head,
                                    expected_source_branch=args.expected_source_branch)
    else:
      result = verify_adapter_output(args.output,
                                     expected_source_head=args.expected_source_head,
                                     expected_source_branch=args.expected_source_branch)
  except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError, TypeError, OverflowError) as exc:
    print(json.dumps({"status": "HOLD", "reason": str(exc), **POLICY}, ensure_ascii=False))
    return 2
  print(json.dumps(result, indent=2, sort_keys=True, ensure_ascii=False, allow_nan=False))
  return 0


if __name__ == "__main__":
  raise SystemExit(main())
