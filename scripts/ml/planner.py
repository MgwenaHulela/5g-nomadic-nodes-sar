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
    candidates  A grid of waypoints relative to where the UAV is: vertical
                moves in {-10,-5,0,+5,+10} m and horizontal moves of 0 or
                15 m on 8 compass headings. Geometry features are recomputed
                exactly at each candidate (d_horiz = |robot-uav| and
                d_slant = hypot(d_horiz, uav_z) both reproduce the logged
                values to ~0.03 m, verified before this was written); radio
                and link history are held fixed, since moving the UAV does
                not rewrite the past.
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
DZ = [-10.0, -5.0, 0.0, 5.0, 10.0]
DXY_R = [0.0, 15.0]
HEADINGS = np.arange(0, 360, 45) * np.pi / 180.0


def candidate_offsets() -> np.ndarray:
    """(dx, dy, dz) offsets from the UAV's current position."""
    out = [(0.0, 0.0, dz) for dz in DZ]
    for r in DXY_R:
        if r == 0:
            continue
        for a in HEADINGS:
            for dz in DZ:
                out.append((r * np.cos(a), r * np.sin(a), dz))
    return np.unique(np.array(out, dtype=float), axis=0)


def rebuild_geometry(block: pd.DataFrame, dx: float, dy: float,
                     dz: float) -> pd.DataFrame:
    """Features for the 4 robots if the UAV moved by (dx,dy,dz).

    Only quantities that genuinely depend on where the UAV is are changed:
    its position, each robot's horizontal and slant range, the one-step
    deltas and 3-cycle means that include the current value, and the action
    displacement. Lagged values are history and are left alone.
    """
    b = block.copy()
    ux = b["uav_x_m"].values + dx
    uy = b["uav_y_m"].values + dy
    uz = np.clip(b["uav_z_m"].values + dz, Z_MIN, Z_MAX)
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

    offs = candidate_offsets()
    print(f"{len(offs)} candidate waypoints per decision point")

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
            n_cand += len(offs)
            p = model.predict_proba(X[feats])[:, 1].reshape(len(offs),
                                                            len(block))
            j_soft = np.array([soft_min_risk(r, args.beta) for r in p])
            j_mean = p.mean(axis=1)
            i_soft, i_mean = int(j_soft.argmin()), int(j_mean.argmin())
            # index of the "stay put" candidate
            i_hold = int(np.argmin(np.abs(offs).sum(axis=1)))
            rows.append(dict(
                terrain=terr, run_id=run, t_elapsed_s=t, n_robots=len(block),
                dz_soft=offs[i_soft][2], dz_mean=offs[i_mean][2],
                dxy_soft=float(np.hypot(*offs[i_soft][:2])),
                risk_hold=j_soft[i_hold], risk_soft=j_soft[i_soft],
                risk_mean_pick=j_mean[i_mean], risk_mean_hold=j_mean[i_hold],
                agree=int(i_soft == i_mean),
                realised_down=float(block["y"].mean()),
                uav_z_m=float(block["uav_z_m"].iloc[0])))
        print(f"  {terr:<11} {len(keys):,} decision points, surrogate fitted "
              f"on the other 3 terrains, "
              f"{100*n_out/max(n_cand,1):.1f}% of candidates outside support "
              f"[{time.time()-t0:.0f}s]")

    r = pd.DataFrame(rows)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    r.to_csv(OUT_DIR / "phase5_planner.csv", index=False)

    print("\n" + "=" * 70)
    print("THE TEST: chosen altitude change per terrain")
    print("  the planner is never told the terrain; Phase 4 says climbing")
    print("  helps in open_field and not in forest")
    print("=" * 70)
    tb = r.groupby("terrain").agg(
        n=("dz_soft", "size"), mean_dz=("dz_soft", "mean"),
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
