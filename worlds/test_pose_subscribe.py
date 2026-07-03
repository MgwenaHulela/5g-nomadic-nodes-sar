#!/usr/bin/env python3
"""
test_pose_subscribe.py (v2) — same as Phase 1's version but takes the
model name as an argument instead of being hardcoded to sar_uav, so it
works for sar_ground_alpha / sar_ground_bravo too without editing.

Usage:
    python3 test_pose_subscribe.py sar_uav
    python3 test_pose_subscribe.py sar_ground_alpha
    python3 test_pose_subscribe.py sar_ground_alpha --seconds 30
"""
import argparse
import time
from gz.transport13 import Node
from gz.msgs10.pose_pb2 import Pose


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("model_name", help="e.g. sar_uav, sar_ground_alpha, sar_ground_bravo")
    ap.add_argument("--seconds", type=float, default=15.0)
    args = ap.parse_args()

    received = {"count": 0}

    def cb(msg):
        received["count"] += 1
        print(f"[{received['count']}] {msg.name}: "
              f"x={msg.position.x:.2f} y={msg.position.y:.2f} z={msg.position.z:.2f}")

    node = Node()
    topic = f"/model/{args.model_name}/pose"
    ok = node.subscribe(Pose, topic, cb)
    print(f"Subscribed to {topic}: {ok}")
    print(f"Waiting {args.seconds:.0f} seconds for messages...")

    time.sleep(args.seconds)

    if received["count"] == 0:
        print(f"\nNO MESSAGES RECEIVED for {args.model_name} — check that "
              f"the PosePublisher plugin is patched into its model.sdf "
              f"(see apply_burger_patch.sh / apply_x500_patch.sh) and that "
              f"it was actually spawned (gz topic -l | grep {args.model_name}).")
    else:
        print(f"\nReceived {received['count']} messages for {args.model_name}.")


if __name__ == "__main__":
    main()
