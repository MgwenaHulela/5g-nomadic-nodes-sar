#!/usr/bin/env python3
"""
traffic_streams.py — persistent per-robot traffic, sampled instead of probed.

WHY THIS REPLACES PER-CYCLE iperf
---------------------------------
The patrol used to launch a fresh `iperf3 -t 2 -O 1` per robot per cycle and
record the result as that robot's throughput. Measured 2026-10-20, that is one
second of TCP after a one-second warm-up — and TCP is still in slow start:

    s1 4.2   s2 7.3   s3 12.9 Mb/s ... still climbing

So every throughput figure was a point on the ramp, not a rate. Four robots
with genuinely different link quality all reported an identical ~2.9 Mb/s
because they were all sampling the same instant of the same ramp. The radio
was never the problem: zero HARQ failures, MCS spreading normally, 0% ping
loss throughout.

A long-lived flow keeps its congestion window open, so reading its current
rate costs nothing and reflects steady state. That also removes, in one
change, four failure modes the per-cycle approach kept producing:

  * slow-start under-reporting (above)
  * iperf servers left wedged by a timed-out test, so the next cycle's probe
    fails instantly and logs a false zero
  * orphaned iperf3 processes when a probe times out (the client outlives the
    killed sudo wrapper and keeps loading the link)
  * 250-second measurement cycles on slow links, which made a row a blend of
    moments minutes apart rather than a snapshot

And it is closer to the real workload: the brief describes these columns as
"TCP goodput (sensor upload)" and "UDP video stream". A SAR robot streams
continuously; it does not sit idle and burst for two seconds. Continuous
load also makes the four robots genuinely contend for the cell, which is the
regime the "keep every robot connected" objective is actually about.

WHAT IT RUNS
------------
Per robot, two long-lived flows bound to that robot's TUN inside its netns:

  TCP  rate-limited to TCP_TARGET_MBPS — a sensor/video uploader with a
       bitrate goal. The useful signal is the SHORTFALL against that target
       when the link degrades, not "how fast can this robot flood the cell".
       An unlimited flow would simply saturate and report a fair share,
       which says more about the scheduler than about the robot's service.
  UDP  constant bitrate at UDP_TARGET_MBPS — a video stream, giving loss and
       jitter against a fixed offered rate the way real video does.

Each writes `-i 1 --forceflush` interval lines to its own log; sampling is a
tail-read of the last few lines.

USAGE
-----
    from traffic_streams import TrafficStreams
    ts = TrafficStreams(["alpha", "bravo"])
    ts.start()
    ...
    tcp_mbps, retrans = ts.tcp_sample("alpha")
    udp_mbps, jitter, loss = ts.udp_sample("alpha")
    ts.stop()

Standalone, for a quick look:
    python3 scripts/traffic_streams.py --seconds 20
"""

import os
import re
import signal
import subprocess
import time
from pathlib import Path

