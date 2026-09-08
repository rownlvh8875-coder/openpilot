# 오프라인 Guardian 검토 묶음

이 도구는 사용자가 준비한 paired JSONL, 모델 계약, 명시적인 연구 정책을 하나의 검토 묶음으로 저장합니다. 같은 입력 바이트와 같은 분석기로 사건 목록, 요약, 검토 순서를 다시 계산하여 묶음의 일관성을 확인할 수 있습니다. 결과는 사람이 조사할 자료이며 차량 안전 판정이나 실기기 실행 승인이 아닙니다.

**`config/egpu_integrated_review_policy.example.json`은 기존 코드 기본값을 그대로 기록한 연구용 예시입니다. 검증되거나 승인된 차량 안전 정책이 아닙니다.** 필요한 정책값은 검토자가 판단하고 전체 필드를 명시해야 합니다. 예시를 사용했더라도 결과에 안전성이나 성능 검증 의미가 생기지 않습니다.

## 이번 단계의 범위

통합 작업의 출발점은 `carrot-wip-integrated-v6`의 `cbce1250f5e58302844bfeb03c4e1de9a5a2e6b3`이며, 앞 단계에서 검토한 upstream은 `a92d3a787e84a29949ca2b802f6a78fc9b580e87`입니다. 이 값들은 과거 검토 기준입니다. 새 자료에는 자료를 만든 정확한 HEAD와 branch를 사용하고, 실행하는 분석기의 Git 정보도 별도로 묶음에 고정합니다.

2026-09-08 로컬 검증에서는 하드웨어 비의존 테스트 **214개**, 합성 fault 시나리오 **29개**, 커밋된 분석기의 실제 CLI 생성·재계산·변조 거부 검사가 통과했습니다. 변경 파일은 새 bundle 도구, 전용 테스트, 정책 schema·example, 이 문서와 CI입니다. 기존 차량 제어 경로는 수정하지 않았습니다.

최종 원격 확인 중 upstream이 `20c7bb7152aaa50998ae44867904e59a6595b761`로 바뀌었습니다. 새 Tesla engage·ACC cancel·CAN wake 변경은 14개 파일에 걸쳐 `controlsd.py`, Tesla `carstate.py`, Panda CAN/ignition 코드를 포함합니다. 이 변경은 이번 오프라인 bundle 개발 브랜치에 가져오지 않았습니다. 기준 `a92d3a`에 대한 검사는 통과했지만 새 upstream과의 실제 검사 결과는 다음과 같습니다.

| 검사 | 새 upstream에 대한 결과 |
| --- | --- |
| eGPU 통합 경계 | `CODE_EQUIVALENT_HEAD_DRIFT` |
| 기존 v6 감시 경로 | `NO_V6_PATH_DRIFT` |
| provenance | `REVIEW_REQUIRED`: `opendbc_repo/opendbc` tree 변경 |
| 전체 | `REVIEW_REQUIRED`, `overallCompatible: false`, 종료 코드 2 |

따라서 위 테스트 통과를 최신 upstream 호환성 통과로 해석하면 안 됩니다. CI는 최신 upstream 검사를 그대로 수행하며, FAIL 상태에서는 병합하지 않습니다. 다음 통합 판단에는 새 Tesla 제어·CAN 변경의 별도 검토와 호환성/회귀 검증이 필요합니다. 기존 baseline을 새 SHA로 치환하거나 변경 경로를 감시 대상에서 제외하지 않았습니다.

이 도구는 로컬 파일만 읽고 새로운 출력 디렉터리를 만듭니다. comma 연결, 원격 자료 수집, rlog 내보내기, 모델 다운로드·컴파일·실행, Params·manager·차량 제어 변경을 수행하지 않습니다. S2A 하드웨어 검증을 대신하지 않으며 S2B qualification, S4C 20 Hz runner를 추가하지 않습니다. S2B는 실제 S2A hardware PASS 이전까지 plan-only이고, S4B의 기존 readiness 조건과 parked ≤5 Hz 제한도 그대로입니다.

`synthetic`은 합성 입력이라는 표시이고, `provided_offline`은 사용자가 제공한 오프라인 입력이라는 표시입니다. 후자를 선택해도 실차 출처나 생산자 주장이 검증되지는 않습니다. 모델 계약은 선언된 구성만 나타내며, 행의 `qcom`·`egpu` 같은 backend 표시는 해당 모델이 실제로 실행되었다는 증거가 아닙니다. hardware, control, public-road, commissioning 승인과 `modelExecutionVerified`, `producerAssertionsVerified`는 모두 false로 유지합니다.

## 준비할 파일

