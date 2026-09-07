# Carrot-WIP Integrated v6 Migration

이 브랜치는 사용자의 현재 comma에서 이미 운용 중인 local v6 동작을 `carrot-wip-integrated-v0` 위로 옮긴 migration 브랜치다.

## 기준

- upstream reviewed carrot-wip: `6f4c00e625dc3d41d3776427a272a5c3fed75e6c`
- integrated base: `carrot-wip-integrated-v0`
- v6 source live head: `ce3d76301c988db8aa955e1ebc6496f0a0fd2abc`
- integrated modeld original blob: `e4de3eb2236f6bb0c54c87666099147311602f4f`

## Migration 원칙

- 기존 `carrot-wip` 원본 브랜치를 수정하지 않는다.
- 실제 comma `/data/openpilot`은 별도 실기기 승인 전 변경하지 않는다.
- generated `openpilot/cereal/services.h`는 source migration에 포함하지 않는다.
- 최신 Carrot `StoppingLeadFilter`와 local v6 `H1Observability`를 모두 보존한다.
- eGPU shadow는 controls/modelV2를 publish하지 않는다.
- 이 migration은 이미 사용 중인 local v6 동작을 보존하는 단계이며, 새로운 사용자 설정/동작 계약 문서는 v6 기능의 정식 승격 시 작성한다.

## Upstream drift gate

`tools/egpu_integrated_source_compat.py`가 eGPU integration critical paths와 local v6 migration paths를 독립적으로 검사한다. upstream HEAD만 바뀌고 해당 path blob이 동일하면 계속 진행할 수 있고, critical path 또는 v6 migration path가 바뀌면 자동으로 review가 필요하다.

## 현재 제한

S4B는 P단·정차·lat/long controls inactive 조건의 최대 5 Hz load probe만 허용한다. 실제 S4B 간섭 evidence가 PASS하기 전에는 20 Hz shadow runner나 public-road shadow를 허용하지 않는다.
