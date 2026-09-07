# Integrated v6 전환 안전 절차

이 문서는 현재 출퇴근 데이터 수집용 `/data/openpilot`을 보호하면서 `carrot-wip-integrated-v6`를 나중에 안전하게 시험하기 위한 절차다.

## 원칙

- 현재 live tree를 임의로 `git switch/reset`하지 않는다.
- generated 파일 변화는 설치 판단 근거로 쓰지 않는다.
- local v6의 실제 소스 fingerprint만 SHA256으로 고정한다.
- preflight PASS는 설치 허가가 아니다.
- transition plan도 설치/재부팅/제어 권한을 부여하지 않는다.
- 첫 부팅은 integrated observer/telemetry/shadow marker가 모두 OFF인 상태에서만 검증한다.

## 1. Live provenance

`tools/egpu_integrated_live_provenance.py`는 다음을 fingerprint한다.

- live branch / HEAD
- v6 tracked 6개 파일의 `git diff --binary` SHA256
- `h1_observability.py`와 Hyundai radar DBC를 포함한 v6 source 8개 파일 SHA256

`services.h`, build output, model artifact 등 generated 파일은 의도적으로 제외한다.

## 2. Install preflight

`tools/egpu_integrated_install_preflight.py`는 read-only다.

source-provenance manifest를 제공하면 generated `git status` 변화 대신 exact v6 source fingerprint를 gate로 사용한다. 다음 조건을 함께 확인한다.

- offroad
- live / target checkout 존재
- backup 존재
- live source provenance 일치
- target branch/head 일치
- target tree clean
- patched modeld marker 제거 시 reviewed Carrot blob exact restore
- observer / telemetry / shadow marker 모두 OFF

PASS여도 `installAuthorization=false`, `rebootAuthorization=false`다.

## 3. Transition plan

`tools/egpu_integrated_transition_plan.py`는 PASS preflight와 exact target SHA가 있을 때만 계획 JSON을 만든다.

계획에는 evidence freeze, 수동 승인, all-features-OFF 설치, full reboot, post-boot verify, 실패 시 rollback 순서를 기록한다. 이 도구는 어떤 단계도 실행하지 않는다.

## 4. Post-boot verification

`tools/egpu_integrated_postboot_verify.py`는 첫 부팅 직후 read-only로 다음을 확인한다.

- offroad
- expected integrated branch/head
- modeld exact source-integrity
- integrated Python compile
- observer/telemetry/shadow marker OFF

PASS한 경우에만 다음 gate를 `S1_OBSERVER_ONLY`로 표시한다. public-road/control/shadow authorization은 계속 false다.

## 5. Rollback

post-boot가 HOLD/FAIL이면 S1로 진행하지 않는다. 원래 live source fingerprint와 backup을 기준으로 rollback한 뒤 `egpu_integrated_live_provenance.py verify`로 원상복구 여부를 다시 확인한다.

## 현재 상태

이 절차는 소프트웨어 준비 단계이며 현재 comma의 `/data/openpilot`을 변경하지 않는다. 실제 전환은 출퇴근 데이터 프로젝트와 충돌하지 않는 시점에 별도로 승인한 뒤 수행한다.
