"""Fig. 5: all four robots C2 up, per arm, forest and open field, capped,
cautious learner for C/D. Each point = one 90-min run; bar = mean over runs.
Runs are read from the runner logs (never edited):
  forest seed 1794000001: capped A/B (logs/capped_forest_flyoff_20261112_002402_runs.csv)
                          cautious C/D (logs/cautious_forest_flyoff_20261112_102709_runs.csv)
  all other seeds/worlds: logs/final_repeats_*_runs.csv (run_final_repeats.sh)
Only runs with rc == 0 and at least 50 cycles are used (a failed start-up
or a run killed before it wrote its cycle CSV is skipped, never imputed). Metric as scripts/ml/flyoff_forest_cautious_analyse.py
(time-weighted, C2 up = loss < 50 %). Writes fig_arms_seeds.pdf and
fig_arms_seeds.csv (one row per run). Prints how many runs each cell has, so a
partial figure is never mistaken for the final one."""
import glob
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from style import setup, COL_W, OUT, MARKERS

setup()
cap = pd.read_csv("logs/capped_forest_flyoff_20261112_002402_runs.csv", header=None,
                  names=["i", "arm", "world", "dir", "rc", "rows"])
cap = cap[cap.arm.isin(["A", "B"])].assign(seed=1794000001)
cau = pd.read_csv("logs/cautious_forest_flyoff_20261112_102709_runs.csv", header=None,
                  names=["i", "arm", "world", "dir", "rc", "rows"]).assign(seed=1794000001)
fr = [pd.read_csv(f, header=None, names=["i", "arm", "world", "seed", "dir", "rc", "rows"])
      for f in sorted(glob.glob("logs/final_repeats_*_runs.csv"))]
runs = pd.concat([cap, cau] + fr, ignore_index=True)
runs = runs[(runs.rc == 0) & (runs.rows >= 50)]


def all4(d, world):
    x = pd.read_csv(f"{d}/{world}.csv"); t = pd.to_datetime(x.timestamp, errors="coerce")
    ts = (t - t.iloc[0]).dt.total_seconds().values
    dt = np.clip(np.diff(ts, append=ts[-1] + np.median(np.diff(ts))), 0, 120)
    n = (np.column_stack([x[f"{c}_c2_loss_pct"].values for c in "abcd"]) < 50).sum(1)
    return 100 * np.sum(dt * (n == 4)) / dt.sum()


runs["all4"] = [all4(r.dir, r.world) for r in runs.itertuples()]
runs.to_csv(f"{OUT}/fig_arms_seeds.csv", index=False)
fig, axes = plt.subplots(1, 2, figsize=(COL_W, 1.7), sharey=True)
for ax, w, title in zip(axes, ["sar_forest", "sar_open_field"], ["(a) forest", "(b) open field"]):
    for i, arm in enumerate("ABCD"):
        v = runs[(runs.world == w) & (runs.arm == arm)].all4.values
        if len(v):
            ax.bar(i, v.mean(), 0.6, color="0.85", edgecolor="0", lw=0.6)
            ax.plot(np.full(len(v), i) + np.linspace(-0.12, 0.12, len(v)), v, MARKERS[0], ms=3,
                    mfc="white", mec="0", mew=0.7)
        ax.text(i, 2, f"n={len(v)}", ha="center", fontsize=6)
    h = runs[(runs.world == w) & (runs.arm == "A")].all4
    if len(h):
        ax.axhline(h.mean(), color="0.4", lw=0.6, ls=(0, (3, 2)), zorder=0)
    ax.set_xticks(range(4)); ax.set_xticklabels(["A", "B", "C", "D"]); ax.set_title(title, fontsize=8)
    ax.set_ylim(0, 100)
axes[0].set_ylabel("all 4 robots C2 up (%)")
fig.tight_layout(w_pad=0.5)
fig.savefig(f"{OUT}/fig_arms_seeds.pdf")
print(runs.groupby(["world", "arm"]).all4.agg(["count", "mean", "min", "max"]).round(1).to_string())
