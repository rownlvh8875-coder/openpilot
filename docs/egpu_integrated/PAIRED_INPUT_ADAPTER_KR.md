# BIG/SMALL paired 오프라인 입력 어댑터

## 목적

실제 BIG/SMALL 결과가 확보되면 `EGPU-Future` 계열 extractor 출력에서 현재 `carrot-wip-integrated-v6` Guardian review row로 연결한다.

이 단계는 **오프라인 증거 변환**이다. 모델을 실행하지 않고 comma, Params, manager, CAN, vehicle control을 변경하지 않는다.

현재 2026-09-08 검색 범위에서는 실제 paired JSONL을 찾지 못했다. 따라서 이 기능의 검증은 합성 입력에 한정되며 실제 모델 성능이나 차량 검증을 의미하지 않는다.

## 설계 원칙

- SMALL/BIG backend를 파일명이나 `modelV2.big`만 보고 추정하지 않는다.
- producer가 `--backend-label small` / `--backend-label big`로 명시한 extractor 출력만 받는다.
- pairing은 동일 `frameId`만 허용한다.
- timestamp fallback은 금지한다.
- 동일 frame의 scene/time evidence가 다르면 같은 카메라 입력으로 보지 않고 HOLD한다.
- 입력 SHA256/size, source HEAD/branch, model contract ID를 모두 확인한다.
- 중복/역순 frameId, NaN/Infinity, 숫자 문자열, 누락 필드, unknown field를 거부한다.
- `sameCameraInputId`는 외부 producer의 명시적 주장이지 검증된 사실이 아니다.
- `producerAssertionsVerified=false`, `modelExecutionVerified=false`를 유지한다.
- control/public-road authorization은 항상 false다.

## 관련 파일

```text
tools/egpu_integrated_paired_input_adapter.py
config/egpu_integrated_paired_input_provenance.schema.json
config/egpu_integrated_guardian_replay_row.schema.json
tools/egpu_integrated_review_bundle.py
```

## 입력 준비

원본 producer 단계에서 동일 카메라 입력에 대해 SMALL과 BIG 결과를 각각 만든다. 권장 extractor는 별도 검토한 `EGPU-Future/tools/extract_model_actions_from_log.py`다.

예시 개념:

```text
SMALL run -> --backend-label small -> small.jsonl
BIG run   -> --backend-label big   -> big.jsonl
```

두 JSONL은 서로 다른 모델 출력값을 가질 수 있지만 같은 `frameId`의 다음 scene evidence는 정확히 같아야 한다.

```text
frameIdExtra
logMonoTimeNs
speedMps
vehicleAccelMps2
standstill
leadPresent / leadDistanceM / leadRelSpeedMps
leadModelProb / leadRadarMatched
```

producer source 파일 자체도 보관한다. 어댑터는 그 파일의 SHA256/size를 provenance 선언과 비교한다. producer Git HEAD 문자열은 기록하지만 네트워크에서 해당 저장소를 자동 신뢰하거나 fetch하지 않는다.

모델 계약은 해당 자료를 만든 실제 source HEAD/branch와 SMALL/BIG model ID를 고정해야 한다. 현재 분석기 HEAD를 과거 producer source에 대신 넣으면 안 된다.

## provenance 선언

`config/egpu_integrated_paired_input_provenance.schema.json` 형식을 사용한다. 핵심 필드는 다음과 같다.

```text
source.head / source.branch
producer.repository / producer.head
producer.extractorSha256 / extractorSize
producer.sameCameraInputId
inputs.small/big sha256 / size / sourceLabel / modelId / backend
modelContractId
mapping.activeInput / shadowInput
pairing.method = frameId
pairing.timestampFallback = false
pairing.sameCameraInputAsserted = true
```

## 어댑터 실행

저장소 루트에서 아직 존재하지 않는 새 출력 디렉터리를 지정한다.

