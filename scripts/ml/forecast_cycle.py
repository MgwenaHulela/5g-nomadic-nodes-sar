#!/usr/bin/env python3
"""Phase 2 — cycle-horizon forecaster (dataset A).

Predicts the same thing Phase 1 baselined: is C2 to THIS robot DOWN at t+h.
Same metric, same splits, same train-only thresholds, so the numbers sit
directly beside `logs/ml/phase1_baselines.csv`. The bar is 3-cycle
persistence: PR-AUC 0.628 / ROC-AUC 0.771 at h=60 s under LOTO.

LightGBM, because 13 k cycles x 4 robots is tabular data with ragged cadence
and missing columns across runs, and because the sign-flip argument in Phase 4
needs effects we can read, not a black box.

**The ablation ladder** (§4.3.2) is the point of this script, not an extra.
Each rung adds inputs that are closer to the label, so the honest claim lives
at the bottom and the ceiling lives at the top:

  radio_blind          geometry + gNB link adaptation + action context.
                       Never observes a C2 failure, present or past. If this
                       beats persistence, the system can see degradation
                       coming from the radio alone -- the strongest result
                       available.
  +c2hist              adds the label's own history. This is the apples-to-
                       apples comparison with persistence: same information,
                       plus the radio.
  +ping                adds RTT and ping loss. The brief requires reporting
                       with and without these.
  +video               adds user-plane QoS. Nearly as close to the label as
                       ping loss; added because the leak is real.

Terrain never enters any rung -- dataset.assert_terrain_blind() is called on
every feature matrix before it reaches the model.

Usage:
    python3 scripts/ml/forecast_cycle.py --horizons 60
    python3 scripts/ml/forecast_cycle.py --horizons 30 60 120 --full
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
import dataset as ds            # noqa: E402
from baselines import score, train_threshold   # noqa: E402

OUT_DIR = ds.REPO / "logs" / "ml"

# Columns worth a history window. Everything a forecaster could plausibly
# read a trend off; lagging the whole table would be mostly noise.
HIST_COLS = ["c2_ok", "c2_loss_pct", "d_slant_m", "d_horiz_m",
             "ul_bits_per_sym", "ul_code_rate", "ul_nb_rbs", "dl_bler",
             "dl_harq_nok", "tcp_retransmits", "rtt_avg_ms", "ping_loss_pct",
             "vid_goodput_mbps", "vid_frame_delivery_pct", "vid_latency_ms"]

LADDER = [
    ("radio_blind", ["geom", "radio", "action"]),
    ("+c2hist",     ["geom", "radio", "action", "c2hist"]),
    ("+ping",       ["geom", "radio", "action", "c2hist", "ping"]),
    ("+video",      ["geom", "radio", "action", "c2hist", "ping", "video"]),
]

# Chosen on an INNER by-time split (first 70% of each run trains, last 30%
# validates), never on the LOTO folds that report the result -- every terrain
# is on both sides of that split, so the choice never sees a held-out terrain.
#
# The first configuration tried, 400 trees x 63 leaves, memorised the training
# terrains: train ROC 0.987 against val 0.877, a +0.110 gap. Everything from
# 200x31 downward is tied on validation (PR 0.884-0.887), so the gap is what
# separates them. Taking the middle of that flat region rather than its
# argmax -- a flat curve's maximum is noise.
PARAMS = dict(
    objective="binary", learning_rate=0.05, num_leaves=15,
    min_child_samples=300, subsample=0.8, subsample_freq=1,
    colsample_bytree=0.8, reg_lambda=5.0, n_estimators=300,
    verbose=-1, n_jobs=-1,
)


def fit_predict(Xtr, ytr, Xte):
    m = lgb.LGBMClassifier(**PARAMS)
    m.fit(Xtr, ytr)
    return m, m.predict_proba(Xtr)[:, 1], m.predict_proba(Xte)[:, 1]


def run(d: pd.DataFrame, ycol: str, cols: list[str], split_name: str,
        folds, rung: str, horizon: int, keep_preds: bool = False):
    rows, imps, preds = [], [], []
    pooled_y, pooled_p, pooled_t = [], [], []
    X = d[cols]
    ds.assert_terrain_blind(X.columns)      # the guard, on every matrix
    y = d[ycol].values
    for fold, tr, te in folds:
        if tr.sum() == 0 or te.sum() == 0 or len(np.unique(y[tr])) < 2:
            continue
        m, p_tr, p_te = fit_predict(X[tr], y[tr], X[te])
        thr = train_threshold(p_tr, y[tr])
        r = score(y[te], p_te, thr)
        r.update(split=split_name, fold=fold, model=rung, horizon_s=horizon,
                 n_features=len(cols))
        rows.append(r)
        pooled_y.append(y[te]); pooled_p.append(p_te)
        pooled_t.append(np.full(te.sum(), thr))
        imps.append(pd.Series(m.booster_.feature_importance("gain"),
                              index=cols, name=fold))
        if keep_preds:
            # Per-row held-out scores. Saved so Phase 4 and any calibration
            # analysis can work from these instead of refitting (a full
            # ladder is ~30 min).
            preds.append(pd.DataFrame({
                "fold": fold, "model": rung, "horizon_s": horizon,
                "split": split_name, "y": y[te], "p": p_te,
                "run_id": d["run_id"].values[te],
                "robot": d["robot"].values[te],
                "terrain": d["terrain"].values[te],
                "policy": d["policy"].values[te],
                "t_elapsed_s": d["t_elapsed_s"].values[te]}))
    if pooled_y:
        yy = np.concatenate(pooled_y); pp = np.concatenate(pooled_p)
        tt = np.concatenate(pooled_t)
        yhat = (pp >= tt).astype(int)
        from sklearn.metrics import average_precision_score, roc_auc_score
        pos = float(yy.mean())
        # Pooling folds compares scores produced by DIFFERENT models. A
        # learned model's probability scale shifts between terrains, so the
        # pooled ranking is penalised for miscalibration even when the model
        # ranks correctly inside every fold. The rank-pooled figures remove
        # that scale difference (each fold's scores -> within-fold
        # percentiles) and so isolate discrimination from calibration.
        from scipy.stats import rankdata
        rr = np.concatenate([rankdata(p) / len(p) for p in pooled_p])
        rows.append(dict(
            split=split_name, fold="POOLED", model=rung, horizon_s=horizon,
            n=len(yy), pos_rate=pos, n_features=len(cols),
            pr_auc=float(average_precision_score(yy, pp)),
            pr_lift=float(average_precision_score(yy, pp) / pos),
            roc_auc=float(roc_auc_score(yy, pp)),
            bal_acc=float((yhat[yy == 1].mean()
                           + 1 - yhat[yy == 0].mean()) / 2),
            pr_auc_rankpooled=float(average_precision_score(yy, rr)),
            roc_auc_rankpooled=float(roc_auc_score(yy, rr))))
    imp = (pd.concat(imps, axis=1).mean(axis=1).sort_values(ascending=False)
           if imps else pd.Series(dtype=float))
    pred = (pd.concat(preds, ignore_index=True) if preds
            else pd.DataFrame())
    return pd.DataFrame(rows), imp, pred


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--horizons", type=float, nargs="+", default=[60.0])
    ap.add_argument("--full", action="store_true",
                    help="all three splits (default: LOTO only)")
    ap.add_argument("--out", default=str(OUT_DIR))
    args = ap.parse_args()

    t0 = time.time()
    df = ds.build()
    df = ds.add_history(df, HIST_COLS, lags=(1, 2, 3))
    print(f"long table {len(df):,} rows x {df.shape[1]} cols "
          f"({time.time()-t0:.0f}s)")

    all_rows, all_imp, all_pred = [], {}, []
    for h in args.horizons:
        d = ds.add_target(df, h, "c2_ok").reset_index(drop=True)
        ycol = f"y_down_h{int(h)}"
        d[ycol] = 1.0 - d[f"y_c2_ok_h{int(h)}"]
        d = d[d[ycol].notna()].reset_index(drop=True)
        meta = d[ds.META]
        print(f"\nh={int(h)}s  {len(d):,} labelled rows  "
              f"DOWN {100*d[ycol].mean():.1f}%")

        splits = [("LOTO", list(ds.leave_one_terrain_out(meta)))]
        if args.full:
            splits += [("by_seed", list(ds.by_run_seed(meta))),
                       ("by_time", list(ds.by_time_block(meta)))]

        for rung, groups in LADDER:
            cols = ds.feature_columns(d, groups, with_history=True)
            for sname, folds in splits:
                r, imp, pr = run(d, ycol, cols, sname, folds, rung, int(h),
                                 keep_preds=(sname == "LOTO"))
                all_rows.append(r)
                if sname == "LOTO":
                    all_imp[(int(h), rung)] = imp
                    all_pred.append(pr)
                pooled = r[r.fold == "POOLED"]
                if not pooled.empty:
                    q = pooled.iloc[0]
                    print(f"  {rung:<12} {sname:<8} {len(cols):>3}f  "
                          f"PR-AUC {q.pr_auc:.3f}  ({q.pr_lift:.2f}x)  "
                          f"ROC {q.roc_auc:.3f}  bal {q.bal_acc:.3f}  "
                          f"[{time.time()-t0:.0f}s]")

    res = pd.concat(all_rows, ignore_index=True)
    outd = Path(args.out); outd.mkdir(parents=True, exist_ok=True)
    res.to_csv(outd / "phase2_forecaster.csv", index=False)
    imp_df = pd.DataFrame({f"h{h}_{r}": v for (h, r), v in all_imp.items()})
    imp_df.to_csv(outd / "phase2_importance.csv")
    if all_pred:
        pd.concat(all_pred, ignore_index=True).to_csv(
            outd / "phase2_predictions.csv.gz", index=False)
    print(f"\nwrote {outd/'phase2_forecaster.csv'} "
          f"and phase2_importance.csv  ({time.time()-t0:.0f}s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
