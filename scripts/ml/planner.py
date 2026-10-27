#!/usr/bin/env python3
"""Phase 5 — the planner. Surrogate + soft-min over the worst robot.

Phases 1-4 forecast. This one acts: at each decision point it chooses where
to move the UAV so that the **worst-connected robot** is as well connected as
possible 60 s from now. That is the loop the README brief describes, and it
is the only part of the project that produces a control output rather than a
number.

HOW IT WORKS
    surrogate   The Phase 2 LightGBM forecaster, trained leave-one-terrain-
                out, so the model planning in a terrain has never seen that
                terrain and never receives the terrain label. Terrain
                blindness holds all the way through to the control output.
    candidates  A grid of **absolute altitude targets** (20..60 m in 5 m
                steps) crossed with horizontal moves of 0 or 15 m on 8
                compass headings. Geometry features are recomputed
                exactly at each candidate (d_horiz = |robot-uav| and
                d_slant = hypot(d_horiz, uav_z) both reproduce the logged
                values to ~0.03 m, verified before this was written); radio
                and link history are held fixed, since moving the UAV does
                not rewrite the past. The chosen target is approached at no
                more than MAX_DZ_STEP per decision, so the action actually
                commanded stays inside the dense part of the training
                support (|action_dz_m| p90 = 9.3 m).

WHY ABSOLUTE TARGETS (the Phase 5 fly-off fix)
    The first fly-off chose a *relative* dz from {-10,-5,0,+5,+10}. That is a
    hill-climber: it only ever compares the current altitude against its
    immediate neighbours, so it cannot see past a local optimum, and because
    its preferred step was positive in every terrain it ratcheted into the
    Z_MAX ceiling inside the first quintile of every run and stayed there
    74-96 % of the time. All terrain differentiation integrated away, and in
    forest -- where Phase 4 says climbing hurts -- it climbed anyway and lost
    12.0 pp of availability for it (section 4.3.12).

    Sweeping the SAME surrogate over absolute altitude shows the information
    was there all along; the relative objective was discarding it. Median
    risk-minimising altitude, by terrain:

        forest 40.0 m (54 % interior)    hills      47.5 m (88 % interior)
        urban  57.5 m (11 % interior)    open_field 60.0 m ( 8 % interior)

    That ordering is Phase 4's ordering. Choosing the argmin over the whole
    band rather than over two neighbours is therefore not a tuning knob --
    it adds no parameter and no altitude cost, it just stops throwing the
    terrain signal away. An explicit altitude penalty was rejected for the
    opposite reason: it would manufacture exactly the terrain differences
    this phase is trying to measure.
    objective   Soft-min over robots of predicted connectivity, i.e. soft-max
                of predicted risk:
                    J(u) = (1/beta) * log sum_i exp(beta * risk_i(u))
                beta -> inf is "protect the worst robot", beta = 0 is "mean
                risk". beta = 10 by default; beta = 0 is run as an ablation,
                because if the two choose the same waypoints then the
                worst-robot framing is decoration.

WHAT THIS IS NOT
    The surrogate is an **observational** model: it learned from positions
    the UAV actually visited, not from a randomised trial of waypoints.
    Querying it at a waypoint the UAV did not take is extrapolation, and the
    further the candidate sits from the data the less the answer means. The
    fraction of candidates falling outside the training support is reported
    with every result, and nothing here is a causal effect estimate. A real
    validation needs the planner flown in the simulator, which is a
    configuration change and an overnight run -- proposed, not done.

THE TEST THAT MATTERS
    Phase 4 established that climbing helps in open terrain and not in
    structured terrain, and that a terrain-blind model recovers that. If the
    planner is sound, its *chosen altitude changes* should show the same
    pattern per terrain -- without being told the terrain, and without anyone
    writing that rule down. That is the headline check below.

Usage:
    python3 scripts/ml/planner.py --stride 10
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import dataset as ds                      # noqa: E402
from forecast_cycle import HIST_COLS, PARAMS   # noqa: E402

OUT_DIR = ds.REPO / "logs" / "ml"
GROUPS = ["geom", "radio", "action", "c2hist"]
HORIZON = 60.0
Z_MIN, Z_MAX = 20.0, 60.0
# Absolute altitudes, not offsets -- and deliberately NARROWER than the
# [Z_MIN, Z_MAX] geofence. The surrogate may only be queried where it has
# training support: uav_z_m runs p1=21.3-22.4 to p99=57.7-58.2 across the four
# LOTO folds, and under 1 % of training rows sit above 58 m. Letting the argmin
# reach 60 m had it picking the top of the grid off a sparse tail -- 88.8 % of
# open_field and 80.8 % of urban choices landed above p99 -- where a GBDT's
# estimate is carried by a handful of leaves. The model-free check agrees that
# this is not merely cautious: binning the logged data by altitude at matched
# robot distance, P(C2 up) PEAKS at or below 55 m in forest, hills and urban,
# and the 58-61 m band holds n<=122 rows per terrain.
#
# The rule is "do not target where the model cannot see", fixed before looking
# at which grid produced a tidier terrain ordering.
Z_TARGETS = list(np.arange(25.0, 55.01, 5.0))
MAX_DZ_STEP = 10.0       # rate limit on the executed climb/descend per decision
DXY_R = [0.0, 15.0]
HEADINGS = np.arange(0, 360, 45) * np.pi / 180.0


def candidate_targets() -> np.ndarray:
    """(dx, dy, z_abs): lateral offset plus an ABSOLUTE altitude target.

    Absolute rather than relative on purpose -- see "WHY ABSOLUTE TARGETS"
    in the module docstring. The altitude leg is a global argmin over the
    whole operating band, so the choice cannot ratchet.
    """
    out = [(0.0, 0.0, z) for z in Z_TARGETS]
    for r in DXY_R:
        if r == 0:
            continue
        for a in HEADINGS:
            for z in Z_TARGETS:
                out.append((r * np.cos(a), r * np.sin(a), z))
    return np.unique(np.array(out, dtype=float), axis=0)


def rate_limit(z_now: float, z_target: float,
               max_step: float = MAX_DZ_STEP) -> float:
    """The dz to actually command this decision, moving toward z_target."""
    return float(np.clip(z_target - z_now, -max_step, max_step))


def rebuild_geometry(block: pd.DataFrame, dx: float, dy: float,
                     z_abs: float) -> pd.DataFrame:
    """Features for the 4 robots if the UAV moved to (x+dx, y+dy, z_abs).

    Only quantities that genuinely depend on where the UAV is are changed:
    its position, each robot's horizontal and slant range, the one-step
    deltas and 3-cycle means that include the current value, and the action
    displacement. Lagged values are history and are left alone.
    """
    b = block.copy()
    ux = b["uav_x_m"].values + dx
    uy = b["uav_y_m"].values + dy
    uz = np.full(len(b), float(np.clip(z_abs, Z_MIN, Z_MAX)))
    dh = np.hypot(b["robot_x_m"].values - ux, b["robot_y_m"].values - uy)
    dsl = np.hypot(dh, uz)
    b["uav_x_m"], b["uav_y_m"], b["uav_z_m"] = ux, uy, uz
    b["d_horiz_m"], b["d_slant_m"] = dh, dsl
    for c, new in (("d_horiz_m", dh), ("d_slant_m", dsl)):
        if f"{c}_lag1" in b:
            b[f"{c}_d1"] = new - b[f"{c}_lag1"].values
        if f"{c}_roll3_mean" in b and f"{c}_lag1" in b and f"{c}_lag2" in b:
            b[f"{c}_roll3_mean"] = (
                new + b[f"{c}_lag1"].values + b[f"{c}_lag2"].values) / 3.0
    b["action_dx_m"], b["action_dy_m"] = dx, dy
    b["action_dz_m"] = uz - block["uav_z_m"].values
    return b


def soft_min_risk(risk: np.ndarray, beta: float) -> float:
    """Soft-max of per-robot risk = soft-min of per-robot connectivity."""
    if beta <= 0:
        return float(risk.mean())
    m = risk.max()
    return float(m + np.log(np.exp(beta * (risk - m)).sum()) / beta)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--stride", type=int, default=10,
                    help="use every Nth cycle as a decision point")
    ap.add_argument("--beta", type=float, default=10.0)
    args = ap.parse_args()

    t0 = time.time()
    df = ds.add_history(ds.build(), HIST_COLS)
    d = ds.add_target(df, HORIZON, "c2_ok").reset_index(drop=True)
    d["y"] = 1.0 - d[f"y_c2_ok_h{int(HORIZON)}"]
    d = d[d["y"].notna()].reset_index(drop=True)
    feats = ds.feature_columns(d, GROUPS)
    ds.assert_terrain_blind(feats)
    print(f"{len(d):,} labelled rows, {len(feats)} features "
          f"({time.time()-t0:.0f}s)")

    offs = candidate_targets()
    print(f"{len(offs)} candidate waypoints per decision point "
          f"({len(Z_TARGETS)} absolute altitudes x "
          f"{len(offs)//len(Z_TARGETS)} lateral offsets)")

    rows = []
    for terr in sorted(d["terrain"].unique()):
        tr = d["terrain"] != terr
        model = lgb.LGBMClassifier(**PARAMS).fit(d.loc[tr, feats],
                                                 d.loc[tr, "y"])
        # training support, used to flag extrapolation
        lo_z, hi_z = d.loc[tr, "uav_z_m"].quantile([.01, .99])
        lo_d, hi_d = d.loc[tr, "d_slant_m"].quantile([.01, .99])

        te = d[d["terrain"] == terr]
        keys = te[["run_id", "t_elapsed_s"]].drop_duplicates()
        keys = keys.iloc[::args.stride]
        g = te.groupby(["run_id", "t_elapsed_s"], sort=False)

        n_out = n_cand = 0
        for run, t in keys.itertuples(index=False):
            try:
                block = g.get_group((run, t))
            except KeyError:
                continue
            if len(block) < 2:
                continue
            X = pd.concat([rebuild_geometry(block, *o) for o in offs],
                          ignore_index=True)
            n_out += int(((X["uav_z_m"] < lo_z) | (X["uav_z_m"] > hi_z) |
                          (X["d_slant_m"] < lo_d) |
                          (X["d_slant_m"] > hi_d)).mean() * len(offs))
            z_now = float(block["uav_z_m"].iloc[0])
            n_cand += len(offs)
            p = model.predict_proba(X[feats])[:, 1].reshape(len(offs),
                                                            len(block))
            j_soft = np.array([soft_min_risk(r, args.beta) for r in p])
            j_mean = p.mean(axis=1)
            i_soft, i_mean = int(j_soft.argmin()), int(j_mean.argmin())
            # "stay put" = no lateral move and the target nearest current z
            i_hold = int(np.argmin(np.hypot(offs[:, 0], offs[:, 1]) * 1e3
                                   + np.abs(offs[:, 2] - z_now)))
            z_star = float(offs[i_soft][2])
            rows.append(dict(
                terrain=terr, run_id=run, t_elapsed_s=t, n_robots=len(block),
                z_target=z_star, z_target_mean=float(offs[i_mean][2]),
                dz_soft=rate_limit(z_now, z_star),
                dz_mean=rate_limit(z_now, float(offs[i_mean][2])),
                dxy_soft=float(np.hypot(*offs[i_soft][:2])),
                risk_hold=j_soft[i_hold], risk_soft=j_soft[i_soft],
                risk_mean_pick=j_mean[i_mean], risk_mean_hold=j_mean[i_hold],
                agree=int(i_soft == i_mean),
                realised_down=float(block["y"].mean()),
                uav_z_m=z_now))
        print(f"  {terr:<11} {len(keys):,} decision points, surrogate fitted "
              f"on the other 3 terrains, "
              f"{100*n_out/max(n_cand,1):.1f}% of candidates outside support "
              f"[{time.time()-t0:.0f}s]")

    r = pd.DataFrame(rows)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    r.to_csv(OUT_DIR / "phase5_planner.csv", index=False)

    print("\n" + "=" * 70)
    print("THE TEST: chosen ABSOLUTE altitude target per terrain")
    print("  the planner is never told the terrain; Phase 4 says climbing")
    print("  helps in open_field and not in forest")
    print("=" * 70)
    tb = r.groupby("terrain").agg(
        n=("z_target", "size"), mean_z_target=("z_target", "mean"),
        median_z_target=("z_target", "median"),
        pct_at_top=("z_target", lambda s: 100 * (s >= Z_TARGETS[-1]).mean()),
        pct_at_floor=("z_target", lambda s: 100 * (s <= Z_TARGETS[0]).mean()),
        mean_dz=("dz_soft", "mean"),
        pct_climb=("dz_soft", lambda s: 100 * (s > 0).mean()),
        pct_descend=("dz_soft", lambda s: 100 * (s < 0).mean()),
        mean_dxy=("dxy_soft", "mean"))
    print(tb.to_string(float_format=lambda v: f"{v:.2f}"))

    print("\nrisk reduction vs holding position (soft-min objective):")
    r["gain"] = r.risk_hold - r.risk_soft
    print(r.groupby("terrain")["gain"].describe()[["mean", "50%", "max"]]
          .to_string(float_format=lambda v: f"{v:.4f}"))

    print(f"\nablation: soft-min (beta={args.beta}) vs mean risk — "
          f"identical waypoint in {100*r.agree.mean():.1f}% of decisions")
    print(f"  mean |dz| soft-min {r.dz_soft.abs().mean():.2f} m vs "
          f"mean-risk {r.dz_mean.abs().mean():.2f} m")
    print(f"\nwrote {OUT_DIR/'phase5_planner.csv'}  ({time.time()-t0:.0f}s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
