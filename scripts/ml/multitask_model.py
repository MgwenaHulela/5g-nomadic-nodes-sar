#!/usr/bin/env python3
"""Phase 7 -- one multi-head, terrain-blind, calibrated link-risk model.

The planner's surrogate answers a single question (will this robot's C2 fail
within 20 s?). A SAR robot needs more than C2: the operator also needs its
video. This replaces the single head with ONE model, ONE shared feature
pipeline and several calibrated heads at the same 20 s horizon:

    c2     P(C2 outage)       ping loss >= 50 % at t+20
    video  P(video unusable)  frame delivery < 90 % OR latency > 500 ms at t+20
    union  P(mission unusable) c2 OR video at t+20   <- the planner's risk
    tcp    P(TCP shortfall)   ARCHITECTED ONLY: no TCP labels exist yet, so it
                              is declared in the bundle and never trained

WHY A UNION HEAD instead of combining c2 and video by formula. The two
failures share one radio link and arrive together. On all rows:

    P(c2 down) 0.285   P(video bad) 0.254   P(both) 0.236
    P(either) observed 0.302   vs   1-(1-a)(1-b) under independence 0.466

so the independence product overstates mission risk by about half, and the
error is largest where both look shaky -- exactly where the planner chooses
between candidates. The union label is fully observed, so it is learned and
calibrated directly. max(c2, video) -- the floor any combination must respect
-- and the independence product are still evaluated against the same label so
the choice is shown, not asserted.

PROTOCOL (identical to the deployed single-head surrogate, so the C2 head is
directly comparable with every number already reported):

  * features: radio_blind (geom + radio + action), assert_terrain_blind on
    every matrix
  * rows: onset only -- each head is trained where ITS service is usable now;
    dead-UE stretches censored at t and at t+h (dead_ue_mask)
  * horizon 20 s, label matched within +/-10 s (dataset.add_target default;
    stricter than the 30 s outer bound)
  * leave-one-terrain-out; calibration and thresholds on an inner BY-TIME
    split of the training terrains only
  * labels are never imputed: a missing input makes the label NaN and the
    head's mask drops the row

MODEL: a shared SimpleImputer + StandardScaler trunk, then per-head L1
logistic (C=0.1) + Platt scaling. LightGBM per head is evaluated as the
heavier comparison.

WHY PLATT, NOT ISOTONIC (decided 2026-11-09 on held-out evidence, union
head): isotonic cut open-field ROC 0.750 -> 0.698 and PR 0.318 -> 0.238, and
emitted only 28-54 distinct values per terrain, so candidate waypoints tied
and the soft-min lost resolution. Platt keeps the raw ranking exactly, stays
continuous, and was better calibrated in three of four terrains. Neither
transfers well: Brier skill stays near zero on hills and urban.

Per-head autoregressive history is run ONLY as an ablation and never reaches
the bundle.

Run it niced and single-threaded while a campaign is flying -- the
rfsimulator is sensitive to CPU starvation:

    nice -n 19 python3 scripts/ml/multitask_model.py
"""
from __future__ import annotations

import os
for _v in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS"):
    os.environ.setdefault(_v, "1")

import argparse                                              # noqa: E402
import json                                                  # noqa: E402
import re                                                    # noqa: E402
import sys                                                   # noqa: E402
import time                                                  # noqa: E402
from pathlib import Path                                     # noqa: E402

import joblib                                                # noqa: E402
import lightgbm as lgb                                       # noqa: E402
import numpy as np                                           # noqa: E402
import pandas as pd                                          # noqa: E402
from sklearn.impute import SimpleImputer                     # noqa: E402
from sklearn.linear_model import LogisticRegression          # noqa: E402
from sklearn.metrics import (average_precision_score,        # noqa: E402
                             brier_score_loss,
                             precision_recall_curve, roc_auc_score)
from sklearn.pipeline import make_pipeline                   # noqa: E402
from sklearn.preprocessing import StandardScaler             # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
import dataset as ds                                         # noqa: E402
from forecast_cycle import HIST_COLS, PARAMS                 # noqa: E402
from onset_forecast import dead_ue_mask, inner_time_split    # noqa: E402
from planner import candidate_targets, rebuild_geometry      # noqa: E402
from multitask_risk import apply_calibration, fit_platt      # noqa: E402

