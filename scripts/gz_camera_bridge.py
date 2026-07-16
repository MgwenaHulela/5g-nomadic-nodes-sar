#!/usr/bin/env python3
"""gz_camera_bridge.py — pipe a robot's Gazebo camera into a raw video stream.

Subscribes to a gz camera Image topic (published by the world's Sensors
system) and writes the raw pixel frames to stdout, so a GStreamer encoder
can consume them. This runs in the DEFAULT network namespace (where gz-
transport is reachable); its stdout is piped into an encoder running INSIDE
the robot's netns, which is how the robot's point of view reaches the 5G
air interface. See stream_camera.sh.

    python3 scripts/gz_camera_bridge.py --topic /sar/bravo/camera > frames.raw

Frames are R8G8B8 (3 bytes/pixel), width*height*3 bytes each, tightly
packed — feed GStreamer with:
    rawvideoparse width=W height=H format=rgb framerate=F/1
"""
import argparse
import sys
import time

sys.path.insert(0, "/usr/lib/python3/dist-packages")
from gz.transport13 import Node
from gz.msgs10.image_pb2 import Image


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--topic", required=True, help="gz camera Image topic")
    ap.add_argument("--width", type=int, default=320)
    ap.add_argument("--height", type=int, default=240)
    args = ap.parse_args()

    out = sys.stdout.buffer
    state = {"n": 0, "warned": False, "expect": args.width * args.height * 3}

    def cb(msg):
        data = msg.data
        # Tightly packed R8G8B8 expected; if the row has stride padding
        # (step > width*3) repack row-by-row so the encoder stays aligned.
        step = msg.step or (msg.width * 3)
        if step != msg.width * 3:
            rows = [data[r * step: r * step + msg.width * 3]
                    for r in range(msg.height)]
            data = b"".join(rows)
        if len(data) != state["expect"] and not state["warned"]:
            sys.stderr.write(
                f"[gz_cam] warn: frame {msg.width}x{msg.height} "
                f"({len(data)}B) != expected {args.width}x{args.height} "
                f"({state['expect']}B) — set --width/--height to match the "
                f"sensor\n")
            state["warned"] = True
        try:
            out.write(data)
            out.flush()
        except BrokenPipeError:
            raise SystemExit(0)
        state["n"] += 1
        if state["n"] % 30 == 0:
            sys.stderr.write(f"[gz_cam] {state['n']} frames "
                             f"({msg.width}x{msg.height})\n")

    node = Node()
    if not node.subscribe(Image, args.topic, cb):
        sys.exit(f"[gz_cam] failed to subscribe to {args.topic}")
    sys.stderr.write(f"[gz_cam] subscribed to {args.topic}; streaming raw "
                     f"frames to stdout\n")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
