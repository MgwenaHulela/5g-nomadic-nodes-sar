#!/usr/bin/env python3
"""Delay-driven uplink cap per robot (AIMD on an HTB ceiling). Runs as root.

WHY (docs/CONGESTION_TEST_REPORT.md, 2026-11-11/12). The campaign video is
1.5 Mb/s CBR per robot. Weak forest uplinks carry 0.1-0.5 Mb/s and the OAI UE
queues the excess without bound, so C2 waits minutes behind the backlog and
links stay "down" 14-18 min after the load stops. Capping each robot in the
kernel, below what its link drains, keeps the backlog out of the modem.
Enabled per campaign with SAR_UL_CAP=adaptive (run_research_campaign.sh).

Validated in four tests (forest/open field static, forest patrol); v2 features:
  * slow start: the cap doubles every 2 s (while in use) until the first cut,
    then +50 kbit every 2 s -- the v1 linear ramp took ~52 s to reach 1.5 Mb/s;
  * survives reattach and stack restarts: the TUN is re-found every second; a
    new or bare TUN gets the HTB tree rebuilt and the controller re-enters slow
    start from START; the probe ping is restarted when it exits.

Each robot pings the core (10.45.0.1) every 0.2 s from its netns. ICMP rides the
priority class, so it skips the kernel's video queue: its RTT rises only when a
backlog builds BEYOND the cap, i.e. in the OAI UE. That is the thing to avoid.
  * baseline = min RTT over the last 60 s
  * congested (median RTT of the last second > baseline + 150 ms, or no reply
    for 1 s): cap *= 0.7, leave slow start, hold 2 s so the backlog can drain
  * clean (median < baseline + 50 ms) and the robot sent >= 70 % of the cap
    since the last check: raise (x2 in slow start, else +50 kbit) every 2 s
  * cap in [50 kbit, 1600 kbit]
Uses only RTT and bytes sent, which any robot can measure; never the
simulator's path loss.

    sudo python3 scripts/adaptive_uplink_cap.py DURATION_S LOG_CSV
"""
import csv, re, subprocess, sys, threading, time
from collections import deque

ROBOTS = ["alpha", "bravo", "charlie", "delta"]
LO, HI, START = 50, 1600, 200          # kbit/s
CUT, STEP, HOLD, UP_EVERY = 0.7, 50, 2.0, 2.0
HIGH_MS, LOW_MS, BASE_WIN = 150.0, 50.0, 60.0
VIDEO_DPORT = ("5392", "0xfff0")       # 5392-5407: the campaign's video ports


def ns(r, *cmd):
    return subprocess.run(["ip", "netns", "exec", f"robot_{r}", *cmd],
                          capture_output=True, text=True)


