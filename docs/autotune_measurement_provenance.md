# Observation v2 measurement provenance and pre-controller target

## Authorized scope and implementation plan

The owner's Phase 8A.3 specification authorizes read-only calculation evidence,
tests and documentation on the existing feature/autotune-actuation-observability
branch. Original upstream base: 604bc0a22733e648ec05b268da1ffaae7db52a69. Starting
observability head: 4d89f044fc74138b0938f309ff8ea88d90faddcd. Both were rechecked
against Draft PR #20; exact-head checks passed and reviews/threads were empty.

Cyberpilot audit PR #5 merged at 2026-10-28T06:11:46Z as
03c3f2f36cbe47a9afec97ce7b9d411d3aa81ccb, with parents
73769b7a173c0ee49f7d18335c06554c6e4291f0 and
d3ef00fd945e2ed3f96f4df3974cb8d72930454b. A fresh independent review found no
Critical/Important/Minor findings. No further cyberpilot-autotune commit is part
of this work.

Goal: expose the exact VM inputs/result and pre-controller target on the same
source command, without changing any original control calculation, Params access,
controller argument, output, limit, gate, timer or CAN message.

Architecture: a frozen helper captures already computed VM values immediately
after self.curvature is calculated. The clipped desired value is captured before
any MEB correction. Only an active supported observation-v2 command receives
the new evidence. Existing receipt v1 joins the retained exact source command
by sourceCarControlMonoTime; no separate receipt interpretation or adapter exists.

Tech stack: existing Python 3.12.3+, stdlib, existing cereal schema bindings,
Cap'n Proto and the existing offline pytest/schema/type/CAN verification tools.
Execute inline with TDD and one final independent read-only implementation review.

- [x] Verify and merge audit PR #5; preserve all source branch history.
- [x] Baseline offline validation: 288 passed, zero failed/skipped.
- [x] RED/GREEN: append optional numeric and observation-v2 schema; legacy defaults
  and ordinals remain unchanged. Files: car.capnp; test_measurement_observation.py.
- [x] RED/GREEN: add measurement_observation.py and same-call controlsd capture;
  test current health, timestamps, effective configuration and exact values.
- [x] RED/GREEN: capture the pre-controller target and prove MEB separation,
  unchanged VM/controller/Params behavior and delayed source lookup.
- [x] Maintain existing receipt compatibility, all regression and offline CAN
  checks; require new-helper branch coverage >=95%; update fork CI and docs.
- [x] Fresh independent implementation review and clean-checkout verification.

Publication uses append-only commits on PR #20. Final exact-head CI outcomes are
recorded in the PR and completion report without a self-referential source hash.

Review focus: stale health after a sticky startup latch; Float32 rounding of actual
Python calculation values; fallback incorrectly marked live; missing/unknown
numeric values becoming zero; previous receipt joined to a newer command.

Ruling: optional numeric payloads use Float64 so a Python binary64 calculation
result/effective ratio/target survives serialization exactly. The recommended
Float32 shape would round those values and violate the required exact-equality
proof. The unavailable/value union semantics are identical, including genuine
available zero. No original arithmetic is changed to fit the telemetry format.

## Observation v2 wire contract

LateralControlObservation keeps v1 ordinals @0 version, @1 controllerKind and
@2 instantaneousLimitState. V2 appends @3 measurement, @4 desiredTargetState and
@5 desiredTargetCurvatureInvM. Measurement has its own version: zero unavailable,
one these measurement semantics. Old v0/v1 bytes decode all new members as
unavailable. Unknown modes/kinds/states and incompatible outer versions cannot
publish usable numeric evidence. Existing log controller timer fields and raw
instantaneous flags are unchanged.

ObservationFloat64 is an unavailable/value union, not a zero sentinel. All seven
measurement numbers and the desired target use it. Float64 preserves the actual
Python calculation; source CarState/liveParameters Float32 values remain exactly
representable when promoted. Existing actuator curvature is still Float32. For
normal controllers the target is the same quantity before wire rounding; tests
also prove exact equality for representable zero and Float32-equivalence for
nonzero targets. MEB has a genuine algorithmic difference, not just rounding.

## Calculation and source provenance

The original state_control statement sequence remains intact:

