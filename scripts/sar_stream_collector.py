#!/usr/bin/env python3
"""sar_stream_collector.py — receives a SAR robot's 5G video+telemetry
stream and reports REAL application-level QoS.

Runs on the host (default namespace), bound to the data-network IP the
robot streams to (the same IPs iperf uses: 10.0.0.200 alpha / 10.0.0.201
bravo):

    python3 scripts/sar_stream_collector.py --bind 10.0.0.201 --robot-id BRAVO

Because sender and collector share this host's wall clock, one-way latency
is measured directly from the packet's send timestamp. Every second it
prints — and optionally logs to CSV — the metrics that tell the streaming
story as the robot moves through the urban world:

    goodput (Mbps)        bytes actually delivered
    frame delivery (%)    frames with ALL packets received / frames sent
    packet loss (%)       from per-frame sequence gaps
    latency / jitter (ms) send->recv delay and its variation

At LoS these sit near 100% / full bitrate / low latency; behind buildings
(NLoS) frame delivery and goodput fall and latency/jitter climb — the same
degradation the channel model injects into the RAN.
"""
import argparse
import csv
import socket
import statistics
import struct
import time
from datetime import datetime, timezone

MAGIC = 0x5AA5
HDR = struct.Struct("!HIHHdH")          # magic frame_id pkt_idx pkts_in_frame send_ts payload_len
TELE_LEN = struct.Struct("!H")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bind", default="0.0.0.0", help="host data-net IP to listen on")
    ap.add_argument("--port", type=int, default=5600)
    ap.add_argument("--robot-id", default="ROBOT")
    ap.add_argument("--csv", default=None, help="path to append per-second metrics")
    ap.add_argument("--duration", type=float, default=0.0, help="0 = run until Ctrl-C")
    args = ap.parse_args()

    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4 << 20)
    s.bind((args.bind, args.port))
    s.settimeout(0.5)
    print(f"[collector] {args.robot_id} listening on {args.bind}:{args.port}",
          flush=True)

    writer = None
    if args.csv:
        new = not __import__("os").path.exists(args.csv)
        cf = open(args.csv, "a", newline="")
        writer = csv.writer(cf)
        if new:
            writer.writerow(["timestamp", "robot", "goodput_mbps",
                             "frame_delivery_pct", "pkt_loss_pct",
                             "latency_ms", "jitter_ms",
                             "frames_sent", "frames_delivered", "last_telemetry"])

    # per-window accumulators
    frames = {}            # frame_id -> [received_pkts, pkts_in_frame]
    win_bytes = 0
    lat_samples = []
    last_tele = ""
    win_start = time.time()
    t_end = time.time() + args.duration if args.duration > 0 else None

    def flush_window():
        nonlocal win_bytes, lat_samples
        now = time.time()
        span = now - win_start
        goodput = win_bytes * 8 / 1e6 / span if span > 0 else 0.0
        lat = statistics.mean(lat_samples) * 1000 if lat_samples else float("nan")
        jit = statistics.pstdev(lat_samples) * 1000 if len(lat_samples) > 1 else 0.0
        # Only evaluate frames the sender has moved past (settled); keep the
        # newest, still-arriving frame for the next window so a frame that
        # straddles the 1 s boundary isn't miscounted as partially lost.
        if frames:
            max_fid = max(frames)
            settled = sorted(fid for fid in frames if fid < max_fid)
        else:
            settled = []
        if settled:
            pif = frames[settled[0]][1]                    # constant bitrate
            frames_sent = settled[-1] - settled[0] + 1     # includes lost frame_ids
            delivered = sum(1 for fid in settled
                            if frames[fid][0] >= frames[fid][1])
            exp = frames_sent * pif
            got = sum(min(frames[fid][0], frames[fid][1]) for fid in settled)
            loss = 100.0 * (1 - got / exp) if exp else 0.0
            delivery = 100.0 * delivered / frames_sent if frames_sent else 0.0
            for fid in settled:
                del frames[fid]
        else:
            frames_sent = delivered = 0
            loss = delivery = 0.0
        ts = datetime.now(timezone.utc).isoformat()
        print(f"[{args.robot_id}] goodput={goodput:5.2f}Mbps  "
              f"frames={delivered}/{frames_sent} ({delivery:5.1f}%)  "
              f"pktloss={loss:5.1f}%  lat={lat:6.1f}ms  jit={jit:5.1f}ms",
              flush=True)
        if writer:
            writer.writerow([ts, args.robot_id, f"{goodput:.3f}",
                             f"{delivery:.1f}", f"{loss:.1f}",
                             f"{lat:.1f}" if lat == lat else "",
                             f"{jit:.1f}", frames_sent, delivered, last_tele])
            cf.flush()
        win_bytes = 0
        lat_samples = []

    try:
        while t_end is None or time.time() < t_end:
            try:
                data, _ = s.recvfrom(2048)
            except socket.timeout:
                data = None
            if data and len(data) >= HDR.size:
                magic, fid, pidx, pif, send_ts, plen = HDR.unpack_from(data)
                if magic == MAGIC:
                    rec = frames.setdefault(fid, [0, pif])
                    rec[0] += 1
                    win_bytes += len(data)
                    lat_samples.append(max(0.0, time.time() - send_ts))
                    if pidx == 0:
                        body = data[HDR.size:]
                        if len(body) >= TELE_LEN.size:
                            (tl,) = TELE_LEN.unpack_from(body)
                            if 0 < tl <= len(body) - TELE_LEN.size:
                                last_tele = body[TELE_LEN.size:TELE_LEN.size + tl].decode("utf-8", "replace")
            if time.time() - win_start >= 1.0:
                flush_window()
                win_start = time.time()
    except KeyboardInterrupt:
        pass
    print(f"[collector] {args.robot_id} stopped", flush=True)


if __name__ == "__main__":
    main()
