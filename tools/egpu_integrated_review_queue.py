#!/usr/bin/env python3
"""Build a de-duplicated human review queue from offline Guardian replay events.

This tool ranks evidence for review only. It never authorizes model selection,
vehicle controls, or public-road use.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
import hashlib
import json
from pathlib import Path
from typing import Any

BUCKET_ORDER = {"ROOT_CAUSE": 0, "PRIORITY_REVIEW": 1, "REVIEW": 2, "OBSERVE": 3}


def _event_fingerprint(event: dict[str, Any]) -> str:
  """Bind grouping to both categorical assessments, including fault-only issues."""
  guardian = event.get("guardian") or {}
  fault = event.get("fault") or {}
  payload = {
    "guardianFingerprint": guardian.get("reviewFingerprint"),
    "guardian": {
      name: sorted(set(guardian.get(name) or []))
      for name in ("hardIssues", "reviewIssues", "sceneTags", "temporalTags")
    },
    "activeBackend": guardian.get("activeBackend"),
    "shadowBackend": guardian.get("shadowBackend"),
    "fault": {
      name: sorted(set(fault.get(name) or []))
      for name in ("hardIssues", "reviewIssues", "temporalTags")
    },
    "faultState": fault.get("state"),
    "faultEvidenceCoherent": fault.get("evidenceCoherent"),
    "sameFrameOutputPreserved": fault.get("sameFrameOutputPreserved"),
    "cutoutActive": (guardian.get("carrotCutoutContext") or {}).get("active"),
  }
  canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False)
  return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


def build_review_queue(events: list[dict[str, Any]], *, include_observe: bool = False) -> dict[str, Any]:
  groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
  source_head = source_branch = None

  for index, event in enumerate(events):
    if event.get("stage") != "OFFLINE_GUARDIAN_REPLAY":
      raise ValueError(f"event {index}: unexpected stage")
    head = event.get("sourceHead")
    branch = event.get("sourceBranch")
    if not isinstance(head, str) or not head.strip() or not isinstance(branch, str) or not branch.strip():
      raise ValueError(f"event {index}: source identity missing")
    if source_head is None:
      source_head, source_branch = head, branch
    elif head != source_head or branch != source_branch:
      raise ValueError(f"event {index}: mixed source identity")
    if type(event.get("frameId")) is not int or event["frameId"] < 0:
      raise ValueError(f"event {index}: nonnegative integer frameId required")
    if event.get("processId") is not None and (not isinstance(event["processId"], str) or not event["processId"].strip()):
      raise ValueError(f"event {index}: invalid process identity")

    if not isinstance(event.get("guardian"), dict) or (event.get("fault") is not None and not isinstance(event["fault"], dict)):
      raise ValueError(f"event {index}: assessment object required")
    bucket = event.get("combinedReviewBucket")
    if bucket not in BUCKET_ORDER:
      raise ValueError(f"event {index}: unknown review bucket {bucket!r}")
    if ((event["guardian"].get("hardIssues") or (event.get("fault") or {}).get("hardIssues")) and bucket != "ROOT_CAUSE"):
      raise ValueError(f"event {index}: hard issues cannot be downgraded by the combined bucket")
    if bucket == "OBSERVE" and not include_observe:
      continue
    fingerprint = _event_fingerprint(event)
    groups[(bucket, fingerprint)].append(event)

  queue: list[dict[str, Any]] = []
  for (bucket, fingerprint), items in groups.items():
    frames = sorted(int(x.get("frameId", 0)) for x in items)
    hard = sorted({str(v) for x in items for v in ((x.get("guardian") or {}).get("hardIssues") or [])})
    review = sorted({str(v) for x in items for v in ((x.get("guardian") or {}).get("reviewIssues") or [])})
    scene = sorted({str(v) for x in items for v in ((x.get("guardian") or {}).get("sceneTags") or [])})
    temporal = sorted({str(v) for x in items for v in ((x.get("guardian") or {}).get("temporalTags") or [])})
    fault_hard = sorted({str(v) for x in items if isinstance(x.get("fault"), dict) for v in (x["fault"].get("hardIssues") or [])})
    fault_review = sorted({str(v) for x in items if isinstance(x.get("fault"), dict) for v in (x["fault"].get("reviewIssues") or [])})
    fault_states = sorted({str(x["fault"].get("state")) for x in items if isinstance(x.get("fault"), dict) and x["fault"].get("state")})
    fault_temporal = sorted({str(v) for x in items if isinstance(x.get("fault"), dict) for v in (x["fault"].get("temporalTags") or [])})
    representative = min(items, key=lambda item: (item["frameId"], item.get("processId") or ""))
    cutout_contexts = [
      (x.get("guardian") or {}).get("carrotCutoutContext")
      for x in items
      if isinstance((x.get("guardian") or {}).get("carrotCutoutContext"), dict)
    ]
    cutout_active_count = sum(1 for value in cutout_contexts if value.get("active") is True)
    queue.append({
      "bucket": bucket,
      "fingerprint": fingerprint,
      "count": len(items),
      "firstFrameId": frames[0],
      "lastFrameId": frames[-1],
      "representativeFrameId": frames[0],
      "representativeProcessId": representative.get("processId"),
      "hardIssues": hard,
      "reviewIssues": review,
      "sceneTags": scene,
      "temporalTags": temporal,
      "faultHardIssues": fault_hard,
      "faultReviewIssues": fault_review,
      "faultStates": fault_states,
      "faultTemporalTags": fault_temporal,
      "carrotCutoutActiveEvents": cutout_active_count,
      "humanReviewRequired": bucket != "OBSERVE",
      "controlAuthorization": False,
      "publicRoadAuthorization": False,
    })

  queue.sort(key=lambda x: (BUCKET_ORDER[x["bucket"]], -x["count"], x["firstFrameId"], x["fingerprint"]))
  return {
    "schemaVersion": 1,
    "stage": "OFFLINE_GUARDIAN_REVIEW_QUEUE",
    "sourceHead": source_head,
    "sourceBranch": source_branch,
    "groups": len(queue),
    "eventsRepresented": sum(int(x["count"]) for x in queue),
    "queue": queue,
    "notes": [
      "Queue order is for human/root-cause review only.",
      "Fingerprint grouping is categorical and does not establish scenario ground truth.",
      "Counts describe input events; they do not establish independent incidents or a physical root cause.",
      "Representative selection is lowest frame ID, then lexicographically lowest process ID.",
      "Carrot cut-out counts are descriptive context, not control authorization.",
    ],
    "controlAuthorization": False,
    "publicRoadAuthorization": False,
  }


def main() -> int:
  ap = argparse.ArgumentParser()
  ap.add_argument("--events", type=Path, required=True)
  ap.add_argument("--output", type=Path, required=True)
  ap.add_argument("--include-observe", action="store_true")
  args = ap.parse_args()

  values: list[dict[str, Any]] = []
  with args.events.open("r", encoding="utf-8") as f:
    for line_no, line in enumerate(f, 1):
      if not line.strip():
        continue
      value = json.loads(line)
      if not isinstance(value, dict):
        raise SystemExit(f"line {line_no}: JSON object required")
      values.append(value)
  try:
    report = build_review_queue(values, include_observe=args.include_observe)
  except ValueError as exc:
    print(json.dumps({"status": "HOLD", "reason": str(exc)}, indent=2))
    return 2
  text = json.dumps(report, indent=2, ensure_ascii=False, allow_nan=False) + "\n"
  args.output.parent.mkdir(parents=True, exist_ok=True)
  args.output.write_text(text, encoding="utf-8")
  print(text, end="")
  return 0


if __name__ == "__main__":
  raise SystemExit(main())
