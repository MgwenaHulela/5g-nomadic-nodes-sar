#!/usr/bin/env python3
"""Phase 1 — baselines.

Nothing later in this project means anything until these numbers exist. A
forecaster that beats nothing is not a result; a forecaster that beats
*persistence* is.

The task, stated once and used everywhere downstream:

    Given everything observable up to time t, predict whether the C2 link to
    THIS robot is DOWN at t + h.

The positive class is the failure, not the healthy state. C2 is available
71.5% of the time overall, so "predict available" scores 71.5% accuracy while
being useless — the whole point is to catch the 28.5%. PR-AUC on the rare
class is therefore the headline metric, and the no-skill PR-AUC (the test
block's own positive rate) is printed beside every number.

The four baselines, and what each one is for:

  majority           The floor. Constant prediction at the TRAIN positive
                     rate. Its PR-AUC equals the test positive rate by
                     construction; its balanced accuracy is 0.5. Any model
                     not clearly above this is noise.

  persistence        The real bar. "It will be what it is now." Most link
                     state is autocorrelated, so this is hard to beat and is
                     the number a predictive claim has to clear. Reported
                     both pure (current state) and smoothed (3-cycle mean,
                     which gives a graded score instead of 0/1 and so a
                     meaningful PR-AUC).

  range_only         The falsifiable prediction, stated in advance. §3.4
                     found corr(slant range, RSRP) = -0.72 in open_field but
                     -0.06..-0.08 in urban/forest/hills. If that is real,
                     a distance-only model should be near-useless everywhere
                     except open_field. If range_only scores well in forest
                     and hills, §3.4 is wrong and so is the premise of the
                     whole terrain-blind argument.

  terrain_oracle     The ceiling, and the cost of blindness. Two forms, both
                     fitted ON THE HELD-OUT TERRAIN ITSELF and therefore
                     in-sample and deliberately optimistic:
                       *_rate  — the terrain's own empirical failure rate
                                 (what pure terrain identity buys)
                       *_range — distance model refitted inside the terrain
                                 (what terrain-specific calibration buys)
                     These are upper bounds, not competitors. The gap between
                     a terrain-blind model and these is what terrain
                     blindness costs.

Splits are never random rows (dataset.py explains why). LOTO is primary.

Usage:
    python3 scripts/ml/baselines.py                  # 60 s, all splits
    python3 scripts/ml/baselines.py --horizons 30 60 120
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import average_precision_score, roc_auc_score

sys.path.insert(0, str(Path(__file__).resolve().parent))
import dataset as ds  # noqa: E402

OUT_DIR = ds.REPO / "logs" / "ml"
RANGE_FEATS = ["d_slant_m"]
GEOM_FEATS = ds.FEATURE_GROUPS["geom"]


# --------------------------------------------------------------------------
# Metrics
# --------------------------------------------------------------------------
def score(y: np.ndarray, p: np.ndarray, thr: float) -> dict:
    """PR-AUC, ROC-AUC and balanced accuracy at a TRAIN-chosen threshold.

    The threshold is never tuned on the test block — that would turn the
    baseline into a model with hindsight and flatter it.
    """
    pos = float(y.mean())
    out = {"n": int(len(y)), "pos_rate": pos}
    if pos in (0.0, 1.0):               # degenerate fold: ranking undefined
        out.update(pr_auc=np.nan, pr_lift=np.nan, roc_auc=np.nan,
                   bal_acc=np.nan)
        return out
    yhat = (p >= thr).astype(int)
    tpr = yhat[y == 1].mean() if (y == 1).any() else np.nan
    tnr = 1 - yhat[y == 0].mean() if (y == 0).any() else np.nan
    const = np.allclose(p, p[0])
    out.update(
        pr_auc=float(average_precision_score(y, p)),
        pr_lift=float(average_precision_score(y, p) / pos),
        roc_auc=np.nan if const else float(roc_auc_score(y, p)),
        bal_acc=float((tpr + tnr) / 2),
    )
    return out


def train_threshold(p_tr: np.ndarray, y_tr: np.ndarray) -> float:
    """Operate at the train prevalence: flag the top `pos_rate` of scores.

    Fairer than 0.5 for scores that are not calibrated probabilities, and it
    uses train information only.
    """
    pos = float(y_tr.mean())
    if pos <= 0 or pos >= 1:
        return 0.5
    # A 0/1 score (pure persistence) has no useful quantile: the (1-pos)
    # quantile of a mostly-zero vector is 0.0, and `p >= 0` flags every row,
    # which would report balanced accuracy 0.5 for a predictor that is in
    # fact informative. Threshold a binary score at its midpoint instead.
    u = np.unique(p_tr[np.isfinite(p_tr)])
    if len(u) <= 2:
        return float(u.mean()) if len(u) == 2 else float(u[0]) + 1e-9
    return float(np.quantile(p_tr, 1 - pos))


# --------------------------------------------------------------------------
# The baselines. Each returns (p_train, p_test).
# --------------------------------------------------------------------------
def _fit_logit(Xtr, ytr, Xte):
    med = np.nanmedian(Xtr, axis=0)
    med = np.where(np.isfinite(med), med, 0.0)
    Xtr = np.where(np.isfinite(Xtr), Xtr, med)
    Xte = np.where(np.isfinite(Xte), Xte, med)
    mu, sd = Xtr.mean(0), Xtr.std(0)
    sd = np.where(sd > 0, sd, 1.0)
    m = LogisticRegression(max_iter=2000, C=1.0)
    m.fit((Xtr - mu) / sd, ytr)
    return (m.predict_proba((Xtr - mu) / sd)[:, 1],
            m.predict_proba((Xte - mu) / sd)[:, 1])


def run_baselines(d: pd.DataFrame, tr: np.ndarray, te: np.ndarray,
                  ycol: str) -> dict[str, tuple]:
    y = d[ycol].values
    ytr, yte = y[tr], y[te]
    base = float(ytr.mean())
    out: dict[str, tuple] = {}

    out["majority"] = (np.full(tr.sum(), base), np.full(te.sum(), base))

    # "it will be what it is now" — current DOWN state, 0/1.
    cur = 1.0 - d["c2_ok"].values
    cur = np.where(np.isfinite(cur), cur, base)
    out["persistence"] = (cur[tr], cur[te])

    # Smoothed: 3-cycle mean of the current state. Graded, so PR-AUC can
    # actually rank; this is the strongest honest no-model predictor.
    roll = 1.0 - d["c2_ok_roll3_mean"].values
    roll = np.where(np.isfinite(roll), roll, base)
    out["persistence_roll3"] = (roll[tr], roll[te])

    Xr = d[RANGE_FEATS].values.astype(float)
    out["range_only"] = _fit_logit(Xr[tr], ytr, Xr[te])

    Xg = d[GEOM_FEATS].values.astype(float)
    out["geom_only"] = _fit_logit(Xg[tr], ytr, Xg[te])

    # --- oracles: fitted on the held-out terrain itself (in-sample) --------
    rate = float(yte.mean())
    out["oracle_terrain_rate"] = (np.full(tr.sum(), base),
                                  np.full(te.sum(), rate))
    try:
        _, p_in = _fit_logit(Xr[te], yte, Xr[te])
    except ValueError:                       # single-class held-out block
        p_in = np.full(te.sum(), rate)
    out["oracle_terrain_range"] = (np.full(tr.sum(), base), p_in)
    return out


# --------------------------------------------------------------------------
# Diagnostic: the sign of the range effect, per terrain.
# --------------------------------------------------------------------------
def range_sign_table(d: pd.DataFrame, ycol: str) -> pd.DataFrame:
    """Coefficient of slant range when predicting DOWN, pooled and per terrain.

    This is the one diagnostic that explains the whole range_only result.
    A single-feature logistic is monotone, so its PR-AUC/ROC-AUC depend only
    on the SIGN of this coefficient -- which means a sign that differs
    between terrains makes a pooled range model actively harmful on the
    terrains whose sign it got wrong, not merely uninformative.
    """
    rows = []
    for key, sub in [("POOLED", d)] + list(d.groupby("terrain")):
        x = sub[["d_slant_m"]].values.astype(float)
        y = sub[ycol].values
        m = np.isfinite(x[:, 0])
        x, y = x[m], y[m]
        if len(np.unique(y)) < 2:
            continue
        mu, sd = x.mean(), (x.std() or 1.0)
        lr = LogisticRegression(max_iter=2000).fit((x - mu) / sd, y)
        rows.append(dict(terrain=key, coef=float(lr.coef_[0][0]), n=len(y),
                         down_rate=float(y.mean()),
                         median_range_m=float(np.median(x))))
    return pd.DataFrame(rows)


# --------------------------------------------------------------------------
# Driver
# --------------------------------------------------------------------------
def evaluate(d: pd.DataFrame, meta: pd.DataFrame, ycol: str,
             split_name: str, folds) -> pd.DataFrame:
    rows, pooled = [], {}
    for fold, tr, te in folds:
        if te.sum() == 0 or tr.sum() == 0:
            continue
        preds = run_baselines(d, tr, te, ycol)
        yte = d[ycol].values[te]
        for name, (p_tr, p_te) in preds.items():
            thr = train_threshold(p_tr, d[ycol].values[tr])
            r = score(yte, p_te, thr)
            r.update(split=split_name, fold=fold, model=name)
            rows.append(r)
            pooled.setdefault(name, ([], [], [], []))
            pooled[name][0].append(yte)
            pooled[name][1].append(p_te)
            pooled[name][2].append(np.full(len(p_te), thr))
            pooled[name][3].append(bool(np.allclose(p_te, p_te[0])))
    for name, (ys, ps, ts, consts) in pooled.items():
        # `majority` is constant at the TRAIN base rate, so pooling its folds
        # ranks rows by which fold they came from -- not a score, blanked
        # below. `oracle_terrain_rate` is also constant per fold, but its
        # constant is the HELD-OUT terrain's own failure rate, so the pooled
        # ranking is exactly the thing the oracle is meant to measure: what
        # knowing the terrain, and nothing else, is worth.
        consts = consts if name != "oracle_terrain_rate" else [False]
        y, p, t = np.concatenate(ys), np.concatenate(ps), np.concatenate(ts)
        # pooled threshold decision uses each fold's own train threshold
        pos = float(y.mean())
        yhat = (p >= t).astype(int)
        tpr = yhat[y == 1].mean() if (y == 1).any() else np.nan
        tnr = 1 - yhat[y == 0].mean() if (y == 0).any() else np.nan
        rows.append(dict(split=split_name, fold="POOLED", model=name,
                         n=len(y), pos_rate=pos,
                         pr_auc=(np.nan if all(consts)
                                 else float(average_precision_score(y, p))),
                         pr_lift=(np.nan if all(consts) else
                                  float(average_precision_score(y, p) / pos)),
                         # If every fold's score was constant, the pooled
                         # ranking encodes only which fold a row came from,
                         # not anything about the row. Not a score.
                         roc_auc=(np.nan if all(consts)
                                  else float(roc_auc_score(y, p))),
                         pr_auc_note=("fold-identity only" if all(consts)
                                      else ""),
                         bal_acc=float((tpr + tnr) / 2)))
    return pd.DataFrame(rows)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--horizons", type=float, nargs="+", default=[60.0])
    ap.add_argument("--no-cache", action="store_true")
    ap.add_argument("--out", default=str(OUT_DIR))
    args = ap.parse_args()

    df = ds.build(cache=not args.no_cache)
    df = ds.add_history(df, ["c2_ok", "d_slant_m"], lags=(1, 2, 3))
    print(f"long table: {len(df):,} rows, {df['run_id'].nunique()} runs")

    all_rows, sign_tables = [], []
    for h in args.horizons:
        d = ds.add_target(df, h, "c2_ok").reset_index(drop=True)
        ycol = f"y_down_h{int(h)}"
        d[ycol] = 1.0 - d[f"y_c2_ok_h{int(h)}"]
        d = d[d[ycol].notna()].reset_index(drop=True)
        meta = d[ds.META]
        ds.assert_terrain_blind(RANGE_FEATS + GEOM_FEATS)
        print(f"\nhorizon {int(h)}s: {len(d):,} labelled rows, "
              f"DOWN rate {100*d[ycol].mean():.1f}%")

        sign = range_sign_table(d, ycol)
        sign["horizon_s"] = int(h)
        sign_tables.append(sign)

        splits = [("LOTO", list(ds.leave_one_terrain_out(meta))),
                  ("by_seed", list(ds.by_run_seed(meta))),
                  ("by_time", list(ds.by_time_block(meta)))]
        for sname, folds in splits:
            r = evaluate(d, meta, ycol, sname, folds)
            r["horizon_s"] = int(h)
            all_rows.append(r)

    res = pd.concat(all_rows, ignore_index=True)
    outd = Path(args.out)
    outd.mkdir(parents=True, exist_ok=True)
    res.to_csv(outd / "phase1_baselines.csv", index=False)
    signs = pd.concat(sign_tables, ignore_index=True)
    signs.to_csv(outd / "phase1_range_sign.csv", index=False)
    print("\n=== range effect: logistic coef on slant range, "
          "predicting C2 DOWN ===")
    print("    (+ = further is worse, the naive expectation; "
          "- = further is BETTER)")
    for h in sorted(signs.horizon_s.unique()):
        print(f"  h={h}s")
        print(signs[signs.horizon_s == h].drop(columns="horizon_s")
              .to_string(index=False,
                         float_format=lambda v: f"{v:.3f}"))

    order = ["majority", "persistence", "persistence_roll3", "range_only",
             "geom_only", "oracle_terrain_rate", "oracle_terrain_range"]
    for h in sorted(res["horizon_s"].unique()):
        for sname in ["LOTO", "by_seed", "by_time"]:
            sub = res[(res.horizon_s == h) & (res.split == sname)]
            if sub.empty:
                continue
            print(f"\n=== h={h}s  split={sname} "
                  f"(positive class = C2 DOWN at t+{h}s) ===")
            for metric in ("pr_auc", "roc_auc"):
                piv = sub.pivot_table(index="fold", columns="model",
                                      values=metric)
                piv = piv.reindex(
                    columns=[c for c in order if c in piv.columns])
                npos = sub.groupby("fold")[["n", "pos_rate"]].first()
                lbl = ("PR-AUC (no-skill = pos_rate)" if metric == "pr_auc"
                       else "ROC-AUC (no-skill = 0.500)")
                print(f"  -- {lbl}")
                print(npos.join(piv).to_string(
                    float_format=lambda v: f"{v:.3f}"))
    print(f"\nwrote {outd/'phase1_baselines.csv'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
