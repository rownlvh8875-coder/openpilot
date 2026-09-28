"""Read-only eGPU hardware telemetry for the Carrot-WIP integrated branch.

This module is intended to be copied to:

  openpilot/selfdrive/modeld/egpu_hardware_telemetry.py

Design constraints:
- disabled by default
- never opens the AMD device merely to collect metrics
- samples only when modeld already owns an opened AMD device
- slow hardware reads run on a daemon thread, never the 20 Hz model loop
- USB bridge access is serialized through Carrot's existing usbgpu_bus_lock
- no PPT, fan, PCIe, USB, or vehicle-control writes are performed
- every failure is contained inside this observer path

Enable with either:

  EGPU_INTEGRATED_TELEMETRY=1

or:

  /data/egpu_integrated/telemetry_enabled
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import math
import os
from pathlib import Path
import tempfile
import threading
import time
from typing import Any


DEFAULT_ROOT = Path("/data/egpu_integrated")
ENABLE_MARKER = DEFAULT_ROOT / "telemetry_enabled"
DEFAULT_STATE_PATH = DEFAULT_ROOT / "hardware.json"
DEFAULT_INTERVAL_S = 2.0


def _env_enabled() -> bool:
  return os.getenv("EGPU_INTEGRATED_TELEMETRY", "0").strip().lower() in {"1", "true", "yes", "on"}


def _finite_float(value: Any) -> float | None:
  try:
    out = float(value)
  except (TypeError, ValueError):
    return None
  return out if math.isfinite(out) else None


def _finite_int(value: Any) -> int | None:
  out = _finite_float(value)
  return int(out) if out is not None else None


def _q10(value: Any) -> int | None:
  out = _finite_int(value)
  return (out + 512) >> 10 if out is not None else None


def _nested_metrics(metrics: Any) -> Any:
  return getattr(metrics, "SmuMetrics", metrics)


def _array_value(values: Any, index: int | None) -> Any:
  if index is None:
    return None
  try:
    return values[index]
  except (IndexError, KeyError, TypeError):
    return None


def _safe_constant(module: Any, name: str) -> int | None:
  value = getattr(module, name, None)
  return int(value) if isinstance(value, int) else None


def normalize_smu_metrics(*, metrics: Any, smu_mod: Any, ip_version: tuple[int, int, int], ppt_limit_w: Any = None) -> dict[str, Any]:
  """Normalize the current tinygrad AMD SMU table into explicit-unit fields.

  MI300-style SMU 13.0.6/13.0.12 tables use Q10 fixed-point fields while
  consumer RDNA tables expose SmuMetrics with integer engineering units.
  This mirrors the distinctions already present in tinygrad's am_smi tool.
  """
  if metrics is None:
    return {}

  special_q10 = ip_version in {(13, 0, 6), (13, 0, 12)}
  if special_q10:
    hotspot = _q10(getattr(metrics, "MaxSocketTemperature", None))
    memory = _q10(getattr(metrics, "MaxHbmTemperature", None))
    power = _q10(getattr(metrics, "SocketPower", None))
    if ip_version == (13, 0, 6):
      table_limit = _q10(getattr(metrics, "MaxSocketPowerLimit", None))
    else:
      table_limit = _q10(getattr(metrics, "SocketPowerLimit", None))
    usage = _q10(getattr(metrics, "SocketGfxBusy", None))
    try:
      clock_raw = getattr(metrics, "GfxclkFrequency")[0]
    except (AttributeError, IndexError, TypeError):
      clock_raw = None
    clock = _q10(clock_raw)
    fan = None
  else:
    m = _nested_metrics(metrics)
    temp_hotspot = _safe_constant(smu_mod, "TEMP_HOTSPOT")
    temp_mem = _safe_constant(smu_mod, "TEMP_MEM")
    hotspot = _finite_float(_array_value(getattr(m, "AvgTemperature", None), temp_hotspot))
    memory = _finite_float(_array_value(getattr(m, "AvgTemperature", None), temp_mem))
    power = _finite_float(getattr(m, "AverageSocketPower", None))
    table_limit = _finite_float(getattr(m, "dGPU_W_MAX", None))
    usage = _finite_float(getattr(m, "AverageGfxActivity", None))
    busy_threshold = 5.0 if ip_version == (14, 0, 2) else 15.0
    use_post = usage is not None and usage <= busy_threshold
    clock = _finite_float(getattr(m, "AverageGfxclkFrequencyPostDs" if use_post else "AverageGfxclkFrequencyPreDs", None))
    if clock in (None, 0.0):
      clock = _finite_float(getattr(m, "AverageGfxclkFrequencyPostDs", None))
    fan = _finite_float(getattr(m, "AvgFanRpm", None))

  limit = _finite_float(ppt_limit_w)
  if limit is None or limit <= 0:
    limit = _finite_float(table_limit)

  out = {
    "tempC": hotspot,
    "memoryTempC": memory,
    "powerDrawW": power,
    "powerLimitW": limit,
    "gpuUsagePercent": usage,
    "gpuClockMhz": clock,
    "fanSpeedRpm": fan,
  }
  return {k: v for k, v in out.items() if v is not None}


def select_metrics_table(smu: Any, ip_version: tuple[int, int, int]) -> Any:
  module = smu.smu_mod
  if ip_version == (13, 0, 6):
    return module.MetricsTableV0_t
  if ip_version == (13, 0, 12):
    return module.MetricsTable_t
  return module.SmuMetricsExternal_t


def read_smu_snapshot(dev: Any) -> dict[str, Any]:
  """Read only SMU metrics from an AMD device already opened by modeld."""
  from tinygrad.runtime.autogen.am import am

  dev_impl = dev.iface.dev_impl
  smu = dev_impl.smu
  ip_version = tuple(dev_impl.ip_ver[am.MP1_HWIP])
  table_t = select_metrics_table(smu, ip_version)
  metrics = smu.read_table(table_t, smu.smu_mod.SMU_TABLE_SMU_METRICS)

  ppt_limit = None
  msg = getattr(smu.smu_mod, "PPSMC_MSG_GetPptLimit", None)
  if isinstance(msg, int):
    try:
      ppt_limit = smu._send_msg(msg, 0, read_back_arg=True, timeout=100)
    except Exception:
      ppt_limit = None

  return normalize_smu_metrics(metrics=metrics, smu_mod=smu.smu_mod, ip_version=ip_version, ppt_limit_w=ppt_limit)


def read_supply_snapshot(*, timeout_ms: int = 150) -> dict[str, Any]:
  """Read Carrot bridge voltage/current/fault under its interprocess USB lock."""
  from openpilot.common.usbgpu_bus_lock import usbgpu_bus_lock
  from openpilot.system.hardware.usbgpu import get_usbgpu_device, get_usbgpu_power_status, is_current_usbgpu_firmware

  device = get_usbgpu_device()
  result: dict[str, Any] = {}
  if device is not None:
    result.update({
      "usbSpeedMbps": int(device.speed_mbps),
      "usbLinkErrorCount": int(device.link_error_count),
      "firmwareCurrent": bool(is_current_usbgpu_firmware(device.product)),
    })

  with usbgpu_bus_lock():
    power = get_usbgpu_power_status(timeout_ms=timeout_ms)
  if power is not None:
    result.update({
      "supplyVoltageMv": int(power.voltage_mv),
      "supplyCurrentMa": int(power.current_ma),
      "supplyFault": bool(power.fault),
    })
  return result


def read_pcie_snapshot(dev: Any) -> dict[str, Any]:
  """Read the USB bridge PCIe LTSSM byte without changing link state."""
  from openpilot.common.usbgpu_bus_lock import usbgpu_bus_lock

  try:
    pci_dev = dev.iface.pci_dev
    bridge = pci_dev.usb
    with usbgpu_bus_lock():
      value = bridge.read(0xB450, 1)[0]
    return {"pcieLtssm": int(value)}
  except Exception:
    return {}


def collect_hardware_snapshot() -> dict[str, Any]:
  """Collect one read-only snapshot without causing the AMD device to open."""
  now = time.monotonic()
  payload: dict[str, Any] = {
    "schemaVersion": 1,
    "timestampMonoS": now,
    "valid": False,
    "amdOpened": False,
  }
  errors: list[str] = []

  try:
    from tinygrad.device import Device
    opened = getattr(Device, "_opened_devices", {})
    if "AMD" in opened:
      payload["amdOpened"] = True
      dev = Device["AMD"]
      try:
        payload.update(read_smu_snapshot(dev))
      except Exception as exc:
        errors.append(f"smu:{type(exc).__name__}")
      try:
        payload.update(read_pcie_snapshot(dev))
      except Exception as exc:
        errors.append(f"pcie:{type(exc).__name__}")
  except Exception as exc:
    errors.append(f"tinygrad:{type(exc).__name__}")

  try:
    payload.update(read_supply_snapshot())
  except Exception as exc:
    errors.append(f"supply:{type(exc).__name__}")

  payload["valid"] = bool(payload.get("amdOpened")) and any(k in payload for k in ("tempC", "powerDrawW", "gpuUsagePercent"))
  payload["errors"] = errors
  payload["sampleDurationMs"] = max(0.0, (time.monotonic() - now) * 1000.0)
  return payload


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
  path.parent.mkdir(parents=True, exist_ok=True)
  fd, tmp_name = tempfile.mkstemp(prefix=".egpu-hw-", suffix=".json", dir=path.parent)
  try:
    with os.fdopen(fd, "w", encoding="utf-8") as f:
      json.dump(payload, f, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
      f.write("\n")
      f.flush()
      os.fsync(f.fileno())
    os.replace(tmp_name, path)
  finally:
    try:
      os.unlink(tmp_name)
    except FileNotFoundError:
      pass


@dataclass
class TelemetryStatus:
  samples: int = 0
  errors: int = 0
  last_error: str | None = None


class EgpuHardwareTelemetry:
  """Low-rate background worker; its output is never consumed by controls."""
  def __init__(self, *, enabled: bool | None = None, state_path: str | Path | None = None,
               interval_s: float = DEFAULT_INTERVAL_S):
    self.enabled = (_env_enabled() or ENABLE_MARKER.is_file()) if enabled is None else bool(enabled)
    self.state_path = Path(state_path) if state_path is not None else Path(os.getenv("EGPU_INTEGRATED_HARDWARE_STATE_PATH", str(DEFAULT_STATE_PATH)))
    self.interval_s = max(0.5, float(interval_s))
    self.status = TelemetryStatus()
    self._stop = threading.Event()
    self._thread: threading.Thread | None = None

  def start(self) -> None:
    if not self.enabled or self._thread is not None:
      return
    self._thread = threading.Thread(target=self._run, name="egpu-hardware-telemetry", daemon=True)
    self._thread.start()

  def stop(self, timeout: float = 1.0) -> None:
    self._stop.set()
    thread = self._thread
    if thread is not None and thread.is_alive():
      thread.join(max(0.0, timeout))

  def _run(self) -> None:
    while not self._stop.is_set():
      started = time.monotonic()
      try:
        payload = collect_hardware_snapshot()
        self.status.samples += 1
        _atomic_write_json(self.state_path, payload)
      except Exception as exc:
        self.status.errors += 1
        self.status.last_error = type(exc).__name__
      elapsed = time.monotonic() - started
      self._stop.wait(max(0.05, self.interval_s - elapsed))
