# comma 4 내장 GPU 개발 계획 — 1차 설정 복원 수정

## 기준과 범위

- 검토 원본: `ajouatom/openpilot:carrot-wip`의 `1ab3448148c936ea257389abaaf6e4ab26dd4af0` (2026-09-26 23:32:07 UTC).
- 사용자 포크: `rownlvh8875-coder/openpilot`. 동기화 전 `carrot-wip`는 `6f4c00e625dc3d41d3776427a272a5c3fed75e6c`이며, 원본보다 269커밋 뒤이고 포크 고유 커밋은 없다.
- 원격 `archive/pre-sync-20260927`에 이전 SHA를 보존한 뒤 `carrot-wip`를 `force:false`로 최신 원본까지 fast-forward했다. 새 원격·로컬 `dev/no-egpu-stage1-20260927`도 같은 최신 SHA를 기반으로 한다.
- 기존 `carrot-wip-integrated-v0` (`9a541782cf8cbc09bc9f744cd2adb59875196afa`), `carrot-wip-integrated-v6` (`7c70355361c929142b520e41412a7965d27bb60e`), `dev-integrated-handoff-20260908` (`00c5fbce0762d6d8058c83798a55635145e551db`)는 별도 작업 이력으로 보존한다. 이 계획에 통째로 병합하지 않는다.
- 대상은 eGPU 없는 comma 4이다. 첫 변경은 커스텀 토크 해제 시 설정 복원을 바로잡는다. 새 조향 튜닝값, 필터, 모델, 장치 배치, safety 한도는 변경 범위에 포함하지 않는다.

## 확인한 문제

기준 원본의 `openpilot/selfdrive/controls/lib/latcontrol_torque.py`는 설정을 10제어 주기마다 읽는다. 커스텀 해제 시 복원 분기의 조건이 이전 값 `> 1`이어서 일반적인 `1 → 0` 전환을 놓친다. 해당 분기도 토크 factor/offset/friction만 복원하므로 커스텀 PID 게인이 남을 수 있다.

`update_live_torque_params()`는 커스텀 사용 중 학습값 적용을 막고 있다. 해제 후에는 기존 호출부의 유효성 확인과 갱신 주기에 따라 정상 학습값을 다시 받아야 한다.

## 1차 변경 설계

1. 생성자가 이미 보존한 차량 기본 factor/offset/friction과 `CP.lateralTuning.torque.as_builder()`의 `torque_params.kp/ki/kf`를 복원에 사용한다. 커스텀 PID 변경은 이 메시지의 게인 필드를 덮어쓰지 않으므로 별도 기본 게인 저장 속성을 추가하지 않는다.
2. 새 설정이 꺼짐이고 이전 설정이 `> 0`이면 다음 기존 설정 갱신에서 위 기본값을 복원한다. 기본 PID 생성과 동일하게 `kd=0`으로 복원한다.
3. PID 객체를 다시 만들거나 `reset()`하지 않는다. 적분 상태 `PID.i`를 보존한다.
4. 이미 꺼져 있는 동안에는 기본값 복원을 반복하지 않는다. 정상 live torque 갱신 결과를 매번 덮어쓰지 않도록 한다.
5. custom ON 동작과 기존 live torque 유효성 조건·갱신 주기는 유지한다.
6. 한국어·영어 설정 카탈로그 요약과 상세 설명, `carrot_settings.json`의 설명을 함께 갱신한다. Wiki 자동 영역은 생성기로 검증한다. 포크의 현재 조회 대상이 원본 Wiki이므로 원격 Wiki를 변경하거나 게시하지 않는다.

## 검증 체크

