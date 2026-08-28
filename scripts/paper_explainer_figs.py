#!/usr/bin/env python3
"""paper_explainer_figs.py — narrative graphs from the campaign data:
(1) path loss and (2) C2 connectivity over the 6-hour run, per terrain, both
UEs, with the static->nomadic gNB transition marked. These show, at a glance,
how each robot's link responds when the drone starts moving."""
import csv, math
from pathlib import Path
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

ROOT = Path.home() / "sar_sim"
FIG = ROOT / "docs" / "paper" / "figures"
OI = dict(blue="#0072B2", green="#009E73", vermillion="#D55E00", grey="#999999")
plt.rcParams.update({
    "font.family": "serif", "font.size": 9, "axes.titlesize": 9,
    "axes.labelsize": 8.5, "legend.fontsize": 7.5, "xtick.labelsize": 7.5,
    "ytick.labelsize": 7.5, "axes.grid": True, "grid.alpha": 0.25,
    "grid.linewidth": 0.5, "axes.axisbelow": True, "figure.dpi": 150,
    "savefig.bbox": "tight", "pdf.fonttype": 42, "legend.frameon": False,
})

C1 = ROOT / "logs" / "campaign_20260728_210058"
C2 = ROOT / "logs" / "campaign_20260729_122440"
WORLDS = [("Open field", C1 / "sar_open_field.csv"),
          ("Urban",      C2 / "sar_urban.csv"),
          ("Forest",     C2 / "sar_forest.csv"),
          ("Hills",      C2 / "sar_hills.csv")]
PHASE_H = 3.0   # static/nomadic boundary (h)


def load(path):
    df = pd.read_csv(path)
    df["t_h"] = pd.to_numeric(df["t_elapsed_s"], errors="coerce") / 3600.0
    for c in df.columns:
        if c not in ("timestamp", "phase", "a_event", "b_event"):
            df[c] = pd.to_numeric(df[c], errors="coerce")
    return df


# ── Figure 1: path loss over time, per terrain, both UEs ─────────────────────
fig, axes = plt.subplots(4, 1, figsize=(3.5, 6.2), sharex=True)
for ax, (name, path) in zip(axes, WORLDS):
    df = load(path)
    ax.axvspan(PHASE_H, df["t_h"].max(), color=OI["grey"], alpha=0.12, lw=0)
    ax.axvline(PHASE_H, color=OI["vermillion"], ls="--", lw=1)
    for s, c, lab in (("a", OI["blue"], "UE $\\alpha$"), ("b", OI["green"], "UE $\\beta$")):
        y = df[f"{s}_ploss_dB"].rolling(9, min_periods=1, center=True).mean()
        ax.plot(df["t_h"], y, color=c, lw=1.1, label=lab)
    ax.axhline(35, color="black", ls=":", lw=0.8)
    ax.set_ylabel("Path loss (dB)")
    ax.set_ylim(0, 45)
    ax.text(0.02, 0.9, name, transform=ax.transAxes, fontsize=8.5,
            fontweight="bold", va="top")
    ax.text(PHASE_H/2, 42, "static gNB", ha="center", fontsize=6.5, color="0.4")
    ax.text((PHASE_H+df["t_h"].max())/2, 42, "nomadic gNB", ha="center",
            fontsize=6.5, color="0.4")
axes[0].legend(loc="lower right", ncol=2)
axes[0].text(0.98, 0.62, "RLF cliff", transform=axes[0].transAxes,
             ha="right", fontsize=6, color="0.3")
axes[-1].set_xlabel("mission time (h)")
fig.align_ylabels(axes)
fig.savefig(FIG / "fig_ts_ploss.pdf"); plt.close(fig)

# ── Figure 2: rolling C2 connectivity over time, per terrain, both UEs ────────
fig, axes = plt.subplots(4, 1, figsize=(3.5, 6.2), sharex=True)
for ax, (name, path) in zip(axes, WORLDS):
    df = load(path)
    ax.axvspan(PHASE_H, df["t_h"].max(), color=OI["grey"], alpha=0.12, lw=0)
    ax.axvline(PHASE_H, color=OI["vermillion"], ls="--", lw=1)
    for s, c, lab in (("a", OI["blue"], "UE $\\alpha$"), ("b", OI["green"], "UE $\\beta$")):
        conn = (df[f"{s}_c2_loss_pct"] < 100).astype(float)
        y = 100 * conn.rolling(12, min_periods=1, center=True).mean()
        ax.plot(df["t_h"], y, color=c, lw=1.1, label=lab)
    ax.set_ylabel("C2 avail. (\\%)")
    ax.set_ylim(-3, 108)
    ax.text(0.02, 0.28, name, transform=ax.transAxes, fontsize=8.5,
            fontweight="bold", va="top")
axes[0].legend(loc="lower right", ncol=2)
axes[0].text(PHASE_H/2, 12, "static", ha="center", fontsize=6.5, color="0.4")
axes[0].text((PHASE_H+load(WORLDS[0][1])["t_h"].max())/2, 12, "nomadic",
             ha="center", fontsize=6.5, color="0.4")
axes[-1].set_xlabel("mission time (h)")
fig.align_ylabels(axes)
fig.savefig(FIG / "fig_ts_c2.pdf"); plt.close(fig)

print("wrote fig_ts_ploss.pdf and fig_ts_c2.pdf to", FIG)
