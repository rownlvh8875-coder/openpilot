# Lateral actuation observability implementation plan and contract

> For agentic workers: execute with superpowers:executing-plans and test-driven-development. The user explicitly authorized implementation, fork publication and a Draft PR.

Goal: publish read-only, versioned evidence bound to one host CI.apply cohort, without changing control arguments, controller outputs or CAN.

Architecture: controlsd attaches controller calculation evidence to its CarControl. card copies the final request after its existing suppressions, samples existing topic health, and retains the matching returned output. The next existing carOutput publication carries that completed receipt. No publication is moved or added.

Tech stack: existing Python 3.12.3+, pycapnp 2.1.0, Cap'n Proto schemas and pytest; no AutoTune dependency.

Spec: the user-approved Phase 8A.1 requirements; the normative contract below records implementation choices.

## Source, authority and phase status

- Upstream: https://github.com/ajouatom/openpilot, carrot-wip at exact 604bc0a22733e648ec05b268da1ffaae7db52a69. Remote head was unchanged at task start.
- Fork: https://github.com/rownlvh8875-coder/openpilot. Its carrot-wip remains at 1ab3448148c936ea257389abaaf6e4ab26dd4af0; it is not the feature base.
- Clean base: base/carrot-wip-604bc0a points directly at the upstream SHA, with no added commit.
- Feature: feature/autotune-actuation-observability, branched directly from that upstream SHA.
- Cyberpilot audit PR #4 was merged as 73769b7a173c0ee49f7d18335c06554c6e4291f0 at 2026-10-28T03:53:54Z. Parents: 09f3f58d9f9f7a59d6f983c4e0536c092aab1a4f and 74cb47a896df54a2888927c62d199a701fba4e7d. No further cyberpilot-autotune commit is part of this work.
- PHASE_8B_PRECHECK: BLOCKED
- PHASE_8B_IMPLEMENTATION: NOT_STARTED
- BLOCKER: REQUIRED_SIGNAL_EVIDENCE_ABSENT
- NEXT_TASK: PHASE_8A_1_CARROT_OBSERVABILITY
- PERSISTENCE: NOT_AUTHORIZED
- APPLY: NOT_AUTHORIZED

Do not repeat the unchanged-SHA Phase 8B precheck. This change creates the missing source evidence; a separate contract re-audit must inspect its eventual exact head.

## Global constraints

No learner/adapter connection, Params access addition, persistence, installation, vehicle testing, real log, tuning-value change, safety-hook change or upstream PR. Existing CI.apply arguments, returned actuator values, CAN bytes/count/order, engagement, override, readiness/reboot gates and saturated timers must remain unchanged. Unknowns use enum ordinal zero, never a fabricated False. Source and publication timestamps are not receive timestamps or physical acknowledgements.

## Evidence layers

1. Requested: controlsd's calculation and original CarControl event.
2. Host admitted: final CC.latActive actually passed to CI.apply after card suppression. Invalid/stale/missing source evidence means unavailable, even if the existing control path still calls apply.
3. Safety permission observed: a fresh, valid, known live pandaStates publication reports unanimous controlsAllowed and no explicit faults. This is not per-frame safety-hook acknowledgement.
4. Physical effect is outside this contract and is not established by any receipt field.

## Schema and cohort lifecycle

Append CarControl.lateralControlObservation @18 and CarOutput.actuationReceipt @1 in opendbc_repo/opendbc/car/car.capnp. Do not alter any existing ordinal. The new observation contains version, controllerKind and instantaneousLimitState. Controller kind is unknown/torque/pid/angle/mebCurvature/other, with unknown at zero. Limit state is unavailable/notLimited/limited, with unavailable at zero.

ActuationReceipt contains receiptVersion, sourceCarControlMonoTime, hostLateralActivityState, safetyPermissionState, safetyStateMonoTime, controllerKind, instantaneousControllerLimitState, finalRequestedActuators, controllerOutputActuators, outputLimitState, hostApplyMonoTime, controllerOutputReturned and safetyObservationMonoTime. Host and safety states also reserve zero for unavailable. Timestamp fields use an explicit unavailable/value union plus clockDomain (unavailable/monotonic/bootTime); an absent stamp is not a valid numeric zero. A zero receipt version means no completed receipt. Actuator struct presence must be checked before reading default numeric fields.

