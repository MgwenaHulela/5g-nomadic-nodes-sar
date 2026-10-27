#!/usr/bin/env python3
"""
auto_patrol.py — Continuous robot patrol + metrics logger.

Moves ground robots along a path, runs periodic iperf bursts,
and logs distance/ploss/throughput to CSV.

Modes:
  linear (default)    — single alpha robot, out-and-back sweep
  random              — single alpha robot, 2m random walk
  both                — alpha + bravo, independent 2m random walks
  free                — alpha + bravo roam continuously (smooth headings,
                        no teleport steps); rows tagged with the position
                        at the midpoint of each measurement window
  research            — two-phase long run (default 6h = 3h + 3h): the aerial
                        gNB (sar_uav) holds position for phase 1, then does a
                        continuous 3D random walk for phase 2, while both robots
                        free-patrol throughout. A watchdog reattaches ONLY a UE
                        stuck in sticky RLF (no full RAN restart). Rows carry
                        `phase` and per-robot `event`/`outage_s`.

Usage:
  python3 scripts/auto_patrol.py                         # linear 10→200m, 180s
  python3 scripts/auto_patrol.py --random                 # random walk, 2m steps
  python3 scripts/auto_patrol.py --both                   # both robots, random
  python3 scripts/auto_patrol.py --free --speed=1.5       # continuous roam
  python3 scripts/auto_patrol.py --research               # 6h: 3h static + 3h mobile gNB
  python3 scripts/auto_patrol.py --research --duration=1800 --phase1=900   # 30min: 15+15
  python3 scripts/auto_patrol.py --research --seed=42 --run-id=forest_s42    # repeatable motion
  python3 scripts/auto_patrol.py --excite --duration=3600 --seed=7          # 1h excitation run

  python3 scripts/auto_patrol.py --duration=300           # 5 min
  python3 scripts/auto_patrol.py --step=2                 # 2m per waypoint

Requires: start_full_stack.sh already running.
"""

import csv
import json
import math
import os
import random
import re
import signal
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

LOG_DIR = Path.home() / "sar_sim" / "logs"
PATROL_LOG = LOG_DIR / f"patrol_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"

sys.path.insert(0, str(Path.home() / "sar_sim" / "scripts"))
from metrics_collector import tail_file, parse_ue_metrics
from traffic_streams import TrafficStreams

sys.path.insert(0, "/usr/lib/python3/dist-packages")
from gz.transport13 import Node as GzNode
from gz.msgs10.pose_pb2 import Pose
from gz.msgs10.boolean_pb2 import Boolean
from gz.msgs10.twist_pb2 import Twist
from gz.msgs10.pose_v_pb2 import Pose_V

# Having imported the Gazebo bindings, demote dist-packages to the END of the
# path. It ships numpy 1.21.5, which otherwise shadows the user's 1.26.4 and
# makes any later `import pandas` fail with the thoroughly misleading
# "C extension: None not built" -- that is what silently reduced the first
# planner fly-off to an excitation run. gz resolves fine from the tail of the
# path; only the shadowing is removed.
_DIST = "/usr/lib/python3/dist-packages"
if _DIST in sys.path:
    sys.path.remove(_DIST)
    sys.path.append(_DIST)

def _detect_world():
    """SAR_WORLD if set, else auto-detect the running gz world from its
    pose topic (so `auto_patrol.py` works without remembering the env var
    when the stack is already up), else the open-field default."""
    env = os.environ.get("SAR_WORLD")
    if env:
        return env
    try:
        out = subprocess.run(["gz", "topic", "-l"], capture_output=True,
                             text=True, timeout=5).stdout
        m = re.search(r"/world/([^/]+)/pose/info", out)
        if m:
            return m.group(1)
    except Exception:
        pass
    return "sar_open_field"


WORLD = _detect_world()
UAV_X = 0.0
UAV_Y = 0.0
UAV_Z = 40.0
GROUND_Z = 0.0  # burger base_footprint is its ground-contact point — 0.1 floats it
TX_REF = -42.0       # recalibrated 2026-07-11: measured SS-RSRP = -42 - DL attenuation
DL_UL_OFFSET = 5.0   # DL baseline is 5 dB below UL baseline (from bridge config)

# ── Robot roster ─────────────────────────────────────────────────────────────
# One entry per robot. Order is significant: it fixes the per-robot column
# order in every CSV and the order robots are probed each cycle.
#
# SAR_ROBOTS overrides the roster, so the published 2-robot SATNAC
# configuration stays reproducible:
#     SAR_ROBOTS=alpha,bravo python3 scripts/auto_patrol.py --research
#
# tun:     OAI names a UE's interface oaitun_ue1p<N> where N is the PDU
#          session index. Each nrue_*.conf pads with N-1 dummy sessions so
#          its real "internet" session lands on a distinct interface —
#          that is how 4 UEs share one host without colliding on oaitun_ue1.
# ue_ip:   assigned by the core from the 10.45.0.0/16 pool, in attach order.
# test_ip: per-robot iperf3 server in the root namespace.
# col:     CSV column prefix (a_, b_, c_, d_).
ROBOT_SPEC = {
    "alpha":   dict(model="sar_ground_alpha",   tun="oaitun_ue1",
                    ns="robot_alpha",   ue_ip="10.45.0.2", test_ip="10.0.0.200",
                    col="a", log="/tmp/ue_alpha.log",   start=(10.0, 0.0),
                    ul_tag="gnb_ue0_alpha_ul"),
    "bravo":   dict(model="sar_ground_bravo",   tun="oaitun_ue1p2",
                    ns="robot_bravo",   ue_ip="10.45.0.3", test_ip="10.0.0.201",
                    col="b", log="/tmp/ue_bravo.log",   start=(25.0, 0.0),
                    ul_tag="gnb_ue1_bravo_ul"),
    "charlie": dict(model="sar_ground_charlie", tun="oaitun_ue1p3",
                    ns="robot_charlie", ue_ip="10.45.0.4", test_ip="10.0.0.202",
                    col="c", log="/tmp/ue_charlie.log", start=(-10.0, 15.0),
                    ul_tag="gnb_ue2_charlie_ul"),
    "delta":   dict(model="sar_ground_delta",   tun="oaitun_ue1p4",
                    ns="robot_delta",   ue_ip="10.45.0.5", test_ip="10.0.0.203",
                    col="d", log="/tmp/ue_delta.log",   start=(-25.0, -15.0),
                    ul_tag="gnb_ue3_delta_ul"),
}
ROBOTS = [r.strip() for r in
          os.environ.get("SAR_ROBOTS", "alpha,bravo,charlie,delta").split(",")
          if r.strip()]
for _r in ROBOTS:
    if _r not in ROBOT_SPEC:
        sys.exit(f"ERROR: unknown robot '{_r}' in SAR_ROBOTS — "
                 f"known: {', '.join(ROBOT_SPEC)}")

MODELS      = [ROBOT_SPEC[r]["model"] for r in ROBOTS]
KEY_OF      = {ROBOT_SPEC[r]["model"]: r for r in ROBOTS}
SPEC_OF     = {ROBOT_SPEC[r]["model"]: ROBOT_SPEC[r] for r in ROBOTS}

# Back-compat aliases. The legacy sweep/walk/search modes still address two
# robots by name; keeping these means the roster change cannot silently break
# them. They are only valid when alpha/bravo are in the roster.
ALPHA_MODEL = ROBOT_SPEC["alpha"]["model"]
BRAVO_MODEL = ROBOT_SPEC["bravo"]["model"]
ALPHA_TUN = ROBOT_SPEC["alpha"]["tun"]
BRAVO_TUN = ROBOT_SPEC["bravo"]["tun"]
ALPHA_IP = ROBOT_SPEC["alpha"]["test_ip"]
BRAVO_IP = ROBOT_SPEC["bravo"]["test_ip"]

# Per-robot network namespaces (setup_robot_netns.sh). When active, the UE
# TUNs live inside these namespaces, so uplink tests must run there — and
# the UE IPs are reachable from the root ns ONLY over the air, which is
# what makes the C2/downlink metric honest.
NS_ALPHA = ROBOT_SPEC["alpha"]["ns"]
NS_BRAVO = ROBOT_SPEC["bravo"]["ns"]
ALPHA_UE_IP = ROBOT_SPEC["alpha"]["ue_ip"]
BRAVO_UE_IP = ROBOT_SPEC["bravo"]["ue_ip"]
# netns is considered active only if EVERY rostered robot has one — a partial
# setup would make some robots' C2 measurements loopback and silently honest-
# looking, which is worse than no measurement at all.
NETNS_MODE = all(Path(f"/var/run/netns/{ROBOT_SPEC[r]['ns']}").exists()
                 for r in ROBOTS)
SUDO_PASS = os.environ.get("SAR_SUDO_PASS", "")  # export SAR_SUDO_PASS; never hardcode


def ns_run(ns, cmd, timeout):
    """Run cmd inside a robot namespace (or directly if netns not set up).

    The command is wrapped in coreutils `timeout` so the REAL process dies on
    overrun. subprocess.run's own timeout kills only the direct child — which
    here is `sudo` — leaving the grandchild (iperf3, ping) orphaned and still
    hammering the link. Observed 2026-10-19 on a 4-robot indoor run: three
    separate 2-second iperf3 tests still alive after 8 minutes, stacked on one
    robot, each one slowing every subsequent cycle until the measurement loop
    managed a single cycle in 6.5 minutes. Over a multi-day campaign that
    leak compounds without bound.

    The inner timeout fires 1 s before the outer one so the kill happens
    inside the namespace, where the process actually lives.
    """
    inner = max(1, int(timeout) - 1)
    wrapped = ["timeout", "-k", "2", str(inner)] + cmd
    if NETNS_MODE:
        return subprocess.run(
            ["sudo", "-S", "-p", "", "ip", "netns", "exec", ns] + wrapped,
            capture_output=True, text=True, timeout=timeout,
            input=SUDO_PASS + "\n",
        )
    return subprocess.run(wrapped, capture_output=True, text=True, timeout=timeout)


def sudo_run(cmd, timeout):
    """Run a root command via `sudo -S`, feeding SAR_SUDO_PASS on stdin.
    Used by the research-mode watchdog to clear stale TUNs and re-home a
    freshly reattached UE's TUN into its robot netns."""
    return subprocess.run(
        ["sudo", "-S", "-p", ""] + cmd,
        capture_output=True, text=True, timeout=timeout,
        input=SUDO_PASS + "\n",
    )
ALPHA_UE_LOG = "/tmp/ue_alpha.log"
BRAVO_UE_LOG = "/tmp/ue_bravo.log"

IPERF_DURATION = 2
IPERF_INTERVAL = 0

# Simulated sensor/video uplink feed rate (~1080p camera stream). Offered
# over the air UE->network; at close range it should arrive nearly intact,
# at range the loss%/jitter columns become the video-QoS degradation signal.
VIDEO_BITRATE_MBPS = 8

# Roam bounds (radius from the UAV's ground track in free mode, from the
# origin in the teleport modes). Overridable via --min-radius= /
# --max-radius=. Coverage edges measured 2026-07-10 at h=40: alpha
# (baseline 20dB) dies ~135m, bravo (baseline 25dB) ~130m — and RLF is
# sticky: walking back does NOT quickly restore the link, and the UE may
# never re-attach without a RAN restart.
MIN_RADIUS = 2
MAX_RADIUS = 150

# ── Obstacle avoidance (urban world) ─────────────────────────────────────────
# Ground robots reactively steer around buildings using the SAME footprint
# geometry the channel model uses (urban_buildings.any_blocked). A short
# forward "whisker" is cast along the desired heading; if it would clip a
# building within the lookahead, the heading is deflected to the first clear
# angle, fanning out symmetrically. Trees are not blockers (clutter only),
# matching the channel model.
try:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "worlds"))
    from urban_buildings import any_blocked as _any_blocked, BUILDINGS as _BUILDINGS
except Exception:
    _any_blocked = None
    _BUILDINGS = []

try:
    from indoor_building import WALLS as _INDOOR_WALLS, OUTER as _INDOOR_OUTER
except Exception:
    _INDOOR_WALLS = []
    _INDOOR_OUTER = None

AVOID_LOOKAHEAD_M = 7.0                      # how far ahead to probe for a wall
AVOID_MARGIN_M = 1.5                         # clearance kept from footprints
AVOID_FAN_DEG = (20, 40, 60, 80, 110, 140)  # deflection angles to try


def _path_clear(x, y, heading, dist=AVOID_LOOKAHEAD_M):
    """True if a straight probe of length `dist` from (x, y) along `heading`
    clears every building footprint (margin included). No geometry (open
    field) -> always clear."""
    if _any_blocked is None:
        return True
    z = GROUND_Z + 0.3
    p0 = (x, y, z)
    p1 = (x + dist * math.cos(heading), y + dist * math.sin(heading), z)
    return not _any_blocked(p0, p1, margin=AVOID_MARGIN_M)


def _away_from_nearest_building(x, y, fallback):
    """Heading pointing away from the nearest building centre — the escape
    direction when a robot is hugging a facade (inside the clearance margin,
    so every probe reads blocked)."""
    if not _BUILDINGS:
        return fallback + math.pi
    cx, cy = min(_BUILDINGS, key=lambda b: (x - b[0])**2 + (y - b[1])**2)[:2]
    return math.atan2(y - cy, x - cx)


def avoid_heading(x, y, desired):
    """Nearest heading to `desired` whose forward path is clear of buildings.
    Returns (heading, blocked): if `desired` is already clear it is returned
    unchanged with blocked=False; otherwise fan out symmetrically and take
    the first clear side (blocked=True so the caller can also slow down).
    If nothing is clear (robot hugging a wall, inside the margin), steer
    directly away from the nearest building to back off."""
    if _any_blocked is None or _path_clear(x, y, desired):
        return desired, False
    for deg in AVOID_FAN_DEG:
        d = math.radians(deg)
        for cand in (desired + d, desired - d):
            if _path_clear(x, y, cand):
                return cand, True
    return _away_from_nearest_building(x, y, desired), True


# ── Indoor wall avoidance (sar_indoor world) ──────────────────────────────────
# The indoor building has thin walls with doorways; the urban avoid_heading()
# doesn't know about them.  Probe a short distance along the desired heading;
# if it crosses any indoor wall segment, fan out to the nearest clear angle.
_IND_PROBE = 1.0   # m — how far to probe ahead (short indoors)
_IND_CLR   = 0.35  # m — minimum clearance from wall centre-line


def _seg_cross(x0, y0, x1, y1, wx, wy, ww, wh):
    """True if the segment (x0,y0)->(x1,y1) crosses wall box (wx,wy,ww,wh)
    with _IND_CLR margin on each side of the wall centre-line."""
    hw, hh = ww / 2 + _IND_CLR, wh / 2 + _IND_CLR
    xmin, xmax = wx - hw, wx + hw
    ymin, ymax = wy - hh, wy + hh
    dx, dy = x1 - x0, y1 - y0
    tmin, tmax = 0.0, 1.0
    for lo, hi, d, s in ((xmin, xmax, dx, x0), (ymin, ymax, dy, y0)):
        if abs(d) < 1e-12:
            if s < lo or s > hi:
                return False
        else:
            t1 = (lo - s) / d
            t2 = (hi - s) / d
            if t1 > t2:
                t1, t2 = t2, t1
            tmin = max(tmin, t1)
            tmax = min(tmax, t2)
            if tmin > tmax:
                return False
    return True


def indoor_avoid(x, y, desired):
    """Like avoid_heading() but for indoor building walls.  Returns
    (heading, blocked)."""
    if not _INDOOR_WALLS:
        return desired, False
    x1 = x + _IND_PROBE * math.cos(desired)
    y1 = y + _IND_PROBE * math.sin(desired)
    blocked = any(_seg_cross(x, y, x1, y1, *w[:4]) for w in _INDOOR_WALLS)
    if not blocked:
        return desired, False
    for deg in (15, 30, 50, 80, 120, 160):
        d = math.radians(deg)
        for cand in (desired + d, desired - d):
            cx = x + _IND_PROBE * math.cos(cand)
            cy = y + _IND_PROBE * math.sin(cand)
            if not any(_seg_cross(x, y, cx, cy, *w[:4]) for w in _INDOOR_WALLS):
                return cand, True
    # every direction blocked — head toward centre of the building footprint
    if _INDOOR_OUTER:
        cx = (_INDOOR_OUTER[0] + _INDOOR_OUTER[1]) / 2
        cy = (_INDOOR_OUTER[2] + _INDOOR_OUTER[3]) / 2
        return math.atan2(cy - y, cx - x), True
    return desired + math.pi, True


def _outside_warehouse(x, y, margin=3.0):
    """If (x, y) is inside the indoor building footprint, project it to the
    nearest point OUTSIDE by `margin` metres.  Returns (x, y) unchanged if
    already outside.  Used by the gNB pilot to keep the drone outside the
    warehouse so the O2I penetration path is real."""
    if not _INDOOR_OUTER:
        return x, y
    xmin, xmax, ymin, ymax = _INDOOR_OUTER
    if not (xmin <= x <= xmax and ymin <= y <= ymax):
        return x, y  # already outside
    # distances to each edge
    dl = x - xmin     # to left
    dr = xmax - x     # to right
    db = y - ymin     # to bottom
    dt = ymax - y     # to top
    closest = min(dl, dr, db, dt)
    if closest == dl:
        return xmin - margin, y
    elif closest == dr:
        return xmax + margin, y
    elif closest == db:
        return x, ymin - margin
    else:
        return x, ymax + margin


# ── A2G path-loss model (mirror of channel_bridge.py — keep in sync) ────────
FREQ_GHZ, MODEL_UE_H, MODEL_REF_H = 3.31968, 0.5, 30.0
A_C, B_C, ETA_LOS, ETA_NLOS = 9.61, 0.16, 1.0, 20.0
BRAVO_UL_BASELINE = 13.0
SAFE_BRAVO_PLOSS_DB = 30.0   # keep worst UE ~5dB under the ~35dB link-death point (recalibrated 2026-07-11, true attenuation)


def _a2g_pl(d_horiz, h_uav):
    d3 = max(math.hypot(d_horiz, h_uav - MODEL_UE_H), 1.0)
    fspl = 20 * math.log10(d3) + 20 * math.log10(FREQ_GHZ * 1e9) - 147.55
    theta = math.degrees(math.atan2(h_uav - MODEL_UE_H, max(d_horiz, 0.1)))
    p_los = 1.0 / (1.0 + A_C * math.exp(-B_C * (theta - A_C)))
    return fspl + p_los * ETA_LOS + (1 - p_los) * ETA_NLOS


_PL_REF = _a2g_pl(50.0, MODEL_REF_H)  # bridge's fixed reference


def safe_radius(h_uav):
    """Largest horizontal distance keeping bravo (worst link budget)
    under SAFE_BRAVO_PLOSS_DB at this UAV altitude. The edge moves with
    altitude: low UAV -> shallow angles -> NLoS excess bites earlier
    (~80m at h=20), high UAV extends it (~145m at h=60)."""
    lo, hi = 5.0, 300.0
    for _ in range(40):
        mid = (lo + hi) / 2
        pl = BRAVO_UL_BASELINE + (_a2g_pl(mid, h_uav) - _PL_REF)
        if pl <= SAFE_BRAVO_PLOSS_DB:
            lo = mid
        else:
            hi = mid
    return lo


