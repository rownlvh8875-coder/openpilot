from __future__ import annotations

"""Observational-only Future-H1 schema-v6 all-loop trace helper.

A trace is emitted after every plannerd main-loop iteration, not only when a
longitudinalPlan is published. This preserves intermediate SubMaster receive
state and FastRadarOverlay-relevant loop boundaries without changing control.
"""

import hashlib
import json
import time
from dataclasses import dataclass
from typing import Any

import openpilot.cereal.messaging as messaging

SCHEMA_VERSION = 6
SERVICE_ORDER = (
  "modelV2", "liveTracks", "carControl", "carState", "controlsState",
  "liveParameters", "radarState", "selfdriveState", "carrotMan",
)
FAST_REASON = {
  "inactive": 0, "active": 1, "notRadarLead": 2,
  "selectionPending": 3, "selectionUnstable": 4, "trackMissing": 5,
  "trackUnmeasured": 6, "nonFinite": 7, "invalidDistance": 8,
  "distanceDiscontinuity": 9, "velocityDiscontinuity": 10,
  "radarStateInvalid": 11, "liveTracksInvalid": 12, "selectionStale": 13,
}
RADAR_INPUT_RAW = 0
RADAR_INPUT_FAST_OVERLAY = 1
RADAR_INPUT_NOT_EVALUATED = 2


def _mask(values: dict[str, bool]) -> int:
  return sum((1 << i) for i, service in enumerate(SERVICE_ORDER) if bool(values[service]))


def _capnp_bytes(value: Any) -> bytes:
  if hasattr(value, "as_builder"):
    return bytes(value.as_builder().to_bytes())
  return bytes(value.as_reader().as_builder().to_bytes())


def _enum_value(value: Any) -> Any:
  return getattr(value, "value", value)


@dataclass(frozen=True)
class H1InputSnapshot:
  submaster_frame: int
  capture_mono_time_ns: int
  log_mono_times: tuple[int, ...]
  recv_frames: tuple[int, ...]
  recv_times_ns: tuple[int, ...]
  seen_mask: int
  updated_mask: int
  alive_mask: int
  freq_ok_mask: int
  valid_mask: int

  def _idx(self, service: str) -> int:
    return SERVICE_ORDER.index(service)

  def log_mono_time(self, service: str) -> int:
    return self.log_mono_times[self._idx(service)]

  def recv_frame(self, service: str) -> int:
    return self.recv_frames[self._idx(service)]

  def recv_time_ns(self, service: str) -> int:
    return self.recv_times_ns[self._idx(service)]