controllerOutputReturned is an enum (unavailable=0, returned=1), not a Boolean: an absent return observation must not look like a proven negative. It reports only that the software call returned; output struct presence and finite validity remain separate. This new field was finalized before branch publication; no upstream field type or ordinal is changed.

The cohort identity is (sourceCarControlMonoTime, hostApplyMonoTime). Repeated use of a still-fresh carControl can produce distinct apply cohorts; SubMaster.frame is never an identity. hostApplyMonoTime uses the existing monotonic apply-start measurement, not a new clock call or a physical response time. carOutput's Event.logMonoTime remains its later publication time.

card.state_publish continues to run before controls_update. The stored completed receipt belongs to the preceding apply and includes its own source identity and copied request/output. It is never combined with the next carControl. A skipped apply clears stored receipt state for the next publication. Every copy is isolated from subsequent source/controller mutation. Observability failure returns unavailable evidence and must not interrupt or modify the original control path.

## Health and unavailable policy

Require actual seen/alive/valid/freq_ok flags, positive known publication metadata, bounded publication and receipt ages, and no health bypass for the relevant topic. Reuse SubMaster's existing ten-service-period alive envelope as a telemetry-only software freshness bound; no new vehicle threshold is introduced. A future/domain-incompatible stamp rejects evidence. Simulation and replay, or unknown simulation status, cannot produce safety permission evidence.

Python carControl and SubMaster receive observations use CLOCK_MONOTONIC; pandaStates uses C++ MessageBuilder.initEvent -> nanos_since_boot -> CLOCK_BOOTTIME (openpilot/cereal/messaging/messaging.h:47-49 and openpilot/common/timing.h:10-13 at the source pin). They diverge across suspend. One guarded telemetry-only CLOCK_BOOTTIME query is recorded as safetyObservationMonoTime for each apply. Compare panda publication age in that boot-clock domain and receive age in the host monotonic domain. Do not subtract a panda boot stamp from host monotonic time. Unsupported/failed boot-clock queries make safety permission unavailable. CI.apply's existing now_nanos argument and all existing clock calls/scheduler cadence remain unchanged; copying/clock sampling has nonzero telemetry overhead, with no hard real-time claim.

For pandaStates use all reported pandas conservatively: a nonempty known set must match CP.safetyConfigs count/model/parameter by index; health and timestamps pass; all permission bits agree; faultStatus is none, faults are empty, heartbeatLost and safetyRxChecksInvalid are false. Unrecognized wire enum values, mixed/unknown sets and explicit fault evidence are unavailable. A fresh unanimously false permission is disallowed. A fresh unanimously true permission is allowed. Neither state acknowledges a CAN frame. Timestamp wrappers remain unavailable when the relevant timestamp is not usable.

## Controller calculation and output limits

Append instantaneousLimitState to the actual log states: PID @10, torque @14, angle @5. Preserve the existing saturated field and _check_saturation implementation/results. Inactive or missing calculation evidence is unavailable.

- PID and torque: the existing raw output-limit proximity predicate, or curvature_limited, before the timer's speed/override/rate exclusions.
- Angle: the existing angle-error threshold, or curvature_limited, independently of the timer result.
- MEB: inspect the actual curvature PID control and finite positive/negative limits, or curvature_limited. Never use the dummy angle state; missing PID evidence remains unavailable.

The output comparison uses the existing controlsd controller-aware expressions and unchanged thresholds: torque/PID compare torque, angle compares steeringAngleDeg, MEB compares curvature difference times speed squared. A pure helper may be shared only with equivalence tests; it must preserve the original controlsd arithmetic, including edge cases. The receipt wrapper rejects non-finite or unknown evidence without changing that existing control result. These are observations of software limiting, not new actuator limits.

## Review focus and tests

The highest-risk cases are request mutation during apply, repeated source commands across distinct applies, replay/suspend clock differences, unknown or mixed multi-panda health, and observability exceptions at the control boundary. Tests must pin all five, alongside legacy schema defaults, final suppression, controller timer identity and CAN preservation.

### Task 1: Schema and cohort receipt

Files: car.capnp, new openpilot/selfdrive/car/actuation_receipt.py, card.py and test_actuation_receipt.py/test_actuation_receipt_schema.py.

- [x] Write schema/default/legacy ordinal and receipt/cohort tests; run them before implementation and record expected failures.
- [x] Add append-only schemas and helpers capture_actuation_receipt(sm, CP, CC, host_apply_ns, replay) / complete_actuation_receipt(receipt, output, speed_mps).
- [x] Integrate copy-before-apply and completion-after-return; retain existing publication/control/CAN call order and clear skipped cohorts.
- [x] Pass missing/stale/invalid source, final reboot/readiness, health/multi-panda, immutable copy, exception and repeated-command tests; commit.

