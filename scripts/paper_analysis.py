#!/usr/bin/env python3
"""paper_analysis.py — full analysis + publication figures for the nomadic-gNB
multi-terrain paper. Reads the campaign CSVs, dumps a stats table, and writes
vector-PDF figures (Okabe-Ito colorblind-safe palette) to docs/paper/figures/.
"""
import csv, json, math, re
from pathlib import Path
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

ROOT = Path.home() / "sar_sim"
FIG = ROOT / "docs" / "paper" / "figures"
FIG.mkdir(parents=True, exist_ok=True)

# Okabe-Ito CVD-safe palette
OI = dict(black="#000000", orange="#E69F00", sky="#56B4E9", green="#009E73",
          yellow="#F0E442", blue="#0072B2", vermillion="#D55E00", purple="#CC79A7")
PHASE_C = {"static": OI["blue"], "nomadic": OI["vermillion"]}
WORLD_C = {"open_field": OI["blue"], "urban": OI["orange"], "forest": OI["green"],
           "hills": OI["sky"], "indoor": OI["purple"]}

plt.rcParams.update({
    "font.family": "serif", "font.size": 9, "axes.titlesize": 9,
    "axes.labelsize": 9, "legend.fontsize": 7.5, "xtick.labelsize": 8,
    "ytick.labelsize": 8, "axes.grid": True, "grid.alpha": 0.25,
    "grid.linewidth": 0.5, "axes.axisbelow": True, "figure.dpi": 150,
    "savefig.bbox": "tight", "pdf.fonttype": 42, "ps.fonttype": 42,
    "legend.frameon": False,
})

CAMP1 = ROOT / "logs" / "campaign_20260728_210058"
CAMP2 = ROOT / "logs" / "campaign_20260729_122440"
CAMP_IN = ROOT / "logs" / "campaign_20260730_112334"
WORLDS = [
    ("open_field", CAMP1 / "sar_open_field.csv", CAMP1 / "sar_open_field_research.log"),
    ("urban",      CAMP2 / "sar_urban.csv",      CAMP2 / "sar_urban_research.log"),
    ("forest",     CAMP2 / "sar_forest.csv",     CAMP2 / "sar_forest_research.log"),
    ("hills",      CAMP2 / "sar_hills.csv",      CAMP2 / "sar_hills_research.log"),
]
ORDER = ["open_field", "urban", "forest", "hills"]
NICE = {"open_field": "Open field", "urban": "Urban", "forest": "Forest",
        "hills": "Hills", "indoor": "Indoor"}


def load(path):
    rows = list(csv.DictReader(open(path)))
    for r in rows:
        for k, v in r.items():
            if k not in ("timestamp", "phase", "a_event", "b_event"):
                try: r[k] = float(v)
                except (ValueError, TypeError): r[k] = math.nan
    return rows


def col(rows, k):
    return np.array([r[k] for r in rows if isinstance(r.get(k), float)
                     and not math.isnan(r[k])], dtype=float)


def avail(rows, s):
    v = col(rows, f"{s}_c2_loss_pct")
    return 100.0 * np.mean(v < 100) if len(v) else math.nan


def logcount(log, pat):
    try:
        return len(re.findall(pat, log.read_text(errors="ignore")))
    except Exception:
        return 0


