#!/usr/bin/env python3
"""
Channel Bridge — Nhlanhla Makhubela, UCT MSc 2026

Reads live UAV / UGV poses directly from gz-transport, computes A2G path loss
with FSPL + LoS/NLoS sigmoid model, and pushes runtime channel parameters into
OAI gNB/UE processes via telnet `channelmod modify`.

RF effects simulated:
  - Path loss (FSPL + LoS/NLoS excess) — via ploss_dB
  - Multipath fading (TDL-D for LoS, TDL-A for NLoS) — via type
  - Doppler-like temporal decorrelation — via forgetfact (mapped from velocity)
  - Propagation delay — via offset (samples from distance)
  - Thermal noise floor — via noise_power_dB (constant)

Model index mapping:
  gNB   (127.0.0.1:9091)  model 1 = rfsimu_channel_ue0 (alpha UL)
  gNB   (127.0.0.1:9091)  model 2 = rfsimu_channel_ue1 (bravo UL)
  UE alpha (127.0.0.1:9092) model 0 = rfsimu_channel_enB0 (DL)
  UE bravo (127.0.0.1:9093) model 0 = rfsimu_channel_enB0 (DL)
"""

import argparse
import csv
import math
import os
import socket
import sys
import time
from datetime import datetime
from collections import defaultdict

from gz.transport13 import Node as GzNode
from gz.msgs10.pose_pb2 import Pose

# ── Path loss model ──────────────────────────────────────────────────────────
FREQ_GHZ = 3.31968
UE_HEIGHT = 0.5
UAV_DEFAULT_H = 30.0
A_COEFF = 9.61
B_COEFF = 0.16
ETA_LOS = 1.0
ETA_NLOS = 20.0


def a2g_path_loss(d_horiz, h_uav, h_ue=UE_HEIGHT, freq_ghz=FREQ_GHZ):
    d_3d = math.sqrt(d_horiz ** 2 + (h_uav - h_ue) ** 2)
    d_3d = max(d_3d, POSITION_MIN_D)
    fspl = 20 * math.log10(d_3d) + 20 * math.log10(freq_ghz * 1e9) - 147.55
    theta = math.degrees(math.atan2(h_uav - h_ue, max(d_horiz, 0.1)))
    los_p = 1.0 / (1.0 + A_COEFF * math.exp(-B_COEFF * (theta - A_COEFF)))
    pl = fspl + los_p * ETA_LOS + (1.0 - los_p) * ETA_NLOS
    return pl, los_p, d_3d, theta


# ── Channel type selection ───────────────────────────────────────────────────
def select_channel_type(los_p):
    """TDL-D (LoS) for elevation > threshold, TDL-A (NLoS) otherwise."""
    return "TDL_D" if los_p > 0.5 else "TDL_A"


# ── Velocity -> forgetfact mapping ───────────────────────────────────────────
# Higher velocity -> lower forgetfact -> faster channel decorrelation (Doppler)
def velocity_to_forgetfact(speed_ms, dt=0.5):
    if speed_ms < 1.0:
        return 0.99
    elif speed_ms > 30.0:
        return 0.50
    else:
        # Linear mapping: 1 m/s -> 0.99, 30 m/s -> 0.50
        return max(0.50, 0.99 - (speed_ms - 1.0) * (0.49 / 29.0))


# ── Distance -> delay offset (samples) ───────────────────────────────────────
# OAI sample rate: 30.72 Msps (for 106 PRB at 30 kHz SCS)
# delay_samples = distance_m / c * sample_rate
SAMPLERATE_HZ = 30.72e6
SPEED_OF_LIGHT = 3e8


def distance_to_offset(d_3d):
    delay_s = d_3d / SPEED_OF_LIGHT
    return int(delay_s * SAMPLERATE_HZ)


# ── Runtime config ───────────────────────────────────────────────────────────
ROBOT_POSE_TOPICS = {
    "sar_uav":          "/model/sar_uav/pose",
    "sar_ground_alpha": "/model/sar_ground_alpha/pose",
    "sar_ground_bravo": "/model/sar_ground_bravo/pose",
}

REFERENCE_HORIZ_DISTANCE_M = 50.0
EMA_ALPHA = 0.3

MODEL_MAP = {
    # name: (telnet_port, model_index, baseline_ploss_dB)
    # gNB model 0 = rfsimu_channel_enB0 (owner: not set — unused), 1 = ue0 (alpha UL), 2 = ue1 (bravo UL)
    "gnb_ue0_alpha_ul": (9091, 1, 20.0),
    "gnb_ue1_bravo_ul": (9091, 2, 25.0),
    "ue_alpha_dl":      (9092, 0, 15.0),
    "ue_bravo_dl":      (9093, 0, 20.0),
}

