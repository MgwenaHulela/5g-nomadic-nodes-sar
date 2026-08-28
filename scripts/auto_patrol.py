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

sys.path.insert(0, "/usr/lib/python3/dist-packages")
from gz.transport13 import Node as GzNode
from gz.msgs10.pose_pb2 import Pose
from gz.msgs10.boolean_pb2 import Boolean
from gz.msgs10.twist_pb2 import Twist
from gz.msgs10.pose_v_pb2 import Pose_V

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

ALPHA_MODEL = "sar_ground_alpha"
BRAVO_MODEL = "sar_ground_bravo"
ALPHA_TUN = "oaitun_ue1"
BRAVO_TUN = "oaitun_ue1p2"
ALPHA_IP = "10.0.0.200"
BRAVO_IP = "10.0.0.201"

# Per-robot network namespaces (setup_robot_netns.sh). When active, the UE
# TUNs live inside these namespaces, so uplink tests must run there — and
# the UE IPs are reachable from the root ns ONLY over the air, which is
# what makes the C2/downlink metric honest.
NS_ALPHA = "robot_alpha"
NS_BRAVO = "robot_bravo"
ALPHA_UE_IP = "10.45.0.2"
BRAVO_UE_IP = "10.45.0.3"
NETNS_MODE = Path("/var/run/netns/robot_alpha").exists()
SUDO_PASS = os.environ.get("SAR_SUDO_PASS", "")  # export SAR_SUDO_PASS; never hardcode


def ns_run(ns, cmd, timeout):
    """Run cmd inside a robot namespace (or directly if netns not set up)."""
    if NETNS_MODE:
        return subprocess.run(
            ["sudo", "-S", "-p", "", "ip", "netns", "exec", ns] + cmd,
            capture_output=True, text=True, timeout=timeout,
            input=SUDO_PASS + "\n",
        )
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


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

# Targeted UE reattach — cures a sticky RLF WITHOUT restart_ran.sh's full RAN
# bounce. Only the dropped UE's softmodem is killed and relaunched (matched by
# its unique config path, so its fork-pair goes too); the gNB, the other UE,
# and the channel bridge keep running untouched. Mirrors restart_ran.sh's
# per-UE launch line and re-homes the fresh TUN into the robot netns.
OAI_DIR = str(Path.home() / "openairinterface5g")
CONF_DIR = str(Path.home() / "sar_sim" / "oai_configs")
UE_ARGS = ["-r", "106", "--numerology", "1", "--band", "78",
           "-C", "3319680000", "--ssb", "516"]
UE_CONF = {ALPHA_MODEL: "nrue_alpha.conf", BRAVO_MODEL: "nrue_bravo.conf"}
UE_TUN_OF = {ALPHA_MODEL: "oaitun_ue1", BRAVO_MODEL: "oaitun_ue1p2"}
UE_LOG_OF = {ALPHA_MODEL: ALPHA_UE_LOG, BRAVO_MODEL: BRAVO_UE_LOG}
UE_IP_OF = {ALPHA_MODEL: ALPHA_UE_IP, BRAVO_MODEL: BRAVO_UE_IP}
NS_OF = {ALPHA_MODEL: NS_ALPHA, BRAVO_MODEL: NS_BRAVO}
RLF_CONFIRM_S = 90.0            # sustained 100% C2 loss before declaring RLF
WATCHDOG_PERIOD_S = 20.0        # how often the watchdog probes each robot's C2
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
INDOOR_START = {ALPHA_MODEL: (0.0, -25.0), BRAVO_MODEL: (0.8, -23.0)}
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
}
INDOOR_PATROL = {   # after entry, loop the rooms the robot reached
    # Alpha: west rooms sweep — back-left, back-center, front-left
    ALPHA_MODEL: [(-20.0, 5.0), (0.0, 5.0), (0.0, -10.0), (-20.0, -10.0)],
    # Bravo: east rooms sweep — back-right, back-center, front-right
    BRAVO_MODEL: [(20.0, 5.0), (0.0, 5.0), (0.0, -10.0), (20.0, -10.0)],
}
# Indoor gNB stays OUTSIDE the building and LOW — O2I is penetration-limited,
# so raw distance eats the indoor link budget.  The drone starts south of the
# building and wanders within ±35 m, projected outside the footprint.
INDOOR_STATIC_Z = 25.0
INDOOR_GNB_XY, INDOOR_GNB_ZMIN, INDOOR_GNB_ZMAX = 35.0, 15.0, 35.0
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
    tags = {"alpha": "gnb_ue0_alpha_ul", "bravo": "gnb_ue1_bravo_ul"}
    tag = tags.get(side, "gnb_ue0_alpha_ul")
    for line in reversed(lines):
        m = re.search(rf'{tag}.*?ploss ([\d.]+)dB.*?d=([\d.]+)m', line)
        if m:
            return float(m.group(1)), float(m.group(2))
    return 0.0, 0.0


