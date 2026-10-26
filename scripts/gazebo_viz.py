"""Gazebo visualization for SAR patrol — markers (beams + text) + metrics topic.

Runs as a standalone process:
  1. Subscribes to /world/sar_open_field/pose/info (Pose_V) for positions
  2. Reads /tmp/ch_bridge.log for ploss/distance per UE
  3. Publishes beam markers + text labels + /sar/metrics string

Usage:
  python3 scripts/gazebo_viz.py
  # or alongside start_full_stack.sh
"""

import json
import os
import re
import subprocess
import threading
import time
import sys
from pathlib import Path

sys.path.insert(0, "/usr/lib/python3/dist-packages")

from gz.transport13 import Node
from gz.msgs10 import marker_pb2, stringmsg_pb2
from gz.msgs10.empty_pb2 import Empty
from gz.msgs10.pose_v_pb2 import Pose_V

# ── Constants ─────────────────────────────────────────────────────────────────
NS = "sar_patrol"
# Marker ids are allocated per robot from these bases, so adding a robot to
# the roster adds its beam and orb automatically. Kept apart so the two
# families never collide as the roster grows.
ID_BEAM_BASE = 100
ID_ORB_BASE = 200
ID_BEAM_ALPHA = ID_BEAM_BASE      # retained: referenced by the clear path
ID_BEAM_BRAVO = ID_BEAM_BASE + 1
ID_ORB_ALPHA = ID_ORB_BASE
ID_ORB_BRAVO = ID_ORB_BASE + 1

# ── Lazy-initialised globals ──────────────────────────────────────────────────
_node = None
_marker_pub = None
_json_pub = None       # /sar/metrics       — JSON, read by SarMetricsTable plugin
_text_pub = None       # /sar/metrics_text  — plain text fallback (TopicEcho / CLI)
_lock = threading.Lock()


def _ensure_node():
    global _node, _json_pub, _text_pub
    with _lock:
        if _node is not None:
            return
        _node = Node()
        # /sar/metrics carries JSON for the SarMetricsTable GUI plugin;
        # /sar/metrics_text keeps the old human-readable table for TopicEcho
        # and `gz topic -e -t /sar/metrics_text` debugging.
        _json_pub = _node.advertise("/sar/metrics", stringmsg_pb2.StringMsg())
        _text_pub = _node.advertise("/sar/metrics_text", stringmsg_pb2.StringMsg())
        # Start a keep-alive spin loop so the node processes messages
        def _spin():
            while True:
                time.sleep(1)
        t = threading.Thread(target=_spin, daemon=True)
        t.start()
        time.sleep(0.2)  # let node initialise


def _publish_marker(m):
    """Send a single Marker.

    /marker is a SERVICE offered by the GUI's MarkerManager plugin
    (gz.msgs.Marker -> gz.msgs.Empty), not a topic — publishing to a
    topic of that name renders nothing. The request fails fast and is
    ignored while no GUI is attached; since we re-send every cycle, a
    GUI picks the markers up within a second of connecting.
    """
    _ensure_node()
    with _lock:
        try:
            _node.request("/marker", m, marker_pb2.Marker, Empty, 100)
        except Exception:
            pass


def _publish_string(which, text):
    # `which` is "json" or "text" — resolved to the global publisher AFTER
    # _ensure_node(), since the globals are None until the node is built.
    _ensure_node()
    msg = stringmsg_pb2.StringMsg()
    msg.data = text
    pub = _json_pub if which == "json" else _text_pub
    with _lock:
        try:
            pub.publish(msg)
        except Exception:
            pass


# ── Marker builders ───────────────────────────────────────────────────────────

def _beam(x1, y1, z1, x2, y2, z2, color):
    m = marker_pb2.Marker()
    m.action = marker_pb2.Marker.ADD_MODIFY
    m.ns = NS
    m.type = marker_pb2.Marker.LINE_STRIP
    m.scale.x = 0.5
    m.scale.y = 0.5
    m.scale.z = 0.5
    m.material.ambient.r = color[0]
    m.material.ambient.g = color[1]
    m.material.ambient.b = color[2]
    m.material.ambient.a = 0.6
    p1 = m.point.add()
    p1.x, p1.y, p1.z = x1, y1, z1
    p2 = m.point.add()
    p2.x, p2.y, p2.z = x2, y2, z2
    return m


