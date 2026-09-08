#!/usr/bin/env python3
"""Apply or verify the reviewed S1+S2+S4B modeld integration to carrot-wip.

This script is intentionally strict:
- only the reviewed carrot-wip modeld blob is accepted
- partial marker states are rejected
- removing every integration block must restore the exact original Git blob
- existing Carrot model execution, same-frame fallback, modelV2 publication,
  controls, car interfaces, and panda safety are not replaced
"""
from __future__ import annotations

from pathlib import Path
import py_compile
import subprocess

TARGET = Path("openpilot/selfdrive/modeld/modeld.py")
PINNED_BLOB = "e4de3eb2236f6bb0c54c87666099147311602f4f"

BLOCKS = (
  ("# EGPU-INTEGRATED OBSERVER IMPORT BEGIN", "# EGPU-INTEGRATED OBSERVER IMPORT END"),
  ("# EGPU-INTEGRATED TELEMETRY IMPORT BEGIN", "# EGPU-INTEGRATED TELEMETRY IMPORT END"),
  ("# EGPU-INTEGRATED SHADOW TAP IMPORT BEGIN", "# EGPU-INTEGRATED SHADOW TAP IMPORT END"),
  ("# EGPU-INTEGRATED OBSERVER INIT BEGIN", "# EGPU-INTEGRATED OBSERVER INIT END"),
  ("# EGPU-INTEGRATED TELEMETRY INIT BEGIN", "# EGPU-INTEGRATED TELEMETRY INIT END"),
  ("# EGPU-INTEGRATED SHADOW TAP INIT BEGIN", "# EGPU-INTEGRATED SHADOW TAP INIT END"),
  ("# EGPU-INTEGRATED SHADOW TAP SEND BEGIN", "# EGPU-INTEGRATED SHADOW TAP SEND END"),
  ("# EGPU-INTEGRATED OBSERVER ATTEMPT BEGIN", "# EGPU-INTEGRATED OBSERVER ATTEMPT END"),
  ("# EGPU-INTEGRATED OBSERVER FALLBACK BEGIN", "# EGPU-INTEGRATED OBSERVER FALLBACK END"),
  ("# EGPU-INTEGRATED OBSERVER SAMPLE BEGIN", "# EGPU-INTEGRATED OBSERVER SAMPLE END"),
)

IMPORT_BLOCK = """# EGPU-INTEGRATED OBSERVER IMPORT BEGIN
from openpilot.selfdrive.modeld.egpu_integration_observer import EgpuIntegrationObserver
# EGPU-INTEGRATED OBSERVER IMPORT END
# EGPU-INTEGRATED TELEMETRY IMPORT BEGIN
from openpilot.selfdrive.modeld.egpu_hardware_telemetry import EgpuHardwareTelemetry
# EGPU-INTEGRATED TELEMETRY IMPORT END
# EGPU-INTEGRATED SHADOW TAP IMPORT BEGIN
from openpilot.selfdrive.modeld.egpu_integrated_shadow_tap import IntegratedShadowTap
# EGPU-INTEGRATED SHADOW TAP IMPORT END
"""

INIT_BLOCK = """  # EGPU-INTEGRATED OBSERVER INIT BEGIN
  egpu_observer = EgpuIntegrationObserver()
  # EGPU-INTEGRATED OBSERVER INIT END
  # EGPU-INTEGRATED TELEMETRY INIT BEGIN
  egpu_hardware_telemetry = EgpuHardwareTelemetry()
  egpu_hardware_telemetry.start()
  # EGPU-INTEGRATED TELEMETRY INIT END
  # EGPU-INTEGRATED SHADOW TAP INIT BEGIN
  egpu_integrated_shadow_tap = IntegratedShadowTap()
  # EGPU-INTEGRATED SHADOW TAP INIT END
"""

SEND_BLOCK = """    # EGPU-INTEGRATED SHADOW TAP SEND BEGIN
    if not prepare_only:
      egpu_integrated_shadow_tap.send(
        model=model,
        meta_main=meta_main,
        meta_extra=meta_extra,
        state_frame_id=frame_id,
        v_ego=v_ego,
        car_state=sm[\"carState\"],
        car_control=sm[\"carControl\"],
        transform_main=model_transform_main,
        transform_extra=model_transform_extra,
        inputs=inputs,
      )
    # EGPU-INTEGRATED SHADOW TAP SEND END
"""

ATTEMPT_BLOCK = """    # EGPU-INTEGRATED OBSERVER ATTEMPT BEGIN
    egpu_attempted_backend = \"egpu\" if bool(getattr(model, \"usbgpu\", False)) else \"qcom\"
    # EGPU-INTEGRATED OBSERVER ATTEMPT END
"""

FALLBACK_BLOCK = """      # EGPU-INTEGRATED OBSERVER FALLBACK BEGIN
      egpu_observer.note_fallback(frame_id=meta_main.frame_id, reason=\"runtime_model_execution_failed\")
      # EGPU-INTEGRATED OBSERVER FALLBACK END
"""

SAMPLE_BLOCK = """    # EGPU-INTEGRATED OBSERVER SAMPLE BEGIN
    egpu_observer.observe(
      frame_id=meta_main.frame_id,
      state_frame_id=frame_id,
      attempted_backend=egpu_attempted_backend,
      active_backend=\"egpu\" if bool(getattr(model, \"usbgpu\", False)) else \"qcom\",
      model_execution_s=model_execution_time,
    )
    # EGPU-INTEGRATED OBSERVER SAMPLE END
"""

