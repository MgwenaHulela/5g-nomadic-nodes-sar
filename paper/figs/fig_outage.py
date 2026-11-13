"""Outage-duration distribution (review R3-W1): how long each C2 loss lasts.
An outage is a maximal run of consecutive cycles in which a robot's C2 is down
(ping loss >= 50 %); its duration is the summed cycle durations (same
time-weighting as the availability metric, cycle gaps capped at 120 s).
Like-for-like arms only (same policy with and without the cap): forest A and B
(x-y-z fly-off 2026-11-10 vs capped fly-off 2026-11-12), open field A
(x-y-z fly-off vs final_repeats run 1). One 90-min run per condition.
Writes fig_outage.pdf, tab_outage.tex, tab_outage.csv."""
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from style import setup, COL_W, OUT, tex_table, LINES, GREYS

setup()
xr = pd.read_csv("logs/xyz_flyoff_20261110_172251_runs.csv", header=None,
                 names=["i", "arm", "world", "dir", "rc", "rows"]).set_index(["world", "arm"]).dir
cap = pd.read_csv("logs/capped_forest_flyoff_20261112_002402_runs.csv", header=None,
                  names=["i", "arm", "world", "dir", "rc", "rows"]).set_index("arm").dir
RUNS = [("forest", "A", "no cap", xr[("sar_forest", "A")]), ("forest", "A", "cap", cap["A"]),
        ("forest", "B", "no cap", xr[("sar_forest", "B")]), ("forest", "B", "cap", cap["B"]),
        ("open field", "A", "no cap", xr[("sar_open_field", "A")]),
        ("open field", "A", "cap", "logs/campaign_20261112_161427")]


def episodes(d):
    t = pd.to_datetime(d.timestamp, errors="coerce"); ts = (t - t.iloc[0]).dt.total_seconds().values
    dt = np.clip(np.diff(ts, append=ts[-1] + np.median(np.diff(ts))), 0, 120)
    out = []
    for c in "abcd":
        down = (pd.to_numeric(d[f"{c}_c2_loss_pct"], errors="coerce") >= 50).values
        run = 0.0
        for k in range(len(down)):
            if down[k]:
                run += dt[k]
            elif run > 0:
                out.append(run); run = 0.0
        if run > 0:
            out.append(run)
    return np.array(out), ts[-1] / 3600


rows, curves = [], {}
for terr, arm, cond, dr in RUNS:
    w = "sar_forest" if terr == "forest" else "sar_open_field"
    e, hours = episodes(pd.read_csv(f"{dr}/{w}.csv", low_memory=False))
    curves[(terr, arm, cond)] = e
    q = (lambda p: np.percentile(e, p) if len(e) else np.nan)
    rows.append(dict(terrain=terr, arm=arm, cond=cond, outages=len(e), per_robot_hour=len(e) / (4 * hours),
                     median_s=q(50), p90_s=q(90), max_s=e.max() if len(e) else np.nan,
                     share_over_60s=(e > 60).mean() if len(e) else np.nan))
r = pd.DataFrame(rows); r.to_csv(f"{OUT}/tab_outage.csv", index=False)
fmt = lambda v, f="{:.0f}": "--" if pd.isna(v) else f.format(v)
tex_table(f"{OUT}/tab_outage.tex", ["Run", "Cap", "Outages/robot-h", "Median (s)", "p90 (s)", "Max (s)"],
          [[f"{x.terrain} {x.arm}", x.cond, fmt(x.per_robot_hour, "{:.1f}"), fmt(x.median_s), fmt(x.p90_s), fmt(x.max_s)]
           for x in r.itertuples()], "@{}llrrrr@{}")
fig, axes = plt.subplots(1, 2, figsize=(COL_W, 1.8), sharey=True)
for ax, terr in zip(axes, ["forest", "open field"]):
    for k, ((t, arm, cond), e) in enumerate([(k, v) for k, v in curves.items() if k[0] == terr]):
        if len(e) == 0:
            continue
        x = np.sort(e); y = 1 - np.arange(len(x)) / len(x)
        ax.step(x, y, where="post", color="0" if cond == "cap" else "0.55",
                ls="-" if arm == "A" else "--", lw=1.0, label=f"{arm}, {cond}")
    ax.set_xscale("log"); ax.set_title(f"({'ab'[['forest','open field'].index(terr)]}) {terr}", fontsize=8)
    ax.set_xlabel("outage duration (s)"); ax.legend(fontsize=6, loc="lower left")
axes[0].set_ylabel("share of outages longer than x")
fig.tight_layout(w_pad=0.6); fig.savefig(f"{OUT}/fig_outage.pdf")
print(r.round(2).to_string(index=False))
