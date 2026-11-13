"""Forest altitude effect under the uplink cap, and what one run can see of it
(docs/ALTITUDE_LEARNER_REPORT.md, 2026-11-12).

1. Within-run fixed-effect slope (240-s blocks), the quantity a time-blocked
   learner such as AltitudeProber estimates, for capped prober arms C and D,
   with a 10-min block bootstrap CI.
2. Cross-run slope: arm C vs arm B (same x-y rule, same seed) paired by
   elapsed time, (C - B) regressed on (z_C - 40), block bootstrap CI.
Reward = share of robots with C2 loss < 50 % (NaN counted as down).

    python3 scripts/ml/forest_altitude_effect_capped.py
writes logs/ml/forest_altitude_effect_capped.csv
"""
import numpy as np
import pandas as pd

rng = np.random.default_rng(0)
RUNS = {"B": "logs/campaign_20261112_015702", "C": "logs/campaign_20261112_032955",
        "D": "logs/campaign_20261112_002402"}


def load(d):
    x = pd.read_csv(f"{d}/sar_forest.csv")
    loss = np.column_stack([x[f"{c}_c2_loss_pct"] for c in "abcd"])
    x["r"] = np.where(np.isfinite(loss), loss < 50, 0).mean(1)
    return x


def fe(t, z, r, block=240):
    b = np.floor(t / block); df = pd.DataFrame(dict(b=b, z=z, r=r)).dropna()
    df["zd"] = df.z - df.groupby("b").z.transform("mean")
    df["rd"] = df.r - df.groupby("b").r.transform("mean")
    return 10 * (df.zd * df.rd).sum() / (df.zd ** 2).sum()


out = []
for arm in ("C", "D"):
    x = load(RUNS[arm]); t, z, r = x.t_elapsed_s.values, x.uav_z_m.values, x.r.values
    blk = (t // 600).astype(int); ub = np.unique(blk); bs = []
    for _ in range(2000):
        s = rng.choice(ub, len(ub)); i = np.concatenate([np.where(blk == b)[0] for b in s])
        tt = np.concatenate([t[blk == b] + k * 1e6 for k, b in enumerate(s)])
        bs.append(fe(tt, z[i], r[i]))
    out.append(dict(estimate=f"within-run FE slope, arm {arm}", pts_per_10m=100 * fe(t, z, r),
                    lo=100 * np.percentile(bs, 2.5), hi=100 * np.percentile(bs, 97.5)))

B, C = load(RUNS["B"]), load(RUNS["C"])
for X in (B, C):
    X["ts"] = X.t_elapsed_s
m = pd.merge_asof(C.sort_values("ts")[["ts", "uav_z_m", "r"]], B.sort_values("ts")[["ts", "r"]],
                  on="ts", suffixes=("", "_B"), direction="nearest", tolerance=40).dropna()
dz = m.uav_z_m.values - 40; dr = m.r.values - m.r_B.values
blk = (m.ts.values // 600).astype(int); ub = np.unique(blk)
sl = lambda i: 10 * np.polyfit(dz[i], dr[i], 1)[0]
bs = []
for _ in range(2000):
    s = rng.choice(ub, len(ub)); i = np.concatenate([np.where(blk == b)[0] for b in s]); bs.append(sl(i))
out.append(dict(estimate="cross-run C - B slope (paired by time)", pts_per_10m=100 * sl(np.arange(len(dz))),
                lo=100 * np.percentile(bs, 2.5), hi=100 * np.percentile(bs, 97.5)))
o = pd.DataFrame(out)
print(o.round(1).to_string(index=False))
o.to_csv("logs/ml/forest_altitude_effect_capped.csv", index=False)