OUT_DIR = ds.REPO / "logs" / "ml"
BUNDLE_DIR = OUT_DIR / "surrogates_multitask"
SINGLE_DIR = OUT_DIR / "surrogates"            # read only: the flying model
GROUPS = ["geom", "radio", "action"]           # radio_blind
HORIZON = 20.0
H = int(HORIZON)
SCHEMA_VERSION = 2
PROTOCOL = "multitask_onset_censored_h20_logisticL1_platt_v2"
PLANNER_HEAD = "union"
HEADS = ["c2", "video", "union"]

LABEL_DEF = {
    "c2": "c2_loss_pct >= 50 at t+20",
    "video": "vid_frame_delivery_pct < 90 OR vid_latency_ms > 500 at t+20",
    "union": "c2 OR video at t+20",
}
TCP_HEAD = {
    "status": "architected_untrained",
    "label": "TCP goodput below its per-robot target at t+20",
    "requires": ["tcp_goodput_mbps", "tcp_target_mbps", "tcp_shortfall"],
    "note": "no TCP shortfall labels exist in the logs; training waits for "
            "a SAR_TCP_MEASURE campaign. Labels are never fabricated.",
}
# Autoregressive stems per head -- ABLATION ONLY. The C2 head never sees
# video QoS, as current video quality is too close to the label.
AR_STEMS = {
    "c2": ["c2_ok", "c2_loss_pct"],
    "video": ["vid_frame_delivery_pct", "vid_latency_ms"],
    "union": ["c2_ok", "c2_loss_pct", "vid_frame_delivery_pct",
              "vid_latency_ms"],
}
_HIST_SFX = re.compile(r"_(lag\d+|d1|roll3_mean|roll3_std)$")


# --------------------------------------------------------------------------
# Labels and masks
# --------------------------------------------------------------------------
def label_bad(d: pd.DataFrame, head: str) -> np.ndarray:
    """1 = service unusable, 0 = usable, NaN = an input is missing."""
    c2 = d["c2_loss_pct"] >= 50
    vid = (d["vid_frame_delivery_pct"] < 90) | (d["vid_latency_ms"] > 500)
    src = {"c2": ["c2_loss_pct"],
           "video": ["vid_frame_delivery_pct", "vid_latency_ms"],
           "union": ["c2_loss_pct", "vid_frame_delivery_pct",
                     "vid_latency_ms"]}[head]
    v = {"c2": c2, "video": vid, "union": c2 | vid}[head].astype(float).values
    v[d[src].isna().any(axis=1).values] = np.nan
    return v


def build_frame():
    raw = ds.build().sort_values(["run_id", "robot", "t_elapsed_s"],
                                 kind="stable").reset_index(drop=True)
    raw["dead"] = dead_ue_mask(raw)
    for h in HEADS:
        raw[f"bad_{h}"] = label_bad(raw, h)
    df = ds.add_history(raw, HIST_COLS, lags=(1, 2, 3))
    for col in [f"bad_{h}" for h in HEADS] + ["dead"]:
        df = ds.add_target(df, HORIZON, col)
    df = df.reset_index(drop=True)

    dead_tgt = df[f"y_dead_h{H}"].fillna(0).values > 0.5
    ok = ~df["dead"].values & ~dead_tgt
    Y, M = {}, {}
    for h in HEADS:
        y = df[f"y_bad_{h}_h{H}"].values
        Y[h] = np.nan_to_num(y, nan=-1.0)          # -1 never used: masked
        M[h] = ok & (df[f"bad_{h}"].values == 0) & ~np.isnan(y)
    feats = ds.feature_columns(df, GROUPS)
    ds.assert_terrain_blind(feats)
    return df, feats, Y, M


def stem_columns(df: pd.DataFrame, stems: list[str]) -> list[str]:
    """History-expanded columns for given stems (feature_columns' rule)."""
    cols = [c for c in df.columns if _HIST_SFX.sub("", c) in stems]
    ds.assert_terrain_blind(cols)
    return cols


# --------------------------------------------------------------------------
# Models
# --------------------------------------------------------------------------
class MultiHeadLinear:
    """Shared imputer+scaler trunk, per-head L1 logistic."""

    def __init__(self, heads, C: float = 0.1):
        self.head_names, self.C = list(heads), C

    def fit(self, X, Y, M):
        any_m = np.logical_or.reduce([M[h] for h in self.head_names])
        self.trunk = make_pipeline(SimpleImputer(strategy="median"),
                                   StandardScaler()).fit(X[any_m])
        Z = self.trunk.transform(X)
        self.heads = {}
        for h in self.head_names:
            self.heads[h] = LogisticRegression(
                max_iter=4000, C=self.C, penalty="l1",
                solver="liblinear").fit(Z[M[h]], Y[h][M[h]])
        return self

    def predict_raw(self, X):
        Z = self.trunk.transform(X)
        return {h: m.predict_proba(Z)[:, 1] for h, m in self.heads.items()}


