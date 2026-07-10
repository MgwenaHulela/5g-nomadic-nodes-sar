#!/usr/bin/env python3
"""
analyze_patrol.py — post-run analysis of an auto_patrol CSV (free/both schema).

Produces a text report + PNG figures in logs/analysis_<csv-stem>/:
  1. ploss vs distance, both robots, against the analytic A2G model
  2. throughput vs distance (TCP / UDP-video), small multiples
  3. latency vs distance (uplink RTT / C2 round trip), small multiples
  4. timeline: distance, ploss, TCP, with outage samples marked

Data hygiene applied (see docs/SYSTEM_ARCHITECTURE.md §7):
  - rows with ping_loss==100 -> RSRP/SINR treated as invalid (parser
    holds last good value during outages); TCP/UDP zeros are REAL
  - outage samples marked with X, annotated, never color-only

Usage:
  python3 scripts/analyze_patrol.py                     # latest patrol_*.csv
  python3 scripts/analyze_patrol.py logs/patrol_X.csv   # specific run(s)
"""

import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

# ── palette (validated defaults; see dataviz references/palette.md) ──────────
C_ALPHA = "#2a78d6"   # categorical slot 1 (blue)
C_BRAVO = "#1baf7a"   # categorical slot 2 (aqua)
C_MODEL = "#52514e"   # neutral ink for the analytic model curve
C_TEXT = "#0b0b0b"
C_GRID = "#d9d8d4"
SURFACE = "#fcfcfb"

plt.rcParams.update({
    "figure.facecolor": SURFACE, "axes.facecolor": SURFACE,
    "axes.edgecolor": C_GRID, "axes.labelcolor": C_TEXT,
    "text.color": C_TEXT, "xtick.color": "#52514e", "ytick.color": "#52514e",
    "axes.grid": True, "grid.color": C_GRID, "grid.linewidth": 0.6,
    "axes.axisbelow": True, "font.size": 10, "figure.dpi": 130,
})

# ── A2G model, mirrored from channel_bridge.py (keep in sync) ────────────────
FREQ_GHZ, UE_H, UAV_DEFAULT_H = 3.31968, 0.5, 30.0
A_C, B_C, ETA_LOS, ETA_NLOS = 9.61, 0.16, 1.0, 20.0
UAV_H = 40.0                       # actual UAV altitude in the world
REF_D = 50.0                       # bridge reference horizontal distance
BASELINES_UL = {"a": 20.0, "b": 25.0}


def a2g_pl(d_horiz, h_uav):
    d3 = max(math.hypot(d_horiz, h_uav - UE_H), 1.0)
    fspl = 20 * math.log10(d3) + 20 * math.log10(FREQ_GHZ * 1e9) - 147.55
    theta = math.degrees(math.atan2(h_uav - UE_H, max(d_horiz, 0.1)))
    p_los = 1.0 / (1.0 + A_C * math.exp(-B_C * (theta - A_C)))
    return fspl + p_los * ETA_LOS + (1 - p_los) * ETA_NLOS


PL_REF = a2g_pl(REF_D, UAV_DEFAULT_H)  # bridge computes ref with default h


def expected_ul_ploss(d, side):
    """What the bridge would send (pre-EMA, unclamped-in-range)."""
    delta = a2g_pl(d, UAV_H) - PL_REF
    return max(10.0, min(80.0, BASELINES_UL[side] + delta))


# ── per-robot column map (free/both schema) ──────────────────────────────────
COLS = {
    "a": dict(x="ax_m", y="ay_m", d="a_d_horiz_m", pl="a_ploss_dB",
              tcp="a_tcp_tp_mbps", udp="a_udp_tp_mbps", jit="a_udp_jitter_ms",
              udploss="a_udp_lost_pct", rtt="a_rtt_avg_ms",
              pingloss="a_ping_loss_pct", rsrp="a_ss_rsrp_dBm",
              sinr="a_ss_sinr_dB", c2rtt="a_c2_rtt_ms", c2loss="a_c2_loss_pct"),
    "b": dict(x="bx_m", y="by_m", d="b_d_horiz_m", pl="b_ploss_dB",
              tcp="b_tcp_tp_mbps", udp="b_udp_tp_mbps", jit="b_udp_jitter_ms",
              udploss="b_udp_lost_pct", rtt="b_rtt_avg_ms",
              pingloss="b_ping_loss_pct", rsrp="b_ss_rsrp_dBm",
              sinr="b_ss_sinr_dB", c2rtt="b_c2_rtt_ms", c2loss="b_c2_loss_pct"),
}
NAMES = {"a": "Alpha", "b": "Bravo"}
COLORS = {"a": C_ALPHA, "b": C_BRAVO}


def load(paths):
    frames = []
    for p in paths:
        df = pd.read_csv(p)
        if "a_c2_rtt_ms" not in df.columns:
            print(f"  skipping {p} (old schema, no C2 columns)")
            continue
        df["source"] = Path(p).name
        frames.append(df)
    if not frames:
        sys.exit("No usable CSVs (need the free/both schema with C2 columns).")
    return pd.concat(frames, ignore_index=True)


