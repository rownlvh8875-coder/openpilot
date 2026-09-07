# S1 Observer Commissioning

S1의 목적은 **observer 자체가 active Carrot/eGPU 주행모델에 측정 가능한 간섭을 만드는지** 확인하는 것이다. S1에서는 telemetry와 shadow inference를 켜지 않는다.

## 전제

- post-boot verification = PASS
- expected integrated HEAD 일치
- P단 / 완전 정차
- lateral / longitudinal controls inactive
- telemetry marker OFF
- shadow marker OFF
- public-road 실행 금지

## 실험 순서

```text
S1_OFF_BEFORE
  observer OFF
  telemetry OFF
  shadow OFF
      ↓ full reboot
S1_ON
  observer ON
  telemetry OFF
  shadow OFF
      ↓ full reboot
S1_OFF_AFTER
  observer OFF
  telemetry OFF
  shadow OFF
```

observer enable 상태는 `modeld`에서 `EgpuIntegrationObserver`가 생성될 때 결정되므로 각 leg 사이에는 full reboot를 사용한다. plan generator는 reboot를 실행하거나 승인하지 않는다.

## Evidence

각 leg마다 최소한 다음 값을 기록한다.

- sample count
- model execution p50 / p95 / p99 / max
- frame gap count
- eGPU fallback count
- stationary/control guard violation count
- observer write error count

형식은 `config/egpu_integrated_s1_evidence.schema.json`에 정의한다.

## Qualification

`tools/egpu_integrated_s1_qualification.py`는 임의 threshold를 갖지 않는다. 별도의 explicit policy가 필요하며 형식은 `config/egpu_integrated_s1_policy.schema.json`을 따른다.

판정 원칙:

- policy 없음 → HOLD
- sample 부족 → HOLD
- OFF-before / OFF-after baseline drift 초과 → HOLD
- guard violation / fallback / observer write error 초과 → FAIL
- baseline이 안정된 상태에서 observer latency/frame-gap 증가가 명시 기준 초과 → FAIL
- 모든 조건 만족 → PASS

baseline이 불안정한 상태에서는 observer latency 차이를 원인으로 단정하지 않는다. 다만 guard violation, fallback, observer write error처럼 attribution과 무관하게 명확한 operational fault는 FAIL로 유지한다.

## PASS가 의미하는 것

S1 PASS는 observer가 해당 정차 commissioning 조건에서 허용된 연구 기준 내에 있었다는 뜻뿐이다.

PASS 이후에도:

```text
controlAuthorization = false
publicRoadAuthorization = false
shadowAuthorization = false
```

이며 다음으로 열리는 것은 `S2_TELEMETRY_PLAN_ONLY`뿐이다.