- [x] 수정 전 `1 → 0`에서 토크 계수 또는 PID 게인이 남는 실패를 재현한다. `test_latcontrol_torque_params.py`의 7개 테스트 중 예상한 5개 실패, 2개 통과를 확인했다.
- [x] `1 → 0`과 이전 양수 값에서 꺼짐으로 전환할 때 factor/offset/friction, PID `kp/ki/kf`, `kd=0` 복원을 확인했다.
- [x] 복원 전후 `PID.i` 보존을 확인했다.
- [x] 꺼짐 유지 중 유효 live torque 값이 반복 복원으로 사라지지 않음을 확인했다.
- [x] custom ON 중 live torque 차단과 custom 게인 적용이 유지됨을 확인했다.
- [x] 기존 설정 읽기 주기 및 live torque 호출부의 유효성·갱신 조건이 바뀌지 않았는지 diff와 집중 회귀로 확인했다.
- [x] 한국어·영어 사용자 문서 검사를 통과했다. Wiki 로컬 생성·CI 검사(184설정, 554파일)와 Markdown 검증(553파일), 생성기 관련 테스트 25개를 통과했다. 카탈로그의 기존 빈 설명 경고 31개는 이 변경과 무관하며 원격 Wiki는 조회·변경하지 않았다.
- [x] 독립 리뷰에서 critical/important/minor 지적 없이 draft PR에 적합하다는 결론을 받았다. 리뷰에서도 집중 테스트 7개 통과를 확인했다. 결과와 제한을 PR에 기록하며 실제 기기 설치나 주행 검증 결과로 표현하지 않는다.

수정 후 `openpilot/selfdrive/controls/tests/test_latcontrol_torque_params.py`의 7개 집중 테스트가 0.43초에 통과했다. 실제 컨트롤러·PID·capnp를 실행하며 데스크톱 실행기 `.analysis/scratch/2026-09-27-stage1/run_focused.py`는 native Params와 hardware import 두 곳만 대체한다.

| 검증 | 결과 |
|---|---|
| 집중 회귀 | 7개 통과, 독립 리뷰에서도 재확인 |
| 사용자 문서 변경 검사 | 통과 |
| 사용자 문서 검사기 테스트 | `tools/docs/tests/test_check_user_docs.py` 19개 통과 |
| Wiki 생성·CI 검사 | 184설정, 554파일 생성·검사 통과; 기존 빈 설명 경고 31개 |
| Wiki Markdown 및 생성기 테스트 | 553파일 검증, 테스트 25개 통과 |
| 카탈로그 변경 범위 | `LateralTorqueCustom`의 `descr/edescr/cdescr`만 변경, 기본값·범위 유지 |
| 정적 검사 | `git diff --check`, Python 컴파일, 오류 lint(`E9,F63,F7,F82`) 통과 |
| 독립 리뷰 | critical/important/minor 지적 없음, draft PR에 적합 |

전체 `pytest -o addopts='' -q`는 미빌드 네이티브 모듈 `openpilot.common.params_pyx`를 불러오지 못해 conftest 단계에서 종료 코드 4로 중단됐다. 전체 테스트 통과나 기기 검증을 주장하지 않는다.

초안 PR #18의 첫 GitHub 사용자 문서 검사는 통과했다. Wiki 검사는 포크의 `openpilot.wiki.git` 익명 복제 단계에서 종료 코드 128로 실패했다. 읽기 전용 검사 대상을 Carrot Web이 실제 조회하는 `ajouatom/openpilot.wiki.git`으로 맞췄다. 검증기와 읽기 전용 권한은 유지하며, Wiki 게시 경로는 변경하지 않는다. 원격 재검사 결과는 PR의 Checks에서 확인한다.

## 후속 단계

| 단계 | 작업과 판단 기준 |
|---|---|
| 2차 | 레인리스 경로의 카메라·모델·계획·제어·액추에이터 총 지연을 실제 로그로 계측한다. 설정상 지연과 관측 지연을 구분한 뒤 조정 대상을 정한다. |
| 3차 | 커브 추종 오차와 조향 포화를 기준으로 속도 계획을 검토하고, 조건부 도로 경계 여유거리 보정의 필요성을 로그로 판단한다. 직선·커브 진입·탈출의 목표 변화와 실제 횡가속도, 감속·해제 시점을 비교한 뒤 변경 범위를 정한다. |
| 4차 | 정지·재출발을 차량별로 검토한다. 선행차 선택, 계획, CAN 요청, 실제 바퀴 움직임과 정지 유지 응답을 구분한다. 기존 정지 필터·재시도·히스테리시스를 새 기능으로 다시 추가하지 않는다. |

후속 제어 변경에는 실제 설치 브랜치와 커밋, 차종·연식·종방향 제어 구성, 현재 설정, 문제가 발생한 주행 로그가 필요하다. 기록 입력 재생은 변경한 명령에 따른 실제 차량 반응을 재현하지 않는다. 1차 수정과 이후 계획의 도로 주행 효능, 승차감 및 차량별 안전성은 아직 확인하지 않았다.
