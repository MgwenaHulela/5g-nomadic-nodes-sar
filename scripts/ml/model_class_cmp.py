#!/usr/bin/env python3
"""Phase 2c — does model capacity help? (cycle data, onset task)

The horizon sweep turned up something that needs testing properly rather than
asserting: a six-feature logistic regression on radio trend matched, and at
20-30 s clearly beat, a tuned LightGBM on the same onset task. The obvious
objection is "you didn't try a neural network," so this script tries one --
two, in fact, a small regularised MLP and a deliberately oversized one -- plus
linear models at three capacities, all under the same leave-one-terrain-out
protocol on the same censored onset population.

The hypothesis being tested is that LOTO punishes capacity:

  * A tree splits on ABSOLUTE feature values ("BLER > 0.0037"). Those
    thresholds are fitted to the three training terrains and do not transfer
    to a fourth.
  * A linear model learns a DIRECTION ("BLER rising -> risk rising"), which
    does transfer.
  * Effective n is not 38,803 rows. It is 20 runs and ~2,600 degradation
    episodes, heavily serially correlated, across 4 folds.

If that is right, the ranking should be roughly linear > trees > MLP, and the
gap should widen at the horizons where the signal is weakest.

Feature sets:
  trend6      the 6 hand-picked trend features -- the incumbent champion
  radio_blind the full 65-column terrain-blind matrix

Usage:
    python3 scripts/ml/model_class_cmp.py                  # h = 20, 60
    python3 scripts/ml/model_class_cmp.py --horizons 20
"""
from __future__ import annotations

import argparse
import sys
import time
import warnings
from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestClassifier
from sklearn.exceptions import ConvergenceWarning
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import average_precision_score, roc_auc_score
from sklearn.neural_network import MLPClassifier
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

sys.path.insert(0, str(Path(__file__).resolve().parent))
import dataset as ds                                       # noqa: E402
from forecast_cycle import HIST_COLS, PARAMS               # noqa: E402
from onset_forecast import dead_ue_mask, TREND_FEATS       # noqa: E402

warnings.filterwarnings("ignore", category=ConvergenceWarning)
OUT_DIR = ds.REPO / "logs" / "ml"


def _pipe(est):
    return make_pipeline(SimpleImputer(strategy="median"),
                         StandardScaler(), est)


def model_zoo() -> dict:
    """Capacity ladder, lowest first."""
    return {
        "logistic_L2":   _pipe(LogisticRegression(max_iter=4000, C=1.0)),
        "logistic_L2_strong": _pipe(LogisticRegression(max_iter=4000, C=0.05)),
        "logistic_L1":   _pipe(LogisticRegression(max_iter=4000, C=0.1,
                                                  penalty="l1",
                                                  solver="liblinear")),
        "mlp_small":     _pipe(MLPClassifier(hidden_layer_sizes=(16,),
                                             alpha=1e-1, max_iter=400,
                                             early_stopping=True,
                                             n_iter_no_change=15,
                                             random_state=0)),
        "mlp_medium":    _pipe(MLPClassifier(hidden_layer_sizes=(64, 32),
                                             alpha=1e-2, max_iter=400,
                                             early_stopping=True,
                                             n_iter_no_change=15,
                                             random_state=0)),
        "mlp_large":     _pipe(MLPClassifier(hidden_layer_sizes=(256, 128, 64),
                                             alpha=1e-4, max_iter=400,
                                             early_stopping=True,
                                             n_iter_no_change=15,
                                             random_state=0)),
        "random_forest": RandomForestClassifier(n_estimators=300,
                                                min_samples_leaf=50,
                                                n_jobs=-1, random_state=0),
        "lightgbm":      lgb.LGBMClassifier(**PARAMS),
    }


def build_onset(h: float):
    raw = ds.build().sort_values(["run_id", "robot", "t_elapsed_s"],
                                 kind="stable").reset_index(drop=True)
    raw["dead"] = dead_ue_mask(raw)
    df = ds.add_history(raw, HIST_COLS, lags=(1, 2, 3))
    d = ds.add_target(df, h, "c2_ok")
    d = ds.add_target(d, h, "dead").reset_index(drop=True)
    d["y"] = 1.0 - d[f"y_c2_ok_h{int(h)}"]
    dead_tgt = d[f"y_dead_h{int(h)}"].fillna(0) > 0.5
    return d[d["y"].notna() & (d["c2_ok"] == 1) & ~d["dead"]
             & ~dead_tgt].reset_index(drop=True)


