#!/usr/bin/env python3
import time

from openpilot.cereal import car
from openpilot.common.params import Params
from openpilot.common.realtime import Priority, config_realtime_process
from openpilot.common.swaglog import cloudlog
from openpilot.selfdrive.controls.lib.ldw import LaneDepartureWarning
from openpilot.selfdrive.controls.lib.longitudinal_planner import LongitudinalPlanner
from openpilot.selfdrive.controls.lib.longitudinal_fast_radar import (
  FastRadarOverlay,
  RadarStateOverride,
)
from openpilot.selfdrive.controls.lib.longitudinal_stopping_lead import StoppingLeadFilter
from openpilot.selfdrive.controls.lib.h1_observability import H1Observability
from openpilot.selfdrive.controls.lib.lateral_planner import LateralPlanner
import openpilot.cereal.messaging as messaging
from openpilot.selfdrive.carrot.carrot_functions import CarrotPlanner
from openpilot.selfdrive.carrot.radar import effective_radar_track_mode


LIVE_TRACKS_FALLBACK_TIMEOUT_S = 0.10
MIN_LONGITUDINAL_PLAN_INTERVAL_NS = 25_000_000
H1_OBSERVABILITY_ERROR_LOG_INTERVAL_S = 60.0


def _log_h1_observability_exception(message: str, next_log_time: float) -> float:
  now = time.monotonic()
  if now < next_log_time:
    return next_log_time
  try:
    cloudlog.exception(message)
  except Exception:
    pass
  return now + H1_OBSERVABILITY_ERROR_LOG_INTERVAL_S


