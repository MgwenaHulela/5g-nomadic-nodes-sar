#!/usr/bin/env python3
"""Phase 4d -- the MODEL-RECOVERED sign flip on a uniform time base.

Phase 4b concluded that the terrain-blind model does not recover the measured
sign flip: two of the four recovered interactions came out inverted and
significant. That conclusion was computed per cycle.

Phase 4c then showed the per-cycle base is not trustworthy for exactly this
comparison. The measurement cycle waits on its probes, probes block when the
link degrades, so cycle duration is partly caused by the outcome; and cadence
differs by terrain (forest 22.1 s, hills 11.0 s). Re-estimating the MEASURED
effects on duration-weighted bins withdrew the hills-forest contrast entirely.

The recovered effects were never given the same treatment, so the paper's
central negative claim still rests on the base that failed. This script closes
that gap. It reuses the per-row risks already fitted under leave-one-terrain-
out in Phase 4b -- no refitting, so the model is identical -- and only changes
how those risks are aggregated before differencing.

Coverage note: risk is defined only on rows where the link is currently up, so
a bin's covered time is the time its UP cycles occupied. Bins below the
coverage floor are dropped and the surviving count is printed per terrain,
because that is where the power actually goes.

Usage:
    python3 scripts/ml/sign_flip_recovered_timebase.py
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import dataset as ds                                        # noqa: E402
from sign_flip import climb_effect, interaction_test        # noqa: E402

OUT_DIR = ds.REPO / "logs" / "ml"
PAIRS = [("open_field", "forest"), ("urban", "forest"),
         ("hills", "forest"), ("open_field", "hills")]
RISKS = [("d_p_raw", "raw"), ("d_p_cal", "calibrated")]


def binned_risk(df: pd.DataFrame, bin_s: float, block_bins: int = 10,
                min_cover: float = 0.6) -> pd.DataFrame:
    """Duration-weighted bins of model risk -> first differences."""
    df = df.sort_values(["run_id", "robot", "t_elapsed_s"],
                        kind="stable").copy()
    df["bin"] = (df["t_elapsed_s"] // bin_s).astype(int)

    def wmean(sub, col):
        w, v = sub["dt"].values, sub[col].values
        m = np.isfinite(v) & np.isfinite(w)
        return np.nan if not m.any() else float(np.average(v[m], weights=w[m]))

    rows = []
    for (rid, robot, b), sub in df.groupby(["run_id", "robot", "bin"],
                                           sort=False):
        cover = sub["dt"].sum()
        if cover < min_cover * bin_s:
            continue
        rows.append(dict(run_id=rid, robot=robot, bin=b,
                         terrain=sub["terrain"].iloc[0],
                         policy=sub["policy"].iloc[0],
                         p_raw=wmean(sub, "p_raw"),
                         p_cal=wmean(sub, "p_cal"),
                         c2_ok=wmean(sub, "c2_ok"),
                         uav_z_m=wmean(sub, "uav_z_m"),
                         d_horiz_m=wmean(sub, "d_horiz_m"),
                         n_cycles=len(sub), cover_s=cover))
    if not rows:
        return pd.DataFrame()
    b = pd.DataFrame(rows).sort_values(["run_id", "robot", "bin"],
                                       kind="stable").reset_index(drop=True)
    gg = b.groupby(["run_id", "robot"], sort=False)
    for c in ("p_raw", "p_cal", "c2_ok", "uav_z_m", "d_horiz_m"):
        b["d_" + c] = b[c] - gg[c].shift(1)
    b["_gap"] = b["bin"] - gg["bin"].shift(1)
    b = b[b["_gap"] == 1].copy()
    b["block"] = (b["run_id"].astype(str) + "|" + b["robot"].astype(str) + "|"
                  + (gg.cumcount() // block_bins).astype(str))
    return b


def show(title, eff):
    print(f"\n  {title}")
    if eff is None or not len(eff):
        print("    -- insufficient data --"); return
    for _, r in eff.iterrows():
        s = "*" if r.significant else " "
        print(f"    {r.terrain:<11}{r.effect_per_10m:+8.4f}  "
              f"[{r.lo:+7.4f}, {r.hi:+7.4f}] {s}  n={int(r.n):,}"
              f"  blocks={int(r.n_blocks)}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--bins", type=float, nargs="+", default=[30., 60., 120.])
    ap.add_argument("--rows", default=str(OUT_DIR / "phase4b_rows.pkl"))
    ap.add_argument("--out", default=str(OUT_DIR))
    args = ap.parse_args()
    t0 = time.time()

    # True cycle duration comes from the FULL series, before onset filtering,
    # so a gap left by a censored row is not silently absorbed into dt.
    raw = ds.build().sort_values(["run_id", "robot", "t_elapsed_s"],
                                 kind="stable").reset_index(drop=True)
    g = raw.groupby(["run_id", "robot"], sort=False)
    raw["dt"] = g["t_elapsed_s"].diff()
    raw["dt"] = raw["dt"].fillna(
        raw.groupby(["run_id", "robot"])["dt"].transform("median"))
    raw["_k"] = raw["t_elapsed_s"].round(1)

    risk = pd.read_pickle(args.rows)
    risk["_k"] = risk["t_elapsed_s"].round(1)
    m = risk[["run_id", "robot", "_k", "p_raw", "p_cal"]].merge(
        raw[["run_id", "robot", "_k", "dt", "terrain", "policy", "c2_ok",
             "uav_z_m", "d_horiz_m", "t_elapsed_s"]],
        on=["run_id", "robot", "_k"], how="inner")
    m = m[m["dt"].between(1.0, 180.0)]
    print(f"risk rows {len(risk):,}  merged {len(m):,}  "
          f"({100*len(m)/len(risk):.1f}% kept)")
    print("  UP-cycle duration by terrain (median s): "
          + "  ".join(f"{t} {v:.1f}" for t, v in
                      m.groupby('terrain')['dt'].median().items()))

    allrows, inter = [], []
    for bin_s in args.bins:
        b = binned_risk(m, bin_s)
        if not len(b):
            print(f"\n[{bin_s:.0f}s] no usable bins"); continue
        print("\n" + "=" * 74)
        print(f"BIN {bin_s:.0f} s   usable differenced bins: {len(b):,}   "
              + "  ".join(f"{t} {n}" for t, n in
                          b['terrain'].value_counts().sort_index().items()))
        print("=" * 74)
        for ycol, tag in RISKS:
            # sign -1: + means the model expects the link to IMPROVE on climb
            eff = climb_effect(b, ycol, -1, None)
            show(f"RECOVERED {tag}, per +10 m (+ = model expects improvement)",
                 eff)
            if len(eff):
                allrows.append(eff.assign(kind=f"recovered_{tag}",
                                          bin_s=bin_s))
            print(f"    -- interactions ({tag}) --")
            for a, bb in PAIRS:
                r = interaction_test(b, ycol, -1, a, bb)
                if not r:
                    print(f"    {a:<11}- {bb:<11}  underpowered")
                    continue
                s_ = "*" if r["significant"] else " "
                print(f"    {a:<11}- {bb:<11}{r['diff_per_10m']:+8.4f} "
                      f"[{r['lo']:+7.4f}, {r['hi']:+7.4f}] {s_} "
                      f"p={r['p_two_sided']:.4g}")
                inter.append(dict(kind=f"recovered_{tag}", bin_s=bin_s, **r))
        # No measured counterpart here: risk exists only on link-UP rows, so
        # c2_ok is identically 1 on these bins and d_c2_ok is identically 0.
        # The measured effects on this time base are Phase 4c's job, over all
        # rows; comparing against those is done in the write-up, not here.
    outd = Path(args.out); outd.mkdir(parents=True, exist_ok=True)
    if allrows:
        pd.concat(allrows).to_csv(outd / "phase4d_recovered_timebase.csv",
                                  index=False)
    if inter:
        pd.DataFrame(inter).to_csv(outd / "phase4d_interactions.csv",
                                   index=False)
    print(f"\nwrote {outd/'phase4d_recovered_timebase.csv'} "
          f"({time.time()-t0:.0f}s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