_PLOSS_TAGS = {"alpha": "gnb_ue0_alpha_ul", "bravo": "gnb_ue1_bravo_ul"}


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
        ["iperf3", "-c", test_ip, "--bind-dev", tun,
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
        ["iperf3", "-u", "-c", test_ip, "--bind-dev", tun,
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
        out = ns_run(ns,
            ["ping", "-c", "2", "-I", tun, "-W", "2", test_ip],
            timeout=8,
        ).stdout
        n_sent = 2
        m_loss = re.search(r'(\d+) packets transmitted, (\d+) (?:received|packets received)', out)
        n_recv = int(m_loss.group(2)) if m_loss else 0
        loss_pct = round((n_sent - n_recv) / n_sent * 100, 1)
        m_rtt = re.search(r'rtt min/avg/max/mdev = ([\d.]+)/([\d.]+)/([\d.]+)/([\d.]+) ms', out)
        if m_rtt:
            return float(m_rtt.group(1)), float(m_rtt.group(2)), float(m_rtt.group(3)), loss_pct
        return 0.0, 0.0, 0.0, loss_pct
    except Exception:
        return 0.0, 0.0, 0.0, 100.0


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
        try:
            sudo_run(["bash", setup_script, "setup"], 60)
        except Exception:
            pass

    # 6. confirm the C2 downlink is actually back
    time.sleep(2)
    _, loss = run_c2(ue_ip)
    return (loss < 100.0), f"post-reattach C2 loss {loss:.0f}%"


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


def fresh_l1(ue_log):
    """Newest SS-RSRP/SINR straight from the UE log tail. The windowed
    parser (metrics_collector) carries values that can be minutes stale;
    the modem prints these lines near-continuously, so the last one in
    the final 2MB is current to within ~a second. (Verified 2026-07-11:
    parser said -48 dBm while the log's fresh line read -64.)"""
    try:
        out = subprocess.run(
            ["bash", "-c", f"tail -c 2000000 {ue_log} | grep -a 'SS-RSRP' | tail -1"],
            capture_output=True, text=True, timeout=10).stdout
        m = re.search(r"SS-RSRP:\s*(-?\d+)\s*dBm.*?SS-SINR:\s*(-?[\d.]+)", out)
        if m:
            return float(m.group(1)), float(m.group(2))
    except Exception:
        pass
    return None, None


def test_one_ue(tun, test_ip, ue_log, seen, prev, ns=NS_ALPHA):
    """Run TCP+UDP+ping for one UE, return (row_values, new_seen, new_prev)."""
    lines, seen = tail_file(ue_log, seen)
    tmp = parse_ue_metrics(lines, prev)
    if tmp.get("ul_bits_per_sym", 0) > 0:
        prev = tmp

    tcp_tp, retrans = run_iperf_tcp(tun, test_ip, ns)
    udp_tp, jitter_ms, udp_lost = run_iperf_udp(tun, test_ip, ns)
    rtt_min, rtt_avg, rtt_max, ping_lost = run_ping(tun, test_ip, ns)

    lines, seen = tail_file(ue_log, seen)
    tmp = parse_ue_metrics(lines, prev)
    if tmp.get("ul_bits_per_sym", 0) > 0:
        prev = tmp

    m = prev
    # fresh L1 read overrides the (potentially stale) windowed parse
    f_rsrp, f_sinr = fresh_l1(ue_log)
    ss_rsrp = f_rsrp if f_rsrp is not None else m.get("ss_rsrp", "")
    ss_sinr = f_sinr if f_sinr is not None else m.get("ss_sinr", "")

    vals = [tcp_tp, retrans, udp_tp, jitter_ms, udp_lost,
            rtt_min, rtt_avg, rtt_max, ping_lost,
            m.get("pbch_rsrp", ""), ss_rsrp, ss_sinr,
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
            "alpha_pbch_rsrp", "alpha_ss_rsrp_dBm", "alpha_ss_sinr_dB",
            "alpha_ul_bits_per_sym", "alpha_ul_code_rate",
            "alpha_ul_nb_rbs", "alpha_ul_nb_sym",
            "alpha_dl_harq_ok", "alpha_dl_harq_nok", "alpha_dl_bler", "alpha_rsrp_computed_dBm",
            "bravo_pbch_rsrp", "bravo_ss_rsrp_dBm", "bravo_ss_sinr_dB",
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
            m_b = prev_b
            row += [m_b.get("pbch_rsrp", ""), m_b.get("ss_rsrp", ""), m_b.get("ss_sinr", ""),
                    m_b.get("ul_bits_per_sym", ""), m_b.get("ul_code_rate", ""),
                    m_b.get("ul_nb_rbs", ""), m_b.get("ul_nb_sym", ""),
                    m_b.get("dl_harq_ok", ""), m_b.get("dl_harq_nok", ""),
                    m_b.get("dl_bler", "")]

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
    seen_a, seen_b = 0, 0
    prev_a, prev_b = {}, {}
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
            "a_pbch_rsrp", "a_ss_rsrp_dBm", "a_ss_sinr_dB",
            "a_ul_bits_per_sym", "a_ul_code_rate",
            "a_ul_nb_rbs", "a_ul_nb_sym",
            "a_dl_harq_ok", "a_dl_harq_nok", "a_dl_bler", "a_rsrp_computed_dBm",
            "bx_m", "by_m", "b_d_horiz_m", "b_d_slant_m", "b_ploss_dB",
            "b_tcp_tp_mbps", "b_tcp_retransmits",
            "b_udp_tp_mbps", "b_udp_jitter_ms", "b_udp_lost_pct",
            "b_rtt_min_ms", "b_rtt_avg_ms", "b_rtt_max_ms", "b_ping_loss_pct",
            "b_pbch_rsrp", "b_ss_rsrp_dBm", "b_ss_sinr_dB",
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

    # one-time placement at start positions (teleport is fine for setup)
    is_indoor = (WORLD == "sar_indoor")
    if is_indoor:
        gz_set_pose(ALPHA_MODEL, *INDOOR_START[ALPHA_MODEL], GROUND_Z)
        gz_set_pose(BRAVO_MODEL, *INDOOR_START[BRAVO_MODEL], GROUND_Z)
    else:
        gz_set_pose(ALPHA_MODEL, 10.0, 0.0, GROUND_Z)
        gz_set_pose(BRAVO_MODEL, 25.0, 0.0, GROUND_Z)
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
    targets = {
        ALPHA_MODEL: random.random() * 2 * math.pi,
        BRAVO_MODEL: random.random() * 2 * math.pi,
    }

    def driver():
        while running.is_set():
            ux, uy, uz = current_uav()
            # outer bound: user cap AND the model's per-altitude edge
            r_max = min(MAX_RADIUS, safe_radius(uz))
            for model in (ALPHA_MODEL, BRAVO_MODEL):
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
    seen_a, seen_b = 0, 0
    prev_a, prev_b = {}, {}

    with open(PATROL_LOG, "w", newline="") as f:
        w = csv.writer(f)
        # Same schema as random_walk_both so downstream tooling is unaffected
        w.writerow([
            "timestamp", "t_elapsed_s",
            "uav_x_m", "uav_y_m", "uav_z_m",
            "ax_m", "ay_m", "a_d_horiz_m", "a_d_slant_m", "a_ploss_dB",
            "a_tcp_tp_mbps", "a_tcp_retransmits",
            "a_udp_tp_mbps", "a_udp_jitter_ms", "a_udp_lost_pct",
            "a_rtt_min_ms", "a_rtt_avg_ms", "a_rtt_max_ms", "a_ping_loss_pct",
            "a_pbch_rsrp", "a_ss_rsrp_dBm", "a_ss_sinr_dB",
            "a_ul_bits_per_sym", "a_ul_code_rate",
            "a_ul_nb_rbs", "a_ul_nb_sym",
            "a_dl_harq_ok", "a_dl_harq_nok", "a_dl_bler", "a_rsrp_computed_dBm",
            "bx_m", "by_m", "b_d_horiz_m", "b_d_slant_m", "b_ploss_dB",
            "b_tcp_tp_mbps", "b_tcp_retransmits",
            "b_udp_tp_mbps", "b_udp_jitter_ms", "b_udp_lost_pct",
            "b_rtt_min_ms", "b_rtt_avg_ms", "b_rtt_max_ms", "b_ping_loss_pct",
            "b_pbch_rsrp", "b_ss_rsrp_dBm", "b_ss_sinr_dB",
            "b_ul_bits_per_sym", "b_ul_code_rate",
            "b_ul_nb_rbs", "b_ul_nb_sym",
            "b_dl_harq_ok", "b_dl_harq_nok", "b_dl_bler", "b_rsrp_computed_dBm",
            "a_c2_rtt_ms", "a_c2_loss_pct", "b_c2_rtt_ms", "b_c2_loss_pct",
        ])

        try:
            while time.time() - t0 < duration_s:
                a_start = current_pos(ALPHA_MODEL, (10.0, 0.0))
                b_start = current_pos(BRAVO_MODEL, (25.0, 0.0))
                u_start = current_uav()

                vals_a, seen_a, prev_a = test_one_ue(ALPHA_TUN, ALPHA_IP, ALPHA_UE_LOG, seen_a, prev_a, NS_ALPHA)
                time.sleep(2)  # let the iperf servers + gNB queues idle between robots
                vals_b, seen_b, prev_b = test_one_ue(BRAVO_TUN, BRAVO_IP, BRAVO_UE_LOG, seen_b, prev_b, NS_BRAVO)
                time.sleep(2)  # C2 probes should measure the channel, not draining test queues
                a_c2_rtt, a_c2_loss = run_c2(ALPHA_UE_IP)
                b_c2_rtt, b_c2_loss = run_c2(BRAVO_UE_IP)

                a_end = current_pos(ALPHA_MODEL, a_start)
                b_end = current_pos(BRAVO_MODEL, b_start)
                u_end = current_uav()

                # Midpoint of the measurement window = best single-point tag
                ax = (a_start[0] + a_end[0]) / 2
                ay = (a_start[1] + a_end[1]) / 2
                bx = (b_start[0] + b_end[0]) / 2
                by = (b_start[1] + b_end[1]) / 2
                # UAV is live too (moves under --vary-uav)
                ux = (u_start[0] + u_end[0]) / 2
                uy = (u_start[1] + u_end[1]) / 2
                uz = (u_start[2] + u_end[2]) / 2

                t_elapsed = time.time() - t0
                ploss_a, _ = read_bridge_ploss_side("alpha")
                ploss_b, _ = read_bridge_ploss_side("bravo")
                d_a = math.hypot(ax - ux, ay - uy)
                d_b = math.hypot(bx - ux, by - uy)
                dz = abs(uz - GROUND_Z)
                a_slant = math.hypot(d_a, dz)
                b_slant = math.hypot(d_b, dz)
                a_rsrp_c = round(TX_REF - (ploss_a - DL_UL_OFFSET), 1)
                b_rsrp_c = round(TX_REF - (ploss_b - DL_UL_OFFSET), 1)

                row = [datetime.now(timezone.utc).isoformat(),
                       round(t_elapsed, 1),
                       round(ux, 1), round(uy, 1), round(uz, 1),
                       round(ax, 1), round(ay, 1), round(d_a, 1), round(a_slant, 1), round(ploss_a, 2)]
                row += vals_a + [a_rsrp_c]
                row += [round(bx, 1), round(by, 1), round(d_b, 1), round(b_slant, 1), round(ploss_b, 2)]
                row += vals_b + [b_rsrp_c]
                row += [a_c2_rtt, a_c2_loss, b_c2_rtt, b_c2_loss]

                w.writerow(row)
                f.flush()

                print(f"  α({ax:.0f},{ay:.0f}) d={d_a:.0f}m ploss={ploss_a:.1f}dB"
                      f"  TCP={vals_a[0]:.1f}M  PingPDR={100-vals_a[8]:.0f}%  RTT={vals_a[6]:.1f}ms"
                      f"  C2={a_c2_rtt:.1f}ms/{a_c2_loss:.0f}%loss")
                print(f"  β({bx:.0f},{by:.0f}) d={d_b:.0f}m ploss={ploss_b:.1f}dB"
                      f"  TCP={vals_b[0]:.1f}M  PingPDR={100-vals_b[8]:.0f}%  RTT={vals_b[6]:.1f}ms"
                      f"  C2={b_c2_rtt:.1f}ms/{b_c2_loss:.0f}%loss")
        finally:
            running.clear()
            # DiffDrive latches the last command — stop the wheels
            time.sleep(tick_s * 2)
            stop = Twist()
            for model in (ALPHA_MODEL, BRAVO_MODEL):
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
    seen_a, seen_b = 0, 0
    prev_a, prev_b = {}, {}
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
            "a_pbch_rsrp", "a_ss_rsrp_dBm", "a_ss_sinr_dB",
            "a_ul_bits_per_sym", "a_ul_code_rate",
            "a_ul_nb_rbs", "a_ul_nb_sym",
            "a_dl_harq_ok", "a_dl_harq_nok", "a_dl_bler", "a_rsrp_computed_dBm",
            "bx_m", "by_m", "b_d_horiz_m", "b_d_slant_m", "b_ploss_dB",
            "b_tcp_tp_mbps", "b_tcp_retransmits",
            "b_udp_tp_mbps", "b_udp_jitter_ms", "b_udp_lost_pct",
            "b_rtt_min_ms", "b_rtt_avg_ms", "b_rtt_max_ms", "b_ping_loss_pct",
            "b_pbch_rsrp", "b_ss_rsrp_dBm", "b_ss_sinr_dB",
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

                vals_a, seen_a, prev_a = test_one_ue(ALPHA_TUN, ALPHA_IP, ALPHA_UE_LOG, seen_a, prev_a, NS_ALPHA)
                time.sleep(2)
                vals_b, seen_b, prev_b = test_one_ue(BRAVO_TUN, BRAVO_IP, BRAVO_UE_LOG, seen_b, prev_b, NS_BRAVO)
                time.sleep(2)
                a_c2_rtt, a_c2_loss = run_c2(ALPHA_UE_IP)
                b_c2_rtt, b_c2_loss = run_c2(BRAVO_UE_IP)

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
            time.sleep(tick_s * 2)
            for model in (ALPHA_MODEL, BRAVO_MODEL):
                try: pubs[model].publish(Twist())
                except Exception: pass

    print(f"\nSearch done. Coverage: Alpha {100.0*visited[ALPHA_MODEL]/total[ALPHA_MODEL]:.0f}%, "
          f"Bravo {100.0*visited[BRAVO_MODEL]/total[BRAVO_MODEL]:.0f}%. "
          f"Streamed: {streamed_mb['a']:.0f}+{streamed_mb['b']:.0f} MB. Saved: {PATROL_LOG}")


# ── Research run: two-phase static-then-mobile gNB, with sticky-RLF recovery ──
def research_run(speed_ms=1.0, duration_s=RESEARCH_DURATION_S,
                 phase1_s=RESEARCH_PHASE1_S, tick_s=0.1):
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
    """
    global _gz_node
    if _gz_node is None:
        _gz_node = GzNode()

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

    # World-aware setup. Indoor: robots start OUTSIDE (south of the entry door)
    # and follow a waypoint path in through the door + doorways to search the
    # rooms; the gNB wanders a tighter box. Other worlds: free-roam patrol.
    is_indoor = (WORLD == "sar_indoor")
    if is_indoor:
        gnb_xy, gnb_zmin, gnb_zmax = INDOOR_GNB_XY, INDOOR_GNB_ZMIN, INDOOR_GNB_ZMAX
        a_start0, b_start0 = INDOOR_START[ALPHA_MODEL], INDOOR_START[BRAVO_MODEL]
        static_z = INDOOR_STATIC_Z
        # Start gNB OUTSIDE the building (south side) so O2I path is real
        gnb_x0, gnb_y0 = 0.0, -25.0
    else:
        gnb_xy, gnb_zmin, gnb_zmax = GNB_XY_BOUND, GNB_Z_MIN, GNB_Z_MAX
        a_start0, b_start0 = (10.0, 0.0), (25.0, 0.0)
        static_z = UAV_Z
        gnb_x0, gnb_y0 = UAV_X, UAV_Y

    # one-time placement at start positions
    gz_set_pose(ALPHA_MODEL, a_start0[0], a_start0[1], GROUND_Z)
    gz_set_pose(BRAVO_MODEL, b_start0[0], b_start0[1], GROUND_Z)
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

    # shared link-recovery state (watchdog ↔ main sampling loop)
    link_lock = threading.Lock()
    reattaching = {ALPHA_MODEL: False, BRAVO_MODEL: False}
    pending_event = {ALPHA_MODEL: ("", ""), BRAVO_MODEL: ("", "")}

    def gnb_pilot():
        """Phase 1: hold. Phase 2: continuous 3D random walk (no dwell).
        In the indoor world the drone is projected OUTSIDE the building
        footprint so the O2I penetration path is real."""
        gz_set_pose("sar_uav", gnb_x0, gnb_y0, static_z)
        while running.is_set() and time.time() - t0 < phase1_s:
            time.sleep(0.5)
        if not running.is_set():
            return
        print(f"[gnb] phase 2 — mobile gNB: continuous 3D random walk "
              f"(x,y ±{gnb_xy:.0f}m, z {gnb_zmin:.0f}-{gnb_zmax:.0f}m)",
              flush=True)
        x, y, z = current_uav()
        while running.is_set():
            tx = random.uniform(-gnb_xy, gnb_xy)
            ty = random.uniform(-gnb_xy, gnb_xy)
            # Indoor world: keep drone OUTSIDE the warehouse footprint
            if is_indoor:
                tx, ty = _outside_warehouse(tx, ty)
            tz = random.uniform(gnb_zmin, gnb_zmax)
            dist = math.sqrt((tx - x) ** 2 + (ty - y) ** 2 + (tz - z) ** 2)
            steps = max(1, int(dist / (GNB_WALK_SPEED * tick_s)))
            for s in range(1, steps + 1):
                if not running.is_set():
                    return
                f = s / steps
                gz_set_pose("sar_uav", x + (tx - x) * f,
                            y + (ty - y) * f, z + (tz - z) * f)
                time.sleep(tick_s)
            x, y, z = tx, ty, tz

    # robot free-patrol driver: wander annulus centered on the live gNB ground
    # track; the guard (below) snaps a stranded robot back inside the coverage
    # edge, cutting how often the watchdog has to fire.
    targets = {
        ALPHA_MODEL: random.random() * 2 * math.pi,
        BRAVO_MODEL: random.random() * 2 * math.pi,
    }

    ploss_now = {ALPHA_MODEL: 0.0, BRAVO_MODEL: 0.0}
    hi_since = {ALPHA_MODEL: None, BRAVO_MODEL: None}
    last_pl_read = [0.0]
    side_of = {ALPHA_MODEL: "alpha", BRAVO_MODEL: "bravo"}

    def driver():
        while running.is_set():
            ux, uy, uz = current_uav()
            r_max = min(MAX_RADIUS, safe_radius(uz))
            # refresh REAL ploss (cheap tail read) at ~2 Hz — this is what makes
            # the guard NLoS-aware; safe_radius alone misses blockage
            if time.time() - last_pl_read[0] > 0.5:
                for m2 in (ALPHA_MODEL, BRAVO_MODEL):
                    p = read_ploss_fast(side_of[m2])
                    if p is not None:
                        ploss_now[m2] = p
                last_pl_read[0] = time.time()
            for model in (ALPHA_MODEL, BRAVO_MODEL):
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
                        ang = random.random() * 2 * math.pi
                        gz_set_pose(model, ux + TP_SAFE_RADIUS_M * math.cos(ang),
                                    uy + TP_SAFE_RADIUS_M * math.sin(ang), GROUND_Z)
                        targets[model] = random.random() * 2 * math.pi
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
                    targets[model] = random.random() * 2 * math.pi
                    continue

                # SOFT guard: elevated real ploss -> head toward the gNB to seek
                # better coverage (higher elevation angle, less NLoS depth);
                # avoid_heading still routes it around any building in the way
                if pl >= PLOSS_STEER_DB:
                    tgt = math.atan2(-ry, -rx)             # toward gNB ground track
                else:
                    tgt = targets[model] + random.gauss(0.0, 0.5 * tick_s)
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
    mode = {ALPHA_MODEL: "enter", BRAVO_MODEL: "enter"}
    idx_i = {ALPHA_MODEL: 0, BRAVO_MODEL: 0}
    stuck_i = {ALPHA_MODEL: [time.time(), None], BRAVO_MODEL: [time.time(), None]}
    ploss_in = {ALPHA_MODEL: 0.0, BRAVO_MODEL: 0.0}
    hi_since = {ALPHA_MODEL: None, BRAVO_MODEL: None}
    last_pl = [0.0]
    side_of = {ALPHA_MODEL: "alpha", BRAVO_MODEL: "bravo"}
    wps_for = {"enter": INDOOR_ENTRY, "patrol": INDOOR_PATROL, "retreat": INDOOR_RETREAT}

    def indoor_driver():
        while running.is_set():
            # refresh real ploss (cheap tail read) at ~2 Hz
            if time.time() - last_pl[0] > 0.5:
                for m2 in (ALPHA_MODEL, BRAVO_MODEL):
                    p = read_ploss_fast(side_of[m2])
                    if p is not None:
                        ploss_in[m2] = p
                last_pl[0] = time.time()
            for model in (ALPHA_MODEL, BRAVO_MODEL):
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

                wl = wps_for[mode[model]][model]
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

    def watchdog():
        """Detect sticky RLF (sustained 100% C2 loss) and reattach that UE."""
        bad_since = {ALPHA_MODEL: None, BRAVO_MODEL: None}
        while running.is_set():
            for model in (ALPHA_MODEL, BRAVO_MODEL):
                if not running.is_set():
                    return
                with link_lock:
                    if reattaching[model]:
                        continue
                _, loss = run_c2(UE_IP_OF[model])
                if loss >= 100.0:
                    if bad_since[model] is None:
                        bad_since[model] = time.time()
                        print(f"[watchdog] {model}: C2 100% loss — arming RLF "
                              f"timer ({RLF_CONFIRM_S:.0f}s)", flush=True)
                    elif time.time() - bad_since[model] >= RLF_CONFIRM_S:
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

    seen_a, seen_b = 0, 0
    prev_a, prev_b = {}, {}

    with open(PATROL_LOG, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow([
            "timestamp", "t_elapsed_s",
            "uav_x_m", "uav_y_m", "uav_z_m",
            "ax_m", "ay_m", "a_d_horiz_m", "a_d_slant_m", "a_ploss_dB",
            "a_tcp_tp_mbps", "a_tcp_retransmits",
            "a_udp_tp_mbps", "a_udp_jitter_ms", "a_udp_lost_pct",
            "a_rtt_min_ms", "a_rtt_avg_ms", "a_rtt_max_ms", "a_ping_loss_pct",
            "a_pbch_rsrp", "a_ss_rsrp_dBm", "a_ss_sinr_dB",
            "a_ul_bits_per_sym", "a_ul_code_rate",
            "a_ul_nb_rbs", "a_ul_nb_sym",
            "a_dl_harq_ok", "a_dl_harq_nok", "a_dl_bler", "a_rsrp_computed_dBm",
            "bx_m", "by_m", "b_d_horiz_m", "b_d_slant_m", "b_ploss_dB",
            "b_tcp_tp_mbps", "b_tcp_retransmits",
            "b_udp_tp_mbps", "b_udp_jitter_ms", "b_udp_lost_pct",
            "b_rtt_min_ms", "b_rtt_avg_ms", "b_rtt_max_ms", "b_ping_loss_pct",
            "b_pbch_rsrp", "b_ss_rsrp_dBm", "b_ss_sinr_dB",
            "b_ul_bits_per_sym", "b_ul_code_rate",
            "b_ul_nb_rbs", "b_ul_nb_sym",
            "b_dl_harq_ok", "b_dl_harq_nok", "b_dl_bler", "b_rsrp_computed_dBm",
            "a_c2_rtt_ms", "a_c2_loss_pct", "b_c2_rtt_ms", "b_c2_loss_pct",
            "phase", "a_event", "a_outage_s", "b_event", "b_outage_s",
        ])

        try:
            while time.time() - t0 < duration_s:
                a_start = current_pos(ALPHA_MODEL, (10.0, 0.0))
                b_start = current_pos(BRAVO_MODEL, (25.0, 0.0))
                u_start = current_uav()

                vals_a, seen_a, prev_a = test_one_ue(ALPHA_TUN, ALPHA_IP, ALPHA_UE_LOG, seen_a, prev_a, NS_ALPHA)
                time.sleep(2)
                vals_b, seen_b, prev_b = test_one_ue(BRAVO_TUN, BRAVO_IP, BRAVO_UE_LOG, seen_b, prev_b, NS_BRAVO)
                time.sleep(2)
                a_c2_rtt, a_c2_loss = run_c2(ALPHA_UE_IP)
                b_c2_rtt, b_c2_loss = run_c2(BRAVO_UE_IP)

                a_end = current_pos(ALPHA_MODEL, a_start)
                b_end = current_pos(BRAVO_MODEL, b_start)
                u_end = current_uav()

                ax = (a_start[0] + a_end[0]) / 2
                ay = (a_start[1] + a_end[1]) / 2
                bx = (b_start[0] + b_end[0]) / 2
                by = (b_start[1] + b_end[1]) / 2
                ux = (u_start[0] + u_end[0]) / 2
                uy = (u_start[1] + u_end[1]) / 2
                uz = (u_start[2] + u_end[2]) / 2

                t_elapsed = time.time() - t0
                phase = 1 if t_elapsed < phase1_s else 2
                ploss_a, _ = read_bridge_ploss_side("alpha")
                ploss_b, _ = read_bridge_ploss_side("bravo")
                d_a = math.hypot(ax - ux, ay - uy)
                d_b = math.hypot(bx - ux, by - uy)
                dz = abs(uz - GROUND_Z)
                a_slant = math.hypot(d_a, dz)
                b_slant = math.hypot(d_b, dz)
                a_rsrp_c = round(TX_REF - (ploss_a - DL_UL_OFFSET), 1)
                b_rsrp_c = round(TX_REF - (ploss_b - DL_UL_OFFSET), 1)

                # consume one-shot recovery events (keep 'reattaching' latched
                # while a reattach is still in progress)
                with link_lock:
                    a_ev, a_out = pending_event[ALPHA_MODEL]
                    b_ev, b_out = pending_event[BRAVO_MODEL]
                    if a_ev not in ("", "reattaching"):
                        pending_event[ALPHA_MODEL] = ("", "")
                    if b_ev not in ("", "reattaching"):
                        pending_event[BRAVO_MODEL] = ("", "")

                row = [datetime.now(timezone.utc).isoformat(),
                       round(t_elapsed, 1),
                       round(ux, 1), round(uy, 1), round(uz, 1),
                       round(ax, 1), round(ay, 1), round(d_a, 1), round(a_slant, 1), round(ploss_a, 2)]
                row += vals_a + [a_rsrp_c]
                row += [round(bx, 1), round(by, 1), round(d_b, 1), round(b_slant, 1), round(ploss_b, 2)]
                row += vals_b + [b_rsrp_c]
                row += [a_c2_rtt, a_c2_loss, b_c2_rtt, b_c2_loss]
                row += [phase, a_ev, a_out, b_ev, b_out]

                w.writerow(row)
                f.flush()

                hh = int(t_elapsed // 3600)
                mm = int((t_elapsed % 3600) // 60)
                print(f"  [{hh}h{mm:02d}m P{phase}] gNB({ux:.0f},{uy:.0f},{uz:.0f})")
                print(f"  α({ax:.0f},{ay:.0f}) d={a_slant:.0f}m ploss={ploss_a:.1f}dB"
                      f"  TCP={vals_a[0]:.1f}M  C2={a_c2_rtt:.0f}ms/{a_c2_loss:.0f}%"
                      f"{('  <' + a_ev + '>') if a_ev else ''}")
                print(f"  β({bx:.0f},{by:.0f}) d={b_slant:.0f}m ploss={ploss_b:.1f}dB"
                      f"  TCP={vals_b[0]:.1f}M  C2={b_c2_rtt:.0f}ms/{b_c2_loss:.0f}%"
                      f"{('  <' + b_ev + '>') if b_ev else ''}")
        finally:
            running.clear()
            time.sleep(tick_s * 2)
            stop = Twist()
            for model in (ALPHA_MODEL, BRAVO_MODEL):
                try:
                    pubs[model].publish(stop)
                except Exception:
                    pass

    print(f"\nResearch run done. Saved: {PATROL_LOG}")


if __name__ == "__main__":
    mode = "linear"
    duration = None          # resolved after parsing (mode-dependent default)
    phase1 = None            # research mode: length of the static-gNB phase
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
        else:
            # fail loudly: silently ignoring e.g. "-duration=300" (one
            # dash) ran the 180s default and wasted a session
            sys.exit(f"ERROR: unrecognized argument '{arg}' — valid: "
                     f"--free --search --research --random --both --circle "
                     f"--vary-uav --duration=N --phase1=N --step=N --speed=N "
                     f"--reach=N --lane=N --max-radius=N --min-radius=N")

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
        research_run(speed_ms=speed, duration_s=duration, phase1_s=phase1)
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