class MultiHeadLGBM:
    """Heavier comparison: one LightGBM per head, native NaN handling."""

    def __init__(self, heads):
        self.head_names = list(heads)

    def fit(self, X, Y, M):
        p = dict(PARAMS, n_jobs=1)
        self.heads = {h: lgb.LGBMClassifier(**p).fit(X[M[h]], Y[h][M[h]])
                      for h in self.head_names}
        return self

    def predict_raw(self, X):
        return {h: m.predict_proba(X)[:, 1] for h, m in self.heads.items()}


def f1_threshold(y, p) -> float:
    pr, rc, th = precision_recall_curve(y, p)
    f1 = 2 * pr[:-1] * rc[:-1] / np.clip(pr[:-1] + rc[:-1], 1e-12, None)
    return float(th[int(np.nanargmax(f1))]) if len(th) else 0.5


def fit_calibrated(make, X, Y, M, meta):
    """Inner by-time split of the TRAINING terrains: Platt + threshold on
    the last 30 % of each run, then the full model on everything."""
    cal = inner_time_split(meta)
    inner = make().fit(X[~cal], {h: Y[h][~cal] for h in Y},
                       {h: M[h][~cal] for h in M})
    pin = inner.predict_raw(X[cal])
    cal_, thr = {}, {}
    for h in M:
        m = M[h][cal]
        y = Y[h][cal][m]
        if m.sum() >= 200 and len(np.unique(y)) == 2:
            cal_[h] = fit_platt(pin[h][m], y)
            thr[h] = f1_threshold(y, apply_calibration(cal_[h], pin[h][m]))
        else:
            cal_[h], thr[h] = None, 0.5
    return make().fit(X, Y, M), cal_, thr


def calibrated(model, cal, X):
    return {h: apply_calibration(cal.get(h), p)
            for h, p in model.predict_raw(X).items()}


# --------------------------------------------------------------------------
# Metrics
# --------------------------------------------------------------------------
def cal_slope_intercept(y, p):
    lg = np.log(np.clip(p, 1e-4, 1 - 1e-4) / np.clip(1 - p, 1e-4, None))
    m = LogisticRegression(C=1e6, max_iter=1000).fit(lg.reshape(-1, 1), y)
    return float(m.coef_[0, 0]), float(m.intercept_[0])


def metrics(y, p) -> dict:
    y = np.asarray(y, float)
    out = dict(n=int(len(y)), pos_rate=float(y.mean()) if len(y) else np.nan)
    if len(y) < 30 or len(np.unique(y)) < 2:
        return out
    pr = average_precision_score(y, p)
    slope, icpt = cal_slope_intercept(y, p)
    out.update(pr_auc=pr, pr_lift=pr / y.mean(), roc_auc=roc_auc_score(y, p),
               brier=brier_score_loss(y, p), cal_slope=slope,
               cal_intercept=icpt, mean_pred=float(np.mean(p)))
    return out


# --------------------------------------------------------------------------
# Support check -- is the planner asking the model about places it never saw?
# --------------------------------------------------------------------------
def support_box(X: pd.DataFrame, lo_q=0.005, hi_q=0.995):
    return (X.quantile(lo_q).to_dict(), X.quantile(hi_q).to_dict())


def out_of_support(Xc: pd.DataFrame, lo: dict, hi: dict) -> pd.DataFrame:
    L = pd.Series(lo)[Xc.columns].values
    U = pd.Series(hi)[Xc.columns].values
    v = Xc.values
    return pd.DataFrame(np.isfinite(v) & ((v < L) | (v > U)),
                        columns=Xc.columns, index=Xc.index)