def main():
  config_realtime_process(7, Priority.CTRL_LOW)

  cloudlog.info("plannerd is waiting for CarParams")
  params = Params()
  CP = messaging.log_from_bytes(params.get("CarParams", block=True), car.CarParams)
  cloudlog.info("plannerd got CarParams: %s", CP.brand)

  # Keep Hyundai's existing radar-triggered fast path. Other brands can publish
  # radar at a different rate (VW MEB: 25 Hz), while the planner integrates at
  # DT_MDL = 50 ms. Preserve their original modelV2 clock independently of which
  # radar source supplies the lead observations.
  enable_radar_tracks_raw = params.get_int("EnableRadarTracks")
  radar_track_mode = effective_radar_track_mode(
    CP.brand,
    CP.radarUnavailable,
    enable_radar_tracks_raw,
  )
  live_tracks_longitudinal = CP.brand == "hyundai" and radar_track_mode >= 1

  ldw = LaneDepartureWarning()
  longitudinal_planner = LongitudinalPlanner(CP)
  lateral_planner = LateralPlanner(CP, debug=False)
  fast_radar = FastRadarOverlay(
    front_radar_delay_s=float(CP.radarDelay),
  )
  stopping_lead_filter = StoppingLeadFilter()

  pm = messaging.PubMaster(['longitudinalPlan', 'driverAssistance', 'lateralPlan', 'carrotH1ReplayTrace', 'carrotH1ConfigSnapshot'])
  # One process owns both planners to avoid another ~100 MB Python runtime.
  # Hyundai's two inputs wake this serial loop independently; no MPC overlaps.
  sm = messaging.SubMaster(
    ['carControl', 'carState', 'controlsState', 'liveParameters', 'radarState',
     'liveTracks', 'modelV2', 'selfdriveState', 'carrotMan'],
    poll=(['modelV2', 'liveTracks'] if live_tracks_longitudinal else 'modelV2'),
    ignore_avg_freq=['radarState'],
  )
  carrot = CarrotPlanner()
  h1_observability = H1Observability(startup_params_raw={
    "EnableRadarTracks": {"type": "int", "value": int(enable_radar_tracks_raw)},
  })
  h1_capture_error_log_deadline = 0.0
  h1_publish_error_log_deadline = 0.0
  model_frame = 0
  last_longitudinal_trigger_mono_ns = 0

  while True:
    sm.update()
    try:
      h1_input_snapshot = h1_observability.capture_submaster(sm)
    except Exception:
      h1_input_snapshot = None
      h1_capture_error_log_deadline = _log_h1_observability_exception(
        "H1 observability input snapshot failed", h1_capture_error_log_deadline,
      )

    if sm.updated['radarState']:
      fast_radar.observe_radar_state(
        sm['radarState'],
        sm.logMonoTime['radarState'],
        sm.valid['radarState'] and sm.alive['radarState'],
      )

    # For a stably selected physical lead in normal radar ACC, liveTracks is
    # the longitudinal clock. Vision-only, experimental, Carrot blended, and
    # radarless/SCC-only operation retain the newest modelV2 clock. Capture the
    # exact monotonic value used by this control decision for schema-v6 replay.
    decision_mono_s = time.monotonic()
    decision_mono_time_ns = int(decision_mono_s * 1e9)
    live_tracks_recent = (
      sm.seen['liveTracks']
      and decision_mono_s - sm.recv_time['liveTracks'] <= LIVE_TRACKS_FALLBACK_TIMEOUT_S
    )
    use_live_tracks_trigger = (
      live_tracks_longitudinal
      and not sm['selfdriveState'].experimentalMode
      and getattr(carrot, 'mode', 'acc') == 'acc'
      and live_tracks_recent
      and sm.valid['radarState']
      and sm.alive['radarState']
      and fast_radar.lead_one_ready(sm['radarState'])
    )
    planning_trigger = 'liveTracks' if use_live_tracks_trigger else 'modelV2'
    trigger_mono_ns = sm.logMonoTime[planning_trigger]
    trigger_interval_ok = (
      last_longitudinal_trigger_mono_ns == 0
      or trigger_mono_ns - last_longitudinal_trigger_mono_ns >= MIN_LONGITUDINAL_PLAN_INTERVAL_NS
    )
    run_longitudinal = sm.updated[planning_trigger] and trigger_interval_ok
    planner_sm = sm
    fast_result = None
    longitudinal_plan_emitted = False

    if run_longitudinal and sm.seen['modelV2']:
      # Bound unexpected replay/recovery bursts without filtering either
      # service's normal 20 Hz cadence.
      last_longitudinal_trigger_mono_ns = trigger_mono_ns
      planner_start = time.monotonic()

      if use_live_tracks_trigger:
        fast_radar_start = time.monotonic()
        fast_result = fast_radar.build(
          sm['radarState'],
          sm['liveTracks'],
          sm['carState'].vEgo,
          sm.logMonoTime['radarState'],
          sm.logMonoTime['liveTracks'],
          radar_state_valid=sm.valid['radarState'] and sm.alive['radarState'],
          live_tracks_valid=sm.valid['liveTracks'] and sm.alive['liveTracks'],
        )
        fast_radar_execution_time = time.monotonic() - fast_radar_start
        planner_sm = RadarStateOverride(sm, fast_result.radar_state)
      else:
        fast_result = None
        fast_radar_execution_time = 0.0
        planner_sm = sm

      # Apply after the fast overlay, which reconstructs vLead from vEgo+vRel.
      # Conditioning every ACC input path also covers model-clock fallbacks.
      stopping_radar_state = stopping_lead_filter.update(
        planner_sm['radarState'],
        stopping=(
          CP.openpilotLongitudinalControl
          and sm['controlsState'].longControlState == car.CarControl.Actuators.LongControlState.stopping
          and not sm['selfdriveState'].experimentalMode
          and getattr(carrot, 'mode', 'acc') == 'acc'
          and not sm['carState'].gasPressed
        ),
        v_ego=sm['carState'].vEgo,
        mono_time_ns=sm.logMonoTime['liveTracks' if fast_result is not None and fast_result.lead_mask else 'radarState'],
        valid=(
          sm.valid['radarState'] and sm.alive['radarState']
          and sm.valid['carState'] and sm.alive['carState']
          and (not use_live_tracks_trigger or (sm.valid['liveTracks'] and sm.alive['liveTracks']))
        ),
      )
      planner_sm = RadarStateOverride(planner_sm, stopping_radar_state)

      longitudinal_planner.update(planner_sm, carrot)
      planner_execution_time = time.monotonic() - planner_start
      longitudinal_planner.publish(
        planner_sm,
        pm,
        carrot,
        planner_execution_time=planner_execution_time,
        live_tracks_mono_time=(sm.logMonoTime['liveTracks'] if sm.seen['liveTracks'] else 0),
        fast_lead_mask=(fast_result.lead_mask if fast_result is not None else 0),
        fast_lead_track_id=(fast_result.lead_one_track_id if fast_result is not None else -1),
        planning_trigger=planning_trigger,
        fast_radar_execution_time=fast_radar_execution_time,
        fast_lead_reason=(fast_result.lead_one_reason if fast_result is not None else 'inactive'),
      )
      longitudinal_plan_emitted = True

    if sm.updated['modelV2']:
      model_frame += 1
      lateral_planner.update(sm, carrot)
      lateral_planner.publish(sm, pm, carrot)

      ldw.update(model_frame, sm['modelV2'], sm['carState'], sm['carControl'])
      msg = messaging.new_message('driverAssistance')
      msg.valid = sm.all_checks(['carState', 'carControl', 'modelV2', 'liveParameters'])
      msg.driverAssistance.leftLaneDeparture = ldw.left
      msg.driverAssistance.rightLaneDeparture = ldw.right
      pm.send('driverAssistance', msg)

    # Schema-v6 observability is deliberately last in every main-loop iteration.
    # Capture happened immediately after sm.update(); publishing here cannot alter
    # the already-computed control outputs or FastRadarOverlay state transition.
    if h1_input_snapshot is not None:
      try:
        h1_observability.publish_loop(
          pm=pm,
          snapshot=h1_input_snapshot,
          decision_mono_time_ns=decision_mono_time_ns,
          planning_trigger=planning_trigger,
          run_longitudinal=run_longitudinal,
          longitudinal_plan_emitted=longitudinal_plan_emitted,
          live_tracks_recent=live_tracks_recent,
          use_live_tracks_trigger=use_live_tracks_trigger,
          trigger_interval_ok=trigger_interval_ok,
          planner_sm=planner_sm,
          carrot=carrot,
          planner=longitudinal_planner,
          fast_result=fast_result,
        )
      except Exception:
        h1_publish_error_log_deadline = _log_h1_observability_exception(
          "H1 observability loop trace publish failed", h1_publish_error_log_deadline,
        )


if __name__ == "__main__":
  main()
