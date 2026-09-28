# Carrot-WIP Integrated eGPU Branch

기준일: 2026-09-07

## 브랜치

- 일상/복구 기준선: `carrot-wip`
- eGPU 통합 개발: `carrot-wip-integrated-v0`
- reviewed upstream base: `6f4c00e625dc3d41d3776427a272a5c3fed75e6c`
- reviewed original `modeld.py` blob: `e4de3eb2236f6bb0c54c87666099147311602f4f`

`carrot-wip`는 통합 실험을 위해 수정하지 않는다. 문제가 생기면 이 브랜치로 돌아가는 것이 1차 rollback이다.

## 현재 통합 범위

### S1 Observer / provenance

기본 OFF.

활성화:

```bash
export EGPU_INTEGRATED_OBSERVER=1
```

또는:

```text
/data/egpu_integrated/observer_enabled
```

출력:

```text
/data/egpu_integrated/state.json
```

기록 항목은 active/attempted backend, frame age, model latency, eGPU->QCOM runtime fallback 횟수/사유다.

### S2 Read-only hardware telemetry

기본 OFF.

활성화:

```bash
export EGPU_INTEGRATED_TELEMETRY=1
```

또는:

```text
/data/egpu_integrated/telemetry_enabled
```

출력:

```text
/data/egpu_integrated/hardware.json
```

수집 대상은 AMD SMU temperature/power/PPT readback/utilization/clock/fan, supply V/I/fault, USB speed/link errors/firmware, PCIe LTSSM이다.

이 계층은 read-only다. PPT/fan/PCIe/USB 상태를 변경하지 않는다.

### S3 Model slots

`qcom`과 `egpu` 모델 metadata slot을 분리한다.

현재 정책:

```text
fallbackSlot = qcom
runtimeHotSwap = false
crossSlotFallback = false
controlAuthorization = false
```

Carrot의 실제 big-model download/compile/startup 경로는 아직 authoritative하다.

### S4A Guardian

active와 shadow evidence의 frame identity/freshness/nonfinite/latency/disagreement/hardware fault를 평가한다.

항상:

```text
controlAuthorization = false
shadowPublishToControls = false
```

이다.

### S4B Parked QCOM shadow load probe

이 단계는 **모델 품질 비교가 아니라 active eGPU path interference 측정**이다.

최대 5 Hz이며 sender/receiver 모두 다음 조건을 요구한다.

```text
gear = P
standstill = true
abs(vEgo) < 0.01 m/s
latActive = false
longActive = false
```

수동 probe:

```bash
python tools/egpu_integrated_s4b_shadow_probe.py --max-hz 5 --duration 60
```

shadow tap은 기본 OFF다. 정차 commissioning에서만 다음 control file로 활성화한다.

```bash
touch /tmp/egpu_integrated_shadow_tap.enable
```

시험 종료 후:

```bash
rm -f /tmp/egpu_integrated_shadow_tap.enable
```

S4B 결과는 항상:

```text
shadowOnly = true
controlEligible = false
qualityComparisonEligible = false
```

이다.

5 Hz 결과로 big/small 주행 품질 우열을 판단하지 않는다. temporal hidden-state history가 20 Hz active model과 다르기 때문이다.

## 현재 보존되는 Carrot 경로

다음은 기존 Carrot-WIP를 그대로 유지한다.

- eGPU primary model selection
- startup USB grace
- PCIe retry
- internal QCOM warm fallback
- eGPU runtime failure 시 동일 camera frame QCOM 재실행
- `modelV2` / `drivingModelData` / `cameraOdometry` publish
- DesireHelper
- controls / car interface
- panda safety
- vehicle actuator limits

`modeld.py` 통합 commit은 marker 49줄 추가, 삭제 0줄이며 marker를 제거하면 reviewed original modeld Git blob과 일치하도록 CI에서 검증한다.

## 실기기 도입 순서

1. 현재 `carrot-wip` baseline 유지/기록
2. `carrot-wip-integrated-v0` 설치하되 observer/telemetry/shadow 모두 OFF 상태로 boot 검증
3. S1 observer만 활성화하고 기존 주행경로 상태 확인
4. S2 telemetry를 활성화하고 GPU/USB/power/PCIe 실측
5. 정차/P단에서 receiver-only 확인
6. 정차/P단에서 S4B QCOM shadow <=5 Hz 실행
7. active eGPU latency before/during/after 비교
8. QCOM/tinygrad profile 및 thermal/power evidence 결합
9. explicit policy로 S4B qualification PASS/HOLD/FAIL
10. 실제 PASS일 때만 S4C parked 20 Hz 계획으로 진행

## 현재 금지사항

- S4B public-road 실행
- shadow 결과의 `modelV2` publish
- shadow 결과의 steering/braking 사용
- automatic PPT 변경
- automatic model hot-swap
- panda/actuator limit 완화
- S4B 실측 PASS 전 20 Hz shadow runner 도입

## Rollback

가장 단순한 rollback은 통합 브랜치 대신 원본 기준 브랜치로 복귀하는 것이다.

```bash
git switch carrot-wip
```

현재 fork의 `carrot-wip`는 upstream reviewed SHA `6f4c00e625dc3d41d3776427a272a5c3fed75e6c`를 유지한다.

실기기 설치 전에 항상 현재 기기의 정상 복귀 절차와 저장된 설정/모델 상태를 확인한다.