def candidate_support(df, feats, te, lo, hi, n_cycles, rng):
    """Replay held-out cycles through the planner's own candidate builder."""
    offs = candidate_targets()
    sub = df[te].dropna(subset=["uav_x_m", "uav_y_m", "uav_z_m",
                                "robot_x_m", "robot_y_m"])
    keys = sub.groupby(["run_id", "t_elapsed_s"]).size()
    keys = keys[keys >= 2].index.to_list()
    if not keys:
        return None
    pick = rng.choice(len(keys), size=min(n_cycles, len(keys)), replace=False)
    g = sub.groupby(["run_id", "t_elapsed_s"])
    rows = []
    for k in pick:
        block = g.get_group(keys[k])
        Xc = pd.concat([rebuild_geometry(block, *o) for o in offs],
                       ignore_index=True)
        for c in feats:
            if c not in Xc:
                Xc[c] = np.nan
        oos = out_of_support(Xc[feats], lo, hi)
        per_row = oos.any(axis=1).values.reshape(len(offs), len(block))
        for i, o in enumerate(offs):
            rows.append(dict(z_target=o[2], lateral=float(np.hypot(o[0], o[1])),
                             any_robot_oos=bool(per_row[i].any()),
                             frac_robots_oos=float(per_row[i].mean())))
        rows.append(dict(_feature_hits=oos.sum().to_dict()))
    hits = pd.DataFrame([r["_feature_hits"] for r in rows
                         if "_feature_hits" in r]).sum()
    cand = pd.DataFrame([r for r in rows if "_feature_hits" not in r])
    return cand, hits


