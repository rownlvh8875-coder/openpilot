#!/usr/bin/env python3
"""Qualify observer-only S1 evidence using explicit research limits."""
from __future__ import annotations
from dataclasses import dataclass
import argparse, json, math
from pathlib import Path
from typing import Any

LEGS = ("S1_OFF_BEFORE", "S1_ON", "S1_OFF_AFTER")

@dataclass(frozen=True)
class S1Metrics:
  leg: str; samples: int; p50_ms: float; p95_ms: float; p99_ms: float; max_ms: float
  frame_gaps: int; fallbacks: int; guard_violations: int; observer_write_errors: int
  observer_state_fresh: bool | None
  @classmethod
  def from_dict(cls, v: dict[str, Any]) -> "S1Metrics":
    f=v.get("observerStateFresh")
    return cls(str(v["leg"]),int(v["samples"]),float(v["p50ModelExecutionMs"]),float(v["p95ModelExecutionMs"]),
               float(v["p99ModelExecutionMs"]),float(v["maxModelExecutionMs"]),int(v.get("frameGapCount",0)),
               int(v.get("fallbackCount",0)),int(v.get("guardViolationCount",0)),int(v.get("observerWriteErrors",0)),
               f if isinstance(f,bool) else None)
  def validate(self)->None:
    if self.leg not in LEGS: raise ValueError(f"unexpected leg: {self.leg}")
    if any(v<0 for v in (self.samples,self.frame_gaps,self.fallbacks,self.guard_violations,self.observer_write_errors)): raise ValueError("counts must be non-negative")
    vals=(self.p50_ms,self.p95_ms,self.p99_ms,self.max_ms)
    if any(not math.isfinite(v) or v<0 for v in vals): raise ValueError("latency values must be finite and non-negative")
    if not self.p50_ms<=self.p95_ms<=self.p99_ms<=self.max_ms: raise ValueError("latency percentiles must be monotonic")

@dataclass(frozen=True)
class S1Policy:
  min_samples_per_leg:int; max_baseline_p95_drift_ms:float; max_baseline_p99_drift_ms:float
  max_observer_p50_increase_ms:float; max_observer_p95_increase_ms:float; max_observer_p99_increase_ms:float
  max_additional_frame_gaps:int; max_fallbacks_per_leg:int; max_guard_violations_per_leg:int; max_observer_write_errors:int
  @classmethod
  def from_dict(cls,v:dict[str,Any])->"S1Policy":
    return cls(int(v["minSamplesPerLeg"]),float(v["maxBaselineP95DriftMs"]),float(v["maxBaselineP99DriftMs"]),
               float(v["maxObserverP50IncreaseMs"]),float(v["maxObserverP95IncreaseMs"]),float(v["maxObserverP99IncreaseMs"]),
               int(v["maxAdditionalFrameGaps"]),int(v["maxFallbacksPerLeg"]),int(v["maxGuardViolationsPerLeg"]),int(v["maxObserverWriteErrors"]))
  def validate(self)->None:
    if self.min_samples_per_leg<=0: raise ValueError("minSamplesPerLeg must be positive")
    nums=(self.max_baseline_p95_drift_ms,self.max_baseline_p99_drift_ms,self.max_observer_p50_increase_ms,self.max_observer_p95_increase_ms,self.max_observer_p99_increase_ms)
    if any(not math.isfinite(v) or v<0 for v in nums): raise ValueError("latency policy limits must be finite and non-negative")
    if any(v<0 for v in (self.max_additional_frame_gaps,self.max_fallbacks_per_leg,self.max_guard_violations_per_leg,self.max_observer_write_errors)): raise ValueError("count policy limits must be non-negative")

