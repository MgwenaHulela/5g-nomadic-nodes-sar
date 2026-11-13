"""Fig. 6: UAV altitude over the mission, capped forest, learned-altitude arms
C and D, exploring (prober) vs cautious learner. Sources: sar_forest.csv in
logs/campaign_20261112_032955 (C prober), _002402 (D prober),
_102709 (C cautious), _115951 (D cautious)."""
import pandas as pd
import matplotlib.pyplot as plt
from style import setup, FULL_W, OUT

setup()
RUNS = {"C": {"exploring": "logs/campaign_20261112_032955", "cautious": "logs/campaign_20261112_102709"},
        "D": {"exploring": "logs/campaign_20261112_002402", "cautious": "logs/campaign_20261112_115951"}}
TITLE = {"C": "(a) arm C: centroid x-y", "D": "(b) arm D: model x-y"}
fig, axes = plt.subplots(1, 2, figsize=(FULL_W, 1.55), sharey=True)
for ax, arm in zip(axes, "CD"):
    for name, ls, g, lw in [("exploring", "-", "0.6", 1.0), ("cautious", "-", "0.0", 0.8)]:
        x = pd.read_csv(f"{RUNS[arm][name]}/sar_forest.csv"); t = pd.to_datetime(x.timestamp)
        ax.step((t - t.iloc[0]).dt.total_seconds() / 60, x.uav_z_m, where="post", color=g, ls=ls, lw=lw,
                label=f"{name} learner")
    ax.axhline(40, color="0", ls=":", lw=0.6)
    ax.set_title(TITLE[arm], fontsize=8); ax.set_xlabel("mission time (min)"); ax.set_xlim(0, 92)
axes[0].set_ylabel("UAV altitude (m)"); axes[0].set_yticks([30, 40, 50])
lg = fig.legend(*axes[0].get_legend_handles_labels(), loc="upper center", ncol=2, bbox_to_anchor=(0.5, 1.1))
for h in lg.get_lines():
    h.set_linewidth(1.6)
fig.tight_layout(w_pad=0.8)
fig.savefig(f"{OUT}/fig_altitude.pdf")
print("fig_altitude.pdf")
