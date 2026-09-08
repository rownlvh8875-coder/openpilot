# Integrated eGPU Guardian / Replay / Fault Analysis

## Scope

This layer is **offline / observe-only**. It exists to turn paired BIG/SMALL evidence into a reproducible human-review queue and to model the currently reviewed Carrot-WIP eGPU failure semantics.

It does **not**:

- select the active driving model,
- publish `modelV2` or `carControl`,
- write Params,
- open VisionIPC,
- access tinygrad/AMD devices,
- change panda/OEM actuator limits,
- authorize public-road use.

Every output keeps `controlAuthorization=false` and `publicRoadAuthorization=false`.

## Reviewed Carrot runtime fault semantics

The current reviewed Carrot source behaves as follows on a runtime BIG/eGPU exception:

1. BIG was attempted for the current camera frame.
2. The exception path disables `UsbGpuActive` and latches `UsbGpuStartupFailed`.
3. `model` switches to the already-loaded QCOM SMALL model.
4. The **same camera frame is rerun through SMALL** so the failed BIG frame does not intentionally create a missing `modelV2` output.
5. Subsequent frames remain on SMALL for the current process/ignition state.

The reviewed runtime has **no automatic in-process BIG re-entry after this fallback**. Therefore the offline fault tracker treats:

`BIG_ACTIVE -> SAME_FRAME_FALLBACK -> SMALL_LATCHED`

as the expected runtime-failure sequence. Seeing BIG active again after the fallback without a known restart boundary is flagged as `unexpected_big_reentry_without_restart_boundary`.

A single `QCOM + startupFailed=true` observation does not prove whether the latch originated at startup or from an earlier runtime failure. The tracker deliberately uses the generic reason `startup_failed_latched_small`; only sequence evidence can add `small_latch_confirmed`.

## Temporal Guardian

The temporal Guardian updates scene state on every paired frame, not only on disagreement frames.

It records review tags such as:

- `lead_acquired` / `lead_lost`
- `close_lead_acquisition` / `close_lead_entry`
- `rapid_range_closure`
- `closing_fast_onset`
- `standstill_entry` / `standstill_exit`
- `stop_disagreement_onset` / `stop_disagreement_resolved`
- `scene_frame_gap`
- `cut_in_candidate_heuristic`

`cut_in_candidate_heuristic` is a heuristic review label only. It is not scenario ground truth and requires independent video/radar/lane review.

## Carrot cut-out context

After the reviewed Carrot `e8937726...` cut-out series, replay rows may also carry `leadCutOutTimeS` and `leadCutOutConfidence` from `RadarState.LeadData`.

The Guardian uses those fields only as descriptive context:

- `lead_cutout_predicted`
- `lead_cutout_prediction_onset`
- `lead_cutout_prediction_resolved`

A BIG/SMALL stop disagreement around a cut-out onset is raised to `PRIORITY_REVIEW` so a human can inspect whether model disagreement, radar geometry, and the new Carrot longitudinal cut-out behavior coincide.

This does **not** modify the cut-out calculation, MPC, following distance, FCW, or any control decision.

Non-finite, negative-time, out-of-range confidence, or active-cutout-with-no-lead combinations are treated as evidence-integrity issues rather than driving conclusions.

## Review buckets

The offline analyzer assigns one of four review buckets:

1. `ROOT_CAUSE` — integrity or state-machine inconsistency; fix evidence/runtime understanding first.
2. `PRIORITY_REVIEW` — meaningful BIG/SMALL disagreement in high-interest temporal context.
3. `REVIEW` — disagreement/context worth inspection but without a root-cause integrity failure.
4. `OBSERVE` — no immediate review trigger.

These are **review priority labels**, not safety PASS/FAIL states.

## Review fingerprints and queue

Each Guardian event gets a deterministic categorical fingerprint from:

- hard issues,
- review issues,
- static scene tags,
- temporal tags,
- stop disagreement state,
- review bucket.

`egpu_integrated_review_queue.py` groups repeated fingerprints so a long replay route does not produce hundreds of redundant manual-review entries. The queue stores frequency and representative frame ranges while preserving source HEAD/branch identity.

## Source identity

Offline replay rows are source-bound. Mixed `sourceHead/sourceBranch` rows are rejected. Fault and Guardian observations for the same row are also checked for frame/backend coherence.

This prevents evidence from different code revisions or different active-backend interpretations from being silently merged into one result.

## Synthetic fault injection

`egpu_integrated_fault_injection.py` is a pure-Python regression harness. Current scenarios include:

- runtime exception with same-frame fallback,
- missing same-frame fallback output,
- unexpected BIG re-entry without restart,
- SMALL-only startup without BIG hardware,
- degraded PCIe evidence while BIG remains active.

The harness touches no hardware and grants no vehicle authorization.

## Current validation boundary

GitHub CI validates the pure-Python fault/Guardian/replay layer and statically prevents runtime/control imports in those tools. This is **not hardware validation**.

Real comma/Chestnut evidence is still required before S2B/S4B commissioning can proceed. The existing commissioning ledger remains authoritative for that progression.
