#!/usr/bin/env python3
"""Generate IEEE-paper figures: fixed-vs-moving comparison and per-altitude
panels (each UAV altitude on its own axis), plus the analytic model overlay.

Outputs to logs/paper_figs/.
"""
import math
import sys
from pathlib import Path
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

LOGS = Path.home() / "sar_sim" / "logs"
OUT = LOGS / "paper_figs"
OUT.mkdir(exist_ok=True)

C_ALPHA, C_BRAVO = "#2a78d6", "#1baf7a"
C_FIXED = "#52514e"
ALT_COLORS = {20: "#e34948", 40: "#eda100", 60: "#2a78d6"}  # red/amber/blue by height
GRID = "#deddd7"
plt.rcParams.update({
    "figure.facecolor": "white", "axes.facecolor": "white",
    "axes.edgecolor": "#b8b7b0", "axes.grid": True, "grid.color": GRID,
    "grid.linewidth": 0.6, "axes.axisbelow": True, "font.size": 10,
    "figure.dpi": 140, "font.family": "sans-serif",
})

# ── A2G model (mirror of channel_bridge.py) ─────────────────────────────────
FREQ_GHZ, UE_H = 3.31968, 0.5
A_C, B_C, ETA_LOS, ETA_NLOS = 9.61, 0.16, 1.0, 20.0
REF_H, REF_D = 30.0, 50.0
BASE = {"a": 8.0, "b": 13.0}

def a2g(d, h):
    d3 = max(math.hypot(d, h - UE_H), 1.0)
    fspl = 20*math.log10(d3) + 20*math.log10(FREQ_GHZ*1e9) - 147.55
    th = math.degrees(math.atan2(h - UE_H, max(d, 0.1)))
    los = 1.0/(1.0 + A_C*math.exp(-B_C*(th - A_C)))
    return fspl + los*ETA_LOS + (1-los)*ETA_NLOS

PL_REF = a2g(REF_D, REF_H)

def model_inj(d, h, side):
    return max(0.0, min(45.0, BASE[side] + a2g(d, h) - PL_REF))

def binline(ax, d, y, color, label, edges=None):
    edges = edges if edges is not None else np.arange(0, 155, 12.5)
    dfb = pd.DataFrame({"d": d, "y": y}).dropna()
    dfb["bin"] = pd.cut(dfb.d, edges)
    g = dfb.groupby("bin", observed=True)["y"].mean()
    cx = [iv.mid for iv in g.index]
    ax.plot(cx, g.values, color=color, lw=2.2, label=label, zorder=5,
            marker="o", ms=4)

sur = pd.read_csv(LOGS/"patrol_20260712_015213.csv")
nom = pd.read_csv(LOGS/"patrol_20260712_032525.csv")
nom_alt = nom.uav_z_m.round(-1)

def live(df, pre):
    return df[df[f"{pre}_ping_loss_pct"] == 0]

# ── FIG 1: fixed vs moving, side by side (alpha TCP vs distance) ─────────────
fig, (axL, axR) = plt.subplots(1, 2, figsize=(9.2, 3.8), sharey=True)
la = live(sur, "a")
axL.scatter(la.a_d_horiz_m, la.a_tcp_tp_mbps, s=10, color=C_ALPHA, alpha=0.35)
binline(axL, la.a_d_horiz_m, la.a_tcp_tp_mbps, C_FIXED, "mean")
axL.set_title("(a) Fixed UAV (40 m)", fontsize=10.5, loc="left", weight="bold")
axL.set_xlabel("horizontal distance to UAV (m)")
axL.set_ylabel("TCP goodput (Mbit/s)")
for z in (20, 40, 60):
    m = live(nom[nom_alt == z], "a")
    binline(axR, m.a_d_horiz_m, m.a_tcp_tp_mbps, ALT_COLORS[z], f"UAV {z} m")