# UAV waypoint schedule for --vary-uav: (x, y, z). Dwell per waypoint is
# duration/len(schedule); the UAV glides between them at UAV_SPEED.
# Altitude is varied both at the origin (pure-altitude effect) and at
# offset positions (combined geometry), covering z in {20,40,60}.
UAV_SCHEDULE = [
    (0, 0, 40), (0, 0, 20), (0, 0, 60),
    (40, 30, 40), (-40, 30, 60), (40, -30, 20),
    (-40, -30, 40), (0, 0, 50), (0, 0, 30),
]
UAV_SPEED = 3.0  # m/s glide between waypoints


# ── Research run mode (6h: static-gNB phase, then mobile-gNB phase) ───────────
# The aerial gNB (sar_uav) holds position for RESEARCH_PHASE1_S, then does a
# continuous 3D random walk (never dwelling) for the remainder. Ground robots
# free-patrol the whole time, so the only variable between phases is gNB
# mobility. A background watchdog reattaches any robot stuck in sticky RLF.
RESEARCH_DURATION_S = 6 * 3600
RESEARCH_PHASE1_S = 3 * 3600
GNB_XY_BOUND = 60.0             # gNB random-walk box: x, y in [-60, 60] m
GNB_Z_MIN, GNB_Z_MAX = 20.0, 60.0
GNB_WALK_SPEED = 3.0            # m/s glide toward each fresh random 3D target

# ── Excitation policy (brief 6.1) ────────────────────────────────────────────
# The two-phase research run only ever produces two gNB behaviours: a hold at
# ONE pose (0,0,40) for 3 h, then a continuous random walk for 3 h. That makes
# "hold" perfectly confounded with phase, time-of-run and position, so no model
# can learn what holding would do ANYWHERE ELSE — which is exactly what an
# action-conditioned forecaster (brief 5.4) has to predict.
#
# The excitation policy instead samples a labelled action, commits to it for a
# dwell, and repeats. Holds then occur at arbitrary poses reached by earlier
# moves, climbs and descents are observed from many altitudes, and every action
# is seen across the whole geofence.
#
# Dwell is drawn above the ~22.8 s median measurement cycle so that most
# per-cycle rows sit inside a single action. Exact action boundaries are
# written separately to logs/actions_<run_id>.csv at full resolution, which is
# what the 10 Hz fast-radio log should be joined against.
EXCITE_ACTIONS = ["hold", "step", "climb", "descend", "toward_weak", "away_weak"]
EXCITE_WEIGHTS = [0.30, 0.25, 0.12, 0.12, 0.12, 0.09]
EXCITE_DWELL_S = (30.0, 90.0)   # total seconds per action, glide included
EXCITE_STEP_M = (5.0, 30.0)     # horizontal step length
EXCITE_DZ_M = (3.0, 12.0)       # climb / descend magnitude


def geofence_target(tx, ty, tz, xy_bound=GNB_XY_BOUND,
                    z_min=GNB_Z_MIN, z_max=GNB_Z_MAX, indoor=False):
    """Clamp a candidate gNB target into the operating box.

    Applied to every action so a sampled step can never walk the gNB out of
    the arena or outside the altitude band. Indoor additionally projects the
    drone outside the warehouse footprint, so the outdoor-to-indoor
    penetration path stays real instead of the drone drifting inside."""
    tx = max(-xy_bound, min(xy_bound, tx))
    ty = max(-xy_bound, min(xy_bound, ty))
    tz = max(z_min, min(z_max, tz))
    if indoor:
        tx, ty = _outside_warehouse(tx, ty)
    return tx, ty, tz


def action_to_target(action, pose, rng, weakest_xy=None,
                     step_m=EXCITE_STEP_M, dz_m=EXCITE_DZ_M,
                     bounds=None):
    """Map a labelled action and the current gNB pose to a target pose.

    Shared deliberately: the excitation policy uses it to GENERATE data, and
    a planner (brief 5.4 / 8) needs the identical mapping to evaluate the
    same candidate moves it will later be asked to execute. If the two ever
    disagreed, the forecaster would be trained on one action semantics and
    queried with another.

    `rng` supplies the magnitudes; pass a Random seeded per run for
    reproducibility. Returns (action, (tx, ty, tz)).

    THE RETURNED ACTION IS AUTHORITATIVE — it is what the gNB will actually
    do, which is not always what was asked for. Pass `bounds` as
    (xy_bound, z_min, z_max) and this function keeps the label honest at the
    edge of the operating box:

      * "climb" with no headroom becomes "descend" (and vice versa), rather
        than a climb that cannot move;
      * a "step" whose bearing runs into a wall is reflected off that wall,
        so it is still a real step of the sampled length;
      * "toward_weak"/"away_weak" that the box would flatten to nothing
        become "hold", because that is what they are.

    Without this, a naive clamp pins ~24% of a long excitation walk against
    the altitude bound and ~18% against the xy bound, and every one of those
    samples is a MISLABELLED no-op: the log says "climb", the gNB does not
    move, and an action-conditioned forecaster learns that climbing does
    nothing. (Measured over 20,000 sampled actions, 2026-10-16.)

    The result is still passed through geofence_target by the caller as a
    final safety clamp; this only stops the box from silently corrupting the
    labels.
    """
    x, y, z = pose
    xy_b = z_lo = z_hi = None
    if bounds is not None:
        xy_b, z_lo, z_hi = bounds

    if action == "hold":
        return action, (x, y, z)

    if action in ("climb", "descend"):
        mag = rng.uniform(*dz_m)
        if bounds is not None:
            # Flip toward whichever side has room for the full magnitude.
            if action == "climb" and z + mag > z_hi:
                if z - mag >= z_lo:
                    return "descend", (x, y, z - mag)
            elif action == "descend" and z - mag < z_lo:
                if z + mag <= z_hi:
                    return "climb", (x, y, z + mag)
        return action, (x, y, z + (mag if action == "climb" else -mag))

    if action == "step":
        bearing = rng.random() * 2 * math.pi
        d = rng.uniform(*step_m)
        dx, dy = d * math.cos(bearing), d * math.sin(bearing)
        if bounds is not None:
            # Reflect the offending component off the wall it would cross,
            # preserving the sampled step length.
            if abs(x + dx) > xy_b:
                dx = -dx
            if abs(y + dy) > xy_b:
                dy = -dy
        return action, (x + dx, y + dy, z)

    if action in ("toward_weak", "away_weak"):
        if weakest_xy is None:
            return "hold", (x, y, z)
        vx, vy = weakest_xy[0] - x, weakest_xy[1] - y
        norm = math.hypot(vx, vy)
        if norm < 1e-6:
            # Already overhead: nothing to move toward, so this degenerates
            # to a hold rather than a divide-by-zero.
            return "hold", (x, y, z)
        d = rng.uniform(*step_m)
        sign = 1.0 if action == "toward_weak" else -1.0
        tx = x + sign * d * vx / norm
        ty = y + sign * d * vy / norm
        if bounds is not None:
            cx = max(-xy_b, min(xy_b, tx))
            cy = max(-xy_b, min(xy_b, ty))
            # If the box eats the move, say so instead of logging a move
            # that did not happen.
            if math.hypot(cx - x, cy - y) < 1.0:
                return "hold", (x, y, z)
            tx, ty = cx, cy
        return action, (tx, ty, z)

    raise ValueError(f"unknown action {action!r}")

# Targeted UE reattach — cures a sticky RLF WITHOUT restart_ran.sh's full RAN
# bounce. Only the dropped UE's softmodem is killed and relaunched (matched by
# its unique config path, so its fork-pair goes too); the gNB, the other UE,
# and the channel bridge keep running untouched. Mirrors restart_ran.sh's
# per-UE launch line and re-homes the fresh TUN into the robot netns.
OAI_DIR = str(Path.home() / "openairinterface5g")
CONF_DIR = str(Path.home() / "sar_sim" / "oai_configs")
UE_ARGS = ["-r", "106", "--numerology", "1", "--band", "78",
           "-C", "3319680000", "--ssb", "516"]
# Roster-driven: reattaching charlie/delta would KeyError otherwise, and the
# watchdog would die on the very robot it was trying to rescue.
UE_CONF = {ROBOT_SPEC[r]["model"]: f"nrue_{r}.conf" for r in ROBOTS}
# Roster-driven. These were alpha/bravo only, so the watchdog would KeyError
# on sar_ground_charlie/delta the moment it tried to corroborate an RLF.
UE_TUN_OF = {ROBOT_SPEC[r]["model"]: ROBOT_SPEC[r]["tun"] for r in ROBOTS}
UE_LOG_OF = {ALPHA_MODEL: ALPHA_UE_LOG, BRAVO_MODEL: BRAVO_UE_LOG}
# Roster-driven. This was {ALPHA_MODEL, BRAVO_MODEL} only, so the RLF
# watchdog hit KeyError on sar_ground_charlie/delta and its THREAD died
# without stopping the run — charlie and delta had no watchdog at all.
UE_IP_OF = {ROBOT_SPEC[r]["model"]: ROBOT_SPEC[r]["ue_ip"] for r in ROBOTS}
# Uplink reachability target, per model — the same server the main loop's
# iperf/ping already use, so the watchdog's corroboration check exercises a
# path we know is routable when the UE is attached.
UE_TEST_IP_OF = {ROBOT_SPEC[r]["model"]: ROBOT_SPEC[r]["test_ip"] for r in ROBOTS}
NS_OF = {ROBOT_SPEC[r]["model"]: ROBOT_SPEC[r]["ns"] for r in ROBOTS}
RLF_CONFIRM_S = 90.0            # sustained 100% C2 loss before declaring RLF
WATCHDOG_PERIOD_S = 20.0        # how often the watchdog probes each robot's C2

# Consecutive failed reattaches before the watchdog gives up on a UE.
#
# WHY THIS EXISTS (2026-07-30, sar_indoor): the watchdog declared sticky RLF,
# the reattach failed with "TUN never reappeared", bad_since was reset, and
# 90 s later it tried again — for THREE HOURS. Both UEs stayed dead and the
# 6 h indoor run produced no CSV at all. A reattach that has failed repeatedly
# is not going to start working; retrying forever only guarantees the UE is
# killed again each time it struggles back up. After this many failures the
# watchdog stands down for that UE and says so, leaving the run to continue
# collecting whatever the other robot can still provide.
# How long to wait for C2 to come back after a targeted reattach before
# calling it a failure. Generous on purpose: see reattach_ue() step 6.
REATTACH_VERIFY_S = 75.0
RLF_MAX_FAILURES = 3
REATTACH_TIMEOUT_S = 150.0      # max wait for the fresh TUN (rfsim PDU is slow)

# ── Coverage guard (prevention, NLoS-aware) ──────────────────────────────────
# safe_radius is a DISTANCE bound from the open-field A2G model — blind to
# building/foliage/diffraction NLoS. In NLoS worlds a robot sits well inside
# safe_radius yet is blocked (~30 dB ploss) → RLF, and a reattach can't recover
# a UE with no signal ("TUN never reappeared"). So the guard reacts to the REAL
# ploss (read_ploss_fast, which includes NLoS excess): steer toward the gNB
# when ploss is elevated, and if it stays near the cliff, teleport the robot
# close to the gNB (near-guaranteed LoS at high elevation angle) BEFORE it dies.
PLOSS_STEER_DB = 25.0          # above this: steer the robot toward the gNB
PLOSS_TP_DB = 30.0            # near the ~35 dB cliff: candidate for rescue
PLOSS_TP_HOLD_S = 10.0        # ...sustained this long -> teleport to coverage
TP_SAFE_RADIUS_M = 15.0      # rescue lands the robot this far from the gNB

# ── Indoor O2I (sar_indoor): robots start OUTSIDE and drive IN ───────────────
# Warehouse building (indoor_building.py): footprint x[-30,30] y[-20,20],
# 6 rooms — front hall + 2 side rooms + 3 back rooms.  South entry door at
# x[-1.5,1.5]/y=-20, interior doorway x[-2.5,2.5]/y=0, back doorway
# x[-2.5,2.5]/y=10, side walls at x=±10 with doorway y[-2.5,2.5].
# Victim at (-20,15) in the back-left room (deepest O2I penetration).
# gNB drone stays OUTSIDE the building footprint for a real O2I path.
# Indoor routes. Building footprint x:[-30,30] y:[-20,20]; south entry door
# is only 3 m wide (x in [-1.5, 1.5]), interior doorways are x in [-2.5, 2.5]
# at y=0 and y=10, and y in [-2.5, 2.5] at x=+-10. Every entry path below
# threads those gaps — do not widen the lateral offsets without re-checking
# them against worlds/indoor_building.py.
INDOOR_START = {
    ROBOT_SPEC["alpha"]["model"]:   (0.0, -25.0),
    ROBOT_SPEC["bravo"]["model"]:   (0.8, -23.0),
    ROBOT_SPEC["charlie"]["model"]: (-0.8, -27.0),
    ROBOT_SPEC["delta"]["model"]:   (1.4, -29.0),
}
INDOOR_ENTRY = {
    # Alpha: south door -> front-center -> interior doorway -> back-left
    # -> victim room.  Threads through y=0 and x=-10 doorways.
    ALPHA_MODEL: [(0.0, -25.0), (0.0, -20.0), (0.0, -10.0), (0.0, -2.5),
                  (0.0, 2.5), (-7.5, 2.5), (-10, 0.0), (-10, 2.5),
                  (-20, 10.0), (-20, 15.0)],
    # Bravo: south door -> front-center -> interior doorway -> back-right
    # -> east room.  Threads through y=0 and x=10 doorways.
    BRAVO_MODEL: [(0.8, -23.0), (0.8, -20.0), (0.8, -10.0), (0.8, -2.5),
                  (0.8, 2.5), (7.5, 2.5), (10, 0.0), (10, 2.5),
                  (20, 10.0), (20, 15.0)],
    # Charlie: south door -> front-center -> y=0 doorway -> y=10 doorway
    # -> back-center room. Stays in the central x in [-10, 10] corridor.
    ROBOT_SPEC["charlie"]["model"]: [
        (-0.8, -27.0), (-0.8, -20.0), (-0.8, -10.0), (-0.8, -2.5),
        (-0.8, 2.5), (0.0, 7.5), (0.0, 10.0), (0.0, 15.0)],
    # Delta: south door -> front-center -> y=0 doorway -> x=+10 doorway
    # -> back down into the front-right room.
    ROBOT_SPEC["delta"]["model"]: [
        (1.4, -29.0), (1.4, -20.0), (1.4, -10.0), (1.4, -2.5),
        (1.4, 2.5), (7.5, 2.5), (10.0, 0.0), (10.0, -2.5),
        (15.0, -6.0), (20.0, -10.0)],
}
INDOOR_PATROL = {   # after entry, loop the rooms the robot reached
    # Alpha: west rooms sweep — back-left, back-center, front-left
    ALPHA_MODEL: [(-20.0, 5.0), (0.0, 5.0), (0.0, -10.0), (-20.0, -10.0)],
    # Bravo: east rooms sweep — back-right, back-center, front-right
    BRAVO_MODEL: [(20.0, 5.0), (0.0, 5.0), (0.0, -10.0), (20.0, -10.0)],
    # Charlie: back-center sweep, inside the central section.
    ROBOT_SPEC["charlie"]["model"]: [(0.0, 15.0), (5.0, 12.0),
                                     (0.0, 5.0), (-5.0, 12.0)],
    # Delta: front-right sweep, x > 10 and y < 0.
    ROBOT_SPEC["delta"]["model"]: [(20.0, -10.0), (25.0, -15.0),
                                   (15.0, -15.0), (20.0, -5.0)],
}
# Indoor gNB stays OUTSIDE the building and LOW — O2I is penetration-limited,
# so raw distance eats the indoor link budget.  The drone starts south of the
# building and wanders within ±35 m, projected outside the footprint.
# 2026-10-19 retune. The O2I model is NOT the limiter: it matches 3GPP
# TR 38.901 (0.5 dB/m depth loss; 9 dB wall penetration, below the standard's
# ~20 dB low-loss figure), and the excess at the robots' rooms is only
# 11.5-14 dB. The budget was being eaten by GEOMETRY — a +-35 m wander box
# around a 60x40 m building, projected outside the footprint, parked the UAV
# 30-35 m away horizontally at 15-35 m altitude, so slant range ran 35-50 m
# before penetration was counted. All four robots then sat at 37-42 dB with
# the traffic cliff near 35 dB.
#
# The box now lets the UAV circle the building perimeter closely (x +-32
# clears the x=+-30 walls, y +-32 clears y=+-20) and fly much lower. A
# nomadic node sent to search a building hovers over the car park beside it,
# not 35 m off in a field — so this is realistic geometry, not a fudge. It
# makes the scenario MARGINAL rather than impossible, which is what makes it
# informative: there is now something for moving the UAV to achieve.
INDOOR_STATIC_Z = 12.0
INDOOR_GNB_XY, INDOOR_GNB_ZMIN, INDOOR_GNB_ZMAX = 32.0, 8.0, 20.0
INDOOR_WP_TOL = 1.5       # m  — reached-waypoint tolerance (warehouse scale)
INDOOR_STUCK_S = 10.0     # s  — no progress toward a waypoint -> skip it

# Indoor RLF recovery. Robots MUST sit in the building (the O2I mission), where
# baseline + O2I(9 dB penetration) + NLoS shadow can spike over the ~35 dB RLF
# cliff — and a reattach can't recover a UE buried inside. So the ploss guard
# (which pulls free-roam robots toward the gNB) does not fit; instead, when a
# robot's real ploss stays near the cliff it RETREATS toward the door via the
# reversed entry path (O2I drops to 0 outside → the UE survives/reattaches),
# then re-enters once recovered. The in/out cycling also re-captures the O2I
# transition, which is exactly what this world is meant to measure.
INDOOR_RETREAT_DB = 29.0     # sustained real ploss above this -> retreat to door
INDOOR_RETREAT_HOLD_S = 6.0  # ...for this long
INDOOR_RESUME_DB = 22.0      # ploss recovered below this -> re-enter (patrol)
INDOOR_RETREAT = {m: list(reversed(wps)) for m, wps in INDOOR_ENTRY.items()}


# In-process gz-transport node for set_pose: a `gz service` subprocess
# costs 100-200ms per call, which capped pose updates at ~2Hz and made
# robot motion look like discrete point-to-point jumps in the GUI.
# Direct requests are ~1ms, allowing a smooth 10Hz motion tick.
_gz_node = None


def gz_set_pose(model, x, y, z):
    global _gz_node
    if _gz_node is None:
        _gz_node = GzNode()
    req = Pose()
    req.name = model
    req.position.x = x
    req.position.y = y
    req.position.z = z
    try:
        _gz_node.request(f"/world/{WORLD}/set_pose", req, Pose, Boolean, 500)
    except Exception:
        pass


