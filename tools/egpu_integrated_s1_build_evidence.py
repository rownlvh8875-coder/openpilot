#!/usr/bin/env python3
"""Assemble the three S1 recorder summaries into one qualification evidence file."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

LEGS = ("S1_OFF_BEFORE", "S1_ON", "S1_OFF_AFTER")


def assemble(values: list[dict[str, Any]]) -> dict[str, Any]:
  by_leg: dict[str, dict[str, Any]] = {}
  for value in values:
    leg = str(value.get("leg", ""))
    if leg not in LEGS:
      raise ValueError(f"unexpected S1 leg: {leg!r}")
    if leg in by_leg:
      raise ValueError(f"duplicate S1 leg: {leg}")
    by_leg[leg] = value
  missing = [leg for leg in LEGS if leg not in by_leg]
  if missing:
    raise ValueError(f"missing S1 legs: {', '.join(missing)}")
  return {
    "schemaVersion": 1,
    "stage": "S1_OBSERVER_EVIDENCE",
    "legs": {leg: by_leg[leg] for leg in LEGS},
    "controlAuthorization": False,
    "publicRoadAuthorization": False,
  }


def main() -> int:
  ap = argparse.ArgumentParser()
  ap.add_argument("summaries", nargs=3, type=Path)
  ap.add_argument("--output", type=Path, required=True)
  args = ap.parse_args()
  values = [json.loads(path.read_text(encoding="utf-8")) for path in args.summaries]
  try:
    evidence = assemble(values)
  except ValueError as exc:
    print(json.dumps({"status": "HOLD", "reason": str(exc)}, indent=2))
    return 2
  text = json.dumps(evidence, indent=2, ensure_ascii=False) + "\n"
  args.output.parent.mkdir(parents=True, exist_ok=True)
  args.output.write_text(text, encoding="utf-8")
  print(text, end="")
  return 0


if __name__ == "__main__":
  raise SystemExit(main())