RUNTIMES = (
  "openpilot/selfdrive/modeld/egpu_integration_observer.py",
  "openpilot/selfdrive/modeld/egpu_hardware_telemetry.py",
  "openpilot/selfdrive/modeld/egpu_integrated_shadow_tap.py",
  "openpilot/selfdrive/modeld/egpu_integrated_model_slots.py",
  "openpilot/selfdrive/modeld/egpu_integrated_guardian.py",
  "tools/egpu_integrated_s4b_shadow_probe.py",
)

LANDMARKS = (
  'model_output = model.run(bufs, transforms, inputs, prepare_only)',
  'cloudlog.exception("eGPU model failed, falling back to internal GPU")',
  'model = small_model',
  "pm.send('modelV2', modelv2_send)",
  'sm = SubMaster(["deviceState", "carState"',
)


def git_hash(path: Path) -> str:
  p = subprocess.run(["git", "hash-object", str(path)], text=True, capture_output=True, check=True)
  return p.stdout.strip()


def git_hash_text(source: str) -> str:
  # Text-mode stdin translates LF to CRLF on Windows, corrupting the exact
  # reviewed Git-blob check even when marker removal restored the source.
  p = subprocess.run(["git", "hash-object", "--stdin"], input=source.encode("utf-8"), capture_output=True, check=True)
  return p.stdout.decode("ascii").strip()


def insert_once(source: str, anchor: str, replacement: str, name: str) -> str:
  n = source.count(anchor)
  if n != 1:
    raise RuntimeError(f"expected exactly one {name} anchor, found {n}")
  return source.replace(anchor, replacement, 1)


def strip_block(source: str, begin: str, end: str) -> str:
  a = source.find(begin)
  if a < 0:
    return source
  b = source.find(end, a)
  if b < 0:
    raise RuntimeError(f"unterminated marker block: {begin}")
  b += len(end)
  line_start = source.rfind("\n", 0, a) + 1
  if b < len(source) and source[b] == "\n":
    b += 1
  return source[:line_start] + source[b:]


def strip_all(source: str) -> str:
  for begin, end in BLOCKS:
    source = strip_block(source, begin, end)
  return source


def marker_count(source: str) -> int:
  return sum(source.count(begin) for begin, _ in BLOCKS)


def validate_patched(source: str) -> str:
  count = marker_count(source)
  if count != len(BLOCKS):
    raise SystemExit(f"incomplete marker set: {count}/{len(BLOCKS)}")
  restored_blob = git_hash_text(strip_all(source))
  if restored_blob != PINNED_BLOB:
    raise SystemExit(f"stripped modeld blob mismatch got={restored_blob} expected={PINNED_BLOB}")
  for landmark in LANDMARKS:
    if landmark not in source:
      raise SystemExit(f"required Carrot landmark missing: {landmark}")
  py_compile.compile(str(TARGET), doraise=True)
  for runtime in RUNTIMES:
    py_compile.compile(runtime, doraise=True)
  return restored_blob


def patch(source: str) -> str:
  if marker_count(source):
    if marker_count(source) == len(BLOCKS):
      return source
    raise RuntimeError("partial eGPU integrated marker set detected")

  import_anchor = "from openpilot.selfdrive.modeld.constants import ModelConstants, Plan\n"
  source = insert_once(source, import_anchor, import_anchor + IMPORT_BLOCK, "import")

  init_anchor = "  params = Params()\n  usbgpu_pkl_path = usbgpu_compiled_path()\n"
  source = insert_once(source, init_anchor,
                       "  params = Params()\n" + INIT_BLOCK + "  usbgpu_pkl_path = usbgpu_compiled_path()\n",
                       "initialization")

  run_anchor = "    mt1 = time.perf_counter()\n    try:\n      model_output = model.run(bufs, transforms, inputs, prepare_only)\n"
  source = insert_once(source, run_anchor, SEND_BLOCK + ATTEMPT_BLOCK + run_anchor, "model run")

  fallback_anchor = (
    "      model = small_model\n"
    "      run_count = 0\n"
    "      # Run the already-loaded internal model for this same camera frame. A\n"
  )
  source = insert_once(source, fallback_anchor,
                       "      model = small_model\n      run_count = 0\n" + FALLBACK_BLOCK +
                       "      # Run the already-loaded internal model for this same camera frame. A\n",
                       "same-frame fallback")

  sample_anchor = "    mt2 = time.perf_counter()\n    model_execution_time = mt2 - mt1\n\n    if model_output is not None:\n"
  source = insert_once(source, sample_anchor,
                       "    mt2 = time.perf_counter()\n    model_execution_time = mt2 - mt1\n" + SAMPLE_BLOCK +
                       "\n    if model_output is not None:\n",
                       "execution sample")
  return source


def main() -> int:
  source = TARGET.read_text(encoding="utf-8")
  count = marker_count(source)
  if count == len(BLOCKS):
    restored_blob = validate_patched(source)
    print(f"alreadyPatched=true\nmarkers={count}\nrestoredBlob={restored_blob}\nbyteRestoreVerification=PASS")
    return 0
  if count:
    raise SystemExit(f"partial marker set before patch: {count}/{len(BLOCKS)}")

  blob = git_hash(TARGET)
  if blob != PINNED_BLOB:
    raise SystemExit(f"modeld blob mismatch got={blob} expected={PINNED_BLOB}")

  patched = patch(source)
  if strip_all(patched) != source:
    raise SystemExit("marker removal did not restore original modeld.py byte-for-byte")
  TARGET.write_text(patched, encoding="utf-8")
  restored_blob = validate_patched(patched)
  print(f"patched=true\noriginalBlob={blob}\nmarkers={len(BLOCKS)}\nrestoredBlob={restored_blob}\nbyteRestoreVerification=PASS")
  return 0


if __name__ == "__main__":
  raise SystemExit(main())
