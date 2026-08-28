#!/usr/bin/env python3
"""paper_coverage_maps.py — spatial coverage field over the search area, per
terrain, for a STATIC drone vs a NOMADIC drone (time-averaged): 'coverage as the
drone moves'. Evaluates the same A2G + terrain channel used by the bridge."""
import sys, math, random
from pathlib import Path
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

ROOT = Path.home() / "sar_sim"
FIG = ROOT / "docs" / "paper" / "figures"
sys.path.insert(0, str(ROOT / "worlds"))
from urban_buildings import any_blocked, BUILDINGS
from forest_trees import foliage_depth, foliage_loss_db, DEPTH_NLOS_M
from terrain_hills import terrain_diffraction_db, DIFFRACTION_NLOS_DB
from indoor_building import indoor_loss

# A2G core (mirror of channel_bridge / auto_patrol, kept in sync)
FREQ_GHZ, UE_H = 3.31968, 0.5
A_C, B_C, ETA_LOS, ETA_NLOS = 9.61, 0.16, 1.0, 20.0
BASELINE = 13.0          # weaker UE (bravo) uplink baseline
CLIFF = 30.0             # attenuation above which the link is unusable (safe margin)
random.seed(0)


def a2g(d_h, h, blocked, excess):
    d3 = max(math.hypot(d_h, h - UE_H), 1.0)
    fspl = 20*math.log10(d3) + 20*math.log10(FREQ_GHZ*1e9) - 147.55
    theta = math.degrees(math.atan2(h - UE_H, max(d_h, 0.1)))
    p_los = 0.0 if blocked else 1.0/(1.0 + A_C*math.exp(-B_C*(theta - A_C)))
    return fspl + p_los*ETA_LOS + (1 - p_los)*ETA_NLOS + excess

PL_REF = a2g(50.0, 30.0, False, 0.0)


def terrain_channel(world, uav, ue):
    if world == "open_field":
        return (False, 0.0)
    if world == "urban":
        return (any_blocked(uav, ue), 0.0)
    if world == "forest":
        depth = foliage_depth(uav, ue)
        return (depth > DEPTH_NLOS_M, foliage_loss_db(depth))
    if world == "hills":
        loss = terrain_diffraction_db(uav, ue)
        return (loss > DIFFRACTION_NLOS_DB, loss)
    return (False, 0.0)


def atten(world, uav, x, y):
    ue = (x, y, UE_H)
    d_h = math.hypot(uav[0]-x, uav[1]-y)
    blocked, excess = terrain_channel(world, uav, ue)
    A = BASELINE + (a2g(d_h, uav[2], blocked, excess) - PL_REF)
    return min(max(A, 0.0), 45.0)


WORLDS = [("Open field", "open_field"), ("Urban", "urban"),
          ("Forest", "forest"), ("Hills", "hills")]
GN = 46
XY = np.linspace(-90, 90, GN)
STATIC = (0.0, 0.0, 40.0)
# nomadic drone positions sampled from the phase-2 box (+/-60 xy, z 20-60)
NOMAD = [(random.uniform(-60, 60), random.uniform(-60, 60),
          random.uniform(20, 60)) for _ in range(14)]

fig, axes = plt.subplots(len(WORLDS), 2, figsize=(5.2, 8.4))
for r, (nice, w) in enumerate(WORLDS):
    # static: attenuation field
    Astat = np.array([[atten(w, STATIC, x, y) for x in XY] for y in XY])
    # nomadic: fraction of drone positions covering each cell
    cov = np.zeros((GN, GN))
    for uav in NOMAD:
        cov += np.array([[1.0 if atten(w, uav, x, y) < CLIFF else 0.0
                          for x in XY] for y in XY])
    cov = 100.0 * cov / len(NOMAD)

    ax0, ax1 = axes[r]
    im0 = ax0.pcolormesh(XY, XY, Astat, cmap="viridis_r", vmin=0, vmax=45,
                         shading="auto")
    ax0.contour(XY, XY, Astat, levels=[CLIFF], colors="white", linewidths=1.2)
    ax1p = ax1.pcolormesh(XY, XY, cov, cmap="viridis", vmin=0, vmax=100,
                          shading="auto")
    for ax in (ax0, ax1):
        if w == "urban":
            for cx, cy, bw, bd, _h in BUILDINGS:
                ax.add_patch(plt.Rectangle((cx-bw/2, cy-bd/2), bw, bd,
                             fill=False, ec="white", lw=0.4, alpha=0.6))
        ax.set_aspect("equal"); ax.set_xticks([-80,0,80]); ax.set_yticks([-80,0,80])
        ax.tick_params(labelsize=6)
    ax0.plot(0, 0, "*", color="red", ms=9, mec="white", mew=0.5)
    ax0.set_ylabel(f"{nice}\n\ny (m)", fontsize=8)
    if r == 0:
        ax0.set_title("Static drone:\nattenuation (dB)", fontsize=8)
        ax1.set_title("Nomadic drone:\n% of time covered", fontsize=8)
axes[-1, 0].set_xlabel("x (m)"); axes[-1, 1].set_xlabel("x (m)")
fig.colorbar(im0, ax=axes[:, 0], shrink=0.5, label="attenuation (dB)", pad=0.02)
fig.colorbar(ax1p, ax=axes[:, 1], shrink=0.5, label="covered (%)", pad=0.02)
fig.savefig(FIG / "fig_coverage_map.pdf", dpi=150)
plt.close(fig)
print("wrote fig_coverage_map.pdf (white contour = coverage edge; red star = static drone)")
