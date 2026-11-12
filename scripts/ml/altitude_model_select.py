#!/usr/bin/env python3
"""Pick the surrogate the planner should fly: skill AND altitude behaviour.

WHY THIS EXISTS. Round 4 swapped the round-3 LightGBM surrogate for an L1
logistic one because the logistic one ranked failures better offline. It was
never asked what ALTITUDE it would choose. A model linear in altitude can only
say "higher is always better" or "always worse", so the planner pinned at the
55 m ceiling in every terrain -- including forest, where round 3 had held a
genuine interior set point of 40.9 m. Offline ranking skill alone selected a
planner that could not do the one thing the forest result needed.

So every candidate here is scored on two things, both leave-one-terrain-out:

  1. SKILL (onset task, dead-UE censored, h = 20 s): PR-AUC, ROC, worst-fold
     ROC, Brier skill. Same protocol as train_surrogates.py.

  2. ALTITUDE BEHAVIOUR on the HELD-OUT terrain. Recorded decision blocks are
     replayed through the planner's own candidate builder (rebuild_geometry)
     at every absolute target 25-55 m, and the soft-min objective picks z*.
     The reference is the MEASURED effect of a 10 m climb on C2 availability
     (logs/ml/phase4_climb_effect.csv, policy ALL):
         open_field +0.169*   urban +0.030*   hills +0.012   forest -0.015
     A good planner climbs in open field and does not in forest.

Candidates:
  linear      the round-4 model (L1 logistic on radio_blind)
  linear_geo  same, plus elevation angle and cubic splines on the geometry
              columns, so risk can be NON-monotone in altitude while radio
              stays linear
  lgbm        LightGBM, regularised, on the same features
Each is also replayed with a SUPPORT FILTER: candidates that put any robot
outside the training box (0.5-99.5 percentile per feature) are not eligible.

    nice -n 10 python3 -u scripts/ml/altitude_model_select.py
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

for _v in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS"):
    os.environ.setdefault(_v, "4")

import numpy as np                                            # noqa: E402
import pandas as pd                                           # noqa: E402
from sklearn.impute import SimpleImputer                      # noqa: E402
from sklearn.linear_model import LogisticRegression           # noqa: E402
from sklearn.metrics import (average_precision_score,         # noqa: E402
                             brier_score_loss, roc_auc_score)
from sklearn.pipeline import make_pipeline                    # noqa: E402
from sklearn.preprocessing import (FunctionTransformer,       # noqa: E402
                                   StandardScaler)

sys.path.insert(0, str(Path(__file__).resolve().parent))
import dataset as ds                                          # noqa: E402
from forecast_cycle import HIST_COLS                          # noqa: E402
from onset_forecast import dead_ue_mask, inner_time_split     # noqa: E402
from planner import Z_TARGETS, rebuild_geometry, soft_min_risk  # noqa: E402
from multitask_risk import fit_platt, apply_calibration       # noqa: E402
from geo_features import GEO, add_elev, make_linear_geo       # noqa: E402

OUT = ds.REPO / "logs" / "ml"
GROUPS = ["geom", "radio", "action"]
HORIZON = 20.0
BETA = 10.0
BLOCKS_PER_TERRAIN = 400
MEASURED = {"open_field": 0.169, "urban": 0.030, "hills": 0.012,
            "forest": -0.015}


def make_linear():
    return make_pipeline(
        SimpleImputer(strategy="median"), StandardScaler(),
        LogisticRegression(max_iter=4000, C=0.1, penalty="l1",
                           solver="liblinear"))


def make_lgbm():
    from lightgbm import LGBMClassifier
    return LGBMClassifier(n_estimators=300, learning_rate=0.05,
                          num_leaves=15, min_child_samples=200,
                          subsample=0.8, subsample_freq=1,
                          colsample_bytree=0.8, reg_lambda=1.0,
                          n_jobs=4, verbose=-1)


# Radio state the altitude effect is allowed to depend on. A terrain-blind
# model can only learn "climbing helps here but not there" if altitude is
# allowed to INTERACT with something that differs between here and there.
INT_RADIO = ["ul_nb_rbs", "ul_bits_per_sym", "ul_code_rate", "dl_bler",
             "dl_harq_nok"]


def add_interactions(X: pd.DataFrame) -> pd.DataFrame:
    X = add_elev(X)
    for g in ("uav_z_m", "elev_deg", "d_slant_m"):
        for r in INT_RADIO:
            if r in X:
                X[f"{g}_x_{r}"] = X[g] * X[r]
    return X


def make_linear_int(feats):
    return make_pipeline(
        FunctionTransformer(add_interactions),
        SimpleImputer(strategy="median"), StandardScaler(),
        LogisticRegression(max_iter=4000, C=0.1, penalty="l1",
                           solver="liblinear"))


CANDIDATES = {"linear": lambda f: make_linear(),
              "linear_geo": make_linear_geo,
              "linear_int": make_linear_int,
              "lgbm": lambda f: make_lgbm()}
_only = os.environ.get("ALTSEL_ONLY")
if _only:
    CANDIDATES = {k: v for k, v in CANDIDATES.items() if k in _only.split(",")}


def fit_calibrated(make, feats, Xtr, ytr, meta_tr):
    cal = inner_time_split(meta_tr)
    platt = None
    if cal.sum() >= 200 and len(np.unique(ytr[cal])) > 1:
        inner = make(feats).fit(Xtr[~cal], ytr[~cal])
        platt = fit_platt(inner.predict_proba(Xtr[cal])[:, 1], ytr[cal])
    return make(feats).fit(Xtr, ytr), platt


def decision_blocks(full: pd.DataFrame, terr: str, rng) -> list:
    """Recorded (run, cycle) blocks of >=2 robots in the held-out terrain."""
    f = full[(full["terrain"] == terr)
             & full[["uav_x_m", "uav_y_m", "uav_z_m"]].notna().all(1)]
    g = f.groupby(["run_id", "t_elapsed_s"]).size()
    keys = g[g >= 2].index.to_list()
    if len(keys) > BLOCKS_PER_TERRAIN:
        idx = rng.choice(len(keys), BLOCKS_PER_TERRAIN, replace=False)
        keys = [keys[i] for i in idx]
    gi = f.set_index(["run_id", "t_elapsed_s"]).sort_index()
    return [gi.loc[k].reset_index() for k in keys]


def replay(model, platt, feats, blocks, lo, hi):
    """z* per block, plain and support-filtered, plus the risk slope."""
    zs = np.array(Z_TARGETS)
    out = []
    for b in blocks:
        X = pd.concat([rebuild_geometry(b, 0.0, 0.0, z) for z in zs],
                      ignore_index=True)
        for c in feats:
            if c not in X:
                X[c] = np.nan
        p = apply_calibration(platt, model.predict_proba(X[feats])[:, 1])
        p = p.reshape(len(zs), len(b))
        J = np.array([soft_min_risk(r, BETA) for r in p])
        v = X[feats].values.astype(float)
        oos = (np.isfinite(v) & ((v < lo) | (v > hi))).any(1)
        oos = oos.reshape(len(zs), len(b)).any(1)
        Js = np.where(oos, np.inf, J)
        z_plain = zs[J.argmin()]
        z_sup = zs[Js.argmin()] if np.isfinite(Js).any() else np.nan
        # mean risk change per +10 m across the grid (negative => climb helps)
        slope = np.polyfit(zs, p.mean(1), 1)[0] * 10.0
        out.append((z_plain, z_sup, slope, oos.mean()))
    return np.array(out, dtype=float)


def main() -> int:
    t0 = time.time()
    rng = np.random.default_rng(1794)
    raw = ds.build()
    raw["dead"] = dead_ue_mask(raw)
    full = ds.add_history(raw, HIST_COLS, lags=(1, 2, 3))
    d = ds.add_target(full, HORIZON, "c2_ok")
    d = ds.add_target(d, HORIZON, "dead").reset_index(drop=True)
    d["y"] = 1.0 - d[f"y_c2_ok_h{int(HORIZON)}"]
    dead_tgt = d[f"y_dead_h{int(HORIZON)}"].fillna(0) > 0.5
    d = d[d["y"].notna() & (d["c2_ok"] == 1) & ~d["dead"]
          & ~dead_tgt].reset_index(drop=True)
    feats = ds.feature_columns(d, GROUPS)
    ds.assert_terrain_blind(feats)
    for g in GEO[:3]:
        assert g in feats, g
    print(f"onset rows {len(d):,}  fail {100*d['y'].mean():.1f}%  "
          f"{len(feats)} features  ({time.time()-t0:.0f}s)", flush=True)

    terrains = sorted(d["terrain"].unique())
    blocks = {t: decision_blocks(full[~full["dead"]], t, rng)
              for t in terrains}
    skill, alt = [], []
    pooled = {k: ([], []) for k in CANDIDATES}
    for name, make in CANDIDATES.items():
        for terr in terrains:
            tr = (d["terrain"] != terr).values
            Xtr, ytr = d.loc[tr, feats], d.loc[tr, "y"].values
            model, platt = fit_calibrated(make, feats, Xtr, ytr,
                                          d.loc[tr, ds.META])
            Xte, yte = d.loc[~tr, feats], d.loc[~tr, "y"].values
            p = apply_calibration(platt, model.predict_proba(Xte)[:, 1])
            pooled[name][0].append(yte)
            pooled[name][1].append(p)
            base = brier_score_loss(yte, np.full(len(yte), ytr.mean()))
            skill.append(dict(model=name, held_out=terr,
                              roc=roc_auc_score(yte, p),
                              pr=average_precision_score(yte, p),
                              base=yte.mean(),
                              brier_skill=1 - brier_score_loss(yte, p) / base))
            lo = np.nanpercentile(Xtr.values.astype(float), 0.5, axis=0)
            hi = np.nanpercentile(Xtr.values.astype(float), 99.5, axis=0)
            r = replay(model, platt, feats, blocks[terr], lo, hi)
            alt.append(dict(
                model=name, held_out=terr, n_blocks=len(r),
                z_mean=np.nanmean(r[:, 0]), at_ceiling=np.mean(r[:, 0] >= 55),
                at_floor=np.mean(r[:, 0] <= 25),
                interior=np.mean((r[:, 0] > 25) & (r[:, 0] < 55)),
                z_mean_sup=np.nanmean(r[:, 1]),
                sup_feasible=np.mean(np.isfinite(r[:, 1])),
                risk_slope_10m=np.mean(r[:, 2]),
                measured_climb=MEASURED.get(terr, np.nan)))
            print(f"  {name:<10} {terr:<10} ROC {skill[-1]['roc']:.3f}  "
                  f"z* {alt[-1]['z_mean']:.1f} (ceil {alt[-1]['at_ceiling']:.2f}, "
                  f"interior {alt[-1]['interior']:.2f})  "
                  f"slope {alt[-1]['risk_slope_10m']:+.4f}  "
                  f"({time.time()-t0:.0f}s)", flush=True)

    S, A = pd.DataFrame(skill), pd.DataFrame(alt)
    rows = []
    for name in CANDIDATES:
        y = np.concatenate(pooled[name][0])
        p = np.concatenate(pooled[name][1])
        s, a = S[S.model == name], A[A.model == name].set_index("held_out")
        # Climb-direction agreement: the model should predict LOWER risk when
        # climbing exactly where climbing measurably helps. Rank agreement of
        # -slope with the measured effect, over the four terrains.
        rho = pd.Series(-a["risk_slope_10m"]).rank().corr(
            pd.Series(a["measured_climb"]).rank())
        rows.append(dict(
            model=name, roc_pooled=roc_auc_score(y, p),
            pr_pooled=average_precision_score(y, p),
            roc_worst=s.roc.min(), brier_skill_mean=s.brier_skill.mean(),
            z_open=a.loc["open_field", "z_mean"],
            z_forest=a.loc["forest", "z_mean"],
            z_gap_open_minus_forest=a.loc["open_field", "z_mean"]
            - a.loc["forest", "z_mean"],
            ceiling_share_mean=a["at_ceiling"].mean(),
            climb_rank_agreement=rho))
    R = pd.DataFrame(rows)
    S.to_csv(OUT / "altsel_skill.csv", index=False)
    A.to_csv(OUT / "altsel_altitude.csv", index=False)
    R.to_csv(OUT / "altsel_summary.csv", index=False)
    pd.set_option("display.width", 200)
    print("\n== altitude behaviour (held-out terrain) ==")
    print(A.round(3).to_string(index=False))
    print("\n== summary ==")
    print(R.round(3).to_string(index=False))
    print(f"\n({time.time()-t0:.0f}s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
