"""Deeper analysis of the capped 3-seed runs (see final_repeats_interim.py for the run list).

A  arm means with a two-stage bootstrap (resample runs, then 10-min blocks within each run),
   arm - hover differences, and an equivalence check against a +/-5-point margin
B  per-robot C2 availability by arm (which robot loses)
C  altitude use (share of time per band, mean z) and the per-run link between z and all-4-up
D  C2 outage episodes per robot: count per hour, median, 90th percentile, longest
E  geometry: horizontal distance and elevation angle UAV -> robot
F  uplink cap: mean cap per robot, share at maximum, cuts per hour; video goodput and latency
Writes logs/ml/final_repeats_deep_*.csv. Runs with < 50 rows are skipped, never imputed.
"""
import numpy as np, pandas as pd
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
rng = np.random.default_rng(1)
R = "abcd"; NAMES = dict(a="alpha", b="bravo", c="charlie", d="delta")
MARGIN = 5.0

runs = pd.read_csv(REPO / "logs/ml/final_repeats_interim.csv")
runs = runs[runs.status == "ok"].reset_index(drop=True)


def load(dr, w):
    d = pd.read_csv(REPO / dr / f"{w}.csv", low_memory=False)
    t = pd.to_datetime(d.timestamp, errors="coerce")
    ts = (t - t.iloc[0]).dt.total_seconds().values
    dt = np.clip(np.diff(ts, append=ts[-1] + np.median(np.diff(ts))), 0, 120)
    up = np.column_stack([d[f"{c}_c2_loss_pct"].values for c in R]) < 50
    return d, ts, dt, up


def all4(dt, up):
    return 100 * np.sum(dt * (up.sum(1) == 4)) / dt.sum()


data = {}
rows_b, rows_c, rows_d, rows_e, rows_f = [], [], [], [], []
for r in runs.itertuples():
    d, ts, dt, up = load(r.dir, r.world)
    key = (r.world, r.arm, r.seed)
    data[key] = (ts, dt, up)
    hrs = dt.sum() / 3600
    z = d.uav_z_m.values
    # B per robot
    for k, c in enumerate(R):
        rows_b.append(dict(world=r.world, arm=r.arm, seed=r.seed, robot=NAMES[c],
                           c2_up=100 * np.sum(dt * up[:, k]) / dt.sum()))
    # C altitude
    bands = {"<35": z < 35, "35-45": (z >= 35) & (z < 45), ">=45": z >= 45}
    corr = np.corrcoef(z, up.sum(1))[0, 1] if np.std(z) > 0.5 else np.nan
    rows_c.append(dict(world=r.world, arm=r.arm, seed=r.seed, z_mean=np.sum(dt * z) / dt.sum(),
                       **{f"time_{b}": 100 * np.sum(dt * m) / dt.sum() for b, m in bands.items()},
                       corr_z_robots_up=corr, moves=int((np.abs(np.diff(z)) > 0.5).sum())))
    # D outages per robot
    for k, c in enumerate(R):
        down = ~up[:, k]; eps, cur = [], 0.0
        for i in range(len(down)):
            if down[i]: cur += dt[i]
            elif cur > 0: eps.append(cur); cur = 0.0
        if cur > 0: eps.append(cur)
        e = np.array(eps)
        rows_d.append(dict(world=r.world, arm=r.arm, seed=r.seed, robot=NAMES[c], n=len(e),
                           per_hour=len(e) / hrs, median_s=np.median(e) if len(e) else 0,
                           p90_s=np.percentile(e, 90) if len(e) else 0, longest_s=e.max() if len(e) else 0))
    # E geometry
    for c in R:
        h = d[f"{c}_d_horiz_m"].values
        el = np.degrees(np.arctan2(z, np.maximum(h, 0.1)))
        rows_e.append(dict(world=r.world, arm=r.arm, seed=r.seed, robot=NAMES[c],
                           horiz_m=np.nanmean(h), elev_deg=np.nanmean(el)))
    # F cap + video
    cap = pd.read_csv(REPO / r.dir / f"{r.world}_ulcap.csv")
    cap.action = cap.action.astype(str).str.strip()
    for c in R:
        g = cap[cap.robot == NAMES[c]]
        rows_f.append(dict(world=r.world, arm=r.arm, seed=r.seed, robot=NAMES[c],
                           cap_mean_kbit=g.cap_kbit.mean(), at_max_pct=100 * (g.cap_kbit >= 1600).mean(),
                           cuts_per_hour=(g.action == "cut").sum() / hrs,
                           vid_goodput_mbps=d[f"{c}_vid_goodput_mbps"].mean(),
                           vid_lat_p90_s=np.nanpercentile(d[f"{c}_vid_latency_ms"], 90) / 1000))