def _orb(x, y, z, color, radius=0.6):
    """Health-status sphere (TEXT markers are rejected by the render
    engine, so status is conveyed by color instead)."""
    m = marker_pb2.Marker()
    m.action = marker_pb2.Marker.ADD_MODIFY
    m.ns = NS
    m.type = marker_pb2.Marker.SPHERE
    m.pose.position.x = x
    m.pose.position.y = y
    m.pose.position.z = z
    m.scale.x = radius
    m.scale.y = radius
    m.scale.z = radius
    m.material.ambient.r = color[0]
    m.material.ambient.g = color[1]
    m.material.ambient.b = color[2]
    m.material.ambient.a = 0.9
    m.material.diffuse.r = color[0]
    m.material.diffuse.g = color[1]
    m.material.diffuse.b = color[2]
    m.material.diffuse.a = 0.9
    return m


def _delete_all():
    m = marker_pb2.Marker()
    m.action = marker_pb2.Marker.DELETE_ALL
    m.ns = NS
    return m


# ── Public API ────────────────────────────────────────────────────────────────

def clear_all():
    _ensure_node()
    m = _delete_all()
    _publish_marker(m)
    time.sleep(0.15)


def update_beam(id_, x1, y1, z1, x2, y2, z2, color):
    m = _beam(x1, y1, z1, x2, y2, z2, color)
    m.id = id_
    _publish_marker(m)


def publish_metrics(text):
    """Plain-text metrics table on /sar/metrics_text (fallback panel)."""
    _publish_string("text", text)


def publish_metrics_json(payload):
    """JSON metrics on /sar/metrics, consumed by the SarMetricsTable plugin."""
    _publish_string("json", json.dumps(payload, separators=(",", ":")))


