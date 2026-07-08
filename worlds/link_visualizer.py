#!/usr/bin/env python3
"""
link_visualizer.py — Nhlanhla Makhubela, UCT MSc 2026

Draws live, color-coded lines in the Gazebo GUI from the UAV to each
ground robot, colored by the SAME a2g_path_loss() model channel_bridge.py
uses — so you can literally watch link quality change in 3D as the
bodies move, including watching it slide toward red as something drifts
toward the RLF cliff (~40dB on this baseline).

Uses Gazebo's Marker API (gz.msgs.Marker on the /marker topic), confirmed
against the real proto at
/usr/share/gz/gz-msgs10/protos/gz/msgs/marker.proto on this system.

Does NOT touch OAI/telnet at all — this is purely visual, safe to run
alongside or instead of the real channel_bridge.py.

Usage:
    python3 link_visualizer.py
"""

import math
import os
import re
import time

from gz.transport13 import Node
from gz.msgs10.pose_pb2 import Pose
from gz.msgs10.marker_pb2 import Marker
from gz.msgs10.vector3d_pb2 import Vector3d

# ── Same path loss model as channel_bridge.py ───────────────────────────
FREQ_GHZ = 3.31968
UE_HEIGHT = 0.5
A_COEFF = 9.61
B_COEFF = 0.16
ETA_LOS = 1.0
ETA_NLOS = 20.0
GOOD_DB = 33.0
WARN_DB = 38.0

# ── Live OAI metric sources ──────────────────────────────────────────────
# gNB stats log is appended continuously by restart_ran.sh's collectors.
# We tail the last N lines each cycle rather than re-reading the whole
# (potentially huge) file — same lesson as tonight's /tmp log-growth issue.
GNB_STATS_LOG = os.path.expanduser("~/sar_sim/oai_configs/nrRRC_stats.log")
GNB_STATS_LOG_ALT = "/tmp/gnb_stats.log"  # fallback if the above doesn't exist


def tail_lines(path, n=40):
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            block = min(size, 8192 * 4)
            f.seek(-block, os.SEEK_END)
            data = f.read().decode(errors="ignore")
        return data.splitlines()[-n:]
    except (FileNotFoundError, OSError):
        return []


def parse_latest_oai_metrics():
    """Best-effort parse of the most recent SNR/BLER/goodput lines per UE
    from the gNB stats log format we've seen all session, e.g.:
      UE db42: dlsch_rounds ... BLER 0.00288 MCS (0) 0 ... goodput 0.00 Mbps
      UE db42: ulsch_rounds ... SNR 21.7 (+1.7) dB ... goodput 0.00 Mbps
    Returns {rnti: {"dl_goodput":..., "ul_goodput":..., "snr":..., "bler":...}}
    """
    metrics = {}
    lines = tail_lines(GNB_STATS_LOG_ALT, n=60)
    for line in lines:
        m = re.search(r"UE (\w+):.*?(dlsch|ulsch)_rounds.*?"
                       r"BLER ([\d.]+).*?SNR ([\-\d.]+)"
                       r".*?goodput ([\d.]+) Mbps", line)
        if not m:
            continue
        rnti, direction, bler, snr, goodput = m.groups()
        entry = metrics.setdefault(rnti, {})
        prefix = "dl" if direction == "dlsch" else "ul"
        entry[f"{prefix}_bler"] = float(bler)
        entry[f"{prefix}_snr"] = float(snr)
        entry[f"{prefix}_goodput"] = float(goodput)
    return metrics


def a2g_path_loss(d_horiz, h_uav, h_ue=UE_HEIGHT, freq_ghz=FREQ_GHZ):
    d_3d = max(math.sqrt(d_horiz ** 2 + (h_uav - h_ue) ** 2), 1.0)
    fspl = 20 * math.log10(d_3d) + 20 * math.log10(freq_ghz * 1e9) - 147.55
    theta = math.degrees(math.atan2(h_uav - h_ue, max(d_horiz, 0.1)))
    los_p = 1.0 / (1.0 + A_COEFF * math.exp(-B_COEFF * (theta - A_COEFF)))
    return fspl + los_p * ETA_LOS + (1.0 - los_p) * ETA_NLOS


def horiz_distance(a, b):
    return math.hypot(a[0] - b[0], a[1] - b[1])


positions = {}


def make_cb(name):
    def cb(msg):
        positions[name] = (msg.position.x, msg.position.y, msg.position.z)
    return cb


def set_color(marker, r, g, b):
    marker.material.ambient.r = r
    marker.material.ambient.g = g
    marker.material.ambient.b = b
    marker.material.ambient.a = 1.0
    marker.material.diffuse.r = r
    marker.material.diffuse.g = g
    marker.material.diffuse.b = b
    marker.material.diffuse.a = 1.0


