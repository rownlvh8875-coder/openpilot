# Integrated BIG/SMALL Model Contracts

## Why this layer exists

Carrot already has a strong BIG-model delivery path:

- HTTPS manifest validation,
- exact model size and SHA256,
- resumable download,
- persistent cache,
- active + previous BIG retention,
- compiled BIG artifact keyed by model SHA.

The integrated branch does **not** replace that machinery.

The missing provenance question is different:

> For this exact openpilot source revision, which built-in QCOM SMALL and which eGPU BIG were reviewed as one model pair?

`egpu_integrated_model_contract.py` answers only that question.

## Baseline rule: one BIG + one SMALL

The current baseline deliberately avoids a large switchable model catalog.

A contract contains exactly:

- one built-in QCOM SMALL,
- one hashed eGPU BIG,
- one common generation number,
- one nominal model frequency,
- QCOM as the only fallback slot.

Policy is fixed:

- `oneBigOneSmallBaseline=true`
- `runtimeHotSwap=false`
- `crossGenerationMix=false`
- `fallbackSlot=qcom`
- `controlAuthorization=false`
- `publicRoadAuthorization=false`

Changing those policy values invalidates the contract.

## QCOM SMALL identity

`driving_supercombo.onnx` is stored through Git LFS. A worktree may contain either:

- the small text LFS pointer, or
- a materialized model file.

The contract therefore **does not hash the worktree file**. It reads the committed Git object with `git show HEAD:<path>` and parses the LFS pointer.

At the reviewed source revision the pointer identifies:

- SHA256: `f73a9e535523d5e9acb9e642c64e33d631825dc8ba74123757d107cedd047bb5`
- size: `60792584` bytes

This makes contract identity independent of Git-LFS smudge/materialization state.

## eGPU BIG identity

The baseline BIG identity comes from Carrot's existing `BigModelManifest` and is stored as the eGPU slot artifact.

The currently pinned Carrot TGC BIG is:

- model id: `comma-pr38739-tgc-a2e422ee-1791d594`
- SHA256: `1791d5940b2c048d0639813426dd2cf1d6f2a6727ed51e17c8bcea8bbe754123`
- size: `765950064` bytes

A future candidate can use another **local manifest JSON** for offline contract review. The contract tool does not fetch a manifest from the network.

## Source/interface binding

A model pair is bound to:

- exact Git source HEAD,
- exact source branch,
- Git blob IDs for:
  - `modeld.py`
  - `fill_model_msg.py`
  - `parse_model_outputs.py`
- a deterministic SHA256 fingerprint of those interface blobs.

The intent is to prevent a model review performed against one model interface from being silently reused after source/interface drift.

## Generation

Contract generation is an explicit integer and must match both the QCOM and eGPU slot generation exactly.

Candidate review requires:

- same source HEAD/branch,
- same interface fingerprint,
- same QCOM SMALL artifact,
- a strictly higher generation,
- a different eGPU BIG artifact.

Passing those checks yields only:

`ELIGIBLE_FOR_OFFLINE_REVIEW`

It does **not** activate the model.

## Rollback plan

A previous contract can be considered for rollback only when:

- source identity is unchanged,
- model interface fingerprint is unchanged,
- QCOM SMALL is unchanged,
- previous generation is older,
- previous eGPU BIG differs from current BIG.

The output is only:

`PLAN_ELIGIBLE`

with:

- `rollbackExecutionAuthorization=false`
- `runtimeHotSwapAuthorization=false`
- `controlAuthorization=false`
- `publicRoadAuthorization=false`

Carrot's existing active/previous BIG cache remains authoritative. This contract layer does not change it.

## Contract integrity

The contract has a deterministic SHA256 `contractId` calculated over the complete canonical payload except `contractId` itself.

The parser rejects:

- an incorrect `contractId`,
- unknown top-level fields,
- altered fixed policy,
- altered registry policy,
- incomplete/duplicate interface bindings,
- different BIG/SMALL/contract generations,
- unsafe slot/backend/fallback metadata.

The 2026-09-08 review also closes empty-ID bypasses and lossy JSON coercions:
imported IDs must be nonempty lowercase SHA256, integers cannot be booleans or
numeric strings, nested unknown/omitted fields cannot be silently normalized,
and slot source/runner values must match the existing reviewed schema. In-memory
drafts may still omit their ID until serialization. Model contract hashes prove
content identity, not trusted authorship or hardware readiness.

## CLI

`tools/egpu_integrated_model_contract.py` supports only metadata operations:

- `build`
- `validate`
- `compare-candidate`
- `rollback-plan`

It does not:

- call `ensure_big_model`,
- download models,
- compile models,
- write Carrot `state.json`,
- write Params,
- switch `modeld`,
- access AMD/QCOM hardware,
- publish cereal services.

## Commissioning relationship

A model contract is provenance evidence, not hardware evidence.

Even an offline-review-eligible candidate BIG cannot enter S2B/S4B or any later commissioning stage without the existing source-bound commissioning ledger and real comma/Chestnut evidence.
