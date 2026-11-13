#!/usr/bin/env python3
"""Phase 2b — ONSET forecasting, with dead-UE stretches removed from training.

Four corrections to the Phase 2 result, all on existing logs:

  1. TRAIN ON THE TASK WE SCORE.  Phase 2 trained on "is C2 down at t+h" over
     all rows, but the only honest evaluation is on rows where C2 is UP at t
     ("will it FAIL within h"). More than half of Phase 2's training positives
     were rows already down, so the model was optimised to recognise a state
     rather than anticipate a transition. Here the training population IS the
     link-UP population.

  2. CENSOR PROCESS DEATH FROM TRAINING, not just evaluation.  A real coverage
     RLF exits nr-uesoftmodem and only a relaunch recovers C2. In the logs the
     UE's disappearance is unmistakable: the gNB-side counters stop updating
     and repeat their last value verbatim for hundreds of cycles --

         t=6391.5  dl_bler 0.000939  ul_nb_rbs 67.2  bits_per_sym 6.0
         t=6405.8  dl_bler 0.001001  ul_nb_rbs 67.3  bits_per_sym 6.0
         t=6421.8  dl_bler 0.001001  ul_nb_rbs 67.3  bits_per_sym 6.0   <- dead
         ...        (613 more identical cycles)

     That freeze is both the correct censoring rule AND, with history features
     on, a trivially learnable tell: `dl_bler_d1 == 0` exactly. It is the
     mechanism behind hills' apparent ROC 0.878. `dead_ue_mask()` finds it
     from the data, replacing the blanket ">=50 cycles down" heuristic.

  3. SWEEP THE HORIZON.  60 s was an assumption. If skill rises sharply at
     20 s there is a real forecaster with an honest horizon; if the curve is
     flat all the way down, the channel (AWGN, no multipath) simply has no
     short-horizon dynamics to read and that is the finding.

  4. CALIBRATE.  Phase 4's recovered sign flip had perfect rank correlation
     with the measured effect and a uniform downward offset -- textbook
     miscalibration, not a perception failure. Isotonic regression is fitted
     on an inner by-time split of the TRAINING terrains only, never on the
     held-out terrain.

Baselines on the onset subset, because persistence is useless there by
construction ("it is up, so it stays up" scores zero recall):
  no_skill     the subset's own positive rate
  range_only   logistic on slant range alone -- the falsifiable §3.4 claim
  trend        logistic on recent radio trend (d1 of bler/MCS/rbs)

Usage:
    python3 scripts/ml/onset_forecast.py                 # 10 20 30 60 s
    python3 scripts/ml/onset_forecast.py --horizons 60
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd
from sklearn.isotonic import IsotonicRegression
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import average_precision_score, roc_auc_score
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.impute import SimpleImputer

sys.path.insert(0, str(Path(__file__).resolve().parent))
import dataset as ds                                      # noqa: E402
from forecast_cycle import HIST_COLS, PARAMS              # noqa: E402
from baselines import train_threshold                     # noqa: E402

OUT_DIR = ds.REPO / "logs" / "ml"

# Columns that freeze verbatim when the UE process is gone from the gNB.
FREEZE_KEY = ["dl_bler", "ul_nb_rbs", "ul_bits_per_sym"]
FREEZE_MIN = 3          # consecutive identical cycles before we call it dead

LADDER = [
    ("radio_blind", ["geom", "radio", "action"]),
    ("+c2hist",     ["geom", "radio", "action", "c2hist"]),
]


# --------------------------------------------------------------------------
# 2. Process-death detection
# --------------------------------------------------------------------------
def dead_ue_mask(df: pd.DataFrame, min_len: int = FREEZE_MIN) -> np.ndarray:
    """Rows inside a stretch where the UE process is gone.

    Two conditions together, because either alone is wrong: the gNB counters
    must be frozen verbatim (the UE is not being scheduled at all) AND C2 must
    be down (a frozen counter while the link works is just a quiet cycle).
    """
    df = df.sort_values(["run_id", "robot", "t_elapsed_s"], kind="stable")
    g = df.groupby(["run_id", "robot"], sort=False)
    frozen = np.ones(len(df), bool)
    for c in FREEZE_KEY:
        if c not in df.columns:
            continue
        frozen &= (df[c] - g[c].shift(1)).abs().fillna(1e9).values < 1e-12
    frozen &= (df["c2_ok"].values == 0)

    dead = np.zeros(len(df), bool)
    for _, idx in g.indices.items():
        s = frozen[idx]
        i = 0
        while i < len(s):
            if s[i]:
                j = i
                while j + 1 < len(s) and s[j + 1]:
                    j += 1
                if (j - i + 1) >= min_len:
                    dead[idx[i:j + 1]] = True
                i = j + 1
            else:
                i += 1
    out = pd.Series(dead, index=df.index)
    return out.reindex(range(len(df))).values if df.index.equals(
        pd.RangeIndex(len(df))) else out.sort_index().values


# --------------------------------------------------------------------------
# 4. Calibration — fitted inside the training terrains only
# --------------------------------------------------------------------------
def inner_time_split(meta: pd.DataFrame, frac: float = 0.3) -> np.ndarray:
    """Last `frac` of each run -> calibration set. No window leakage."""
    cal = np.zeros(len(meta), bool)
    for _, idx in meta.groupby("run_id", sort=False).indices.items():
        t = meta["t_elapsed_s"].values[idx]
        cal[idx[t >= np.quantile(t, 1 - frac)]] = True
    return cal


def fit_calibrated(Xtr, ytr, meta_tr):
    """LightGBM + isotonic, both fitted strictly within the training fold."""
    cal = inner_time_split(meta_tr)
    if cal.sum() < 200 or len(np.unique(ytr[~cal])) < 2 \
            or len(np.unique(ytr[cal])) < 2:
        m = lgb.LGBMClassifier(**PARAMS).fit(Xtr, ytr)
        return m, None
    inner = lgb.LGBMClassifier(**PARAMS).fit(Xtr[~cal], ytr[~cal])
    p_cal = inner.predict_proba(Xtr[cal])[:, 1]
    iso = IsotonicRegression(out_of_bounds="clip").fit(p_cal, ytr[cal])
    full = lgb.LGBMClassifier(**PARAMS).fit(Xtr, ytr)
    return full, iso


# --------------------------------------------------------------------------
# Simple reference models for the onset subset
# --------------------------------------------------------------------------
def _linear(cols):
    return make_pipeline(SimpleImputer(strategy="median"),
                         StandardScaler(),
                         LogisticRegression(max_iter=2000, C=1.0))


TREND_FEATS = ["dl_bler_d1", "ul_bits_per_sym_d1", "ul_nb_rbs_d1",
               "dl_bler_roll3_mean", "ul_bits_per_sym_roll3_mean",
               "d_slant_m_d1"]


def reference_models(d, tr, te, feats_range, feats_trend):
    out = {}
    for name, cols in (("range_only", feats_range), ("trend", feats_trend)):
        cols = [c for c in cols if c in d.columns]
        if not cols:
            continue
        try:
            mdl = _linear(cols).fit(d.loc[tr, cols], d.loc[tr, "y"])
            out[name] = mdl.predict_proba(d.loc[te, cols])[:, 1]
        except Exception:
            pass
    return out


def summarise(y, p):
    if len(y) < 50 or y.min() == y.max():
        return None
    base = float(y.mean())
    pr = float(average_precision_score(y, p))
    return dict(n=len(y), pos_rate=base, pr_auc=pr, pr_lift=pr / base,
                roc_auc=float(roc_auc_score(y, p)))


# --------------------------------------------------------------------------
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--horizons", type=float, nargs="+",
                    default=[10.0, 20.0, 30.0, 60.0])
    ap.add_argument("--out", default=str(OUT_DIR))
    args = ap.parse_args()

    t0 = time.time()
    raw = ds.build().sort_values(["run_id", "robot", "t_elapsed_s"],
                                 kind="stable").reset_index(drop=True)
    raw["dead"] = dead_ue_mask(raw)
    print(f"long table {len(raw):,} rows   "
          f"dead-UE rows {100*raw['dead'].mean():.1f}%  "
          f"({int(raw['dead'].sum()):,})")
    print("\ndead-UE share of DOWN labels, per terrain:")
    for t, g in raw.groupby("terrain"):
        dn = (g["c2_ok"] == 0)
        print(f"  {t:<11} {100*(g['dead'] & dn).sum()/max(dn.sum(),1):5.1f}%"
              f"   C2 avail {100*g['c2_ok'].mean():5.1f}% raw ->"
              f" {100*g.loc[~g['dead'],'c2_ok'].mean():5.1f}% censored")

    df = ds.add_history(raw, HIST_COLS, lags=(1, 2, 3))
    rows = []

    for h in args.horizons:
        d = ds.add_target(df, h, "c2_ok")
        d = ds.add_target(d, h, "dead").reset_index(drop=True)
        d["y"] = 1.0 - d[f"y_c2_ok_h{int(h)}"]
        dead_at_tgt = d[f"y_dead_h{int(h)}"].fillna(0) > 0.5

        # (1) onset population, (2) censored at t AND at t+h
        keep = (d["y"].notna() & (d["c2_ok"] == 1)
                & ~d["dead"] & ~dead_at_tgt)
        d = d[keep].reset_index(drop=True)
        print(f"\n{'='*78}\nHORIZON {int(h)} s   onset rows {len(d):,}   "
              f"FAIL within {int(h)}s: {100*d['y'].mean():.1f}%"
              f"   [{time.time()-t0:.0f}s]\n{'='*78}")
        if len(d) < 500 or d["y"].nunique() < 2:
            print("  -- too few onset rows --")
            continue

        feats_range = ["d_slant_m", "d_horiz_m"]
        pooled = {}
        for rung, groups in LADDER:
            feats = ds.feature_columns(d, groups, with_history=True)
            ds.assert_terrain_blind(feats)
            acc = {k: [] for k in ("raw", "cal", "range_only", "trend")}
            accy = []
            per_terr = []
            for terr, tr, te in ds.leave_one_terrain_out(d[ds.META]):
                if len(np.unique(d.loc[tr, "y"])) < 2:
                    continue
                m, iso = fit_calibrated(d.loc[tr, feats], d.loc[tr, "y"].values,
                                        d.loc[tr, ds.META])
                p_raw = m.predict_proba(d.loc[te, feats])[:, 1]
                p_cal = iso.predict(p_raw) if iso is not None else p_raw
                thr = train_threshold(m.predict_proba(d.loc[tr, feats])[:, 1],
                                      d.loc[tr, "y"].values)
                y_te = d.loc[te, "y"].values
                s = summarise(y_te, p_raw)
                if s:
                    f = (p_raw >= thr).astype(int)
                    TP = ((y_te == 1) & (f == 1)).sum()
                    FP = ((y_te == 0) & (f == 1)).sum()
                    FN = ((y_te == 1) & (f == 0)).sum()
                    s.update(terrain=terr, model=rung, horizon_s=int(h),
                             recall=TP/max(TP+FN, 1), precision=TP/max(TP+FP, 1))
                    per_terr.append(s)
                    rows.append(s)
                accy.append(y_te)
                acc["raw"].append(p_raw); acc["cal"].append(p_cal)
                for k, v in reference_models(d, tr, te, feats_range,
                                             TREND_FEATS).items():
                    acc[k].append(v)

            yy = np.concatenate(accy)
            print(f"\n  MODEL {rung}  ({len(feats)} features)")
            print(f"  {'held out':<12}{'n':>8}{'FAIL%':>7}{'PR-AUC':>9}"
                  f"{'xno-skill':>11}{'ROC':>7}{'recall':>8}{'prec':>7}")
            for s in per_terr:
                print(f"  {s['terrain']:<12}{s['n']:>8,}{s['pos_rate']*100:>7.1f}"
                      f"{s['pr_auc']:>9.3f}{s['pr_lift']:>11.2f}"
                      f"{s['roc_auc']:>7.3f}{s['recall']*100:>7.1f}%"
                      f"{s['precision']*100:>6.1f}%")
            for k in ("raw", "cal", "range_only", "trend"):
                if not acc[k] or len(acc[k]) != len(accy):
                    continue
                s = summarise(yy, np.concatenate(acc[k]))
                if not s:
                    continue
                tag = {"raw": "POOLED", "cal": "POOLED calibrated"}.get(k, k)
                print(f"  {tag:<20} n={s['n']:,}  PR {s['pr_auc']:.3f} "
                      f"({s['pr_lift']:.2f}x)  ROC {s['roc_auc']:.3f}")
                rows.append(dict(terrain="POOLED", model=f"{rung}|{k}",
                                 horizon_s=int(h), **s))
                if rung == LADDER[0][0] and k in ("range_only", "trend"):
                    pooled[k] = s
            print(f"  {'no_skill':<20} n={len(yy):,}  PR {yy.mean():.3f} "
                  f"(1.00x)  ROC 0.500")

    res = pd.DataFrame(rows)
    outd = Path(args.out); outd.mkdir(parents=True, exist_ok=True)
    res.to_csv(outd / "phase2b_onset.csv", index=False)
    print(f"\nwrote {outd/'phase2b_onset.csv'}   ({time.time()-t0:.0f}s)")

    print("\n" + "=" * 78)
    print("HORIZON SWEEP — pooled radio_blind, raw vs calibrated")
    print("=" * 78)
    p = res[res.terrain == "POOLED"]
    print(f"  {'h (s)':>6}{'model':>22}{'PR-AUC':>9}{'xno-skill':>11}{'ROC':>8}")
    for _, r in p.sort_values(["horizon_s", "model"]).iterrows():
        print(f"  {r.horizon_s:>6}{r.model:>22}{r.pr_auc:>9.3f}"
              f"{r.pr_lift:>11.2f}{r.roc_auc:>8.3f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
