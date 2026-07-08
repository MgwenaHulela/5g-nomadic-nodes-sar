#!/usr/bin/env python3
"""
auto_patrol.py — Continuous robot patrol + metrics logger.

Moves ground robots along a path, runs periodic iperf bursts,
and logs distance/ploss/throughput to CSV.

Modes:
  linear (default)    — single alpha robot, out-and-back sweep
  random              — single alpha robot, 2m random walk
  both                — alpha + bravo, independent 2m random walks

Usage:
  python3 scripts/auto_patrol.py                         # linear 10→200m, 180s
  python3 scripts/auto_patrol.py --random                 # random walk, 2m steps
  python3 scripts/auto_patrol.py --both                   # both robots, random
  python3 scripts/auto_patrol.py --duration 300           # 5 min
  python3 scripts/auto_patrol.py --step 2                 # 2m per waypoint

Requires: start_full_stack.sh already running.
"""

import csv
import json
import math
import os
import random
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

LOG_DIR = Path.home() / "sar_sim" / "logs"
PATROL_LOG = LOG_DIR / f"patrol_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"

sys.path.insert(0, str(Path.home() / "sar_sim" / "scripts"))
from metrics_collector import tail_file, parse_ue_metrics

WORLD = "sar_open_field"
UAV_X = 0.0
UAV_Y = 0.0
UAV_Z = 40.0
GROUND_Z = 0.1

ALPHA_MODEL = "sar_ground_alpha"
BRAVO_MODEL = "sar_ground_bravo"
ALPHA_TUN = "oaitun_ue1"
BRAVO_TUN = "oaitun_ue1p2"
ALPHA_IP = "10.0.0.200"
BRAVO_IP = "10.0.0.201"
ALPHA_UE_LOG = "/tmp/ue_alpha.log"
BRAVO_UE_LOG = "/tmp/ue_bravo.log"

IPERF_DURATION = 2
IPERF_INTERVAL = 0

# Random walk bounds (stay within this radius from origin)
MIN_RADIUS = 2
MAX_RADIUS = 150


def gz_set_pose(model, x, y, z):
    req = f'name: "{model}" position: {{x: {x:.1f}, y: {y:.1f}, z: {z:.1f}}}'
    subprocess.run(
        ["gz", "service", "-s", f"/world/{WORLD}/set_pose",
         "--reqtype", "gz.msgs.Pose", "--reptype", "gz.msgs.Boolean",
         "--timeout", "2000", "--req", req],
        capture_output=True, text=True, timeout=5,
    )


def read_bridge_ploss_side(side):
    """Read latest ploss and distance for alpha or bravo from bridge log."""
    log = Path("/tmp/ch_bridge.log")
    if not log.exists():
        return 0.0, 0.0
    lines = log.read_text().strip().splitlines()
    tags = {"alpha": "gnb_ue0_alpha_ul", "bravo": "gnb_ue1_bravo_ul"}
    tag = tags.get(side, "gnb_ue0_alpha_ul")
    for line in reversed(lines):
        m = re.search(rf'{tag}.*?ploss ([\d.]+)dB.*?d=([\d.]+)m', line)
        if m:
            return float(m.group(1)), float(m.group(2))
    return 0.0, 0.0


def run_iperf_tcp(tun, test_ip):
    try:
        out = subprocess.run(
            ["iperf3", "-c", test_ip, "--bind-dev", tun,
             "-t", str(IPERF_DURATION), "-J"],
            capture_output=True, text=True, timeout=IPERF_DURATION + 10,
        ).stdout
        data = json.loads(out)
        mbps = data["end"]["sum_received"]["bits_per_second"] / 1e6
        retrans = data["end"]["sum_sent"]["retransmits"]
        return round(mbps, 2), retrans
    except Exception:
        return 0.0, 0


def run_iperf_udp(tun, test_ip):
    try:
        out = subprocess.run(
            ["iperf3", "-u", "-c", test_ip, "--bind-dev", tun,
             "-b", "100M", "--reverse", "-t", str(IPERF_DURATION), "-J"],
            capture_output=True, text=True, timeout=IPERF_DURATION + 10,
        ).stdout
        data = json.loads(out)
        end = data["end"]
        mbps = end["sum_received"]["bits_per_second"] / 1e6
        jitter = end["sum_received"]["jitter_ms"]
        lost_pct = end["sum_received"]["lost_percent"]
        return round(mbps, 2), round(jitter, 3), round(lost_pct, 2)
    except Exception:
        return 0.0, 0.0, 100.0


