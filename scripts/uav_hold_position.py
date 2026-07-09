#!/usr/bin/env python3
"""
uav_hold_position.py — temporary stopgap.

Repeatedly teleports sar_uav back to a fixed pose via gz set_pose. Used
only to keep the UAV airborne at a fixed altitude while gravity would
otherwise pull the (ungoverned, no-thrust) x500 model back to the
ground. Not a substitute for real PX4 offboard flight — kill this the
moment PX4 SITL is holding position on its own.

Usage:
    python3 uav_hold_position.py --x 0 --y 0 --z 40
"""
import argparse
import subprocess
import time

WORLD = "sar_open_field"


def gz_set_pose(model, x, y, z):
    try:
        subprocess.run(
            ["gz", "service", "-s", f"/world/{WORLD}/set_pose",
             "--reqtype", "gz.msgs.Pose", "--reptype", "gz.msgs.Boolean",
             "--timeout", "1000",
             "--req", f'name: "{model}" position: {{x: {x}, y: {y}, z: {z}}}'],
            capture_output=True, timeout=2,
        )
    except subprocess.TimeoutExpired:
        pass


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="sar_uav")
    ap.add_argument("--x", type=float, default=0.0)
    ap.add_argument("--y", type=float, default=0.0)
    ap.add_argument("--z", type=float, default=40.0)
    ap.add_argument("--rate-hz", type=float, default=5.0)
    args = ap.parse_args()

    period = 1.0 / args.rate_hz
    print(f"Holding {args.model} at ({args.x}, {args.y}, {args.z}) @ {args.rate_hz}Hz")
    while True:
        gz_set_pose(args.model, args.x, args.y, args.z)
        time.sleep(period)


if __name__ == "__main__":
    main()