def update_viz(ux, uy, uz, robots, t_elapsed):
    """`robots` maps robot name -> (x, y, z, ploss, d). Driven by ROBOTS so
    the scene shows every rostered robot rather than a hardcoded pair."""
    """Update all visualisation elements in one call.

    Beam color encodes link health:
      green  = C2 reachable, channel healthy
      amber  = degraded (ploss >= 33dB or C2 RTT > 150ms)
      red    = C2 (command/e-stop) link LOST
      neutral cyan/orange = netns inactive, C2 not measurable
    """
    c2 = {r: _c2_state.get(r, (0.0, "na")) for r in robots}
    colors = {}
    for i, (r, (_x, _y, _z, pl, _d)) in enumerate(robots.items()):
        neutral = COLOR_NEUTRAL_A if i % 2 == 0 else COLOR_NEUTRAL_B
        colors[r] = _health_color(pl, c2[r][0], c2[r][1], neutral)

    # Beams (color = link health). NOTE: no in-scene text labels — the
    # render engines behind the GUI reject Marker type TEXT ("Invalid
    # Marker type 7" spam in OgreMarker.cc), so numeric readouts live in
    # the TopicEcho "Metrics" panel (/sar/metrics) instead.
    # One beam and one health orb per rostered robot. The orb floats above
    # the robot because a TurtleBot3 Burger is nearly invisible at the zoom
    # levels used to watch a whole patrol.
    for i, (r, (x, y, z, _pl, _d)) in enumerate(robots.items()):
        mb = _beam(ux, uy, uz, x, y, z, colors[r])
        mb.id = ID_BEAM_BASE + i
        _publish_marker(mb)
        mo = _orb(x, y, z + 1.2, colors[r])
        mo.id = ID_ORB_BASE + i
        _publish_marker(mo)

    def _c2_str(rtt, st):
        return {"ok": f"{rtt:.0f}ms", "lost": "LOST",
                "stale": "stale?", "na": "n/a"}[st]

    # LoS/NLoS per robot from the same building geometry the channel uses.
    los = {r: _los_state((ux, uy, uz), (x, y, z))
           for r, (x, y, z, _pl, _d) in robots.items()}

    # One metrics dict per node — feeds both the JSON payload (the
    # SarMetricsTable GUI plugin) and the plain-text fallback below.
    # Missing readings use the sentinels the plugin's QML checks against:
    # -1 for never-negative fields (dist/tput/ploss/RTT), -9999 for RSRP/
    # SINR which are legitimately negative dBm/dB.
    def _node(label, key, d, ploss, c2_rtt, c2_st, los):
        rsrp, sinr = _l1_state[key]
        tp = _tp_state[key]
        # Cross-validate C2 against the geometry: a "lost" reading while the
        # robot has clear line of sight is almost certainly a stale/contended
        # probe, not a real outage. Only trust "lost" when the geometry
        # agrees the robot is shadowed (NLoS). los True=LoS, False=NLoS,
        # None=unknown (non-urban -> trust the probe as-is).
        if c2_st == "lost" and los is True:
            c2_st = "stale"
        los_s = "LOS" if los is True else "NLOS" if los is False else "na"
        return {
            "label": label,
            "d": d if d > 0 else -1.0,
            "tp_mbps": tp if tp is not None else -1.0,
            "ploss_db": ploss if ploss > 0 else -1.0,
            "rsrp_dbm": rsrp if rsrp is not None else -9999.0,
            "sinr_db": sinr if sinr is not None else -9999.0,
            "c2_rtt_ms": c2_rtt if c2_st == "ok" else -1.0,
            "c2_status": c2_st,
            "los": los_s,
        }

    nodes = [
        _node(r.upper(), r, robots[r][4], robots[r][3],
              c2[r][0], c2[r][1], los.get(r, ""))
        for r in robots
    ]
    publish_metrics_json({
        "t": t_elapsed,
        "uav": {"x": ux, "y": uy, "z": uz},
        "nodes": nodes,
    })

    # Plain-text fallback on /sar/metrics_text. ASCII only: the gz StringMsg
    # viewer escapes non-ASCII bytes, so a Unicode box rule ("─") becomes a
    # wall of \342\224\200 escapes — plain dashes stay readable.
    def _row(nd):
        d_s = ("%.1f" % nd["d"] + "m") if nd["d"] > 0 else "--m"
        pl_s = ("%.1f" % nd["ploss_db"] + "dB") if nd["ploss_db"] > 0 else "--dB"
        tp_s = ("%.1f" % nd["tp_mbps"] + "Mbps") if nd["tp_mbps"] >= 0 else "--Mbps"
        rsrp_s = ("%.0f" % nd["rsrp_dbm"] + "dBm") if nd["rsrp_dbm"] > -9000 else "--dBm"
        sinr_s = ("%.0f" % nd["sinr_db"] + "dB") if nd["sinr_db"] > -9000 else "--dB"
        return (f"{nd['label']:<6} {nd['los']:<4} d:{d_s:<7} tp:{tp_s:<8} "
                f"ploss:{pl_s:<7} rsrp:{rsrp_s:<7} sinr:{sinr_s:<6} "
                f"c2:{_c2_str(nd['c2_rtt_ms'], nd['c2_status'])}")

    rule = "-" * 60
    text = (
        f"UAV      x:{ux:.1f}  y:{uy:.1f}  z:{uz:.1f}\n"
        f"{rule}\n"
        f"{_row(nodes[0])}\n"
        f"{_row(nodes[1])}\n"
        f"{rule}\n"
        f"t: {t_elapsed:.1f}s\n"
    )
    publish_metrics(text)


# ── Runtime state ──────────────────────────────────────────────────────────────
def _detect_world():
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


_WORLD = _detect_world()
_POSE_TOPIC = f"/world/{_WORLD}/pose/info"
# Roster, matching ROBOT_SPEC in auto_patrol.py and scripts/robots.env.
# This is the VISUALISATION layer only — beams, labels and the /sar/metrics
# panel. It was left at two robots when the testbed went to four on
# 2026-10-19, so the GUI drew alpha and bravo and silently omitted charlie
# and delta even though all four were in the world and logging normally.
ROBOTS = [r.strip() for r in
          os.environ.get("SAR_ROBOTS", "alpha,bravo,charlie,delta").split(",")
          if r.strip()]
