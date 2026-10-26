#!/usr/bin/env python3
"""
derive_thresholds.py — regenerate the data-derived numbers in
configs/experiment.json from the logs, so none of them is a magic constant.

    python3 scripts/derive_thresholds.py                 # archive terrains
    python3 scripts/derive_thresholds.py --include-new   # + 4-robot campaigns

What it derives
---------------
The RSRP (and SINR) floor used by the planner's per-robot margin.

The brief's default floor of -80 dBm is unusable: the minimum RSRP ever
observed is -78 dBm and nothing falls below -80, so `margin = rsrp - floor`
is identically positive and the soft-min objective has nothing to optimise
(DATA_AUDIT.md 4.6).

A floor is useful only if it satisfies BOTH conditions:
  * it PREDICTS trouble  — a large gap in C2 availability either side of it
  * it HAPPENS           — enough observations fall below it to matter

Maximising the gap alone picks the extreme (at -78 dBm the gap looks huge,
but only 0.2% of rows are below it, so the margin term would stay degenerate).
This script therefore reports the gap at every candidate and selects the best
one subject to a minimum coverage, and prints the per-terrain breakdown so a
floor that only works in one world is visible as such.
"""

import argparse
import csv
import glob
import os
from collections import defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ARCHIVE = os.path.join(ROOT, "NOMADIC5G_TERRAIN_RESULTS_2026", "*.csv")
CAMPAIGNS = os.path.join(ROOT, "logs", "campaign_*", "*.csv")
MIN_COVERAGE_PCT = 5.0
MIN_BELOW_N = 30


def fnum(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def load(include_new):
    """Pooled (rsrp, sinr, c2_available, terrain) across every available run."""
    rows = []
    for path in sorted(glob.glob(ARCHIVE)):
        terrain = os.path.basename(path).replace("_results.csv", "")
        rows += _read(path, terrain, ("a", "b"))
    if include_new:
        for path in sorted(glob.glob(CAMPAIGNS)):
            base = os.path.basename(path)
            if base.endswith(("_actions.csv", "_fastradio.csv", "manifest.csv")):
                continue
            terrain = base.replace(".csv", "")
            rows += _read(path, terrain, ("a", "b", "c", "d"))
    return rows


def _read(path, terrain, cols):
    out = []
    try:
        with open(path) as f:
            for r in csv.DictReader(f):
                for c in cols:
                    rsrp = fnum(r.get(f"{c}_ss_rsrp_dBm"))
                    sinr = fnum(r.get(f"{c}_ss_sinr_dB"))
                    c2 = fnum(r.get(f"{c}_c2_loss_pct"))
                    if rsrp is None or c2 is None:
                        continue
                    out.append((rsrp, sinr, c2 < 50, terrain))
    except OSError:
        pass
    return out


def sweep(rows, idx, label, unit, lo, hi, step):
    n = len(rows)
    print(f"\n{label} floor candidates  (n={n})")
    print(f"  {'floor':>7} {'avail below':>12} {'avail above':>12} {'gap':>7} {'% below':>9}")
    best = None
    v = lo
    while v <= hi:
        below = [r for r in rows if r[idx] is not None and r[idx] <= v]
        above = [r for r in rows if r[idx] is not None and r[idx] > v]
        if len(below) >= MIN_BELOW_N and above:
            ab = 100 * sum(1 for r in below if r[2]) / len(below)
            aa = 100 * sum(1 for r in above if r[2]) / len(above)
            gap, cov = aa - ab, 100 * len(below) / n
            mark = ""
            if cov >= MIN_COVERAGE_PCT and (best is None or gap > best[1]):
                best, mark = (v, gap, cov), "  <-"
            print(f"  {v:7.0f} {ab:11.1f}% {aa:11.1f}% {gap:6.1f} {cov:8.1f}%{mark}")
        v += step
    if best:
        print(f"  => {label} floor {best[0]:.0f} {unit}  "
              f"(gap {best[1]:.1f} pp, {best[2]:.1f}% of rows below)")
    return best


def per_terrain(rows, idx, floor, label):
    print(f"\nPer-terrain behaviour at {label} floor {floor:.0f}:")
    by = defaultdict(list)
    for r in rows:
        by[r[3]].append(r)
    for t in sorted(by):
        sub = by[t]
        below = [r for r in sub if r[idx] is not None and r[idx] <= floor]
        above = [r for r in sub if r[idx] is not None and r[idx] > floor]
        ab = 100 * sum(1 for r in below if r[2]) / len(below) if below else float("nan")
        aa = 100 * sum(1 for r in above if r[2]) / len(above) if above else float("nan")
        warn = ""
        if below and above and not (aa - ab) > 20:
            warn = "   <- does NOT separate here"
        print(f"  {t:16s} below n={len(below):5d} avail={ab:5.1f}%"
              f"   above n={len(above):5d} avail={aa:5.1f}%{warn}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--include-new", action="store_true",
                    help="also pool logs/campaign_*/ runs (4-robot schema)")
    a = ap.parse_args()
    rows = load(a.include_new)
    if not rows:
        raise SystemExit("no (RSRP, C2) observations found")

    mn = min(r[0] for r in rows)
    below80 = sum(1 for r in rows if r[0] < -80)
    print(f"pooled observations : {len(rows)}")
    print(f"min RSRP observed   : {mn:.0f} dBm")
    print(f"rows below -80 dBm  : {below80}"
          f"{'   <- the brief default is degenerate' if below80 == 0 else ''}")

    print("\nC2 availability by RSRP bin:")
    for lo, hi in [(-120, -75), (-75, -70), (-70, -65), (-65, -60),
                   (-60, -55), (-55, -50), (-50, -45), (-45, 0)]:
        sel = [r for r in rows if lo <= r[0] < hi]
        if not sel:
            continue
        av = 100 * sum(1 for r in sel if r[2]) / len(sel)
        print(f"  [{lo:5},{hi:4})  n={len(sel):5}  {av:5.1f}%  {'#' * int(av / 3)}")

    r = sweep(rows, 0, "RSRP", "dBm", -76, -50, 2)
    if r:
        per_terrain(rows, 0, r[0], "RSRP")
    if any(x[1] is not None for x in rows):
        s = sweep(rows, 1, "SINR", "dB", 14, 44, 2)
        if s:
            per_terrain(rows, 1, s[0], "SINR")

    if r:
        print(f"\nWrite into configs/experiment.json: "
              f"link_quality.rsrp_floor_dBm = {r[0]:.1f}")


if __name__ == "__main__":
    main()
