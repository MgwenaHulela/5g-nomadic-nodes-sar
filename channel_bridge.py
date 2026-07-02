#!/usr/bin/env python3
"""
Channel Bridge — Nhlanhla Makhubela, UCT MSc 2026

Reads live UAV / UGV poses directly from gz-transport (same method as
sar_node.py — bypasses ros_gz_bridge, which UCT's multicast block breaks),
computes A2G path loss with the same FSPL + LoS/NLoS sigmoid model already
used in sar_node.py, and pushes the result into the running OAI gNB/UE
processes at runtime via their telnet `channelmod modify` interface.

This does NOT replace sar_node.py's CSV metrics logging — it's a separate,
standalone process. Run both side by side.

Model index mapping (confirmed from live telnet output on innocentmsc-PPB660M-C):
  gNB   (127.0.0.1:9091)  model 1 = rfsimu_channel_ue0 (alpha UL) baseline ploss 25dB
  gNB   (127.0.0.1:9091)  model 2 = rfsimu_channel_ue1 (bravo UL) baseline ploss 30dB
  UE alpha (127.0.0.1:9092) model 0 = rfsimu_channel_enB0 (DL)    baseline ploss 20dB
  UE bravo (127.0.0.1:9093) model 0 = rfsimu_channel_enB0 (DL)    baseline ploss 25dB

If you rebuild/reconfig and these indices change, update MODEL_MAP below —
run `echo "channelmod show current" | nc 127.0.0.1 <port>` to re-check.
"""

import argparse
import csv
import math
import os
import socket
import time
from datetime import datetime

# gz-transport direct imports (same as sar_node.py)
from gz.transport13 import Node as GzNode
from gz.msgs10.pose_pb2 import Pose

# ── Path loss model — copied from sar_node.py for consistency ──────────────
FREQ_GHZ = 3.31968   # actual n78 carrier used by the OAI stack (-C 3319680000)
UE_HEIGHT = 0.5
UAV_DEFAULT_H = 30.0
A_COEFF = 9.61
B_COEFF = 0.16
ETA_LOS = 1.0
ETA_NLOS = 20.0


def a2g_path_loss(d_horiz, h_uav, h_ue=UE_HEIGHT, freq_ghz=FREQ_GHZ):
    d_3d = math.sqrt(d_horiz ** 2 + (h_uav - h_ue) ** 2)
    d_3d = max(d_3d, 1.0)
    fspl = 20 * math.log10(d_3d) + 20 * math.log10(freq_ghz * 1e9) - 147.55
    theta = math.degrees(math.atan2(h_uav - h_ue, max(d_horiz, 0.1)))
    los_p = 1.0 / (1.0 + A_COEFF * math.exp(-B_COEFF * (theta - A_COEFF)))
    pl = fspl + los_p * ETA_LOS + (1.0 - los_p) * ETA_NLOS
    return pl, los_p, d_3d, theta


# ── Runtime config ───────────────────────────────────────────────────────
# Per-model pose topics: each carries exactly ONE world-frame pose for that
# model. The aggregate /world/sar_world/pose/info topic is NOT used because
# it also contains child-link / nested poses (some relative to parent, near
# origin) that collide with model names and corrupt the position table.
ROBOT_POSE_TOPICS = {
    "sar_uav":          "/model/sar_uav/pose",
    "sar_ground_alpha": "/model/sar_ground_alpha/pose",
    "sar_ground_bravo": "/model/sar_ground_bravo/pose",
}

# horizontal distance (m) at which today's static config ploss values are
# assumed correct. Set this to whatever alpha/bravo's typical distance from
# the UAV is at simulation start — adjust and re-run if link quality at
# startup looks off vs. what you're used to.
REFERENCE_HORIZ_DISTANCE_M = 50.0

# Exponential moving average factor for ploss smoothing (0 < alpha <= 1).
# 1.0 = no smoothing; 0.3 = each update moves 30% toward the new value.
# Prevents link-flapping from fast pose changes or residual jitter.
EMA_ALPHA = 0.3