### Task 2: Instantaneous controller evidence

Files: log.capnp, new controls/lib/lateral_observation.py, latcontrol_pid.py/torque.py/angle.py, controlsd.py, test_lateral_observation.py and affected AST test fixtures.

- [x] Write failing active/inactive/raw-limit/curvature tests and timer/return equivalence checks for PID, torque, angle and actual MEB PID.
- [x] Populate new state fields and same-CarControl evidence, with version/kind matching; dummy and missing metadata remain unavailable.
- [x] Prove unchanged controlsd limit comparisons and run existing controls/card regressions; commit.

### Task 3: Behavior evidence, CI and publication

Files: focused behavior tests, a fork-only observability workflow and this document.

- [x] Pin synthetic legacy schema/CAN evidence at the exact base; compare real controller outputs, apply arguments and CAN bytes/count/order with observations enabled/absent.
- [x] Run native schema loading/generation, regression, lint/type checks where supported, and document all exclusions or blockers.
- [ ] Obtain fresh read-only whole-branch review, fix material findings with regression tests, push only the clean base and feature to the user's fork, and create the required Draft PR.
- [ ] Inspect exact-head CI, record the precise readiness-for-re-audit decision, keep the worktree clean and stop. Do not declare Phase 8B ready.

## Validation ledger

Baseline setup: the Windows checkout is isolated on the requested feature branch. WSL Ubuntu 24.04 / Python 3.12.3 uses a separate native-filesystem validation checkout of the same Git objects. Pytest collection on the NTFS mount traversed a protected Windows root entry; this environment failure is separate from product tests. Native schema tests use the repository-pinned pycapnp 2.1.0. No reference/vehicle process is started.

Ruling: retain a native Linux validation checkout rather than changing production paths or pytest policy to accommodate NTFS collection. Only task-owned files are synchronized into it; final verification must identify the real source commit.

Schema RED: four compatibility/default tests failed because the new fields were absent. Receipt RED: 51 tests failed with the producer module absent. GREEN: 166 tests pass, including schema, receipt, angle saturation, lateral readiness, lateral startup control and card diagnostics. Native Cap'n Proto 1.0.1 generated all cereal/car schemas and g++ accepted all generated C++ translation units with -fsyntax-only.

The focused test plugin isolates external settings IO with a strict read-only synthetic Params double; it does not replace controller, schema, vehicle-model or CAN algorithms. Native Params bindings are not available in the minimal WSL environment. These tests do not validate Params or a production IPC process, and no real settings/logs are read.

Task 1 complete: card integration RED had eight missing-receipt failures while six baseline differential cases passed. An additional malformed-publication case failed after initial integration, then passed with the telemetry-only guard. Current focused regression is 203 passed, zero failed/skipped, including existing Tesla final-boundary and event-readiness tests. Their AST fixture namespace now includes the real new telemetry helpers and the CP attribute present in production. The six card differential cases execute both complete production methods from the pinned base/current source, preserving apply arguments, returned actuator values, synthetic CAN payload bytes/count/order and original subscribed CC. Hardware-specific CAN packing still requires the separate Task 3 evidence; these are not physical CAN tests.

Ruling: source tracing proved different Python/C++ clock domains. Add explicit domain tags and the boot-clock observation anchor rather than assuming a common epoch; otherwise a suspend offset can make an old safety publication appear fresh. Three clock-domain tests failed before this extension. A malformed matching safetyModel wire enum also failed before known-enum validation. These are evidence corrections only, without changing any actuation decision.

Task 2 complete: instantaneous/controller evidence RED was 27 failures with 14 unchanged behavior cases passing. Two full state_control cases then failed on missing same-command observation. The implementation and fixture namespace update make the focused suite pass 283 tests. Three controllers each match pinned-baseline returns, legacy log fields, PID state and saturation timer over 80 synthetic transitions. Thirty full publish-comparison blocks match the pinned source, including inactive cached state and non-finite legacy inputs. The original arithmetic and thresholds are shared unchanged with the receipt; the receipt adds finite/known guards without changing controlsd behavior. An unknown calculation wire enum failed before being mapped to unavailable. MEB observes the actual bounded curvature PID and leaves its dummy angle evidence unavailable.