_TARGET_MODELS = {"sar_uav"} | {f"sar_ground_{r}" for r in ROBOTS}
_positions = {}
_bridge_log = Path("/tmp/ch_bridge.log")

# Same building geometry the channel model uses, so the panel's LoS/NLoS
# label and the injected path loss agree on where the walls are.
_URBAN = _WORLD == "sar_urban"
if _URBAN:
    sys.path.insert(0, os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "..", "worlds"))
    from urban_buildings import any_blocked


def _los_state(uav, ue_pos):
    """True = LoS, False = NLoS (a building blocks the UAV->robot ray),
    None = unknown (non-urban world, no geometry)."""
    if not _URBAN:
        return None
    try:
        return not any_blocked(uav, ue_pos)
    except Exception:
        return None

# ── C2 (command/downlink) live probe ─────────────────────────────────────────
# One small ping base->robot every ~2s per UE. Only meaningful when the
# robot netns is active (otherwise the reply path is loopback, not air).
_ALL_UE_IPS = {"alpha":"10.45.0.2","bravo":"10.45.0.3","charlie":"10.45.0.4","delta":"10.45.0.5"}
UE_IPS = {r: _ALL_UE_IPS[r] for r in ROBOTS if r in _ALL_UE_IPS}
# side -> (rtt_ms, status) with status in {"ok", "lost", "na"}
_c2_state = {r: (0.0, "na") for r in ROBOTS}

# Beam colors by link health
COLOR_HEALTHY = (0.15, 0.9, 0.25)   # green
COLOR_DEGRADED = (1.0, 0.65, 0.0)   # amber
COLOR_LOST = (1.0, 0.15, 0.15)      # red
COLOR_NEUTRAL_A = (0.0, 0.8, 1.0)   # fallback when C2 not measurable
COLOR_NEUTRAL_B = (1.0, 0.6, 0.0)

PLOSS_DEGRADED_DB = 25.0   # recalibrated 2026-07-11: TCP halves ~20dB, link dies ~35dB
C2_RTT_DEGRADED_MS = 150.0


def _netns_active():
    return Path("/var/run/netns/robot_alpha").exists()


C2_MISS_LIMIT = 3   # consecutive misses before declaring LOST


def _patrol_running():
    return subprocess.run(["pgrep", "-f", "auto_patrol.py"],
                          capture_output=True).returncode == 0


def _c2_probe_loop():
    # Require several consecutive misses before declaring LOST: under iperf
    # load / NLoS a single probe can exceed the wait without the link being
    # down (a manual ping in the same conditions returned 0% loss). A 2 s
    # wait tolerates the tens-of-ms RTTs seen behind buildings.
    misses = {side: 0 for side in UE_IPS}
    while True:
        active = _netns_active()
        # During a patrol, defer to its CSV C2 rather than running a SECOND
        # ping: two independent pingers + iperf load collide and drop ICMP,
        # which read as "lost" even while the data plane pushes ~10 Mbps.
        if active and _patrol_running():
            c2 = _c2_from_latest_csv()
            for side in UE_IPS:
                r = c2.get(side)
                if r is None:
                    continue                      # keep last-good on blank row
                rtt, loss = r
                _c2_state[side] = (0.0, "lost") if loss >= 100 else (rtt, "ok")
                misses[side] = 0
            time.sleep(2)
            continue
        for side, ip in UE_IPS.items():
            if not active:
                _c2_state[side] = (0.0, "na")
                misses[side] = 0
                continue
            rtt = None
            try:
                out = subprocess.run(
                    ["ping", "-c", "1", "-W", "2", "-s", "64", ip],
                    capture_output=True, text=True, timeout=4,
                ).stdout
                m = re.search(r'time=([\d.]+) ms', out)
                rtt = float(m.group(1)) if m else None
            except Exception:
                pass
            if rtt is not None:
                misses[side] = 0
                _c2_state[side] = (rtt, "ok")
            else:
                misses[side] += 1
                if misses[side] >= C2_MISS_LIMIT:
                    _c2_state[side] = (0.0, "lost")
        time.sleep(2)


