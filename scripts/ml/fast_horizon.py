#!/usr/bin/env python3
"""Phase 3 — fast-horizon forecaster (datasets B and C).

Dataset A (the per-cycle table Phases 1-2 used) has an 11-22 s cadence, so it
cannot say anything about the seconds before a link drops. The overnight RSRP
recovery runs exist for exactly this: four runs, one per terrain, 10 Hz,
~54 k ticks each, with genuine UE-measured SS-RSRP and SS-SINR -- the only
data in the project with a real physical-layer measurement at rate.

The task: **will this robot's RSRP be below the -68 dBm service floor in h
seconds?** at h = 1, 3, 5, 10 s. Two versions, and the second is the useful
one:

  unconditional  every tick. Dominated by ticks that are already below the
                 floor and simply stay there, so persistence is very strong
                 and the headline number means little.
  early warning  ticks that are currently ABOVE the floor. "Is this link
                 about to drop?" -- the question the UAV actually needs
                 answered, and the one where persistence has nothing to say.

Provenance, because the whole project rests on it: these values are parsed
from the UE's own PHY log (`RE_SS_COMBINED` in metrics_collector), not
computed from the simulator's channel model. `ploss_dB` and
`rsrp_computed_dBm` appear nowhere in this file, and the terrain-blind guard
runs on every feature matrix.

Standing caveats (§4.3.4), which bind harder here than anywhere else:
  * **One run per terrain.** Leave-one-terrain-out has a single run on the
    held-out side, so a fold is one UAV trajectory and the CIs are wide.
    This is a secondary result and is scoped as one.
  * **RSRP is censored above at -42 dBm** -- every run hits that ceiling.
  * **`excite` policy only**, so no static-phase behaviour.
  * `n_samples == 0` means nothing fresh arrived and the value was carried
    forward; those ticks are excluded from labels.

Usage:
    python3 scripts/ml/fast_horizon.py --horizons 1 5
    python3 scripts/ml/fast_horizon.py --horizons 1 3 5 10 --model
"""
from __future__ import annotations

import argparse
import glob
import sys
import time
from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import dataset as ds            # noqa: E402

OUT_DIR = ds.REPO / "logs" / "ml"
FLOOR_DBM = -68.0
RATE_HZ = 10
ROBOTS = ["alpha", "bravo", "charlie", "delta"]

# Recovery runs only: these are the four with phy_log_level="debug" and so
# the only ones carrying real SS-RSRP.
RECOVERY = ["campaign_20261025_235902", "campaign_20261026_013033",
            "campaign_20261026_030216", "campaign_20261026_043517"]

PER_ROBOT = ["rsrp_dBm", "rsrp_dBre", "rsrp_dBre_mean", "rsrp_dBre_min",
             "rsrp_dBre_max", "sinr_dB", "n_samples", "age_s"]

# Row offsets at 10 Hz: 0.1 s .. 5 s of history.
LAGS = [1, 2, 5, 10, 20, 50]
WINDOWS = [10, 50, 100]          # 1 s, 5 s, 10 s


def load_long() -> pd.DataFrame:
    frames = []
    for camp in RECOVERY:
        for f in sorted(glob.glob(str(ds.LOG_DIR / camp / "sar_*_fastradio.csv"))):
            w = pd.read_csv(f)
            terr = str(w["terrain"].iloc[0]).replace("sar_", "")
            for r in ROBOTS:
                cols = {f"{r}_{c}": c for c in PER_ROBOT if f"{r}_{c}" in w}
                if not cols:
                    continue
                sub = w[list(cols)].rename(columns=cols).copy()
                sub["t_elapsed_s"] = w["t_elapsed_s"].values
                sub["run_id"] = w["run_id"].iloc[0]
                sub["terrain"] = terr          # metadata only
                sub["robot"] = r
                frames.append(sub)
    d = pd.concat(frames, ignore_index=True)
    for c in PER_ROBOT + ["t_elapsed_s"]:
        if c in d:
            d[c] = pd.to_numeric(d[c], errors="coerce").astype("float32")
    return d.sort_values(["run_id", "robot", "t_elapsed_s"]).reset_index(
        drop=True)


