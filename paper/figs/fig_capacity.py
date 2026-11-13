"""Fig. 4: forecasting skill by model class, 65 radio_blind features, LOTO,
20-s onset horizon (n = 38,303 onset rows). Bars: pooled PR-AUC lift over
no-skill; markers: worst held-out-terrain ROC-AUC (right axis is avoided:
printed as text). Source: logs/ml/phase2c_model_class.csv
(scripts/ml/model_class_cmp.py); the 20-s block is the one with n == 38303."""
import pandas as pd
import matplotlib.pyplot as plt
from style import setup, COL_W, OUT

setup()
d = pd.read_csv("logs/ml/phase2c_model_class.csv")
d = d[(d.terrain == "POOLED") & (d.features == "radio_blind") & (d.n == 38303)]
NAME = {"logistic_L1": "logistic (L1)", "logistic_L2_strong": "logistic (L2, strong)",
        "logistic_L2": "logistic (L2)", "lightgbm": "LightGBM", "random_forest": "random forest",
        "mlp_small": "MLP small", "mlp_medium": "MLP medium", "mlp_large": "MLP large"}
d = d.set_index("model").loc[list(NAME)]
fig, ax = plt.subplots(figsize=(COL_W, 1.9))
y = range(len(d))[::-1]
face = ["0.2"] * 3 + ["0.55"] * 2 + ["white"] * 3
hatch = [None] * 5 + ["////"] * 3
for yi, (m, r), f, h in zip(y, d.iterrows(), face, hatch):
    ax.barh(yi, r.pr_lift, 0.62, color=f, edgecolor="0", hatch=h, lw=0.6)
    ax.text(r.pr_lift + 0.03, yi, f"{r.pr_lift:.2f}$\\times$   worst ROC {r.roc_min_fold:.3f}",
            va="center", fontsize=6.5)
ax.set_yticks(list(y)); ax.set_yticklabels([NAME[m] for m in d.index])
ax.axvline(1, color="0", ls=":", lw=0.7)
ax.set_xlim(0, 2.9); ax.set_xlabel("PR-AUC lift over no-skill (pooled, held-out terrains)")
fig.savefig(f"{OUT}/fig_capacity.pdf")
print(d[["pr_lift", "roc_auc", "roc_min_fold"]].round(3).to_string())
