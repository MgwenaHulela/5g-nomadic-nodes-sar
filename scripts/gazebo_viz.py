"""Gazebo visualization for SAR patrol — markers (beams + text) + metrics topic.

Runs as a standalone process:
  1. Subscribes to /world/sar_open_field/pose/info (Pose_V) for positions
  2. Reads /tmp/ch_bridge.log for ploss/distance per UE
  3. Publishes beam markers + text labels + /sar/metrics string

Usage:
  python3 scripts/gazebo_viz.py
  # or alongside start_full_stack.sh
"""

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
ID_BEAM_ALPHA = 100
ID_BEAM_BRAVO = 101
ID_ORB_ALPHA = 200
ID_ORB_BRAVO = 201

# ── Lazy-initialised globals ──────────────────────────────────────────────────
_node = None
_marker_pub = None
_string_pub = None
_lock = threading.Lock()


def _ensure_node():
    global _node, _string_pub
    with _lock:
        if _node is not None:
            return
        _node = Node()
        _string_pub = _node.advertise("/sar/metrics", stringmsg_pb2.StringMsg())
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


def _publish_string(text):
    _ensure_node()
    msg = stringmsg_pb2.StringMsg()
    msg.data = text
    with _lock:
        try:
            _string_pub.publish(msg)
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
    _publish_string(text)


def update_viz(ux, uy, uz,
               ax, ay, az, a_ploss, a_d,
               bx, by, bz, b_ploss, b_d,
               t_elapsed):
    """Update all visualisation elements in one call.

    Beam color encodes link health:
      green  = C2 reachable, channel healthy
      amber  = degraded (ploss >= 33dB or C2 RTT > 150ms)
      red    = C2 (command/e-stop) link LOST
      neutral cyan/orange = netns inactive, C2 not measurable
    """
    a_c2_rtt, a_c2_st = _c2_state["alpha"]
    b_c2_rtt, b_c2_st = _c2_state["bravo"]
    a_color = _health_color(a_ploss, a_c2_rtt, a_c2_st, COLOR_NEUTRAL_A)
    b_color = _health_color(b_ploss, b_c2_rtt, b_c2_st, COLOR_NEUTRAL_B)

    # Beams (color = link health). NOTE: no in-scene text labels — the
    # render engines behind the GUI reject Marker type TEXT ("Invalid
    # Marker type 7" spam in OgreMarker.cc), so numeric readouts live in
    # the TopicEcho "Metrics" panel (/sar/metrics) instead.
    m1 = _beam(ux, uy, uz, ax, ay, az, a_color)
    m1.id = ID_BEAM_ALPHA
    _publish_marker(m1)

    m2 = _beam(ux, uy, uz, bx, by, bz, b_color)
    m2.id = ID_BEAM_BRAVO
    _publish_marker(m2)

    # Health orb floating above each robot — visible from any zoom level
    # where the tiny burger itself isn't.
    m3 = _orb(ax, ay, az + 1.2, a_color)
    m3.id = ID_ORB_ALPHA
    _publish_marker(m3)

    m4 = _orb(bx, by, bz + 1.2, b_color)
    m4.id = ID_ORB_BRAVO
    _publish_marker(m4)

    def _c2_str(rtt, st):
        return {"ok": f"C2 {rtt:.0f}ms", "lost": "C2 LOST", "na": "C2 n/a"}[st]

    # Metrics string for the GUI TopicViewer panel
    def _panel(side, x, y, z, d, ploss, c2_rtt, c2_st):
        rsrp = round(TX_REF - (ploss - DL_UL_OFFSET), 1) if ploss > 0 else 0.0
        return (
            f"── {side} ──\n"
            f"  Position: ({x:.1f}, {y:.1f})m   d_horiz: {d:.1f}m\n"
            f"  Ploss(UL): {ploss:.1f}dB   RSRP(calc): {rsrp:.1f}dBm\n"
            f"  C2 downlink: {_c2_str(c2_rtt, c2_st)}\n"
        )

    text = (
        f"=== SAR A2G Link Monitor ===\n"
        f"Time: {t_elapsed:.1f}s   UAV: ({ux:.1f}, {uy:.1f}, {uz:.1f})m\n\n"
        + _panel("ALPHA", ax, ay, az, a_d, a_ploss, a_c2_rtt, a_c2_st) + "\n"
        + _panel("BRAVO", bx, by, bz, b_d, b_ploss, b_c2_rtt, b_c2_st)
        + "\nBeams: green=healthy amber=degraded red=C2 lost\n"
    )
    publish_metrics(text)