def evaluate(d, feats, est_name, est, rows, fset):
    accy, accp = [], []
    for terr, tr, te in ds.leave_one_terrain_out(d[ds.META]):
        ytr = d.loc[tr, "y"].values
        if len(np.unique(ytr)) < 2:
            continue
        Xtr = d.loc[tr, feats]
        Xte = d.loc[te, feats]
        if est_name in ("lightgbm", "random_forest"):
            Xtr = Xtr.fillna(Xtr.median()); Xte = Xte.fillna(Xtr.median())
        from sklearn.base import clone
        m = clone(est).fit(Xtr, ytr)
        p = m.predict_proba(Xte)[:, 1]
        y = d.loc[te, "y"].values
        accy.append(y); accp.append(p)
        if len(np.unique(y)) > 1:
            rows.append(dict(features=fset, model=est_name, terrain=terr,
                             n=len(y), pos_rate=float(y.mean()),
                             pr_auc=float(average_precision_score(y, p)),
                             roc_auc=float(roc_auc_score(y, p))))
    yy, pp = np.concatenate(accy), np.concatenate(accp)
    base = float(yy.mean())
    pr = float(average_precision_score(yy, pp))
    roc = float(roc_auc_score(yy, pp))
    per_terr = [r["roc_auc"] for r in rows
                if r["model"] == est_name and r["features"] == fset
                and r["terrain"] != "POOLED"]
    rows.append(dict(features=fset, model=est_name, terrain="POOLED",
                     n=len(yy), pos_rate=base, pr_auc=pr, pr_lift=pr / base,
                     roc_auc=roc, roc_min_fold=min(per_terr) if per_terr else np.nan))
    return pr, pr / base, roc, (min(per_terr) if per_terr else np.nan)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--horizons", type=float, nargs="+", default=[20.0, 60.0])
    ap.add_argument("--out", default=str(OUT_DIR))
    args = ap.parse_args()
    t0 = time.time()
    rows = []

    for h in args.horizons:
        d = build_onset(h)
        full = ds.feature_columns(d, ["geom", "radio", "action"],
                                  with_history=True)
        trend = [c for c in TREND_FEATS if c in d.columns]
        ds.assert_terrain_blind(full); ds.assert_terrain_blind(trend)
        print(f"\n{'='*86}\nHORIZON {int(h)} s   onset rows {len(d):,}   "
              f"FAIL {100*d['y'].mean():.1f}%   "
              f"trend6={len(trend)}f  radio_blind={len(full)}f\n{'='*86}")
        print(f"{'features':<14}{'model':<22}{'PR-AUC':>9}{'xno-skill':>11}"
              f"{'ROC':>8}{'worst fold ROC':>16}")
        for fset, feats in (("trend6", trend), ("radio_blind", full)):
            for name, est in model_zoo().items():
                try:
                    pr, lift, roc, worst = evaluate(d, feats, name, est,
                                                    rows, fset)
                    print(f"{fset:<14}{name:<22}{pr:>9.3f}{lift:>11.2f}"
                          f"{roc:>8.3f}{worst:>16.3f}   [{time.time()-t0:.0f}s]")
                except Exception as e:
                    print(f"{fset:<14}{name:<22}  FAILED: {type(e).__name__}: "
                          f"{str(e)[:50]}")
            print(f"{fset:<14}{'no_skill':<22}"
                  f"{d['y'].mean():>9.3f}{1.0:>11.2f}{0.5:>8.3f}{0.5:>16.3f}")

    res = pd.DataFrame(rows)
    outd = Path(args.out); outd.mkdir(parents=True, exist_ok=True)
    res.to_csv(outd / "phase2c_model_class.csv", index=False)
    print(f"\nwrote {outd/'phase2c_model_class.csv'}  ({time.time()-t0:.0f}s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