# Offered load per robot. These are targets, not caps on what the link could
# do: the measurement that matters is how much of the target actually lands.
#
# SIZED FROM MEASURED CAPACITY (2026-10-20), not guessed. The first attempt
# used 12 Mb/s TCP + 8 Mb/s UDP per robot: four robots then offered 80 Mb/s
# into a cell that delivers ~13, and everything collapsed into congestion —
# UDP does not back off so it crowded TCP out to 0.00 Mb/s, and C2 was lost
# on EVERY cycle because the pings could not get through the queue. Under
# those conditions throughput measures contention, not link quality, which
# destroys exactly the signal the forecaster needs.
#
# At ~2 Mb/s per robot the four together offer ~8 Mb/s, about 60% of cell
# capacity. That leaves headroom for C2, and means a shortfall against target
# is caused by the LINK degrading rather than by the queue — which is the
# whole point of logging it. Both are also realistic for a SAR robot:
# low-rate telemetry video plus sensor upload.
# MEASURED 2026-10-20 on this host, uncapped:
#   single robot            ~19-21 Mb/s
#   four robots concurrent  ~19 Mb/s AGGREGATE -> ~4.7 Mb/s fair share each
# (Both figures DOUBLED on 2026-10-20 when do_CSIRS was enabled in the gNB
#  config. With CSI-RS off the UE reported CQI 0 on every report, the gNB
#  set dl_max_mcs from that, and MCS was pinned at 0-5. Enabling it lifted
#  MCS to 28. The remaining ceiling is rfsimulator's CPU-bound PHY.)
# Capacity does not scale with UEs; it is a fixed ceiling being divided.
# rfsimulator software-simulates the PHY and is CPU-bound, so this ceiling is
# a property of the TESTBED, not of the n78/106-PRB configuration it emulates.
# Absolute Mb/s here should be reported as simulator-bound, and comparisons
# made on FRACTION OF TARGET ACHIEVED rather than raw rate.
#
# Targets sit at ~66% of the measured fair share so a healthy robot comfortably
# meets them: shortfall then means the LINK degraded, not that the cell was
# already saturated. The previous 1.2+0.8 offered 8.0 Mb/s into an 8.5 Mb/s
# cell (~94% load), which is why C2 RTT ran ~200 ms. Re-sized after the
# CSI-RS fix: 2.8 Mb/s per robot is ~60% of the new 4.7 Mb/s fair share.
# 2.5 Mb/s per robot (1.8 TCP + 0.7 UDP). CALIBRATED 2026-10-21 by running
# the real excitation policy at three rates, each with FRESH iperf3 servers
# (a wedged server silently empties the throughput column — see
# setup_over_air_test.sh) and the 10-packet C2 ping:
#
#   rate   C2 avail   TCP across distance bands   corr(dist,TCP)
#   1.5     90%       1.02 1.00 1.01 0.95  FLAT      ~0
#   2.5     71%       1.89 1.72 1.62 1.19          -0.408
#   3.5     41%       2.67 2.34 1.74 1.09          -0.577
#
# This is a genuine trade-off, not a free choice. Too low and throughput is
# pinned at the cap everywhere, so it carries NO information about the link
# and UAV motion is invisible in it. Too high and the cell saturates
# whenever the UAV roams: C2 availability collapses to 41% (7-11% at long
# range), the "keep every robot connected" objective is saturated at
# DISCONNECTED, and the planner has no headroom to show improvement.
#
# 2.5 keeps BOTH signals alive: C2 grades 95/79/59/41% across distance —
# wide dynamic range for the label — while throughput still tracks the link.
TCP_TARGET_MBPS = float(os.environ.get("SAR_TCP_TARGET_MBPS", "1.8"))
UDP_TARGET_MBPS = float(os.environ.get("SAR_UDP_TARGET_MBPS", "0.7"))
SAMPLE_WINDOW_S = 5        # how many interval lines a sample averages over
# Flow logs and the iperf3 server logs this module reads. Overridable so a
# campaign can keep them on the data disk (and so the suite can run
# somewhere /tmp is not writable). setup_over_air_test.sh writes the
# server logs, so change BOTH together or udp_sample() reads nothing.
LOG_DIR = Path(os.environ.get("SAR_TRAFFIC_LOG_DIR", "/tmp"))
SRV_LOG_DIR = Path(os.environ.get("SAR_IPERF_SRV_LOG_DIR", str(LOG_DIR)))

# An iperf3 server runs ONE test at a time per port, so the TCP and UDP flows
# for a robot must target different ports or the second one to start dies with
# "the server is busy running a test". setup_over_air_test.sh starts a server
# on each of these for every robot's test IP.
TCP_PORT = 5201
UDP_PORT = 5202

# iperf3 interval lines, e.g.
#   [  5]   4.00-5.00   sec  1.43 MBytes  12.0 Mbits/sec    0   191 KBytes
# UDP client lines carry jitter and loss instead of retransmits.
RE_RATE = re.compile(r"([\d.]+)\s+([KMG]?)bits/sec")
RE_RETR = re.compile(r"bits/sec\s+(\d+)")
RE_UDP = re.compile(r"([\d.]+)\s+ms\s+(\d+)/(\d+)\s+\(([\d.]+)%\)")

_MULT = {"": 1e-6, "K": 1e-3, "M": 1.0, "G": 1e3}


def _tail(path, n=4000):
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as f:
            if size > n:
                f.seek(size - n)
                f.readline()
            return f.read().decode("utf-8", "replace").splitlines()
    except OSError:
        return []