def add_features(d: pd.DataFrame) -> tuple[pd.DataFrame, list[str]]:
    g = d.groupby(["run_id", "robot"], sort=False)
    new, feats = {}, []
    for c in ["rsrp_dBre", "sinr_dB", "rsrp_dBre_min"]:
        for L in LAGS:
            k = f"{c}_lag{L}"
            new[k] = g[c].shift(L).astype("float32"); feats.append(k)
        for L in [1, 10, 50]:
            k = f"{c}_d{L}"
            new[k] = (d[c] - g[c].shift(L)).astype("float32"); feats.append(k)
        for W in WINDOWS:
            k = f"{c}_m{W}"
            new[k] = g[c].transform(
                lambda s, W=W: s.rolling(W, min_periods=2).mean()
            ).astype("float32"); feats.append(k)
            k = f"{c}_s{W}"
            new[k] = g[c].transform(
                lambda s, W=W: s.rolling(W, min_periods=2).std()
            ).astype("float32"); feats.append(k)
            k = f"{c}_min{W}"
            new[k] = g[c].transform(
                lambda s, W=W: s.rolling(W, min_periods=2).min()
            ).astype("float32"); feats.append(k)
    d = pd.concat([d, pd.DataFrame(new, index=d.index)], axis=1)
    feats += ["rsrp_dBre", "sinr_dB", "rsrp_dBre_min", "rsrp_dBre_mean",
              "rsrp_dBre_max", "age_s", "n_samples"]
    # Margin above the floor -- the quantity the planner will care about.
    d["margin_dB"] = (d["rsrp_dBm"] - FLOOR_DBM).astype("float32")
    feats.append("margin_dB")
    ds.assert_terrain_blind(feats)
    return d, feats


def add_target(d: pd.DataFrame, h_s: float) -> pd.DataFrame:
    """RSRP below the floor h seconds ahead, by row offset on the 10 Hz grid.

    The offset is only trusted where the clock actually advanced by ~h; a
    dropped tick would otherwise silently shorten the horizon.
    """
    off = int(round(h_s * RATE_HZ))
    g = d.groupby(["run_id", "robot"], sort=False)
    fut = g["rsrp_dBm"].shift(-off)
    fut_t = g["t_elapsed_s"].shift(-off)
    ok = ((fut_t - d["t_elapsed_s"]) - h_s).abs() <= 0.3 * h_s
    # A stale future tick is a carried-forward value, not a measurement.
    ok &= g["n_samples"].shift(-off) > 0
    y = (fut < FLOOR_DBM).astype("float32")
    d[f"y_h{h_s:g}"] = np.where(ok, y, np.nan)
    return d


def report(y, p, thr, label, n_note=""):
    from sklearn.metrics import average_precision_score, roc_auc_score
    pos = float(y.mean())
    yhat = (p >= thr).astype(int)
    tpr = yhat[y == 1].mean() if (y == 1).any() else np.nan
    tnr = 1 - yhat[y == 0].mean() if (y == 0).any() else np.nan
    const = np.allclose(p, p[0])
    return dict(model=label, n=len(y), pos_rate=pos,
                pr_auc=float(average_precision_score(y, p)),
                pr_lift=float(average_precision_score(y, p) / pos) if pos else np.nan,
                roc_auc=np.nan if const else float(roc_auc_score(y, p)),
                bal_acc=float((tpr + tnr) / 2), note=n_note)


# Same regularised configuration Phase 2 selected on an inner by-time split;
# not retuned here, so nothing is chosen on these folds.
PARAMS = dict(objective="binary", learning_rate=0.05, num_leaves=15,
              min_child_samples=300, subsample=0.8, subsample_freq=1,
              colsample_bytree=0.8, reg_lambda=5.0, n_estimators=300,
              verbose=-1, n_jobs=-1)
NEG_PER_POS = 20


def fit_fold(Xtr, ytr, Xte, seed=0):
    """Fit with the training negatives downsampled.

    Crossings are 0.5-1.5% of ticks, so ~99% of training rows are negatives
    that contribute almost nothing. Keeping every positive and sampling
    NEG_PER_POS negatives per positive cuts the fit from minutes to seconds.
    PR-AUC and ROC-AUC are ranking metrics and are unaffected by the constant
    shift in base rate this induces; the TEST set is never touched.
    """
    rng = np.random.default_rng(seed)
    pos = np.where(ytr == 1)[0]
    neg = np.where(ytr == 0)[0]
    take = min(len(neg), NEG_PER_POS * max(len(pos), 1))
    keep = np.concatenate([pos, rng.choice(neg, take, replace=False)])
    m = lgb.LGBMClassifier(**PARAMS).fit(Xtr.iloc[keep], ytr[keep])
    return m, m.predict_proba(Xte)[:, 1]


