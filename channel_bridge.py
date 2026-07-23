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
import random
import re
import socket
import subprocess
import sys
import time
from datetime import datetime
from collections import defaultdict

from gz.transport13 import Node as GzNode
from gz.msgs10.pose_v_pb2 import Pose_V

# ── Path loss model ──────────────────────────────────────────────────────────
FREQ_GHZ = 3.31968
UE_HEIGHT = 0.5
UAV_DEFAULT_H = 30.0
A_COEFF = 9.61
B_COEFF = 0.16
ETA_LOS = 1.0
ETA_NLOS = 20.0

# Log-normal shadow fading (Gaussian in dB) layered on top of the mean A2G
# path loss. The pure geometry gives a clean, noise-free loss; real urban
# shadowing has several dB of spread, and NLoS scenes shadow harder than
# LoS (rooftop diffraction + clutter depth).
#
# Crucially the fade is correlated over DISTANCE, not time: a receiver that
# stops moving keeps a constant shadow offset (Gudmundson model, corr =
# exp(-d_moved / d_corr)). Driving it off wall-clock ticks instead made a
# stationary robot's loss flicker every tick — the "metrics coming and
# going" symptom. d_corr is the decorrelation length (tens of m in cities).
SHADOW_SIGMA_LOS = 2.0    # dB, 1-sigma shadowing with clear line of sight
SHADOW_SIGMA_NLOS = 5.0   # dB, 1-sigma shadowing when a building blocks
SHADOW_DCORR_M = 20.0     # m, shadow-fading decorrelation distance


def a2g_path_loss(d_horiz, h_uav, h_ue=UE_HEIGHT, freq_ghz=FREQ_GHZ,
                  los_blocked=None, excess_db=0.0):
    """A2G path loss = FSPL + LoS/NLoS excess + terrain excess.

    los_blocked=None (open-field): LoS *probability* from the elevation
    angle (Al-Hourani sigmoid), the graded model used for the LoS study.
    los_blocked=True/False (urban/forest/hills/indoor): LoS is
    *deterministic* — set by a geometric check of whether an obstacle
    blocks the UAV->UE ray. This is what makes each world's obstacles
    genuinely shadow the link, so that climbing the UAV to clear a
    rooftop / ridge / canopy restores LoS.
    excess_db: extra attenuation from graded terrain clutter — foliage
    depth (Weissberger), terrain diffraction (knife-edge), or building
    penetration — layered on top of the FSPL+LoS/NLoS baseline. This is
    what lets forest/hills/indoor worlds add *continuous* loss instead
    of only a binary block.
    """
    d_3d = math.sqrt(d_horiz ** 2 + (h_uav - h_ue) ** 2)
    d_3d = max(d_3d, POSITION_MIN_D)
    fspl = 20 * math.log10(d_3d) + 20 * math.log10(freq_ghz * 1e9) - 147.55
    theta = math.degrees(math.atan2(h_uav - h_ue, max(d_horiz, 0.1)))
    if los_blocked is None:
        los_p = 1.0 / (1.0 + A_COEFF * math.exp(-B_COEFF * (theta - A_COEFF)))
    else:
        los_p = 0.0 if los_blocked else 1.0
    pl = fspl + los_p * ETA_LOS + (1.0 - los_p) * ETA_NLOS + excess_db
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
REFERENCE_HORIZ_DISTANCE_M = 50.0
EMA_ALPHA = 0.3

MODEL_MAP = {
    # name: (telnet_port, model_index, baseline_ploss_dB)
    # gNB model 0 = rfsimu_channel_enB0 (owner: not set — unused), 1 = ue0 (alpha UL), 2 = ue1 (bravo UL)
    #
    # Baselines recalibrated 2026-07-11 for TRUE attenuation semantics
    # (see the sign note at the telnet send): with noise_power_dB = -40,
    # the link is healthy at 0 dB attenuation and dies at ~35 dB.
    # Alpha UL 8 + distance delta (0..~27 dB over 10-190 m) spans
    # healthy -> cliff like the original design; bravo +5 dB weaker.
    "gnb_ue0_alpha_ul": (9091, 1, 8.0),
    "gnb_ue1_bravo_ul": (9091, 2, 13.0),
    "ue_alpha_dl":      (9092, 0, 3.0),
    "ue_bravo_dl":      (9093, 0, 8.0),
}

PLOSS_MIN, PLOSS_MAX = 0.0, 45.0
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

    def query(self, cmd, wait=1.5):
        """Send a command and collect the multi-line reply."""
        if self.sock is None and not self.connect():
            return ""
        try:
            self.sock.sendall((cmd + "\n").encode())
            time.sleep(wait)
            out = b""
            try:
                while True:
                    chunk = self.sock.recv(4096)
                    if not chunk:
                        break
                    out += chunk
            except socket.timeout:
                pass
            return out.decode(errors="replace")
        except OSError:
            self.sock = None
            return ""


# ── Pose data ────────────────────────────────────────────────────────────────
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


WORLD = _detect_world()
POSE_TOPIC = f"/world/{WORLD}/pose/info"

