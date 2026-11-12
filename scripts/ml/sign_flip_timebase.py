#!/usr/bin/env python3
"""Phase 4c — the sign flip on a UNIFORM TIME BASE.

The per-cycle first-difference estimator has a defect that Section IX of the
write-up flags as unresolved: the measurement cycle waits for its probes, and
probes block when the link is failing, so the cycle DURATION is partly caused
by the outcome being measured.

    C2 available    median cycle 11.6 s
    C2 unavailable  median cycle 18.6 s
    forest overall  22.1 s        hills overall  11.0 s

A forest "observation" therefore covers about twice the wall-clock time of a
hills one, and the sampling interval is a post-treatment variable. Conditioning
on it -- which is what "one row per cycle" silently does -- can attenuate or
exaggerate a between-terrain difference.

This script removes the dependence by resampling onto a fixed grid before
differencing. Within each (run, robot), cycles are assigned to fixed-width
time bins; availability is averaged over the bin WEIGHTED BY CYCLE DURATION,
so a long failing cycle contributes the time it actually occupied rather than
one row; altitude is averaged the same way. First differences are then taken
between CONSECUTIVE bins, which are equal in duration by construction, in
every terrain.

Everything else matches Phase 4b: dead-process censoring, the same block
bootstrap, the same interaction tests, effects reported per +10 m of climb.

Usage:
    python3 scripts/ml/sign_flip_timebase.py              # 30, 60, 120 s bins
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import dataset as ds                                        # noqa: E402
import sign_flip as sf                                      # noqa: E402
from sign_flip import climb_effect, interaction_test, first_differences  # noqa: E402
from onset_forecast import dead_ue_mask                     # noqa: E402

OUT_DIR = ds.REPO / "logs" / "ml"
PAIRS = [("open_field", "forest"), ("open_field", "hills"),
         ("urban", "forest"), ("hills", "forest")]


def time_binned_differences(raw: pd.DataFrame, bin_s: float,
                            block_bins: int = 10,
                            min_cover: float = 0.6) -> pd.DataFrame:
    """One row per (run, robot, bin) -> first differences on a uniform grid."""
    df = raw.sort_values(["run_id", "robot", "t_elapsed_s"],
                         kind="stable").copy()
    g = df.groupby(["run_id", "robot"], sort=False)
    # Duration each cycle actually occupied. The first cycle of a series has
    # no predecessor; give it the series median rather than dropping it.
    df["dt"] = g["t_elapsed_s"].diff()
    df["dt"] = df["dt"].fillna(df.groupby(["run_id", "robot"])["dt"]
                               .transform("median"))
    df = df[df["dt"].between(1.0, 180.0)].copy()    # drop restarts/stalls
    df["bin"] = (df["t_elapsed_s"] // bin_s).astype(int)

    def wmean(sub, col):
        w = sub["dt"].values
        v = sub[col].values
        m = np.isfinite(v) & np.isfinite(w)
        return np.nan if not m.any() else float(np.average(v[m], weights=w[m]))

    rows = []
    for (rid, robot, b), sub in df.groupby(["run_id", "robot", "bin"],
                                           sort=False):
        cover = sub["dt"].sum()
        if cover < min_cover * bin_s:
            continue                                 # partial bin
        rows.append(dict(run_id=rid, robot=robot, bin=b,
                         terrain=sub["terrain"].iloc[0],
                         policy=sub["policy"].iloc[0],
                         c2_ok=wmean(sub, "c2_ok"),
                         uav_z_m=wmean(sub, "uav_z_m"),
                         d_horiz_m=wmean(sub, "d_horiz_m"),
                         n_cycles=len(sub), cover_s=cover))
    b = pd.DataFrame(rows).sort_values(["run_id", "robot", "bin"],
                                       kind="stable").reset_index(drop=True)
    gg = b.groupby(["run_id", "robot"], sort=False)
    for c in ("c2_ok", "uav_z_m", "d_horiz_m"):
        b["d_" + c] = b[c] - gg[c].shift(1)
    b["_gap"] = b["bin"] - gg["bin"].shift(1)
    b = b[b["_gap"] == 1].copy()                     # consecutive bins only
    b["block"] = (b["run_id"].astype(str) + "|" + b["robot"].astype(str) + "|"
                  + (gg.cumcount() // block_bins).astype(str))
    return b.rename(columns={"d_uav_z_m": "d_uav_z_m"})


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
    ap.add_argument("--out", default=str(OUT_DIR))
    args = ap.parse_args()

    raw = ds.build().sort_values(["run_id", "robot", "t_elapsed_s"],
                                 kind="stable").reset_index(drop=True)
    raw["dead"] = dead_ue_mask(raw)
    cen = raw[~raw["dead"]].copy()
    print(f"rows {len(raw):,}  dead-UE censored {100*raw['dead'].mean():.1f}%"
          f"  -> {len(cen):,} live rows")

    print("\n" + "=" * 72)
    print("REFERENCE: per-cycle estimator (what Phase 4b reported)")
    print("=" * 72)
    ref = first_differences(cen)
    show("effect per +10 m", climb_effect(ref, "d_c2_ok", 1, None))
    for a, b in PAIRS:
        r = interaction_test(ref, "d_c2_ok", 1, a, b)
        if r:
            s = "*" if r["significant"] else " "
            print(f"    {a:<11}- {b:<11}{r['diff_per_10m']:+8.4f} "
                  f"[{r['lo']:+7.4f}, {r['hi']:+7.4f}] {s} "
                  f"p={r['p_two_sided']:.4g}")

    out = []
    for bs in args.bins:
        print("\n" + "=" * 72)
        print(f"UNIFORM TIME BASE: {int(bs)} s bins, duration-weighted")
        print("=" * 72)
        b = time_binned_differences(cen, bs)
        print(f"  bins {len(b):,}   median cycles/bin "
              f"{b['n_cycles'].median():.1f}   "
              f"median coverage {b['cover_s'].median():.0f} s")
        by = b.groupby("terrain")["n_cycles"].median()
        print("  median cycles per bin by terrain: "
              + ", ".join(f"{k} {v:.1f}" for k, v in by.items()))
        eff = climb_effect(b, "d_c2_ok", 1, None)
        show("effect per +10 m", eff)
        if eff is not None and len(eff):
            out.append(eff.assign(bin_s=int(bs), kind="timebase"))
        for a, bb in PAIRS:
            r = interaction_test(b, "d_c2_ok", 1, a, bb)
            if r:
                s = "*" if r["significant"] else " "
                print(f"    {a:<11}- {bb:<11}{r['diff_per_10m']:+8.4f} "
                      f"[{r['lo']:+7.4f}, {r['hi']:+7.4f}] {s} "
                      f"p={r['p_two_sided']:.4g}")
                out.append(pd.DataFrame([dict(bin_s=int(bs), kind="interaction",
                                              terrain=f"{a}-{bb}",
                                              effect_per_10m=r["diff_per_10m"],
                                              lo=r["lo"], hi=r["hi"],
                                              significant=r["significant"],
                                              p=r["p_two_sided"])]))
    if out:
        outd = Path(args.out); outd.mkdir(parents=True, exist_ok=True)
        pd.concat(out, ignore_index=True).to_csv(
            outd / "phase4c_timebase.csv", index=False)
        print(f"\nwrote {outd/'phase4c_timebase.csv'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
