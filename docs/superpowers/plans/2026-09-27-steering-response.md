# Offline steering response assessment plan

> **For agentic workers:** Execute the approved diagnostic inline, with independent model-math implementation and final review.

**Goal:** Determine whether recorded steering commands predict held-out measured steering sufficiently to justify further model-based controller comparison.

**Architecture:** Decode full cereal logs, join only past service messages on a 20 Hz grid, reject invalid/intervened intervals, form independent 5 second windows, and split by chronological segments. Fit a stable first-order response family on training windows, select on validation, and report untouched test errors against no-command and persistence baselines.

**Tech Stack:** Python 3.12+, numpy; extraction additionally pycapnp 2.1.0 and zstandard.

**Spec:** User-approved three-stage response-analysis design in this conversation, 2026-09-27. This is an offline diagnostic, not a vehicle controller or a deployable vehicle model.

## Global constraints

- No runtime, vehicle settings, CAN limits, driver monitoring, or default branch changes.
- No raw route data, settings dumps, identifiers, or private analysis outputs in public git.
- Logged carOutput is the software-applied command, not an EPS torque measurement.
- No held-out outcomes in fitting or candidate selection; no observed future angles in prediction recurrence.
- Effective recorded-signal lag is not physical actuator delay; observational prediction cannot establish causal response under a new controller.
- Fail/abstain explicitly for insufficient data or poor prediction. Do not rank Kp candidates unless feedback validation is separately justified.

## Review focus

- Missing/corrupt segments must not silently contribute partial data.
- Brief driver intervention and stale service intervals must invalidate windows.
- Windows cannot cross gaps or train/validation/test boundaries.
- Predictions must not use future measured steering or future commands.
- Empty/malformed data must produce actionable errors, not improvement claims.

## Tasks

- [x] Add extraction/alignment tests, see failures, implement `extract.py`: `align_segment(records, segment, driver_limit=50)` and `make_windows(rows)`.
- [x] Add causal prediction and synthetic-recovery tests, see failures, implement `model.py`: `fit_candidates`, `predict`, `evaluate`, `baseline_metrics`.
- [x] Add `assess.py` CLI, run complete extraction and train/validation/test assessment on authorized local logs. Keep results private.
- [x] Compare driver filtering sensitivity and source timing; independently review code and conclusions. Fix material findings, rerun relevant tests, commit tool and create draft PR.
- [x] Save a user report with exact statistics, limitations, code provenance and next action.

## Verification ledger

- Extraction: missing API failures followed by 6 passing behavior tests; model math: 8 passing tests including known synthetic response recovery and no future-output leakage; split assessment: 2 passing tests.
- Independent review found three material issues: stale intervals hidden between grid endpoints; incomplete zstd frames silently accepted by streaming decompression; unused feature margins expanding apparent training support.
- All three were reproduced as failing regressions and fixed. Final standalone suite: 19/19 passed; compilation and whitespace checks passed. Root pytest collection could not start because pytest is not installed in this runtime. Vehicle/native integration is outside this offline tool's validation.
- Initial speed eligibility 15–30 m/s left no training windows. Before fitting any model, eligibility was expanded to 8–40 m/s based on route coverage; chronological split boundaries stayed fixed. This is documented to distinguish coverage inspection from outcome-driven tuning.
- Code lives on an isolated development branch. Review did not require changes to vehicle runtime or settings. No findings were deferred.
- Final full-log CLI rerun retained the failed-prediction conclusion for all three driver filters. The private report was delivered separately; PR #19 remains draft and unmerged. No Kp performance ranking or physical actuator-delay estimate was emitted.