# Maximum dB the bridge may push ABOVE each model's baseline. Empirically
# determined (1 Jul 2026): alpha UL at baseline 25dB has ~21.5dB SNR; pushing
# ploss to +20dB over baseline (45dB) caused radio link failure and RNTI
# churn (2463 -> 6061/6e77, BLER 0.86). +15dB keeps ~6dB SNR margin above
# the RLF cliff, leaving room for Rayleigh fading dips + HARQ recovery.
MAX_DELTA_DB = 15.0

MODEL_MAP = {
    # name: (telnet_port, model_index, baseline_ploss_dB)
    "gnb_ue0_alpha_ul": (9091, 1, 25.0),
    "gnb_ue1_bravo_ul": (9091, 2, 30.0),
    "ue_alpha_dl":      (9092, 0, 20.0),
    "ue_bravo_dl":      (9093, 0, 25.0),
}

PLOSS_MIN, PLOSS_MAX = 10.0, 70.0   # clamp so we never send a nonsense value
CHANGE_THRESHOLD_DB = 0.5           # don't spam telnet for sub-dB jitter
UPDATE_INTERVAL_S = 0.5             # 2 Hz

CSV_PATH = os.path.expanduser("~/sar_sim/logs/channel_bridge_log.csv")


# ── Persistent telnet connections ────────────────────────────────────────
class TelnetLink:
    def __init__(self, port, host="127.0.0.1"):
        self.host = host
        self.port = port
        self.sock = None

    def connect(self):
        try:
            self.sock = socket.create_connection((self.host, self.port), timeout=2)
            self.sock.settimeout(1)
            # drain the banner/prompt
            try:
                self.sock.recv(4096)
            except socket.timeout:
                pass
            return True
        except OSError as e:
            print(f"[telnet:{self.port}] connect failed: {e}")
            self.sock = None
            return False

    def send(self, cmd):
        if self.sock is None:
            if not self.connect():
                return False
        try:
            self.sock.sendall((cmd + "\n").encode())
            try:
                self.sock.recv(4096)  # drain response, ignore content
            except socket.timeout:
                pass
            return True
        except OSError as e:
            print(f"[telnet:{self.port}] send failed ({e}), will reconnect")
            self.sock = None
            return False


# ── Pose subscribers (one per model — no name collisions possible) ───────
positions = {}


def make_pose_callback(model_name):
    def cb(msg):
        positions[model_name] = (msg.position.x, msg.position.y, msg.position.z)
    return cb