| 입력 | 요구사항 |
| --- | --- |
| paired rows JSONL | `config/egpu_integrated_guardian_replay_row.schema.json`에 대응하는 행. 모든 행의 `sourceHead`와 `sourceBranch`가 외부에서 지정한 기대값과 같아야 합니다. |
| 모델 계약 JSON | 기존 `tools/egpu_integrated_model_contract.py`로 작성·검토한 계약. 계약의 source도 같은 기대값에 묶여야 합니다. |
| 정책 JSON | `config/egpu_integrated_review_policy.schema.json`에 대응하는 전체 정책. `schemaVersion: 1`, `guardian`, `temporal` 외의 최상위 필드는 허용하지 않습니다. |

입력에는 필요한 자료만 포함하고 로컬에 보관합니다. 이 도구는 원본 자료나 생성 결과를 GitHub에 업로드하지 않습니다.

정책의 `guardian`에는 `GuardianPolicy`, `temporal`에는 `TemporalHeuristicPolicy`의 모든 필드를 적습니다. 생략된 필드를 기본값으로 채우지 않으며 알 수 없는 필드와 잘못된 자료형을 거부합니다. 입력 JSON·JSONL 전체에서 중복 키, NaN·Infinity, 숫자 overflow도 거부합니다. 비유한값에 대한 Guardian 진단은 기존 합성 fault injection으로 별도 확인하며, 그런 값을 검토 묶음에 그대로 넣지 않습니다. `null`을 허용하는 정책 항목은 `max_execution_ms`, `max_hardware_age_s`, `max_action_timestamp_skew_s` 세 개입니다. 해당 항목의 `null`은 그 선택적 검사를 설정하지 않았다는 뜻입니다.

기존 정책의 양수·음수·정수 제약을 그대로 사용합니다. `close_acquisition_m >= close_lead_m`과 `0 < standstill_speed_mps < creep_speed_mps`의 관계도 실행 시 검사합니다. 표준 JSON Schema로 표현하지 못하는 필드 간 숫자 비교는 기존 정책 validator가 담당합니다. 새 차량 안전 임계값을 정하지 않습니다.

분석 관련 소스에 미커밋 변경이 있으면 묶음을 만들지 않습니다. 변경 사항을 검토하여 커밋하고, 그 분석기 커밋에서 실행해야 합니다. 분석기는 로컬 의존성과 package initializer를 포함한 13개의 Python 소스를 추적하며, `sourceNormalization: utf8-lf`에 따라 CRLF를 LF로 정규화한 소스가 커밋된 내용과 일치하는지 확인합니다. 실제 import된 모듈의 위치도 확인하며 import 이후 파일이 바뀌면 새 프로세스로 다시 실행해야 합니다. 기록된 분석기 HEAD가 실제 Git commit이고 해당 커밋의 소스 hash가 일치하는지도 검증합니다. 이 정규화는 분석기 Python 소스의 운영체제별 개행 차이를 처리합니다. 입력·결과 파일은 정규화하지 않고 정확한 바이트를 해시합니다. 입력 자료의 source와 분석기의 source는 서로 다른 역할이며, 분석기 HEAD를 입력 행에 복사해서 원래 출처를 바꾸면 안 됩니다.

## 만들기

저장소 루트에서 실행합니다. 아래 `<...>`는 설명용 자리표시자이므로 실제 경로, 40자리 source SHA, branch로 바꾸어야 합니다. 출력 경로는 아직 없는 새 디렉터리여야 합니다.

```text
python tools/egpu_integrated_review_bundle.py build --rows "<paired-rows.jsonl>" --contract "<model-contract.json>" --policy "<review-policy.json>" --output "<new-bundle-directory>" --expected-source-head "<40-character-source-sha>" --expected-source-branch "<source-branch>" --evidence-kind provided_offline
```

합성 자료로 도구 동작을 확인할 때는 같은 명령의 마지막 값을 `--evidence-kind synthetic`으로 지정합니다. 합성 자료에 실제 주행 자료라는 표지를 붙이지 않습니다.

출력 디렉터리가 이미 있으면 덮어쓰지 않습니다. 기존 결과를 그대로 두고 다른 새 디렉터리를 지정합니다. 입력·계약·정책 검사에 실패한 경우 원본을 확인하고 수정한 별도 입력으로 다시 실행합니다. 실패를 피하려고 source SHA나 branch를 임의로 맞추지 않습니다.

파일은 새 디렉터리에 배타적으로 생성하고 manifest를 마지막에 기록합니다. 저장 도중 오류가 발생하면 미완성 디렉터리를 남기며, 불완전한 파일 집합은 검증에서 거부합니다. 기존 출력이나 다른 파일을 자동 삭제하지 않습니다.

## 생성되는 파일과 외부 ID 보관