def qualify(metrics:dict[str,S1Metrics],policy:S1Policy,*,source_head:str|None=None,source_branch:str|None=None)->dict[str,Any]:
  policy.validate()
  if set(metrics)!=set(LEGS): return {"status":"HOLD","reasons":["all three S1 legs are required"],"sourceHead":source_head,"sourceBranch":source_branch}
  for x in metrics.values(): x.validate()
  before,on,after=(metrics[n] for n in LEGS); hold=[]; hard=[]; attr=[]
  for x in (before,on,after):
    if x.samples<policy.min_samples_per_leg: hold.append(f"{x.leg}:sample_shortage")
    if x.guard_violations>policy.max_guard_violations_per_leg: hard.append(f"{x.leg}:guard_violations")
    if x.fallbacks>policy.max_fallbacks_per_leg: hard.append(f"{x.leg}:fallbacks")
  if on.observer_state_fresh is not True: hard.append("observer_state_not_fresh")
  d95=abs(before.p95_ms-after.p95_ms); d99=abs(before.p99_ms-after.p99_ms)
  if d95>policy.max_baseline_p95_drift_ms: hold.append("baseline_p95_drift")
  if d99>policy.max_baseline_p99_drift_ms: hold.append("baseline_p99_drift")
  base={"p50":(before.p50_ms+after.p50_ms)/2,"p95":(before.p95_ms+after.p95_ms)/2,"p99":(before.p99_ms+after.p99_ms)/2,"frameGaps":max(before.frame_gaps,after.frame_gaps)}
  delta={"p50Ms":on.p50_ms-base["p50"],"p95Ms":on.p95_ms-base["p95"],"p99Ms":on.p99_ms-base["p99"],"additionalFrameGaps":on.frame_gaps-base["frameGaps"]}
  if delta["p50Ms"]>policy.max_observer_p50_increase_ms: attr.append("observer_p50_increase")
  if delta["p95Ms"]>policy.max_observer_p95_increase_ms: attr.append("observer_p95_increase")
  if delta["p99Ms"]>policy.max_observer_p99_increase_ms: attr.append("observer_p99_increase")
  if delta["additionalFrameGaps"]>policy.max_additional_frame_gaps: attr.append("observer_frame_gap_increase")
  if on.observer_write_errors>policy.max_observer_write_errors: hard.append("observer_write_errors")
  if hard: status="FAIL"; fail=hard+attr
  elif hold: status="HOLD"; fail=[]
  elif attr: status="FAIL"; fail=attr
  else: status="PASS"; fail=[]
  return {"schemaVersion":2,"stage":"S1_OBSERVER_QUALIFICATION","status":status,"sourceHead":source_head,"sourceBranch":source_branch,
          "holdReasons":hold,"failReasons":fail,"baselineDrift":{"p95Ms":d95,"p99Ms":d99},"observerDeltaVsMeanBaseline":delta,
          "observerStateFresh":on.observer_state_fresh,"controlAuthorization":False,"publicRoadAuthorization":False,"telemetryAuthorization":False,
          "shadowAuthorization":False,"nextGate":"S2_TELEMETRY_PLAN_ONLY" if status=="PASS" else None,
          "thresholdProvenance":"explicit-project-research-policy-not-official-comma-safety-thresholds"}

def main()->int:
  ap=argparse.ArgumentParser(); ap.add_argument("--evidence",type=Path,required=True); ap.add_argument("--policy",type=Path); ap.add_argument("--output",type=Path); args=ap.parse_args()
  evidence=json.loads(args.evidence.read_text(encoding="utf-8"))
  source_head=evidence.get("sourceHead"); source_branch=evidence.get("sourceBranch")
  if args.policy is None:
    report={"schemaVersion":2,"stage":"S1_OBSERVER_QUALIFICATION","status":"HOLD","sourceHead":source_head,"sourceBranch":source_branch,
            "holdReasons":["explicit policy file required"],"controlAuthorization":False,"publicRoadAuthorization":False,"nextGate":None}
  else:
    policy=S1Policy.from_dict(json.loads(args.policy.read_text(encoding="utf-8"))); vals=evidence.get("legs",evidence)
    metrics={n:S1Metrics.from_dict(vals[n]) for n in LEGS if n in vals}; report=qualify(metrics,policy,source_head=source_head,source_branch=source_branch)
  text=json.dumps(report,indent=2,ensure_ascii=False)+"\n"; print(text,end="")
  if args.output: args.output.parent.mkdir(parents=True,exist_ok=True); args.output.write_text(text,encoding="utf-8")
  return 0 if report["status"]=="PASS" else (1 if report["status"]=="HOLD" else 2)

if __name__=="__main__": raise SystemExit(main())