PLOSS_MIN, PLOSS_MAX = 10.0, 80.0
MAX_DELTA_DB = 50.0  # allow full A2G range for ML training
POSITION_MIN_D = 1.0
POSITION_MAX_JUMP_M = 200.0  # reject pose jumps >200m (Gazebo glitch filter)
CHANGE_THRESHOLD_DB = 0.5
UPDATE_INTERVAL_S = 0.5
NOISE_POWER_DB = -6.0
DS_TDL_DEFAULT = 50e-9  # 50 ns RMS delay spread

CSV_PATH = os.path.expanduser("~/sar_sim/logs/channel_bridge_log.csv")


# ── Persistent telnet connections ────────────────────────────────────────────
class TelnetLink:
    def __init__(self, port, host="127.0.0.1"):
        self.host = host
        self.port = port
        self.sock = None

    def connect(self):
        try:
            self.sock = socket.create_connection((self.host, self.port), timeout=2)
            self.sock.settimeout(1)
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
                self.sock.recv(4096)
            except socket.timeout:
                pass
            return True
        except OSError as e:
            print(f"[telnet:{self.port}] send failed ({e}), will reconnect")
            self.sock = None
            return False


# ── Pose subscribers ─────────────────────────────────────────────────────────
positions = {}
prev_positions = {}  # {name: (x, y, z, timestamp)} — previous for speed calc
prev_types = {}      # track last channel type sent to avoid redundant type changes


def make_pose_callback(model_name):
    def cb(msg):
        # PosePublisher emits both the model pose (name == model_name) and
        # each link's pose (name == "<model_name>::<link>", relative, ~zero)
        # on the same topic — ignore anything but the model-level message.
        if msg.name != model_name:
            return
        now = time.time()
        pos = (msg.position.x, msg.position.y, msg.position.z)
        if model_name in positions:
            prev_positions[model_name] = (*positions[model_name], now)
        else:
            prev_positions[model_name] = (0.0, 0.0, 0.0, now)
        positions[model_name] = pos
    return cb


def horiz_distance(a, b):
    return math.hypot(a[0] - b[0], a[1] - b[1])


def compute_speed(model_name, pos, now):
    if model_name not in prev_positions:
        return 0.0
    px, py, pz, pt = prev_positions[model_name]
    dt = now - pt
    if dt <= 0:
        return 0.0
    dx = pos[0] - px
    dy = pos[1] - py
    dz = pos[2] - pz
    return math.hypot(dx, dy, dz) / dt





