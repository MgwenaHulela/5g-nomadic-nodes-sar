#!/usr/bin/env python3
"""video_streams.py — real SAR video traffic, one RTP-style feed per robot.

Why this exists
---------------
traffic_streams.py drives iperf3, which measures the TRANSPORT: bits per
second and nothing else. A search-and-rescue operator does not care about
bits per second, they care whether the next frame arrives. This module runs
the real thing instead:

    sar_stream_sender.py     inside each robot's netns  -> over the 5G uplink
    sar_stream_collector.py  on the host                -> per-second metrics

and samples what the collector measures:

    goodput_mbps        bytes actually delivered
    frame_delivery_pct  frames with EVERY packet received  <- mission metric
    pkt_loss_pct        from per-frame sequence gaps
    latency_ms          one-way, from the packet's send timestamp
    jitter_ms           variation in that delay

frame_delivery_pct and latency_ms are the two the forecaster wants and iperf
cannot provide: delivery falls off a cliff at NLoS while Mb/s degrades
gracefully, so it separates "degraded" from "unusable" far better.

Design notes carried over from traffic_streams.py, each one a bug that cost a
run (see DATA_AUDIT.md section 10):

  * Flows are PERSISTENT, sampled not probed — a per-cycle burst measures TCP
    slow-start, not the link.
  * A sample with no data returns None, never 0.0. A relaunched stream has an
    empty log for a few seconds; reporting 0.0 there is indistinguishable
    from a link that genuinely carried nothing, and stripes the column with
    false outages.
  * Collector logs are TRUNCATED at start. A stale log feeds last run's rows
    into this run's cycles.
  * Children get start_new_session=True so a dead parent cannot take the
    stream with it, and are killed by process GROUP on stop.
"""

import csv
import os
import signal
import subprocess
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent

# One port per robot: a collector binds one (host_ip, port) pair, and sharing
# a port across robots would silently merge two robots' frames into one
# stream (this is the bug that made TCP and UDP fight over 5201).
BASE_PORT = int(os.environ.get("SAR_VIDEO_BASE_PORT", "5401"))

# 1.5 Mb/s per robot. CALIBRATED 2026-10-23 with FOUR robots and the
# channel bridge alive (pathloss 0-27 dB):
#   2.5 Mb/s -> C2 availability 32%  (label degenerate)
#   1.5 Mb/s -> C2 availability 86%, goodput 1.70, frames 99.6%
# Video is UDP CBR and does NOT back off, so it needs a lower rate
# than iperf for the same C2 health: TCP throttles when the link
# degrades, a video encoder keeps pumping and starves the pings.
# With TWO robots the cell allows ~4x this; raise it via SAR_VIDEO_MBPS.
BITRATE_MBPS = float(os.environ.get("SAR_VIDEO_MBPS", "1.5"))
FPS = int(os.environ.get("SAR_VIDEO_FPS", "25"))

LOG_DIR = Path(os.environ.get("SAR_VIDEO_LOG_DIR", "/tmp"))

# Average the last few per-second rows. One row is noisy (a single late frame
# swings delivery by 1/fps); the whole file would smear across the cycle.
SAMPLE_WINDOW_S = 5


def _port_for(robots, robot):
    return BASE_PORT + list(robots).index(robot)