def read_bridge_ploss_side(side):
    """Read latest ploss and distance for alpha or bravo from bridge log."""
    log = Path("/tmp/ch_bridge.log")
    if not log.exists():
        return 0.0, 0.0
    lines = log.read_text().strip().splitlines()
    # Per-robot bridge tag. The old map covered only alpha/bravo and fell
    # back to ALPHA for anything else, so charlie and delta were silently
    # reported with alpha's path loss — wrong in the CSV, and worse, the
    # driver's cliff-retreat guards steered them on another robot's signal.
    # Returning None for an unknown robot makes that impossible to repeat.
    spec = ROBOT_SPEC.get(side)
    if spec is None:
        return 0.0, 0.0
    tag = spec["ul_tag"]
    for line in reversed(lines):
        m = re.search(rf'{tag}.*?ploss ([\d.]+)dB.*?d=([\d.]+)m', line)
        if m:
            return float(m.group(1)), float(m.group(2))
    return 0.0, 0.0


_PLOSS_TAGS = {r: ROBOT_SPEC[r]["ul_tag"] for r in ROBOT_SPEC}


def read_ploss_fast(side):
    """Latest UL ploss (dB) for a robot, read cheaply — seek to the tail of the
    bridge log and scan the last ~64 KB only. Safe to call at driver rate
    (10 Hz); read_bridge_ploss_side reads the WHOLE (400 MB+) log and must not
    be used in the control loop. Returns None if not found. This ploss carries
    the world's NLoS excess (buildings/foliage/diffraction/O2I), which the
    geometric safe_radius does NOT — so the guard can react to real coverage."""
    tag = _PLOSS_TAGS.get(side, "gnb_ue0_alpha_ul")
    try:
        with open("/tmp/ch_bridge.log", "rb") as f:
            f.seek(0, 2)
            size = f.tell()
            f.seek(max(0, size - 65536))
            chunk = f.read().decode("utf-8", "ignore")
        for line in reversed(chunk.splitlines()):
            m = re.search(rf'{tag}.*?ploss ([\d.]+)dB', line)
            if m:
                return float(m.group(1))
    except Exception:
        pass
    return None


def _iperf_json(ns, args, timeout):
    """Run iperf3, retrying once on failure. A test killed by our
    subprocess timeout leaves the iperf3 SERVER stuck mid-test, and the
    next connection is rejected instantly ('server busy') — in the
    2026-07-10 90-min run this cascaded into 19% of samples logging
    tcp=0 on a perfectly live link. One retry after a settle beat
    absorbs it."""
    for attempt in (0, 1):
        try:
            out = ns_run(ns, args, timeout=timeout).stdout
            data = json.loads(out)
            if "error" not in data:
                return data
        except Exception:
            pass
        time.sleep(2.5)
    return None


def run_iperf_tcp(tun, test_ip, ns=NS_ALPHA):
    # -O 1: run 1 extra warm-up second and exclude it from the stats, so
    # the reported rate is post-slow-start steady state rather than the
    # ramp (a bare 2s test under-reports a ~20Mbps link as ~5-6Mbps).
    data = _iperf_json(ns,
        # no --bind-dev: iperf 3.9 lacks it; the netns pins the egress tun
        ["iperf3", "-c", test_ip,
         "-t", str(IPERF_DURATION), "-O", "1", "-J"],
        timeout=IPERF_DURATION + 11)
    try:
        mbps = data["end"]["sum_received"]["bits_per_second"] / 1e6
        retrans = data["end"]["sum_sent"]["retransmits"]
        return round(mbps, 2), retrans
    except Exception:
        return 0.0, 0


def run_iperf_udp(tun, test_ip, ns=NS_ALPHA):
    """Fixed-bitrate UDP UPLINK stream (UE -> network), modelling the
    robot's live video/sensor feed. The old --reverse (downlink) version
    never crossed the air interface — both tunnel IPs are local to this
    host, so reverse traffic short-circuited via loopback and logged the
    offered load (~100Mbps) regardless of geometry.

    Returns (delivered_mbps, jitter_ms, lost_pct) as measured server-side,
    i.e. after the radio link.
    """
    data = _iperf_json(ns,
        # no --bind-dev: iperf 3.9 lacks it; the netns pins the egress tun
        ["iperf3", "-u", "-c", test_ip,
         "-b", f"{VIDEO_BITRATE_MBPS}M", "-t", str(IPERF_DURATION), "-J"],
        timeout=IPERF_DURATION + 10)
    try:
        end = data["end"]
        s = end.get("sum") or end.get("sum_received") or {}
        jitter = s["jitter_ms"]
        lost_pct = s["lost_percent"]
        delivered = s["bits_per_second"] / 1e6 * (1.0 - lost_pct / 100.0)
        return round(delivered, 2), round(jitter, 3), round(lost_pct, 2)
    except Exception:
        return 0.0, 0.0, 100.0


def run_ping(tun, test_ip, ns=NS_ALPHA):
    try:
        # 10 packets, not 2. With -c 2 the only possible loss values are
        # 0 / 50 / 100%, and the C2 label is "loss < 50%" — so a SINGLE late
        # packet flipped a healthy link to unavailable. That biases the label
        # hard toward failure: at 90% per-packet delivery it reads 81%, at
        # 70% it reads 49%. -i 0.2 keeps the whole probe inside ~2 s so the
        # measurement cycle does not grow.
        out = ns_run(ns,
            ["ping", "-c", "10", "-i", "0.2", "-I", tun, "-W", "2", test_ip],
            timeout=12,
        ).stdout
        n_sent = 10
        m_loss = re.search(r'(\d+) packets transmitted, (\d+) (?:received|packets received)', out)
        n_recv = int(m_loss.group(2)) if m_loss else 0
        loss_pct = round((n_sent - n_recv) / n_sent * 100, 1)
        m_rtt = re.search(r'rtt min/avg/max/mdev = ([\d.]+)/([\d.]+)/([\d.]+)/([\d.]+) ms', out)
        if m_rtt:
            return float(m_rtt.group(1)), float(m_rtt.group(2)), float(m_rtt.group(3)), loss_pct
        return 0.0, 0.0, 0.0, loss_pct
    except Exception:
        return 0.0, 0.0, 0.0, 100.0


def _rehome_tuns(reason=""):
    """Move every UE TUN back into its robot namespace. Returns (ok, detail).

    Two traps this exists to avoid, both of which silently broke a run on
    2026-10-21:

    1. sudo has env_reset, so SAR_ROBOTS is STRIPPED on the way through and
       setup_robot_netns.sh falls back to its default 4-robot roster. On a
       2-robot run it then tried to re-home charlie and delta, whose TUNs do
       not exist. The roster is passed explicitly through `env`.

    2. sudo_run() does not raise on a non-zero exit, and the caller wrapped
       it in `except Exception: pass`. A FAILED re-home was therefore
       invisible: the watchdog reported a successful reattach while leaving
       the robot's TUN stranded in the DEFAULT namespace, where the robot's
       own netns cannot reach it. From the host the robot looks attached; it
       carries no traffic at all for the rest of the run. Here the exit code
       is checked AND each TUN is confirmed to have actually landed.
    """
    setup_script = str(Path(__file__).resolve().parent / "setup_robot_netns.sh")
    try:
        r = sudo_run(["env", f"SAR_ROBOTS={','.join(ROBOTS)}",
                      "bash", setup_script, "setup"], 120)
    except Exception as e:
        return False, f"re-home raised {e.__class__.__name__}"
    if r.returncode != 0:
        tail = (r.stderr or r.stdout or "").strip().splitlines()
        return False, f"re-home rc={r.returncode}: {tail[-1] if tail else '?'}"
    missing = [x for x in ROBOTS
               if not _tun_present(ROBOT_SPEC[x]["tun"], ROBOT_SPEC[x]["ns"])]
    if missing:
        return False, f"re-home ran but TUN still absent for: {','.join(missing)}"
    return True, f"TUNs re-homed{(' (' + reason + ')') if reason else ''}"


# Set by a background thread that has decided the run cannot continue. The
# MAIN loop checks it and exits, so stop() handlers still run and no video /
# iperf child is left behind — os._exit() from a thread would skip all that
# and leak a process per robot on every abort.
_abort_reason = None


def _gnb_alive():
    """True while the gNB softmodem process exists.

    The gNB can die mid-run — rfsimulator AssertFatal on a malformed packet,
    an OAI assertion, OOM. Nothing downstream notices: the UEs stay up, the
    TUNs stay up, the patrol keeps writing rows, and every radio column goes
    quietly to zero. A 5.5 h run then yields hours of rows that look like a
    total blackout but are really "no network existed". Detect it and stop.
    """
    return subprocess.run(["pgrep", "-x", "nr-softmodem"],
                          capture_output=True).returncode == 0


def _tun_present(tun, ns):
    """True if this robot's TUN still exists inside its namespace.

    A real coverage RLF makes nr-uesoftmodem EXIT, which takes the TUN with
    it. That is unambiguous — unlike a C2 probe, it cannot be confused with
    congestion — so the watchdog can act on it without waiting.
    """
    try:
        r = ns_run(ns, ["ip", "link", "show", tun], timeout=5)
        return r.returncode == 0
    except Exception:
        return False


def run_c2(ue_ip):
    """Command-and-control downlink probe: small packets base -> robot,
    like waypoint updates or an e-stop. Sent from the root namespace to
    the robot's UE IP, so (with netns active) the round trip genuinely
    crosses the air in both directions. Returns (rtt_avg_ms, loss_pct).
    """
    if not NETNS_MODE:
        return 0.0, 100.0  # without netns the DL path is loopback — no honest measurement
    try:
        out = subprocess.run(
            ["ping", "-c", "5", "-i", "0.2", "-W", "1", "-s", "64", ue_ip],
            capture_output=True, text=True, timeout=8,
        ).stdout
        m_loss = re.search(r'(\d+) packets transmitted, (\d+) (?:received|packets received)', out)
        n_recv = int(m_loss.group(2)) if m_loss else 0
        loss_pct = round((5 - n_recv) / 5 * 100, 1)
        m_rtt = re.search(r'rtt min/avg/max/mdev = [\d.]+/([\d.]+)/', out)
        rtt_avg = float(m_rtt.group(1)) if m_rtt else 0.0
        return rtt_avg, loss_pct
    except Exception:
        return 0.0, 100.0


def reattach_ue(model):
    """Bounce ONLY this robot's UE softmodem to cure a sticky RLF, leaving the
    gNB, the other UE, and the channel bridge running. Mirrors restart_ran.sh's
    per-UE launch. Returns (ok, detail_str)."""
    conf = UE_CONF[model]
    tun = UE_TUN_OF[model]
    log = UE_LOG_OF[model]
    ns = NS_OF[model]
    ue_ip = UE_IP_OF[model]
    setup_script = str(Path(__file__).resolve().parent / "setup_robot_netns.sh")

    # 1. kill just this UE (matched by its unique config path → its fork-pair too)
    #
    # SIGTERM FIRST, not SIGKILL. The UE holds a TCP connection to the gNB's
    # rfsimulator port carrying framed sample blocks. SIGKILL drops it mid
    # packet, and the gNB then parses the tail of a partial block as the next
    # header — producing a garbage beam_map and tripping
    #   AssertFatal(b->th.beam_map == 1ULL || ..., "The transmitter has
    #   enabled beam simulation while this receiver has not")
    # in radio/rfsimulator/simulator.cpp, which EXITS the gNB. Observed
    # 2026-10-21: a watchdog reattach killed the whole cell. A graceful TERM
    # lets the UE close the socket so the gNB sees a clean EOF.
    subprocess.run(["pkill", "-TERM", "-f", conf])
    for _ in range(10):                      # up to ~5 s to exit cleanly
        time.sleep(0.5)
        if subprocess.run(["pgrep", "-f", conf],
                          capture_output=True).returncode != 0:
            break
    else:
        # Refused to go: fall back to SIGKILL rather than hang the run.
        print(f"[reattach] {model}: UE ignored SIGTERM, escalating to SIGKILL",
              flush=True)
        subprocess.run(["pkill", "-9", "-f", conf])
    time.sleep(3)

    # 2. clear the stale TUN wherever it lives (root ns and/or the robot netns)
    try:
        sudo_run(["ip", "link", "delete", tun], 10)
        if NETNS_MODE:
            sudo_run(["ip", "netns", "exec", ns, "ip", "link", "delete", tun], 10)
    except Exception:
        pass

    # 3. relaunch exactly as restart_ran.sh does (append → keep the UE log history)
    launch = (f"cd {OAI_DIR} && nohup ./cmake_targets/ran_build/build/nr-uesoftmodem "
              f"--telnetsrv --rfsim -O {CONF_DIR}/{conf} {' '.join(UE_ARGS)} "
              f">> {log} 2>&1 &")
    subprocess.run(["bash", "-c", launch])

    # 4. wait for the fresh TUN to appear in the root ns (PDU session up)
    deadline = time.time() + REATTACH_TIMEOUT_S
    got_tun = False
    while time.time() < deadline:
        if subprocess.run(["ip", "addr", "show", tun],
                          capture_output=True).returncode == 0:
            got_tun = True
            break
        time.sleep(3)
    if not got_tun:
        return False, "TUN never reappeared (check the UE log)"

    # 5. re-home the fresh TUN into the robot netns so the C2/downlink path is
    #    honest again. setup is idempotent: the healthy UE stays in its own netns.
    if NETNS_MODE:
        ok_rh, detail_rh = _rehome_tuns("after reattach")
        if not ok_rh:
            # Do NOT report success here. The modem is up but the robot
            # cannot use it, which is worse than a clean failure: every
            # later cycle looks attached and carries nothing.
            return False, f"UE relaunched but {detail_rh}"

    # 6. confirm the C2 downlink is actually back.
    #
    # Poll, do not take a single reading 2 s after relaunch. A freshly
    # reattached UE needs tens of seconds to re-register, re-establish its PDU
    # session and start answering pings; measured 2026-10-21, a recovery that
    # genuinely worked still read 100% loss at +2 s and only came good around
    # +37 s. The single early check reported reattach_failed on a HEALTHY
    # modem, and three of those trip RLF_MAX_FAILURES and stand the robot down
    # for the rest of the run — losing a robot that was actually fine.
    deadline = time.time() + REATTACH_VERIFY_S
    loss = 100.0
    while time.time() < deadline:
        time.sleep(3)
        _, loss = run_c2(ue_ip)
        if loss < 100.0:
            return True, f"post-reattach C2 loss {loss:.0f}%"
    return False, (f"post-reattach C2 still {loss:.0f}% after "
                   f"{REATTACH_VERIFY_S:.0f}s")


def random_step(x, y, step_m, min_r, max_r):
    """Take a 2D random step of step_m, staying within [min_r, max_r] from origin."""
    for _ in range(20):
        angle = random.random() * 2 * math.pi
        nx = x + step_m * math.cos(angle)
        ny = y + step_m * math.sin(angle)
        dist = math.hypot(nx, ny)
        if min_r <= dist <= max_r:
            return nx, ny
    return x, y


# Per-UE value block emitted by test_one_ue(), in order. EVERY CSV header
# derives its per-robot columns from this list, so a change to the values
# cannot silently shift the columns — which is exactly how linear_sweep's
# bravo half drifted one place out of alignment (fixed 2026-10-19).
# Console glyph per robot, purely cosmetic.
ROBOT_GLYPH = {"alpha": "\u03b1", "bravo": "\u03b2",
               "charlie": "\u03b3", "delta": "\u03b4"}

UE_VALS_COLS = [
    "tcp_tp_mbps", "tcp_retransmits",
    "udp_tp_mbps", "udp_jitter_ms", "udp_lost_pct",
    "rtt_min_ms", "rtt_avg_ms", "rtt_max_ms", "ping_loss_pct",
    "pbch_rsrp", "ss_rsrp_dBm", "ss_rsrp_dBre", "ss_sinr_dB",
    "ss_samples", "ss_stale", "probe_skipped",
    "ul_bits_per_sym", "ul_code_rate", "ul_nb_rbs", "ul_nb_sym",
    "dl_harq_ok", "dl_harq_nok", "dl_bler",
]


def _blank(v):
    """None -> empty cell. A measurement that did not happen is not a zero."""
    return "" if v is None else v


def research_header(robots):
    """Column list for the research/excitation CSV, derived from the roster."""
    h = ["timestamp", "t_elapsed_s", "uav_x_m", "uav_y_m", "uav_z_m"]
    for r in robots:
        c = ROBOT_SPEC[r]["col"]
        h += [f"{c}x_m", f"{c}y_m", f"{c}_d_horiz_m", f"{c}_d_slant_m",
              f"{c}_ploss_dB"]
        h += [f"{c}_{v}" for v in UE_VALS_COLS]
        # Seconds from the START of this cycle at which this robot's traffic
        # probes finished. A cycle is a SEQUENTIAL battery over every robot,
        # so columns in one row are not contemporaneous: with 4 robots on
        # slow links a cycle ran 253 s and a robot's ping and its C2 landed
        # ~177 s apart, which read as "ping fine, C2 dead" in the same row
        # when in truth the link died in between. These offsets make that
        # skew visible and filterable instead of silently wrong.
        h += [f"{c}_rsrp_computed_dBm", f"{c}_probe_t_s"]
    for r in robots:
        c = ROBOT_SPEC[r]["col"]
        h += [f"{c}_c2_rtt_ms", f"{c}_c2_loss_pct", f"{c}_c2_t_s"]
    # Application-level video QoS (video_streams.py). goodput is what iperf
    # would have told us; frame_delivery_pct and latency_ms are what it could
    # not. Delivery falls off a cliff at NLoS while Mb/s degrades gradually,
    # so it separates "degraded" from "unusable" — the distinction the
    # planner actually has to act on. Empty when running in iperf mode.
    for r in robots:
        c = ROBOT_SPEC[r]["col"]
        h += [f"{c}_vid_goodput_mbps", f"{c}_vid_frame_delivery_pct",
              f"{c}_vid_pkt_loss_pct", f"{c}_vid_latency_ms",
              f"{c}_vid_jitter_ms"]
    # Wall-clock span of the whole cycle. Treat a row whose span is large
    # relative to how fast the scene changes as a BLEND, not a snapshot.
    h += ["cycle_span_s", "phase"]
    for r in robots:
        c = ROBOT_SPEC[r]["col"]
        h += [f"{c}_event", f"{c}_outage_s"]
    # Run metadata (brief 6.4). `terrain` is recorded for evaluation,
    # stratification and the terrain-given ablation ONLY — never a model
    # input, like ploss_dB and rsrp_computed_dBm.
    h += ["run_id", "seed", "terrain", "policy",
          "action", "action_tx_m", "action_ty_m", "action_tz_m"]
    return h