| 파일 | 내용 |
| --- | --- |
| `rows.jsonl` | 입력 행 파일의 정확한 바이트 |
| `model-contract.json` | 입력 모델 계약의 정확한 바이트 |
| `policy.json` | 명시적인 입력 정책의 정확한 바이트 |
| `events.jsonl` | 정책을 적용해 다시 계산한 Guardian·fault 사건 |
| `summary.json` | 입력 행 수, 태그·검토 구간별 집계 |
| `review-queue.json` | 사람이 확인할 사건 묶음과 대표 프레임. `OBSERVE`도 포함하여 모든 사건을 보존 |
| `manifest.json` | 파일 바이트 해시, source·분석기 정보, 묶음 ID와 승인 제한 |

묶음 ID는 동일한 입력과 동일한 분석 조건에서 결정적으로 계산됩니다. 원본 파일의 공백이나 개행도 바이트 해시에 포함되므로, JSON 의미가 같아도 파일을 다시 저장하면 ID가 달라질 수 있습니다.

묶음의 manifest에는 큐 생성 조건 `includeObserve: true`가 고정됩니다. 검토 큐에 `OBSERVE`를 포함하므로 낮은 검토 우선순위의 사건도 누락하지 않습니다. 이 조건은 사용자가 제공하는 Guardian·temporal 정책과 별개의 묶음 생성 조건입니다.

**만든 시점의 묶음 ID를 묶음 디렉터리 밖의 신뢰할 수 있는 검토 기록에 따로 보관합니다.** 기대 source SHA와 branch도 함께 기록합니다. 다음 검증 명령에는 그 외부 기록의 ID를 입력해야 합니다. 검증 직전에 검증 대상 `manifest.json`에서 ID를 읽어 기대값으로 넣으면, 누군가 전체 묶음과 ID를 함께 바꿨을 때 원래 묶음임을 확인할 수 없습니다.

해시는 전자서명이나 생산자 인증이 아닙니다. 외부 기대 ID는 특정 바이트 묶음을 식별하는 기준이며, 자료의 진위나 실제 하드웨어 동작을 증명하지 않습니다.

## 다시 검증하기

검증에 사용하는 분석기의 `sourceFingerprint`가 묶음에 고정된 값과 같아야 합니다. 추적하는 분석기 소스가 같다면 다른 Git HEAD에서도 검증할 수 있으며, 실제 검증기 HEAD는 별도로 보고됩니다. 외부에 보관한 ID와 source 정보를 사용합니다.

```text
python tools/egpu_integrated_review_bundle.py verify --bundle "<bundle-directory>" --expected-bundle-id "<externally-recorded-64-character-bundle-id>" --expected-source-head "<40-character-source-sha>" --expected-source-branch "<source-branch>"
```

검증은 저장된 해시만 읽어 신뢰하지 않습니다. 입력과 결과의 실제 바이트를 확인하고, 정책·계약·source·분석기 정보를 검사한 다음 사건, 요약, 검토 순서를 다시 계산하여 저장된 결과와 비교합니다. 검증 성공은 이 오프라인 묶음의 일관성과 재계산 결과가 맞았다는 의미입니다. 실기기 검증이나 모델 성능 검증 결과로 사용할 수 없습니다.

분석기가 변경되었다면 먼저 변경 내용을 검토합니다. 이전 결과를 덮어쓰거나 manifest를 편집하지 말고, 필요한 경우 원본 입력으로 새 묶음을 만든 뒤 새 ID를 별도로 기록하고 결과 차이를 검토합니다.

## 검토 순서와 해석

1. 입력 종류, source HEAD·branch, 모델 계약, 사용한 정책이 검토하려는 자료에 맞는지 확인합니다.
2. 외부 ID를 이용한 `verify`가 성공했는지 확인합니다.
3. `summary.json`에서 누락·불일치·stale·nonfinite 증거와 시간 연속성 문제를 확인합니다.
4. `review-queue.json`의 우선순위를 따라 `events.jsonl`과 원본 입력을 함께 읽습니다.
5. 물리적 원인이나 CUT-IN·CUT-OUT 장면을 판단해야 한다면, 별도로 확보하고 검증한 영상·레이더·실기기 증거와 대조합니다.

`ROOT_CAUSE`는 증거나 상태 불일치에 대한 조사 우선순위이며 물리적 원인이 확정되었다는 뜻이 아닙니다. 큐의 개수는 입력 사건 개수이고, 서로 독립적인 사고 횟수가 아닙니다. `OBSERVE`도 안전 PASS가 아닙니다.

`sameFrameOutputPreserved`는 행에 선언된 프레임·나이·유한값 증거의 일관성을 나타냅니다. SMALL latch나 상태 전이 오류가 동시에 존재할 수 있습니다. `processId`와 `restartBoundary`는 입력 생산자의 선언이며 실제 프로세스 재시작을 검증하지 않습니다. frame/time gap을 넘어 발생 시점이나 해제 시점을 추론하지 않습니다.

이 오프라인 검토가 끝나도 실기기 단계에는 별도 명시적인 작업 지시와 적용할 HEAD·branch에 묶인 새 evidence가 필요합니다.
