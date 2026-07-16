#!/usr/bin/env python3
"""sar_stream_sender.py — real application traffic from a SAR robot.

Runs INSIDE a robot's network namespace so the stream genuinely traverses
the 5G air interface (rfsim gNB -> UPF), not loopback:

    sudo ip netns exec robot_bravo python3 scripts/sar_stream_sender.py \
        --dst 10.0.0.201 --bind-dev oaitun_ue1p2 --robot-id BRAVO

It models a robot's uplink video + sensor feed: a constant-bitrate stream
of framed UDP packets (like an RTP video flow) with a small JSON telemetry
blob riding in the first packet of every frame. Each packet is sequence-
and timestamp-tagged so the collector (sar_stream_collector.py) can measure
real application-level goodput, frame-delivery ratio, packet loss, one-way
latency and jitter — all of which degrade as the robot goes NLoS behind a
building, because the same channel model shaping iperf shapes this too.

Wire format per packet (network byte order):
    magic(H)=0x5AA5  frame_id(I)  pkt_idx(H)  pkts_in_frame(H)
    send_ts(d)  payload_len(H)  then payload_len bytes.
Packet 0 of each frame carries: tele_len(H) + telemetry JSON + padding.
"""
import argparse
import json
import math
import os
import random
import socket
import struct
import sys
import time

MAGIC = 0x5AA5
HDR = struct.Struct("!HIHHdH")          # 20 bytes
PKT_PAYLOAD = 1200                       # keeps total under a ~1400 MTU
TELE_LEN = struct.Struct("!H")


def _telemetry(robot_id, frame_id):
    """A small synthetic sensor blob — the 'data' the robot is streaming."""
    return json.dumps({
        "robot": robot_id,
        "frame": frame_id,
        "batt_pct": round(max(0.0, 100.0 - frame_id * 0.001), 2),
        "temp_c": round(22.0 + 3.0 * math.sin(frame_id / 90.0), 2),
        "co_ppm": round(5.0 + max(0.0, random.gauss(0, 2)), 1),
        "gps_fix": True,
    }, separators=(",", ":")).encode()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dst", required=True, help="collector IP (host data-net IP)")
    ap.add_argument("--port", type=int, default=5600)
    ap.add_argument("--bind-dev", default=None, help="egress tun, e.g. oaitun_ue1p2")
    ap.add_argument("--src-ip", default=None, help="bind source to the UE IP")
    ap.add_argument("--robot-id", default="ROBOT")
    ap.add_argument("--bitrate-mbps", type=float, default=3.0)
    ap.add_argument("--fps", type=int, default=15)
    ap.add_argument("--duration", type=float, default=0.0, help="0 = run forever")
    args = ap.parse_args()

    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    if args.bind_dev:
        try:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_BINDTODEVICE,
                         (args.bind_dev + "\0").encode())
        except (PermissionError, OSError) as e:
            print(f"[sender] SO_BINDTODEVICE({args.bind_dev}) skipped: {e}",
                  file=sys.stderr)
    if args.src_ip:
        try:
            s.bind((args.src_ip, 0))
        except OSError as e:
            print(f"[sender] bind src {args.src_ip} skipped: {e}", file=sys.stderr)

    dst = (args.dst, args.port)
    bytes_per_frame = int(args.bitrate_mbps * 1e6 / 8 / args.fps)
    pkts_per_frame = max(1, math.ceil(bytes_per_frame / PKT_PAYLOAD))
    frame_period = 1.0 / args.fps

    print(f"[sender] {args.robot_id} -> {args.dst}:{args.port} "
          f"{args.bitrate_mbps} Mbps @ {args.fps} fps "
          f"({pkts_per_frame} pkts/frame, {PKT_PAYLOAD}B each)"
          + (f" via {args.bind_dev}" if args.bind_dev else ""), flush=True)

    frame_id = 0
    t_end = time.time() + args.duration if args.duration > 0 else None
    sent_pkts = 0
    t0 = time.time()
    try:
        while t_end is None or time.time() < t_end:
            frame_start = time.time()
            for pkt_idx in range(pkts_per_frame):
                if pkt_idx == 0:
                    tele = _telemetry(args.robot_id, frame_id)
                    body = TELE_LEN.pack(len(tele)) + tele
                    body += b"\0" * max(0, PKT_PAYLOAD - len(body))
                else:
                    body = b"\0" * PKT_PAYLOAD
                hdr = HDR.pack(MAGIC, frame_id, pkt_idx, pkts_per_frame,
                               time.time(), len(body))
                try:
                    s.sendto(hdr + body, dst)
                    sent_pkts += 1
                except OSError:
                    pass
            frame_id += 1
            # pace to the frame rate
            sleep = frame_period - (time.time() - frame_start)
            if sleep > 0:
                time.sleep(sleep)
    except KeyboardInterrupt:
        pass
    dur = time.time() - t0
    print(f"[sender] done: {frame_id} frames, {sent_pkts} pkts in {dur:.1f}s "
          f"(~{sent_pkts*(HDR.size+PKT_PAYLOAD)*8/1e6/max(dur,1e-3):.2f} Mbps offered)",
          flush=True)


if __name__ == "__main__":
    main()