def run_ping(tun, test_ip):
    try:
        out = subprocess.run(
            ["ping", "-c", "5", "-i", "0.2", "-I", tun, "-W", "2", test_ip],
            capture_output=True, text=True, timeout=5,
        ).stdout
        n_sent = 5
        m_loss = re.search(r'(\d+) packets transmitted, (\d+) (?:received|packets received)', out)
        n_recv = int(m_loss.group(2)) if m_loss else 0
        loss_pct = round((n_sent - n_recv) / n_sent * 100, 1)
        m_rtt = re.search(r'rtt min/avg/max/mdev = ([\d.]+)/([\d.]+)/([\d.]+)/([\d.]+) ms', out)
        if m_rtt:
            return float(m_rtt.group(1)), float(m_rtt.group(2)), float(m_rtt.group(3)), loss_pct
        return 0.0, 0.0, 0.0, loss_pct
    except Exception:
        return 0.0, 0.0, 0.0, 100.0


def random_step(x, y, step_m, min_r, max_r):
    """Take a 2D random step of step_m, staying within [min_r, max_r] from origin."""
    for _ in range(20):
        angle = random.random() * 2 * math.pi
        nx = x + step_m * math.cos(angle)
        ny = y + step_m * math.sin(angle)
        dist = math.hypot(nx, ny)
        if min_r <= dist <= max_r:
            return nx, ny
    return x, y


def test_one_ue(tun, test_ip, ue_log, seen, prev):
    """Run TCP+UDP+ping for one UE, return (row_values, new_seen, new_prev)."""
    lines, seen = tail_file(ue_log, seen)
    tmp = parse_ue_metrics(lines, prev)
    if tmp.get("ul_bits_per_sym", 0) > 0:
        prev = tmp

    tcp_tp, retrans = run_iperf_tcp(tun, test_ip)
    udp_tp, jitter_ms, udp_lost = run_iperf_udp(tun, test_ip)
    rtt_min, rtt_avg, rtt_max, ping_lost = run_ping(tun, test_ip)

    lines, seen = tail_file(ue_log, seen)
    tmp = parse_ue_metrics(lines, prev)
    if tmp.get("ul_bits_per_sym", 0) > 0:
        prev = tmp

    m = prev
    vals = [tcp_tp, retrans, udp_tp, jitter_ms, udp_lost,
            rtt_min, rtt_avg, rtt_max, ping_lost,
            m.get("pbch_rsrp", ""), m.get("ss_rsrp", ""), m.get("ss_sinr", ""),
            m.get("ul_bits_per_sym", ""), m.get("ul_code_rate", ""),
            m.get("ul_nb_rbs", ""), m.get("ul_nb_sym", ""),
            m.get("dl_harq_ok", ""), m.get("dl_harq_nok", ""),
            m.get("dl_bler", "")]
    return vals, seen, prev


