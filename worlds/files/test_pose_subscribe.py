#!/usr/bin/env python3
"""
test_pose_subscribe.py — checks whether direct gz.transport13 Python
subscription (the same method channel_bridge.py uses) receives data for
sar_uav, independent of whether the `gz topic -e` CLI tool works.

If this prints poses but `gz topic -e` times out, that confirms the CLI
echo path is the broken one specifically, not inter-process transport in
general — and channel_bridge.py should work fine as-is.
"""
import time
from gz.transport13 import Node
from gz.msgs10.pose_pb2 import Pose

received = {"count": 0}


def cb(msg):
    received["count"] += 1
    print(f"[{received['count']}] {msg.name}: "
          f"x={msg.position.x:.2f} y={msg.position.y:.2f} z={msg.position.z:.2f}")


node = Node()
topic = "/model/sar_uav/pose"
ok = node.subscribe(Pose, topic, cb)
print(f"Subscribed to {topic}: {ok}")
print("Waiting 15 seconds for messages...")

time.sleep(15)

if received["count"] == 0:
    print("\nNO MESSAGES RECEIVED — transport issue is not CLI-specific.")
else:
    print(f"\nReceived {received['count']} messages — direct gz.transport13 "
          f"subscription works. The 'gz topic -e' CLI is the broken path, "
          f"not inter-process transport itself.")