class TrafficStreams:
    """Owns the long-lived flows for a roster of robots."""

    def __init__(self, robots, spec, sudo_pass="", netns=True):
        """`spec` maps robot -> dict with tun / test_ip / ns (ROBOT_SPEC)."""
        self.robots = list(robots)
        self.spec = spec
        self.sudo_pass = sudo_pass
        self.netns = netns
        self.procs = {}
        self.logs = {}

    def _launch(self, robot, kind):
        sp = self.spec[robot]
        log = LOG_DIR / f"traffic_{kind}_{robot}.log"
        # The flow runs as root under sudo, so its log is root-owned and a
        # plain unlink() from this process fails with EPERM. Swallowing that
        # would leave the old file in place for iperf3 to APPEND to, and a
        # tail-read would then mix in lines from a previous run — stale
        # numbers that look like live ones. Remove it with the same
        # privilege the writer will have.
        if self.netns:
            subprocess.run(["sudo", "-S", "-p", "", "rm", "-f", str(log)],
                           input=(self.sudo_pass + "\n").encode(),
                           stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL)
        else:
            try:
                log.unlink()
            except OSError:
                pass
        port = TCP_PORT if kind == "tcp" else UDP_PORT
        # See NOTE above about --bind-dev: iperf 3.9 lacks it and the netns
        # already pins the egress interface.
        base = ["iperf3", "-c", sp["test_ip"],
                "-p", str(port),
                # NOT "-t 0": iperf3 reads that as ZERO seconds, not as
                # "run forever", so the flow exits almost immediately and the
                # sampler then re-reads its stale final summary line forever
                # — every sample identical, and wrong. A day is longer than
                # any run; stop() kills the flow when the run ends.
                "-t", "86400",
                "-i", "1", "--forceflush", "--logfile", str(log)]
        if kind == "tcp":
            # Target <= 0 means GREEDY: omit -b entirely so TCP takes whatever
            # the link will give. Throughput then measures link CAPACITY
            # rather than our chosen offered load — at the cost of saturating
            # the cell, which is what the C2 label has to survive.
            cmd = base + (["-b", f"{TCP_TARGET_MBPS}M"]
                          if TCP_TARGET_MBPS > 0 else [])
        else:
            # --get-server-output brings the receiver's loss/jitter back to
            # the client; a UDP client's own interval lines only report what
            # it SENT, so loss measured client-side is always zero.
            # UDP is never greedy: with no rate limit it would blast the
            # cell open-loop and tell us nothing except that it can.
            cmd = base + ["-u", "-b", f"{max(UDP_TARGET_MBPS, 0.1)}M"]
        if self.netns:
            cmd = ["sudo", "-S", "-p", "", "ip", "netns", "exec", sp["ns"]] + cmd
            p = subprocess.Popen(cmd, stdin=subprocess.PIPE,
                                 stdout=subprocess.DEVNULL,
                                 stderr=subprocess.DEVNULL,
                                 start_new_session=True)
            try:
                p.stdin.write((self.sudo_pass + "\n").encode())
                p.stdin.flush()
            except Exception:
                pass
        else:
            p = subprocess.Popen(cmd, stdout=subprocess.DEVNULL,
                                 stderr=subprocess.DEVNULL,
                                 start_new_session=True)
        self.procs[(robot, kind)] = p
        self.logs[(robot, kind)] = log
        return p

    def start(self):
        for r in self.robots:
            for kind in ("tcp", "udp"):
                self._launch(r, kind)
        # Let the flows get past slow start before anyone samples them. This
        # wait happens ONCE per run, not once per cycle — which is the whole
        # point of the change.
        time.sleep(6)

    def restart(self, robot, kind):
        """Relaunch one flow. A flow can die when its UE reattaches."""
        p = self.procs.get((robot, kind))
        if p and p.poll() is None:
            try:
                os.killpg(os.getpgid(p.pid), signal.SIGKILL)
            except Exception:
                pass
        self._launch(robot, kind)

    def alive(self, robot, kind):
        p = self.procs.get((robot, kind))
        return bool(p and p.poll() is None)

    def _ensure(self, robot, kind):
        """Relaunch a flow that has exited, so a sample is never stale.

        A flow dies when its UE reattaches, when the server is bounced, or
        if it is misconfigured. Without this the sampler happily reports the
        dead flow's last summary line forever — which is exactly how a
        broken run can look like a stable one.
        """
        if not self.alive(robot, kind):
            self.restart(robot, kind)
            return False
        return True

    def tcp_sample(self, robot):
        """(mbps, retransmits) averaged over the last SAMPLE_WINDOW_S lines."""
        self._ensure(robot, "tcp")
        rates, retr = [], 0
        for line in _tail(self.logs.get((robot, "tcp"), ""))[-SAMPLE_WINDOW_S * 2:]:
            if ("bits/sec" not in line or "sender" in line
                    or "receiver" in line or "0.00-" in line):
                continue
            m = RE_RATE.search(line)
            if m:
                rates.append(float(m.group(1)) * _MULT.get(m.group(2), 1.0))
            mr = RE_RETR.search(line)
            if mr:
                retr += int(mr.group(1))
        rates = rates[-SAMPLE_WINDOW_S:]
        if not rates:
            # No interval lines to average. Either _ensure() just relaunched
            # this flow (its log is recreated EMPTY, so the next few samples
            # have nothing in them) or the flow has not reached its first
            # interval yet. Returning 0.00 here is a fabrication: it is
            # indistinguishable from a link that genuinely carried nothing,
            # and a flow that dies and restarts repeatedly would stripe the
            # throughput column with zeros that look like outages.
            # Report "not measured" and let the caller mark it.
            return None, retr
        return round(sum(rates) / len(rates), 2), retr

    def udp_sample(self, robot):
        """(mbps, jitter_ms, loss_pct) from the SERVER's interval lines.

        Loss and jitter are only known to the receiver. The client's own
        interval lines end in a datagram COUNT, not a loss figure, and its
        receiver summary only arrives if the test ends cleanly — which never
        happens for a flow we kill when the run ends. So read the server log,
        which reports loss per interval while the flow is live.
        """
        self._ensure(robot, "udp")
        srv = SRV_LOG_DIR / f"iperfsrv_{self.spec[robot]['test_ip']}_{UDP_PORT}.log"
        rates, jit, loss = [], [], []
        for line in _tail(srv)[-SAMPLE_WINDOW_S * 3:]:
            if ("bits/sec" not in line or "sender" in line
                    or "receiver" in line or "0.00-" in line):
                continue
            m = RE_RATE.search(line)
            mu = RE_UDP.search(line)
            if m and mu:
                rates.append(float(m.group(1)) * _MULT.get(m.group(2), 1.0))
                jit.append(float(mu.group(1)))
                loss.append(float(mu.group(4)))
        if rates:
            n = SAMPLE_WINDOW_S
            return (round(sum(rates[-n:]) / len(rates[-n:]), 2),
                    round(sum(jit[-n:]) / len(jit[-n:]), 3),
                    round(sum(loss[-n:]) / len(loss[-n:]), 1))
        # No server lines yet: report unknown rather than inventing 100% loss,
        # which would read as a total outage on a perfectly good link. The
        # RATE is unknown for the same reason, so it is None as well — a 0.0
        # here was the same fabrication tcp_sample used to make.
        return None, None, None

    def _udp_sample_client_unused(self, robot):
        rates, jit, loss = [], [], []
        for line in _tail(self.logs.get((robot, "udp"), ""))[-SAMPLE_WINDOW_S * 2:]:
            if "bits/sec" not in line or "sender" in line or "receiver" in line:
                continue
            m = RE_RATE.search(line)
            if m:
                rates.append(float(m.group(1)) * _MULT.get(m.group(2), 1.0))
            mu = RE_UDP.search(line)
            if mu:
                jit.append(float(mu.group(1)))
                loss.append(float(mu.group(4)))
        rates, jit, loss = rates[-SAMPLE_WINDOW_S:], jit[-SAMPLE_WINDOW_S:], loss[-SAMPLE_WINDOW_S:]
        return (round(sum(rates) / len(rates), 2) if rates else 0.0,
                round(sum(jit) / len(jit), 3) if jit else 0.0,
                round(sum(loss) / len(loss), 1) if loss else 100.0)

    def stop(self):
        for (r, kind), p in list(self.procs.items()):
            if p and p.poll() is None:
                try:
                    os.killpg(os.getpgid(p.pid), signal.SIGKILL)
                except Exception:
                    pass
        self.procs.clear()