def tidy(df):
    """One row per robot-sample with normalized column names + flags."""
    out = []
    for side, c in COLS.items():
        t = pd.DataFrame({
            "t": df["t_elapsed_s"], "side": side,
            "d": df[c["d"]], "pl": df[c["pl"]],
            "tcp": df[c["tcp"]], "udp": df[c["udp"]],
            "jit": df[c["jit"]], "udploss": df[c["udploss"]],
            "rtt": df[c["rtt"]], "pingloss": df[c["pingloss"]],
            "rsrp": df[c["rsrp"]], "sinr": df[c["sinr"]],
            "c2rtt": df[c["c2rtt"]], "c2loss": df[c["c2loss"]],
            "source": df["source"],
        })
        out.append(t)
    t = pd.concat(out, ignore_index=True)
    # outage flag: total ping loss OR total C2 loss with no traffic
    t["outage"] = (t["pingloss"] >= 100) | ((t["c2loss"] >= 100) & (t["tcp"] == 0))
    # radio metrics are stale during outages — invalidate
    t.loc[t["outage"], ["rsrp", "sinr"]] = np.nan
    # rtt/c2rtt of 0 means "no measurement", not zero latency
    t.loc[t["rtt"] <= 0, "rtt"] = np.nan
    t.loc[t["c2rtt"] <= 0, "c2rtt"] = np.nan
    return t


def binned(t, col, edges):
    g = t.groupby([pd.cut(t["d"], edges, right=False), "side"], observed=True)[col]
    return g.mean().unstack("side")


def fig_ploss(t, outdir):
    fig, ax = plt.subplots(figsize=(7.2, 4.4))
    dd = np.linspace(2, max(150, t["d"].max() * 1.05), 300)
    for side in ("a", "b"):
        ax.plot(dd, [expected_ul_ploss(x, side) for x in dd],
                color=C_MODEL, lw=1.4, ls="--", zorder=2)
        s = t[t["side"] == side]
        ax.scatter(s["d"], s["pl"], s=22, color=COLORS[side], zorder=3,
                   label=f"{NAMES[side]} (measured UL ploss)")
        out = s[s["outage"]]
        ax.scatter(out["d"], out["pl"], marker="x", s=60, color=COLORS[side],
                   lw=2, zorder=4)
    ax.plot([], [], color=C_MODEL, lw=1.4, ls="--",
            label="A2G model (FSPL + LoS/NLoS)")
    ax.scatter([], [], marker="x", s=60, color="#52514e", lw=2,
               label="outage sample")
    ax.set_xlabel("horizontal distance to UAV (m)")
    ax.set_ylabel("path loss injected into OAI (dB)")
    ax.set_title("Measured vs modeled A2G path loss")
    ax.legend(frameon=False, fontsize=9)
    fig.tight_layout()
    fig.savefig(outdir / "1_ploss_vs_distance.png")
    plt.close(fig)


def fig_throughput(t, outdir):
    fig, axes = plt.subplots(2, 1, figsize=(7.2, 6.0), sharex=True)
    for ax, col, title, ylab in (
        (axes[0], "tcp", "TCP goodput (bulk sensor upload)", "Mbps"),
        (axes[1], "udp", "UDP video feed delivered (8 Mbps offered)", "Mbps"),
    ):
        for side in ("a", "b"):
            s = t[t["side"] == side].sort_values("d")
            ax.scatter(s["d"], s[col], s=20, color=COLORS[side], alpha=0.85,
                       label=NAMES[side])
            out = s[s["outage"]]
            ax.scatter(out["d"], out[col], marker="x", s=55,
                       color=COLORS[side], lw=2)
        ax.set_ylabel(ylab)
        ax.set_title(title, fontsize=10, loc="left")
    axes[0].legend(frameon=False, fontsize=9)
    axes[1].set_xlabel("horizontal distance to UAV (m)")
    fig.suptitle("Uplink throughput vs distance (X = outage sample)", y=0.995)
    fig.tight_layout()
    fig.savefig(outdir / "2_throughput_vs_distance.png")
    plt.close(fig)


def fig_latency(t, outdir):
    fig, axes = plt.subplots(2, 1, figsize=(7.2, 6.0), sharex=True)
    for ax, col, title in (
        (axes[0], "rtt", "Uplink-path ping RTT (robot → base)"),
        (axes[1], "c2rtt", "C2 round trip (base → robot → base, over air both ways)"),
    ):
        for side in ("a", "b"):
            s = t[t["side"] == side]
            ax.scatter(s["d"], s[col], s=20, color=COLORS[side], alpha=0.85,
                       label=NAMES[side])
        ax.set_yscale("log")
        ax.set_ylabel("ms (log)")
        ax.set_title(title, fontsize=10, loc="left")
    axes[0].legend(frameon=False, fontsize=9)
    axes[1].set_xlabel("horizontal distance to UAV (m)")
    fig.suptitle("Latency vs distance — C2 RTT spikes precede loss", y=0.995)
    fig.tight_layout()
    fig.savefig(outdir / "3_latency_vs_distance.png")
    plt.close(fig)