def run_model(sub: pd.DataFrame, feats: list[str], yc: str, h: float,
              tag: str):
    """LOTO (one run per terrain) plus a within-run time block."""
    ds.assert_terrain_blind(feats)
    X, y = sub[feats], sub[yc].values
    out, imps = [], []
    terr = sub["terrain"].values
    for t in sorted(pd.unique(terr)):
        tr, te = terr != t, terr == t
        if y[te].sum() < 20 or y[tr].sum() < 20:
            continue
        m, p = fit_fold(X[tr], y[tr], X[te])
        r = report(y[te], p, np.quantile(p, 1 - y[tr].mean()),
                   "lightgbm", n_note=t)
        r.update(split="LOTO", fold=t, horizon_s=h, task=tag)
        out.append(r)
        imps.append(pd.Series(m.booster_.feature_importance("gain"),
                              index=feats, name=f"{tag}_h{h:g}_{t}"))
    # within-run time block: last 30% of each run
    q = sub.groupby("run_id")["t_elapsed_s"].transform(
        lambda s: s >= s.quantile(0.7)).values.astype(bool)
    if y[q].sum() >= 20 and y[~q].sum() >= 20:
        m, p = fit_fold(X[~q], y[~q], X[q])
        r = report(y[q], p, np.quantile(p, 1 - y[~q].mean()), "lightgbm")
        r.update(split="by_time", fold="last_30pct", horizon_s=h, task=tag)
        out.append(r)
    return pd.DataFrame(out), imps


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--horizons", type=float, nargs="+", default=[1, 3, 5, 10])
    ap.add_argument("--model", action="store_true", help="also fit LightGBM")
    args = ap.parse_args()

    t0 = time.time()
    d = load_long()
    print(f"dataset B: {len(d):,} robot-ticks  "
          f"{d.run_id.nunique()} runs x {len(ROBOTS)} robots  "
          f"({time.time()-t0:.0f}s)")
    print(f"  stale ticks (n_samples==0): "
          f"{100*(d.n_samples==0).mean():.2f}%")
    print(f"  below floor now: {100*(d.rsrp_dBm<FLOOR_DBM).mean():.2f}%")
    d, feats = add_features(d)
    print(f"  {len(feats)} features, terrain-blind guard passed")

    rows, all_imp = [], []
    for h in args.horizons:
        d = add_target(d, h)
        yc = f"y_h{h:g}"
        base = d[d[yc].notna() & (d.n_samples > 0)]
        early = base[base.rsrp_dBm >= FLOOR_DBM]
        print(f"\n--- h={h:g}s ---  labelled {len(base):,}  "
              f"below@t+h {100*base[yc].mean():.2f}%  |  "
              f"early-warning subset {len(early):,}  "
              f"crossings {100*early[yc].mean():.3f}%")

        for tag, sub in [("all", base), ("early_warning", early)]:
            y = sub[yc].values
            if y.sum() < 50:
                print(f"    [{tag}] too few positives ({int(y.sum())}) — skipped")
                continue
            pos = float(y.mean())
            res = []
            # persistence: it is below now, so it will be below then
            p_now = (sub.rsrp_dBm < FLOOR_DBM).astype(float).values
            res.append(report(y, p_now, 0.5, "persistence"))
            # margin: rank by how close to the floor it already is
            res.append(report(y, -sub.margin_dB.values,
                              np.quantile(-sub.margin_dB.values, 1 - pos),
                              "margin_now"))
            # linear extrapolation of the last 1 s of RSRP. The slope is
            # undefined for the first second of each series; treating that as
            # "no trend" keeps every baseline scored on an identical row set,
            # which dropping the rows would not.
            slope = (sub.rsrp_dBre - sub.rsrp_dBre_lag10).values  # per 1 s
            slope = np.where(np.isfinite(slope), slope, 0.0)
            proj = sub.margin_dB.values + slope * h
            proj = np.where(np.isfinite(proj), proj, sub.margin_dB.values)
            res.append(report(y, -proj, np.quantile(-proj, 1 - pos),
                              "extrapolate"))
            r = pd.DataFrame(res)
            r["horizon_s"] = h; r["task"] = tag
            r["split"] = "none"; r["fold"] = "all"
            rows.append(r)
            print(f"    [{tag}] pos={100*pos:.3f}%")
            print(r[["model", "pr_auc", "pr_lift", "roc_auc",
                     "bal_acc"]].to_string(
                index=False, float_format=lambda v: f"{v:.3f}"))

            if args.model:
                mr, mi = run_model(sub, feats, yc, h, tag)
                if not mr.empty:
                    rows.append(mr); all_imp.extend(mi)
                    print("      lightgbm:")
                    print(mr[["split", "fold", "pr_auc", "pr_lift",
                              "roc_auc", "bal_acc"]].to_string(
                        index=False, float_format=lambda v: f"{v:.3f}"))

    out = pd.concat(rows, ignore_index=True)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out.to_csv(OUT_DIR / "phase3_baselines.csv", index=False)
    if all_imp:
        pd.concat(all_imp, axis=1).to_csv(OUT_DIR / "phase3_importance.csv")
    print(f"\nwrote {OUT_DIR/'phase3_baselines.csv'}  "
          f"({time.time()-t0:.0f}s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