1. Select lp using the existing startup gate/fallback; calculate x and resolved sr
   using the existing Params reads; call VM.update_params(x, sr).
2. Compute steering angle minus offset and self.curvature with the existing VM.
   Immediately freeze the resulting curvature, CS speed/angle, actual x/sr,
   lp offset/roll, publication stamps and current health. No control calculation
   is repeated by the helper and no new settings access occurs.
3. Run the existing longitudinal/desired-target calculations. Immediately after
   clip_curvature updates self.desired_curvature, freeze that pre-controller value.
4. Run unchanged MEB PID/EPS/VM correction and normal lateral controller update.
   Preserve existing actuator finite sanitization and instantaneous/timer logic.
5. Attach only the new fields to the same active supported CC, before its Event
   is created in publish. No later ControlsState reconstruction occurs.

carStateMonoTime and liveParametersMonoTime come directly from the exact current
SubMaster slots used by this invocation, not recv_time or frame. All new stamps
are CLOCK_MONOTONIC. calculationMonoTime is a guarded monotonic_ns observation
after the VM result and before CC creation. Valid evidence requires source stamps
to be positive and not future relative to this earlier anchor, which is stronger
than checking against the later command Event. An unsupported clock is unavailable.
Fallback has no liveParameters source stamp. The existing receipt's separate panda
CLOCK_BOOTTIME semantics do not change and are never subtracted from these stamps.

## Current health versus startup latch

The latch is unchanged and is not validity evidence. Each invocation directly
checks carState/liveParameters seen, alive, valid and freq_ok. All must be actual
known booleans. A simulator/unknown simulation flag or an ignored-health topic
is unavailable. carState.canValid and liveParameters valid/sensorValid/posenetValid
are also required; payload failures are invalid even if transport flags pass.

- vehicleModelLive identifies the actual lp object selected from SubMaster.
- vehicleModelFallback identifies NominalLateralParameters; it is never valid
  for learning. Unknown selections are unavailable, not guessed live.
- valid additionally requires present non-future stamps; finite actual numbers;
  nonnegative speed, positive effective stiffness/ratio and positive finite live
  geometry. These are numeric representability checks, not approved vehicle bounds.
- Known bad current health, non-finite inputs or invalid effective values give
  invalid. Missing health/timestamps/clock/mode give unavailable.
- All measurement numeric unions stay unavailable unless validity is valid.
  Invalid/unavailable source metadata can retain known stamps for diagnostics.
  Inactive or unsupported commands leave the entire nested evidence unavailable.

No new age or skew threshold exists. alive/freq_ok are observations of current
SubMaster policy, not proof of sensor acquisition latency. The future adapter's
caller-approved immutable age/skew policy must still examine the preserved source
times and delayed receipt. That acceptance policy and its adapter are not implemented.

## Effective configuration and static context

The group records the actual effectiveStiffnessFactor passed as x, the actual
resolved effectiveSteerRatio passed as sr (including CustomSR/SteerRatioRate), and
the offset/roll/speed/steering angle used in that calculation. No opaque epoch
stands in for those values. Values are frozen before later mutation and checked
again at serialization; inconsistent valid/fallback/health/stamp copies fail
unavailable without touching existing controller evidence or actuators.

Static VehicleModel configuration must be bound to the pinned CarParams/source
context: mass, rotationalInertia, wheelbase, centerToFront, steerRatioRear,
tireStiffnessFront and tireStiffnessRear; initial steerRatio is used during VM
construction, while the published effective ratio describes this calculation.
See opendbc_repo/opendbc/car/vehicle_model.py, VehicleModel.__init__/update_params,
at original base 604bc0a and starting head 4d89f044. These sources are unchanged.
No VIN, route, GPS or driver identity is part of the evidence or context proposal.

## MEB target lifecycle and receipt join

The pre-PID target is the clipped self.desired_curvature, captured before the
MEB block overwrites actuators.curvature with its PID plus EPS/VM correction.
Available requires active CC, supported observation v2 and a finite value captured
in that invocation. Inactive/non-finite/unsupported input stays unavailable.
The source preserves both values; neither is substituted for the other.

ActuationReceipt remains version 1 with its prior fields/meaning. Capture accepts
source observations 1 and 2 for the existing kind/instantaneous evidence. No
sourceObservationVersion field is necessary because **exact source CC lookup is
mandatory** for measurement/target interpretation. A receipt alone cannot supply
these groups. V1 sources remain legacy kind/limit evidence only.

