# Integrated v6 다음 하드웨어 세션 — 2026-09-08

> 이 문서는 handoff 브랜치에 보관하지만, 실제 차량/commissioning expected HEAD는 아래 `7c703553...`로 고정한다. 문서 브랜치의 SHA를 차량 source로 사용하지 않는다.

## 고정 기준

- integrated branch: `carrot-wip-integrated-v6`
- expected integrated HEAD: `7c70355361c929142b520e41412a7965d27bb60e`
- PR #15 paired-input adapter: merged
- PR #16 evidence discovery: merged
- PR #17 unverified/invalid classification fix: merged
- final local checkout: clean
- final post-merge CI: SUCCESS

## 현재 HOLD 원인

현재 회사 PC에서는 개인 NAS와 comma에 접근할 수 없다.

- `\\DS1821P\openpilot`: DNS/SMB 접근 불가
- `https://upload.shind0.synology.me`: 회사 보안망 `CLOUD 기타 / 기본차단`
- `10.108.18.70:22`: 접근 불가
- `192.168.219.107:22`: 접근 불가

따라서 현재 상태는 `HOLD_EXTERNAL_ACCESS_REQUIRED`이다.

## 1. NAS가 보이면 가장 먼저 할 일

최종 통합 checkout에서 read-only discovery를 실행한다.

```text
py -3.14 tools\egpu_integrated_paired_evidence_discovery.py ^
  --root "\\DS1821P\openpilot" ^
  --expected-source-head 7c70355361c929142b520e41412a7965d27bb60e ^
  --expected-source-branch carrot-wip-integrated-v6 ^
  --output <new-discovery-report.json>
```

- `VERIFIED_PAIRED_EVIDENCE_FOUND` → 기존 paired adapter/review bundle로 실제 오프라인 분석 진행
- `PAIRED_EVIDENCE_FOUND_NOT_VERIFIED` → full verification부터 수행
- `INVALID_PAIRED_EVIDENCE_FOUND` → 원본 보존 후 provenance/receipt 오류 조사
- paired evidence 없음 → 아래 정차 commissioning 준비로 이동

`rlog/qlog`만 있다고 동일 입력 BIG/SMALL 증거로 승격하지 않는다.

## 2. comma 정차 접속 후 설치 전 read-only gate

차량이 offroad이고 안전하게 정차한 상태에서 먼저 현재 live source를 보존한다.

```text
python3 tools/egpu_integrated_live_provenance.py snapshot \
  --repo /data/openpilot \
  --output <live-provenance.json>
```

그 다음 install preflight를 수행한다.

```text
python3 tools/egpu_integrated_install_preflight.py \
  --live /data/openpilot \
  --target /data/openpilot-egpu-integrated-v6-src \
  --backup <existing-backup-dir> \
  --expected-target-head 7c70355361c929142b520e41412a7965d27bb60e \
  --live-provenance <live-provenance.json>
```

`<existing-backup-dir>`에는 최소 `tracked_changes.patch`와 `status.txt`가 이미 존재해야 한다. 이 저장소에는 backup을 자동 생성하는 도구가 없으므로, 없으면 preflight를 PASS로 만들려고 임의 파일을 생성하지 말고 HOLD한다.

## 3. preflight PASS 이후의 경계

preflight PASS는 설치 허가가 아니다. PASS JSON을 외부 파일로 보존한 뒤 plan-only 도구만 실행할 수 있다.

```text
python3 tools/egpu_integrated_transition_plan.py \
  --preflight <preflight-pass.json> \
  --expected-target-head 7c70355361c929142b520e41412a7965d27bb60e \
  --output <transition-plan.json>
```

실제 source 전환, marker 변경, full reboot는 이 plan이 자동 실행하지 않는다. 해당 단계는 별도 명시 승인과 당시 live state 재검증이 필요하다.

첫 integrated 부팅은 observer/telemetry/shadow marker가 모두 OFF인 상태여야 한다.

부팅 후에는 read-only post-boot verification을 실행한다.

```text
python3 tools/egpu_integrated_postboot_verify.py \
  --live /data/openpilot \
  --expected-head 7c70355361c929142b520e41412a7965d27bb60e \
  > <postboot.json>
```

`status=PASS`, `nextGate=S1_OBSERVER_ONLY`, exact HEAD/branch 일치가 아니면 S1로 진행하지 않는다.

## 4. S1 observer commissioning

post-boot PASS에서만 S1 plan을 생성한다.

```text
python3 tools/egpu_integrated_s1_plan.py \
  --postboot <postboot-pass.json> \
  --expected-head 7c70355361c929142b520e41412a7965d27bb60e \
  --output <s1-plan.json>
```

S1은 반드시 아래 OFF/ON/OFF 순서이며 각 leg 사이 full reboot가 필요하다.

```text
S1_OFF_BEFORE : observer OFF / telemetry OFF / shadow OFF
S1_ON         : observer ON  / telemetry OFF / shadow OFF
S1_OFF_AFTER  : observer OFF / telemetry OFF / shadow OFF
```

모든 leg는 P단, 완전 정차, lateral/longitudinal controls inactive 조건이다. recorder의 `--duration`은 `>0` 및 `<=600 s`만 허용하며, 이번 준비 단계에서 임의 duration을 acceptance 기준으로 정하지 않는다.

각 leg recorder 형식:

```text
python3 tools/egpu_integrated_s1_recorder.py \
  --leg <S1_OFF_BEFORE|S1_ON|S1_OFF_AFTER> \
  --duration <explicit-seconds> \
  --output-dir <s1-evidence-dir> \
  --expected-head 7c70355361c929142b520e41412a7965d27bb60e
```

recorder는 marker를 변경하지 않는다. observer marker `/data/egpu_integrated/observer_enabled`, telemetry marker `/data/egpu_integrated/telemetry_enabled`, shadow marker `/tmp/egpu_integrated_shadow_tap.enable`의 실제 변경과 reboot는 별도 승인 전 수행하지 않는다.

3개 summary가 모두 확보된 뒤:

```text
python3 tools/egpu_integrated_s1_build_evidence.py \
  <S1_OFF_BEFORE.summary.json> \
  <S1_ON.summary.json> \
  <S1_OFF_AFTER.summary.json> \
  --output <s1-evidence.json>
```

qualification에는 `config/egpu_integrated_s1_policy.schema.json`에 맞는 explicit policy가 반드시 필요하다. policy가 없으면 정상 상태는 HOLD이며 임의 threshold를 생성하지 않는다.