def main():
    import argparse
    import sys
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=int, default=20)
    a = ap.parse_args()
    os.environ.setdefault("SAR_WORLD", "sar_open_field")
    import auto_patrol as apx
    ts = TrafficStreams(apx.ROBOTS, apx.ROBOT_SPEC,
                        sudo_pass=os.environ.get("SAR_SUDO_PASS", ""),
                        netns=apx.NETNS_MODE)
    print(f"starting flows: TCP {TCP_TARGET_MBPS} Mb/s, UDP {UDP_TARGET_MBPS} Mb/s "
          f"per robot, netns={apx.NETNS_MODE}")
    ts.start()
    try:
        for _ in range(max(1, a.seconds // 3)):
            for r in apx.ROBOTS:
                t, retr = ts.tcp_sample(r)
                u, j, l = ts.udp_sample(r)
                # loss is None until the server has reported an interval;
                # print it as unknown rather than crashing or inventing 0.
                fmt = lambda v, w, d: " " * (w - 3) + "n/a" if v is None else f"{v:{w}.{d}f}"
                ls = "  n/a" if l is None else f"{l:5.1f}"
                print(f"  {r:8s} TCP {fmt(t, 6, 2)} Mb/s (retr {retr:4d})   "
                      f"UDP {fmt(u, 6, 2)} Mb/s jit {fmt(j, 5, 2)} ms loss {ls}%")
            print()
            time.sleep(3)
    finally:
        ts.stop()
        print("flows stopped.")


if __name__ == "__main__":
    main()
