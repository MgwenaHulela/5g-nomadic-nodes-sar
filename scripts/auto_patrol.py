#!/usr/bin/env python3
"""
auto_patrol.py — Continuous robot patrol + metrics logger.

Moves ground robots along a path, runs periodic iperf bursts,
and logs distance/ploss/throughput to CSV.

Modes:
  linear (default)    — single alpha robot, out-and-back sweep
  random              — single alpha robot, 2m random walk
  both                — alpha + bravo, independent 2m random walks
  free                — alpha + bravo roam continuously (smooth headings,
                        no teleport steps); rows tagged with the position
                        at the midpoint of each measurement window

Usage:
  python3 scripts/auto_patrol.py                         # linear 10→200m, 180s
  python3 scripts/auto_patrol.py --random                 # random walk, 2m steps
  python3 scripts/auto_patrol.py --both                   # both robots, random
  python3 scripts/auto_patrol.py --free --speed=1.5       # continuous roam
  python3 scripts/auto_patrol.py --duration=300           # 5 min
  python3 scripts/auto_patrol.py --step=2                 # 2m per waypoint

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
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

LOG_DIR = Path.home() / "sar_sim" / "logs"
PATROL_LOG = LOG_DIR / f"patrol_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"

sys.path.insert(0, str(Path.home() / "sar_sim" / "scripts"))
from metrics_collector import tail_file, parse_ue_metrics

sys.path.insert(0, "/usr/lib/python3/dist-packages")
from gz.transport13 import Node as GzNode
from gz.msgs10.pose_pb2 import Pose
from gz.msgs10.boolean_pb2 import Boolean
from gz.msgs10.twist_pb2 import Twist
from gz.msgs10.pose_v_pb2 import Pose_V

WORLD = "sar_open_field"
UAV_X = 0.0
UAV_Y = 0.0
UAV_Z = 40.0
GROUND_Z = 0.0  # burger base_footprint is its ground-contact point — 0.1 floats it
TX_REF = -11.0       # calibrated: RSRP_observed + ploss_DL at close range
DL_UL_OFFSET = 5.0   # DL baseline is 5 dB below UL baseline (from bridge config)

ALPHA_MODEL = "sar_ground_alpha"
BRAVO_MODEL = "sar_ground_bravo"
ALPHA_TUN = "oaitun_ue1"
BRAVO_TUN = "oaitun_ue1p2"
ALPHA_IP = "10.0.0.200"
BRAVO_IP = "10.0.0.201"

# Per-robot network namespaces (setup_robot_netns.sh). When active, the UE
# TUNs live inside these namespaces, so uplink tests must run there — and
# the UE IPs are reachable from the root ns ONLY over the air, which is
# what makes the C2/downlink metric honest.
NS_ALPHA = "robot_alpha"
NS_BRAVO = "robot_bravo"
ALPHA_UE_IP = "10.45.0.2"
BRAVO_UE_IP = "10.45.0.3"
NETNS_MODE = Path("/var/run/netns/robot_alpha").exists()
SUDO_PASS = "Makhubela1"  # same convention as restart_ran.sh / start_full_stack.sh


def ns_run(ns, cmd, timeout):
    """Run cmd inside a robot namespace (or directly if netns not set up)."""
    if NETNS_MODE:
        return subprocess.run(
            ["sudo", "-S", "-p", "", "ip", "netns", "exec", ns] + cmd,
            capture_output=True, text=True, timeout=timeout,
            input=SUDO_PASS + "\n",
        )
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
ALPHA_UE_LOG = "/tmp/ue_alpha.log"
BRAVO_UE_LOG = "/tmp/ue_bravo.log"

IPERF_DURATION = 2
IPERF_INTERVAL = 0

# Simulated sensor/video uplink feed rate (~1080p camera stream). Offered
# over the air UE->network; at close range it should arrive nearly intact,
# at range the loss%/jitter columns become the video-QoS degradation signal.
VIDEO_BITRATE_MBPS = 8

# Roam bounds (radius from origin). Overridable via --min-radius= /
# --max-radius=. Coverage edges measured 2026-07-10: alpha (baseline
# 20dB) dies ~135m, bravo (baseline 25dB) ~130m — and RLF is sticky:
# walking back does NOT quickly restore the link, and the UE may never
# re-attach without a RAN restart.
MIN_RADIUS = 2
MAX_RADIUS = 150


# In-process gz-transport node for set_pose: a `gz service` subprocess
# costs 100-200ms per call, which capped pose updates at ~2Hz and made
# robot motion look like discrete point-to-point jumps in the GUI.
# Direct requests are ~1ms, allowing a smooth 10Hz motion tick.
_gz_node = None


def gz_set_pose(model, x, y, z):
    global _gz_node
    if _gz_node is None:
        _gz_node = GzNode()
    req = Pose()
    req.name = model
    req.position.x = x
    req.position.y = y
    req.position.z = z
    try:
        _gz_node.request(f"/world/{WORLD}/set_pose", req, Pose, Boolean, 500)
    except Exception:
        pass


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


def _iperf_json(ns, args, timeout):
    """Run iperf3, retrying once on failure. A test killed by our
    subprocess timeout leaves the iperf3 SERVER stuck mid-test, and the
    next connection is rejected instantly ('server busy') — in the
    2026-07-10 90-min run this cascaded into 19% of samples logging
    tcp=0 on a perfectly live link. One retry after a settle beat
    absorbs it."""
    for attempt in (0, 1):
        try:
            out = ns_run(ns, args, timeout=timeout).stdout
            data = json.loads(out)
            if "error" not in data:
                return data
        except Exception:
            pass
        time.sleep(2.5)
    return None


def run_iperf_tcp(tun, test_ip, ns=NS_ALPHA):
    # -O 1: run 1 extra warm-up second and exclude it from the stats, so
    # the reported rate is post-slow-start steady state rather than the
    # ramp (a bare 2s test under-reports a ~20Mbps link as ~5-6Mbps).
    data = _iperf_json(ns,
        ["iperf3", "-c", test_ip, "--bind-dev", tun,
         "-t", str(IPERF_DURATION), "-O", "1", "-J"],
        timeout=IPERF_DURATION + 11)
    try:
        mbps = data["end"]["sum_received"]["bits_per_second"] / 1e6
        retrans = data["end"]["sum_sent"]["retransmits"]
        return round(mbps, 2), retrans
    except Exception:
        return 0.0, 0


def run_iperf_udp(tun, test_ip, ns=NS_ALPHA):
    """Fixed-bitrate UDP UPLINK stream (UE -> network), modelling the
    robot's live video/sensor feed. The old --reverse (downlink) version
    never crossed the air interface — both tunnel IPs are local to this
    host, so reverse traffic short-circuited via loopback and logged the
    offered load (~100Mbps) regardless of geometry.

    Returns (delivered_mbps, jitter_ms, lost_pct) as measured server-side,
    i.e. after the radio link.
    """
    data = _iperf_json(ns,
        ["iperf3", "-u", "-c", test_ip, "--bind-dev", tun,
         "-b", f"{VIDEO_BITRATE_MBPS}M", "-t", str(IPERF_DURATION), "-J"],
        timeout=IPERF_DURATION + 10)
    try:
        end = data["end"]
        s = end.get("sum") or end.get("sum_received") or {}
        jitter = s["jitter_ms"]
        lost_pct = s["lost_percent"]
        delivered = s["bits_per_second"] / 1e6 * (1.0 - lost_pct / 100.0)
        return round(delivered, 2), round(jitter, 3), round(lost_pct, 2)
    except Exception:
        return 0.0, 0.0, 100.0


def run_ping(tun, test_ip, ns=NS_ALPHA):
    try:
        out = ns_run(ns,
            ["ping", "-c", "2", "-I", tun, "-W", "2", test_ip],
            timeout=8,
        ).stdout
        n_sent = 2
        m_loss = re.search(r'(\d+) packets transmitted, (\d+) (?:received|packets received)', out)
        n_recv = int(m_loss.group(2)) if m_loss else 0
        loss_pct = round((n_sent - n_recv) / n_sent * 100, 1)
        m_rtt = re.search(r'rtt min/avg/max/mdev = ([\d.]+)/([\d.]+)/([\d.]+)/([\d.]+) ms', out)
        if m_rtt:
            return float(m_rtt.group(1)), float(m_rtt.group(2)), float(m_rtt.group(3)), loss_pct
        return 0.0, 0.0, 0.0, loss_pct
    except Exception:
        return 0.0, 0.0, 0.0, 100.0


def run_c2(ue_ip):
    """Command-and-control downlink probe: small packets base -> robot,
    like waypoint updates or an e-stop. Sent from the root namespace to
    the robot's UE IP, so (with netns active) the round trip genuinely
    crosses the air in both directions. Returns (rtt_avg_ms, loss_pct).
    """
    if not NETNS_MODE:
        return 0.0, 100.0  # without netns the DL path is loopback — no honest measurement
    try:
        out = subprocess.run(
            ["ping", "-c", "5", "-i", "0.2", "-W", "1", "-s", "64", ue_ip],
            capture_output=True, text=True, timeout=8,
        ).stdout
        m_loss = re.search(r'(\d+) packets transmitted, (\d+) (?:received|packets received)', out)
        n_recv = int(m_loss.group(2)) if m_loss else 0
        loss_pct = round((5 - n_recv) / 5 * 100, 1)
        m_rtt = re.search(r'rtt min/avg/max/mdev = [\d.]+/([\d.]+)/', out)
        rtt_avg = float(m_rtt.group(1)) if m_rtt else 0.0
        return rtt_avg, loss_pct
    except Exception:
        return 0.0, 100.0


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


def test_one_ue(tun, test_ip, ue_log, seen, prev, ns=NS_ALPHA):
    """Run TCP+UDP+ping for one UE, return (row_values, new_seen, new_prev)."""
    lines, seen = tail_file(ue_log, seen)
    tmp = parse_ue_metrics(lines, prev)
    if tmp.get("ul_bits_per_sym", 0) > 0:
        prev = tmp

    tcp_tp, retrans = run_iperf_tcp(tun, test_ip, ns)
    udp_tp, jitter_ms, udp_lost = run_iperf_udp(tun, test_ip, ns)
    rtt_min, rtt_avg, rtt_max, ping_lost = run_ping(tun, test_ip, ns)

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
            "rtt_min_ms", "rtt_avg_ms", "rtt_max_ms",             "ping_loss_pct",
            "alpha_pbch_rsrp", "alpha_ss_rsrp_dBm", "alpha_ss_sinr_dB",
            "alpha_ul_bits_per_sym", "alpha_ul_code_rate",
            "alpha_ul_nb_rbs", "alpha_ul_nb_sym",
            "alpha_dl_harq_ok", "alpha_dl_harq_nok", "alpha_dl_bler", "alpha_rsrp_computed_dBm",
            "bravo_pbch_rsrp", "bravo_ss_rsrp_dBm", "bravo_ss_sinr_dB",
            "bravo_ul_bits_per_sym", "bravo_ul_code_rate",
            "bravo_ul_nb_rbs", "bravo_ul_nb_sym",
            "bravo_dl_harq_ok", "bravo_dl_harq_nok", "bravo_dl_bler", "bravo_rsrp_computed_dBm",
        ])

        while time.time() - t0 < duration_s:
            gz_set_pose(ALPHA_MODEL, x, 0, GROUND_Z)
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

            vals_a, seen_a, prev_a = test_one_ue(ALPHA_TUN, ALPHA_IP, ALPHA_UE_LOG, seen_a, prev_a, NS_ALPHA)

            a_rsrp_c = round(TX_REF - (ploss_a - DL_UL_OFFSET), 1)

            row = [datetime.now(timezone.utc).isoformat(),
                   round(t_elapsed, 1), round(x, 1),
                   round(ploss_a, 2), round(d_a, 1)]
            row += vals_a + [a_rsrp_c]
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
    gz_set_pose(ALPHA_MODEL, ax, ay, GROUND_Z)
    gz_set_pose(BRAVO_MODEL, bx, by, GROUND_Z)
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
            "a_dl_harq_ok", "a_dl_harq_nok", "a_dl_bler", "a_rsrp_computed_dBm",
            "bx_m", "by_m", "b_d_horiz_m", "b_d_slant_m", "b_ploss_dB",
            "b_tcp_tp_mbps", "b_tcp_retransmits",
            "b_udp_tp_mbps", "b_udp_jitter_ms", "b_udp_lost_pct",
            "b_rtt_min_ms", "b_rtt_avg_ms", "b_rtt_max_ms", "b_ping_loss_pct",
            "b_pbch_rsrp", "b_ss_rsrp_dBm", "b_ss_sinr_dB",
            "b_ul_bits_per_sym", "b_ul_code_rate",
            "b_ul_nb_rbs", "b_ul_nb_sym",
            "b_dl_harq_ok", "b_dl_harq_nok", "b_dl_bler", "b_rsrp_computed_dBm",
            "a_c2_rtt_ms", "a_c2_loss_pct", "b_c2_rtt_ms", "b_c2_loss_pct",
        ])

        while time.time() - t0 < duration_s:
            ax, ay = random_step(ax, ay, step_m, MIN_RADIUS, MAX_RADIUS)
            bx, by = random_step(bx, by, step_m, MIN_RADIUS, MAX_RADIUS)
            gz_set_pose(ALPHA_MODEL, ax, ay, GROUND_Z)
            gz_set_pose(BRAVO_MODEL, bx, by, GROUND_Z)
            time.sleep(2)

            now = time.time()
            t_elapsed = now - t0
            ploss_a, d_a = read_bridge_ploss_side("alpha")
            ploss_b, d_b = read_bridge_ploss_side("bravo")

            vals_a, seen_a, prev_a = test_one_ue(ALPHA_TUN, ALPHA_IP, ALPHA_UE_LOG, seen_a, prev_a, NS_ALPHA)
            vals_b, seen_b, prev_b = test_one_ue(BRAVO_TUN, BRAVO_IP, BRAVO_UE_LOG, seen_b, prev_b, NS_BRAVO)
            a_c2_rtt, a_c2_loss = run_c2(ALPHA_UE_IP)
            b_c2_rtt, b_c2_loss = run_c2(BRAVO_UE_IP)

            dz = abs(UAV_Z - GROUND_Z)
            a_slant = math.hypot(d_a, dz)
            b_slant = math.hypot(d_b, dz)
            a_rsrp_c = round(TX_REF - (ploss_a - DL_UL_OFFSET), 1)
            b_rsrp_c = round(TX_REF - (ploss_b - DL_UL_OFFSET), 1)

            row = [datetime.now(timezone.utc).isoformat(),
                   round(t_elapsed, 1),
                   UAV_X, UAV_Y, UAV_Z,
                   round(ax, 1), round(ay, 1), round(d_a, 1), round(a_slant, 1), round(ploss_a, 2)]
            row += vals_a + [a_rsrp_c]
            row += [round(bx, 1), round(by, 1), round(d_b, 1), round(b_slant, 1), round(ploss_b, 2)]
            row += vals_b + [b_rsrp_c]
            row += [a_c2_rtt, a_c2_loss, b_c2_rtt, b_c2_loss]

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


def free_roam_both(speed_ms=1.0, duration_s=180, tick_s=0.1):
    """Both robots DRIVE continuously and independently — DiffDrive
    cmd_vel with pose feedback and heading-error steering, so wheels
    genuinely spin and motion is kinematic, not teleportation. Metrics
    are sampled in parallel; each CSV row is tagged with the robot's
    position at the midpoint of its measurement window (an iperf+ping
    cycle spans ~8-10s of travel).

    Fine-grained ploss-vs-position at every 0.5s tick is already captured
    by channel_bridge_log.csv; this CSV adds the expensive link metrics.
    """
    global _gz_node
    if _gz_node is None:
        _gz_node = GzNode()

    # live pose feedback (position + yaw) from the world's pose topic
    fb_lock = threading.Lock()
    feedback = {}  # model -> (x, y, yaw)

    def pose_cb(msg):
        for p in msg.pose:
            if p.name in (ALPHA_MODEL, BRAVO_MODEL):
                q = p.orientation
                yaw = math.atan2(2.0 * (q.w * q.z + q.x * q.y),
                                 1.0 - 2.0 * (q.y * q.y + q.z * q.z))
                with fb_lock:
                    feedback[p.name] = (p.position.x, p.position.y, yaw)

    _gz_node.subscribe(Pose_V, f"/world/{WORLD}/pose/info", pose_cb)

    pubs = {
        ALPHA_MODEL: _gz_node.advertise(f"/model/{ALPHA_MODEL}/cmd_vel", Twist()),
        BRAVO_MODEL: _gz_node.advertise(f"/model/{BRAVO_MODEL}/cmd_vel", Twist()),
    }

    def current_pos(model, default):
        with fb_lock:
            fb = feedback.get(model)
        return (fb[0], fb[1]) if fb else default

    # one-time placement at start positions (teleport is fine for setup)
    gz_set_pose(ALPHA_MODEL, 10.0, 0.0, GROUND_Z)
    gz_set_pose(BRAVO_MODEL, 25.0, 0.0, GROUND_Z)
    time.sleep(2)

    running = threading.Event()
    running.set()
    # wander heading target per robot; the driver steers toward it
    targets = {
        ALPHA_MODEL: random.random() * 2 * math.pi,
        BRAVO_MODEL: random.random() * 2 * math.pi,
    }

    def driver():
        while running.is_set():
            for model in (ALPHA_MODEL, BRAVO_MODEL):
                with fb_lock:
                    fb = feedback.get(model)
                if fb is None:
                    continue
                x, y, yaw = fb
                # wander: drift the target heading; steer back inside the
                # allowed annulus near its edges
                tgt = targets[model] + random.gauss(0.0, 0.5 * tick_s)
                r = math.hypot(x, y)
                if r > MAX_RADIUS - 5:
                    tgt = math.atan2(-y, -x)
                elif r < MIN_RADIUS + 1:
                    tgt = math.atan2(y, x)
                targets[model] = tgt

                err = math.atan2(math.sin(tgt - yaw), math.cos(tgt - yaw))
                tw = Twist()
                tw.angular.z = max(-1.5, min(1.5, 2.0 * err))
                # slow down through sharp turns, never fully stop
                tw.linear.x = speed_ms * max(0.15, math.cos(err))
                try:
                    pubs[model].publish(tw)
                except Exception:
                    pass
            time.sleep(tick_s)

    threading.Thread(target=driver, daemon=True).start()

    t0 = time.time()
    seen_a, seen_b = 0, 0
    prev_a, prev_b = {}, {}

    with open(PATROL_LOG, "w", newline="") as f:
        w = csv.writer(f)
        # Same schema as random_walk_both so downstream tooling is unaffected
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
            "a_dl_harq_ok", "a_dl_harq_nok", "a_dl_bler", "a_rsrp_computed_dBm",
            "bx_m", "by_m", "b_d_horiz_m", "b_d_slant_m", "b_ploss_dB",
            "b_tcp_tp_mbps", "b_tcp_retransmits",
            "b_udp_tp_mbps", "b_udp_jitter_ms", "b_udp_lost_pct",
            "b_rtt_min_ms", "b_rtt_avg_ms", "b_rtt_max_ms", "b_ping_loss_pct",
            "b_pbch_rsrp", "b_ss_rsrp_dBm", "b_ss_sinr_dB",
            "b_ul_bits_per_sym", "b_ul_code_rate",
            "b_ul_nb_rbs", "b_ul_nb_sym",
            "b_dl_harq_ok", "b_dl_harq_nok", "b_dl_bler", "b_rsrp_computed_dBm",
            "a_c2_rtt_ms", "a_c2_loss_pct", "b_c2_rtt_ms", "b_c2_loss_pct",
        ])

        try:
            while time.time() - t0 < duration_s:
                a_start = current_pos(ALPHA_MODEL, (10.0, 0.0))
                b_start = current_pos(BRAVO_MODEL, (25.0, 0.0))

                vals_a, seen_a, prev_a = test_one_ue(ALPHA_TUN, ALPHA_IP, ALPHA_UE_LOG, seen_a, prev_a, NS_ALPHA)
                time.sleep(2)  # let the iperf servers + gNB queues idle between robots
                vals_b, seen_b, prev_b = test_one_ue(BRAVO_TUN, BRAVO_IP, BRAVO_UE_LOG, seen_b, prev_b, NS_BRAVO)
                time.sleep(2)  # C2 probes should measure the channel, not draining test queues
                a_c2_rtt, a_c2_loss = run_c2(ALPHA_UE_IP)
                b_c2_rtt, b_c2_loss = run_c2(BRAVO_UE_IP)

                a_end = current_pos(ALPHA_MODEL, a_start)
                b_end = current_pos(BRAVO_MODEL, b_start)

                # Midpoint of the measurement window = best single-point tag
                ax = (a_start[0] + a_end[0]) / 2
                ay = (a_start[1] + a_end[1]) / 2
                bx = (b_start[0] + b_end[0]) / 2
                by = (b_start[1] + b_end[1]) / 2

                t_elapsed = time.time() - t0
                ploss_a, _ = read_bridge_ploss_side("alpha")
                ploss_b, _ = read_bridge_ploss_side("bravo")
                d_a = math.hypot(ax - UAV_X, ay - UAV_Y)
                d_b = math.hypot(bx - UAV_X, by - UAV_Y)
                dz = abs(UAV_Z - GROUND_Z)
                a_slant = math.hypot(d_a, dz)
                b_slant = math.hypot(d_b, dz)
                a_rsrp_c = round(TX_REF - (ploss_a - DL_UL_OFFSET), 1)
                b_rsrp_c = round(TX_REF - (ploss_b - DL_UL_OFFSET), 1)

                row = [datetime.now(timezone.utc).isoformat(),
                       round(t_elapsed, 1),
                       UAV_X, UAV_Y, UAV_Z,
                       round(ax, 1), round(ay, 1), round(d_a, 1), round(a_slant, 1), round(ploss_a, 2)]
                row += vals_a + [a_rsrp_c]
                row += [round(bx, 1), round(by, 1), round(d_b, 1), round(b_slant, 1), round(ploss_b, 2)]
                row += vals_b + [b_rsrp_c]
                row += [a_c2_rtt, a_c2_loss, b_c2_rtt, b_c2_loss]

                w.writerow(row)
                f.flush()

                print(f"  α({ax:.0f},{ay:.0f}) d={d_a:.0f}m ploss={ploss_a:.1f}dB"
                      f"  TCP={vals_a[0]:.1f}M  PingPDR={100-vals_a[8]:.0f}%  RTT={vals_a[6]:.1f}ms"
                      f"  C2={a_c2_rtt:.1f}ms/{a_c2_loss:.0f}%loss")
                print(f"  β({bx:.0f},{by:.0f}) d={d_b:.0f}m ploss={ploss_b:.1f}dB"
                      f"  TCP={vals_b[0]:.1f}M  PingPDR={100-vals_b[8]:.0f}%  RTT={vals_b[6]:.1f}ms"
                      f"  C2={b_c2_rtt:.1f}ms/{b_c2_loss:.0f}%loss")
        finally:
            running.clear()
            # DiffDrive latches the last command — stop the wheels
            time.sleep(tick_s * 2)
            stop = Twist()
            for model in (ALPHA_MODEL, BRAVO_MODEL):
                try:
                    pubs[model].publish(stop)
                except Exception:
                    pass

    print(f"\nSaved: {PATROL_LOG}")


if __name__ == "__main__":
    mode = "linear"
    duration = 180
    step = 15
    speed = 1.0

    for arg in sys.argv[1:]:
        if arg == "--circle":
            mode = "circle"
        elif arg == "--random":
            mode = "random"
        elif arg == "--both":
            mode = "both"
        elif arg == "--free":
            mode = "free"
        elif arg.startswith("--duration="):
            duration = int(arg.split("=")[1])
        elif arg.startswith("--step="):
            step = float(arg.split("=")[1])
        elif arg.startswith("--speed="):
            speed = float(arg.split("=")[1])
        elif arg.startswith("--max-radius="):
            # <=110m: survivable long runs (bravo stays under ~39dB).
            # 150m: deliberately crosses the coverage edge — labeled
            # outage data, but expect a UE death + RAN restart per run.
            MAX_RADIUS = float(arg.split("=")[1])
        elif arg.startswith("--min-radius="):
            MIN_RADIUS = float(arg.split("=")[1])
        else:
            # fail loudly: silently ignoring e.g. "-duration=300" (one
            # dash) ran the 180s default and wasted a session
            sys.exit(f"ERROR: unrecognized argument '{arg}' — valid: "
                     f"--free --random --both --circle --duration=N "
                     f"--step=N --speed=N --max-radius=N --min-radius=N")

    print(f"Patrol mode={mode}, duration={duration}s")
    if mode in ("random", "both"):
        print(f"  random walk, step={step}m, bounds=[{MIN_RADIUS},{MAX_RADIUS}]m")
    elif mode == "free":
        print(f"  free roam, speed={speed}m/s, bounds=[{MIN_RADIUS},{MAX_RADIUS}]m")
    print(f"Logging to: {PATROL_LOG}")
    print()

    if mode in ("random", "both"):
        random_walk_both(step_m=step, duration_s=duration)
    elif mode == "free":
        free_roam_both(speed_ms=speed, duration_s=duration)
    else:
        linear_sweep(step=step, duration_s=duration)