class VideoStreams:
    """Per-robot video feed: collector on the host, sender in the netns."""

    def __init__(self, robots, spec, sudo_pass="", netns=True,
                 bitrate_mbps=BITRATE_MBPS, fps=FPS):
        self.robots = list(robots)
        self.spec = spec
        self.sudo_pass = sudo_pass
        self.netns = netns
        self.bitrate = bitrate_mbps
        self.fps = fps
        self.coll = {}      # robot -> collector Popen (host side)
        self.send = {}      # robot -> sender Popen (netns side)
        self.csv = {}       # robot -> collector CSV path

    # ---------------------------------------------------------------- launch
    def _launch_collector(self, robot):
        out = LOG_DIR / f"video_{robot}.csv"
        try:
            out.unlink()          # never inherit last run's rows
        except OSError:
            pass
        log = LOG_DIR / f"video_coll_{robot}.log"
        cmd = ["python3", str(HERE / "sar_stream_collector.py"),
               "--bind", self.spec[robot]["test_ip"],
               "--port", str(_port_for(self.robots, robot)),
               "--robot-id", robot.upper(),
               "--csv", str(out), "--duration", "0"]
        p = subprocess.Popen(cmd, stdout=open(log, "wb"),
                             stderr=subprocess.STDOUT,
                             start_new_session=True)
        self.coll[robot] = p
        self.csv[robot] = out
        return p

    def _launch_sender(self, robot):
        sp = self.spec[robot]
        log = LOG_DIR / f"video_send_{robot}.log"
        cmd = ["python3", str(HERE / "sar_stream_sender.py"),
               "--dst", sp["test_ip"],
               "--port", str(_port_for(self.robots, robot)),
               "--src-ip", sp["ue_ip"],
               "--robot-id", robot.upper(),
               "--bitrate-mbps", str(self.bitrate),
               "--fps", str(self.fps),
               "--duration", "0"]          # 0 = run until killed
        if self.netns:
            cmd = ["sudo", "-S", "-p", "", "ip", "netns", "exec", sp["ns"]] + cmd
            p = subprocess.Popen(cmd, stdin=subprocess.PIPE,
                                 stdout=open(log, "wb"),
                                 stderr=subprocess.STDOUT,
                                 start_new_session=True)
            try:
                p.stdin.write((self.sudo_pass + "\n").encode())
                p.stdin.flush()
            except Exception:
                pass
        else:
            p = subprocess.Popen(cmd, stdout=open(log, "wb"),
                                 stderr=subprocess.STDOUT,
                                 start_new_session=True)
        self.send[robot] = p
        return p

    def start(self):
        self._reap_strays()
        for r in self.robots:
            self._launch_collector(r)
        time.sleep(1.5)               # collectors must be bound before senders
        for r in self.robots:
            self._launch_sender(r)
        # Let the first frames land so the first sample is real, not empty.
        time.sleep(4)

    # ---------------------------------------------------------------- health
    def _kill(self, p):
        """Kill a stream and its whole process group.

        The sender runs under `sudo ip netns exec ...`, so its children are
        ROOT-owned. An unprivileged os.killpg() therefore fails with EPERM —
        and if that is swallowed, the old stream keeps running. Generations
        then pile up: four of them were found streaming into one collector,
        their independent frame_id sequences interleaving so that NO frame
        ever completed (frame delivery 0.3%) while goodput read ~3x target.
        Escalate to a root kill when the plain one is refused.
        """
        if not p or p.poll() is not None:
            return
        try:
            pgid = os.getpgid(p.pid)
        except Exception:
            pgid = None
        try:
            if pgid is not None:
                os.killpg(pgid, signal.SIGKILL)
            else:
                p.kill()
            return
        except PermissionError:
            pass
        except Exception:
            pass
        if pgid is not None:
            subprocess.run(["sudo", "-S", "-p", "", "kill", "-9", "--", f"-{pgid}"],
                           input=(self.sudo_pass + "\n").encode(),
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def _reap_strays(self):
        """Kill any sender/collector left over from an earlier run.

        A previous generation streaming to the same collector port corrupts
        every metric, so never start on top of one.
        """
        pats = [f"sar_stream_sender.py", f"sar_stream_collector.py"]
        try:
            out = subprocess.run(["ps", "-eo", "pid,args"],
                                 capture_output=True, text=True, timeout=10).stdout
        except Exception:
            return
        victims = []
        for line in out.splitlines()[1:]:
            if any(x in line for x in pats) and "--robot-id" in line:
                try:
                    victims.append(int(line.split()[0]))
                except Exception:
                    pass
        for pid in victims:
            subprocess.run(["sudo", "-S", "-p", "", "kill", "-9", str(pid)],
                           input=(self.sudo_pass + "\n").encode(),
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if victims:
            print(f"[video] reaped {len(victims)} stray stream process(es)",
                  flush=True)

    def _alive(self, robot):
        c, s = self.coll.get(robot), self.send.get(robot)
        return bool(c and c.poll() is None and s and s.poll() is None)

    def _ensure(self, robot):
        """Relaunch a dead feed. Returns False if it had to restart."""
        if self._alive(robot):
            return True
        for d in (self.send, self.coll):
            self._kill(d.get(robot))
        self._launch_collector(robot)
        time.sleep(0.5)
        self._launch_sender(robot)
        return False

    # ---------------------------------------------------------------- sample
    def sample(self, robot):
        """Mean of the last SAMPLE_WINDOW_S collector rows.

        Returns a dict of floats, or all-None when the feed has produced
        nothing yet (just relaunched, or still binding). None means NOT
        MEASURED and must be written as an empty cell, never as 0.
        """
        keys = ("goodput_mbps", "frame_delivery_pct", "pkt_loss_pct",
                "latency_ms", "jitter_ms")
        blank = {k: None for k in keys}
        self._ensure(robot)
        path = self.csv.get(robot)
        if not path or not Path(path).exists():
            return blank
        try:
            with open(path) as f:
                rows = list(csv.DictReader(f))
        except OSError:
            return blank
        if not rows:
            return blank

        # Drop the collector's warm-up rows: before the first frame arrives it
        # emits zeros, and averaging those in drags every metric down.
        live = [r for r in rows if _f(r.get("frames_sent")) not in (None, 0.0)]
        live = live[-SAMPLE_WINDOW_S:]
        if not live:
            return blank

        out = {}
        for k in keys:
            vals = [_f(r.get(k)) for r in live]
            vals = [v for v in vals if v is not None]
            out[k] = round(sum(vals) / len(vals), 3) if vals else None
        return out

    # ------------------------------------------------------------------ stop
    def _sudo_pkill(self, pattern):
        """Kill a sudo-launched child by command line.

        killpg() is not enough for the senders. They run as
        `sudo ip netns exec <ns> python3 sar_stream_sender.py ...`, and this
        host has `use_pty` in its sudoers: sudo runs the command in its OWN
        pty session, so the real python3 is NOT in the group we killpg, and
        it survives. A leaked sender keeps transmitting into the next run's
        stack bring-up and corrupts its first cycles.
        """
        try:
            subprocess.run(["sudo", "-S", "-p", "", "pkill", "-f", pattern],
                           input=(self.sudo_pass + "\n").encode(),
                           stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL, timeout=10)
        except Exception:
            pass

    def stop(self):
        for d in (self.send, self.coll):
            for robot, p in list(d.items()):
                self._kill(p)
        # Belt and braces: anything that survived killpg (see _sudo_pkill)
        # is matched by its own distinctive command line. Scoped per robot so
        # this can never touch another run's streams.
        for robot in self.robots:
            port = _port_for(self.robots, robot)
            self._sudo_pkill(f"sar_stream_sender.py .*--port {port} ")
            self._sudo_pkill(f"sar_stream_collector.py .*--port {port} ")
        self.send.clear()
        self.coll.clear()
        # sudoers here has `use_pty`, so sudo runs the sender in its OWN
        # session: the python child does not share sudo's process group and
        # survives killpg(). Sweep by name as well, or a root-owned sender
        # keeps transmitting into the next run.
        self._reap_strays()


def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def main():
    import argparse
    import sys
    sys.path.insert(0, str(HERE))
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=int, default=24)
    ap.add_argument("--mbps", type=float, default=BITRATE_MBPS)
    a = ap.parse_args()
    os.environ.setdefault("SAR_WORLD", "sar_open_field")
    import auto_patrol as apx

    vs = VideoStreams(apx.ROBOTS, apx.ROBOT_SPEC,
                      sudo_pass=os.environ.get("SAR_SUDO_PASS", ""),
                      netns=apx.NETNS_MODE, bitrate_mbps=a.mbps)
    print(f"starting video feeds: {a.mbps} Mb/s @ {FPS} fps per robot "
          f"({len(apx.ROBOTS)} robots), netns={apx.NETNS_MODE}")
    vs.start()
    try:
        for _ in range(max(1, a.seconds // 3)):
            for r in apx.ROBOTS:
                m = vs.sample(r)
                def s(k, w, d, unit=""):
                    v = m[k]
                    return (" " * (w - 3) + "n/a") if v is None else f"{v:{w}.{d}f}{unit}"
                print(f"  {r:8s} goodput {s('goodput_mbps',6,2)} Mb/s   "
                      f"frames {s('frame_delivery_pct',5,1)}%   "
                      f"loss {s('pkt_loss_pct',5,1)}%   "
                      f"lat {s('latency_ms',6,1)} ms   "
                      f"jit {s('jitter_ms',5,1)} ms")
            print()
            time.sleep(3)
    finally:
        vs.stop()
        print("video feeds stopped.")


if __name__ == "__main__":
    main()
