"""Fig. 3: forest fly-off, every arm uncapped vs capped. (a) all four robots
C2 up, with 95 % block-bootstrap CI; (b) no robot C2 up. Source:
logs/capped_forest_flyoff_20261112_002402_summary.csv (sets 'xyz' = uncapped
2026-11-10, 'capped' = 2026-11-12), produced by
scripts/ml/flyoff_forest_capped_analyse.py. One 90-min run per bar.
Note: C/D learner differs (uncapped: UCB bandit; capped: prober)."""
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from style import setup, COL_W, OUT

setup()
s = pd.read_csv("logs/capped_forest_flyoff_20261112_002402_summary.csv")
arms = ["A", "B", "C", "D"]
lab = ["A", "B", "C", "D"]
fig, axes = plt.subplots(1, 2, figsize=(COL_W, 1.75))
x = np.arange(4); w = 0.38
for k, (setname, face, hatch, name) in enumerate([("xyz", "white", "////", "no cap"),
                                                  ("capped", "0.25", None, "adaptive cap")]):
    q = s[s.set == setname].set_index("arm").loc[arms]
    lo = np.array([float(c.split("-")[0]) for c in q.all4_ci]); hi = np.array([float(c.split("-")[1]) for c in q.all4_ci])
    axes[0].bar(x + (k - .5) * w, q.all4, w, color=face, edgecolor="0", hatch=hatch, lw=0.6, label=name,
                yerr=[q.all4 - lo, hi - q.all4], error_kw=dict(lw=0.6, capsize=1.5))
    axes[1].bar(x + (k - .5) * w, q.none, w, color=face, edgecolor="0", hatch=hatch, lw=0.6)
axes[0].set_ylabel("time (%)"); axes[0].set_title("(a) all 4 robots C2 up", fontsize=8)
axes[1].set_title("(b) no robot C2 up", fontsize=8)
for ax in axes:
    ax.set_xticks(x); ax.set_xticklabels(lab); ax.set_ylim(0, 100); ax.set_xlabel("arm")
fig.legend(*axes[0].get_legend_handles_labels(), loc="upper center", ncol=2, bbox_to_anchor=(0.5, 1.07))
fig.tight_layout(w_pad=0.6)
fig.savefig(f"{OUT}/fig_cap_arms.pdf")
print(s[s.set.isin(["xyz", "capped"])][["set", "arm", "C2", "all4", "all4_ci", "none"]].to_string(index=False))
