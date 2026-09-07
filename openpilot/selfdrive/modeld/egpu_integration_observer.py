"""Fail-open observer for the Carrot-WIP integrated eGPU branch.

This module intentionally uses only the Python standard library and never
returns a value that modeld should use for control decisions.  It is designed
to be copied to:

  openpilot/selfdrive/modeld/egpu_integration_observer.py

The observer is disabled by default. Enable it with either:

  EGPU_INTEGRATED_OBSERVER=1

or the marker file:

  /data/egpu_integrated/observer_enabled

Its only output is an atomically replaced JSON state file. Any observer error
is swallowed so the research path cannot take down modeld.
"""
from __future__ import annotations

from collections import deque
import json
import math
import os
from pathlib import Path
import tempfile
import time
from typing import Deque


DEFAULT_ROOT = Path("/data/egpu_integrated")
ENABLE_MARKER = DEFAULT_ROOT / "observer_enabled"
DEFAULT_STATE_PATH = DEFAULT_ROOT / "state.json"


def _env_enabled() -> bool:
  return os.getenv("EGPU_INTEGRATED_OBSERVER", "0").strip().lower() in {"1", "true", "yes", "on"}


def _percentile(values: list[float], q: float) -> float | None:
  if not values:
    return None
  xs = sorted(values)
  if len(xs) == 1:
    return xs[0]
  pos = max(0.0, min(1.0, q)) * (len(xs) - 1)
  lo = int(math.floor(pos))
  hi = min(lo + 1, len(xs) - 1)
  frac = pos - lo
  return xs[lo] * (1.0 - frac) + xs[hi] * frac


class EgpuIntegrationObserver:
  def __init__(self, *, enabled: bool | None = None, state_path: str | Path | None = None,
               publish_interval_s: float = 1.0, latency_window: int = 512):
    self.enabled = (_env_enabled() or ENABLE_MARKER.is_file()) if enabled is None else bool(enabled)
    self.state_path = Path(state_path) if state_path is not None else Path(os.getenv("EGPU_INTEGRATED_STATE_PATH", str(DEFAULT_STATE_PATH)))
    self.publish_interval_s = max(0.1, float(publish_interval_s))
    self._latencies_ms: Deque[float] = deque(maxlen=max(16, int(latency_window)))
    self.frames_observed = 0
    self.fallback_count = 0
    self.last_fallback_frame_id: int | None = None
    self.last_fallback_reason: str | None = None
    self.last_publish_mono = 0.0
    self.max_model_execution_ms = 0.0
    self.write_errors = 0

  def note_fallback(self, *, frame_id: int, reason: str) -> None:
    if not self.enabled:
      return
    try:
      self.fallback_count += 1
      self.last_fallback_frame_id = int(frame_id)
      self.last_fallback_reason = str(reason)[:160]
    except Exception:
      pass

  def observe(self, *, frame_id: int, state_frame_id: int, attempted_backend: str,
              active_backend: str, model_execution_s: float) -> None:
    if not self.enabled:
      return
    try:
      latency_ms = max(0.0, float(model_execution_s) * 1000.0)
      self._latencies_ms.append(latency_ms)
      self.frames_observed += 1
      self.max_model_execution_ms = max(self.max_model_execution_ms, latency_ms)
      now = time.monotonic()
      if now - self.last_publish_mono < self.publish_interval_s:
        return
      self.last_publish_mono = now
      self._publish({
        "schemaVersion": 1,
        "enabled": True,
        "timestampMonoS": now,
        "frameId": int(frame_id),
        "stateFrameId": int(state_frame_id),
        "frameAge": max(int(state_frame_id) - int(frame_id), 0),
        "attemptedBackend": str(attempted_backend),
        "activeBackend": str(active_backend),
        "modelExecutionMs": latency_ms,
        "p95ModelExecutionMs": _percentile(list(self._latencies_ms), 0.95),
        "maxModelExecutionMs": self.max_model_execution_ms,
        "latencyWindowSamples": len(self._latencies_ms),
        "framesObserved": self.frames_observed,
        "fallbackCount": self.fallback_count,
        "lastFallbackFrameId": self.last_fallback_frame_id,
        "lastFallbackReason": self.last_fallback_reason,
        "writeErrors": self.write_errors,
      })
    except Exception:
      # Absolutely no observer failure may escape into modeld.
      pass

  def _publish(self, payload: dict) -> None:
    try:
      self.state_path.parent.mkdir(parents=True, exist_ok=True)
      fd, tmp_name = tempfile.mkstemp(prefix=".egpu-state-", suffix=".json", dir=self.state_path.parent)
      try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
          json.dump(payload, f, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
          f.write("\n")
          f.flush()
          os.fsync(f.fileno())
        os.replace(tmp_name, self.state_path)
      finally:
        try:
          os.unlink(tmp_name)
        except FileNotFoundError:
          pass
    except Exception:
      self.write_errors += 1
