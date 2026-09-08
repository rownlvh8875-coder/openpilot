# carrot-wip-integrated-v6 최신 인계 — 2026-09-08 KST

> **브랜치 구분:** 이 인계 문서는 `dev-integrated-handoff-20260908`에만 보관한다. 실제 차량/commissioning의 고정 source는 `carrot-wip-integrated-v6 @ 7c70355361c929142b520e41412a7965d27bb60e`이다. handoff 브랜치의 문서 커밋 SHA를 차량 expected HEAD로 사용하지 않는다.

## 현재 Git 기준점

```text
branch = carrot-wip-integrated-v6
HEAD = 7c70355361c929142b520e41412a7965d27bb60e
merge = PR #17: distinguish unverified paired evidence discovery
working tree = clean
index = clean
```

오늘 병합 완료:

- PR #15 `29a40d3b`: source-bound paired offline input adapter
- PR #16 `89cd6bcd`: read-only paired evidence discovery
- PR #17 `7c703553`: unverified/invalid discovery 상태 분리

병합 후 `egpu-integrated-ci` #126와 `user docs` #45 모두 SUCCESS이다.

## 검증 완료 상태

최종 로컬 검증:

```text
hardware-free integrated unittest = 260/260 PASS
paired evidence discovery focused = 9/9 PASS
synthetic fault suite = 29/29 PASS
route viewer = 18/18 PASS
radar continuity/graph = 15/15 PASS
browser logic = 6/6 PASS
modeld reviewed-source restore = PASS
```

upstream `ajouatom/openpilot:carrot-wip` 기준은 `b7ab68addc29c3b9dc8f02023de36dba7109ed8c`이며 source compatibility는 `REVIEWED_WITH_EXCLUSIONS`, `overallCompatible=true`, integration/v6/provenance 모두 `EXACT_REVIEWED`이다.

control / opendbc / panda / modeld runtime / manager / launcher 제어 경로 변경은 없다.

## 오늘 실제 evidence 탐색 결과

최종 discovery report는 schemaVersion 2 기준 `NO_PAIRED_EVIDENCE_FOUND`이다.

접근 가능 범위:

```text
Codex current outputs: 31 files
C:\Users\hca2240095\Downloads: 0 files
D:\Data\Downloads: 961 files
D:\codex: 26,275 files
```

위 범위에서 verified / invalid / unverified / loose BIG-SMALL paired evidence, shadow output, raw rlog/qlog 후보는 모두 0건이었다. 이 결과는 접근한 범위만 의미하며 다른 저장장치의 부재를 뜻하지 않는다.

## 회사 PC에서 막힌 외부 evidence 접근

2026-09-08 회사망에서는 다음 경로가 접근되지 않았다.

```text
\\DS1821P\openpilot SMB = name resolution / mount unavailable
https://upload.shind0.synology.me = company security block page
comma SSH 10.108.18.70:22 = unreachable
comma SSH 192.168.219.107:22 = unreachable
W: vehicle settings drive = not mapped
```

따라서 NAS가 오프라인이거나 comma가 고장났다고 판단하면 안 된다. 회사 보안망/네트워크 경계 때문에 외부 evidence source를 확인할 수 없었던 것이다.

집 또는 홈 NAS가 보이는 네트워크에서 discovery를 다시 실행하는 것이 다음 우선순위다.

## 집에서 첫 번째로 할 일 — Git 기준점 동기화

집 PC의 openpilot 작업사본에서 먼저 다음을 수행한다.

```text
git fetch origin
git switch carrot-wip-integrated-v6
git pull --ff-only origin carrot-wip-integrated-v6
git rev-parse HEAD
```

기대 HEAD:

```text
7c70355361c929142b520e41412a7965d27bb60e
```

HEAD가 다르면 그 상태에서 차량/NAS evidence를 새 source의 것으로 간주하지 않는다. 먼저 remote 상태와 branch history를 확인한다.

## 집에서 두 번째로 할 일 — 홈 NAS evidence discovery

Windows Python에서 저장소 루트를 현재 디렉터리로 두고 실행한다.

```text
py -3.14 tools\egpu_integrated_paired_evidence_discovery.py ^
  --root "\\DS1821P\openpilot" ^
  --expected-source-head 7c70355361c929142b520e41412a7965d27bb60e ^
  --expected-source-branch carrot-wip-integrated-v6 ^
  --max-depth 8 ^
  --max-files 250000 ^
  --no-verify-paired ^
  --output paired-evidence-discovery-home-fast.json
```

`truncated=true`이면 해당 결과로 부재를 결론내리지 말고 범위를 나누거나 `--max-files`를 높인다.

fast discovery 판정별 후속:

- `PAIRED_EVIDENCE_FOUND_NOT_VERIFIED` → 해당 candidate parent만 다시 **검증 모드**로 실행
- `VERIFIED_PAIRED_EVIDENCE_FOUND` → `provided_paired_offline` review bundle 생성 단계로 이동
- `LOOSE_PAIRED_CANDIDATE_FOUND` → 원본 source/model/input provenance부터 복구
- `SHADOW_OUTPUT_ONLY_FOUND` → matching active output과 동일 input provenance 탐색
- `RAW_ROUTE_ONLY_FOUND` → rlog/qlog만으로 same-input BIG/SMALL 결과를 만들었다고 주장하지 않음
- `NO_PAIRED_EVIDENCE_FOUND` → 실제 장비 commissioning을 통한 새 증거 수집 준비

검증 모드에서는 `--no-verify-paired`를 제거한다. exact evidence가 현재 source와 맞지 않으면 파일을 고쳐 맞추지 말고 원본 source identity를 조사한다.

## 실제 hardware evidence chain 현재 위치

현재 실제 장비 evidence는 **post-boot / S1 시작 이전에서 HOLD**다.

```text
software + synthetic gates = ready
actual POSTBOOT_ALL_FEATURES_OFF evidence = 없음
actual S1 observer evidence = 없음
actual S2A telemetry evidence = 없음
actual paired BIG/SMALL evidence = 없음
```

따라서 S2A/S2B/S4B로 건너뛰지 않는다. 실제 다음 gate는 `POSTBOOT_ALL_FEATURES_OFF`이며, PASS할 때만 `S1_OBSERVER_ONLY` plan을 생성한다.

## 상세 다음 세션 runbook

실제 NAS/차량 세션의 명령 순서는 [`NEXT_HARDWARE_SESSION_KR.md`](NEXT_HARDWARE_SESSION_KR.md)를 따른다. 이 문서보다 임의로 S2A/S2B/S4B 또는 shadow 실행을 앞당기지 않는다.

Machine-readable 상태는 [`HANDOFF_STATE_20260908.json`](HANDOFF_STATE_20260908.json)에 동일하게 기록한다.