def _health_color(ploss, c2_rtt, c2_status, neutral):
    if c2_status == "na":
        return neutral
    if c2_status == "lost":
        return COLOR_LOST
    if ploss >= PLOSS_DEGRADED_DB or c2_rtt > C2_RTT_DEGRADED_MS:
        return COLOR_DEGRADED
    return COLOR_HEALTHY

REFERENCE_HORIZ_DISTANCE_M = 50.0
TX_REF = -42.0
DL_UL_OFFSET = 5.0
UAV_DEFAULT_H = 40.0
UE_HEIGHT = 0.1

# ── L1 (measured RSRP/SINR) + live throughput ────────────────────────────────
_ALL_UE_LOGS = {"alpha":"/tmp/ue_alpha.log","bravo":"/tmp/ue_bravo.log","charlie":"/tmp/ue_charlie.log","delta":"/tmp/ue_delta.log"}
UE_LOGS = {r: _ALL_UE_LOGS[r] for r in ROBOTS if r in _ALL_UE_LOGS}
_ALL_UE_TUN = {"alpha":"oaitun_ue1","bravo":"oaitun_ue1p2","charlie":"oaitun_ue1p3","delta":"oaitun_ue1p4"}
UE_TUN = {r: _ALL_UE_TUN[r] for r in ROBOTS if r in _ALL_UE_TUN}
_ALL_UE_TESTIP = {"alpha":"10.0.0.200","bravo":"10.0.0.201","charlie":"10.0.0.202","delta":"10.0.0.203"}
UE_TESTIP = {r: _ALL_UE_TESTIP[r] for r in ROBOTS if r in _ALL_UE_TESTIP}
_ALL_UE_NS = {r: f"robot_{r}" for r in ("alpha", "bravo", "charlie", "delta")}
# Roster-driven like every other map above. This was alpha/bravo only, so
# _measure_tp("charlie") raised KeyError and killed the monitor thread.
UE_NS     = {r: _ALL_UE_NS[r] for r in ROBOTS if r in _ALL_UE_NS}
SUDO_PASS = os.environ.get("SAR_SUDO_PASS", "")

_l1_state = {"alpha": (None, None), "bravo": (None, None)}  # (rsrp_dBm, sinr_dB)
_tp_state = {r: None for r in ROBOTS}                       # TCP Mbps


def _read_l1(ue_log):
    """Freshest SS-RSRP / SS-SINR straight from the UE log tail.

    Wider tail window (6 MB) so the per-slot PHY debug spam can't push the
    last SS-RSRP line out of view, and a longer timeout so a loaded system
    doesn't abort the read mid-patrol. Returns (None, None) only on genuine
    absence/failure — the caller keeps the last good value on a miss."""
    try:
        out = subprocess.run(
            ["bash", "-c", f"tail -c 6000000 {ue_log} | grep -a 'SS-RSRP' | tail -1"],
            capture_output=True, text=True, timeout=10).stdout
        m = re.search(r"SS-RSRP:\s*(-?\d+)\s*dBm.*?SS-SINR:\s*(-?[\d.]+)", out)
        if m:
            return float(m.group(1)), float(m.group(2))
    except Exception:
        pass
    return None, None


_COL = {"alpha": "a", "bravo": "b", "charlie": "c", "delta": "d"}


def _latest_run_csv():
    """Newest CSV written by a running driver, patrol OR research campaign.

    This used to glob patrol_*.csv only. A research campaign writes to
    logs/campaign_*/..._research.csv, so the guard found nothing, returned
    None, and the panel silently showed stale throughput for the whole run.
    """
    import glob
    base = Path.home() / "sar_sim" / "logs"
    cands = glob.glob(str(base / "patrol_*.csv")) + glob.glob(str(base / "campaign_*" / "*.csv"))
    cands = [c for c in cands if not c.endswith(("_actions.csv", "_fastradio.csv", "manifest.csv"))]
    if not cands:
        return None
    return max(cands, key=lambda f: Path(f).stat().st_mtime)


