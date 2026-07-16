#!/usr/bin/env python3
"""plot_stream.py — publication figure from a SAR 5G streaming run.

Reads a stream collector CSV (logs/stream_<robot>_<ts>.csv) and plots the
application QoS over time: frame-delivery ratio, goodput, and latency. If a
channel_bridge_log.csv is available it overlays the robot's path loss and
shades the NLoS (behind-a-building) periods across every panel — so the
figure shows the streaming quality falling exactly when the robot loses
line of sight.

Usage:
  python3 scripts/plot_stream.py                      # newest stream CSV
  python3 scripts/plot_stream.py logs/stream_bravo_20260716_1600.csv
  python3 scripts/plot_stream.py --no-bridge          # QoS only, no shading
"""
import argparse
import csv
import glob
import os
import sys
from datetime import datetime

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

# calm, colourblind-safe palette (neutrals biased toward the accents)
C_DELIV = "#1a9e8f"   # teal  — frame delivery
C_GOOD  = "#3b6fb0"   # blue  — goodput
C_LAT   = "#7d5ba6"   # mauve — latency
C_PLOSS = "#c1663a"   # rust  — path loss (the cause)
C_NLOS  = "#e0a33a"   # amber — NLoS shading
INK     = "#2b2f33"
GRID    = "#d9dce0"


def _epoch(ts):
    """ISO timestamp -> epoch seconds. Naive strings are read as local time
    (channel_bridge_log), aware strings keep their offset (stream collector);
    both resolve to the same absolute scale via .timestamp()."""
    try:
        return datetime.fromisoformat(ts).timestamp()
    except Exception:
        return None


def load_stream(path):
    t, deliv, good, lat = [], [], [], []
    with open(path) as f:
        for r in csv.DictReader(f):
            e = _epoch(r["timestamp"])
            if e is None:
                continue
            t.append(e)
            deliv.append(float(r["frame_delivery_pct"] or 0))
            good.append(float(r["goodput_mbps"] or 0))
            lat.append(float(r["latency_ms"]) if r.get("latency_ms") else float("nan"))
    robot = "ROBOT"
    try:
        with open(path) as f:
            robot = next(csv.DictReader(f))["robot"]
    except Exception:
        pass
    return t, deliv, good, lat, robot


def load_bridge(path, robot, t0, t1):
    """Return (t, ploss, nlos_bool) for the robot, clipped to [t0, t1]."""
    ch_col = "alpha_ch_type" if robot.lower() == "alpha" else "bravo_ch_type"
    pl_col = "ploss_gnb_ue0" if robot.lower() == "alpha" else "ploss_gnb_ue1"
    t, pl, nlos = [], [], []
    try:
        with open(path) as f:
            for r in csv.DictReader(f):
                e = _epoch(r["timestamp"])
                if e is None or e < t0 - 2 or e > t1 + 2:
                    continue
                try:
                    p = float(r.get(pl_col) or "nan")
                except ValueError:
                    p = float("nan")
                t.append(e)
                pl.append(p)
                nlos.append(r.get(ch_col, "") == "TDL_A")
    except FileNotFoundError:
        return None
    return (t, pl, nlos) if t else None


def _nlos_spans(t, nlos):
    spans, start = [], None
    for i, flag in enumerate(nlos):
        if flag and start is None:
            start = t[i]
        elif not flag and start is not None:
            spans.append((start, t[i])); start = None
    if start is not None:
        spans.append((start, t[-1]))
    return spans


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("csv", nargs="?", help="stream CSV (default: newest)")
    ap.add_argument("--bridge-csv", default="logs/channel_bridge_log.csv")
    ap.add_argument("--no-bridge", action="store_true")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    path = args.csv
    if not path:
        cands = sorted(glob.glob("logs/stream_*.csv"), key=os.path.getmtime)
        if not cands:
            sys.exit("no logs/stream_*.csv found — run stream_demo.sh first")
        path = cands[-1]
    t, deliv, good, lat, robot = load_stream(path)
    if not t:
        sys.exit(f"no rows in {path}")
    t0 = t[0]
    rel = [x - t0 for x in t]

    bridge = None
    if not args.no_bridge:
        bridge = load_bridge(args.bridge_csv, robot, t[0], t[-1])

    npanels = 4 if bridge else 3
    fig, axes = plt.subplots(npanels, 1, figsize=(9, 2.1 * npanels),
                             sharex=True, constrained_layout=True)
    fig.suptitle(f"{robot} — live video+telemetry over 5G  "
                 f"(mean delivery {sum(deliv)/len(deliv):.0f}%, "
                 f"mean goodput {sum(good)/len(good):.1f} Mbps)",
                 fontsize=12, fontweight="bold", color=INK)

    spans = []
    if bridge:
        bt, bpl, bnlos = bridge
        spans = _nlos_spans([x - t0 for x in bt], bnlos)

    def style(ax, ylabel):
        ax.set_ylabel(ylabel, fontsize=10, color=INK)
        ax.grid(True, color=GRID, lw=0.6)
        ax.set_axisbelow(True)
        for s in ("top", "right"):
            ax.spines[s].set_visible(False)
        for a, b in spans:
            ax.axvspan(a, b, color=C_NLOS, alpha=0.13, lw=0)

    axes[0].plot(rel, deliv, color=C_DELIV, lw=2)
    axes[0].fill_between(rel, deliv, color=C_DELIV, alpha=0.12)
    axes[0].axhline(100, ls="--", lw=1, color=GRID)
    axes[0].set_ylim(-5, 108)
    style(axes[0], "Frame\ndelivery (%)")

    axes[1].plot(rel, good, color=C_GOOD, lw=2)
    axes[1].fill_between(rel, good, color=C_GOOD, alpha=0.12)
    axes[1].set_ylim(bottom=0)
    style(axes[1], "Goodput\n(Mbps)")

    axes[2].plot(rel, lat, color=C_LAT, lw=2)
    axes[2].set_ylim(bottom=0)
    style(axes[2], "Latency\n(ms)")

    if bridge:
        brel = [x - t0 for x in bt]
        axes[3].plot(brel, bpl, color=C_PLOSS, lw=1.8)
        axes[3].set_ylim(bottom=0)
        style(axes[3], "Path loss\n(dB)")

    axes[-1].set_xlabel("time (s)", fontsize=10, color=INK)
    if spans:
        axes[0].text(0.995, 1.02, "amber = NLoS (behind building)",
                     transform=axes[0].transAxes, ha="right", va="bottom",
                     fontsize=9, color=C_NLOS, fontweight="bold")

    out = args.out or (os.path.splitext(path)[0] + ".png")
    fig.savefig(out, dpi=140)
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
