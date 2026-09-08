#!/usr/bin/env python3
"""Build and review source-bound BIG/SMALL model contracts.

This CLI is metadata-only. It never downloads, compiles, selects, activates, or
runs a model and never writes Params or Carrot's big-model state/cache.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from openpilot.selfdrive.modeld.big_model import BigModelManifest
from openpilot.selfdrive.modeld.egpu_integrated_model_contract import (
  EXPECTED_BRANCH,
  build_contract,
  compare_candidate,
  contract_from_dict,
  contract_payload,
  rollback_plan,
)


def read_json(path: Path) -> Any:
  return json.loads(path.read_text(encoding="utf-8"))


def load_contract(path: Path):
  return contract_from_dict(read_json(path))


def emit(value: Any, output: Path | None) -> None:
  text = json.dumps(value, indent=2, ensure_ascii=False, sort_keys=True, allow_nan=False) + "\n"
  print(text, end="")
  if output is not None:
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(text, encoding="utf-8")


def main() -> int:
  ap = argparse.ArgumentParser(description=__doc__)
  sub = ap.add_subparsers(dest="command", required=True)

  build = sub.add_parser("build", help="build one source-bound model-pair contract")
  build.add_argument("--repo", type=Path, default=Path("."))
  build.add_argument("--generation", type=int, required=True)
  build.add_argument("--manifest", type=Path, help="optional local BIG manifest JSON; no network fetch occurs")
  build.add_argument("--expected-branch", default=EXPECTED_BRANCH)
  build.add_argument("--output", type=Path, required=True)

  validate = sub.add_parser("validate", help="validate a contract and print its immutable identity")
  validate.add_argument("--contract", type=Path, required=True)
  validate.add_argument("--output", type=Path)

  compare = sub.add_parser("compare-candidate", help="review a candidate BIG contract; never activate it")
  compare.add_argument("--current", type=Path, required=True)
  compare.add_argument("--candidate", type=Path, required=True)
  compare.add_argument("--output", type=Path)

  rollback = sub.add_parser("rollback-plan", help="review an older BIG contract; never execute rollback")
  rollback.add_argument("--current", type=Path, required=True)
  rollback.add_argument("--previous", type=Path, required=True)
  rollback.add_argument("--output", type=Path)

  args = ap.parse_args()

  try:
    if args.command == "build":
      manifest = None
      if args.manifest is not None:
        raw = read_json(args.manifest)
        manifest = BigModelManifest.from_dict(raw)
      contract = build_contract(
        args.repo,
        generation=args.generation,
        big_manifest=manifest,
        expected_branch=args.expected_branch,
      )
      emit(contract_payload(contract), args.output)
      return 0

    if args.command == "validate":
      contract = load_contract(args.contract)
      emit({
        "schemaVersion": 1,
        "stage": "MODEL_CONTRACT_VALIDATION",
        "status": "PASS",
        "contractId": contract.resolved_contract_id,
        "sourceHead": contract.source.head,
        "sourceBranch": contract.source.branch,
        "generation": contract.generation,
        "runtimeActivationAuthorization": False,
        "controlAuthorization": False,
        "publicRoadAuthorization": False,
      }, args.output)
      return 0

    if args.command == "compare-candidate":
      result = compare_candidate(load_contract(args.current), load_contract(args.candidate))
      emit(result, args.output)
      return 0 if result["status"] == "ELIGIBLE_FOR_OFFLINE_REVIEW" else 2

    if args.command == "rollback-plan":
      result = rollback_plan(load_contract(args.current), load_contract(args.previous))
      emit(result, args.output)
      return 0 if result["status"] == "PLAN_ELIGIBLE" else 2
  except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
    emit({
      "schemaVersion": 1,
      "stage": "MODEL_CONTRACT_TOOL",
      "status": "HOLD",
      "reason": str(exc),
      "runtimeActivationAuthorization": False,
      "controlAuthorization": False,
      "publicRoadAuthorization": False,
    }, getattr(args, "output", None))
    return 2

  raise AssertionError("unreachable")


if __name__ == "__main__":
  raise SystemExit(main())