def _tp_from_latest_csv():
    """When a patrol owns the iperf servers, read the last CSV row's TCP
    goodput rather than measuring (avoids a server-busy collision)."""
    try:
        latest = _latest_run_csv()
        if not latest:
            return None, None
        csvs = [latest]
        hdr = subprocess.run(["head", "-1", csvs[-1]], capture_output=True, text=True, timeout=3).stdout.strip().split(",")
        vals = subprocess.run(["tail", "-1", csvs[-1]], capture_output=True, text=True, timeout=3).stdout.strip().split(",")
        def col(name):
            try:
                v = vals[hdr.index(name)]
                return float(v) if v not in ("", None) else None
            except (ValueError, IndexError):
                return None
        # Returned per-robot for the whole roster; the two-value form is kept
        # for the caller below, which still tracks alpha/bravo positionally.
        for r in ROBOTS:
            v = col(f"{_COL[r]}_tcp_tp_mbps")
            if v is not None:
                _tp_state[r] = v
        return col("a_tcp_tp_mbps"), col("b_tcp_tp_mbps")
    except Exception:
        return None, None


def _c2_from_latest_csv():
    """Per-robot (rtt_ms, loss_pct) from the running patrol's last CSV row,
    so the panel's C2 matches the patrol console instead of a second,
    contending ping. Returns {} / partial dict on a blank/absent row."""
    import glob
    out = {}
    try:
        latest = _latest_run_csv()
        if not latest:
            return out
        csvs = [latest]
        hdr = subprocess.run(["head", "-1", csvs[-1]], capture_output=True, text=True, timeout=3).stdout.strip().split(",")
        vals = subprocess.run(["tail", "-1", csvs[-1]], capture_output=True, text=True, timeout=3).stdout.strip().split(",")
        def col(name):
            try:
                v = vals[hdr.index(name)]
                return float(v) if v not in ("", None) else None
            except (ValueError, IndexError):
                return None
        for side, rk, lk in [(r, f"{_COL[r]}_c2_rtt_ms", f"{_COL[r]}_c2_loss_pct")
                             for r in ROBOTS if r in _COL]:
            rtt, loss = col(rk), col(lk)
            if rtt is not None and loss is not None:
                out[side] = (rtt, loss)
    except Exception:
        pass
    return out


def _sweep_iperf(side):
    """Reap any orphaned iperf3 client for this test IP. subprocess.run's
    timeout kills the sudo parent but the root-owned iperf3 child in the
    netns can survive and wedge the next probe (this is what left bravo's
    throughput reading stuck at 0). Empty SUDO_PASS just fails auth fast via
    -S rather than blocking on a prompt."""
    try:
        # Match THIS probe's distinctive flag, not every iperf3 aimed at the
        # test IP. The broad pattern also matched traffic_streams.py's
        # persistent campaign flows (-t 86400) and any shell whose command
        # line merely mentioned the probe, killing both.
        subprocess.run(
            ["sudo", "-S", "-p", "", "pkill", "-f",
             f"iperf3 -c {UE_TESTIP[side]} .*--connect-timeout"],
            capture_output=True, text=True, timeout=5, input=SUDO_PASS + "\n")
    except Exception:
        pass


def _measure_tp(side):
    """Quick 1 s TCP goodput inside the robot's namespace. Hardened:
    --connect-timeout makes a bad/NLoS link fail fast instead of hanging,
    and orphaned iperf3 clients are swept before and after so a wedged
    probe can't pin the reading at 0 indefinitely."""
    import json
    _sweep_iperf(side)   # clear any leftover from a previous timed-out probe
    try:
        r = subprocess.run(
            ["sudo", "-S", "-p", "", "ip", "netns", "exec", UE_NS[side],
             # no --bind-dev: iperf 3.9 lacks it; the netns pins the tun
             "iperf3", "-c", UE_TESTIP[side],
             "--connect-timeout", "2000", "-t", "1", "-J"],
            capture_output=True, text=True, timeout=8, input=SUDO_PASS + "\n")
        return round(json.loads(r.stdout)["end"]["sum_received"]["bits_per_second"] / 1e6, 1)
    except Exception:
        _sweep_iperf(side)   # a timed-out probe orphans the root iperf3 child
        return None


