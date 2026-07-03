#!/usr/bin/env python3
"""
diagnose_gps.py — prints raw GPS/position telemetry for a few seconds
so we can see WHY is_global_position_ok is false, instead of just
knowing that it is. Run this in a second terminal while
uav_patrol_mission.py is still waiting on "Position lock OK" in the
first terminal.

Ctrl+C to stop.
"""
import asyncio
from mavsdk import System


async def run():
    drone = System()
    await drone.connect(system_address="udp://:14540")

    async def watch_gps():
        async for gps in drone.telemetry.gps_info():
            print(f"GPS: fix_type={gps.fix_type}  num_satellites={gps.num_satellites}")

    async def watch_health():
        async for health in drone.telemetry.health():
            print(f"HEALTH: global_pos_ok={health.is_global_position_ok} "
                  f"home_pos_ok={health.is_home_position_ok} "
                  f"local_pos_ok={health.is_local_position_ok} "
                  f"gyro_ok={health.is_gyrometer_calibration_ok} "
                  f"accel_ok={health.is_accelerometer_calibration_ok} "
                  f"mag_ok={health.is_magnetometer_calibration_ok}")

    await asyncio.gather(watch_gps(), watch_health())


if __name__ == "__main__":
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        pass