def fresh_l1(ue_log):
    """Newest SS-RSRP/SINR straight from the UE log tail. The windowed
    parser (metrics_collector) carries values that can be minutes stale;
    the modem prints these lines near-continuously, so the last one in
    the final 2MB is current to within ~a second. (Verified 2026-07-11:
    parser said -48 dBm while the log's fresh line read -64.)

    Returns (rsrp_dBm, rsrp_dBre, sinr_dB), any of which may be None.

    `rsrp_dBre` is the bracketed full-precision value on the same line.
    The leading dBm integer is that number truncated to 1 dB, which is what
    produced the 20-35-level staircase in the campaign CSVs — over a 43,332
    sample reference capture the integer took 32 distinct values and the
    float 4,002 (DATA_AUDIT.md 4.1b). Prefer dB/RE for anything that needs
    resolution; dBm is kept so existing columns do not change meaning."""
    try:
        out = subprocess.run(
            ["bash", "-c", f"tail -c 2000000 {ue_log} | grep -a 'SS-RSRP' | tail -1"],
            capture_output=True, text=True, timeout=10).stdout
        m = re.search(r"SS-RSRP:\s*(-?\d+)\s*dBm/RE\s*\(\s*(-?[\d.]+)\s*dB/RE\s*\)"
                      r".*?SS-SINR:\s*(-?[\d.]+)", out)
        if m:
            return float(m.group(1)), float(m.group(2)), float(m.group(3))
        # Older split format without the bracketed dB/RE term.
        m = re.search(r"SS-RSRP:\s*(-?\d+)\s*dBm.*?SS-SINR:\s*(-?[\d.]+)", out)
        if m:
            return float(m.group(1)), None, float(m.group(2))
    except Exception:
        pass
    return None, None, None


def link_alive(tun, test_ip, ns):
    """One cheap packet to decide whether the expensive probes are worth it.

    iperf3 on a dead link costs ~60 s per robot: each of the TCP and UDP
    tests runs to its ~13 s timeout and _iperf_json retries once, by design,
    to absorb a server left busy by a PREVIOUS timeout. On a link that is
    simply down the retry only buys a second timeout. With four robots that
    turned an indoor cycle into 253 s (measured 2026-10-19), which is both
    useless as a sampling rate and actively harmful: a robot's ping and its
    C2 ended up ~177 s apart, so the columns in one row described different
    moments.

    One ping with a 1 s deadline costs ~0.1 s when the link is healthy and
    replaces ~60 s of guaranteed timeouts when it is not.
    """
    try:
        out = ns_run(ns, ["ping", "-c", "1", "-W", "1", "-I", tun, test_ip],
                     timeout=4).stdout
        return "1 received" in out or " 0% packet loss" in out
    except Exception:
        return False


def test_one_ue(tun, test_ip, ue_log, seen, prev, ns=NS_ALPHA,
                streams=None, robot=None, video_mode=False):
    """Measure one UE, return (row_values, new_seen, new_prev).

    With `streams` (a TrafficStreams) throughput is SAMPLED from that robot's
    persistent flows; without it the legacy per-cycle iperf probes run, so the
    older sweep/walk/search modes keep working unchanged.
    """
    lines, seen = tail_file(ue_log, seen)
    tmp = parse_ue_metrics(lines, prev)
    if tmp.get("ul_bits_per_sym", 0) > 0:
        prev = tmp

    # Throughput comes from the PERSISTENT flows when they are running
    # (scripts/traffic_streams.py): a long-lived connection keeps its
    # congestion window open, so reading its current rate is both instant and
    # a true steady-state figure. The old path launched a fresh 2 s iperf per
    # robot per cycle and measured TCP mid-slow-start — s1 4.2, s2 7.3,
    # s3 12.9 Mb/s — so every throughput value was a point on the ramp. That
    # is why four robots with different link quality all reported an
    # identical ~2.9 Mb/s (DATA_AUDIT.md; measured 2026-10-20).
    #
    # `probe_skipped` still records WHY a zero is a zero: 0 = sampled from a
    # live flow, 1 = link could not pass a packet so nothing was measured,
    # 2 = the flow had just been relaunched and had produced no interval
    # line yet, so the throughput cells are EMPTY rather than 0.00,
    # 3 = video traffic mode, so no iperf ran at all and the TCP/UDP cells
    # are empty by design (see the *_vid_* columns instead).
    if video_mode:
        # Video mode carries the load; running iperf here as well would make
        # the robots fight themselves and neither figure would mean anything.
        # probe_skipped = 3 records WHY these cells are empty.
        probe_skipped = 3
        tcp_tp, retrans, udp_tp, jitter_ms, udp_lost = "", 0, "", "", ""
    elif streams is not None:
        tcp_tp, retrans = streams.tcp_sample(robot)
        udp_tp, jitter_ms, udp_lost = streams.udp_sample(robot)
        # A persistent flow dies when its UE reattaches, and _ensure()
        # relaunches it — which recreates its log EMPTY. For the next few
        # cycles there is nothing to average, so the samplers return None.
        # That is "not measured", not "measured zero": writing 0.00 would
        # stripe the throughput column with false outages on a healthy link.
        probe_skipped = 0 if tcp_tp is not None else 2
        if tcp_tp is None:
            tcp_tp = ""
        if udp_tp is None:
            udp_tp = ""
        if jitter_ms is None:
            jitter_ms = ""
        if udp_lost is None:          # server has not reported an interval yet
            udp_lost = ""
    elif link_alive(tun, test_ip, ns):
        probe_skipped = 0
        tcp_tp, retrans = run_iperf_tcp(tun, test_ip, ns)
        udp_tp, jitter_ms, udp_lost = run_iperf_udp(tun, test_ip, ns)
    else:
        probe_skipped = 1
        tcp_tp, retrans, udp_tp, jitter_ms, udp_lost = 0.0, 0, 0.0, 0.0, 100.0
    rtt_min, rtt_avg, rtt_max, ping_lost = run_ping(tun, test_ip, ns)

    lines, seen = tail_file(ue_log, seen)
    tmp = parse_ue_metrics(lines, prev)
    if tmp.get("ul_bits_per_sym", 0) > 0:
        prev = tmp

    m = prev
    # fresh L1 read overrides the (potentially stale) windowed parse
    f_rsrp, f_dbre, f_sinr = fresh_l1(ue_log)
    ss_rsrp = f_rsrp if f_rsrp is not None else m.get("ss_rsrp", "")
    ss_sinr = f_sinr if f_sinr is not None else m.get("ss_sinr", "")
    ss_dbre = f_dbre if f_dbre is not None else m.get("ss_rsrp_dBre", "")

    # Staleness markers (DATA_AUDIT.md 4.3). `ss_samples` counts the SS lines
    # the windowed parse saw during THIS cycle; `ss_stale` is 1 when both the
    # fresh tail read and the windowed parse came up empty, so the RSRP/SINR
    # reported here is carried over from an earlier cycle rather than
    # measured in this one. Without this flag a carried value is
    # indistinguishable from a genuinely unchanging link.
    ss_samples = m.get("ss_samples", 0) or 0
    ss_stale = 1 if (f_rsrp is None and ss_samples == 0) else 0

    vals = [tcp_tp, retrans, udp_tp, jitter_ms, udp_lost,
            rtt_min, rtt_avg, rtt_max, ping_lost,
            m.get("pbch_rsrp", ""), ss_rsrp, ss_dbre, ss_sinr,
            ss_samples, ss_stale, probe_skipped,
            m.get("ul_bits_per_sym", ""), m.get("ul_code_rate", ""),
            m.get("ul_nb_rbs", ""), m.get("ul_nb_sym", ""),
            m.get("dl_harq_ok", ""), m.get("dl_harq_nok", ""),
            m.get("dl_bler", "")]
    return vals, seen, prev


