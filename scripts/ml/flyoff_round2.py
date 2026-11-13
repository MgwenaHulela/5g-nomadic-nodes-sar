#!/usr/bin/env python3
"""Fly-off analysis: a new planner round vs the round-1 relative planner vs
excitation, all at matched robot geometry.

The three-way shape is the point. Round 1 could only contrast planner against
excitation, which confounds "did the planner help" with "is a planner better
than a random walk". A later round adds planner-vs-planner with the policy family,
the surrogate and the candidate lateral grid all held constant, so the only
thing that differs is the objective -- which is the thing that was fixed.

Robot geometry is controlled by binning on d_horiz and reweighting onto the
comparator's exposure, because a replayed seed does NOT replay the robot paths
(see run_planner_flyoff.sh). Block bootstrap over 20-cycle blocks, since C2 is
strongly serially correlated.

The round under test is passed on the command line; round 1 and the excitation
baseline are fixed below. Used for round 2 (discarded to a reference-frame bug)
and then round 3.

Usage:  python3 scripts/ml/flyoff_round2.py <new_open_field> <new_urban> \
                                            <new_forest> <new_hills>
"""
import sys
import numpy as np
import pandas as pd
from scipy import stats

ROBOTS = ["a", "b", "c", "d"]
BINS = [(0, 25), (25, 50), (50, 75), (75, 100), (100, 150)]
BLOCK = 20
NBOOT = 600
PH4 = {"open_field": 0.1641, "urban": 0.0370, "hills": 0.0085,
       "forest": -0.0120}            # Phase 4 measured 10 m-climb effect
V1 = {"open_field": "campaign_20261026_222941",
      "urban": "campaign_20261027_000120",
      "forest": "campaign_20261027_013304",
      "hills": "campaign_20261027_030546"}
EX = {"open_field": "campaign_20261025_235902",
      "urban": "campaign_20261026_013033",
      "forest": "campaign_20261026_030216",
      "hills": "campaign_20261026_043517"}
rng = np.random.default_rng(7)


def stack(d, terr):
    df = pd.read_csv(f"logs/{d}/sar_{terr}.csv", low_memory=False)
    dt = df["t_elapsed_s"].diff().shift(-1)
    dt.iloc[-1] = dt.median()
    out = []
    for r in ROBOTS:
        out.append(pd.DataFrame(dict(
            cyc=np.arange(len(df)), dh=df[f"{r}_d_horiz_m"],
            z=df["uav_z_m"], loss=df[f"{r}_c2_loss_pct"],
            dt=dt.clip(lower=0))))
    s = pd.concat(out).dropna(subset=["dh", "loss"])
    s["ok"] = (s.loss < 50).astype(float)
    s["blk"] = s.cyc // BLOCK
    return s


def matched(a, b):
    num = den = 0.0
    for lo, hi in BINS:
        x = a[(a.dh >= lo) & (a.dh < hi)]
        y = b[(b.dh >= lo) & (b.dh < hi)]
        if len(x) < 40 or len(y) < 40:
            continue
        w = len(y)
        num += (x.ok.mean() - y.ok.mean()) * 100 * w
        den += w
    return num / den if den else np.nan


def boot(A, B):
    ba = [g for _, g in A.groupby("blk")]
    bb = [g for _, g in B.groupby("blk")]
    out = []
    for _ in range(NBOOT):
        ia = pd.concat([ba[i] for i in rng.integers(0, len(ba), len(ba))])
        ib = pd.concat([bb[i] for i in rng.integers(0, len(bb), len(bb))])
        v = matched(ia, ib)
        if np.isfinite(v):
            out.append(v)
    return np.percentile(out, [2.5, 97.5]) if out else (np.nan, np.nan)


def wavg(df, col):
    m = df[col].notna() & df["dt"].notna()
    return float(np.average(df.loc[m, col], weights=df.loc[m, "dt"]))


