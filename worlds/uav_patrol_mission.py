#!/usr/bin/env python3
"""
uav_patrol_mission.py — Nhlanhla Makhubela, UCT MSc 2026

Takes off the PX4 x500 (named "sar_uav" in Gazebo) to 30m and flies a
continuous circular patrol, matching the "UAV circles overhead at 30m as
the 5G gNB" behavior from the earlier Fortress-based project.

This uses MAVSDK Offboard position setpoints rather than a PX4 Mission,
because Missions don't loop natively — Offboard lets us just keep
computing new circle points forever until interrupted.

Connects over udp://:14540, PX4 SITL's default MAVSDK port.

Usage:
    python3 uav_patrol_mission.py                  # circle radius 60m, alt 30m
    python3 uav_patrol_mission.py --radius 100      # wider circle
    python3 uav_patrol_mission.py --altitude 40 --period 90
"""

import argparse
import asyncio
import math
import time

from mavsdk import System
from mavsdk.offboard import OffboardError, PositionNedYaw


async def run(radius_m: float, altitude_m: float, period_s: float):
    drone = System()
    await drone.connect(system_address="udp://:14540")

    print("Waiting for drone connection...")
    async for state in drone.core.connection_state():
        if state.is_connected:
            print("-- Connected")
            break

    print("Waiting for global position + home lock...")
    async for health in drone.telemetry.health():
        if health.is_global_position_ok and health.is_home_position_ok:
            print("-- Position lock OK")
            break

    print("-- Arming")
    await drone.action.arm()

    print(f"-- Taking off to {altitude_m:.0f}m")
    await drone.action.set_takeoff_altitude(altitude_m)
    await drone.action.takeoff()
    await asyncio.sleep(max(10.0, altitude_m / 2.0))  # rough climb time

    # NED convention: down is positive, so altitude_m up = -altitude_m in "down"
    down = -altitude_m

    print("-- Priming offboard with an initial setpoint")
    await drone.offboard.set_position_ned(PositionNedYaw(0.0, 0.0, down, 0.0))
    try:
        await drone.offboard.start()
    except OffboardError as e:
        print(f"Offboard start failed: {e._result.result}")
        await drone.action.land()
        return

    print(f"-- Patrolling: radius={radius_m}m altitude={altitude_m}m period={period_s}s "
          f"(Ctrl+C to land and exit)")
    omega = 2.0 * math.pi / period_s
    t0 = time.monotonic()

    try:
        while True:
            t = time.monotonic() - t0
            angle = omega * t
            north = radius_m * math.cos(angle)
            east = radius_m * math.sin(angle)
            # yaw to face direction of travel (tangent to circle)
            yaw_deg = math.degrees(angle + math.pi / 2.0)
            await drone.offboard.set_position_ned(
                PositionNedYaw(north, east, down, yaw_deg))
            await asyncio.sleep(0.1)  # 10 Hz setpoint rate, well above PX4's min
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
    finally:
        print("\n-- Stopping offboard, returning to land")
        try:
            await drone.offboard.stop()
        except Exception:
            pass  # connection may already be gone if interrupted mid-RPC
        try:
            await drone.action.land()
        except Exception:
            pass


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--radius", type=float, default=60.0,
                     help="patrol circle radius in meters (default 60)")
    ap.add_argument("--altitude", type=float, default=30.0,
                     help="patrol altitude in meters, matches UAV_DEFAULT_H "
                          "in channel_bridge.py (default 30)")
    ap.add_argument("--period", type=float, default=90.0,
                     help="seconds per full circle (default 90)")
    args = ap.parse_args()
    asyncio.run(run(args.radius, args.altitude, args.period))


if __name__ == "__main__":
    main()