data = {w: load(p) for w, p, _ in WORLDS}
logs = {w: lg for w, _, lg in WORLDS}
stats = {}
for w in ORDER:
    rows = data[w]
    p1 = [r for r in rows if r.get("phase") == "1"]
    p2 = [r for r in rows if r.get("phase") == "2"]
    d = {"rows": len(rows), "n_static": len(p1), "n_nomadic": len(p2),
         "guard_tp": logcount(logs[w], r"teleported near gNB"),
         "reattach_failed": logcount(logs[w], r"reattach_failed"),
         "rlf_recovered": logcount(logs[w], r"rlf_recovered")}
    for ph, rs in (("static", p1), ("nomadic", p2)):
        for s in ("a", "b"):
            pre = f"{ph}_{s}"
            d[f"{pre}_c2"] = avail(rs, s)
            for m, key in (("ploss", f"{s}_ploss_dB"), ("sinr", f"{s}_ss_sinr_dB"),
                           ("rsrp", f"{s}_ss_rsrp_dBm"), ("tcp", f"{s}_tcp_tp_mbps"),
                           ("udp", f"{s}_udp_tp_mbps"), ("uloss", f"{s}_udp_lost_pct"),
                           ("jit", f"{s}_udp_jitter_ms"), ("bler", f"{s}_dl_bler"),
                           ("dist", f"{s}_d_slant_m"), ("c2rtt", f"{s}_c2_rtt_ms")):
                v = col(rs, key)
                d[f"{pre}_{m}"] = float(np.mean(v)) if len(v) else math.nan
    stats[w] = d

(ROOT / "docs" / "paper" / "stats.json").write_text(json.dumps(stats, indent=2))

# ── console summary ──────────────────────────────────────────────────────────
print(f"{'world':11}{'rows':>6}{'guardTP':>8}{'reatt_fail':>11}")
for w in ORDER:
    s = stats[w]
    print(f"{w:11}{s['rows']:6}{s['guard_tp']:8}{s['reattach_failed']:11}")
print("\nC2 availability (static -> nomadic), robot-averaged:")
for w in ORDER:
    s = stats[w]
    st = np.nanmean([s["static_a_c2"], s["static_b_c2"]])
    no = np.nanmean([s["nomadic_a_c2"], s["nomadic_b_c2"]])
    print(f"  {w:11} {st:5.1f}% -> {no:5.1f}%  (delta {no-st:+.1f} pp)")

# ============================ FIGURES =========================================

# Fig 1: C2 availability by world x phase (grouped bars, robot-avg with min/max)
fig, ax = plt.subplots(figsize=(3.5, 2.4))
x = np.arange(len(ORDER)); wbar = 0.36
for i, ph in enumerate(("static", "nomadic")):
    vals = [np.nanmean([stats[w][f"{ph}_a_c2"], stats[w][f"{ph}_b_c2"]]) for w in ORDER]
    lo = [min(stats[w][f"{ph}_a_c2"], stats[w][f"{ph}_b_c2"]) for w in ORDER]
    hi = [max(stats[w][f"{ph}_a_c2"], stats[w][f"{ph}_b_c2"]) for w in ORDER]
    err = [np.array(vals)-np.array(lo), np.array(hi)-np.array(vals)]
    ax.bar(x + (i-0.5)*wbar, vals, wbar, label=ph.capitalize()+" gNB",
           color=PHASE_C[ph], edgecolor="white", linewidth=0.6,
           yerr=err, ecolor="0.3", capsize=2, error_kw=dict(lw=0.7))
ax.set_xticks(x); ax.set_xticklabels([NICE[w] for w in ORDER])
ax.set_ylabel("C2 availability (\\%)"); ax.set_ylim(0, 105)
ax.legend(ncol=2, loc="lower center", bbox_to_anchor=(0.5, 1.0))
ax.grid(axis="x", visible=False)
fig.savefig(FIG / "fig_c2_avail.pdf"); plt.close(fig)

# Fig 2: nomadic effect (delta C2) per world, both robots (diverging bars)
fig, ax = plt.subplots(figsize=(3.5, 2.3))
xs = []; ys = []; cs = []; labs = []
for wi, w in enumerate(ORDER):
    for si, s in enumerate(("a", "b")):
        delta = stats[w][f"nomadic_{s}_c2"] - stats[w][f"static_{s}_c2"]
        pos = wi*2.4 + si
        xs.append(pos); ys.append(delta)
        cs.append(OI["green"] if delta > 0 else OI["vermillion"])
        labs.append(pos)
