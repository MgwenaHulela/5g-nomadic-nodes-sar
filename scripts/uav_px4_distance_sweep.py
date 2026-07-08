#!/usr/bin/env python3
"""
uav_px4_distance_sweep.py — validates channel_bridge.py's simulated path loss
and real over-the-air iperf3 throughput both track UAV-to-alpha horizontal
distance, with the UAV genuinely hovering under PX4 flight control (not a
gz set_pose teleport that free-falls without an active flight controller).

Flies sar_uav (real PX4 x500, MAVSDK offboard) to 30m altitude and a series
of horizontal offsets from alpha's live position. At each step reads back
the ploss channel_bridge.py actually pushed to the gNB telnet port (via its
own CSV log — a fresh telnet connection would be refused, the gNB telnet
server only tolerates the two connections the bridge already holds) plus
real iperf3 throughput over the air interface. NOT ping — ping -I loopback
bypasses the RF path entirely and proves nothing about the channel model.

Requires: gNB + alpha UE + channel_bridge.py + setup_over_air_test.sh
already running/applied, and PX4 SITL attached to the live sar_uav entity.
"""

import asyncio
import csv
import json
import re
import subprocess
import sys

from mavsdk import System
from mavsdk.offboard import OffboardError, PositionNedYaw

ALPHA_TUN = "oaitun_ue1"
ALPHA_TEST_IP = "10.0.0.200"
ALTITUDE_M = 30.0
IPERF_DURATION_S = 5
HOLD_S = 15.0  # extra settle time after arrival, for the bridge's EMA to converge

DISTANCES_M = [15, 40, 60, 100, 150, 220, 300]

BRIDGE_CSV = "/home/innocentmsc/sar_sim/logs/channel_bridge_log.csv"
CSV_OUT = "/home/innocentmsc/sar_sim/logs/uav_px4_distance_sweep.csv"


def gz_get_pose(model):
    out = subprocess.run(
        ["gz", "topic", "-e", "-t", f"/model/{model}/pose", "-n", "1"],
        capture_output=True, text=True, timeout=5,
    ).stdout
    idx = out.rfind(f'name: "{model}"\n')
    if idx == -1:
        raise RuntimeError(f"could not find world-frame pose for {model} in:\n{out}")
    chunk = out[idx:]
    x = re.search(r"x:\s*([-\d.eE+]+)", chunk)
    y = re.search(r"y:\s*([-\d.eE+]+)", chunk)
    return (float(x.group(1)) if x else 0.0, float(y.group(1)) if y else 0.0)


def read_ploss_and_dhoriz():
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


async def fly_to_world_xy(drone, target_wx, target_wy, down, settle_s,
                           tolerance_m=5.0, max_flight_s=60.0):
    """gz world (x,y) maps to PX4 NED (north=y, east=x) in this world's
    ENU/NED alignment (confirmed empirically: commanded east tracked world_x,
    commanded north tracked world_y). Streams setpoints continuously (required
    to keep offboard mode alive), polling live position until within
    tolerance_m or max_flight_s elapses, then holds an extra settle_s for the
    bridge's EMA smoothing to converge on the new distance."""
    north, east = target_wy, target_wx
    elapsed = 0.0
    while elapsed < max_flight_s:
        await drone.offboard.set_position_ned(PositionNedYaw(north, east, down, 0.0))
        await asyncio.sleep(0.2)
        elapsed += 0.2
        if elapsed >= 2.0 and int(elapsed * 5) % 10 == 0:  # check every ~2s
            wx, wy = gz_get_pose("sar_uav")
            d = ((wx - target_wx) ** 2 + (wy - target_wy) ** 2) ** 0.5
            if d <= tolerance_m:
                break
    ticks = int(settle_s / 0.2)
    for _ in range(ticks):
        await drone.offboard.set_position_ned(PositionNedYaw(north, east, down, 0.0))
        await asyncio.sleep(0.2)


async def main():
    drone = System()
    await drone.connect(system_address="udp://:14540")

    async for state in drone.core.connection_state():
        if state.is_connected:
            print("-- Connected to PX4")
            break

    async for health in drone.telemetry.health():
        if health.is_global_position_ok and health.is_home_position_ok:
            print("-- Position lock OK")
            break

    print("-- Arming")
    await drone.action.arm()

    print(f"-- Taking off to {ALTITUDE_M}m")
    await drone.action.set_takeoff_altitude(ALTITUDE_M)
    await drone.action.takeoff()
    await asyncio.sleep(20)

    down = -ALTITUDE_M
    print("-- Starting offboard")
    await drone.offboard.set_position_ned(PositionNedYaw(0.0, 0.0, down, 0.0))
    try:
        await drone.offboard.start()
    except OffboardError as e:
        print(f"Offboard start failed: {e._result.result}")
        await drone.action.land()
        return

    rows = []
    try:
        for d in DISTANCES_M:
            # alpha patrols continuously -- re-read its live position each
            # step rather than relying on a stale reading from sweep start
            ax, ay = gz_get_pose("sar_ground_alpha")
            target_x, target_y = ax + d, ay
            print(f"\n-- alpha now at ({ax:.1f},{ay:.1f}); flying UAV to world "
                  f"({target_x:.1f}, {target_y:.1f}), target horiz d={d}m")
            await fly_to_world_xy(drone, target_x, target_y, down, HOLD_S)

            uav_x, uav_y = gz_get_pose("sar_uav")
            real_d = ((uav_x - ax) ** 2 + (uav_y - ay) ** 2) ** 0.5
            print(f"  actual UAV pos=({uav_x:.1f},{uav_y:.1f}) real horiz d={real_d:.1f}m")

            ploss, logged_d = read_ploss_and_dhoriz()
            print(f"  ploss (bridge CSV, last_sent): {ploss:.2f} dB  (bridge-measured d_horiz={logged_d:.1f}m)")

            mbps = run_iperf3()
            print(f"  iperf3 throughput: {mbps:.2f} Mbps")

            rows.append((d, real_d, logged_d, ploss, mbps))
    finally:
        print("\n-- Sweep done, returning to land")
        try:
            await drone.offboard.stop()
        except Exception:
            pass
        try:
            await drone.action.land()
        except Exception:
            pass

    with open(CSV_OUT, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["target_distance_m", "actual_uav_gz_d_horiz_m", "bridge_measured_d_horiz_m",
                    "ploss_dB", "iperf3_mbps"])
        w.writerows(rows)

    print(f"\nSaved: {CSV_OUT}")
    print("\n{:>10} {:>12} {:>14} {:>10} {:>12}".format(
        "target(m)", "actual(m)", "bridge(m)", "ploss(dB)", "Mbps"))
    for d, real_d, logged_d, pl, mbps in rows:
        print("{:>10} {:>12.1f} {:>14.1f} {:>10.2f} {:>12.2f}".format(
            d, real_d, logged_d, pl, mbps))


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
