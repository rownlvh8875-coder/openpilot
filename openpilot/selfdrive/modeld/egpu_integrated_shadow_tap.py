"""Fail-open Stage-4B metadata tap for parked shadow commissioning.

The active model loop never waits for this sender. The packet contains only
metadata/input arrays plus a stationary/control-state guard. No camera pixels,
model output, or vehicle command is sent through this socket.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import json
import math
import os
import socket
import time
from typing import Any, Iterable

PROTOCOL_VERSION = 2
DEFAULT_SOCKET_PATH = "/tmp/egpu_integrated_shadow_input.sock"
DEFAULT_CONTROL_PATH = "/tmp/egpu_integrated_shadow_tap.enable"
MAX_PACKET_BYTES = 8192
CONTROL_POLL_S = 0.25


def _floats(values: Any) -> tuple[float, ...]:
  try:
    return tuple(float(v) for v in values.reshape(-1))
  except AttributeError:
    return tuple(float(v) for v in values)


def _finite(values: Iterable[float]) -> bool:
  return all(math.isfinite(float(v)) for v in values)


def normalize_gear(value: Any) -> str:
  text = str(value).lower()
  if "park" in text:
    return "park"
  if "drive" in text:
    return "drive"
  if "reverse" in text:
    return "reverse"
  if "neutral" in text:
    return "neutral"
  return text.rsplit(".", 1)[-1]


@dataclass(frozen=True)
class IntegratedShadowSnapshot:
  frame_id: int
  frame_id_extra: int
  state_frame_id: int
  camera_sof_ns: int
  camera_eof_ns: int
  active_backend: str
  v_ego: float
  standstill: bool
  gear: str
  lat_active: bool
  long_active: bool
  main_transform: tuple[float, ...]
  extra_transform: tuple[float, ...]
  desire_pulse: tuple[float, ...]
  traffic_convention: tuple[float, ...]
  action_t: tuple[float, ...]
  created_mono_ns: int

  def validate(self) -> None:
    if self.frame_id <= 0 or self.frame_id_extra <= 0:
      raise ValueError("frame ids must be positive")
    if self.state_frame_id < 0:
      raise ValueError("state_frame_id must be non-negative")
    if self.active_backend not in {"egpu", "qcom"}:
      raise ValueError("active_backend must be egpu or qcom")
    if len(self.main_transform) != 9 or len(self.extra_transform) != 9:
      raise ValueError("transforms must contain 9 floats")
    if len(self.traffic_convention) != 2 or len(self.action_t) != 2 or not self.desire_pulse:
      raise ValueError("invalid model input metadata dimensions")
    if not _finite((*self.main_transform, *self.extra_transform, *self.desire_pulse,
                    *self.traffic_convention, *self.action_t, self.v_ego)):
      raise ValueError("non-finite shadow metadata")

  @property
  def stationary_guard_ok(self) -> bool:
    return bool(self.standstill) and abs(float(self.v_ego)) < 0.01 and self.gear == "park" and not self.lat_active and not self.long_active


def encode_snapshot(snapshot: IntegratedShadowSnapshot) -> bytes:
  snapshot.validate()
  dat = json.dumps({"version": PROTOCOL_VERSION, **asdict(snapshot)}, separators=(",", ":"), allow_nan=False).encode("utf-8")
  if len(dat) > MAX_PACKET_BYTES:
    raise ValueError("shadow packet exceeds maximum size")
  return dat


def decode_snapshot(dat: bytes) -> IntegratedShadowSnapshot:
  if len(dat) > MAX_PACKET_BYTES:
    raise ValueError("shadow packet exceeds maximum size")
  value = json.loads(dat.decode("utf-8"))
  if value.get("version") != PROTOCOL_VERSION:
    raise ValueError("unsupported shadow protocol version")
  snap = IntegratedShadowSnapshot(
    frame_id=int(value["frame_id"]), frame_id_extra=int(value["frame_id_extra"]), state_frame_id=int(value["state_frame_id"]),
    camera_sof_ns=int(value["camera_sof_ns"]), camera_eof_ns=int(value["camera_eof_ns"]), active_backend=str(value["active_backend"]),
    v_ego=float(value["v_ego"]), standstill=bool(value["standstill"]), gear=str(value["gear"]),
    lat_active=bool(value["lat_active"]), long_active=bool(value["long_active"]),
    main_transform=tuple(float(x) for x in value["main_transform"]), extra_transform=tuple(float(x) for x in value["extra_transform"]),
    desire_pulse=tuple(float(x) for x in value["desire_pulse"]), traffic_convention=tuple(float(x) for x in value["traffic_convention"]),
    action_t=tuple(float(x) for x in value["action_t"]), created_mono_ns=int(value["created_mono_ns"]),
  )
  snap.validate()
  return snap


class IntegratedShadowTap:
  """Dynamic, best-effort sender. Send status must never affect modeld."""
  def __init__(self, *, enabled: bool | None = None, socket_path: str = DEFAULT_SOCKET_PATH,
               control_path: str = DEFAULT_CONTROL_PATH, control_poll_s: float = CONTROL_POLL_S):
    self._static_enabled = enabled
    self.socket_path = socket_path
    self.control_path = control_path
    self.control_poll_s = max(0.02, float(control_poll_s))
    self._last_poll = 0.0
    self._enabled = False
    self._sock: socket.socket | None = None
    self.sent = self.dropped = self.guard_rejected = self.encode_errors = 0
    self._refresh(force=True)

  def _desired(self) -> bool:
    if self._static_enabled is not None:
      return bool(self._static_enabled)
    env = os.getenv("EGPU_INTEGRATED_SHADOW_TAP", "0").strip().lower() in {"1", "true", "yes", "on"}
    return env or os.path.exists(self.control_path)

  def _refresh(self, *, force: bool = False) -> None:
    now = time.monotonic()
    if not force and now - self._last_poll < self.control_poll_s:
      return
    self._last_poll = now
    desired = self._desired()
    if desired == self._enabled:
      return
    self._enabled = desired
    if desired:
      self._sock = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
      self._sock.setblocking(False)
    elif self._sock is not None:
      self._sock.close(); self._sock = None

  def send(self, *, model: Any, meta_main: Any, meta_extra: Any, state_frame_id: int, v_ego: float,
           car_state: Any, car_control: Any, transform_main: Any, transform_extra: Any, inputs: dict[str, Any]) -> bool:
    self._refresh()
    if not self._enabled or self._sock is None:
      return False
    try:
      snap = IntegratedShadowSnapshot(
        frame_id=int(meta_main.frame_id), frame_id_extra=int(meta_extra.frame_id), state_frame_id=int(state_frame_id),
        camera_sof_ns=int(meta_main.timestamp_sof), camera_eof_ns=int(meta_main.timestamp_eof),
        active_backend="egpu" if bool(getattr(model, "usbgpu", False)) else "qcom", v_ego=float(v_ego),
        standstill=bool(getattr(car_state, "standstill", False)), gear=normalize_gear(getattr(car_state, "gearShifter", "unknown")),
        lat_active=bool(getattr(car_control, "latActive", False)), long_active=bool(getattr(car_control, "longActive", False)),
        main_transform=_floats(transform_main), extra_transform=_floats(transform_extra), desire_pulse=_floats(inputs["desire_pulse"]),
        traffic_convention=_floats(inputs["traffic_convention"]), action_t=_floats(inputs["action_t"]), created_mono_ns=time.monotonic_ns(),
      )
      if not snap.stationary_guard_ok:
        self.guard_rejected += 1
        return False
      self._sock.sendto(encode_snapshot(snap), self.socket_path)
      self.sent += 1
      return True
    except (BlockingIOError, FileNotFoundError, ConnectionRefusedError, OSError):
      self.dropped += 1
    except (KeyError, TypeError, ValueError, OverflowError):
      self.encode_errors += 1; self.dropped += 1
    return False

  def close(self) -> None:
    if self._sock is not None:
      self._sock.close(); self._sock = None
    self._enabled = False


class IntegratedShadowReceiver:
  """Latest-only receiver used by manual commissioning tools."""
  def __init__(self, path: str = DEFAULT_SOCKET_PATH):
    self.path = path
    self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM); self.sock.setblocking(False)
    try: os.unlink(path)
    except FileNotFoundError: pass
    self.sock.bind(path)
    self.received = self.superseded = self.decode_errors = self.guard_rejected = 0

  def recv_latest(self) -> IntegratedShadowSnapshot | None:
    latest = None
    while True:
      try: dat = self.sock.recv(MAX_PACKET_BYTES)
      except BlockingIOError: break
      self.received += 1
      try: snap = decode_snapshot(dat)
      except Exception:
        self.decode_errors += 1; continue
      if not snap.stationary_guard_ok:
        self.guard_rejected += 1; continue
      if latest is not None: self.superseded += 1
      latest = snap
    return latest

  def close(self) -> None:
    try: self.sock.close()
    finally:
      try: os.unlink(self.path)
      except FileNotFoundError: pass
