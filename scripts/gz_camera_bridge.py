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
    ap.add_argument("--format", choices=("rgb", "thermal"), default="rgb",
                    help="rgb = R8G8B8 passthrough; thermal = L16 temperature "
                         "normalised per-frame to gray8 (feed GStreamer with "
                         "format=gray8)")
    args = ap.parse_args()

    out = sys.stdout.buffer
    bpp = 2 if args.format == "thermal" else 3
    state = {"n": 0, "warned": False,
             "expect": args.width * args.height * (1 if args.format == "thermal" else 3)}
    _np = None
    if args.format == "thermal":
        import numpy as _np

    def cb(msg):
        data = msg.data
        # Repack row-by-row if the source row has stride padding.
        step = msg.step or (msg.width * bpp)
        if step != msg.width * bpp:
            rows = [data[r * step: r * step + msg.width * bpp]
                    for r in range(msg.height)]
            data = b"".join(rows)
        if args.format == "thermal":
            # L16 temperature -> gray8, auto-scaled per frame so the warm
            # victim stands out against the cooler background.
            a = _np.frombuffer(data, dtype="<u2").astype(_np.float32)
            lo, hi = float(a.min()), float(a.max())
            a = (a - lo) * (255.0 / (hi - lo)) if hi > lo else a * 0
            data = a.astype(_np.uint8).tobytes()
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
            sys.stderr.write(f"[gz_cam] {state['n']} {args.format} frames "
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
