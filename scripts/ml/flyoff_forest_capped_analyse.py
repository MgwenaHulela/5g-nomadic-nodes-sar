"""Forest arms: uncapped fly-offs (xyz 2026-11-10, prober 2026-11-11) vs the
capped re-flight (2026-11-12). Same metric as flyoff_analyse2.py: time-weighted,
C2 up = loss < 50 %, 10-min block bootstrap for all-4-up. Adds a bootstrap CI
on the capped arm vs capped A/B differences (same seed, separate runs)."""
import re
import numpy as np, pandas as pd
rng = np.random.default_rng(0)
sets = [("xyz", "logs/xyz_flyoff_20261110_172251_runs.csv"),
        ("prober", "logs/prober_flyoff_20261111_092251_runs.csv"),
        ("capped", "logs/capped_forest_flyoff_20261112_002402_runs.csv")]
runs = pd.concat([pd.read_csv(f, header=None, names=["i", "arm", "world", "dir", "rc", "rows"]).assign(set=s)
                  for s, f in sets], ignore_index=True)
runs = runs[runs.world == "sar_forest"]
out, series = [], {}
for r in runs.itertuples():
    d = pd.read_csv(f"{r.dir}/{r.world}.csv")
    t = pd.to_datetime(d.timestamp, errors="coerce")
    ts = (t - t.iloc[0]).dt.total_seconds().values
    dt = np.clip(np.diff(ts, append=ts[-1] + np.median(np.diff(ts))), 0, 120)
    loss = np.column_stack([d[f"{c}_c2_loss_pct"].values for c in "abcd"])
    up = loss < 50; n = up.sum(1); w = dt / dt.sum()
    blk = (ts // 600).astype(int); ub = np.unique(blk); bs = []
    for _ in range(1000):
        s = rng.choice(ub, len(ub)); idx = np.concatenate([np.where(blk == b)[0] for b in s])
        bs.append(100 * np.sum(dt[idx] * (n[idx] == 4)) / dt[idx].sum())
    lo, hi = np.percentile(bs, [2.5, 97.5])
    z = d.uav_z_m.values; h = ts >= ts[-1] / 2
    lat = np.column_stack([d[f"{c}_vid_latency_ms"] for c in "abcd"])
    R = open(f"{r.dir}/{r.world}_research.log").read()
    series[(r.set, r.arm)] = (ts, dt, up, n)
    out.append(dict(set=r.set, arm=r.arm, mins=round(ts[-1] / 60), rows=len(d),
                    C2=100 * np.sum(w * up.mean(1)), all4=100 * np.sum(w * (n == 4)),
                    all4_ci=f"{lo:.0f}-{hi:.0f}", ge3=100 * np.sum(w * (n >= 3)),
                    none=100 * np.sum(w * (n == 0)), z_2nd_half=np.sum(w[h] * z[h]) / w[h].sum(),
                    vid_p90_s=np.nanpercentile(lat, 90) / 1000,
                    rec=len(re.findall("rlf_recovered after", R)),
                    fail=len(re.findall("reattach_failed after", R))))
o = pd.DataFrame(out).sort_values(["arm", "set"])
pd.set_option("display.width", 200)
print(o.round(1).to_string(index=False))
o.round(2).to_csv("logs/capped_forest_flyoff_20261112_002402_summary.csv", index=False)

# capped arms vs capped baselines: block-bootstrap the difference in all-4-up
# and mean C2 (independent resampling of each run's 10-min blocks)
def boot(key, stat):
    ts, dt, up, n = series[key]
    blk = (ts // 600).astype(int); ub = np.unique(blk)
    v = []
    for _ in range(2000):
        s = rng.choice(ub, len(ub)); i = np.concatenate([np.where(blk == b)[0] for b in s])
        v.append(stat(dt[i], up[i], n[i]))
    return np.array(v)
stats = {"all4": lambda dt, up, n: 100 * np.sum(dt * (n == 4)) / dt.sum(),
         "C2": lambda dt, up, n: 100 * np.sum(dt * up.mean(1)) / dt.sum()}
print("\ncapped: arm - baseline, 95% block-bootstrap CI")
for m, f in stats.items():
    B = {a: boot(("capped", a), f) for a in "ABCD"}
    for a in "BCD":
        for b in "AB":
            if a == b: continue
            dlt = B[a] - B[b]
            print(f"  {m:5} {a}-{b}: {np.mean(dlt):+5.1f}  [{np.percentile(dlt,2.5):+5.1f}, {np.percentile(dlt,97.5):+5.1f}]")
