# Selective offline upstream review — 2026-09-08

## Fact: scope and source identities

The user requested the next work after PR #14 was held by newly detected
upstream changes. This review decides how those changes relate to the existing
offline-only integration. It does not authorize vehicle deployment or change
steering/braking authority, Params, manager, Panda safety, or live CAN behavior.

| Role | Commit |
| --- | --- |
| Integrated v6 at review start | `cbce1250f5e58302844bfeb03c4e1de9a5a2e6b3` |
| PR #14 development/retained source | `8997be8b7cbc9f8b978ef942b7c9d577bf76f790` |
| Previously reviewed upstream | `a92d3a787e84a29949ca2b802f6a78fc9b580e87` |
| Tesla upstream delta | `20c7bb7152aaa50998ae44867904e59a6595b761` |
| Reviewed radar upstream delta | `b7ab68addc29c3b9dc8f02023de36dba7109ed8c` |

These are review snapshots. The final PR and merge commit are reported with
their exact CI results in the completion report. The original fork's
`carrot-wip` branch is preserved.

## Fact: all 21 upstream paths have a disposition

The complete `a92d3a..b7ab68a` change contains 21 paths. Their previous,
reviewed-remote and retained/integrated Git objects, modes and types are
recorded in `config/egpu_integrated_upstream_disposition.json`.

### Fourteen Tesla paths retained

All 14 paths from the Tesla series remain exactly at the previous integrated
source, which equals their `a92d3a` versions. This includes the corresponding
settings and Korean/English documentation. Two upstream-only tests remain
explicitly absent, rather than testing unimported features.

The excluded change affects Tesla ACC cancellation pulses, ESP-based standstill
detection, LKAS-conflict interpretation, the lateral-control standstill gate,
and Panda wake/ignition separation with gear, seatbelt, door and CAN-counter
logic. These are actual behavior changes. Their tests were inspected to
understand intent; they do not establish vehicle safety or integrated support.

Retaining the old code also retains its old limitations. This branch does not
claim the new upstream Tesla engagement, cancellation or wake fixes. The
entire local `opendbc`, `controls` and `panda` trees are additionally required
to match the retained source. This preserves the local Hyundai radar DBC and
H1 observation-only additions without introducing a CAN/control update.

### Six radar paths selectively integrated

The independent `20c7bb7..b7ab68a` radar change is applied to:

- `openpilot/selfdrive/carrot/radar/tools/radar_web_export.py`
- `tools/carrot_route_vault/radar_view.js`
- `tools/carrot_route_vault/viewer.py`
- `tools/carrot_route_vault/tests/test_radar.py`
- `tools/carrot_route_vault/tests/test_viewer.py`
- `tools/carrot_route_vault/README.md`

The exporter reuses the desktop reviewer's continuity series for lead distance,
lead speed, SCC distance/acceleration and Carrot target acceleration. It retains
the integrated source hash coverage for Python, capnp and DBC files, stable
POSIX ordering, regular-file selection and complete committed source bundles.

The web viewer fixes replay sensitivity to the existing upstream value 3, and
uses a common video/radar timeline with separate distance/speed and acceleration
plots. This affects offline replay analysis only. It neither changes recorded
lead decisions nor changes vehicle settings.

Local review additionally prevents graph lines from spanning null/unmapped
samples. A short aligned video can hand off to the remaining radar timeline
with an explicit video-ended notice. A longer video pauses at the end of
paired radar evidence. Seeking and segment changes preserve or reset playback
state intentionally; these transitions have browser-logic regression tests.

### Repository instructions retained

`AGENTS.md` remains at the previous integrated value. The upstream addition
requiring a running replay-service deployment is not imported. This task does
not deploy the route-vault service, access the NAS, publish route data, or
change the user's original/model branches. The route-vault README records the
offline integration boundary and the need for a separately authorized service
deployment.

## Fact: compatibility is explicitly scoped

The compatibility command still fetches the current upstream. Its reviewed
remote reference can advance to `b7ab68a` only together with the complete
disposition and the actual selected-source commit. It verifies:

1. The three historical review commits and selected-source commit are real
   local Git commits; every recorded old/remote/integrated object matches them.
2. The disposition covers the complete 21-path reviewed delta once, with the
   fixed permitted selection/exclusion categories and explicit absences.
3. Local HEAD, index and working bytes match the reviewed selected or retained
   objects. A committed/unchanged disposition record is required.
4. Local protected trees remain at the retained control source. This is an
   equality constraint, not a blanket exemption for future modifications.
5. Fresh upstream changes to existing watched surfaces, any disposition path,
   the controls tree, Panda board tree or route-vault tree require review.
   Added and deleted nested sources are included in tree comparisons.

Success is `REVIEWED_WITH_EXCLUSIONS`, with `upstreamFullyIntegrated=false`
and `verificationScope=WATCHED_BOUNDARIES_ONLY`. `overallCompatible` describes
this reviewed selective integration only, not full upstream equality or a
vehicle/runtime safety verdict. Known differences are reported individually;
they are not silently skipped. Unrelated head drift retains the existing
watched-path comparison semantics.

## Fact: validation plan and limits

Validation includes the existing complete hardware-free suite and 29 synthetic
fault scenarios; committed review-bundle CLI creation/recalculation/tamper
rejection; mutation/deletion/absence and source-disposition tests; all route
viewer Python tests; desktop continuity/graph calculations; and browser timeline
and graph behavior using a deterministic DOM/canvas fixture.

The new pytest steps use `--noconftest` to avoid the repository-wide device,
Params and manager fixtures. Native desktop-window construction is outside
these headless tests; the Windows runtime has no `pyray`. Neither a browser
logic fixture nor a static/synthetic PASS proves native rendering, actual
video timing, a deployed replay service, or vehicle behavior.

PR CI must pass on the final development head before merge. A future upstream
change or local protected-source change can hold the integration again; the
review record must not be rewritten merely to clear the gate.

## Pending hardware validation

The actual comma checkout, live branch, Params, manager and vehicle CAN/control
path are untouched. Model execution and producer assertions are not verified.
All commissioning, runtime, control and public-road authorizations remain false.
S2B stays plan-only until real S2A hardware PASS; no S4C 20 Hz runner is added.
Existing S4B parked <=5 Hz and readiness restrictions remain unchanged.