def main():
    v2 = dict(zip(["open_field", "urban", "forest", "hills"], sys.argv[1:5]))
    terrs = ["open_field", "urban", "forest", "hills"]

    print("=" * 74)
    print("DID THE CEILING RATCHET GO AWAY?  (the thing that was fixed)")
    print("=" * 74)
    print(f"{'terrain':11} {'new mean z':>13} {'%>=58m':>8} "
          f"{'round1 mean z':>13} {'%>=58m':>8}   new z by quintile")
    for t in terrs:
        a = pd.read_csv(f"logs/{v2[t]}/sar_{t}.csv", low_memory=False)
        b = pd.read_csv(f"logs/{V1[t]}/sar_{t}.csv", low_memory=False)
        q = " ".join(f"{v:5.1f}" for v in a.groupby(
            pd.qcut(a.t_elapsed_s, 5, labels=False)).uav_z_m.mean())
        print(f"{t:11} {a.uav_z_m.mean():13.1f} {100*(a.uav_z_m>=58).mean():7.1f}% "
              f"{b.uav_z_m.mean():13.1f} {100*(b.uav_z_m>=58).mean():7.1f}%   {q}")

    print("\n  predicted set points from the surrogate z-sweep:")
    print("    forest 40.0   hills 47.5   urban 57.5   open_field 60.0 m")

    print("\n" + "=" * 74)
    print("P(C2 up) AT MATCHED ROBOT GEOMETRY, block bootstrap")
    print("=" * 74)
    res = {}
    for label, comp in (("vs round-1 planner (objective fix isolated)", V1),
                        ("vs excitation baseline", EX)):
        print(f"\n  {label}")
        d4 = {}
        for t in terrs:
            A, B = stack(v2[t], t), stack(comp[t], t)
            d = matched(A, B)
            lo, hi = boot(A, B)
            d4[t] = d
            sig = "*" if (lo > 0 or hi < 0) else " "
            print(f"    {t:11} {d:+6.1f} pp  [{lo:+6.1f}, {hi:+6.1f}] {sig}")
        res[label] = d4

    print("\n" + "=" * 74)
    print("AVAILABILITY vs PHASE 4 CLIMB EFFECT")
    print("  NOTE: the 'vs round-1' row is NOT a sign-flip test. It measures where")
    print("  the objective fix helped, which is the INVERSE of where climbing helps,")
    print("  so a NEGATIVE correlation there is the expected, desirable signature.")
    print("=" * 74)
    for label, d4 in res.items():
        x = [PH4[k] for k in terrs]
        y = [d4[k] for k in terrs]
        if any(not np.isfinite(v) for v in y):
            print(f"  {label}: incomplete")
            continue
        print(f"  {label}\n    Pearson {stats.pearsonr(x,y)[0]:+.3f}  "
              f"Spearman {stats.spearmanr(x,y)[0]:+.3f}  "
              f"sign agreement {sum(1 for a,b in zip(x,y) if np.sign(a)==np.sign(b))}/4")

    print("\n" + "=" * 74)
    print("RAW worst-robot C2 availability = ALL FOUR robots up (time-weighted;")
    print("CONFOUNDED, context only; near-zero in forest by arithmetic, not failure)")
    print("=" * 74)
    for t in terrs:
        r = []
        for tag, d in (("new", v2[t]), ("round1", V1[t]), ("excite", EX[t])):
            df = pd.read_csv(f"logs/{d}/sar_{t}.csv", low_memory=False)
            dt = df["t_elapsed_s"].diff().shift(-1); dt.iloc[-1] = dt.median()
            ok = pd.concat([(df[f"{x}_c2_loss_pct"] < 50).astype(float)
                            .where(df[f"{x}_c2_loss_pct"].notna())
                            for x in ROBOTS], axis=1)
            w = pd.DataFrame(dict(v=ok.min(axis=1), dt=dt.clip(lower=0))).dropna()
            r.append(f"{tag} {100*np.average(w.v, weights=w.dt):5.1f}%")
        print(f"  {t:11} " + "   ".join(r))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