# ── Per-world terrain coupling ───────────────────────────────────────────────
# Each world defines world_channel(uav, ue) -> (los_blocked, excess_db):
#   los_blocked: True/False forces deterministic NLoS/LoS; None keeps the
#                open-field probabilistic elevation-angle model.
#   excess_db:   graded extra attenuation from terrain clutter (foliage
#                depth, terrain diffraction, building penetration) added on
#                top of FSPL+LoS/NLoS.
# The obstacle geometry is imported from a shared module that ALSO drives
# the world SDF generator, so the radio and the visuals agree on where the
# trees / hills / walls are. Open-field is the graded-probability baseline.
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "worlds"))

if WORLD == "sar_urban":
    from urban_buildings import any_blocked, BUILDINGS

    def world_channel(uav, ue):
        return (any_blocked(uav, ue), 0.0)
    print(f"[urban] NLoS active — {len(BUILDINGS)} buildings drive LoS/NLoS")

elif WORLD == "sar_forest":
    from forest_trees import foliage_depth, foliage_loss_db, TREES, DEPTH_NLOS_M

    def world_channel(uav, ue):
        depth = foliage_depth(uav, ue)
        return (depth >= DEPTH_NLOS_M, foliage_loss_db(depth))
    print(f"[forest] foliage attenuation active — {len(TREES)} trees "
          f"(Weissberger, NLoS >= {DEPTH_NLOS_M} m canopy)")

elif WORLD == "sar_hills":
    from terrain_hills import terrain_diffraction_db, HILLS, DIFFRACTION_NLOS_DB

    def world_channel(uav, ue):
        loss = terrain_diffraction_db(uav, ue)
        return (loss >= DIFFRACTION_NLOS_DB, loss)
    print(f"[hills] terrain diffraction active — {len(HILLS)} hills "
          f"(ITU-R P.526 knife-edge, NLoS >= {DIFFRACTION_NLOS_DB} dB)")

elif WORLD == "sar_indoor":
    from indoor_building import indoor_loss, WALLS

    def world_channel(uav, ue):
        return indoor_loss(uav, ue)   # (blocked, excess_db)
    print(f"[indoor] building-penetration active — {len(WALLS)} walls "
          f"(roof + per-wall loss when the robot is inside)")

else:
    def world_channel(uav, ue):
        return (None, 0.0)

# {model_name: (x, y, z)}
positions = {}
prev_positions = {}  # {name: (x, y, z, timestamp)} — previous for speed calc
prev_types = {}      # track last channel type sent to avoid redundant type changes

# Models spawned via EntityFactory with an explicit name: override, so
# pose/info reports them under exactly these names (nested x500_base /
# turtlebot3_burger entities are merged into the wrapper and don't appear).
TARGET_MODELS = {"sar_uav", "sar_ground_alpha", "sar_ground_bravo"}


