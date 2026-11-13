#!/usr/bin/env python3
"""Retrain the terrain-blind forecaster on clean (capped) data and compare.

Question: was the forecaster weak because its training labels were dirty
(forest C2-down partly = UE queue backlog, docs/CONGESTION_TEST_REPORT.md)?

Same protocol as model_class_cmp.py: onset label (C2 up at t, down at t+h),
h = 20 s, dead-process cycles censored at t and t+h, 65 radio_blind features,
assert_terrain_blind on every matrix, the same 8-model capacity ladder.

Tests (each fold reports ROC-AUC and PR-AUC lift over its own no-skill rate):
  T1 old->clean     train on ALL old (pre-cap) data, test on clean data, by terrain
  T2 clean LOTO     clean data only, leave one terrain out (forest <-> open field)
  T3 clean LOSO     clean data only, leave one seed out (pooled terrains)
  T4 old+clean LOSO train on old + clean other seeds, test the held-out clean seed
Reference: the old 4-terrain LOTO result is in logs/ml/phase2c_model_class.csv.

    nice -n 10 python3 -u scripts/ml/retrain_clean.py [--models logistic_L1 lightgbm ...]
writes logs/ml/retrain_clean.csv
"""
from __future__ import annotations

import argparse
import sys
import time
import warnings
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.base import clone
from sklearn.exceptions import ConvergenceWarning
from sklearn.metrics import average_precision_score, roc_auc_score

sys.path.insert(0, str(Path(__file__).resolve().parent))
import dataset as ds                                   # noqa: E402
from clean_dataset import build_clean                  # noqa: E402
from forecast_cycle import HIST_COLS                   # noqa: E402
from model_class_cmp import model_zoo                  # noqa: E402
from onset_forecast import dead_ue_mask                # noqa: E402

warnings.filterwarnings("ignore", category=ConvergenceWarning)
H = 20.0
OUT = ds.REPO / "logs" / "ml" / "retrain_clean.csv"


def onset(raw: pd.DataFrame) -> pd.DataFrame:
    raw = raw.sort_values(["run_id", "robot", "t_elapsed_s"], kind="stable").reset_index(drop=True)
    raw["dead"] = dead_ue_mask(raw)
    d = ds.add_history(raw, HIST_COLS, lags=(1, 2, 3))
    d = ds.add_target(d, H, "c2_ok")
    d = ds.add_target(d, H, "dead").reset_index(drop=True)
    d["y"] = 1.0 - d[f"y_c2_ok_h{int(H)}"]
    dead_tgt = d[f"y_dead_h{int(H)}"].fillna(0) > 0.5
    return d[d["y"].notna() & (d["c2_ok"] == 1) & ~d["dead"] & ~dead_tgt].reset_index(drop=True)


def fit_predict(name, est, Xtr, ytr, Xte):
    if name in ("lightgbm", "random_forest"):
        med = Xtr.median(); Xtr = Xtr.fillna(med); Xte = Xte.fillna(med)
    return clone(est).fit(Xtr, ytr).predict_proba(Xte)[:, 1]


def score(test, model, fold, y, p, rows):
    if len(np.unique(y)) < 2:
        return
    base = float(y.mean()); pr = float(average_precision_score(y, p))
    rows.append(dict(test=test, model=model, fold=fold, n=len(y), pos_rate=base,
                     pr_auc=pr, pr_lift=pr / base, roc_auc=float(roc_auc_score(y, p))))


def pooled(test, model, ys, ps, rows):
    folds = [r for r in rows if r["test"] == test and r["model"] == model and r["fold"] != "POOLED"]
    if not ys:
        return
    y, p = np.concatenate(ys), np.concatenate(ps)
    score(test, model, "POOLED", y, p, rows)
    if rows and rows[-1]["fold"] == "POOLED" and rows[-1]["model"] == model:
        rows[-1]["roc_worst_fold"] = min(r["roc_auc"] for r in folds) if folds else np.nan


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", nargs="+", default=None)
    args = ap.parse_args()
    t0 = time.time()
    old, new = onset(ds.build()), onset(build_clean())
    feats = ds.feature_columns(old, ["geom", "radio", "action"], with_history=True)
    ds.assert_terrain_blind(feats)
    assert set(feats) <= set(new.columns), "clean data lacks a feature"
    print(f"old onset rows {len(old):,} (fail {100*old.y.mean():.1f}%), "
          f"clean onset rows {len(new):,} (fail {100*new.y.mean():.1f}%), {len(feats)} features")
    print(new.groupby(["terrain", "seed"]).agg(rows=("y", "size"), fail=("y", "mean")).round(3).to_string())
    zoo = model_zoo()
    names = args.models or list(zoo)
    rows = []
    for name in names:
        est = zoo[name]
        # T1 old -> clean
        ys, ps = [], []
        p_all = fit_predict(name, est, old[feats], old.y.values, new[feats])
        for terr in sorted(new.terrain.unique()):
            m = (new.terrain == terr).values
            score("T1_old_to_clean", name, terr, new.y.values[m], p_all[m], rows)
        ys.append(new.y.values); ps.append(p_all); pooled("T1_old_to_clean", name, ys, ps, rows)
        # T2 clean LOTO, T3 clean LOSO, T4 old+clean LOSO
        for test, key, add_old in (("T2_clean_LOTO", "terrain", False),
                                   ("T3_clean_LOSO", "seed", False),
                                   ("T4_oldplus_clean_LOSO", "seed", True)):
            groups = sorted(new[key].unique())
            if len(groups) < 2:
                continue
            ys, ps = [], []
            for g in groups:
                te = (new[key] == g).values
                tr = new[~te]
                if add_old:
                    tr = pd.concat([old, tr], ignore_index=True)
                if tr.y.nunique() < 2:
                    continue
                p = fit_predict(name, est, tr[feats], tr.y.values, new.loc[te, feats])
                score(test, name, str(g), new.y.values[te], p, rows)
                ys.append(new.y.values[te]); ps.append(p)
            pooled(test, name, ys, ps, rows)
        done = [r for r in rows if r["model"] == name and r["fold"] == "POOLED"]
        for r in done:
            print(f"  {name:<20}{r['test']:<24} ROC {r['roc_auc']:.3f}  lift {r['pr_lift']:.2f}  "
                  f"worst {r.get('roc_worst_fold', np.nan):.3f}  n={r['n']:,}  [{time.time()-t0:.0f}s]", flush=True)
    res = pd.DataFrame(rows)
    res.to_csv(OUT, index=False)
    print(f"wrote {OUT}  ({time.time()-t0:.0f}s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