def linear_sweep(start=10, end=200, step=15, duration_s=180):
    t0 = time.time()
    direction = 1
    x = start
    seen_a = 0
    prev_a = {}
    seen_b = 0
    prev_b = {}

    with open(PATROL_LOG, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow([
            "timestamp", "t_elapsed_s", "x_m",
            "ploss_dB", "d_horiz_m",
            "tcp_tp_mbps", "tcp_retransmits",
            "udp_tp_mbps", "udp_jitter_ms", "udp_lost_pct",
            "rtt_min_ms", "rtt_avg_ms", "rtt_max_ms", "ping_loss_pct",
            "alpha_pbch_rsrp", "alpha_ss_rsrp_dBm", "alpha_ss_sinr_dB",
            "alpha_ul_bits_per_sym", "alpha_ul_code_rate",
            "alpha_ul_nb_rbs", "alpha_ul_nb_sym",
            "alpha_dl_harq_ok", "alpha_dl_harq_nok", "alpha_dl_bler",
            "bravo_pbch_rsrp", "bravo_ss_rsrp_dBm", "bravo_ss_sinr_dB",
            "bravo_ul_bits_per_sym", "bravo_ul_code_rate",
            "bravo_ul_nb_rbs", "bravo_ul_nb_sym",
            "bravo_dl_harq_ok", "bravo_dl_harq_nok", "bravo_dl_bler",
        ])

        while time.time() - t0 < duration_s:
            gz_set_pose(ALPHA_MODEL, x, 0, 0.1)
            time.sleep(2)

            now = time.time()
            t_elapsed = now - t0
            ploss_a, d_a = read_bridge_ploss_side("alpha")
            ploss_b, d_b = read_bridge_ploss_side("bravo")

            lines, seen_a = tail_file(ALPHA_UE_LOG, seen_a)
            tmp = parse_ue_metrics(lines, prev_a)
            if tmp.get("ul_bits_per_sym", 0) > 0:
                prev_a = tmp
            lines, seen_b = tail_file(BRAVO_UE_LOG, seen_b)
            tmp = parse_ue_metrics(lines, prev_b)
            if tmp.get("ul_bits_per_sym", 0) > 0:
                prev_b = tmp

            vals_a, seen_a, prev_a = test_one_ue(ALPHA_TUN, ALPHA_IP, ALPHA_UE_LOG, seen_a, prev_a)

            row = [datetime.now(timezone.utc).isoformat(),
                   round(t_elapsed, 1), round(x, 1),
                   round(ploss_a, 2), round(d_a, 1)]
            row += vals_a
            m_b = prev_b
            row += [m_b.get("pbch_rsrp", ""), m_b.get("ss_rsrp", ""), m_b.get("ss_sinr", ""),
                    m_b.get("ul_bits_per_sym", ""), m_b.get("ul_code_rate", ""),
                    m_b.get("ul_nb_rbs", ""), m_b.get("ul_nb_sym", ""),
                    m_b.get("dl_harq_ok", ""), m_b.get("dl_harq_nok", ""),
                    m_b.get("dl_bler", "")]

            w.writerow(row)
            f.flush()
            ss_sinr = prev_a.get("ss_sinr", "")
            ss_rsrp = prev_a.get("ss_rsrp", "")
            se = prev_a.get("ul_bits_per_sym", "")
            print(f"  x={x:.0f}m  ploss={ploss_a:.1f}dB"
                  f"  TCP={vals_a[0]:.1f}Mbps  PingPDR={100-vals_a[8]:.1f}%"
                  f"  RTT={vals_a[6]:.2f}ms"
                  f"  RSRP={ss_rsrp}dBm  SINR={ss_sinr}dB  bits/sym={se}")

            x += direction * step
            if x > end or x < start:
                direction *= -1
                x += direction * step

    print(f"\nSaved: {PATROL_LOG}")


def random_walk_both(step_m=2, duration_s=180):
    t0 = time.time()
    seen_a, seen_b = 0, 0
    prev_a, prev_b = {}, {}
    ax, ay = 10.0, 0.0
    bx, by = 25.0, 0.0
    gz_set_pose(ALPHA_MODEL, ax, ay, 0.1)
    gz_set_pose(BRAVO_MODEL, bx, by, 0.1)
    time.sleep(2)

    with open(PATROL_LOG, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow([
            "timestamp", "t_elapsed_s",
            "uav_x_m", "uav_y_m", "uav_z_m",
            "ax_m", "ay_m", "a_d_horiz_m", "a_d_slant_m", "a_ploss_dB",
            "a_tcp_tp_mbps", "a_tcp_retransmits",
            "a_udp_tp_mbps", "a_udp_jitter_ms", "a_udp_lost_pct",
            "a_rtt_min_ms", "a_rtt_avg_ms", "a_rtt_max_ms", "a_ping_loss_pct",
            "a_pbch_rsrp", "a_ss_rsrp_dBm", "a_ss_sinr_dB",
            "a_ul_bits_per_sym", "a_ul_code_rate",
            "a_ul_nb_rbs", "a_ul_nb_sym",
            "a_dl_harq_ok", "a_dl_harq_nok", "a_dl_bler",
            "bx_m", "by_m", "b_d_horiz_m", "b_d_slant_m", "b_ploss_dB",
            "b_tcp_tp_mbps", "b_tcp_retransmits",
            "b_udp_tp_mbps", "b_udp_jitter_ms", "b_udp_lost_pct",
            "b_rtt_min_ms", "b_rtt_avg_ms", "b_rtt_max_ms", "b_ping_loss_pct",
            "b_pbch_rsrp", "b_ss_rsrp_dBm", "b_ss_sinr_dB",
            "b_ul_bits_per_sym", "b_ul_code_rate",
            "b_ul_nb_rbs", "b_ul_nb_sym",
            "b_dl_harq_ok", "b_dl_harq_nok", "b_dl_bler",
        ])

        while time.time() - t0 < duration_s:
            ax, ay = random_step(ax, ay, step_m, MIN_RADIUS, MAX_RADIUS)
            bx, by = random_step(bx, by, step_m, MIN_RADIUS, MAX_RADIUS)
            gz_set_pose(ALPHA_MODEL, ax, ay, 0.1)
            gz_set_pose(BRAVO_MODEL, bx, by, 0.1)
            time.sleep(2)

            now = time.time()
            t_elapsed = now - t0
            ploss_a, d_a = read_bridge_ploss_side("alpha")
            ploss_b, d_b = read_bridge_ploss_side("bravo")

            vals_a, seen_a, prev_a = test_one_ue(ALPHA_TUN, ALPHA_IP, ALPHA_UE_LOG, seen_a, prev_a)
            vals_b, seen_b, prev_b = test_one_ue(BRAVO_TUN, BRAVO_IP, BRAVO_UE_LOG, seen_b, prev_b)

            dz = abs(UAV_Z - GROUND_Z)
            a_slant = math.hypot(d_a, dz)
            b_slant = math.hypot(d_b, dz)

            row = [datetime.now(timezone.utc).isoformat(),
                   round(t_elapsed, 1),
                   UAV_X, UAV_Y, UAV_Z,
                   round(ax, 1), round(ay, 1), round(d_a, 1), round(a_slant, 1), round(ploss_a, 2)]
            row += vals_a
            row += [round(bx, 1), round(by, 1), round(d_b, 1), round(b_slant, 1), round(ploss_b, 2)]
            row += vals_b

            w.writerow(row)
            f.flush()

            sa = prev_a
            sb = prev_b
            print(f"  U({UAV_X:.0f},{UAV_Y:.0f},{UAV_Z:.0f})")
            print(f"  α({ax:.0f},{ay:.0f}) ploss={ploss_a:.1f}dB  d={a_slant:.0f}m"
                  f"  TCP={vals_a[0]:.1f}M  PingPDR={100-vals_a[8]:.0f}%  RTT={vals_a[6]:.1f}ms"
                  f"  RSRP={sa.get('ss_rsrp','')}dBm  SINR={sa.get('ss_sinr','')}dB"
                  f"  bits/sym={sa.get('ul_bits_per_sym','')}")
            print(f"  β({bx:.0f},{by:.0f}) ploss={ploss_b:.1f}dB  d={b_slant:.0f}m"
                  f"  TCP={vals_b[0]:.1f}M  PingPDR={100-vals_b[8]:.0f}%  RTT={vals_b[6]:.1f}ms"
                  f"  RSRP={sb.get('ss_rsrp','')}dBm  SINR={sb.get('ss_sinr','')}dB"
                  f"  bits/sym={sb.get('ul_bits_per_sym','')}")

    print(f"\nSaved: {PATROL_LOG}")


if __name__ == "__main__":
    mode = "linear"
    duration = 180
    step = 15

    for arg in sys.argv[1:]:
        if arg == "--circle":
            mode = "circle"
        elif arg == "--random":
            mode = "random"
        elif arg == "--both":
            mode = "both"
        elif arg.startswith("--duration="):
            duration = int(arg.split("=")[1])
        elif arg.startswith("--step="):
            step = float(arg.split("=")[1])

    print(f"Patrol mode={mode}, duration={duration}s")
    if mode in ("random", "both"):
        print(f"  random walk, step={step}m, bounds=[{MIN_RADIUS},{MAX_RADIUS}]m")
    print(f"Logging to: {PATROL_LOG}")
    print()

    if mode in ("random", "both"):
        random_walk_both(step_m=step, duration_s=duration)
    else:
        linear_sweep(step=step, duration_s=duration)
