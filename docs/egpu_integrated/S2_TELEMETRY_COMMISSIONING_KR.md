# S2 Telemetry Commissioning

S2의 목적은 eGPU 하드웨어 telemetry가 active Carrot/eGPU 주행모델에 주는 간섭과 telemetry 자체의 신뢰성을 분리해서 검증하는 것이다.

## S2A — telemetry-only 간섭시험

S1 observer qualification이 PASS해야 계획을 만들 수 있다. S2A에서는 observer와 shadow를 모두 끄고 telemetry만 OFF/ON/OFF로 바꾼다.

```text
S2A_OFF_BEFORE
  observer OFF / telemetry OFF / shadow OFF
      ↓ full reboot
S2A_TELEMETRY_ON
  observer OFF / telemetry ON / shadow OFF
      ↓ full reboot
S2A_OFF_AFTER
  observer OFF / telemetry OFF / shadow OFF
```

모든 leg는 P단·완전정차·lat/long controls inactive다. 계획과 recorder는 marker/reboot를 직접 수행하지 않는다.

## S2A Evidence

active model 측정:

- modelExecutionTime p50/p95/p99/max
- frame gaps
- eGPU fallback
- stationary/control guard violation

telemetry ON leg 측정:

- `hardware.json` freshness
- hardware sample count / valid sample count
- telemetry error sample count
- supply fault sample count
- hardware sample duration p95/max
- max GPU temp
- max memory temp
- max power draw
- 원본 JSONL에는 power limit, usage, clock, fan, supply V/I, USB speed/link error, PCIe LTSSM도 함께 남긴다.

## S2A Qualification

threshold는 코드에 들어있지 않다. `config/egpu_integrated_s2a_policy.schema.json`의 모든 필드를 사용자가 explicit policy로 제공해야 한다.

온도·메모리온도·전력 limit은 반드시 필드 자체는 존재해야 하지만 `null`을 명시하면 해당 항목을 acceptance gate에서 제외한다. 즉 비-gating도 숨은 기본값이 아니다.

판정 원칙:

- policy 없음 → HOLD
- model/hardware sample 부족 → HOLD
- OFF-before/OFF-after baseline drift → HOLD
- telemetry freshness 실패 → FAIL
- hardware valid fraction 부족 → FAIL
- telemetry error/supply fault/sample-duration policy 초과 → FAIL
- 명시한 thermal/power limit 초과 → FAIL
- thermal/power gate를 요청했는데 해당 evidence 없음 → HOLD
- baseline이 안정된 경우에만 telemetry ON latency/frame-gap 증가를 attribution FAIL로 판정

PASS해도 public-road/control/shadow authorization은 모두 false다.

## S2B — observer + telemetry 공존성

S1 PASS와 S2A PASS가 모두 있어야 `S2B_OBSERVER_TELEMETRY_COEXISTENCE_PLAN_ONLY`가 열린다.

```text
observer ON + telemetry OFF
        ↓
observer ON + telemetry ON
        ↓
observer ON + telemetry OFF
```

이렇게 해야 이미 S1에서 검증된 observer stack 위에 telemetry를 추가했을 때의 incremental effect를 다시 분리할 수 있다.

현재는 **S2B plan만 구현**하고 recorder/qualification은 의도적으로 보류한다. 실제 S2A 장비 evidence가 PASS하기 전에는 S2B 실행도구를 만들지 않는다.
