#!/usr/bin/env python3
"""Fit an effective path-loss exponent and residual spread per terrain.

Backs section 5 of docs/CHANNEL_MODEL_LIMITATIONS.md. Reads the campaign CSVs
only -- nothing here writes to logs/.

The logged `*_ploss_dB` is the value actually sent to the rfsimulator: baseline
+ distance delta, EMA-smoothed, with log-normal shadowing added, clamped to
[0, 45] dB. Subtracting the per-robot baseline leaves the modelled path loss up
to a constant, which is all a slope fit needs:

    ploss - baseline = A + 10*n*log10(d_3d)

Rows sitting on the 45 dB clamp are dropped (the clamp truncates the fit); the
clamped fraction is reported so the exclusion is visible.

READ THE CAVEATS IN THE DOC BEFORE QUOTING THESE NUMBERS. The exponents are
what the simulator produces, not physics -- open_field's is inflated by the
Al-Hourani sigmoid's angle dependence, hills' is depressed because the UAV
climbs when a ridge blocks it, and the residual sd is not a shadowing sigma.

    python3 scripts/fit_path_loss.py
"""
from __future__ import annotations

import glob
import os

import numpy as np
import pandas as pd

# Per-robot UL baselines -- channel_bridge.py MODEL_MAP.
BASE = {"a": 8.0, "b": 13.0, "c": 10.0, "d": 15.0}
PLOSS_MAX = 45.0            # channel_bridge.py:155
CAMPAIGNS = "logs/campaign_2026102[3-6]_*/"


def load() -> pd.DataFrame:
    rows = []
    for d in sorted(glob.glob(CAMPAIGNS)):
        if any(x in d for x in ("SUPERSEDED", "PARTIAL", "ABORTED")):
            continue
        for f in sorted(glob.glob(os.path.join(d, "sar_*.csv"))):
            if "_fastradio" in f or "_actions" in f:
                continue
            w = pd.read_csv(f, low_memory=False)
            if w.empty or len(w) < 50:          # the 300 s smoke run
                continue
            terr = str(w["terrain"].iloc[0]).replace("sar_", "")
            for p in "abcd":
                need = [f"{p}_ploss_dB", f"{p}_d_horiz_m", f"{p}_d_slant_m"]
                if not all(c in w.columns for c in need):
                    continue
                rows.append(pd.DataFrame({
                    "terrain": terr,
                    "ploss": pd.to_numeric(w[f"{p}_ploss_dB"], errors="coerce"),
                    "ds": pd.to_numeric(w[f"{p}_d_slant_m"], errors="coerce"),
                    "uz": pd.to_numeric(w["uav_z_m"], errors="coerce"),
                    "base": BASE[p],
                }))
    d = pd.concat(rows, ignore_index=True).dropna()
    return d[(d.ds > 1) & (d.ploss > 0)]


def main() -> int:
    d = load()
    print(f"rows: {len(d):,}   terrains: {sorted(d.terrain.unique())}\n")
    print(f"{'terrain':<12}{'n':>8}{'%@45dB':>9}{'n_fit':>8}"
          f"{'exponent':>10}{'resid sd':>10}{'R2':>7}{'dz/10m':>9}")
    for t, g in d.groupby("terrain"):
        clamp = (g.ploss >= PLOSS_MAX - 1e-6).mean() * 100
        gg = g[g.ploss < PLOSS_MAX - 1e-6]
        if len(gg) < 100:
            print(f"{t:<12}{len(g):>8,}{clamp:>8.1f}%   -- too few unclamped --")
            continue
        y = (gg.ploss - gg.base).values
        X = np.column_stack([np.ones(len(gg)), 10 * np.log10(gg.ds.values)])
        beta, *_ = np.linalg.lstsq(X, y, rcond=None)
        resid = y - X @ beta
        r2 = 1 - resid.var() / y.var()
        # altitude sensitivity holding the log-range term fixed
        X2 = np.column_stack([X, gg.uz.values])
        b2, *_ = np.linalg.lstsq(X2, y, rcond=None)
        print(f"{t:<12}{len(g):>8,}{clamp:>8.1f}%{len(gg):>8,}"
              f"{beta[1]:>10.2f}{resid.std():>10.2f}{r2:>7.2f}{b2[2]*10:>9.2f}")
    print("\nFree-space reference exponent = 2.00.")
    print("The residual sd is NOT a shadowing sigma: it carries the 19 dB")
    print("LoS/NLoS step and the terrain clutter term as well as the fading.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