def main():
    sys.stdout.reconfigure(line_buffering=True)
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--rate", type=float, default=UPDATE_INTERVAL_S)
    ap.add_argument("--ref-distance", type=float, default=REFERENCE_HORIZ_DISTANCE_M)
    args = ap.parse_args()

    gz = GzNode()
    for model_name, topic in ROBOT_POSE_TOPICS.items():
        ok = gz.subscribe(Pose, topic, make_pose_callback(model_name))
        if not ok:
            print(f"ERROR: failed to subscribe to {topic}")
            return
        print(f"Subscribed to {topic}")

    links = {name: TelnetLink(port) for name, (port, _idx, _base) in MODEL_MAP.items()}
    if not args.dry_run:
        for name, link in links.items():
            link.connect()

    pl_ref, _, _, _ = a2g_path_loss(args.ref_distance, UAV_DEFAULT_H)

    os.makedirs(os.path.dirname(CSV_PATH), exist_ok=True)
    csv_new = not os.path.exists(CSV_PATH)
    csv_file = open(CSV_PATH, "a", newline="")
    writer = csv.writer(csv_file)
    if csv_new:
        writer.writerow(["timestamp", "uav_x", "uav_y", "uav_z",
                          "alpha_d_horiz", "bravo_d_horiz",
                          "alpha_delta_db", "bravo_delta_db",
                          "alpha_speed_ms", "bravo_speed_ms",
                          "alpha_ch_type", "bravo_ch_type",
                          "alpha_forgetfact", "bravo_forgetfact",
                          "ploss_gnb_ue0", "ploss_gnb_ue1",
                          "ploss_ue_alpha_dl", "ploss_ue_bravo_dl"])

    last_sent = {name: base for name, (_p, _i, base) in MODEL_MAP.items()}
    smoothed = {name: base for name, (_p, _i, base) in MODEL_MAP.items()}
    sent_ds = set()
    prev_accepted = {}

    print(f"Reference: {args.ref_distance:.1f}m horiz -> pl_ref={pl_ref:.1f}dB")
    print("Waiting for pose data..." if not positions else "Pose data flowing.")

    try:
        while True:
            time.sleep(args.rate)

            if "sar_uav" not in positions:
                print("waiting for sar_uav pose...")
                continue

            uav = positions["sar_uav"]
            uav_h = uav[2] if uav[2] > 0 else UAV_DEFAULT_H
            now = time.time()

            row = [datetime.now().isoformat(), *uav]

            # Compute per-UE metrics
            ue_data = {}
            for ue_name, key in [("sar_ground_alpha", "alpha"), ("sar_ground_bravo", "bravo")]:
                if ue_name not in positions:
                    ue_data[key] = None
                    continue
                ue_pos = positions[ue_name]
                d_horiz = horiz_distance(uav, ue_pos)

                # Glitch filter: reject position jumps > 50m (Gazebo artifact)
                prev_horiz = prev_accepted.get(ue_name)
                if prev_horiz is not None and abs(d_horiz - prev_horiz) > POSITION_MAX_JUMP_M:
                    print(f"  [GLITCH] {ue_name} jumped {abs(d_horiz - prev_horiz):.0f}m — skipping")
                    ue_data[key] = None
                    continue
                prev_accepted[ue_name] = d_horiz

                pl, los_p, d_3d, theta = a2g_path_loss(d_horiz, uav_h)
                delta = pl - pl_ref
                speed = compute_speed(ue_name, ue_pos, now)
                ch_type = select_channel_type(los_p)
                forgetfact = velocity_to_forgetfact(speed, args.rate)
                offset = distance_to_offset(d_3d)
                ue_data[key] = {
                    "d_horiz": d_horiz, "delta": delta, "los_p": los_p,
                    "speed": speed, "ch_type": ch_type,
                    "forgetfact": forgetfact, "offset": offset,
                }

            for key in ["alpha", "bravo"]:
                if ue_data[key]:
                    row += [ue_data[key]["d_horiz"], ue_data[key]["delta"]]
                else:
                    row += ["", ""]

            for key in ["alpha", "bravo"]:
                if ue_data[key]:
                    row += [ue_data[key]["speed"], ue_data[key]["ch_type"],
                            f"{ue_data[key]['forgetfact']:.3f}"]
                else:
                    row += ["", "", ""]

            # Send telnet commands per model
            for name, (port, idx, baseline) in MODEL_MAP.items():
                ue_key = "alpha" if "alpha" in name else "bravo"
                data = ue_data.get(ue_key)
                if data is None:
                    continue

                capped_delta = max(0.0, min(data["delta"], MAX_DELTA_DB))
                raw_ploss = max(PLOSS_MIN, min(PLOSS_MAX, baseline + capped_delta))
                smoothed[name] = (EMA_ALPHA * raw_ploss
                                   + (1.0 - EMA_ALPHA) * smoothed[name])
                new_ploss = smoothed[name]

                if abs(new_ploss - last_sent[name]) >= CHANGE_THRESHOLD_DB:
                    if not args.dry_run:
                        links[name].send(f"channelmod modify {idx} ploss {new_ploss:.2f}")
                    print(f"  {name} (:{port}:{idx}) -> ploss {new_ploss:.2f}dB "
                          f"(d={data['d_horiz']:.1f}m, v={data['speed']:.1f}m/s)")
                    last_sent[name] = new_ploss

                # Set delay offset (runtime-supported)
                if data["offset"] > 0:
                    if not args.dry_run:
                        links[name].send(f"channelmod modify {idx} offset {data['offset']}")

                # Note: type, forgetfact, ds_tdl, noise_power_dB are set in config
                # files at startup (not changeable via telnet at runtime in this OAI version)

            row.extend([
                f"{last_sent.get('gnb_ue0_alpha_ul', ''):.2f}",
                f"{last_sent.get('gnb_ue1_bravo_ul', ''):.2f}",
                f"{last_sent.get('ue_alpha_dl', ''):.2f}",
                f"{last_sent.get('ue_bravo_dl', ''):.2f}",
            ])
            writer.writerow(row)
            csv_file.flush()

    except KeyboardInterrupt:
        print("\nStopping channel bridge.")
    finally:
        csv_file.close()


if __name__ == "__main__":
    main()
