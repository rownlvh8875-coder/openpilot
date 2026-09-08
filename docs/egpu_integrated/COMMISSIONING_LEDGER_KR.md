# Integrated eGPU Commissioning Ledger

이 문서는 실제 comma의 출퇴근 데이터 수집 경로와 분리된 `carrot-wip-integrated-v6` 연구 commissioning evidence chain을 설명한다.

## 목적

각 단계가 같은 source HEAD에서 수행됐는지, 어떤 evidence와 policy로 PASS가 판정됐는지, 다음 단계에 필요한 evidence가 실제로 존재하는지를 하나의 ledger로 추적한다.

## Evidence chain

```text
POSTBOOT_ALL_FEATURES_OFF PASS
        ↓
S1_OBSERVER_QUALIFICATION PASS binding
        ↓
S2A_TELEMETRY_ONLY_QUALIFICATION PASS binding
        ↓
S2B_OBSERVER_TELEMETRY_COEXISTENCE_QUALIFICATION PASS binding
        ↓
READY_S4B_REVIEW
```

각 qualification binding은 evidence / policy / qualification 파일 SHA256과 exact recomputation 결과를 포함한다.

## 현재 의도적 HOLD

현재 S2B는 plan-only이다. 실제 S2A comma hardware evidence가 아직 없으므로 S2B recorder와 qualification은 구현하지 않는다.

따라서 실제 evidence chain은 정상적으로 `HOLD_S2B_BINDING_REQUIRED`에 머물러야 한다. synthetic test에서만 미래의 S2B PASS binding을 만들어 S4B gate 자체의 논리를 검증한다.

## S4B readiness

ledger가 `READY_S4B_REVIEW`여도 shadow inference 실행 권한은 아니다.

`egpu_integrated_s4b_readiness.py`가 한 번 더 source identity, stage chain, missing requirements, authorization boundary를 검사한다. PASS해도 열리는 것은 `S4B_PARKED_SHADOW_LOAD_PROBE_PLAN_ONLY`뿐이다.

## 변하지 않는 안전 경계

- 실제 `/data/openpilot` 자동 변경 없음
- branch switch/reset 없음
- marker 자동 변경 없음
- Params write 없음
- reboot 자동 실행 없음
- public-road authorization 없음
- shadow execution authorization 없음
- controlAuthorization=false

실차 commissioning은 현재 진행 중인 출퇴근 데이터 프로젝트와 분리해서 명시적으로 시작하기 전까지 실행하지 않는다.
