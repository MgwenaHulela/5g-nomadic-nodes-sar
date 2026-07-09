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
import threading
import time
import sys
from pathlib import Path

sys.path.insert(0, "/usr/lib/python3/dist-packages")

from gz.transport13 import Node
from gz.msgs10 import marker_pb2, stringmsg_pb2
from gz.msgs10.pose_v_pb2 import Pose_V

# ── Constants ─────────────────────────────────────────────────────────────────
NS = "sar_patrol"
ID_BEAM_ALPHA = 100
ID_BEAM_BRAVO = 101
ID_LABEL_ALPHA = 200
ID_LABEL_BRAVO = 201
ID_LABEL_UAV = 202

# ── Lazy-initialised globals ──────────────────────────────────────────────────
_node = None
_marker_pub = None
_string_pub = None
_lock = threading.Lock()


def _ensure_node():
    global _node, _marker_pub, _string_pub
    with _lock:
        if _node is not None:
            return
        _node = Node()
        _marker_pub = _node.advertise("/marker", marker_pb2.Marker())
        _string_pub = _node.advertise("/sar/metrics", stringmsg_pb2.StringMsg())
        # Start a keep-alive spin loop so the node processes messages
        def _spin():
            while True:
                time.sleep(1)
        t = threading.Thread(target=_spin, daemon=True)
        t.start()
        time.sleep(0.2)  # let node initialise


def _publish_marker(m):
    """Send a single Marker."""
    _ensure_node()
    with _lock:
        try:
            _marker_pub.publish(m)
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


def _label_marker(x, y, z, text, color, scale=0.5):
    m = marker_pb2.Marker()
    m.action = marker_pb2.Marker.ADD_MODIFY
    m.ns = NS
    m.type = marker_pb2.Marker.TEXT
    m.pose.position.x = x
    m.pose.position.y = y
    m.pose.position.z = z
    m.scale.x = scale
    m.scale.y = scale
    m.scale.z = scale
    m.text = text
    m.material.ambient.r = color[0]
    m.material.ambient.g = color[1]
    m.material.ambient.b = color[2]
    m.material.ambient.a = 1.0
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


def update_label(id_, x, y, z, text, color=(1, 1, 1), scale=0.5):
    m = _label_marker(x, y, z, text, color, scale)
    m.id = id_
    _publish_marker(m)


def publish_metrics(text):
    _publish_string(text)


def update_viz(ux, uy, uz,
               ax, ay, az, a_ploss, a_tcp, a_pdr, a_rtt, a_bits, a_rsrp, a_sinr,
               bx, by, bz, b_ploss, b_tcp, b_pdr, b_rtt, b_bits, b_rsrp, b_sinr,
               t_elapsed):
    """Update all visualisation elements in one call."""
    # Beams
    m1 = _beam(ux, uy, uz, ax, ay, az, (0.0, 0.8, 1.0))
    m1.id = ID_BEAM_ALPHA
    _publish_marker(m1)

    m2 = _beam(ux, uy, uz, bx, by, bz, (1.0, 0.6, 0.0))
    m2.id = ID_BEAM_BRAVO
    _publish_marker(m2)

    # Labels
    la = f"Alpha  ploss={a_ploss}dB  TCP={a_tcp}M  PDR={a_pdr}%  RSRP={a_rsrp}dBm  SINR={a_sinr}dB"
    m3 = _label_marker(ax, ay, az + 1.5, la, (0.0, 0.8, 1.0))
    m3.id = ID_LABEL_ALPHA
    _publish_marker(m3)

    lb = f"Bravo  ploss={b_ploss}dB  TCP={b_tcp}M  PDR={b_pdr}%  RSRP={b_rsrp}dBm  SINR={b_sinr}dB"
    m4 = _label_marker(bx, by, bz + 1.5, lb, (1.0, 0.6, 0.0))
    m4.id = ID_LABEL_BRAVO
    _publish_marker(m4)

    m5 = _label_marker(ux, uy, uz + 2.0, f"UAV  t={t_elapsed:.0f}s", (1, 1, 1))
    m5.id = ID_LABEL_UAV
    _publish_marker(m5)

    # Metrics string for TopicViewer
    text = (
        f"=== SAR Patrol Metrics ===\n"
        f"Time: {t_elapsed:.1f}s\n"
        f"UAV: ({ux:.1f}, {uy:.1f}, {uz:.1f})m\n\n"
        f"Alpha ──\n"
        f"  Position: ({ax:.1f}, {ay:.1f}, {az:.1f})m\n"
        f"  Ploss: {a_ploss:.1f}dB  TCP: {a_tcp:.1f}Mbps\n"
        f"  PDR: {a_pdr:.1f}%  RTT: {a_rtt:.1f}ms\n"
        f"  RSRP: {a_rsrp:.1f}dBm  SINR: {a_sinr:.1f}dB\n"
        f"  Bits/sym: {a_bits}\n\n"
        f"── Bravo ──\n"
        f"  Position: ({bx:.1f}, {by:.1f}, {bz:.1f})m\n"
        f"  Ploss: {b_ploss:.1f}dB  TCP: {b_tcp:.1f}Mbps\n"
        f"  PDR: {b_pdr:.1f}%  RTT: {b_rtt:.1f}ms\n"
        f"  RSRP: {b_rsrp:.1f}dBm  SINR: {b_sinr:.1f}dB\n"
        f"  Bits/sym: {b_bits}\n"
    )
    publish_metrics(text)


# ── Runtime state ──────────────────────────────────────────────────────────────
_POSE_TOPIC = "/world/sar_open_field/pose/info"
_TARGET_MODELS = {"sar_uav", "sar_ground_alpha", "sar_ground_bravo"}
_positions = {}
_bridge_log = Path("/tmp/ch_bridge.log")

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

        # Build display values (floats; 0 means N/A)
        a_tcp = 0.0
        a_pdr = 0.0
        a_rtt = 0.0
        a_bits = 0.0
        a_rsrp_c = 0.0
        a_sinr = 0.0

        b_tcp = 0.0
        b_pdr = 0.0
        b_rtt = 0.0
        b_bits = 0.0
        b_rsrp_c = 0.0
        b_sinr = 0.0

        if a_ploss > 0:
            a_rsrp_c = round(TX_REF - (a_ploss - DL_UL_OFFSET), 1)
        if b_ploss > 0:
            b_rsrp_c = round(TX_REF - (b_ploss - DL_UL_OFFSET), 1)

        update_viz(
            ux, uy, uz,
            ax, ay, az,
            a_ploss, a_tcp, a_pdr, a_rtt, a_bits, a_rsrp_c, a_sinr,
            bx, by, bz,
            b_ploss, b_tcp, b_pdr, b_rtt, b_bits, b_rsrp_c, b_sinr,
            now,
        )

        print(f"[gazebo_viz] α({ax:.1f},{ay:.1f}) ploss={a_ploss:.1f}dB  "
              f"β({bx:.1f},{by:.1f}) ploss={b_ploss:.1f}dB  t={now:.0f}s", flush=True)


if __name__ == "__main__":
    main()