class Link:
    def __init__(self, r):
        self.r = r
        self.dev = None
        self.p = None
        self.rtts = deque()            # (t, ms)
        self.reset()

    def reset(self):
        self.cap, self.slow = START, True
        self.last_reply = time.time()
        self.t_cut = self.t_up = 0.0
        self.sent = (time.time(), 0)
        self.rtts.clear()

    def find_dev(self):
        m = re.search(r"\d+: (oaitun\S*?):", ns(self.r, "ip", "-o", "link").stdout)
        return m.group(1) if m else None

    def build(self):
        d = self.dev
        tc = lambda *a: ns(self.r, "tc", *a)
        tc("qdisc", "del", "dev", d, "root")
        tc("qdisc", "add", "dev", d, "root", "handle", "1:", "htb", "default", "10")
        tc("class", "add", "dev", d, "parent", "1:", "classid", "1:1", "htb",
           "rate", f"{self.cap}kbit", "ceil", f"{self.cap}kbit", "quantum", "1514")
        for cls, rate, prio in (("1:10", "20kbit", "0"), ("1:20", "8kbit", "1")):
            tc("class", "add", "dev", d, "parent", "1:1", "classid", cls, "htb", "rate",
               rate, "ceil", f"{self.cap}kbit", "prio", prio, "quantum", "1514")
        tc("qdisc", "add", "dev", d, "parent", "1:10", "handle", "10:", "pfifo", "limit", "20")
        tc("qdisc", "add", "dev", d, "parent", "1:20", "handle", "20:", "pfifo", "limit", "10")
        tc("filter", "add", "dev", d, "parent", "1:", "protocol", "ip", "prio", "1", "u32",
           "match", "ip", "protocol", "17", "0xff",
           "match", "ip", "dport", *VIDEO_DPORT, "flowid", "1:20")

    def has_tree(self):
        return "htb 1:" in ns(self.r, "tc", "qdisc", "show", "dev", self.dev).stdout

    def ensure(self):
        """Re-find the TUN; rebuild the tree if it is new or bare. Returns an
        event string or None."""
        dev = self.find_dev()
        if dev is None:
            self.dev = None
            return "no_tun"
        ev = None
        if dev != self.dev or not self.has_tree():
            ev = "rebuild" if self.dev is None or dev == self.dev else f"new_tun:{dev}"
            self.dev = dev
            self.reset()
            self.build()
            self.sent = (time.time(), self.bytes_sent())
        if self.p is None or self.p.poll() is not None:
            self.p = subprocess.Popen(["ip", "netns", "exec", f"robot_{self.r}", "ping",
                                       "-D", "-i", "0.2", "-W", "2", "10.45.0.1"],
                                      stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                      text=True)
            threading.Thread(target=self.read, args=(self.p,), daemon=True).start()
        return ev

    def read(self, p):
        for line in p.stdout:
            m = re.search(r"time=([\d.]+)", line)
            if m:
                now = time.time()
                self.rtts.append((now, float(m.group(1))))
                self.last_reply = now

    def set(self, k):
        # `tc class change` resets omitted fields (prio -> 0), so restate them
        for cls, extra in (("1:1", ["rate", f"{k}kbit"]),
                           ("1:10", ["rate", "20kbit", "prio", "0"]),
                           ("1:20", ["rate", "8kbit", "prio", "1"])):
            ns(self.r, "tc", "class", "change", "dev", self.dev, "classid", cls,
               "htb", *extra, "ceil", f"{k}kbit", "quantum", "1514")

    def bytes_sent(self):
        m = re.search(r"class htb 1:1 root.*?Sent (\d+) bytes", ns(
            self.r, "tc", "-s", "class", "show", "dev", self.dev).stdout, re.S)
        return int(m.group(1)) if m else 0

    def step(self, now):
        ev = self.ensure()
        if self.dev is None:
            return [f"{now:.3f}", self.r, "", "", "", "", "", "no_tun"]
        while self.rtts and self.rtts[0][0] < now - BASE_WIN:
            self.rtts.popleft()
        recent = sorted(ms for t, ms in self.rtts if t >= now - 1.0)
        base = min((ms for _, ms in self.rtts), default=float("nan"))
        med = recent[len(recent) // 2] if recent else float("nan")
        silent = now - self.last_reply > 1.0
        act = ev or "hold"
        if ev is None:
            if (silent or (recent and med > base + HIGH_MS)) and now - self.t_cut >= HOLD:
                self.cap = max(LO, int(self.cap * CUT)); self.t_cut = now
                self.slow = False; act = "cut"
            elif recent and med < base + LOW_MS and now - self.t_up >= UP_EVERY \
                    and now - self.t_cut >= HOLD:
                # raise only when the cap is actually in use (RFC 7661 idea)
                t0, b0 = self.sent
                b1 = self.bytes_sent()
                used = (b1 - b0) * 8 / 1000 / max(now - t0, 1e-3)
                self.sent = (now, b1)
                self.t_up = now
                if used < 0.7 * self.cap:
                    act = "idle"
                elif self.cap >= HI:
                    act = "at_max"
                else:
                    self.cap = min(HI, self.cap * 2 if self.slow else self.cap + STEP)
                    act = "up_x2" if self.slow else "up"
            if act in ("cut", "up", "up_x2"):
                self.set(self.cap)
        return [f"{now:.3f}", self.r, f"{med:.0f}", f"{base:.0f}", int(silent),
                self.cap, int(self.slow), act]


def main():
    dur, out = float(sys.argv[1]), sys.argv[2]
    links = [Link(r) for r in ROBOTS]
    end = time.time() + dur
    with open(out, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["t", "robot", "rtt_med_ms", "rtt_base_ms", "silent", "cap_kbit",
                    "slow_start", "action"])
        while time.time() < end:
            time.sleep(1.0)
            now = time.time()
            for l in links:
                try:
                    w.writerow(l.step(now))
                except Exception as e:          # netns gone mid-restart etc.
                    w.writerow([f"{now:.3f}", l.r, "", "", "", "", "", f"error:{e!r}"[:80]])
            f.flush()
    for l in links:
        if l.p:
            l.p.terminate()


if __name__ == "__main__":
    main()