def fig_timeline(t, outdir):
    fig, axes = plt.subplots(3, 1, figsize=(8.4, 7.2), sharex=True)
    specs = (
        (axes[0], "d", "distance to UAV (m)"),
        (axes[1], "pl", "injected path loss (dB)"),
        (axes[2], "tcp", "TCP goodput (Mbps)"),
    )
    for ax, col, ylab in specs:
        for side in ("a", "b"):
            s = t[t["side"] == side].sort_values("t")
            ax.plot(s["t"] / 60, s[col], color=COLORS[side], lw=1.8,
                    label=NAMES[side])
            out = s[s["outage"]]
            ax.scatter(out["t"] / 60, out[col], marker="x", s=55,
                       color=COLORS[side], lw=2, zorder=4)
        ax.set_ylabel(ylab)
    axes[0].legend(frameon=False, fontsize=9, ncol=2)
    axes[2].set_xlabel("elapsed time (min)")
    fig.suptitle("Patrol timeline (X = outage sample)", y=0.995)
    fig.tight_layout()
    fig.savefig(outdir / "4_timeline.png")
    plt.close(fig)


def report(t, outdir):
    lines = []
    say = lines.append
    say("=== SAR patrol analysis ===")
    say(f"sources: {', '.join(sorted(t['source'].unique()))}")
    say(f"span: {t['t'].max()/60:.1f} min, {len(t)//2} samples/robot")
    edges = [0, 25, 50, 75, 100, 125, 200]
    for side in ("a", "b"):
        s = t[t["side"] == side]
        ok = s[~s["outage"]]
        say(f"\n── {NAMES[side]} ──")
        say(f"  distance range: {s['d'].min():.0f}–{s['d'].max():.0f} m")
        say(f"  outage samples: {int(s['outage'].sum())}/{len(s)}")
        if len(ok):
            say(f"  TCP: mean {ok['tcp'].mean():.1f} Mbps, "
                f"min {ok['tcp'].min():.1f} (at {ok.loc[ok['tcp'].idxmin(),'d']:.0f} m)")
            say(f"  UDP video: delivered ≥7.9 Mbps in "
                f"{(ok['udp'] >= 7.9).mean()*100:.0f}% of live samples")
            say(f"  C2 RTT: median {ok['c2rtt'].median():.0f} ms, "
                f"p95 {ok['c2rtt'].quantile(0.95):.0f} ms")
        # farthest healthy / first trouble
        healthy = s[(s["tcp"] >= 2) & (s["c2loss"] == 0)]
        if len(healthy):
            say(f"  farthest fully-healthy sample: {healthy['d'].max():.0f} m")
        trouble = s[(s["c2loss"] > 0) | s["outage"]]
        if len(trouble):
            say(f"  first C2 trouble at: {trouble['d'].min():.0f} m")
        # model residuals (live rows only)
        live = ok.dropna(subset=["pl"])
        if len(live):
            resid = live["pl"] - live.apply(
                lambda r: expected_ul_ploss(r["d"], side), axis=1)
            say(f"  ploss vs model: mean residual {resid.mean():+.1f} dB, "
                f"MAE {resid.abs().mean():.1f} dB (EMA lag explains most)")
    say("\n── binned means (TCP Mbps / C2 RTT ms) ──")
    tb = binned(t[~t["outage"]], "tcp", edges).round(1)
    cb = binned(t, "c2rtt", edges).round(0)
    for iv in tb.index:
        a_t = tb.loc[iv].get("a", np.nan)
        b_t = tb.loc[iv].get("b", np.nan)
        a_c = cb.loc[iv].get("a", np.nan) if iv in cb.index else np.nan
        b_c = cb.loc[iv].get("b", np.nan) if iv in cb.index else np.nan
        say(f"  {str(iv):12s}  alpha {a_t:>5} / {a_c:>4}   bravo {b_t:>5} / {b_c:>4}")
    text = "\n".join(lines)
    (outdir / "report.txt").write_text(text + "\n")
    print(text)


def main():
    logs = Path.home() / "sar_sim" / "logs"
    paths = sys.argv[1:] or [str(sorted(logs.glob("patrol_*.csv"))[-1])]
    df = load(paths)
    t = tidy(df)
    outdir = logs / f"analysis_{Path(paths[-1]).stem}"
    outdir.mkdir(exist_ok=True)
    fig_ploss(t, outdir)
    fig_throughput(t, outdir)
    fig_latency(t, outdir)
    fig_timeline(t, outdir)
    report(t, outdir)
    print(f"\nFigures + report: {outdir}/")


if __name__ == "__main__":
    main()
