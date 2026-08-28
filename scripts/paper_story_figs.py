#!/usr/bin/env python3
"""paper_story_figs.py — result graphs, every one comparing STATIC vs NOMADIC
gNB, per terrain: video streaming, link adaptation (MCS/BLER), control (C2),
and throughput/coverage vs UE-gNB distance."""
from pathlib import Path
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Patch

ROOT = Path.home() / "sar_sim"
FIG = ROOT / "docs" / "paper" / "figures"
OI = dict(blue="#0072B2", green="#009E73", vermillion="#D55E00", grey="#888888")
PH = {"1": ("Static gNB", OI["blue"]), "2": ("Nomadic gNB", OI["vermillion"])}
plt.rcParams.update({
    "font.family": "serif", "font.size": 9, "axes.titlesize": 8.5,
    "axes.labelsize": 8.5, "legend.fontsize": 7.5, "xtick.labelsize": 7.5,
    "ytick.labelsize": 7.5, "axes.grid": True, "grid.alpha": 0.25,
    "grid.linewidth": 0.5, "axes.axisbelow": True, "figure.dpi": 150,
    "savefig.bbox": "tight", "pdf.fonttype": 42, "legend.frameon": False,
})
C1 = ROOT / "logs" / "campaign_20260728_210058"
C2 = ROOT / "logs" / "campaign_20260729_122440"
WORLDS = [("Open field", C1 / "sar_open_field.csv"),
          ("Urban", C2 / "sar_urban.csv"),
          ("Forest", C2 / "sar_forest.csv"),
          ("Hills", C2 / "sar_hills.csv")]
NAMES = [n for n, _ in WORLDS]


def load(path):
    df = pd.read_csv(path)
    for c in df.columns:
        if c not in ("timestamp", "phase", "a_event", "b_event"):
            df[c] = pd.to_numeric(df[c], errors="coerce")
    df["phase"] = df["phase"].astype(str)
    return df

data = {n: load(p) for n, p in WORLDS}


def both(df, metric):
    return pd.concat([df[f"a_{metric}"], df[f"b_{metric}"]])


def scalar(agg):
    """agg(sub_df)->value; returns {world:{phase:value}} pooling both UEs."""
    out = {}
    for n, df in data.items():
        out[n] = {}
        for ph in ("1", "2"):
            out[n][ph] = agg(df[df["phase"] == ph])
    return out


def grouped_bars(ax, table, ylabel, title=None, annot=None):
    x = np.arange(len(NAMES)); w = 0.38
    for i, ph in enumerate(("1", "2")):
        vals = [table[n][ph] for n in NAMES]
        ax.bar(x + (i-0.5)*w, vals, w, color=PH[ph][1], edgecolor="white",
               linewidth=0.6, label=PH[ph][0])
        if annot:
            for xi, v in enumerate(vals):
                if annot(v):
                    ax.annotate(f"{v:.0f}", (x[xi]+(i-0.5)*w, v), ha="center",
                                va="bottom", fontsize=6, color="0.3")
    ax.set_xticks(x); ax.set_xticklabels(NAMES, fontsize=7)
    ax.set_ylabel(ylabel); ax.grid(axis="x", visible=False)
    if title: ax.set_title(title)