def horiz_distance(a, b):
    return math.hypot(a[0] - b[0], a[1] - b[1])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true",
                     help="compute and print ploss values but never send telnet commands")
    ap.add_argument("--rate", type=float, default=UPDATE_INTERVAL_S,
                     help="update interval in seconds (default 0.5 = 2Hz)")
    ap.add_argument("--ref-distance", type=float, default=REFERENCE_HORIZ_DISTANCE_M,
                     help="horizontal distance (m) that today's static config ploss corresponds to")
    args = ap.parse_args()

    gz = GzNode()
    for model_name, topic in ROBOT_POSE_TOPICS.items():
        ok = gz.subscribe(Pose, topic, make_pose_callback(model_name))
        if not ok:
            print(f"ERROR: failed to subscribe to {topic} — is Gazebo running?")
            return
        print(f"Subscribed to {topic}")

    links = {name: TelnetLink(port) for name, (port, _idx, _base) in MODEL_MAP.items()}
    if not args.dry_run:
        for name, link in links.items():
            link.connect()

    # pre-compute the reference path loss once (theta depends only on
    # UAV height + ref distance, both fixed)
    pl_ref, _, _, _ = a2g_path_loss(args.ref_distance, UAV_DEFAULT_H)

    os.makedirs(os.path.dirname(CSV_PATH), exist_ok=True)
    csv_new = not os.path.exists(CSV_PATH)
    csv_file = open(CSV_PATH, "a", newline="")
    writer = csv.writer(csv_file)
    if csv_new:
        writer.writerow(["timestamp", "uav_x", "uav_y", "uav_z",
                          "d_alpha_horiz", "d_bravo_horiz",
                          "pl_alpha_delta", "pl_bravo_delta",
                          "ploss_gnb_ue0", "ploss_gnb_ue1",
                          "ploss_ue_alpha_dl", "ploss_ue_bravo_dl"])

    last_sent = {name: base for name, (_p, _i, base) in MODEL_MAP.items()}
    smoothed = {name: base for name, (_p, _i, base) in MODEL_MAP.items()}

    print(f"Reference: {args.ref_distance:.1f}m horiz -> pl_ref={pl_ref:.1f}dB "
          f"(baseline configs assumed correct at this distance)")
    print("Waiting for pose data..." if not positions else "Pose data flowing.")

    try:
        while True:
            time.sleep(args.rate)

            if "sar_uav" not in positions:
                print("waiting for sar_uav pose...")
                continue

            uav = positions["sar_uav"]
            uav_h = uav[2] if uav[2] > 0 else UAV_DEFAULT_H

            row = [datetime.now().isoformat(), *uav]

            deltas = {}
            for ue_name, key in [("sar_ground_alpha", "alpha"), ("sar_ground_bravo", "bravo")]:
                if ue_name not in positions:
                    deltas[key] = None
                    continue
                ue_pos = positions[ue_name]
                d_horiz = horiz_distance(uav, ue_pos)
                pl, _los_p, _d3d, _theta = a2g_path_loss(d_horiz, uav_h)
                deltas[key] = (d_horiz, pl - pl_ref)

            row.append(deltas["alpha"][0] if deltas["alpha"] else "")
            row.append(deltas["bravo"][0] if deltas["bravo"] else "")
            row.append(deltas["alpha"][1] if deltas["alpha"] else "")
            row.append(deltas["bravo"][1] if deltas["bravo"] else "")

            sent_values = {}
            for name, (port, idx, baseline) in MODEL_MAP.items():
                ue_key = "alpha" if "alpha" in name else "bravo"
                if deltas[ue_key] is None:
                    sent_values[name] = last_sent[name]
                    continue
                _d, delta = deltas[ue_key]
                capped_delta = min(delta, MAX_DELTA_DB)
                raw_ploss = max(PLOSS_MIN,
                                min(PLOSS_MAX, baseline + capped_delta))
                # EMA smoothing to prevent link-flapping on fast pose changes
                smoothed[name] = (EMA_ALPHA * raw_ploss
                                   + (1.0 - EMA_ALPHA) * smoothed[name])
                new_ploss = smoothed[name]
                sent_values[name] = new_ploss

                if abs(new_ploss - last_sent[name]) >= CHANGE_THRESHOLD_DB:
                    cmd = f"channelmod modify {idx} ploss {new_ploss:.2f}"
                    if args.dry_run:
                        print(f"[dry-run] {name} (:{port} model {idx}): {cmd}")
                    else:
                        if links[name].send(cmd):
                            print(f"{name} (:{port} model {idx}) -> ploss {new_ploss:.2f}dB "
                                  f"(d={deltas[ue_key][0]:.1f}m)")
                    last_sent[name] = new_ploss

            row.extend([sent_values.get("gnb_ue0_alpha_ul", ""),
                        sent_values.get("gnb_ue1_bravo_ul", ""),
                        sent_values.get("ue_alpha_dl", ""),
                        sent_values.get("ue_bravo_dl", "")])
            writer.writerow(row)
            csv_file.flush()

    except KeyboardInterrupt:
        print("\nStopping channel bridge.")
    finally:
        csv_file.close()


if __name__ == "__main__":
    main()
