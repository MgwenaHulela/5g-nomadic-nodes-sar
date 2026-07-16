#!/usr/bin/env python3
"""sar_map.py — live top-down situational map of the SAR fleet.

Shows where the ground robots are in relation to the UAV (the "drone"),
over the urban geometry, with:
  - buildings + trees (the same footprints the channel model uses)
  - the UAV ground position and its coverage radius
  - each robot with heading, distance-to-UAV, and LoS/NLoS status
  - LoS/NLoS beams UAV->robot (green = clear, red = shadowed)
  - the thermal "victim" location
  - each robot's live lidar scan (what it currently senses)

Reads the world pose topic and the robots' lidar scans off gz-transport.
Refreshes a PNG every interval (works headless); pass --live for an
interactive window (needs a display).

  python3 scripts/sar_map.py                 # -> logs/sar_map.png, refreshing
  python3 scripts/sar_map.py --live          # interactive window
"""
import argparse
import math
import os
import sys
import threading
import time

sys.path.insert(0, "/usr/lib/python3/dist-packages")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "worlds"))

from gz.transport13 import Node
from gz.msgs10.pose_v_pb2 import Pose_V
from gz.msgs10.laserscan_pb2 import LaserScan

from urban_buildings import BUILDINGS, TREES, VICTIM, any_blocked

C_BG = "#f4f5f6"; C_BLDG = "#c3c7cc"; C_BLDG_E = "#9aa0a6"
C_TREE = "#4a9a5a"; C_UAV = "#3b6fb0"; C_COV = "#3b6fb0"
C_ALPHA = "#1a9e8f"; C_BRAVO = "#3b6fb0"; C_VICTIM = "#e0562d"
C_LOS = "#2a9d5a"; C_NLOS = "#d94b3f"; C_LIDAR = "#8a9096"; INK = "#2b2f33"
ROBOT_C = {"sar_ground_alpha": C_ALPHA, "sar_ground_bravo": C_BRAVO}
LABEL = {"sar_ground_alpha": "ALPHA", "sar_ground_bravo": "BRAVO"}


def _yaw(q):
    return math.atan2(2.0 * (q.w * q.z + q.x * q.y),
                      1.0 - 2.0 * (q.y * q.y + q.z * q.z))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--world", default=os.environ.get("SAR_WORLD", "sar_urban"))
    ap.add_argument("--out", default="logs/sar_map.png")
    ap.add_argument("--interval", type=float, default=1.0)
    ap.add_argument("--coverage", type=float, default=130.0, help="UAV coverage radius (m)")
    ap.add_argument("--no-lidar", action="store_true")
    ap.add_argument("--live", action="store_true")
    args = ap.parse_args()

    import matplotlib
    if not args.live:
        matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.patches import Rectangle, Circle

    lock = threading.Lock()
    poses = {}                     # name -> (x, y, yaw)
    scans = {"sar_ground_alpha": None, "sar_ground_bravo": None}

    def pose_cb(msg):
        with lock:
            for p in msg.pose:
                if p.name in ("sar_uav", "sar_ground_alpha", "sar_ground_bravo"):
                    poses[p.name] = (p.position.x, p.position.y, _yaw(p.orientation))

    def make_scan_cb(name):
        def cb(msg):
            pts = []
            n = len(msg.ranges)
            for i in range(n):
                r = msg.ranges[i]
                if not (msg.range_min <= r <= msg.range_max) or math.isinf(r):
                    continue
                a = msg.angle_min + i * msg.angle_step
                pts.append((r, a))
            with lock:
                scans[name] = pts
        return cb

    node = Node()
    node.subscribe(Pose_V, f"/world/{args.world}/pose/info", pose_cb)
    if not args.no_lidar:
        for rn in ("sar_ground_alpha", "sar_ground_bravo"):
            t = f"/world/{args.world}/model/{rn}/link/sar_lidar_link/sensor/lidar/scan"
            node.subscribe(LaserScan, t, make_scan_cb(rn))

    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    fig, ax = plt.subplots(figsize=(8, 8))
    if args.live:
        plt.ion(); plt.show()

    def draw():
        with lock:
            P = dict(poses); S = {k: (list(v) if v else None) for k, v in scans.items()}
        ax.clear()
        ax.set_facecolor(C_BG)
        ax.set_aspect("equal"); ax.set_xlim(-160, 160); ax.set_ylim(-160, 160)
        ax.grid(True, color="#e2e4e7", lw=0.6); ax.set_axisbelow(True)
        for s in ("top", "right"):
            ax.spines[s].set_visible(False)

        for (cx, cy, w, d, h) in BUILDINGS:
            ax.add_patch(Rectangle((cx - w/2, cy - d/2), w, d,
                         facecolor=C_BLDG, edgecolor=C_BLDG_E, lw=1))
        for (tx, ty, *_1) in TREES:
            ax.plot(tx, ty, "o", color=C_TREE, ms=4, alpha=0.7)

        vx, vy = VICTIM
        ax.plot(vx, vy, "*", color=C_VICTIM, ms=18, zorder=6)
        ax.annotate("victim", (vx, vy), (vx + 4, vy + 4), color=C_VICTIM,
                    fontsize=9, fontweight="bold")

        uav = P.get("sar_uav")
        if uav:
            ux, uy, _ = uav
            ax.add_patch(Circle((ux, uy), args.coverage, fill=False,
                         ls="--", ec=C_COV, lw=1.2, alpha=0.6))
            ax.plot(ux, uy, "D", color=C_UAV, ms=13, zorder=7)
            ax.annotate("UAV", (ux, uy), (ux + 4, uy + 4), color=C_UAV,
                        fontsize=10, fontweight="bold")

        for rn in ("sar_ground_alpha", "sar_ground_bravo"):
            rp = P.get(rn)
            if not rp:
                continue
            rx, ry, ryaw = rp
            col = ROBOT_C[rn]
            # lidar points (world frame)
            if S.get(rn):
                xs = [rx + r * math.cos(ryaw + a) for (r, a) in S[rn]]
                ys = [ry + r * math.sin(ryaw + a) for (r, a) in S[rn]]
                ax.plot(xs, ys, ".", color=C_LIDAR, ms=1.5, alpha=0.5, zorder=3)
            # LoS/NLoS beam to UAV
            if uav:
                blocked = any_blocked((ux, uy, 40.0), (rx, ry, 0.3))
                d = math.hypot(rx - ux, ry - uy)
                ax.plot([ux, rx], [uy, ry], "-",
                        color=(C_NLOS if blocked else C_LOS), lw=1.6, alpha=0.8, zorder=4)
                tag = "NLoS" if blocked else "LoS"
                ax.annotate(f"{LABEL[rn]}  {d:.0f}m {tag}", (rx, ry), (rx + 3, ry - 6),
                            color=col, fontsize=9, fontweight="bold")
            # robot marker with heading
            ax.plot(rx, ry, "o", color=col, ms=9, zorder=6)
            ax.plot([rx, rx + 6*math.cos(ryaw)], [ry, ry + 6*math.sin(ryaw)],
                    "-", color=col, lw=2, zorder=6)

        ax.set_title("SAR fleet — ground robots relative to the UAV",
                     fontsize=12, fontweight="bold", color=INK)
        ax.set_xlabel("x (m)"); ax.set_ylabel("y (m)")

    try:
        while True:
            draw()
            fig.savefig(args.out, dpi=120)
            if args.live:
                plt.pause(0.01)
            time.sleep(args.interval)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
