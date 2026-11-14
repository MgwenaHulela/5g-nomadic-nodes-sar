"""Interim analysis of the 3-seed final-repeats campaign (run_final_repeats.sh).

Same metric as flyoff_forest_cautious_analyse.py: time-weighted, C2 up = loss < 50 %,
95 % CIs from a 10-min block bootstrap within each run. Forest seed 1 comes from the
capped (00:24) and cautious (10:27) fly-off runners, so all arms share every seed.
Paired differences vs hover (arm A) are taken within a seed: same robot paths.
Runs with < 50 rows (failed start-up, or killed mid-run) are skipped, never imputed.

Writes logs/ml/final_repeats_interim.csv (per run) and prints per-arm summaries.
"""
import glob, numpy as np, pandas as pd
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
rng = np.random.default_rng(0)
FOREST_S1 = {"A": "logs/campaign_20261112_050243", "B": "logs/campaign_20261112_015702",
             "C": "logs/campaign_20261112_102709", "D": "logs/campaign_20261112_115951"}


def load(dr, w):
    f = REPO / dr / f"{w}.csv"
    if not f.exists():
        return None
    d = pd.read_csv(f, low_memory=False)
    if len(d) < 50:
        return None
    t = pd.to_datetime(d.timestamp, errors="coerce")
    ts = (t - t.iloc[0]).dt.total_seconds().values
    dt = np.clip(np.diff(ts, append=ts[-1] + np.median(np.diff(ts))), 0, 120)
    up = np.column_stack([d[f"{c}_c2_loss_pct"].values for c in "abcd"]) < 50
    return ts, dt, up


def metrics(dt, up):
    n = up.sum(1)
    return (100 * np.sum(dt * up.mean(1)) / dt.sum(), 100 * np.sum(dt * (n == 4)) / dt.sum(),
            100 * np.sum(dt * (n == 0)) / dt.sum())


def boot_all4(ts, dt, up, B=1000):
    blk = (ts // 600).astype(int); ub = np.unique(blk); v = []
    for _ in range(B):
        i = np.concatenate([np.where(blk == b)[0] for b in rng.choice(ub, len(ub))])
        v.append(metrics(dt[i], up[i])[1])
    return np.percentile(v, [2.5, 97.5])


runs = []
for f in sorted(glob.glob(str(REPO / "logs/final_repeats_2*_runs.csv"))
                + glob.glob(str(REPO / "logs/final_repeats_rerun_2*_runs.csv"))):
    r = pd.read_csv(f, header=None, names=["i", "arm", "world", "seed", "dir", "rc", "rows"])
    runs += [dict(i=x.i, arm=x.arm, world=x.world, seed=int(str(x.seed)[-1]), dir=x.dir) for x in r.itertuples()]
runs += [dict(i=0, arm=a, world="sar_forest", seed=1, dir=d) for a, d in FOREST_S1.items()]

out = []
for r in runs:
    L = load(r["dir"], r["world"])
    if L is None:
        out.append({**r, "status": "no data"}); continue
    c2, a4, none = metrics(L[1], L[2]); lo, hi = boot_all4(*L)
    out.append({**r, "status": "ok", "c2_up": round(c2, 1), "all4_up": round(a4, 1),
                "all4_lo": round(lo, 1), "all4_hi": round(hi, 1), "none_up": round(none, 1)})
df = pd.DataFrame(out)
(REPO / "logs/ml").mkdir(parents=True, exist_ok=True)
df.to_csv(REPO / "logs/ml/final_repeats_interim.csv", index=False)

ok = df[df.status == "ok"]
for w in ["sar_open_field", "sar_forest"]:
    print(f"\n== {w}: all-4-up % by arm x seed ==")
    print(ok[ok.world == w].pivot_table(index="arm", columns="seed", values="all4_up").round(1).to_string())
    g = ok[ok.world == w].groupby("arm")
    print(pd.DataFrame({"n": g.size(), "all4_mean": g.all4_up.mean().round(1), "all4_min": g.all4_up.min(),
                        "all4_max": g.all4_up.max(), "c2_mean": g.c2_up.mean().round(1),
                        "none_max": g.none_up.max()}).to_string())
    p = ok[ok.world == w].pivot_table(index="seed", columns="arm", values="all4_up")
    for a in [c for c in p.columns if c != "A"]:
        d = (p[a] - p["A"]).dropna()
        print(f"  {a} - A paired by seed: {', '.join(f'{v:+.1f}' for v in d)}  mean {d.mean():+.1f} (n={len(d)})")
print("\nmissing:", df[df.status != "ok"][["i", "arm", "world", "seed", "dir"]].to_string(index=False))