ax.axhline(0, color="0.4", lw=0.8)
ax.bar(xs, ys, 0.85, color=cs, edgecolor="white", linewidth=0.5)
ax.set_xticks([wi*2.4 + 0.5 for wi in range(len(ORDER))])
ax.set_xticklabels([NICE[w] for w in ORDER])
ax.set_ylabel("$\\Delta$ C2 avail., nomadic$-$static (pp)")
for xp, yp in zip(xs, ys):
    ax.annotate(f"{yp:+.0f}", (xp, yp), ha="center",
                va="bottom" if yp >= 0 else "top", fontsize=6.5,
                xytext=(0, 2 if yp >= 0 else -2), textcoords="offset points")
ax.grid(axis="x", visible=False); ax.margins(y=0.18)
fig.savefig(FIG / "fig_nomadic_effect.pdf"); plt.close(fig)

# Fig 3: ploss vs slant distance, per world (binned mean +/- std)
fig, ax = plt.subplots(figsize=(3.5, 2.5))
for w in ORDER:
    rows = data[w]
    d = np.concatenate([col(rows, "a_d_slant_m"), col(rows, "b_d_slant_m")])
    p = np.concatenate([col(rows, "a_ploss_dB"), col(rows, "b_ploss_dB")])
    m = min(len(d), len(p)); d, p = d[:m], p[:m]
    if not m: continue
    bins = np.linspace(np.nanmin(d), np.nanpercentile(d, 98), 9)
    idx = np.digitize(d, bins); xb=[]; yb=[]
    for b in range(1, len(bins)):
        sel = p[idx == b]
        if len(sel) >= 3:
            xb.append((bins[b-1]+bins[b])/2); yb.append(np.mean(sel))
    ax.plot(xb, yb, "-o", color=WORLD_C[w], ms=3, lw=1.4, label=NICE[w])
ax.set_xlabel("gNB$\\,$-$\\,$robot slant distance (m)")
ax.set_ylabel("Path loss / attenuation (dB)")
ax.legend(loc="upper left", ncol=2)
fig.savefig(FIG / "fig_ploss_dist.pdf"); plt.close(fig)

# Fig 4: TCP throughput distribution by world x phase (box)
fig, ax = plt.subplots(figsize=(3.5, 2.5))
positions=[]; boxdata=[]; colors=[]; ticks=[]; ticklabs=[]
for wi, w in enumerate(ORDER):
    for pi, ph in enumerate(("1", "2")):
        rs = [r for r in data[w] if r.get("phase") == ph]
        vals = np.concatenate([col(rs, "a_tcp_tp_mbps"), col(rs, "b_tcp_tp_mbps")])
        vals = vals[vals > 0]
        pos = wi*1.0 + (pi-0.5)*0.32
        positions.append(pos); boxdata.append(vals if len(vals) else [0])
        colors.append(PHASE_C["static" if ph=="1" else "nomadic"])
    ticks.append(wi*1.0); ticklabs.append(NICE[w])
bp = ax.boxplot(boxdata, positions=positions, widths=0.28, patch_artist=True,
                showfliers=False, medianprops=dict(color="black", lw=1))
for patch, c in zip(bp["boxes"], colors):
    patch.set_facecolor(c); patch.set_alpha(0.85); patch.set_edgecolor("white")
ax.set_xticks(ticks); ax.set_xticklabels(ticklabs)
ax.set_ylabel("TCP throughput (Mbit/s), served")
from matplotlib.patches import Patch
ax.legend(handles=[Patch(fc=PHASE_C["static"], label="Static gNB"),
                   Patch(fc=PHASE_C["nomadic"], label="Nomadic gNB")],
          loc="upper right", ncol=1)
ax.grid(axis="x", visible=False)
fig.savefig(FIG / "fig_throughput.pdf"); plt.close(fig)

# Fig 5: SINR CDF by world (both robots, all phases)
fig, ax = plt.subplots(figsize=(3.5, 2.5))
for w in ORDER:
    rows = data[w]
    v = np.concatenate([col(rows, "a_ss_sinr_dB"), col(rows, "b_ss_sinr_dB")])
    v = np.sort(v[~np.isnan(v)])
    if not len(v): continue
    ax.plot(v, np.linspace(0, 1, len(v)), color=WORLD_C[w], lw=1.5, label=NICE[w])