def linear_sweep(start=10, end=200, step=15, duration_s=180):
    t0 = time.time()
    direction = 1
    x = start
    seen_a = 0
    prev_a = {}
    seen_b = 0
    prev_b = {}

    with open(PATROL_LOG, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow([
            "timestamp", "t_elapsed_s", "x_m",
            "ploss_dB", "d_horiz_m",
            "tcp_tp_mbps", "tcp_retransmits",
            "udp_tp_mbps", "udp_jitter_ms", "udp_lost_pct",
            "rtt_min_ms", "rtt_avg_ms", "rtt_max_ms",             "ping_loss_pct",
            "alpha_pbch_rsrp", "alpha_ss_rsrp_dBm", "alpha_ss_rsrp_dBre", "alpha_ss_sinr_dB", "alpha_ss_samples", "alpha_ss_stale", "alpha_probe_skipped",
            "alpha_ul_bits_per_sym", "alpha_ul_code_rate",
            "alpha_ul_nb_rbs", "alpha_ul_nb_sym",
            "alpha_dl_harq_ok", "alpha_dl_harq_nok", "alpha_dl_bler", "alpha_rsrp_computed_dBm",
            "bravo_pbch_rsrp", "bravo_ss_rsrp_dBm", "bravo_ss_rsrp_dBre", "bravo_ss_sinr_dB", "bravo_ss_samples", "bravo_ss_stale", "bravo_probe_skipped",
            "bravo_ul_bits_per_sym", "bravo_ul_code_rate",
            "bravo_ul_nb_rbs", "bravo_ul_nb_sym",
            "bravo_dl_harq_ok", "bravo_dl_harq_nok", "bravo_dl_bler", "bravo_rsrp_computed_dBm",
        ])

        while time.time() - t0 < duration_s:
            gz_set_pose(ALPHA_MODEL, x, 0, GROUND_Z)
            time.sleep(2)

            now = time.time()
            t_elapsed = now - t0
            ploss_a, d_a = read_bridge_ploss_side("alpha")
            ploss_b, d_b = read_bridge_ploss_side("bravo")

            lines, seen_a = tail_file(ALPHA_UE_LOG, seen_a)
            tmp = parse_ue_metrics(lines, prev_a)
            if tmp.get("ul_bits_per_sym", 0) > 0:
                prev_a = tmp
            lines, seen_b = tail_file(BRAVO_UE_LOG, seen_b)
            tmp = parse_ue_metrics(lines, prev_b)
            if tmp.get("ul_bits_per_sym", 0) > 0:
                prev_b = tmp

            vals_a, seen_a, prev_a = test_one_ue(ALPHA_TUN, ALPHA_IP, ALPHA_UE_LOG, seen_a, prev_a, NS_ALPHA)

            a_rsrp_c = round(TX_REF - (ploss_a - DL_UL_OFFSET), 1)

            row = [datetime.now(timezone.utc).isoformat(),
                   round(t_elapsed, 1), round(x, 1),
                   round(ploss_a, 2), round(d_a, 1)]
            row += vals_a + [a_rsrp_c]
            # Bravo is not probed in this mode (only alpha is swept), so its
            # half of the row is read straight from the windowed parse rather
            # than from test_one_ue. Keep this list in lockstep with the
            # bravo_* header entries above — it drifted out of sync once
            # already (the row omitted bravo_rsrp_computed_dBm, so every
            # bravo column after pbch_rsrp was shifted one place left).
            m_b = prev_b
            b_rsrp_c = round(TX_REF - (ploss_b - DL_UL_OFFSET), 1)
            b_samples = m_b.get("ss_samples", 0) or 0
            row += [m_b.get("pbch_rsrp", ""), m_b.get("ss_rsrp", ""),
                    m_b.get("ss_rsrp_dBre", ""), m_b.get("ss_sinr", ""),
                    b_samples, 1 if b_samples == 0 else 0,
                    "",  # probe_skipped: not applicable, bravo is not probed here
                    m_b.get("ul_bits_per_sym", ""), m_b.get("ul_code_rate", ""),
                    m_b.get("ul_nb_rbs", ""), m_b.get("ul_nb_sym", ""),
                    m_b.get("dl_harq_ok", ""), m_b.get("dl_harq_nok", ""),
                    m_b.get("dl_bler", ""), b_rsrp_c]

            w.writerow(row)
            f.flush()
            ss_sinr = prev_a.get("ss_sinr", "")
            ss_rsrp = prev_a.get("ss_rsrp", "")
            se = prev_a.get("ul_bits_per_sym", "")
            print(f"  x={x:.0f}m  ploss={ploss_a:.1f}dB"
                  f"  TCP={vals_a[0]:.1f}Mbps  PingPDR={100-vals_a[8]:.1f}%"
                  f"  RTT={vals_a[6]:.2f}ms"
                  f"  RSRP={ss_rsrp}dBm  SINR={ss_sinr}dB  bits/sym={se}")

            x += direction * step
            if x > end or x < start:
                direction *= -1
                x += direction * step

    print(f"\nSaved: {PATROL_LOG}")


def random_walk_both(step_m=2, duration_s=180):
    t0 = time.time()
    seen = {r: 0 for r in ROBOTS}
    prev = {r: {} for r in ROBOTS}

    # Persistent per-robot traffic: a rate-limited TCP uploader and a CBR UDP
    # video stream, started ONCE here rather than per cycle. Besides giving
    # steady-state throughput instead of slow-start samples, this is what the
    # columns were always meant to represent ("TCP goodput (sensor upload)",
    # "UDP video stream") and it makes the robots genuinely contend for the
    # cell, which is the regime the all-robots-connected objective is about.
    # free roam is a manual/demo mode, not a campaign run, so it keeps the
    # iperf flows and has no video feed to sample.
    video = None
    streams = TrafficStreams(ROBOTS, ROBOT_SPEC, sudo_pass=SUDO_PASS,
                             netns=NETNS_MODE)
    print(f"  traffic: persistent TCP+UDP flows per robot "
          f"({len(ROBOTS)} robots)", flush=True)
    streams.start()
    ax, ay = 10.0, 0.0
    bx, by = 25.0, 0.0
    gz_set_pose(ALPHA_MODEL, ax, ay, GROUND_Z)
    gz_set_pose(BRAVO_MODEL, bx, by, GROUND_Z)
    time.sleep(2)

    with open(PATROL_LOG, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow([
            "timestamp", "t_elapsed_s",
            "uav_x_m", "uav_y_m", "uav_z_m",
            "ax_m", "ay_m", "a_d_horiz_m", "a_d_slant_m", "a_ploss_dB",
            "a_tcp_tp_mbps", "a_tcp_retransmits",
            "a_udp_tp_mbps", "a_udp_jitter_ms", "a_udp_lost_pct",
            "a_rtt_min_ms", "a_rtt_avg_ms", "a_rtt_max_ms", "a_ping_loss_pct",
            "a_pbch_rsrp", "a_ss_rsrp_dBm", "a_ss_rsrp_dBre", "a_ss_sinr_dB", "a_ss_samples", "a_ss_stale", "a_probe_skipped",
            "a_ul_bits_per_sym", "a_ul_code_rate",
            "a_ul_nb_rbs", "a_ul_nb_sym",
            "a_dl_harq_ok", "a_dl_harq_nok", "a_dl_bler", "a_rsrp_computed_dBm",
            "bx_m", "by_m", "b_d_horiz_m", "b_d_slant_m", "b_ploss_dB",
            "b_tcp_tp_mbps", "b_tcp_retransmits",
            "b_udp_tp_mbps", "b_udp_jitter_ms", "b_udp_lost_pct",
            "b_rtt_min_ms", "b_rtt_avg_ms", "b_rtt_max_ms", "b_ping_loss_pct",
            "b_pbch_rsrp", "b_ss_rsrp_dBm", "b_ss_rsrp_dBre", "b_ss_sinr_dB", "b_ss_samples", "b_ss_stale", "b_probe_skipped",
            "b_ul_bits_per_sym", "b_ul_code_rate",
            "b_ul_nb_rbs", "b_ul_nb_sym",
            "b_dl_harq_ok", "b_dl_harq_nok", "b_dl_bler", "b_rsrp_computed_dBm",
            "a_c2_rtt_ms", "a_c2_loss_pct", "b_c2_rtt_ms", "b_c2_loss_pct",
        ])

        while time.time() - t0 < duration_s:
            ax, ay = random_step(ax, ay, step_m, MIN_RADIUS, MAX_RADIUS)
            bx, by = random_step(bx, by, step_m, MIN_RADIUS, MAX_RADIUS)
            gz_set_pose(ALPHA_MODEL, ax, ay, GROUND_Z)
            gz_set_pose(BRAVO_MODEL, bx, by, GROUND_Z)
            time.sleep(2)

            now = time.time()
            t_elapsed = now - t0
            ploss_a, d_a = read_bridge_ploss_side("alpha")
            ploss_b, d_b = read_bridge_ploss_side("bravo")

            vals_a, seen_a, prev_a = test_one_ue(ALPHA_TUN, ALPHA_IP, ALPHA_UE_LOG, seen_a, prev_a, NS_ALPHA)
            vals_b, seen_b, prev_b = test_one_ue(BRAVO_TUN, BRAVO_IP, BRAVO_UE_LOG, seen_b, prev_b, NS_BRAVO)
            a_c2_rtt, a_c2_loss = run_c2(ALPHA_UE_IP)
            b_c2_rtt, b_c2_loss = run_c2(BRAVO_UE_IP)

            dz = abs(UAV_Z - GROUND_Z)
            a_slant = math.hypot(d_a, dz)
            b_slant = math.hypot(d_b, dz)
            a_rsrp_c = round(TX_REF - (ploss_a - DL_UL_OFFSET), 1)
            b_rsrp_c = round(TX_REF - (ploss_b - DL_UL_OFFSET), 1)

            row = [datetime.now(timezone.utc).isoformat(),
                   round(t_elapsed, 1),
                   UAV_X, UAV_Y, UAV_Z,
                   round(ax, 1), round(ay, 1), round(d_a, 1), round(a_slant, 1), round(ploss_a, 2)]
            row += vals_a + [a_rsrp_c]
            row += [round(bx, 1), round(by, 1), round(d_b, 1), round(b_slant, 1), round(ploss_b, 2)]
            row += vals_b + [b_rsrp_c]
            row += [a_c2_rtt, a_c2_loss, b_c2_rtt, b_c2_loss]

            w.writerow(row)
            f.flush()

            sa = prev_a
            sb = prev_b
            print(f"  U({UAV_X:.0f},{UAV_Y:.0f},{UAV_Z:.0f})")
            print(f"  α({ax:.0f},{ay:.0f}) ploss={ploss_a:.1f}dB  d={a_slant:.0f}m"
                  f"  TCP={vals_a[0]:.1f}M  PingPDR={100-vals_a[8]:.0f}%  RTT={vals_a[6]:.1f}ms"
                  f"  RSRP={sa.get('ss_rsrp','')}dBm  SINR={sa.get('ss_sinr','')}dB"
                  f"  bits/sym={sa.get('ul_bits_per_sym','')}")
            print(f"  β({bx:.0f},{by:.0f}) ploss={ploss_b:.1f}dB  d={b_slant:.0f}m"
                  f"  TCP={vals_b[0]:.1f}M  PingPDR={100-vals_b[8]:.0f}%  RTT={vals_b[6]:.1f}ms"
                  f"  RSRP={sb.get('ss_rsrp','')}dBm  SINR={sb.get('ss_sinr','')}dB"
                  f"  bits/sym={sb.get('ul_bits_per_sym','')}")

    print(f"\nSaved: {PATROL_LOG}")


def free_roam_both(speed_ms=1.0, duration_s=180, tick_s=0.1, vary_uav=False):
    """Both robots DRIVE continuously and independently — DiffDrive
    cmd_vel with pose feedback and heading-error steering, so wheels
    genuinely spin and motion is kinematic, not teleportation. Metrics
    are sampled in parallel; each CSV row is tagged with the robot's
    position at the midpoint of its measurement window (an iperf+ping
    cycle spans ~8-10s of travel).

    With vary_uav, the UAV glides through UAV_SCHEDULE (x, y AND
    altitude vary; equal dwell per waypoint). The robots' wander annulus
    is centered on the UAV's live ground track and its outer radius
    follows safe_radius(altitude), so a descending UAV pulls the robots
    inward before the shrinking coverage edge can RLF them.

    Fine-grained ploss-vs-position at every 0.5s tick is already captured
    by channel_bridge_log.csv; this CSV adds the expensive link metrics.
    """
    global _gz_node
    if _gz_node is None:
        _gz_node = GzNode()

    # live pose feedback from the world's pose topic
    fb_lock = threading.Lock()
    feedback = {}       # robot model -> (x, y, yaw)
    uav_now = [UAV_X, UAV_Y, UAV_Z]   # live UAV pose (mutated by pose_cb)

    def pose_cb(msg):
        for p in msg.pose:
            if p.name in MODELS:
                q = p.orientation
                yaw = math.atan2(2.0 * (q.w * q.z + q.x * q.y),
                                 1.0 - 2.0 * (q.y * q.y + q.z * q.z))
                with fb_lock:
                    feedback[p.name] = (p.position.x, p.position.y, yaw)
            elif p.name == "sar_uav":
                with fb_lock:
                    uav_now[0], uav_now[1], uav_now[2] = \
                        p.position.x, p.position.y, p.position.z

    _gz_node.subscribe(Pose_V, f"/world/{WORLD}/pose/info", pose_cb)

    # Roster-driven: with a hardcoded alpha/bravo pair, charlie and delta
    # had no cmd_vel publisher, so they never moved — the same silent
    # "attached but stationary" failure the drivability gate was added for.
    pubs = {m: _gz_node.advertise(f"/model/{m}/cmd_vel", Twist())
            for m in MODELS}

    def current_pos(model, default):
        with fb_lock:
            fb = feedback.get(model)
        return (fb[0], fb[1]) if fb else default

    def current_uav():
        with fb_lock:
            return tuple(uav_now)

    # one-time placement at start positions (teleport is fine for setup)
    is_indoor = (WORLD == "sar_indoor")
    if is_indoor:
        for m in MODELS:
            gz_set_pose(m, *INDOOR_START[m], GROUND_Z)
    else:
        for r in ROBOTS:
            gz_set_pose(ROBOT_SPEC[r]["model"], *ROBOT_SPEC[r]["start"],
                        GROUND_Z)
    if vary_uav:
        gz_set_pose("sar_uav", *UAV_SCHEDULE[0])
    time.sleep(2)

    running = threading.Event()
    running.set()

    def uav_pilot():
        """Glide the (static) UAV through the schedule: kinematic steps
        at UAV_SPEED, then dwell. Equal dwell per waypoint over the run."""
        dwell = max(30.0, duration_s / len(UAV_SCHEDULE))
        x, y, z = UAV_SCHEDULE[0]
        i = 0
        while running.is_set():
            # dwell (interruptible)
            end = time.time() + dwell
            while running.is_set() and time.time() < end:
                time.sleep(0.5)
            if not running.is_set():
                return
            i = (i + 1) % len(UAV_SCHEDULE)
            tx, ty, tz = UAV_SCHEDULE[i]
            dist = math.sqrt((tx-x)**2 + (ty-y)**2 + (tz-z)**2)
            steps = max(1, int(dist / (UAV_SPEED * 0.1)))
            for s in range(1, steps + 1):
                if not running.is_set():
                    return
                f = s / steps
                gz_set_pose("sar_uav", x + (tx-x)*f, y + (ty-y)*f, z + (tz-z)*f)
                time.sleep(0.1)
            x, y, z = tx, ty, tz
            print(f"[uav] repositioned to ({x}, {y}, {z}), "
                  f"safe radius {safe_radius(z):.0f}m", flush=True)

    # wander heading target per robot; the driver steers toward it
    targets = {m: random.random() * 2 * math.pi for m in MODELS}

    def driver():
        while running.is_set():
            ux, uy, uz = current_uav()
            # outer bound: user cap AND the model's per-altitude edge
            r_max = min(MAX_RADIUS, safe_radius(uz))
            for model in MODELS:
                with fb_lock:
                    fb = feedback.get(model)
                if fb is None:
                    continue
                x, y, yaw = fb
                rx, ry = x - ux, y - uy
                r = math.hypot(rx, ry)
                # Emergency guard: a relocating UAV can strand a 1 m/s
                # robot outside the (moving, altitude-dependent) coverage
                # edge for minutes — long enough for sticky RLF that only
                # a RAN restart cures. Snap it back inside the annulus;
                # rows log true positions, so the data stays honest.
                if r > r_max + 5:
                    f = (r_max * 0.6) / r
                    gz_set_pose(model, ux + rx * f, uy + ry * f, GROUND_Z)
                    targets[model] = random.random() * 2 * math.pi
                    print(f"[guard] {model} was {r:.0f}m from UAV "
                          f"(edge {r_max:.0f}m) — teleported inside", flush=True)
                    continue
                # wander: drift the target heading; steer back inside the
                # allowed annulus (centered on the UAV ground track)
                tgt = targets[model] + random.gauss(0.0, 0.5 * tick_s)
                if r > r_max - 5:
                    tgt = math.atan2(-ry, -rx)
                elif r < MIN_RADIUS + 1:
                    tgt = math.atan2(ry, rx)
                targets[model] = tgt

                # Reactive obstacle avoidance: deflect around any building in
                # the path. When blocked, commit to the detour heading so the
                # robot slides along the facade instead of fighting the wall.
                if is_indoor:
                    steer, blocked = indoor_avoid(x, y, tgt)
                else:
                    steer, blocked = avoid_heading(x, y, tgt)
                if blocked:
                    targets[model] = steer

                err = math.atan2(math.sin(steer - yaw), math.cos(steer - yaw))
                tw = Twist()
                tw.angular.z = max(-1.5, min(1.5, 2.0 * err))
                # slow down through sharp turns / while squeezing past a wall,
                # never fully stop
                floor = 0.05 if blocked else 0.15
                tw.linear.x = speed_ms * max(floor, math.cos(err))
                try:
                    pubs[model].publish(tw)
                except Exception:
                    pass
            time.sleep(tick_s)

    threading.Thread(target=driver, daemon=True).start()
    if vary_uav:
        threading.Thread(target=uav_pilot, daemon=True).start()

    t0 = time.time()
    seen = {r: 0 for r in ROBOTS}
    prev = {r: {} for r in ROBOTS}

    # Persistent per-robot traffic: a rate-limited TCP uploader and a CBR UDP
    # video stream, started ONCE here rather than per cycle. Besides giving
    # steady-state throughput instead of slow-start samples, this is what the
    # columns were always meant to represent ("TCP goodput (sensor upload)",
    # "UDP video stream") and it makes the robots genuinely contend for the
    # cell, which is the regime the all-robots-connected objective is about.
    # free roam is a manual/demo mode: iperf flows, no video feed.
    # free roam runs no watchdog, but the shared measurement block
    # brackets its probes with this flag; give it a local one.
    probe_busy = threading.Event()
    video = None
    streams = TrafficStreams(ROBOTS, ROBOT_SPEC, sudo_pass=SUDO_PASS,
                             netns=NETNS_MODE)
    print(f"  traffic: persistent TCP+UDP flows per robot "
          f"({len(ROBOTS)} robots)", flush=True)
    streams.start()

    with open(PATROL_LOG, "w", newline="") as f:
        w = csv.writer(f)
        # Same schema as random_walk_both so downstream tooling is unaffected
        # Roster-driven header. This was a hardcoded a_*/b_* list, so a
        # 4-robot roster wrote two robots' columns and silently dropped the
        # rest. research_header() already derives the layout from ROBOTS.
        w.writerow(research_header(ROBOTS))

        try:
            while time.time() - t0 < duration_s:
                start_pos = {r: current_pos(ROBOT_SPEC[r]["model"],
                                            ROBOT_SPEC[r]["start"])
                             for r in ROBOTS}
                u_start = current_uav()

                # This whole block used to be two hand-written alpha/bravo
                # stanzas referring to seen_a / prev_a / d_a / ploss_a, none
                # of which existed any more after the roster migration — the
                # mode crashed on its first measurement with
                # "UnboundLocalError: local variable 'seen_a'".
                vals, c2_rtt, c2_loss = {}, {}, {}
                probe_t, c2_t = {}, {}
                cycle_t0 = time.time()
                probe_busy.set()
                try:
                    for r in ROBOTS:
                        sp = ROBOT_SPEC[r]
                        vals[r], seen[r], prev[r] = test_one_ue(
                            sp["tun"], sp["test_ip"], sp["log"],
                            seen[r], prev[r], sp["ns"],
                            streams=streams, robot=r,
                            video_mode=(video is not None))
                        probe_t[r] = round(time.time() - cycle_t0, 1)
                    for r in ROBOTS:
                        c2_rtt[r], c2_loss[r] = run_c2(ROBOT_SPEC[r]["ue_ip"])
                        c2_t[r] = round(time.time() - cycle_t0, 1)
                    vid = {r: (video.sample(r) if video else {})
                           for r in ROBOTS}
                finally:
                    probe_busy.clear()

                if not _gnb_alive():
                    print("FATAL: gNB process is gone — aborting this run.",
                          flush=True)
                    raise SystemExit(3)

                end_pos = {r: current_pos(ROBOT_SPEC[r]["model"], start_pos[r])
                           for r in ROBOTS}
                u_end = current_uav()
                pos = {r: ((start_pos[r][0] + end_pos[r][0]) / 2,
                           (start_pos[r][1] + end_pos[r][1]) / 2)
                       for r in ROBOTS}
                ux = (u_start[0] + u_end[0]) / 2
                uy = (u_start[1] + u_end[1]) / 2
                uz = (u_start[2] + u_end[2]) / 2

                t_elapsed = time.time() - t0
                dz = abs(uz - GROUND_Z)
                ploss, dist, slant, rsrp_c = {}, {}, {}, {}
                for r in ROBOTS:
                    ploss[r], _ = read_bridge_ploss_side(r)
                    dist[r] = math.hypot(pos[r][0] - ux, pos[r][1] - uy)
                    slant[r] = math.hypot(dist[r], dz)
                    # Simulator ground truth, logged for evaluation only —
                    # never a model input (DATA_AUDIT.md 4.5).
                    rsrp_c[r] = round(TX_REF - (ploss[r] - DL_UL_OFFSET), 1)

                row = [datetime.now(timezone.utc).isoformat(),
                       round(t_elapsed, 1),
                       round(ux, 1), round(uy, 1), round(uz, 1)]
                for r in ROBOTS:
                    px, py = pos[r]
                    row += [round(px, 1), round(py, 1), round(dist[r], 1),
                            round(slant[r], 1), round(ploss[r], 2)]
                    row += vals[r] + [rsrp_c[r], probe_t.get(r, "")]
                for r in ROBOTS:
                    row += [c2_rtt[r], c2_loss[r], c2_t.get(r, "")]
                for r in ROBOTS:
                    vm = vid.get(r) or {}
                    row += [_blank(vm.get("goodput_mbps")),
                            _blank(vm.get("frame_delivery_pct")),
                            _blank(vm.get("pkt_loss_pct")),
                            _blank(vm.get("latency_ms")),
                            _blank(vm.get("jitter_ms"))]
                row += [round(time.time() - cycle_t0, 1), 0]
                for r in ROBOTS:
                    row += ["", ""]           # event / outage_s: free roam has none
                row += ["free_roam", "", WORLD, "free", "", "", "", ""]

                w.writerow(row)
                f.flush()

                for r in ROBOTS:
                    _tcp = vals[r][0]
                    _tcp = f"{_tcp:.1f}M" if isinstance(_tcp, (int, float)) else "  n/a"
                    print(f"  {ROBOT_GLYPH.get(r, r[0])}"
                          f"({pos[r][0]:.0f},{pos[r][1]:.0f}) "
                          f"d={slant[r]:.0f}m ploss={ploss[r]:.1f}dB"
                          f"  TCP={_tcp}"
                          f"  C2={c2_rtt[r]:.0f}ms/{c2_loss[r]:.0f}%")
        finally:
            running.clear()
            # DiffDrive latches the last command — stop the wheels
            time.sleep(tick_s * 2)
            stop = Twist()
            for model in MODELS:
                try:
                    pubs[model].publish(stop)
                except Exception:
                    pass

    print(f"\nSaved: {PATROL_LOG}")


# ── SAR coverage search ──────────────────────────────────────────────────────
def _coverage_lawnmower(x_from, x_to, reach, lane, ystep=8):
    """Boustrophedon (lawnmower) waypoints covering the vertical strip
    between x_from and x_to within a `reach` radius, skipping building
    interiors (with a clearance margin). Adjacent columns run in opposite
    y-directions so the robot sweeps back and forth."""
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "worlds"))
        from urban_buildings import BUILDINGS
    except Exception:
        BUILDINGS = []

    def in_building(x, y):
        return any(abs(x - cx) < w / 2 + 2.5 and abs(y - cy) < d / 2 + 2.5
                   for cx, cy, w, d, h in BUILDINGS)

    step = int(lane) if x_to >= x_from else -int(lane)
    reach = int(reach)
    xs = list(range(int(x_from), int(x_to), step))
    wps = []
    for i, x in enumerate(xs):
        ys = list(range(-reach, reach + 1, int(ystep)))
        if i % 2:
            ys.reverse()
        for y in ys:
            if x * x + y * y <= reach * reach and not in_building(x, y):
                wps.append((float(x), float(y)))
    return wps