# --------------------------------------------------------------------------
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--support-cycles", type=int, default=200)
    ap.add_argument("--skip-lgbm", action="store_true")
    ap.add_argument("--out", default=str(OUT_DIR))
    args = ap.parse_args()
    t0 = time.time()
    out = Path(args.out)
    rng = np.random.default_rng(0)

    df, feats, Y, M = build_frame()
    meta = df[ds.META]
    X = df[feats]
    print(f"rows {len(df):,}   {len(feats)} radio_blind features")
    for h in HEADS:
        print(f"  head {h:<6} onset rows {int(M[h].sum()):,}   "
              f"FAIL within {H}s {100*Y[h][M[h]].mean():.1f}%")

    # Consistency guard: the C2 head population must be the population the
    # deployed single-head surrogate was trained on.
    fj = json.loads((SINGLE_DIR / "features.json").read_text())
    if int(M["c2"].sum()) != int(fj.get("onset_rows", -1)):
        print(f"  WARNING c2 onset rows {int(M['c2'].sum()):,} != deployed "
              f"surrogate's {fj.get('onset_rows')} -- not like-for-like")
    if fj["features"] != feats:
        print("  WARNING feature list differs from the deployed surrogate")

    # ---- dependence on the planner-relevant population --------------------
    um = M["union"]
    c2y, vy = Y["c2"][um], Y["video"][um]
    dep = dict(population="union onset rows (both usable now)",
               n=int(um.sum()), p_c2=c2y.mean(), p_video=vy.mean(),
               p_both=float(((c2y == 1) & (vy == 1)).mean()),
               p_either=float(((c2y == 1) | (vy == 1)).mean()),
               p_either_indep=float(1 - (1 - c2y.mean()) * (1 - vy.mean())),
               p_either_max=float(max(c2y.mean(), vy.mean())))
    print("\ndependence on onset rows: " + "  ".join(
        f"{k} {v:.3f}" for k, v in dep.items() if isinstance(v, float)))
    pd.DataFrame([dep]).to_csv(out / "phase7_dependence.csv", index=False)

    MODELS = {"linear": lambda: MultiHeadLinear(HEADS)}
    if not args.skip_lgbm:
        MODELS["lightgbm"] = lambda: MultiHeadLGBM(HEADS)

    P = {k: {h: np.full(len(df), np.nan) for h in HEADS} for k in MODELS}
    P["ablation_ar"] = {h: np.full(len(df), np.nan) for h in HEADS}
    p_single = np.full(len(df), np.nan)
    thr_fold = {}
    sup_rows, sup_hits = [], []

    for terr, tr, te in ds.leave_one_terrain_out(meta):
        Ytr = {h: Y[h][tr] for h in HEADS}
        Mtr = {h: M[h][tr] for h in HEADS}
        for name, make in MODELS.items():
            mdl, cal, thr = fit_calibrated(make, X[tr], Ytr, Mtr, meta[tr])
            pc = calibrated(mdl, cal, X[te])
            for h in HEADS:
                P[name][h][te] = pc[h]
            if name == "linear":
                thr_fold[terr] = thr
            print(f"  fold {terr:<11} {name:<9} done [{time.time()-t0:.0f}s]")

        # ablation: each head alone, plus its own autoregressive history
        for h in HEADS:
            cols = feats + [c for c in stem_columns(df, AR_STEMS[h])
                            if c not in feats]
            mdl, cal, _ = fit_calibrated(
                lambda h=h: MultiHeadLinear([h]), df.loc[tr, cols],
                {h: Ytr[h]}, {h: Mtr[h]}, meta[tr])
            P["ablation_ar"][h][te] = calibrated(mdl, cal, df.loc[te, cols])[h]

        # the deployed single-head surrogate, on identical rows (read only)
        b = joblib.load(SINGLE_DIR / f"surrogate_heldout_{terr}.joblib")
        ps = b["model"].predict_proba(df.loc[te, b["features"]])[:, 1]
        p_single[te] = b["iso"].predict(ps) if b.get("iso") is not None else ps

        # candidate support, against this fold's training box
        lo, hi = support_box(X[tr])
        sup = candidate_support(df, feats, te, lo, hi, args.support_cycles,
                                rng)
        if sup is not None:
            cand, hits = sup
            sup_rows.append(cand.assign(terrain=terr))
            sup_hits.append(hits.rename(terr))

    # ---- per-head metrics --------------------------------------------------
    rows = []
    terrains = sorted(meta["terrain"].unique())
    for name, ph in P.items():
        for h in HEADS:
            per = []
            for terr in terrains:
                m = M[h] & (meta["terrain"].values == terr)
                r = metrics(Y[h][m], ph[h][m])
                per.append(r)
                rows.append(dict(model=name, head=h, fold=terr,
                                 threshold=thr_fold.get(terr, {}).get(h)
                                 if name == "linear" else None, **r))
            m = M[h]
            r = metrics(Y[h][m], ph[h][m])
            rocs = [x["roc_auc"] for x in per if "roc_auc" in x]
            rows.append(dict(model=name, head=h, fold="POOLED",
                             worst_fold_roc=min(rocs) if rocs else np.nan,
                             **r))

    # combiners, judged against the observed union label on union rows
    um = M["union"]
    lin = P["linear"]
    comb = {"union_head": lin["union"],
            "max_c2_video": np.fmax(lin["c2"], lin["video"]),
            "indep_product": 1 - (1 - lin["c2"]) * (1 - lin["video"])}
    for cname, pc in comb.items():
        per = []
        for terr in terrains:
            m = um & (meta["terrain"].values == terr)
            r = metrics(Y["union"][m], pc[m])
            per.append(r)
            rows.append(dict(model="combiner", head=cname, fold=terr, **r))
        r = metrics(Y["union"][um], pc[um])
        rocs = [x["roc_auc"] for x in per if "roc_auc" in x]
        rows.append(dict(model="combiner", head=cname, fold="POOLED",
                         worst_fold_roc=min(rocs) if rocs else np.nan, **r))
    res = pd.DataFrame(rows)
    res.to_csv(out / "phase7_multitask.csv", index=False)

    # ---- vs the deployed single-head surrogate -----------------------------
    vs = []
    for terr in terrains + ["POOLED"]:
        m = M["c2"] & ((meta["terrain"].values == terr) if terr != "POOLED"
                       else True)
        for name, p in (("multitask_c2_head", P["linear"]["c2"]),
                        ("deployed_single_c2", p_single)):
            vs.append(dict(fold=terr, model=name, **metrics(Y["c2"][m], p[m])))
    pd.DataFrame(vs).to_csv(out / "phase7_vs_single.csv", index=False)

    # ---- all four robots usable at t+20 ------------------------------------
    af = []
    d4 = pd.DataFrame({"run_id": df["run_id"], "t": df["t_elapsed_s"],
                       "terrain": meta["terrain"], "robot": df["robot"],
                       "m": M["union"], "y": Y["union"],
                       "p": P["linear"]["union"]})
    nrob = d4.groupby(["run_id", "t"])["robot"].transform("nunique")
    cyc = d4[nrob == 4].groupby(["run_id", "t"]).agg(
        terrain=("terrain", "first"), n_ok=("m", "sum"),
        all_ok_after=("y", lambda s: float((s == 0).all())),
        pred_all_ok=("p", lambda s: float(np.prod(1 - s))))
    for terr in terrains + ["POOLED"]:
        c = cyc if terr == "POOLED" else cyc[cyc["terrain"] == terr]
        usable_now = c[c["n_ok"] == 4]
        af.append(dict(
            fold=terr, cycles_4_robots=len(c),
            frac_all4_usable_now=float((c["n_ok"] == 4).mean()) if len(c)
            else np.nan,
            n_all4_usable_now=len(usable_now),
            observed_all4_still_usable_t20=usable_now["all_ok_after"].mean(),
            predicted_all4_still_usable_t20=usable_now["pred_all_ok"].mean(),
            brier=brier_score_loss(usable_now["all_ok_after"],
                                   usable_now["pred_all_ok"])
            if len(usable_now) > 30 and usable_now["all_ok_after"].nunique() > 1
            else np.nan))
    pd.DataFrame(af).to_csv(out / "phase7_allfour.csv", index=False)

    # ---- support -----------------------------------------------------------
    if sup_rows:
        s = pd.concat(sup_rows)
        aggs = dict(candidates=("any_robot_oos", "size"),
                    frac_any_robot_oos=("any_robot_oos", "mean"),
                    frac_robots_oos=("frac_robots_oos", "mean"))
        summ = s.groupby(["terrain", "z_target"]).agg(**aggs).reset_index()
        tot = (s.groupby("terrain").agg(**aggs).reset_index()
               .assign(z_target="ALL"))
        pd.concat([tot, summ]).to_csv(out / "phase7_support.csv", index=False)
        hits = pd.concat(sup_hits, axis=1).fillna(0)
        hits = hits[hits.sum(axis=1) > 0].sort_values(
            by=list(hits.columns), ascending=False)
        hits.to_csv(out / "phase7_support_features.csv")

    # ---- final bundles: one per held-out terrain ---------------------------
    BUNDLE_DIR.mkdir(parents=True, exist_ok=True)
    for terr, tr, te in ds.leave_one_terrain_out(meta):
        Ytr = {h: Y[h][tr] for h in HEADS}
        Mtr = {h: M[h][tr] for h in HEADS}
        mdl, cal, thr = fit_calibrated(lambda: MultiHeadLinear(HEADS),
                                       X[tr], Ytr, Mtr, meta[tr])
        lo, hi = support_box(X[tr])
        joblib.dump({
            "schema_version": SCHEMA_VERSION, "protocol": PROTOCOL,
            "horizon_s": HORIZON, "features": feats, "groups": GROUPS,
            "hist_cols": HIST_COLS, "held_out": terr,
            "trunk": mdl.trunk,
            "heads": {h: {"model": mdl.heads[h], "calibrator": cal[h],
                          "threshold": thr[h], "label": LABEL_DEF[h],
                          "onset_rows": int(Mtr[h].sum())} for h in HEADS},
            "tcp_head": TCP_HEAD,
            "planner_head": PLANNER_HEAD,
            "support": {"lo": lo, "hi": hi, "q": [0.005, 0.995]},
            "trained": time.strftime("%F %T"),
        }, BUNDLE_DIR / f"multitask_heldout_{terr}.joblib")
        print(f"  bundle {terr:<11} thresholds " + "  ".join(
            f"{h} {thr[h]:.3f}" for h in HEADS))
    (BUNDLE_DIR / "features.json").write_text(json.dumps({
        "schema_version": SCHEMA_VERSION, "protocol": PROTOCOL,
        "features": feats, "groups": GROUPS, "horizon_s": HORIZON,
        "hist_cols": HIST_COLS, "heads": HEADS, "planner_head": PLANNER_HEAD,
        "label_defs": LABEL_DEF, "tcp_head": TCP_HEAD,
        "estimator": "shared imputer+scaler; per-head logistic_L1(C=0.1)"
                     "+platt",
        "onset_rows": {h: int(M[h].sum()) for h in HEADS},
        "trained": time.strftime("%F %T")}, indent=1))

    # ---- console summary ---------------------------------------------------
    pool = res[res["fold"] == "POOLED"]
    cols = ["model", "head", "n", "pos_rate", "pr_auc", "pr_lift", "roc_auc",
            "worst_fold_roc", "brier", "cal_slope", "cal_intercept"]
    print("\n" + pool[cols].to_string(index=False, float_format="%.3f"))
    print("\nvs deployed single-head C2:")
    print(pd.DataFrame(vs)[pd.DataFrame(vs)["fold"] == "POOLED"][
        ["model", "n", "pr_auc", "roc_auc", "brier"]].to_string(
        index=False, float_format="%.4f"))
    print("\nall four robots:")
    print(pd.DataFrame(af).to_string(index=False, float_format="%.3f"))
    if sup_rows:
        print("\ncandidate support (any robot out of the training box):")
        print(tot.to_string(index=False, float_format="%.3f"))
    print(f"\nwrote phase7_*.csv and {BUNDLE_DIR}  ({time.time()-t0:.0f}s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
