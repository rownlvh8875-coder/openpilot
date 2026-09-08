# S4B Shadow Probe Entry Gate

S4B는 주행 품질 비교가 아니라 정차 상태에서 두 번째 QCOM inference workload가 active eGPU driving path에 간섭하는지 보는 최대 5 Hz load probe다.

## 추가 진입조건

`egpu_integrated_s4b_shadow_probe.py`는 이제 다음 두 조건을 카메라/QCOM 초기화 전에 모두 검증한다.

1. `S4B_REVIEW_READINESS` artifact가 PASS이고 `S4B_PARKED_SHADOW_LOAD_PROBE_PLAN_ONLY` gate를 가리킬 것
2. 실행 중 checkout의 branch/head가 readiness와 CLI `--expected-head/--expected-branch`와 정확히 일치할 것

readiness artifact에는 다음 authorization이 모두 false여야 한다.

- shadowExecutionAuthorization
- observerEnableAuthorization
- telemetryEnableAuthorization
- rebootAuthorization
- publicRoadAuthorization
- controlAuthorization

하나라도 true이면 probe는 시작하지 않는다.

## Artifact provenance

실제 실행 시 startup JSONL event에 다음을 남긴다.

- sourceHead
- sourceBranch
- S4B readiness file SHA256

따라서 나중에 어떤 readiness evidence를 근거로 어떤 source에서 load probe가 실행됐는지 재현할 수 있다.

## 변하지 않는 제한

- 최대 5 Hz
- P단·정차·lat/long controls inactive
- active backend가 eGPU가 아니면 inference하지 않음
- manager auto-start 금지
- cereal/modelV2/control publish 없음
- 결과는 model-quality 비교에 사용하지 않음
- public-road authorization 없음
- control authorization 없음

현재는 S2B 실기기 evidence가 없으므로 정상 commissioning ledger에서는 S4B readiness가 PASS할 수 없다. 이 gate는 미래 실행 전 안전장치로 미리 준비된 것이다.