# ── Runtime state ──────────────────────────────────────────────────────────────
_POSE_TOPIC = "/world/sar_open_field/pose/info"
_TARGET_MODELS = {"sar_uav", "sar_ground_alpha", "sar_ground_bravo"}
_positions = {}
_bridge_log = Path("/tmp/ch_bridge.log")

# ── C2 (command/downlink) live probe ─────────────────────────────────────────
# One small ping base->robot every ~2s per UE. Only meaningful when the
# robot netns is active (otherwise the reply path is loopback, not air).
UE_IPS = {"alpha": "10.45.0.2", "bravo": "10.45.0.3"}
# side -> (rtt_ms, status) with status in {"ok", "lost", "na"}
_c2_state = {"alpha": (0.0, "na"), "bravo": (0.0, "na")}

# Beam colors by link health
COLOR_HEALTHY = (0.15, 0.9, 0.25)   # green
COLOR_DEGRADED = (1.0, 0.65, 0.0)   # amber
COLOR_LOST = (1.0, 0.15, 0.15)      # red
COLOR_NEUTRAL_A = (0.0, 0.8, 1.0)   # fallback when C2 not measurable
COLOR_NEUTRAL_B = (1.0, 0.6, 0.0)

PLOSS_DEGRADED_DB = 33.0   # ~110m: TCP falling, HARQ retransmissions rising
C2_RTT_DEGRADED_MS = 150.0


def _netns_active():
    return Path("/var/run/netns/robot_alpha").exists()


def _c2_probe_loop():
    # Two consecutive misses before declaring LOST — a single dropped probe
    # (e.g. coinciding with an iperf burst) shouldn't flash the beam red.
    misses = {side: 0 for side in UE_IPS}
    while True:
        active = _netns_active()
        for side, ip in UE_IPS.items():
            if not active:
                _c2_state[side] = (0.0, "na")
                continue
            rtt = None
            try:
                out = subprocess.run(
                    ["ping", "-c", "1", "-W", "1", "-s", "64", ip],
                    capture_output=True, text=True, timeout=3,
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
                if misses[side] >= 2:
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
TX_REF = -11.0
DL_UL_OFFSET = 5.0
UAV_DEFAULT_H = 40.0
UE_HEIGHT = 0.1


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
    print(f"[gazebo_viz] C2 probing {'ACTIVE' if _netns_active() else 'n/a (netns not set up)'}")

    clear_all()
    t0 = time.time()

    U_MISSING = (0.0, 0.0, 0.0)

    while True:
        time.sleep(0.5)
        now = time.time() - t0

        uav = _positions.get("sar_uav", U_MISSING)
        ux, uy, uz = uav
        uz = uz if uz > 0 else UAV_DEFAULT_H

        alpha = _positions.get("sar_ground_alpha", U_MISSING)
        ax, ay, az = alpha
        az = az if az > 0 else UE_HEIGHT

        bravo = _positions.get("sar_ground_bravo", U_MISSING)
        bx, by, bz = bravo
        bz = bz if bz > 0 else UE_HEIGHT

        a_ploss, a_d = _find_latest_ploss("alpha")
        b_ploss, b_d = _find_latest_ploss("bravo")

        update_viz(
            ux, uy, uz,
            ax, ay, az, a_ploss, a_d,
            bx, by, bz, b_ploss, b_d,
            now,
        )

        a_c2 = _c2_state["alpha"]
        b_c2 = _c2_state["bravo"]
        print(f"[gazebo_viz] α({ax:.1f},{ay:.1f}) ploss={a_ploss:.1f}dB C2={a_c2[1]}:{a_c2[0]:.0f}ms  "
              f"β({bx:.1f},{by:.1f}) ploss={b_ploss:.1f}dB C2={b_c2[1]}:{b_c2[0]:.0f}ms  t={now:.0f}s",
              flush=True)


if __name__ == "__main__":
    main()