def pose_cb(msg):
    now = time.time()
    for p in msg.pose:
        name = p.name
        if name not in TARGET_MODELS:
            continue
        pos = (p.position.x, p.position.y, p.position.z)
        if name in positions:
            prev_positions[name] = (*positions[name], now)
        else:
            prev_positions[name] = (0.0, 0.0, 0.0, now)
        positions[name] = pos


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
    ap.add_argument("--no-shadow", action="store_true",
                    help="disable log-normal shadow fading (deterministic "
                         "geometric path loss only, reproducible)")
    ap.add_argument("--shadow-seed", type=int, default=None,
                    help="RNG seed for shadow fading, for reproducible runs")
    args = ap.parse_args()
    if args.shadow_seed is not None:
        random.seed(args.shadow_seed)

    # ONE TelnetLink PER PORT, shared by all models on that port. OAI's
    # telnet server accepts a single client: a second socket to the same
    # port completes the TCP handshake (listen backlog) but is never
    # accepted, and every write to it is silently discarded. With one
    # link per model, bravo's gNB-side UL commands (second socket to
    # 9091) vanished without any error — its uplink channel was never
    # modulated in any campaign before 2026-07-11.
    port_links = {}
    for name, (port, _idx, _base) in MODEL_MAP.items():
        if port not in port_links:
            port_links[port] = TelnetLink(port)
    links = {name: port_links[port] for name, (port, _i, _b) in MODEL_MAP.items()}

    if not args.dry_run:
        for port, link in port_links.items():
            # Probe until LIVE, not merely connected: a connection sitting
            # in the server's accept backlog (e.g. behind a dead client not
            # yet reaped) completes the TCP handshake but drops all writes.
            # Only a connection that answers a query is trustworthy.
            live = False
            for attempt in range(10):
                if link.sock is not None:
                    try:
                        link.sock.close()
                    except OSError:
                        pass
                    link.sock = None
                if link.connect() and "model" in link.query("channelmod show current", wait=1.5):
                    live = True
                    break
                time.sleep(2.5)
            if not live:
                print(f"FATAL: telnet :{port} never became responsive")
                return
            print(f"[verify] telnet :{port}: connection live (attempt {attempt+1})")
        # Read-back verification: write each model's baseline, then parse
        # it back from `channelmod show current`. A silently-dropped write
        # (this bug's signature) fails loudly here instead of poisoning a
        # whole campaign.
        for name, (port, idx, base) in MODEL_MAP.items():
            port_links[port].send(f"channelmod modify {idx} ploss {-base:.2f}")
        time.sleep(1)
        for port, link in port_links.items():
            expect = {idx: -base for n, (p, idx, base) in MODEL_MAP.items() if p == port}
            ok = False
            for attempt in range(4):
                reply = link.query("channelmod show current", wait=2.0)
                # pair model index -> its own block's path loss
                got = {}
                # NOTE: don't split on "model " — the "model owner:" line
                # inside each block would sever the header from its params
                for m in re.finditer(
                        r"model (\d+) rfsimu[\s\S]*?path loss: (-?[\d.]+)",
                        reply):
                    got[int(m.group(1))] = float(m.group(2))
                ok = all(got.get(i) is not None and abs(got[i] - w) < 0.01
                         for i, w in expect.items())
                if ok:
                    break
                # empty/partial replies happen on a freshly-drained
                # connection — resend baselines and retry before declaring
                # the link dead
                for n, (p, idx, base) in MODEL_MAP.items():
                    if p == port:
                        link.send(f"channelmod modify {idx} ploss {-base:.2f}")
                time.sleep(2)
            if not ok:
                print(f"FATAL: telnet :{port} read-back failed after retries "
                      f"(expect {expect}, got {got}) — writes may be dropped")
                return
            print(f"[verify] telnet :{port}: all model baselines read back OK")

    # gz subscription starts AFTER telnet verification — fail fast on a
    # bad RAN link before touching the simulation side.
    gz = GzNode()
    ok = gz.subscribe(Pose_V, POSE_TOPIC, pose_cb)
    if not ok:
        print(f"ERROR: failed to subscribe to {POSE_TOPIC}")
        return
    print(f"Subscribed to {POSE_TOPIC}")

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
    shadow = {name: 0.0 for name in MODEL_MAP}   # AR(1) shadow-fade state
    sent_ds = set()
    prev_accepted = {}

    print(f"Reference: {args.ref_distance:.1f}m horiz -> pl_ref={pl_ref:.1f}dB")
    print("Waiting for pose data..." if not positions else "Pose data flowing.")

    try:
        while True:
            time.sleep(args.rate)

            if "sar_uav" not in positions:
                print("waiting for UAV pose...")
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

                # Per-world terrain coupling: deterministic LoS block and/or
                # graded excess loss (foliage / diffraction / wall). Open-field
                # returns (None, 0) -> probabilistic elevation-angle model.
                blocked, excess = world_channel(uav, ue_pos)
                pl, los_p, d_3d, theta = a2g_path_loss(d_horiz, uav_h,
                                                       los_blocked=blocked,
                                                       excess_db=excess)
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

                # Negative deltas (robot closer than the 50m reference) are
                # allowed: signal is genuinely stronger near the UAV's nadir,
                # floored by PLOSS_MIN. Clamping them to 0 (Jul 3 precaution,
                # alongside the justified MAX_DELTA_DB cap for the 45dB RLF
                # cliff) froze ploss at baseline for ALL distances < 50m —
                # a dead zone that erased channel variation from the ML data.
                capped_delta = min(data["delta"], MAX_DELTA_DB)
                raw_ploss = max(PLOSS_MIN, min(PLOSS_MAX, baseline + capped_delta))
                smoothed[name] = (EMA_ALPHA * raw_ploss
                                   + (1.0 - EMA_ALPHA) * smoothed[name])

                # Log-normal shadow fading on top of the smoothed mean, with
                # sigma larger under NLoS. Correlated over distance moved
                # (Gudmundson): rho -> 1 when the robot is still, so its
                # shadow offset holds steady instead of flickering; only
                # motion mixes in new fading. Kept separate from the EMA so
                # its stationary std matches sigma rather than being damped.
                if args.no_shadow:
                    shadow[name] = 0.0
                else:
                    sigma = (SHADOW_SIGMA_NLOS if data["los_p"] < 0.5
                             else SHADOW_SIGMA_LOS)
                    d_moved = max(0.0, data["speed"]) * args.rate
                    rho = math.exp(-d_moved / SHADOW_DCORR_M)
                    shadow[name] = (rho * shadow[name]
                                    + math.sqrt(1.0 - rho ** 2)
                                    * random.gauss(0.0, sigma))
                new_ploss = max(PLOSS_MIN,
                                min(PLOSS_MAX, smoothed[name] + shadow[name]))

                if abs(new_ploss - last_sent[name]) >= CHANGE_THRESHOLD_DB:
                    # SIGN NOTE (2026-07-11): OAI's rfsimulator applies this
                    # field as a linear GAIN — apply_channelmod.c: "path_loss_dB
                    # should contain the total path gain"; pow(10, +x/20).
                    # Positive values AMPLIFY (until sample clipping). All
                    # internal math here keeps ploss as a positive attenuation
                    # magnitude; the sign is flipped exactly once, at the wire.
                    if not args.dry_run:
                        links[name].send(f"channelmod modify {idx} ploss {-new_ploss:.2f}")
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