def color_for_ploss(ploss):
    if ploss < GOOD_DB:
        return (0.15, 0.8, 0.15)   # green
    elif ploss < WARN_DB:
        return (0.95, 0.85, 0.1)   # yellow
    else:
        return (0.9, 0.15, 0.15)   # red — approaching RLF cliff


def main():
    node = Node()
    pub = node.advertise("/marker", Marker)

    topics = {
        "sar_uav": "/model/sar_uav/pose",
        "sar_ground_alpha": "/model/sar_ground_alpha/pose",
        "sar_ground_bravo": "/model/sar_ground_bravo/pose",
    }
    for name, topic in topics.items():
        ok = node.subscribe(Pose, topic, make_cb(name))
        print(f"Subscribed to {topic}: {ok}")

    print("Waiting for pose data...")
    print("NOTE: OAI metrics (SNR/goodput) are shown per-RNTI, not mapped to")
    print("alpha/bravo by name — RNTIs are dynamic and we have no reliable")
    print("live name<->RNTI mapping from outside OAI itself. Distance and")
    print("path loss below ARE correctly mapped per-robot (Gazebo pose data).")
    marker_id = 1000

    try:
        while True:
            time.sleep(0.5)

            if "sar_uav" not in positions:
                continue

            uav = positions["sar_uav"]
            uav_h = uav[2] if uav[2] > 1.0 else 30.0
            oai_metrics = parse_latest_oai_metrics()

            for ue_name in ("sar_ground_alpha", "sar_ground_bravo"):
                if ue_name not in positions:
                    continue
                ue = positions[ue_name]
                d = horiz_distance(uav, ue)
                ploss = a2g_path_loss(d, uav_h)
                r, g, b = color_for_ploss(ploss)

                line = Marker()
                line.action = Marker.ADD_MODIFY
                line.type = Marker.LINE_LIST
                line.ns = "link_quality"
                line.id = marker_id if ue_name == "sar_ground_alpha" else marker_id + 1
                line.visibility = Marker.ALL
                set_color(line, r, g, b)
                p1 = line.point.add()
                p1.x, p1.y, p1.z = uav[0], uav[1], uav[2]
                p2 = line.point.add()
                p2.x, p2.y, p2.z = ue[0], ue[1], ue[2]
                pub.publish(line)

                label = Marker()
                label.action = Marker.ADD_MODIFY
                label.type = Marker.TEXT
                label.ns = "link_quality_labels"
                label.id = marker_id + 10 if ue_name == "sar_ground_alpha" else marker_id + 11
                label.visibility = Marker.ALL
                label.text = f"{ue_name}\nd={d:.1f}m  ploss={ploss:.1f}dB"
                set_color(label, r, g, b)
                label.scale.x = 3.0
                label.scale.y = 3.0
                label.scale.z = 3.0
                mid_x = (uav[0] + ue[0]) / 2.0
                mid_y = (uav[1] + ue[1]) / 2.0
                mid_z = (uav[2] + ue[2]) / 2.0 + 2.0
                label.pose.position.x = mid_x
                label.pose.position.y = mid_y
                label.pose.position.z = mid_z
                pub.publish(label)

                print(f"{ue_name}: d={d:.1f}m  ploss={ploss:.1f}dB  "
                      f"color={'green' if ploss < GOOD_DB else 'yellow' if ploss < WARN_DB else 'RED'}")

            # Real OAI metrics — floating summary above the UAV, one entry
            # per currently-visible RNTI (not name-mapped, see note above)
            if oai_metrics:
                lines_txt = []
                for rnti, m in oai_metrics.items():
                    dl_g = m.get("dl_goodput", 0.0)
                    ul_g = m.get("ul_goodput", 0.0)
                    dl_snr = m.get("dl_snr", 0.0)
                    lines_txt.append(f"RNTI {rnti}: SNR {dl_snr:.1f}dB  "
                                      f"DL {dl_g:.2f}Mbps UL {ul_g:.2f}Mbps")
                summary = Marker()
                summary.action = Marker.ADD_MODIFY
                summary.type = Marker.TEXT
                summary.ns = "oai_summary"
                summary.id = 2000
                summary.visibility = Marker.ALL
                summary.text = "\n".join(lines_txt)
                set_color(summary, 1.0, 1.0, 1.0)
                summary.scale.x = 2.5
                summary.scale.y = 2.5
                summary.scale.z = 2.5
                summary.pose.position.x = uav[0]
                summary.pose.position.y = uav[1]
                summary.pose.position.z = uav[2] + 8.0
                pub.publish(summary)
                print("OAI:", " | ".join(lines_txt))

    except KeyboardInterrupt:
        print("\nStopping link visualizer, clearing markers...")
        clear = Marker()
        clear.action = Marker.DELETE_ALL
        clear.ns = "link_quality"
        pub.publish(clear)
        clear2 = Marker()
        clear2.action = Marker.DELETE_ALL
        clear2.ns = "link_quality_labels"
        pub.publish(clear2)


if __name__ == "__main__":
    main()