class H1Observability:
  def __init__(self, startup_params_raw: dict[str, Any] | None = None) -> None:
    self.process_epoch = time.monotonic_ns()
    self.loop_sequence = 0
    self.planner_cycle = 0
    self.config_sequence = 0
    self.startup_params_raw = dict(startup_params_raw or {})
    self._last_config_sha256: bytes | None = None

  def capture_submaster(self, sm: Any) -> H1InputSnapshot:
    return H1InputSnapshot(
      submaster_frame=int(sm.frame),
      capture_mono_time_ns=time.monotonic_ns(),
      log_mono_times=tuple(int(sm.logMonoTime[s]) for s in SERVICE_ORDER),
      recv_frames=tuple(int(sm.recv_frame[s]) for s in SERVICE_ORDER),
      recv_times_ns=tuple(max(0, int(float(sm.recv_time[s]) * 1e9)) for s in SERVICE_ORDER),
      seen_mask=_mask({s: bool(sm.seen[s]) for s in SERVICE_ORDER}),
      updated_mask=_mask({s: bool(sm.updated[s]) for s in SERVICE_ORDER}),
      alive_mask=_mask({s: bool(sm.alive[s]) for s in SERVICE_ORDER}),
      freq_ok_mask=_mask({s: bool(sm.freq_ok[s]) for s in SERVICE_ORDER}),
      valid_mask=_mask({s: bool(sm.valid[s]) for s in SERVICE_ORDER}),
    )

  def _applied_config(self, carrot: Any, planner: Any) -> dict[str, Any]:
    raw_params = dict(self.startup_params_raw)
    raw_params.update(getattr(carrot, "h1_consumed_params_raw", {}))
    raw_params.update(getattr(planner, "h1_consumed_params_raw", {}))
    return {
      "rawParams": raw_params,
      "appliedConfig": {
      "configuredDrivingMode": _enum_value(carrot.myDrivingMode_last),
      "myDrivingModeAuto": getattr(carrot, "myDrivingModeAuto", None),
      "myHighModeFactor": carrot.myHighModeFactor,
      "trafficLightDetectMode": carrot.trafficLightDetectMode,
      "tFollowGap1": carrot.tFollowGap1,
      "tFollowGap2": carrot.tFollowGap2,
      "tFollowGap3": carrot.tFollowGap3,
      "tFollowGap4": carrot.tFollowGap4,
      "dynamicTFollow": carrot.dynamicTFollow,
      "leadAccelResponse": carrot.leadAccelResponse,
      "dynamicTFollowLC": carrot.dynamicTFollowLC,
      "enableSpeedTF": carrot.enableSpeedTF,
      "tFollowDecelBoost": carrot.tFollowDecelBoost,
      "cruiseMaxVals0": carrot.cruiseMaxVals0,
      "cruiseMaxVals1": carrot.cruiseMaxVals1,
      "cruiseMaxVals2": carrot.cruiseMaxVals2,
      "cruiseMaxVals3": carrot.cruiseMaxVals3,
      "cruiseMaxVals4": carrot.cruiseMaxVals4,
      "cruiseMaxVals5": carrot.cruiseMaxVals5,
      "cruiseMaxVals6": carrot.cruiseMaxVals6,
      "stopDistanceCarrotApplied": carrot.stop_distance,
      "cruiseEcoControlApplied": carrot.eco_over_speed,
      "autoNaviSpeedDecelRateApplied": carrot.autoNaviSpeedDecelRate,
      "aChangeCostStartingApplied": carrot.aChangeCostStarting,
      "trafficStopDistanceAdjustApplied": carrot.trafficStopDistanceAdjust,
      "longActuatorDelaySeconds": planner.observedLongActuatorDelaySeconds,
      "vEgoStoppingMetersPerSecond": planner.observedVEgoStoppingMetersPerSecond,
      },
    }

  @staticmethod
  def _canonical_config(config: dict[str, Any]) -> bytes:
    return json.dumps(config, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")

  def _publish_config_if_changed(self, pm: Any, carrot: Any, planner: Any) -> bytes:
    payload = self._canonical_config(self._applied_config(carrot, planner))
    digest = hashlib.sha256(payload).digest()
    if digest != self._last_config_sha256:
      self.config_sequence += 1
      msg = messaging.new_message("carrotH1ConfigSnapshot")
      msg.valid = True
      out = msg.carrotH1ConfigSnapshot
      out.schemaVersion = SCHEMA_VERSION
      out.processEpoch = self.process_epoch
      out.configSequence = self.config_sequence
      out.configSha256 = digest
      out.canonicalJsonUtf8 = payload
      pm.send("carrotH1ConfigSnapshot", msg)
      self._last_config_sha256 = digest
    return digest

  @staticmethod
  def _snapshot_identity(payload: dict[str, Any]) -> bytes:
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")
    return hashlib.sha256(raw).digest()

  def publish_loop(
    self,
    *,
    pm: Any,
    snapshot: H1InputSnapshot,
    decision_mono_time_ns: int,
    planning_trigger: str,
    run_longitudinal: bool,
    longitudinal_plan_emitted: bool,
    live_tracks_recent: bool,
    use_live_tracks_trigger: bool,
    trigger_interval_ok: bool,
    planner_sm: Any,
    carrot: Any,
    planner: Any,
    fast_result: Any | None,
  ) -> None:
    if planning_trigger not in ("modelV2", "liveTracks"):
      raise ValueError(f"unsupported planning trigger: {planning_trigger}")

    self.loop_sequence += 1
    if longitudinal_plan_emitted:
      config_digest = self._publish_config_if_changed(pm, carrot, planner)
      self.planner_cycle += 1
      radar_digest = hashlib.sha256(_capnp_bytes(planner_sm["radarState"])).digest()
      radar_input_kind = RADAR_INPUT_FAST_OVERLAY if fast_result is not None else RADAR_INPUT_RAW
    else:
      config_digest = self._last_config_sha256 or b""
      radar_digest = b""
      radar_input_kind = RADAR_INPUT_NOT_EVALUATED

    identity_payload = {
      "processEpoch": self.process_epoch,
      "loopSequence": self.loop_sequence,
      "plannerCycle": self.planner_cycle,
      "subMasterFrame": snapshot.submaster_frame,
      "captureMonoTimeNs": snapshot.capture_mono_time_ns,
      "decisionMonoTimeNs": int(decision_mono_time_ns),
      "logMonoTimes": snapshot.log_mono_times,
      "recvFrames": snapshot.recv_frames,
      "recvTimesNs": snapshot.recv_times_ns,
      "seenMask": snapshot.seen_mask,
      "updatedMask": snapshot.updated_mask,
      "aliveMask": snapshot.alive_mask,
      "freqOkMask": snapshot.freq_ok_mask,
      "validMask": snapshot.valid_mask,
      "planningTriggerKind": 1 if planning_trigger == "liveTracks" else 0,
      "planningTriggerLogMonoTime": snapshot.log_mono_time(planning_trigger),
      "runLongitudinal": bool(run_longitudinal),
      "longitudinalPlanEmitted": bool(longitudinal_plan_emitted),
      "liveTracksRecent": bool(live_tracks_recent),
      "useLiveTracksTrigger": bool(use_live_tracks_trigger),
      "triggerIntervalOk": bool(trigger_interval_ok),
      "configSequence": self.config_sequence,
      "configSha256": config_digest.hex(),
      "radarInputKind": radar_input_kind,
      "effectiveRadarStateSha256": radar_digest.hex(),
    }
    snapshot_digest = self._snapshot_identity(identity_payload)

    msg = messaging.new_message("carrotH1ReplayTrace")
    msg.valid = True
    out = msg.carrotH1ReplayTrace
    out.schemaVersion = SCHEMA_VERSION
    out.processEpoch = self.process_epoch
    out.plannerCycle = self.planner_cycle
    out.subMasterFrame = snapshot.submaster_frame
    out.planningTriggerKind = identity_payload["planningTriggerKind"]
    out.planningTriggerLogMonoTime = identity_payload["planningTriggerLogMonoTime"]
    out.configSequence = self.config_sequence
    out.configSha256 = config_digest
    for service, field in (
      ("modelV2", "modelV2LogMonoTime"), ("liveTracks", "liveTracksLogMonoTime"),
      ("carControl", "carControlLogMonoTime"), ("carState", "carStateLogMonoTime"),
      ("controlsState", "controlsStateLogMonoTime"), ("liveParameters", "liveParametersLogMonoTime"),
      ("radarState", "radarStateLogMonoTime"), ("selfdriveState", "selfdriveStateLogMonoTime"),
      ("carrotMan", "carrotManLogMonoTime"),
    ):
      setattr(out, field, snapshot.log_mono_time(service))
    out.updatedMask = snapshot.updated_mask
    out.aliveMask = snapshot.alive_mask
    out.freqOkMask = snapshot.freq_ok_mask
    out.validMask = snapshot.valid_mask
    out.radarInputKind = radar_input_kind
    if fast_result is None:
      out.fastLeadMask = 0
      out.fastLeadTrackId = -1
      out.fastLeadReason = FAST_REASON["inactive"]
    else:
      out.fastLeadMask = int(fast_result.lead_mask)
      out.fastLeadTrackId = int(fast_result.lead_one_track_id)
      reason = str(fast_result.lead_one_reason)
      if reason not in FAST_REASON:
        raise ValueError(f"unknown fast lead reason: {reason}")
      out.fastLeadReason = FAST_REASON[reason]
    out.effectiveRadarStateSha256 = radar_digest
    out.loopSequence = self.loop_sequence
    out.captureMonoTimeNs = snapshot.capture_mono_time_ns
    out.decisionMonoTimeNs = int(decision_mono_time_ns)
    out.seenMask = snapshot.seen_mask
    out.longitudinalPlanEmitted = bool(longitudinal_plan_emitted)
    out.runLongitudinal = bool(run_longitudinal)
    out.liveTracksRecent = bool(live_tracks_recent)
    out.useLiveTracksTrigger = bool(use_live_tracks_trigger)
    out.triggerIntervalOk = bool(trigger_interval_ok)
    for service, field in (
      ("modelV2", "modelV2RecvFrame"), ("liveTracks", "liveTracksRecvFrame"),
      ("carControl", "carControlRecvFrame"), ("carState", "carStateRecvFrame"),
      ("controlsState", "controlsStateRecvFrame"), ("liveParameters", "liveParametersRecvFrame"),
      ("radarState", "radarStateRecvFrame"), ("selfdriveState", "selfdriveStateRecvFrame"),
      ("carrotMan", "carrotManRecvFrame"),
    ):
      setattr(out, field, snapshot.recv_frame(service))
    for service, field in (
      ("modelV2", "modelV2RecvTimeNs"), ("liveTracks", "liveTracksRecvTimeNs"),
      ("carControl", "carControlRecvTimeNs"), ("carState", "carStateRecvTimeNs"),
      ("controlsState", "controlsStateRecvTimeNs"), ("liveParameters", "liveParametersRecvTimeNs"),
      ("radarState", "radarStateRecvTimeNs"), ("selfdriveState", "selfdriveStateRecvTimeNs"),
      ("carrotMan", "carrotManRecvTimeNs"),
    ):
      setattr(out, field, snapshot.recv_time_ns(service))
    out.consumedSnapshotIdentitySha256 = snapshot_digest
    pm.send("carrotH1ReplayTrace", msg)