axR.set_title("(b) Nomadic UAV, by altitude", fontsize=10.5, loc="left", weight="bold")
axR.set_xlabel("horizontal distance to UAV (m)")
axR.legend(frameon=False, fontsize=9, title="drone height")
fig.tight_layout()
fig.savefig(OUT/"fig_fixed_vs_moving.png", bbox_inches="tight")
plt.close(fig)

# ── FIG 2: per-altitude panels — TCP vs distance, each altitude on own axis ──
fig, axes = plt.subplots(1, 3, figsize=(10.5, 3.5), sharey=True, sharex=True)
for ax, z in zip(axes, (20, 40, 60)):
    for pre, c, nm in [("a", C_ALPHA, "Alpha"), ("b", C_BRAVO, "Bravo")]:
        m = live(nom[nom_alt == z], pre)
        ax.scatter(m[f"{pre}_d_horiz_m"], m[f"{pre}_tcp_tp_mbps"], s=9,
                   color=c, alpha=0.30)
        binline(ax, m[f"{pre}_d_horiz_m"], m[f"{pre}_tcp_tp_mbps"], c, nm)
    ax.set_title(f"UAV altitude = {z} m", fontsize=10.5, weight="bold")
    ax.set_xlabel("horizontal distance (m)")
    ax.axvline({20:101,40:131,60:163}[z], color="#b03030", ls=":", lw=1.3)
axes[0].set_ylabel("TCP goodput (Mbit/s)")
axes[0].legend(frameon=False, fontsize=9)
axes[2].text(0.97, 0.9, "dotted = safe radius", transform=axes[2].transAxes,
             ha="right", fontsize=8, color="#b03030")
fig.tight_layout()
fig.savefig(OUT/"fig_per_altitude_tput.png", bbox_inches="tight")
plt.close(fig)

# ── FIG 3: per-altitude panels — measured path loss vs distance + model ─────
fig, axes = plt.subplots(1, 3, figsize=(10.5, 3.5), sharey=True, sharex=True)
dd = np.linspace(2, 150, 200)
for ax, z in zip(axes, (20, 40, 60)):
    for pre, c, nm in [("a", C_ALPHA, "Alpha"), ("b", C_BRAVO, "Bravo")]:
        m = live(nom[nom_alt == z], pre)
        ax.scatter(m[f"{pre}_d_horiz_m"], m[f"{pre}_ploss_dB"], s=9,
                   color=c, alpha=0.30, label=f"{nm} (meas.)")
        ax.plot(dd, [model_inj(x, z, pre) for x in dd], color=c, lw=1.6,
                ls="--", zorder=6)
    ax.set_title(f"UAV altitude = {z} m", fontsize=10.5, weight="bold")
    ax.set_xlabel("horizontal distance (m)")
axes[0].set_ylabel("injected path loss (dB)")
axes[0].plot([], [], color="#666", ls="--", label="A2G model")
axes[0].legend(frameon=False, fontsize=8.5)
fig.tight_layout()
fig.savefig(OUT/"fig_per_altitude_ploss.png", bbox_inches="tight")
plt.close(fig)

# ── FIG 4: fixed-vs-nomadic@40 consistency (validation) ─────────────────────
fig, ax = plt.subplots(figsize=(5.6, 3.8))
binline(ax, live(sur,"a").a_d_horiz_m, live(sur,"a").a_tcp_tp_mbps,
        C_FIXED, "Fixed run @40 m")
m40 = live(nom[nom_alt==40], "a")
binline(ax, m40.a_d_horiz_m, m40.a_tcp_tp_mbps, "#eb6834",
        "Nomadic run, 40 m segment")
ax.set_xlabel("horizontal distance to UAV (m)")
ax.set_ylabel("Alpha TCP goodput (Mbit/s)")
ax.set_title("Cross-run consistency at equal altitude", fontsize=10.5,
             weight="bold", loc="left")
ax.legend(frameon=False, fontsize=9)
fig.tight_layout()
fig.savefig(OUT/"fig_consistency.png", bbox_inches="tight")
plt.close(fig)

print("wrote:", *[p.name for p in sorted(OUT.glob("*.png"))])