def dist_panels(metric_col, ylabel, suptitle, fname, ylim=None, connectivity=False):
    """2x2 small multiples; per terrain a static and a nomadic curve vs distance."""
    fig, axes = plt.subplots(2, 2, figsize=(5.2, 3.8), sharex=True, sharey=True)
    for ax, n in zip(axes.ravel(), NAMES):
        df = data[n]
        for ph in ("1", "2"):
            sub = df[df["phase"] == ph]
            d = both(sub, "d_slant_m").values
            if connectivity:
                y = (both(sub, "c2_loss_pct") < 100).astype(float).values * 100
            else:
                y = both(sub, metric_col).values
            m = ~(np.isnan(d) | np.isnan(y)); d, y = d[m], y[m]
            if len(d) < 6: continue
            bins = np.linspace(np.nanpercentile(d, 2), np.nanpercentile(d, 98), 8)
            idx = np.digitize(d, bins); xs, ys = [], []
            for b in range(1, len(bins)):
                sel = y[idx == b]
                if len(sel) >= 3:
                    xs.append((bins[b-1]+bins[b])/2); ys.append(np.mean(sel))
            ax.plot(xs, ys, "-o", color=PH[ph][1], ms=3, lw=1.4, label=PH[ph][0])
        ax.text(0.04, 0.06, n, transform=ax.transAxes, fontsize=8,
                fontweight="bold", va="bottom")
        if ylim: ax.set_ylim(*ylim)
    axes[0, 0].legend(loc="upper right", fontsize=7)
    for ax in axes[-1, :]: ax.set_xlabel("UE–gNB slant distance (m)")
    for ax in axes[:, 0]: ax.set_ylabel(ylabel)
    fig.suptitle(suptitle, fontsize=9, y=1.0)
    fig.savefig(FIG / fname); plt.close(fig)


def med_rtt(sub):
    r = sub.loc[both(sub, "c2_loss_pct").index]  # align
    rtt = pd.concat([sub["a_c2_rtt_ms"].where(sub["a_c2_loss_pct"] < 100),
                     sub["b_c2_rtt_ms"].where(sub["b_c2_loss_pct"] < 100)])
    return rtt.median()


# ── 1. Video streaming: delivered rate + loss, static vs nomadic per terrain ──
fig, (a1, a2) = plt.subplots(1, 2, figsize=(5.4, 2.4))
grouped_bars(a1, scalar(lambda s: both(s, "udp_tp_mbps").mean()),
             "Delivered video (Mbit/s)", "What the operator sees")
a1.axhline(8, color="0.4", ls="--", lw=0.8); a1.set_ylim(0, 9.5)
a1.text(len(NAMES)-0.5, 8.1, "offered", ha="right", fontsize=6, color="0.4")
a1.legend(loc="lower left")
grouped_bars(a2, scalar(lambda s: both(s, "udp_lost_pct").mean()),
             "Video loss (\\%)", "Video packet loss")
a2.set_ylim(0, 100)
fig.savefig(FIG / "fig_video.pdf"); plt.close(fig)

# ── 2. Link adaptation: spectral efficiency + BLER, static vs nomadic ────────
fig, (a1, a2) = plt.subplots(1, 2, figsize=(5.4, 2.4))
grouped_bars(a1, scalar(lambda s: both(s, "ul_bits_per_sym").mean()),
             "UL spectral eff. (bits/sym)", "Scheduler adapts MCS")
a1.legend(loc="lower left")
grouped_bars(a2, scalar(lambda s: both(s, "dl_bler").mean()),
             "DL BLER (\\%)", "Block error rate")
fig.savefig(FIG / "fig_linkadapt.pdf"); plt.close(fig)

# ── 3. Control: C2 availability + C2 RTT, static vs nomadic per terrain ───────
fig, (a1, a2) = plt.subplots(1, 2, figsize=(5.4, 2.4))
grouped_bars(a1, scalar(lambda s: 100*((both(s, "c2_loss_pct") < 100).mean())),
             "C2 availability (\\%)", "Controllability")
a1.set_ylim(0, 105); a1.legend(loc="lower left")
grouped_bars(a2, scalar(med_rtt), "C2 RTT median (ms)", "Command latency")
fig.savefig(FIG / "fig_control.pdf"); plt.close(fig)

# ── 4. Throughput as the UE moves: TCP vs distance, static vs nomadic ────────
dist_panels("tcp_tp_mbps", "TCP goodput (Mbit/s)",
            "Throughput vs. distance: static vs. nomadic gNB",
            "fig_tput_distance.pdf")

# ── 5. Coverage as the UE moves: C2 availability vs distance ─────────────────
dist_panels(None, "C2 availability (\\%)",
            "Coverage vs. distance: static vs. nomadic gNB",
            "fig_coverage_distance.pdf", ylim=(0, 105), connectivity=True)

print("wrote fig_video, fig_linkadapt, fig_control, fig_tput_distance,"
      " fig_coverage_distance (all static-vs-nomadic per terrain)")
