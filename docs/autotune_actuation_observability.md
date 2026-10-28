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

ActuationReceipt contains receiptVersion, sourceCarControlMonoTime, hostLateralActivityState, safetyPermissionState, safetyStateMonoTime, controllerKind, instantaneousControllerLimitState, finalRequestedActuators, controllerOutputActuators, outputLimitState, hostApplyMonoTime and controllerOutputReturned. Host and safety states also reserve zero for unavailable. Timestamp fields use an explicit unavailable/value union; an absent stamp is not a valid numeric zero. A zero receipt version means no completed receipt. Actuator struct presence must be checked before reading default numeric fields.

The cohort identity is (sourceCarControlMonoTime, hostApplyMonoTime). Repeated use of a still-fresh carControl can produce distinct apply cohorts; SubMaster.frame is never an identity. hostApplyMonoTime uses the existing monotonic apply-start measurement, not a new clock call or a physical response time. carOutput's Event.logMonoTime remains its later publication time.

card.state_publish continues to run before controls_update. The stored completed receipt belongs to the preceding apply and includes its own source identity and copied request/output. It is never combined with the next carControl. A skipped apply clears stored receipt state for the next publication. Every copy is isolated from subsequent source/controller mutation. Observability failure returns unavailable evidence and must not interrupt or modify the original control path.

## Health and unavailable policy

Require actual seen/alive/valid/freq_ok flags, positive known publication metadata, bounded publication and receipt ages, and no health bypass for the relevant topic. Reuse SubMaster's existing ten-service-period alive envelope as a telemetry-only software freshness bound; no new vehicle threshold is introduced. A future/domain-incompatible stamp rejects evidence. Simulation and replay, or unknown simulation status, cannot produce safety permission evidence.

For pandaStates use all reported pandas conservatively: a nonempty known set, health and timestamps must pass; all permission bits agree; faultStatus is none, faults are empty, heartbeatLost and safetyRxChecksInvalid are false. Mixed/unknown sets and explicit fault evidence are unavailable. A fresh unanimously false permission is disallowed. A fresh unanimously true permission is allowed. Neither state acknowledges a CAN frame. Timestamp wrappers remain unavailable when the relevant timestamp is not usable.

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

- [ ] Write schema/default/legacy ordinal and receipt/cohort tests; run them before implementation and record expected failures.
- [ ] Add append-only schemas and helpers capture_actuation_receipt(sm, CP, CC, host_apply_ns, replay) / complete_actuation_receipt(receipt, output, speed_mps).
- [ ] Integrate copy-before-apply and completion-after-return; retain existing publication/control/CAN call order and clear skipped cohorts.
- [ ] Pass missing/stale/invalid source, final reboot/readiness, health/multi-panda, immutable copy, exception and repeated-command tests; commit.

### Task 2: Instantaneous controller evidence

Files: log.capnp, new controls/lib/lateral_observation.py, latcontrol_pid.py/torque.py/angle.py, controlsd.py, test_lateral_observation.py and affected AST test fixtures.

- [ ] Write failing active/inactive/raw-limit/curvature tests and timer/return equivalence checks for PID, torque, angle and actual MEB PID.
- [ ] Populate new state fields and same-CarControl evidence, with version/kind matching; dummy and missing metadata remain unavailable.
- [ ] Prove unchanged controlsd limit comparisons and run existing controls/card regressions; commit.

### Task 3: Behavior evidence, CI and publication

Files: focused behavior tests, a fork-only observability workflow and this document.

- [ ] Pin synthetic legacy schema/CAN evidence at the exact base; compare real controller outputs, apply arguments and CAN bytes/count/order with observations enabled/absent.
- [ ] Run native schema loading/generation, regression, lint/type checks where supported, and document all exclusions or blockers.
- [ ] Obtain fresh read-only whole-branch review, fix material findings with regression tests, push only the clean base and feature to the user's fork, and create the required Draft PR.
- [ ] Inspect exact-head CI, record the precise readiness-for-re-audit decision, keep the worktree clean and stop. Do not declare Phase 8B ready.

## Validation ledger

Baseline setup: the Windows checkout is isolated on the requested feature branch. WSL Ubuntu 24.04 / Python 3.12.3 uses a separate native-filesystem validation checkout of the same Git objects. Pytest collection on the NTFS mount traversed a protected Windows root entry; this environment failure is separate from product tests. Native schema tests use the repository-pinned pycapnp 2.1.0. No reference/vehicle process is started.

Ruling: retain a native Linux validation checkout rather than changing production paths or pytest policy to accommodate NTFS collection. Only task-owned files are synchronized into it; final verification must identify the real source commit.