def two_stage(keys, B=2000):
    out = []
    for _ in range(B):
        vals = []
        for k in rng.choice(len(keys), len(keys)):
            ts, dt, up = data[keys[k]]
            blk = (ts // 600).astype(int); ub = np.unique(blk)
            i = np.concatenate([np.where(blk == b)[0] for b in rng.choice(ub, len(ub))])
            vals.append(all4(dt[i], up[i]))
        out.append(np.mean(vals))
    return np.array(out)


rows_a = []
for w in ["sar_open_field", "sar_forest"]:
    boots = {}
    for a in "ABCD":
        keys = [k for k in data if k[0] == w and k[1] == a]
        if keys:
            boots[a] = (keys, two_stage(keys))
    for a, (keys, b) in boots.items():
        row = dict(world=w, arm=a, runs=len(keys), all4_mean=np.mean([all4(*data[k][1:]) for k in keys]),
                   lo=np.percentile(b, 2.5), hi=np.percentile(b, 97.5))
        if a != "A":
            diff = b - boots["A"][1]
            lo, hi = np.percentile(diff, [2.5, 97.5]); lo90, hi90 = np.percentile(diff, [5, 95])
            row.update(diff_vs_A=diff.mean(), diff_lo=lo, diff_hi=hi,
                       verdict=("worse than hover" if hi < 0 else "better than hover" if lo > 0 else
                                f"equivalent within +/-{MARGIN:g}" if (lo90 > -MARGIN and hi90 < MARGIN) else
                                "not established"))
        rows_a.append(row)

out = REPO / "logs/ml"
for name, rows in dict(a_arms=rows_a, b_robots=rows_b, c_altitude=rows_c, d_outages=rows_d,
                       e_geometry=rows_e, f_cap_video=rows_f).items():
    pd.DataFrame(rows).round(2).to_csv(out / f"final_repeats_deep_{name}.csv", index=False)

pd.set_option("display.width", 200)
A = pd.DataFrame(rows_a).round(1)
print("== A. arm all-4-up, two-stage bootstrap (runs, then blocks) ==\n", A.to_string(index=False))
Bd = pd.DataFrame(rows_b)
print("\n== B. per-robot C2 up %, mean over runs ==\n",
      Bd.pivot_table(index=["world", "arm"], columns="robot", values="c2_up").round(1).to_string())
Cd = pd.DataFrame(rows_c)
print("\n== C. altitude ==\n", Cd.groupby(["world", "arm"])[["z_mean", "time_<35", "time_35-45", "time_>=45",
      "corr_z_robots_up", "moves"]].mean().round(2).to_string())
Dd = pd.DataFrame(rows_d)
print("\n== D. C2 outages per robot, mean over runs and robots ==\n", Dd.groupby(["world", "arm"])[
      ["per_hour", "median_s", "p90_s", "longest_s"]].mean().round(1).to_string())
print("   longest single outage per arm:\n", Dd.groupby(["world", "arm"]).longest_s.max().round(0).to_string())
Ed = pd.DataFrame(rows_e)
print("\n== E. geometry, mean over runs and robots ==\n", Ed.groupby(["world", "arm"])[["horiz_m", "elev_deg"]].mean().round(1).to_string())
Fd = pd.DataFrame(rows_f)
print("\n== F. cap and video, mean over runs and robots ==\n", Fd.groupby(["world", "arm"])[
      ["cap_mean_kbit", "at_max_pct", "cuts_per_hour", "vid_goodput_mbps", "vid_lat_p90_s"]].mean().round(2).to_string())
print("\n   per robot (forest, all arms):\n", Fd[Fd.world == "sar_forest"].groupby("robot")[
      ["cap_mean_kbit", "at_max_pct", "vid_goodput_mbps"]].mean().round(2).to_string())