```text
python tools/egpu_integrated_paired_input_adapter.py build \
  --small <small.jsonl> \
  --big <big.jsonl> \
  --producer-source <extract_model_actions_from_log.py> \
  --provenance <paired-source-provenance.json> \
model-contract.json
  --contract <paired-evidence-directory>/model-contract.json \
  --output <new-paired-evidence-directory> \
  --expected-source-head <40-char-source-sha> \
  --expected-source-branch <source-branch>
```

성공한 출력은 self-contained evidence directory다.

```text
small-input.jsonl
big-input.jsonl
producer-extractor.py
paired-source-provenance.json
model-contract.json
rows.jsonl
input-provenance.json
```

`rows.jsonl`은 원본 SMALL/BIG에서 다시 계산할 수 있어야 한다. `input-provenance.json`은 입력 파일 hash/size, model contract ID, pairing 수량, adapter Git source identity, 출력 rows hash를 묶는다.

검증은 같은 디렉터리의 원본부터 다시 계산한다.

```text
python tools/egpu_integrated_paired_input_adapter.py verify \
  --output <paired-evidence-directory> \
  --expected-source-head <40-char-source-sha> \
  --expected-source-branch <source-branch>
```

출력 디렉터리에 파일이 하나라도 빠지거나 추가되면 거부한다. 기존 디렉터리는 덮어쓰지 않는다.

## Guardian review bundle 연결

실제 SMALL/BIG 증거는 일반 `provided_offline` 대신 `provided_paired_offline`으로 묶는다.

```text
python tools/egpu_integrated_review_bundle.py build \
  --rows <paired-evidence-directory>/rows.jsonl \
  --paired-evidence-dir <paired-evidence-directory> \
  --contract <paired-evidence-directory>/model-contract.json \
  --policy <review-policy.json> \
  --output <new-review-bundle-directory> \
  --expected-source-head <40-char-source-sha> \
  --expected-source-branch <source-branch> \
  --evidence-kind provided_paired_offline
```

이 경우 review bundle은 변환된 `rows.jsonl`뿐 아니라 SMALL/BIG 원본, producer extractor, provenance 선언, adapter receipt도 함께 복사한다.

검증 시 다음 순서로 다시 확인한다.

1. 모든 bundle member의 exact byte hash/size 확인
2. model contract source와 LFS pointer binding 확인
3. producer source와 SMALL/BIG input hash 확인
4. SMALL/BIG extractor row의 strict type/field/backend 검사
5. 동일 `frameId` 1:1 pairing 재계산
6. scene/time evidence 동일성 검사
7. integrated `rows.jsonl` exact byte 재계산
8. adapter receipt 재계산
9. Guardian/fault events, summary, review queue 재계산
10. 외부 보관 bundle ID와 최종 manifest 비교

따라서 `rows.jsonl`이나 receipt만 다시 해시해 바꿔치기하는 것으로 검증을 통과할 수 없다.

## 해석 제한

이 구조가 증명하는 것은 **제공된 파일들 사이의 재현 가능한 오프라인 일관성**이다. 다음은 증명하지 않는다.

- SMALL/BIG 모델이 실제 차량에서 실행됐다는 사실
- `sameCameraInputId` producer 주장의 진위
- eGPU hardware 상태나 모델 품질
- comma commissioning PASS
- 차량 안전성
- public-road 실행 승인
- steering/braking/longitudinal control 변경 승인

실제 paired 입력이 확보되기 전에는 합성 테스트 PASS만 기록하며 성능 우열이나 차량 적용 결론을 내리지 않는다.

## 병합 후 재검증

receipt에는 생성 당시 adapter Git HEAD/branch와 source SHA256을 기록한다. 이후 개발 브랜치가 통합 브랜치에 병합되어 HEAD/branch가 달라져도 **adapter 소스 바이트 SHA256이 동일하면** 기록된 생성 commit을 검증한 뒤 재계산할 수 있다.

현재 verifier의 adapter 소스 SHA256이 기록된 값과 다르면 새 분석기 검토가 필요하며 기존 receipt를 그대로 재계산하지 않는다. HEAD가 같다는 이유만으로 변경된 소스를 허용하지 않는다.