def search_both(speed_ms=1.0, duration_s=600, tick_s=0.1, reach=110, lane=18):
    """SAR area-coverage search: each robot sweeps a lawnmower pattern over
    half the search area (Alpha the +x half, Bravo the -x half), driving
    via DiffDrive and steering toward successive coverage waypoints while
    avoiding building interiors. Robots stream their sensor feed (the fixed
    UDP uplink) throughout, so the CSV/console capture link quality and
    delivered-data volume across the covered ground — the SAR mission of
    'search the area while streaming data back over the nomadic 5G link'.
    """
    global _gz_node
    if _gz_node is None:
        _gz_node = GzNode()

    wps = {
        ALPHA_MODEL: _coverage_lawnmower( 8,  reach, reach, lane),
        BRAVO_MODEL: _coverage_lawnmower(-8, -reach, reach, lane),
    }
    total = {m: max(1, len(w)) for m, w in wps.items()}
    print(f"  coverage plan: Alpha {total[ALPHA_MODEL]} cells (+x half), "
          f"Bravo {total[BRAVO_MODEL]} cells (-x half), lane {lane} m")

    fb_lock = threading.Lock()
    feedback = {}
    uav_now = [UAV_X, UAV_Y, UAV_Z]

    def pose_cb(msg):
        for p in msg.pose:
            if p.name in (ALPHA_MODEL, BRAVO_MODEL):
                q = p.orientation
                yaw = math.atan2(2.0 * (q.w * q.z + q.x * q.y),
                                 1.0 - 2.0 * (q.y * q.y + q.z * q.z))
                with fb_lock:
                    feedback[p.name] = (p.position.x, p.position.y, yaw)
            elif p.name == "sar_uav":
                with fb_lock:
                    uav_now[0], uav_now[1], uav_now[2] = \
                        p.position.x, p.position.y, p.position.z

    _gz_node.subscribe(Pose_V, f"/world/{WORLD}/pose/info", pose_cb)
    pubs = {
        ALPHA_MODEL: _gz_node.advertise(f"/model/{ALPHA_MODEL}/cmd_vel", Twist()),
        BRAVO_MODEL: _gz_node.advertise(f"/model/{BRAVO_MODEL}/cmd_vel", Twist()),
    }

    def current_pos(model, default):
        with fb_lock:
            fb = feedback.get(model)
        return (fb[0], fb[1]) if fb else default

    def current_uav():
        with fb_lock:
            return tuple(uav_now)

    gz_set_pose(ALPHA_MODEL, 8.0, -reach, GROUND_Z)
    gz_set_pose(BRAVO_MODEL, -8.0, -reach, GROUND_Z)
    time.sleep(2)

    running = threading.Event(); running.set()
    idx = {ALPHA_MODEL: 0, BRAVO_MODEL: 0}
    visited = {ALPHA_MODEL: 0, BRAVO_MODEL: 0}
    stuck = {ALPHA_MODEL: [time.time(), None], BRAVO_MODEL: [time.time(), None]}
    TOL, STUCK_S = 3.0, 9.0

    def driver():
        while running.is_set():
            for model in (ALPHA_MODEL, BRAVO_MODEL):
                wl = wps[model]
                i = idx[model]
                if i >= len(wl):                      # sweep complete → hold
                    try: pubs[model].publish(Twist())
                    except Exception: pass
                    continue
                with fb_lock:
                    fb = feedback.get(model)
                if fb is None:
                    continue
                x, y, yaw = fb
                tx, ty = wl[i]
                dx, dy = tx - x, ty - y
                dist = math.hypot(dx, dy)
                now = time.time()
                if dist < TOL:                        # reached this cell
                    idx[model] = i + 1
                    visited[model] += 1
                    stuck[model] = [now, None]
                    continue
                # obstacle/stuck: no progress toward the cell for STUCK_S → skip
                if stuck[model][1] is None or dist < stuck[model][1] - 0.3:
                    stuck[model] = [now, dist]
                elif now - stuck[model][0] > STUCK_S:
                    idx[model] = i + 1
                    stuck[model] = [now, None]
                    continue
                desired = math.atan2(dy, dx)
                # deflect around any building between the robot and the cell
                steer, blocked = avoid_heading(x, y, desired)
                err = math.atan2(math.sin(steer - yaw), math.cos(steer - yaw))
                tw = Twist()
                tw.angular.z = max(-1.5, min(1.5, 2.0 * err))
                tw.linear.x = speed_ms * max(0.05 if blocked else 0.1, math.cos(err))
                try: pubs[model].publish(tw)
                except Exception: pass
            time.sleep(tick_s)

    threading.Thread(target=driver, daemon=True).start()

    t0 = time.time()
    seen = {r: 0 for r in ROBOTS}
    prev = {r: {} for r in ROBOTS}

    # Persistent per-robot traffic: a rate-limited TCP uploader and a CBR UDP
    # video stream, started ONCE here rather than per cycle. Besides giving
    # steady-state throughput instead of slow-start samples, this is what the
    # columns were always meant to represent ("TCP goodput (sensor upload)",
    # "UDP video stream") and it makes the robots genuinely contend for the
    # cell, which is the regime the all-robots-connected objective is about.
    streams = TrafficStreams(ROBOTS, ROBOT_SPEC, sudo_pass=SUDO_PASS,
                             netns=NETNS_MODE)
    print(f"  traffic: persistent TCP+UDP flows per robot "
          f"({len(ROBOTS)} robots)", flush=True)
    streams.start()
    streamed_mb = {"a": 0.0, "b": 0.0}   # delivered UDP sensor data (MB)

    with open(PATROL_LOG, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow([
            "timestamp", "t_elapsed_s",
            "uav_x_m", "uav_y_m", "uav_z_m",
            "ax_m", "ay_m", "a_d_horiz_m", "a_d_slant_m", "a_ploss_dB",
            "a_tcp_tp_mbps", "a_tcp_retransmits",
            "a_udp_tp_mbps", "a_udp_jitter_ms", "a_udp_lost_pct",
            "a_rtt_min_ms", "a_rtt_avg_ms", "a_rtt_max_ms", "a_ping_loss_pct",
            "a_pbch_rsrp", "a_ss_rsrp_dBm", "a_ss_rsrp_dBre", "a_ss_sinr_dB", "a_ss_samples", "a_ss_stale", "a_probe_skipped",
            "a_ul_bits_per_sym", "a_ul_code_rate",
            "a_ul_nb_rbs", "a_ul_nb_sym",
            "a_dl_harq_ok", "a_dl_harq_nok", "a_dl_bler", "a_rsrp_computed_dBm",
            "bx_m", "by_m", "b_d_horiz_m", "b_d_slant_m", "b_ploss_dB",
            "b_tcp_tp_mbps", "b_tcp_retransmits",
            "b_udp_tp_mbps", "b_udp_jitter_ms", "b_udp_lost_pct",
            "b_rtt_min_ms", "b_rtt_avg_ms", "b_rtt_max_ms", "b_ping_loss_pct",
            "b_pbch_rsrp", "b_ss_rsrp_dBm", "b_ss_rsrp_dBre", "b_ss_sinr_dB", "b_ss_samples", "b_ss_stale", "b_probe_skipped",
            "b_ul_bits_per_sym", "b_ul_code_rate",
            "b_ul_nb_rbs", "b_ul_nb_sym",
            "b_dl_harq_ok", "b_dl_harq_nok", "b_dl_bler", "b_rsrp_computed_dBm",
            "a_c2_rtt_ms", "a_c2_loss_pct", "b_c2_rtt_ms", "b_c2_loss_pct",
            "a_coverage_pct", "b_coverage_pct", "a_streamed_mb", "b_streamed_mb",
        ])
        try:
            while time.time() - t0 < duration_s:
                # mission complete early if both robots finished their sweep
                if idx[ALPHA_MODEL] >= len(wps[ALPHA_MODEL]) and \
                   idx[BRAVO_MODEL] >= len(wps[BRAVO_MODEL]):
                    print("  coverage complete — both sweeps finished")
                    break

                a_start = current_pos(ALPHA_MODEL, (8.0, 0.0))
                b_start = current_pos(BRAVO_MODEL, (-8.0, 0.0))
                u_start = current_uav()
                dt0 = time.time()

                probe_busy.set()
                try:
                    vals_a, seen_a, prev_a = test_one_ue(ALPHA_TUN, ALPHA_IP, ALPHA_UE_LOG, seen_a, prev_a, NS_ALPHA)
                    time.sleep(2)
                    vals_b, seen_b, prev_b = test_one_ue(BRAVO_TUN, BRAVO_IP, BRAVO_UE_LOG, seen_b, prev_b, NS_BRAVO)
                    time.sleep(2)
                    # The cycle's own C2 reading is taken AFTER the iperf
                    # tests have finished, so it measures the link rather
                    # than the load — unlike the watchdog, which used to
                    # probe concurrently.
                    a_c2_rtt, a_c2_loss = run_c2(ALPHA_UE_IP)
                    b_c2_rtt, b_c2_loss = run_c2(BRAVO_UE_IP)
                finally:
                    probe_busy.clear()

                a_end = current_pos(ALPHA_MODEL, a_start)
                b_end = current_pos(BRAVO_MODEL, b_start)
                u_end = current_uav()
                dt = max(0.1, time.time() - dt0)

                ax = (a_start[0] + a_end[0]) / 2; ay = (a_start[1] + a_end[1]) / 2
                bx = (b_start[0] + b_end[0]) / 2; by = (b_start[1] + b_end[1]) / 2
                ux = (u_start[0] + u_end[0]) / 2; uy = (u_start[1] + u_end[1]) / 2
                uz = (u_start[2] + u_end[2]) / 2

                # streamed sensor data = delivered UDP rate integrated over dt
                streamed_mb["a"] += vals_a[2] * dt / 8.0
                streamed_mb["b"] += vals_b[2] * dt / 8.0
                cov_a = 100.0 * visited[ALPHA_MODEL] / total[ALPHA_MODEL]
                cov_b = 100.0 * visited[BRAVO_MODEL] / total[BRAVO_MODEL]

                t_elapsed = time.time() - t0
                ploss_a, _ = read_bridge_ploss_side("alpha")
                ploss_b, _ = read_bridge_ploss_side("bravo")
                d_a = math.hypot(ax - ux, ay - uy)
                d_b = math.hypot(bx - ux, by - uy)
                dz = abs(uz - GROUND_Z)
                a_slant = math.hypot(d_a, dz); b_slant = math.hypot(d_b, dz)
                a_rsrp_c = round(TX_REF - (ploss_a - DL_UL_OFFSET), 1)
                b_rsrp_c = round(TX_REF - (ploss_b - DL_UL_OFFSET), 1)

                row = [datetime.now(timezone.utc).isoformat(), round(t_elapsed, 1),
                       round(ux, 1), round(uy, 1), round(uz, 1),
                       round(ax, 1), round(ay, 1), round(d_a, 1), round(a_slant, 1), round(ploss_a, 2)]
                row += vals_a + [a_rsrp_c]
                row += [round(bx, 1), round(by, 1), round(d_b, 1), round(b_slant, 1), round(ploss_b, 2)]
                row += vals_b + [b_rsrp_c]
                row += [a_c2_rtt, a_c2_loss, b_c2_rtt, b_c2_loss]
                row += [round(cov_a, 1), round(cov_b, 1),
                        round(streamed_mb["a"], 1), round(streamed_mb["b"], 1)]
                w.writerow(row); f.flush()

                print(f"  α cov {cov_a:3.0f}% ({ax:.0f},{ay:.0f}) ploss={ploss_a:.1f}dB "
                      f"stream={vals_a[2]:.1f}M loss={vals_a[4]:.0f}% "
                      f"sent={streamed_mb['a']:.0f}MB C2={a_c2_rtt:.0f}ms")
                print(f"  β cov {cov_b:3.0f}% ({bx:.0f},{by:.0f}) ploss={ploss_b:.1f}dB "
                      f"stream={vals_b[2]:.1f}M loss={vals_b[4]:.0f}% "
                      f"sent={streamed_mb['b']:.0f}MB C2={b_c2_rtt:.0f}ms")
        finally:
            running.clear()
            streams.stop()
            with action_log_lock:
                action_log.close()
            print(f"  action log: {action_log_path}", flush=True)
            time.sleep(tick_s * 2)
            for model in (ALPHA_MODEL, BRAVO_MODEL):
                try: pubs[model].publish(Twist())
                except Exception: pass

    print(f"\nSearch done. Coverage: Alpha {100.0*visited[ALPHA_MODEL]/total[ALPHA_MODEL]:.0f}%, "
          f"Bravo {100.0*visited[BRAVO_MODEL]/total[BRAVO_MODEL]:.0f}%. "
          f"Streamed: {streamed_mb['a']:.0f}+{streamed_mb['b']:.0f} MB. Saved: {PATROL_LOG}")


# ── Research run: two-phase static-then-mobile gNB, with sticky-RLF recovery ──
def research_run(speed_ms=1.0, duration_s=RESEARCH_DURATION_S,
                 phase1_s=RESEARCH_PHASE1_S, tick_s=0.1,
                 seed=None, run_id=None, policy="two_phase", dwell=None):
    """Two-phase long-duration research run (default 6h = 3h + 3h).

      Phase 1 (0 .. phase1_s):   the aerial gNB (sar_uav) HOLDS position at
                                 (UAV_X, UAV_Y, UAV_Z); ground robots free-patrol.
      Phase 2 (phase1_s .. end): the gNB does a CONTINUOUS 3D random walk
                                 (x,y in ±GNB_XY_BOUND, z in [GNB_Z_MIN,
                                 GNB_Z_MAX], never dwelling); robots keep
                                 patrolling. Only gNB mobility changes between
                                 phases, so the two halves are directly
                                 comparable.

    A background watchdog probes each robot's C2 downlink; if one stays in
    100% loss for RLF_CONFIRM_S (sticky RLF), it reattaches ONLY that UE
    (reattach_ue) — no full RAN restart, so the gNB, the other robot, and the
    bridge are undisturbed. Each CSV row carries `phase` and, per robot, an
    `event` (reattaching / rlf_recovered / reattach_failed) and `outage_s`.

    `seed` makes the run repeatable: the gNB walk and each robot's heading
    draw from their OWN Random instance derived from it, rather than from the
    global `random` module. That matters because these run in separate
    threads — sharing one global stream makes the draw ORDER depend on thread
    interleaving, so the same seed would not reproduce the same trajectories.
    With per-thread streams each actor replays identically.

    Caveat, stated plainly: this fixes the trajectories, not the whole run.
    Channel shadow fading lives in channel_bridge.py (seed it separately with
    --shadow-seed), and the OAI/network timing underneath is not deterministic
    at all. So a replayed seed reproduces the same MOTION, not a bit-identical
    run.

    `policy` selects the gNB behaviour:

      "two_phase"  the published SATNAC behaviour — hold at one pose for
                   phase1_s, then a continuous 3D random walk. Default, so
                   existing campaigns reproduce unchanged.
      "excite"     the excitation policy (brief 6.1): sample a labelled action
                   from EXCITE_ACTIONS, commit to it for a dwell, repeat. This
                   is what breaks the hold/roam confound; see the comment on
                   EXCITE_ACTIONS. `phase` is written as 0 throughout, since
                   there is no static/nomadic split to report.

    LIMIT on paired counterfactuals (brief 6.3): the robot driver wanders an
    annulus centred on the LIVE gNB ground track, so robot motion depends on
    where the gNB went. Replaying one seed under two different policies
    therefore does NOT reproduce identical robot trajectories — the robots
    react to the gNB. True paired counterfactuals need that coupling removed
    (robots following a gNB-independent path), which is a behaviour change
    beyond this one and is not done here.
    """
    global _gz_node
    if _gz_node is None:
        _gz_node = GzNode()

    seed_used = random.randrange(2 ** 31) if seed is None else int(seed)
    run_id = run_id or f"{WORLD}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    # Separate streams per actor — see the docstring on why the global
    # `random` module will not do here.
    rng_gnb = random.Random(seed_used ^ 0xA5A5A5)
    rng_bot = random.Random(seed_used ^ 0x5A5A5A)
    print(f"[research] run_id={run_id} seed={seed_used} world={WORLD}",
          flush=True)

    # Separate stream for action sampling so that, for a given seed, the robot
    # headings do not shift when the gNB policy changes.
    rng_act = random.Random(seed_used ^ 0xC3C3C3)
    # Dwell band for the excitation policy. Long (30-90 s) keeps every slow
    # measurement cycle inside a single action, which the per-cycle
    # throughput and C2 columns need to be attributable. Short (15-35 s)
    # roughly triples the number of labelled actions per hour, at the cost of
    # some cycles straddling two actions — acceptable when the consumer is
    # the 10 Hz fast-radio log, which carries exact action boundaries anyway.
    dwell_band = tuple(dwell) if dwell else EXCITE_DWELL_S

    # The gNB action in force, sampled into each CSV row. Phase 1 is a true
    # hold at a fixed pose; phase 2 glides toward a fresh random 3D target.
    action_lock = threading.Lock()
    gnb_action = {"action": "hold", "tx": "", "ty": "", "tz": ""}

    # Authoritative action boundaries, one row per action, at full resolution.
    # The per-cycle CSV's `action` column is only the action in force when that
    # row happened to be written; if a dwell is shorter than the measurement
    # cycle the row straddles two actions. This log has the exact intervals, so
    # the 10 Hz fast-radio samples can be attributed to actions precisely.
    action_log_path = LOG_DIR / f"actions_{run_id}.csv"
    action_log = open(action_log_path, "w", newline="")
    action_w = csv.writer(action_log)
    action_w.writerow([
        "timestamp", "t_elapsed_s", "run_id", "seed", "terrain", "policy",
        "action", "dwell_s",
        "from_x_m", "from_y_m", "from_z_m",
        "to_x_m", "to_y_m", "to_z_m",
    ])
    action_log_lock = threading.Lock()

    def log_action(action, dwell, frm, to):
        with action_log_lock:
            action_w.writerow([
                datetime.now(timezone.utc).isoformat(),
                round(time.time() - t0, 2), run_id, seed_used, WORLD, policy,
                action, round(dwell, 2),
                round(frm[0], 2), round(frm[1], 2), round(frm[2], 2),
                round(to[0], 2), round(to[1], 2), round(to[2], 2),
            ])
            action_log.flush()

    fb_lock = threading.Lock()
    feedback = {}
    uav_now = [UAV_X, UAV_Y, UAV_Z]

    def pose_cb(msg):
        for p in msg.pose:
            if p.name in MODELS:
                q = p.orientation
                yaw = math.atan2(2.0 * (q.w * q.z + q.x * q.y),
                                 1.0 - 2.0 * (q.y * q.y + q.z * q.z))
                with fb_lock:
                    feedback[p.name] = (p.position.x, p.position.y, yaw)
            elif p.name == "sar_uav":
                with fb_lock:
                    uav_now[0], uav_now[1], uav_now[2] = \
                        p.position.x, p.position.y, p.position.z

    _gz_node.subscribe(Pose_V, f"/world/{WORLD}/pose/info", pose_cb)
    pubs = {
        m: _gz_node.advertise(f"/model/{m}/cmd_vel", Twist()) for m in MODELS
    }

    def current_pos(model, default):
        with fb_lock:
            fb = feedback.get(model)
        return (fb[0], fb[1]) if fb else default

    def current_uav():
        with fb_lock:
            return tuple(uav_now)

    # World-aware setup. Indoor: robots start OUTSIDE (south of the entry door)
    # and follow a waypoint path in through the door + doorways to search the
    # rooms; the gNB wanders a tighter box. Other worlds: free-roam patrol.
    is_indoor = (WORLD == "sar_indoor")
    if is_indoor:
        gnb_xy, gnb_zmin, gnb_zmax = INDOOR_GNB_XY, INDOOR_GNB_ZMIN, INDOOR_GNB_ZMAX
        # Indoor start points are defined per model; robots beyond the two
        # with explicit entries fall back to their roster start, offset so
        # they do not stack on the same spot outside the door.
        start0 = {r: INDOOR_START.get(ROBOT_SPEC[r]["model"],
                                      (ROBOT_SPEC[r]["start"][0], -30.0))
                  for r in ROBOTS}
        static_z = INDOOR_STATIC_Z
        # Start gNB OUTSIDE the building (south side) so O2I path is real
        gnb_x0, gnb_y0 = 0.0, -25.0
    else:
        gnb_xy, gnb_zmin, gnb_zmax = GNB_XY_BOUND, GNB_Z_MIN, GNB_Z_MAX
        start0 = {r: ROBOT_SPEC[r]["start"] for r in ROBOTS}
        static_z = UAV_Z
        gnb_x0, gnb_y0 = UAV_X, UAV_Y

    # one-time placement at start positions
    for r in ROBOTS:
        gz_set_pose(ROBOT_SPEC[r]["model"], start0[r][0], start0[r][1], GROUND_Z)
    gz_set_pose("sar_uav", gnb_x0, gnb_y0, static_z)
    time.sleep(2)
    # poll briefly for the first gNB pose before warning — the pose topic can
    # take a moment to deliver the first sample after (re)subscribing, so a
    # single immediate check gives a false "drone absent" alarm.
    for _ in range(10):
        with fb_lock:
            have_uav = "sar_uav" in feedback
        if have_uav:
            break
        time.sleep(0.5)
    if not have_uav:
        print("  WARNING: no sar_uav pose seen — if the gNB drone is absent "
              "from this world, gNB motion won't register and the bridge may "
              "skip rows.")

    running = threading.Event()
    running.set()
    t0 = time.time()

    # Set while the main loop has iperf/ping traffic in flight, so the
    # watchdog does not measure C2 against a saturated link and mistake
    # congestion for radio-link failure. See the watchdog docstring.
    probe_busy = threading.Event()

    # shared link-recovery state (watchdog ↔ main sampling loop)
    link_lock = threading.Lock()
    reattaching = {m: False for m in MODELS}
    pending_event = {m: ("", "") for m in MODELS}

    def _glide_to(tx, ty, tz, frm):
        """Glide the gNB from `frm` to the target at GNB_WALK_SPEED.

        Returns the pose actually reached — `frm` if the run was stopped
        mid-glide, so the caller never records a target it did not arrive at."""
        x, y, z = frm
        dist = math.sqrt((tx - x) ** 2 + (ty - y) ** 2 + (tz - z) ** 2)
        steps = max(1, int(dist / (GNB_WALK_SPEED * tick_s)))
        for i in range(1, steps + 1):
            if not running.is_set():
                return (x, y, z)
            f = i / steps
            gz_set_pose("sar_uav", x + (tx - x) * f,
                        y + (ty - y) * f, z + (tz - z) * f)
            time.sleep(tick_s)
        return (tx, ty, tz)

    def _weakest_xy(default):
        """Ground position of the robot most likely to be struggling.

        Uses the LARGER slant distance from the gNB as the proxy. Distance is
        chosen deliberately over the bridge's injected ploss: ploss is
        simulator ground truth, and a behaviour policy that consulted it could
        not be reproduced on hardware. Robot position is telemetry a real
        deployment has."""
        ux, uy, uz = current_uav()
        dz = abs(uz - GROUND_Z)
        worst, worst_d = None, -1.0
        for r in ROBOTS:
            px, py = current_pos(ROBOT_SPEC[r]["model"], ROBOT_SPEC[r]["start"])
            d = math.hypot(math.hypot(px - ux, py - uy), dz)
            if d > worst_d:
                worst, worst_d = (px, py), d
        return worst if worst is not None else default

    def _excite_pilot():
        """Randomised labelled actions over the robot patrols (brief 6.1).

        Each iteration samples one action, commits to it for a dwell drawn
        above the measurement cycle, and records the exact interval. Because
        the pose at the start of each action is wherever the previous actions
        left the gNB, `hold` is observed across the whole geofence instead of
        at a single pose — which is the entire point."""
        print(f"[gnb] excitation policy: actions={EXCITE_ACTIONS} "
              f"weights={EXCITE_WEIGHTS} dwell={dwell_band[0]:.0f}-"
              f"{dwell_band[1]:.0f}s", flush=True)
        x, y, z = current_uav()
        while running.is_set():
            action = rng_act.choices(EXCITE_ACTIONS, weights=EXCITE_WEIGHTS)[0]
            dwell = rng_act.uniform(*dwell_band)
            a_start = time.time()

            weakest = (_weakest_xy((x, y))
                       if action in ("toward_weak", "away_weak") else None)
            action, (tx, ty, tz) = action_to_target(
                action, (x, y, z), rng_act, weakest_xy=weakest,
                bounds=(gnb_xy, gnb_zmin, gnb_zmax))
            tx, ty, tz = geofence_target(tx, ty, tz, gnb_xy, gnb_zmin,
                                         gnb_zmax, indoor=is_indoor)
            with action_lock:
                gnb_action.update(action=action, tx=round(tx, 1),
                                  ty=round(ty, 1), tz=round(tz, 1))
            log_action(action, dwell, (x, y, z), (tx, ty, tz))

            x, y, z = _glide_to(tx, ty, tz, (x, y, z))
            # Hold at the target for whatever remains of the dwell. A long
            # glide simply consumes the whole dwell; the action still occupies
            # one contiguous, logged interval either way.
            while running.is_set() and time.time() - a_start < dwell:
                time.sleep(min(0.5, tick_s * 5))

    def _two_phase_pilot():
        """Phase 1: hold. Phase 2: continuous 3D random walk (no dwell).
        In the indoor world the drone is projected OUTSIDE the building
        footprint so the O2I penetration path is real."""
        gz_set_pose("sar_uav", gnb_x0, gnb_y0, static_z)
        with action_lock:
            gnb_action.update(action="hold", tx=round(gnb_x0, 1),
                              ty=round(gnb_y0, 1), tz=round(static_z, 1))
        log_action("hold", phase1_s, (gnb_x0, gnb_y0, static_z),
                   (gnb_x0, gnb_y0, static_z))
        while running.is_set() and time.time() - t0 < phase1_s:
            time.sleep(0.5)
        if not running.is_set():
            return
        print(f"[gnb] phase 2 — mobile gNB: continuous 3D random walk "
              f"(x,y ±{gnb_xy:.0f}m, z {gnb_zmin:.0f}-{gnb_zmax:.0f}m)",
              flush=True)
        with action_lock:
            gnb_action.update(action="walk", tx="", ty="", tz="")
        x, y, z = current_uav()
        while running.is_set():
            tx = rng_gnb.uniform(-gnb_xy, gnb_xy)
            ty = rng_gnb.uniform(-gnb_xy, gnb_xy)
            # Indoor world: keep drone OUTSIDE the warehouse footprint
            if is_indoor:
                tx, ty = _outside_warehouse(tx, ty)
            tz = rng_gnb.uniform(gnb_zmin, gnb_zmax)
            with action_lock:
                gnb_action.update(action="walk", tx=round(tx, 1),
                                  ty=round(ty, 1), tz=round(tz, 1))
            log_action("walk", 0.0, (x, y, z), (tx, ty, tz))
            x, y, z = _glide_to(tx, ty, tz, (x, y, z))

    # ── Phase 5 planner in the loop ──────────────────────────────────────
    # Shared with the research writer below, which feeds each completed cycle
    # row into the planner's history buffer.
    planner_holder = {}
    PLANNER_DWELL_S = 30.0      # receding horizon: surrogate forecasts 60 s

    def _planner_pilot():
        """Fly the Phase 5 planner: score 45 candidate waypoints against the
        leave-one-terrain-out surrogate and take the best one.

        The surrogate for this world was trained on the OTHER three terrains
        and never receives the terrain as an input, so the fly-off keeps the
        terrain blindness the offline results were built on.

        Any planner failure degrades to the excitation policy rather than
        stopping the run — a campaign that dies at 03:00 because a model file
        moved is a worse outcome than one that falls back and says so.
        """
        try:
            sys.path.insert(0, str(Path(__file__).resolve().parent / "ml"))
            from online_planner import OnlinePlanner
            planner_holder["p"] = OnlinePlanner(
                WORLD, beta=float(os.environ.get("SAR_PLANNER_BETA", 10.0)),
                log=lambda m: print(m, flush=True))
        except Exception as e:
            print(f"[gnb] planner unavailable ({type(e).__name__}: {e}) — "
                  f"falling back to excitation policy", flush=True)
            _excite_pilot()
            return

        x, y, z = current_uav()
        n_hold = n_act = 0
        while running.is_set():
            a_start = time.time()
            pick = planner_holder["p"].choose()
            if pick is None:
                # Not enough history yet, or a transient. Hold position: the
                # planner declines to steer rather than guessing.
                action, (tx, ty, tz) = "hold", (x, y, z)
                n_hold += 1
            else:
                dx, dy, dz, gain = pick
                tx, ty, tz = x + dx, y + dy, z + dz
                action = ("climb" if dz > 0 else "descend" if dz < 0
                          else ("step" if (dx or dy) else "hold"))
                n_act += 1
            tx, ty, tz = geofence_target(tx, ty, tz, gnb_xy, gnb_zmin,
                                         gnb_zmax, indoor=is_indoor)
            with action_lock:
                gnb_action.update(action=action, tx=round(tx, 1),
                                  ty=round(ty, 1), tz=round(tz, 1))
            log_action(action, PLANNER_DWELL_S, (x, y, z), (tx, ty, tz))
            x, y, z = _glide_to(tx, ty, tz, (x, y, z))
            while running.is_set() and time.time() - a_start < PLANNER_DWELL_S:
                time.sleep(min(0.5, tick_s * 5))

    def gnb_pilot():
        if policy == "planner":
            _planner_pilot()
        elif policy == "excite":
            _excite_pilot()
        else:
            _two_phase_pilot()

    # robot free-patrol driver: wander annulus centered on the live gNB ground
    # track; the guard (below) snaps a stranded robot back inside the coverage
    # edge, cutting how often the watchdog has to fire.
    targets = {m: rng_bot.random() * 2 * math.pi for m in MODELS}

    ploss_now = {m: 0.0 for m in MODELS}
    hi_since = {m: None for m in MODELS}
    last_pl_read = [0.0]
    side_of = dict(KEY_OF)

    def driver():
        while running.is_set():
            ux, uy, uz = current_uav()
            r_max = min(MAX_RADIUS, safe_radius(uz))
            # refresh REAL ploss (cheap tail read) at ~2 Hz — this is what makes
            # the guard NLoS-aware; safe_radius alone misses blockage
            if time.time() - last_pl_read[0] > 0.5:
                for m2 in MODELS:
                    p = read_ploss_fast(side_of[m2])
                    if p is not None:
                        ploss_now[m2] = p
                last_pl_read[0] = time.time()
            for model in MODELS:
                with fb_lock:
                    fb = feedback.get(model)
                if fb is None:
                    continue
                x, y, yaw = fb
                rx, ry = x - ux, y - uy
                r = math.hypot(rx, ry)
                pl = ploss_now[model]

                # HARD guard (NLoS-aware): real ploss near the RLF cliff for too
                # long -> teleport the robot close to the gNB (steep elevation
                # angle => near-guaranteed LoS in every world) so it never dies
                # where a reattach can't recover it.
                if pl >= PLOSS_TP_DB:
                    if hi_since[model] is None:
                        hi_since[model] = time.time()
                    elif time.time() - hi_since[model] > PLOSS_TP_HOLD_S:
                        ang = rng_bot.random() * 2 * math.pi
                        gz_set_pose(model, ux + TP_SAFE_RADIUS_M * math.cos(ang),
                                    uy + TP_SAFE_RADIUS_M * math.sin(ang), GROUND_Z)
                        targets[model] = rng_bot.random() * 2 * math.pi
                        hi_since[model] = None
                        print(f"[guard] {model} ploss {pl:.0f}dB (NLoS dead zone) "
                              f"-> teleported near gNB", flush=True)
                        continue
                else:
                    hi_since[model] = None

                # geometric far-guard (secondary bound, open-field)
                if r > r_max + 5:
                    f = (r_max * 0.6) / r
                    gz_set_pose(model, ux + rx * f, uy + ry * f, GROUND_Z)
                    targets[model] = rng_bot.random() * 2 * math.pi
                    continue

                # SOFT guard: elevated real ploss -> head toward the gNB to seek
                # better coverage (higher elevation angle, less NLoS depth);
                # avoid_heading still routes it around any building in the way
                if pl >= PLOSS_STEER_DB:
                    tgt = math.atan2(-ry, -rx)             # toward gNB ground track
                else:
                    tgt = targets[model] + rng_bot.gauss(0.0, 0.5 * tick_s)
                    if r > r_max - 5:
                        tgt = math.atan2(-ry, -rx)
                    elif r < MIN_RADIUS + 1:
                        tgt = math.atan2(ry, rx)
                targets[model] = tgt
                steer, blocked = avoid_heading(x, y, tgt)
                if blocked:
                    targets[model] = steer
                err = math.atan2(math.sin(steer - yaw), math.cos(steer - yaw))
                tw = Twist()
                tw.angular.z = max(-1.5, min(1.5, 2.0 * err))
                floor = 0.05 if blocked else 0.15
                tw.linear.x = speed_ms * max(floor, math.cos(err))
                try:
                    pubs[model].publish(tw)
                except Exception:
                    pass
            time.sleep(tick_s)

    # Indoor waypoint-follower with RLF recovery. Modes per robot:
    #   'enter'   — outside -> door -> its room
    #   'patrol'  — loop the room's points
    #   'retreat' — reversed entry path back toward the door when real ploss
    #               nears the RLF cliff, so the UE survives/reattaches; on
    #               recovery it re-enters. No UAV-annulus guard; a wedged robot
    #               skips to the next point.
    mode = {m: "enter" for m in MODELS}
    idx_i = {m: 0 for m in MODELS}
    stuck_i = {m: [time.time(), None] for m in MODELS}
    ploss_in = {m: 0.0 for m in MODELS}
    hi_since = {m: None for m in MODELS}
    last_pl = [0.0]
    side_of = dict(KEY_OF)
    wps_for = {"enter": INDOOR_ENTRY, "patrol": INDOOR_PATROL, "retreat": INDOOR_RETREAT}
    _warned_no_route = set()

    def indoor_driver():
      try:
        while running.is_set():
            # refresh real ploss (cheap tail read) at ~2 Hz
            if time.time() - last_pl[0] > 0.5:
                for m2 in MODELS:
                    p = read_ploss_fast(side_of[m2])
                    if p is not None:
                        ploss_in[m2] = p
                last_pl[0] = time.time()
            for model in MODELS:
                with fb_lock:
                    fb = feedback.get(model)
                if fb is None:
                    continue
                x, y, yaw = fb
                pl = ploss_in[model]
                now = time.time()

                # RLF-recovery transitions
                if mode[model] != "retreat":
                    if pl >= INDOOR_RETREAT_DB:
                        if hi_since[model] is None:
                            hi_since[model] = now
                        elif now - hi_since[model] > INDOOR_RETREAT_HOLD_S:
                            mode[model] = "retreat"
                            idx_i[model] = 0
                            hi_since[model] = None
                            stuck_i[model] = [now, None]
                            print(f"[indoor] {model} ploss {pl:.0f}dB near cliff "
                                  f"-> retreating to door", flush=True)
                    else:
                        hi_since[model] = None
                elif pl < INDOOR_RESUME_DB:              # recovered while retreating
                    mode[model] = "patrol"
                    idx_i[model] = 0
                    stuck_i[model] = [now, None]
                    print(f"[indoor] {model} recovered (ploss {pl:.0f}dB) "
                          f"-> re-entering", flush=True)

                # A robot with no route for this mode is skipped, not fatal.
                # Previously a missing key raised straight out of the thread,
                # killing the driver for EVERY robot — one unrouted robot
                # stopped all four, and the run looked healthy because the
                # links stayed pristine while nobody ever entered the
                # building (observed 2026-10-19 with charlie/delta).
                wl = wps_for[mode[model]].get(model)
                if not wl:
                    if model not in _warned_no_route:
                        _warned_no_route.add(model)
                        print(f"[indoor] WARNING: {model} has no '{mode[model]}' "
                              f"route — it will not be driven indoors",
                              flush=True)
                    continue
                i = idx_i[model]
                tx, ty = wl[i]
                dx, dy = tx - x, ty - y
                dist = math.hypot(dx, dy)
                if dist < INDOOR_WP_TOL:                 # reached this point
                    i += 1
                    stuck_i[model] = [now, None]
                    if i >= len(wl):
                        if mode[model] == "enter":
                            mode[model] = "patrol"
                            print(f"[indoor] {model} reached its room — patrolling",
                                  flush=True)
                        elif mode[model] == "retreat":
                            mode[model] = "enter"       # reached outside -> go back in
                        i = 0
                    idx_i[model] = i
                    continue
                # bumped a wall / wedged: no progress for a while -> skip point
                if stuck_i[model][1] is None or dist < stuck_i[model][1] - 0.2:
                    stuck_i[model] = [now, dist]
                elif now - stuck_i[model][0] > INDOOR_STUCK_S:
                    idx_i[model] = (i + 1) % len(wl)
                    stuck_i[model] = [now, None]
                    continue
                desired = math.atan2(dy, dx)
                err = math.atan2(math.sin(desired - yaw), math.cos(desired - yaw))
                tw = Twist()
                tw.angular.z = max(-1.5, min(1.5, 2.0 * err))
                # crawl indoors; slow through tight turns but never fully stop
                tw.linear.x = min(speed_ms, 0.6) * max(0.1, math.cos(err))
                try:
                    pubs[model].publish(tw)
                except Exception:
                    pass
            time.sleep(tick_s)

      except Exception as e:
        # A driver crash must be loud: without this the thread dies silently,
        # every robot stops moving, and the run keeps logging pristine links
        # from robots parked outside the building.
        import traceback
        print(f"[indoor] DRIVER CRASHED: {e}", flush=True)
        traceback.print_exc()
        running.clear()

    def watchdog():
        """Detect sticky RLF (sustained 100% C2 loss) and reattach that UE.

        Three things this deliberately does NOT do, each of which cost a run:

        1. It does not probe while the main loop has traffic in flight. The
           C2 probe is five small pings; an iperf3 TCP/UDP test saturating the
           same link makes them queue and drop, so a BUSY link reads as a DEAD
           one. In the 2026-07-30 indoor run the very first cycle logged TCP
           at 10 and 27 Mbps while the watchdog concurrently called 100% C2
           loss on both UEs — congestion misread as radio-link failure.

        2. It does not reattach on the downlink probe alone. Before declaring
           RLF it checks the UPLINK from inside the robot namespace. If the
           robot can still reach the core, the UE is attached and the radio is
           up, whatever the downlink probe says — so the right action is to
           leave it alone, not to kill a working modem.

        3. It does not retry forever (see RLF_MAX_FAILURES).
        """
        bad_since = {m: None for m in MODELS}
        failures = {m: 0 for m in MODELS}
        stood_down = {m: False for m in MODELS}
        while running.is_set():
            for model in MODELS:
                if not running.is_set():
                    return
                if stood_down[model]:
                    continue
                with link_lock:
                    if reattaching[model]:
                        continue
                # (1) never measure C2 against a link that is mid-iperf.
                #
                # Skip the MEASUREMENT, but do NOT clear bad_since. Clearing
                # it starves the watchdog exactly when it is needed: on a
                # degraded link the iperf/ping probes run to timeout, so the
                # probe window covers most of a cycle, and a watchdog that
                # resets its timer on every busy poll can never accumulate
                # RLF_CONFIRM_S. Observed 2026-10-19 on sar_indoor — C2 was
                # 100% lost for six minutes across several cycles and the
                # watchdog never fired. Leaving the timer running just means
                # "no new evidence this tick"; the next idle poll either
                # confirms the loss or clears it.
                # DO NOT skip on probe_busy any more.
                #
                # That guard was written when throughput was measured as a
                # short iperf BURST each cycle, so "busy" meant a few seconds
                # of saturation that would make a healthy link read as dead.
                # With persistent flows (traffic_streams.py) the link is
                # ALWAYS carrying traffic: measured 2026-10-21, cycle period
                # 16.4 s and probe window 16.4 s — a ZERO-second idle gap. So
                # "skip while busy" had become "never run", and the watchdog
                # was silently disabled for the whole campaign. alpha's modem
                # died 13 cycles into a 5.5 h run and was never recovered.
                #
                # Congestion false-positives are still handled, and better, by
                # the two guards that follow: RLF_CONFIRM_S of SUSTAINED 100%
                # loss, then uplink corroboration from inside the robot's
                # namespace before anything is killed.
                #
                # A missing TUN needs neither: the modem process is gone, so
                # there is nothing to misread. Treat it as RLF immediately.
                # A missing TUN is AUTHORITATIVE and must not be second-guessed
                # by the C2 probe. run_c2 pings the UE IP from the HOST, and a
                # TUN that is up in the root namespace still answers that ping
                # even when the robot's own namespace has no route at all — so
                # C2 reads "fine" while the robot is isolated. Observed
                # 2026-10-21 after setup_robot_netns.sh failed and left every
                # TUN in the root ns: the watchdog armed on the dead TUN, then
                # immediately disarmed itself on a healthy-looking host ping,
                # and looped. tun_gone therefore BYPASSES both the C2 test and
                # the corroboration step below.
                tun_gone = not _tun_present(UE_TUN_OF[model], NS_OF[model])
                if tun_gone:
                    print(f"[watchdog] {model}: TUN {UE_TUN_OF[model]} missing "
                          f"from {NS_OF[model]} — modem is gone, reattaching",
                          flush=True)
                _, loss = run_c2(UE_IP_OF[model])
                if tun_gone or loss >= 100.0:
                    if bad_since[model] is None:
                        bad_since[model] = time.time()
                        print(f"[watchdog] {model}: "
                              f"{'TUN gone' if tun_gone else 'C2 100% loss'} — "
                              f"arming RLF timer ({RLF_CONFIRM_S:.0f}s)",
                              flush=True)
                    elif tun_gone or time.time() - bad_since[model] >= RLF_CONFIRM_S:
                        # (2) corroborate before killing a modem — but only
                        # when there IS a modem to corroborate. With the TUN
                        # gone the uplink ping has no interface to leave by,
                        # so its result carries no information.
                        ul_loss = 100.0 if tun_gone else run_ping(
                            UE_TUN_OF[model], UE_TEST_IP_OF[model],
                            NS_OF[model])[3]
                        if ul_loss < 100.0:
                            print(f"[watchdog] {model}: C2 downlink looks lost "
                                  f"but the uplink still reaches the core "
                                  f"({ul_loss:.0f}% loss) — NOT an RLF, "
                                  f"disarming", flush=True)
                            bad_since[model] = None
                            continue
                        start = bad_since[model]
                        with link_lock:
                            reattaching[model] = True
                            pending_event[model] = ("reattaching", "")
                        print(f"[watchdog] {model}: sticky RLF confirmed — "
                              f"targeted UE reattach (gNB/other UE untouched)",
                              flush=True)
                        ok, detail = reattach_ue(model)
                        outage = round(time.time() - start, 1)
                        ev = "rlf_recovered" if ok else "reattach_failed"
                        with link_lock:
                            pending_event[model] = (ev, outage)
                            reattaching[model] = False
                        bad_since[model] = None
                        print(f"[watchdog] {model}: {ev} after {outage:.0f}s "
                              f"({detail})", flush=True)
                        # (3) stand down after repeated failures
                        failures[model] = 0 if ok else failures[model] + 1
                        if failures[model] >= RLF_MAX_FAILURES:
                            stood_down[model] = True
                            # Do NOT quietly carry on a robot short.
                            #
                            # A UE whose softmodem died cannot always be
                            # re-attached in rfsimulator: the relaunched
                            # process re-runs cell search and logs
                            # "synch Failed" forever, because the gNB's rfsim
                            # server still holds the dead client's slot
                            # (observed 2026-10-21/23). Standing the robot
                            # down means every later row records it as
                            # permanently disconnected — indistinguishable in
                            # the data from genuine, terrain-caused outage,
                            # and it silently turns an N-robot experiment into
                            # an (N-1)-robot one. alpha was lost this way 13
                            # cycles into a 5.5 h run.
                            #
                            # Exit instead. run_research_campaign.sh marks the
                            # run failed and starts the next world from a
                            # clean stack, which is the only thing that
                            # reliably recovers a dead UE.
                            print(f"[watchdog] {model}: {failures[model]} "
                                  f"consecutive failed reattaches — this UE "
                                  f"cannot be recovered in-run. ABORTING so "
                                  f"the campaign does not record an "
                                  f"{len(ROBOTS)}-robot run that is really "
                                  f"{len(ROBOTS) - 1} robots.", flush=True)
                            global _abort_reason
                            _abort_reason = (
                                f"{model}: {failures[model]} consecutive "
                                f"failed reattaches — UE unrecoverable in-run")
                            running.clear()
                            return
                else:
                    if bad_since[model] is not None:
                        print(f"[watchdog] {model}: C2 recovered on its own — "
                              f"disarming timer", flush=True)
                    bad_since[model] = None
            end = time.time() + WATCHDOG_PERIOD_S
            while running.is_set() and time.time() < end:
                time.sleep(0.5)

    threading.Thread(target=(indoor_driver if is_indoor else driver),
                     daemon=True).start()
    threading.Thread(target=gnb_pilot, daemon=True).start()
    if NETNS_MODE:
        threading.Thread(target=watchdog, daemon=True).start()
        print(f"  watchdog: ON — targeted reattach after {RLF_CONFIRM_S:.0f}s "
              f"of sticky C2 loss")
    else:
        print("  watchdog: OFF (no netns — the C2 path is loopback, so 100% "
              "loss can't be measured honestly)")

    seen = {r: 0 for r in ROBOTS}
    prev = {r: {} for r in ROBOTS}

    # Persistent per-robot traffic: a rate-limited TCP uploader and a CBR UDP
    # video stream, started ONCE here rather than per cycle. Besides giving
    # steady-state throughput instead of slow-start samples, this is what the
    # columns were always meant to represent ("TCP goodput (sensor upload)",
    # "UDP video stream") and it makes the robots genuinely contend for the
    # cell, which is the regime the all-robots-connected objective is about.
    # TRAFFIC MODE. "video" (default) runs a real RTP-style feed per robot
    # via video_streams.py and does NOT also run iperf: the two would compete
    # for the same cell and neither number would mean anything. "iperf" keeps
    # the old TCP+UDP flows for continuity with the archive.
    mode = os.environ.get("SAR_TRAFFIC_MODE", "video").strip().lower()
    if mode not in ("video", "iperf"):
        raise SystemExit(f"SAR_TRAFFIC_MODE must be video|iperf, got {mode!r}")
    video = None
    streams = None
    if mode == "video":
        from video_streams import VideoStreams, BITRATE_MBPS, FPS
        video = VideoStreams(ROBOTS, ROBOT_SPEC, sudo_pass=SUDO_PASS,
                             netns=NETNS_MODE)
        print(f"  traffic: VIDEO {BITRATE_MBPS} Mb/s @ {FPS} fps per robot "
              f"({len(ROBOTS)} robots) - goodput, frame delivery, one-way "
              f"latency and jitter", flush=True)
        video.start()
    else:
        streams = TrafficStreams(ROBOTS, ROBOT_SPEC, sudo_pass=SUDO_PASS,
                                 netns=NETNS_MODE)
        print(f"  traffic: persistent TCP+UDP flows per robot "
              f"({len(ROBOTS)} robots)", flush=True)
        streams.start()

    with open(PATROL_LOG, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(research_header(ROBOTS))

        try:
            while time.time() - t0 < duration_s:
                start_pos = {r: current_pos(ROBOT_SPEC[r]["model"],
                                            ROBOT_SPEC[r]["start"])
                             for r in ROBOTS}
                u_start = current_uav()

                vals, c2_rtt, c2_loss = {}, {}, {}
                probe_t, c2_t = {}, {}
                cycle_t0 = time.time()
                probe_busy.set()
                try:
                    for r in ROBOTS:
                        sp = ROBOT_SPEC[r]
                        vals[r], seen[r], prev[r] = test_one_ue(
                            sp["tun"], sp["test_ip"], sp["log"],
                            seen[r], prev[r], sp["ns"],
                            streams=streams, robot=r,
                            video_mode=(video is not None))
                        probe_t[r] = round(time.time() - cycle_t0, 1)
                    # The cycle's own C2 reading is taken AFTER the iperf
                    # tests have finished, so it measures the link rather
                    # than the load — unlike the watchdog, which used to
                    # probe concurrently.
                    for r in ROBOTS:
                        c2_rtt[r], c2_loss[r] = run_c2(ROBOT_SPEC[r]["ue_ip"])
                        c2_t[r] = round(time.time() - cycle_t0, 1)
                    vid = {r: (video.sample(r) if video else {})
                           for r in ROBOTS}
                finally:
                    probe_busy.clear()

                # A dead gNB means every subsequent row is meaningless.
                # Abort with a non-zero exit so run_research_campaign.sh marks
                # the run failed and moves to the next world, instead of
                # burning the remaining hours writing zeros.
                if _abort_reason:
                    print(f"FATAL: {_abort_reason}. Aborting this run.",
                          flush=True)
                    raise SystemExit(4)
                if not _gnb_alive():
                    print("FATAL: gNB process is gone — the cell no longer "
                          "exists. Aborting this run so the campaign does not "
                          "record hours of empty rows. Check /tmp/gnb.log for "
                          "an AssertFatal near the end.", flush=True)
                    raise SystemExit(3)

                end_pos = {r: current_pos(ROBOT_SPEC[r]["model"], start_pos[r])
                           for r in ROBOTS}
                u_end = current_uav()

                # Position reported for a cycle is the midpoint of where the
                # robot was when the cycle started and where it ended, since
                # the measurements span that whole interval.
                pos = {r: ((start_pos[r][0] + end_pos[r][0]) / 2,
                           (start_pos[r][1] + end_pos[r][1]) / 2)
                       for r in ROBOTS}
                ux = (u_start[0] + u_end[0]) / 2
                uy = (u_start[1] + u_end[1]) / 2
                uz = (u_start[2] + u_end[2]) / 2

                t_elapsed = time.time() - t0
                # Under "excite" there is no static/nomadic split to report,
                # so phase is written as 0 rather than inventing one.
                # Neither "excite" nor "planner" has a static/nomadic split
                # to report, so phase is written as 0 rather than invented.
                phase = (0 if policy in ("excite", "planner")
                         else (1 if t_elapsed < phase1_s else 2))
                dz = abs(uz - GROUND_Z)
                ploss, dist, slant, rsrp_c = {}, {}, {}, {}
                for r in ROBOTS:
                    ploss[r], _ = read_bridge_ploss_side(r)
                    dist[r] = math.hypot(pos[r][0] - ux, pos[r][1] - uy)
                    slant[r] = math.hypot(dist[r], dz)
                    # Simulator ground truth, logged for evaluation only —
                    # never a model input (DATA_AUDIT.md 4.5).
                    rsrp_c[r] = round(TX_REF - (ploss[r] - DL_UL_OFFSET), 1)

                # consume one-shot recovery events (keep 'reattaching' latched
                # while a reattach is still in progress)
                ev, out = {}, {}
                with link_lock:
                    for r in ROBOTS:
                        m = ROBOT_SPEC[r]["model"]
                        ev[r], out[r] = pending_event[m]
                        if ev[r] not in ("", "reattaching"):
                            pending_event[m] = ("", "")

                # Assembled in exactly the order research_header(ROBOTS)
                # declares. Both iterate ROBOTS, so adding or removing a
                # robot keeps them in step by construction.
                row = [datetime.now(timezone.utc).isoformat(),
                       round(t_elapsed, 1),
                       round(ux, 1), round(uy, 1), round(uz, 1)]
                for r in ROBOTS:
                    px, py = pos[r]
                    row += [round(px, 1), round(py, 1), round(dist[r], 1),
                            round(slant[r], 1), round(ploss[r], 2)]
                    row += vals[r] + [rsrp_c[r], probe_t.get(r, "")]
                for r in ROBOTS:
                    row += [c2_rtt[r], c2_loss[r], c2_t.get(r, "")]
                for r in ROBOTS:
                    vm = vid.get(r) or {}
                    # None means NOT MEASURED (feed relaunching) -> empty cell,
                    # never 0.0, which would read as a real outage.
                    row += [_blank(vm.get("goodput_mbps")),
                            _blank(vm.get("frame_delivery_pct")),
                            _blank(vm.get("pkt_loss_pct")),
                            _blank(vm.get("latency_ms")),
                            _blank(vm.get("jitter_ms"))]
                row += [round(time.time() - cycle_t0, 1), phase]
                for r in ROBOTS:
                    row += [ev[r], out[r]]
                with action_lock:
                    act = dict(gnb_action)
                row += [run_id, seed_used, WORLD, policy,
                        act["action"], act["tx"], act["ty"], act["tz"]]

                w.writerow(row)
                f.flush()

                # Feed the completed cycle to the planner's history buffer.
                # Guarded: the planner must never be able to stop the run.
                _pl = planner_holder.get("p")
                if _pl is not None:
                    try:
                        _pl.ingest(research_header(ROBOTS), row)
                    except Exception as _e:
                        print(f"[planner] ingest error: {type(_e).__name__}",
                              flush=True)

                hh = int(t_elapsed // 3600)
                mm = int((t_elapsed % 3600) // 60)
                print(f"  [{hh}h{mm:02d}m P{phase}] gNB({ux:.0f},{uy:.0f},{uz:.0f})")
                for r in ROBOTS:
                    # vals[r][0] is "" when the flow had just been relaunched
                    # and had produced no interval line yet (probe_skipped=2).
                    # "" has no float format, so render it as n/a.
                    _tcp = vals[r][0]
                    _tcp = f"{_tcp:.1f}M" if isinstance(_tcp, (int, float)) else "  n/a"
                    print(f"  {ROBOT_GLYPH.get(r, r[0])}"
                          f"({pos[r][0]:.0f},{pos[r][1]:.0f}) "
                          f"d={slant[r]:.0f}m ploss={ploss[r]:.1f}dB"
                          f"  TCP={_tcp}"
                          f"  C2={c2_rtt[r]:.0f}ms/{c2_loss[r]:.0f}%"
                          f"{('  <' + ev[r] + '>') if ev[r] else ''}")
        finally:
            running.clear()
            if streams is not None:
                streams.stop()
            if video is not None:
                video.stop()
            with action_log_lock:
                action_log.close()
            print(f"  action log: {action_log_path}", flush=True)
            time.sleep(tick_s * 2)
            stop = Twist()
            for model in MODELS:
                try:
                    pubs[model].publish(stop)
                except Exception:
                    pass

    print(f"\nResearch run done. Saved: {PATROL_LOG}")


def _term_handler(signum, frame):
    """Turn SIGTERM into KeyboardInterrupt so cleanup actually happens.

    Python's default SIGTERM handler terminates the process immediately, so
    no `finally` block runs — which meant video senders and collectors were
    LEFT RUNNING every time the campaign runner or `timeout` stopped a run.
    A leaked sender keeps transmitting into the next run's stack bring-up and
    corrupts its first cycles. Raising here lets research_run()'s finally
    stop the streams, close the action log and flush the CSV.
    """
    raise KeyboardInterrupt(f"signal {signum}")


signal.signal(signal.SIGTERM, _term_handler)

if __name__ == "__main__":
    mode = "linear"
    duration = None          # resolved after parsing (mode-dependent default)
    phase1 = None            # research mode: length of the static-gNB phase
    policy = "two_phase"     # research mode: gNB behaviour (see research_run)
    dwell = None             # excite mode: (min,max) dwell seconds, None = default
    seed = None              # research mode: RNG seed (None = pick and log one)
    run_id = None            # research mode: run identifier (None = autogenerate)
    step = 15
    speed = 1.0
    vary_uav = False
    reach = 110.0
    lane = 18.0

    for arg in sys.argv[1:]:
        if arg == "--circle":
            mode = "circle"
        elif arg == "--random":
            mode = "random"
        elif arg == "--both":
            mode = "both"
        elif arg == "--free":
            mode = "free"
        elif arg == "--search":
            mode = "search"
        elif arg == "--research":
            mode = "research"
        elif arg == "--excite":
            # Excitation data collection (brief 6.1): same measurement loop
            # and same CSV schema as --research, different gNB behaviour.
            mode = "research"
            policy = "excite"
        elif arg == "--planner":
            # Phase 5 fly-off: same measurement loop and CSV schema again,
            # but the gNB waypoint is chosen by the leave-one-terrain-out
            # surrogate instead of sampled.
            mode = "research"
            policy = "planner"
        elif arg == "--vary-uav":
            vary_uav = True
        elif arg.startswith("--duration="):
            duration = int(arg.split("=")[1])
        elif arg.startswith("--phase1="):
            phase1 = float(arg.split("=")[1])
        elif arg.startswith("--step="):
            step = float(arg.split("=")[1])
        elif arg.startswith("--speed="):
            speed = float(arg.split("=")[1])
        elif arg.startswith("--reach="):
            reach = float(arg.split("=")[1])
        elif arg.startswith("--lane="):
            lane = float(arg.split("=")[1])
        elif arg.startswith("--max-radius="):
            # <=110m: survivable long runs (bravo stays under ~39dB).
            # 150m: deliberately crosses the coverage edge — labeled
            # outage data, but expect a UE death + RAN restart per run.
            MAX_RADIUS = float(arg.split("=")[1])
        elif arg.startswith("--min-radius="):
            MIN_RADIUS = float(arg.split("=")[1])
        elif arg.startswith("--seed="):
            seed = int(arg.split("=")[1])
        elif arg.startswith("--run-id="):
            run_id = arg.split("=", 1)[1]
        elif arg.startswith("--dwell="):
            # --dwell=MIN,MAX seconds, excitation policy only
            try:
                lo, hi = (float(v) for v in arg.split("=", 1)[1].split(","))
            except ValueError:
                sys.exit(f"ERROR: --dwell wants MIN,MAX seconds, got "
                         f"'{arg.split('=', 1)[1]}' (e.g. --dwell=15,35)")
            if not 0 < lo <= hi:
                sys.exit(f"ERROR: --dwell needs 0 < MIN <= MAX, got {lo},{hi}")
            dwell = (lo, hi)
        else:
            # fail loudly: silently ignoring e.g. "-duration=300" (one
            # dash) ran the 180s default and wasted a session
            sys.exit(f"ERROR: unrecognized argument '{arg}' — valid: "
                     f"--free --search --research --excite --random --both --circle "
                     f"--vary-uav --duration=N --phase1=N --step=N --speed=N "
                     f"--reach=N --lane=N --max-radius=N --min-radius=N "
                     f"--seed=N --run-id=STR --dwell=MIN,MAX")

    # mode-dependent defaults for the two duration knobs
    if duration is None:
        duration = RESEARCH_DURATION_S if mode == "research" else 180
    if phase1 is None:
        phase1 = RESEARCH_PHASE1_S

    # Write our OWN pid to a well-known file. Prior runs captured the pid
    # via `pgrep | head -1`, which grabbed a transient (the setsid/nohup
    # chain), and when THAT exited a watcher wrongly declared the run over
    # and launched a second patrol on top of the still-running first —
    # two patrols fighting over the same robots (2026-07-12). Self-written
    # pid is unambiguous.
    Path("/tmp/patrol.pid").write_text(str(os.getpid()))

    print(f"Patrol mode={mode}, duration={duration}s, world={WORLD}")

    # Indoor-only guard: the indoor waypoint-follower and building geometry
    # only exist for sar_indoor.  Running them in other worlds would teleport
    # robots into outdoor coordinates with no wall awareness.
    if WORLD == "sar_indoor" and mode not in ("free", "research"):
        print(f"  ERROR: mode '{mode}' is not supported in the indoor world. "
              f"Use --free or --research instead.")
        sys.exit(1)
    if WORLD != "sar_indoor" and mode in ("research",):
        print(f"  NOTE: mode '{mode}' will use outdoor behaviour in '{WORLD}'.")

    if mode in ("random", "both"):
        print(f"  random walk, step={step}m, bounds=[{MIN_RADIUS},{MAX_RADIUS}]m")
    elif mode == "free":
        print(f"  free roam, speed={speed}m/s, bounds=[{MIN_RADIUS},{MAX_RADIUS}]m")
        if vary_uav:
            dwell = max(30.0, duration / len(UAV_SCHEDULE))
            print(f"  UAV schedule: {len(UAV_SCHEDULE)} waypoints, "
                  f"dwell {dwell/60:.1f}min each, z 20-60m, "
                  f"safe radius {safe_radius(20):.0f}-{safe_radius(60):.0f}m by altitude")
    elif mode == "search":
        print(f"  SAR coverage search, speed={speed}m/s, reach={reach}m, lane={lane}m, "
              f"streaming sensor feed throughout")
    elif mode == "research" and policy == "excite":
        print(f"  excitation run, speed={speed}m/s, {duration/3600:.2f}h: "
              f"randomised labelled gNB actions "
              f"({(dwell or EXCITE_DWELL_S)[0]:.0f}-"
              f"{(dwell or EXCITE_DWELL_S)[1]:.0f}s dwell); "
              f"sticky-RLF watchdog → targeted UE reattach")
    elif mode == "research":
        phase2 = max(0.0, duration - phase1)
        print(f"  research run, speed={speed}m/s: "
              f"phase 1 static gNB {phase1/3600:.1f}h, "
              f"phase 2 mobile gNB (3D random walk) {phase2/3600:.1f}h; "
              f"sticky-RLF watchdog → targeted UE reattach")
    print(f"Logging to: {PATROL_LOG}")
    print()

    if mode in ("random", "both"):
        random_walk_both(step_m=step, duration_s=duration)
    elif mode == "free":
        free_roam_both(speed_ms=speed, duration_s=duration, vary_uav=vary_uav)
    elif mode == "search":
        search_both(speed_ms=speed, duration_s=duration, reach=reach, lane=lane)
    elif mode == "research":
        research_run(speed_ms=speed, duration_s=duration, phase1_s=phase1,
                     seed=seed, run_id=run_id, policy=policy, dwell=dwell)
    else:
        linear_sweep(step=step, duration_s=duration)

    # gz.transport13's pybind bindings abort during normal interpreter teardown
    # ("scoped_acquire::dec_ref(): thread state must be current!" -> SIGABRT /
    # rc=134) because the pose-subscription callback thread still holds Python
    # refs at finalize. Every run function has already flushed and closed its
    # CSV, so flush the console and exit immediately, skipping the crashy
    # finalizer. Reached only on success — an exception propagates before here.
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(0)