Ruling: use a pure shared output comparison, with the original active guard, controller branch selection, operators and threshold values preserved. Move the existing angle/curvature constants without changing their values, and preserve their old module exports. Differential tests exercise the exact pinned/current production blocks rather than a second implementation of the expected result. Native Params and full vehicle IPC remain outside the focused offline test claim.

## Reproducible offline checks

The fork-only actuation-observability workflow runs on Ubuntu 24.04 with Python 3.12.3 and current 3.12, for feature pushes and Draft PRs against the pinned base. Existing deployment/model/wiki workflows are branch-filtered and are not invoked by this feature/base publication. The ordinary user-docs PR check is preserved. Docs-Not-Needed: read-only developer telemetry; no user-facing setting or control behavior changes.

With the workflow's explicit development dependencies and native capnproto/libcapnp-dev/g++ installed, run from the repository root:

```sh
git diff --check 604bc0a22733e648ec05b268da1ffaae7db52a69
python tools/actuation_observability/verify.py lint
ty check --no-progress openpilot/selfdrive/car/actuation_receipt.py openpilot/selfdrive/controls/lib/lateral_observation.py
python tools/actuation_observability/verify.py schema
python tools/actuation_observability/verify.py test
```

The test command expands to pytest -c /dev/null --rootdir=. --noconftest -q with the explicit observability_support settings-IO plugin, branch coverage for the two new runtime helpers, and the eleven safe offline test files listed in verify.py. It first generates DBCs using the existing source generator. Git must contain the pinned base object for differential tests. No network fixture, route loader, actual CAN interface, full control process or device Params is used.

Local result: 287 passed, zero failed/skipped. New-helper branch-inclusive coverage is 95.65% combined (receipt 96.18%, lateral observation 94.74%). Nine new Python files pass Ruff formatting; no new Ruff diagnostics exist. Forty-one existing diagnostics in touched legacy source remain unchanged (card 23, controlsd 10, angle 2, torque 6); the baseline-aware check compares diagnostic multiplicity, code, message and source line rather than suppressing newly introduced errors. ty reports no issues in the two new runtime modules. Native schema generation and g++ syntax compilation pass for all four generated car/cereal C++ units. This is not a complete openpilot/SCons production build or verification of native Params/IPC/hardware/real-log suites.

Real offline CAN evidence: Honda Civic, Toyota RAV4, Nissan Leaf and VW ID.4 MEB each execute forty synthetic cycles both with pinned-base controller.update and with current code plus attached observations/receipt copying. Actual CarInterface, CarController, DBC and CAN packers run; no control algorithm or packer is mocked. All returned actuator values, CAN payload bytes/count/order and synthetic settings operation traces match. Existing interface-construction settings writes are captured only in an in-memory test double; no actual Params read/write occurs. All temporary aliases are restored, including modules imported during a test. These profiles prove software regression behavior for the synthetic cases, not vehicle suitability, road performance or physical acceptance.

The fresh core review at 872d7f54096e36a1efd012941851109cb808960d found zero Critical/Important/Minor issues and independently reran 283 checks. Final review/CI must additionally cover the later return-state enum clarification and Task 3 files; exact remote outcomes are recorded by the final Draft PR checks/report.

## Separate Phase 8A contract re-audit procedure

1. Pin the exact observability feature head, its unchanged upstream base, changed blob hashes and actual generated schemas. Do not repeat the old unchanged-upstream Phase 8B precheck.
2. Trace controlsd calculation -> same CarControl observation -> card final suppression -> unchanged CI.apply arguments -> stored return -> next carOutput, including skipped/repeated commands. Match both source command and host-apply timestamps; never join to the current command by receiver frame.
3. Verify controller kind/version, enum availability, legacy defaults, MEB real-PID evidence and unavailable dummy/unknown states. Independently interpret instantaneous and timer-qualified states.
4. Verify each timestamp's domain, cohort publication delay, boot-clock safety age, monotonic receipt age, known configuration-correlated multi-panda health and unsupported/simulation/replay exclusions. Permission observations cannot acknowledge individual CAN frames.
5. Revisit the remaining Snapshot estimate/sign/freshness/context and caller-owned immutable-baseline contracts. This branch adds no registry, vehicle limits, adapter or application permission.
6. Only that separate audit may judge Phase 8B readiness. This work can establish OBSERVABILITY_READY_FOR_CONTRACT_REAUDIT, never vehicle acceptance or authorization for persistence/Apply.
