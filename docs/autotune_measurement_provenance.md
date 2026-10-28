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
- [ ] RED/GREEN: append optional numeric and observation-v2 schema; legacy defaults
  and ordinals remain unchanged. Files: car.capnp; test_measurement_observation.py.
- [ ] RED/GREEN: add measurement_observation.py and same-call controlsd capture;
  test current health, timestamps, effective configuration and exact values.
- [ ] RED/GREEN: capture the pre-controller target and prove MEB separation,
  unchanged VM/controller/Params behavior and delayed source lookup.
- [ ] Maintain existing receipt compatibility, all regression and offline CAN
  checks; require new-helper branch coverage >=95%; update fork CI and docs.
- [ ] Fresh review, final exact-head verification, append-only push to PR #20.

Review focus: stale health after a sticky startup latch; Float32 rounding of actual
Python calculation values; fallback incorrectly marked live; missing/unknown
numeric values becoming zero; previous receipt joined to a newer command.

Ruling: optional numeric payloads use Float64 so a Python binary64 calculation
result/effective ratio/target survives serialization exactly. The recommended
Float32 shape would round those values and violate the required exact-equality
proof. The unavailable/value union semantics are identical, including genuine
available zero. No original arithmetic is changed to fit the telemetry format.

Status: implementation in progress. Only
OBSERVABILITY_READY_FOR_MEASUREMENT_REAUDIT can be claimed after verification.
No Phase 8B, adapter, persistence, Apply, upstream PR, vehicle or real-log work.