ax.set_xlabel("SS-SINR (dB)"); ax.set_ylabel("Empirical CDF")
ax.legend(loc="lower right", ncol=2); ax.set_ylim(0, 1)
fig.savefig(FIG / "fig_sinr_cdf.pdf"); plt.close(fig)

# Fig 6: link-recovery events per world (RLF recovered + reattach fails + guard)
fig, ax = plt.subplots(figsize=(3.5, 2.3))
metrics = [("rlf_recovered", OI["blue"], "RLF recovered"),
           ("guard_tp", OI["green"], "Guard rescues"),
           ("reattach_failed", OI["vermillion"], "Reattach failed")]
x = np.arange(len(ORDER)); wbar = 0.26
for i, (k, c, lab) in enumerate(metrics):
    ax.bar(x + (i-1)*wbar, [stats[w][k] for w in ORDER], wbar, color=c,
           edgecolor="white", linewidth=0.5, label=lab)
ax.set_xticks(x); ax.set_xticklabels([NICE[w] for w in ORDER])
ax.set_ylabel("Count over 6\\,h run"); ax.legend(loc="upper left")
ax.grid(axis="x", visible=False)
fig.savefig(FIG / "fig_events.pdf"); plt.close(fig)

# Fig 7: gNB trajectory static vs nomadic (top-down) from urban run
fig, (a1, a2) = plt.subplots(1, 2, figsize=(5.0, 2.4), sharex=True, sharey=True)
rows = data["urban"]
for ax_, ph, title in ((a1, "1", "Static gNB (phase 1)"),
                       (a2, "2", "Nomadic gNB (phase 2)")):
    rs = [r for r in rows if r.get("phase") == ph]
    ux = col(rs, "uav_x_m"); uy = col(rs, "uav_y_m")
    ax_.plot(ux, uy, color=OI["vermillion"], lw=0.6, alpha=0.7, zorder=1)
    ax_.scatter(ux, uy, s=4, color=OI["vermillion"], zorder=2, label="gNB")
    ax_.scatter(col(rs, "ax_m"), col(rs, "ay_m"), s=2, color=OI["blue"],
                alpha=0.4, label="Robot A")
    ax_.scatter(col(rs, "bx_m"), col(rs, "by_m"), s=2, color=OI["green"],
                alpha=0.4, label="Robot B")
    ax_.set_title(title); ax_.set_xlabel("x (m)"); ax_.set_aspect("equal")
a1.set_ylabel("y (m)"); a1.legend(loc="upper right", markerscale=2)
fig.savefig(FIG / "fig_gnb_traj.pdf"); plt.close(fig)

# Fig 8: indoor O2I -- ploss timeline (low-gNB smoke) showing cliff + retreat
fig, ax = plt.subplots(figsize=(3.5, 2.4))
irows = load(CAMP_IN / "sar_indoor.csv")
t = col(irows, "t_elapsed_s")
for s, c, lab in (("a", OI["blue"], "Robot A"), ("b", OI["green"], "Robot B")):
    p = np.array([r.get(f"{s}_ploss_dB", math.nan) for r in irows])
    tt = np.array([r.get("t_elapsed_s", math.nan) for r in irows])
    mask = ~np.isnan(p)
    ax.plot(tt[mask], p[mask], "-o", color=c, ms=3, lw=1.2, label=lab)
ax.axhline(35, color=OI["vermillion"], ls="--", lw=1, label="RLF cliff ($\\approx$35 dB)")
ax.set_xlabel("time (s)"); ax.set_ylabel("Path loss (dB)")
ax.legend(loc="lower right"); ax.set_title("Indoor O2I: at the cliff throughout")
fig.savefig(FIG / "fig_indoor.pdf"); plt.close(fig)

print("\nWrote 8 figures to", FIG)
print("Wrote stats to docs/paper/stats.json")