def _monitor_loop():
    """Refresh RSRP/SINR (cheap, every cycle) and TCP throughput (measured
    when idle, read from the patrol CSV when a patrol is running)."""
    while True:
        # RSRP/SINR: keep the last good reading on a transient miss so a
        # single loaded-system timeout doesn't blank the panel to "--" when
        # the link is actually up (the -9999 flicker).
        for side in UE_IPS:
            rsrp, sinr = _read_l1(UE_LOGS[side])
            if rsrp is not None:
                _l1_state[side] = (rsrp, sinr)
        if not _netns_active():
            for side in UE_IPS:
                _tp_state[side] = None            # netns down -> genuinely no data
        elif _patrol_running():
            # Patrol owns the iperf servers: read its CSV, but keep last-good
            # when a column is blank (e.g. a just-created CSV with no rows).
            a, b = _tp_from_latest_csv()
            if a is not None:
                _tp_state["alpha"] = a
            if b is not None:
                _tp_state["bravo"] = b
        else:
            for side in UE_IPS:
                tp = _measure_tp(side)
                if tp is not None:
                    _tp_state[side] = tp
        time.sleep(6)


def _find_latest_ploss(side):
    if not _bridge_log.exists():
        return 0.0, 0.0
    lines = _bridge_log.read_text().strip().splitlines()
    tag = "gnb_ue0_alpha_ul" if side == "alpha" else "gnb_ue1_bravo_ul"
    for line in reversed(lines):
        m = re.search(rf'{tag}.*?ploss ([\d.]+)dB.*?d=([\d.]+)m', line)
        if m:
            return float(m.group(1)), float(m.group(2))
    return 0.0, 0.0


def _pose_cb(msg):
    for p in msg.pose:
        name = p.name
        if name not in _TARGET_MODELS:
            continue
        _positions[name] = (p.position.x, p.position.y, p.position.z)


def main():
    gz = Node()
    ok = gz.subscribe(Pose_V, _POSE_TOPIC, _pose_cb)
    if not ok:
        print(f"ERROR: failed to subscribe to {_POSE_TOPIC}")
        return
    print(f"[gazebo_viz] subscribed to {_POSE_TOPIC}")

    threading.Thread(target=_c2_probe_loop, daemon=True).start()
    threading.Thread(target=_monitor_loop, daemon=True).start()
    print(f"[gazebo_viz] C2 probing {'ACTIVE' if _netns_active() else 'n/a (netns not set up)'}; "
          f"RSRP/SINR+throughput monitor started")

    clear_all()
    t0 = time.time()

    U_MISSING = (0.0, 0.0, 0.0)

    while True:
        time.sleep(0.5)
        now = time.time() - t0

        uav = _positions.get("sar_uav", U_MISSING)
        ux, uy, uz = uav
        uz = uz if uz > 0 else UAV_DEFAULT_H

        robots = {}
        for r in ROBOTS:
            x, y, z = _positions.get(f"sar_ground_{r}", U_MISSING)
            z = z if z > 0 else UE_HEIGHT
            pl, d = _find_latest_ploss(r)
            robots[r] = (x, y, z, pl, d)

        update_viz(ux, uy, uz, robots, now)

        glyph = {"alpha": "\u03b1", "bravo": "\u03b2",
                 "charlie": "\u03b3", "delta": "\u03b4"}
        parts = []
        for r, (x, y, _z, pl, _d) in robots.items():
            st, rtt = _c2_state.get(r, (0.0, "na"))[1], _c2_state.get(r, (0.0, "na"))[0]
            parts.append(f"{glyph.get(r, r[0])}({x:.0f},{y:.0f}) "
                         f"pl={pl:.0f}dB C2={st}:{rtt:.0f}ms")
        print(f"[gazebo_viz] {'  '.join(parts)}  t={now:.0f}s", flush=True)


if __name__ == "__main__":
    main()