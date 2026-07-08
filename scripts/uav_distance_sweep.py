#!/usr/bin/env python3
"""
uav_distance_sweep.py — validates that channel_bridge.py's simulated path loss
(ploss_dB, via OAI telnet channelmod) and real over-the-air iperf3 throughput
both track horizontal UAV-to-alpha distance as expected.

Moves sar_uav (not sar_ground_alpha — alpha has a live TrajectoryFollower
patrol plugin that fights teleportation) via gz set_pose to a fixed altitude
and a series of horizontal offsets from alpha's current live position, and at
each step reads back the actual ploss channel_bridge.py pushed to the gNB
telnet port plus real iperf3 throughput over the air interface (oaitun_ue1 ->
alpha UE -> rfsim -> gNB -> UPF -> 10.0.0.200), NOT ping (ping -I loopback
bypasses the RF path entirely and proves nothing about the channel model).

Requires: gNB + alpha UE + channel_bridge.py + setup_over_air_test.sh already
running/applied.
"""

import csv
import json
import re
import subprocess
import sys
import time

WORLD = "sar_open_field"
ALPHA_TUN = "oaitun_ue1"
ALPHA_TEST_IP = "10.0.0.200"
UAV_ALT_M = 30.0
IPERF_DURATION_S = 5
SETTLE_S = 6.0  # EMA_ALPHA=0.3, rate=0.5s -> ~9 ticks for >95% convergence

DISTANCES_M = [15, 40, 60, 100, 150, 220, 300]

# channel_bridge.py already holds the only two connections the gNB telnet
# server tolerates (alpha + bravo TelnetLinks) — a 3rd connection just hangs.
# Read the ploss it actually sent from its own CSV log instead of telnet.
BRIDGE_CSV = "/home/innocentmsc/sar_sim/logs/channel_bridge_log.csv"
CSV_OUT = "/home/innocentmsc/sar_sim/logs/uav_distance_sweep.csv"


def gz_set_pose(model, x, y, z):
    req = f'name: "{model}" position: {{x: {x}, y: {y}, z: {z}}}'
    subprocess.run(
        ["gz", "service", "-s", f"/world/{WORLD}/set_pose",
         "--reqtype", "gz.msgs.Pose", "--reptype", "gz.msgs.Boolean",
         "--timeout", "2000", "--req", req],
        capture_output=True, text=True, check=True,
    )


def gz_get_pose(model):
    out = subprocess.run(
        ["gz", "topic", "-e", "-t", f"/model/{model}/pose", "-n", "1"],
        capture_output=True, text=True, timeout=5,
    ).stdout
    # two messages arrive: "<model>::link" (relative, ~0) then "<model>" (world) -- take the latter
    idx = out.rfind(f'name: "{model}"\n')
    if idx == -1:
        raise RuntimeError(f"could not find world-frame pose for {model} in:\n{out}")
    chunk = out[idx:]
    x = re.search(r"x:\s*([-\d.eE+]+)", chunk)
    y = re.search(r"y:\s*([-\d.eE+]+)", chunk)
    return (float(x.group(1)) if x else 0.0, float(y.group(1)) if y else 0.0)


def read_ploss_and_dhoriz():
    """Last row of channel_bridge.py's own CSV log: what it actually sent.

    NOTE: read positionally, not via DictReader — this file was created by
    an older schema version and its header (12 cols) no longer matches the
    18-col rows the current code writes (schema-drift bug, flagged separately).
    Current write order: [.., alpha_d_horiz(4), .., ploss_gnb_ue0(14), ..].
    """
    with open(BRIDGE_CSV, newline="") as f:
        rows = list(csv.reader(f))
    last = rows[-1]
    return float(last[14]), float(last[4])


def run_iperf3():
    out = subprocess.run(
        ["iperf3", "-c", ALPHA_TEST_IP, "--bind-dev", ALPHA_TUN,
         "-t", str(IPERF_DURATION_S), "-J"],
        capture_output=True, text=True, timeout=IPERF_DURATION_S + 15,
    ).stdout
    data = json.loads(out)
    return data["end"]["sum_received"]["bits_per_second"] / 1e6


def main():
    print("Reading alpha's current live position...")
    ax, ay = gz_get_pose("sar_ground_alpha")
    print(f"  alpha at x={ax:.2f}, y={ay:.2f}")

    rows = []
    try:
        for d in DISTANCES_M:
            target_x, target_y = ax + d, ay
            gz_set_pose("sar_uav", target_x, target_y, UAV_ALT_M)
            print(f"\nUAV -> ({target_x:.1f}, {target_y:.1f}, {UAV_ALT_M}m), horiz d={d}m from alpha")
            print(f"  settling {SETTLE_S}s for EMA convergence...")
            time.sleep(SETTLE_S)

            ploss, logged_d = read_ploss_and_dhoriz()
            print(f"  ploss (bridge CSV, last_sent): {ploss:.2f} dB  (bridge-measured d_horiz={logged_d:.1f}m)")

            mbps = run_iperf3()
            print(f"  iperf3 throughput: {mbps:.2f} Mbps")

            rows.append((d, logged_d, ploss, mbps))
    finally:
        print("\nRestoring sar_uav to (0, 0, 0.3)...")
        gz_set_pose("sar_uav", 0, 0, 0.3)

    with open(CSV_OUT, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["target_distance_m", "bridge_measured_d_horiz_m", "ploss_dB", "iperf3_mbps"])
        w.writerows(rows)

    print(f"\nSaved: {CSV_OUT}")
    print("\n{:>10} {:>14} {:>10} {:>12}".format("target(m)", "measured(m)", "ploss(dB)", "Mbps"))
    for d, logged_d, pl, mbps in rows:
        print("{:>10} {:>14.1f} {:>10.2f} {:>12.2f}".format(d, logged_d, pl, mbps))


if __name__ == "__main__":
    sys.exit(main())