Use the receipt's sourceCarControlMonoTime to retrieve an immutable retained CC,
and hostApplyMonoTime to distinguish repeated applies of that CC. Missing exact
source rejects; never use the newest command or current outer Event.valid.
Publication order and timing remain unchanged. Tests demonstrate the previous
receipt still resolves its old v2 target/measurement after current evidence changes.

## Verification and separate re-audit

Tests cover optional unions/legacy bytes, all three normal controller families,
real MEB PID target separation, current invalid health after latch, exact private
settings/configuration, numeric failures, timestamps and telemetry failure/IO
boundaries. Full state_control differential cases preserve values, VM/PID state,
timer and Params read traces. An AST equivalence check removes only the three
new observation statements and proves the entire original function body and
operation order identical to 4d89f044. Real offline CarController/CAN packers are
compared with both v1 and v2 metadata for four profiles, forty cycles per variant.

The final verification record and exact head are supplied with PR #20 and the
completion report. A full production SCons/native IPC/Params/hardware/real-log
qualification is not claimed by the focused offline checks.

Phase 8A.4 must pin the final exact head, inspect schema/producer/receipt joins,
check actual CP configuration and all health/clock/availability rules, re-evaluate
MEB desired versus measured semantics and the remaining caller baseline/policy
contracts. Model curvature is not a physical measurement, CAN acknowledgement or
vehicle-response confirmation. No Phase 8B readiness follows from this source work.

## Implementation qualification record

Status: **OBSERVABILITY_READY_FOR_MEASUREMENT_REAUDIT**. This is source evidence
readiness for a separate audit only. Phase 8B, persistence and Apply remain
unauthorized; no Phase 8A.4 or other feature starts here.

The independently reviewed implementation head is
b4e7334cf2275ba4e9f90b68ea82cf10302190d3. Fresh native Linux checkout verification
passed **378 tests, zero failed/skipped/warnings**. Branch-inclusive coverage is
100.00% for the new helper and 97.37% combined across the three observed helpers;
existing receipt 96.18% and lateral helper 94.74% are maintained. No whole-project
coverage claim is made. Four real CAN profiles run with both v1/v2 metadata and
forty cycles per variant; tracked DBCs remain unchanged.

Ruff reports no added diagnostics (41 pre-existing diagnostics retained), with
13 new Python files relative to upstream passing formatting. ty passes all three
runtime evidence modules. Native Cap'n Proto generation and g++ syntax checking
pass for four generated C++ units. The source AST comparison and full calculation
differentials preserve original control operations/order and Params access.

An independent read-only review of 4d89f044 through b4e7334 found zero Critical,
Important or Minor findings and independently repeated the tests/type/schema
checks. Only qualification documentation follows that implementation head.

Executed from the Linux checkout using the existing isolated development runtime:

```sh
git diff --check 604bc0a22733e648ec05b268da1ffaae7db52a69 HEAD
/tmp/carrot-observability-env/bin/python tools/actuation_observability/verify.py lint
/tmp/carrot-observability-env/bin/ty check --no-progress openpilot/selfdrive/car/actuation_receipt.py openpilot/selfdrive/controls/lib/lateral_observation.py openpilot/selfdrive/controls/lib/measurement_observation.py
env PATH=/tmp/carrot-capnp-tools/extracted/usr/bin:/usr/bin:/bin LD_LIBRARY_PATH=/tmp/carrot-capnp-tools/extracted/usr/lib/x86_64-linux-gnu /tmp/carrot-observability-env/bin/python tools/actuation_observability/verify.py schema --capnp-include /tmp/carrot-capnp-tools/extracted/usr/include
/tmp/carrot-observability-env/bin/python tools/actuation_observability/verify.py test
/tmp/carrot-observability-env/bin/python -m coverage report --precision=2
git status --short
```

The tool enforces new-helper coverage >=95%. Full production SCons, native CAN
extension, device Params/IPC, hardware, live timing and real-log acceptance remain
unverified. No physical measurement or vehicle response is established by these
offline software checks. Final exact-head push/PR checks remain visible on Draft
PR #20; neither Ready nor merge is part of this publication.
