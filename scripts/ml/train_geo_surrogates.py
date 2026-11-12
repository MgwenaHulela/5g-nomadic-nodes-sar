#!/usr/bin/env python3
"""Train the leave-one-terrain-out `linear_geo` surrogates (SAR_PLANNER_MODEL=geo).

Same protocol as train_surrogates.py -- onset rows only, dead-UE censored at t
and t+h, h = 20 s, radio_blind features, terrain never an input -- with two
differences: the estimator is geo_features.make_linear_geo (non-linear
geometry block), and calibration is Platt, fitted on the inner by-time split
of the training terrains (isotonic gave only tens of distinct values and tied
candidates; see docs/MULTITASK_REPORT.md).

Writes to logs/ml/surrogates_geo/, never to the live logs/ml/surrogates/, so
the default planner is unchanged.

    nice -n 10 python3 -u scripts/ml/train_geo_surrogates.py
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import joblib

sys.path.insert(0, str(Path(__file__).resolve().parent))
import dataset as ds                                        # noqa: E402
from forecast_cycle import HIST_COLS                        # noqa: E402
from geo_features import make_linear_geo                    # noqa: E402
from multitask_risk import fit_platt                        # noqa: E402
from onset_forecast import dead_ue_mask, inner_time_split   # noqa: E402

OUT = ds.REPO / "logs" / "ml" / "surrogates_geo"
GROUPS = ["geom", "radio", "action"]
HORIZON = 20.0
PROTOCOL = "onset_censored_h20_linear_geo_platt"


def main() -> int:
    t0 = time.time()
    OUT.mkdir(parents=True, exist_ok=True)
    raw = ds.build()
    raw["dead"] = dead_ue_mask(raw)
    df = ds.add_history(raw, HIST_COLS, lags=(1, 2, 3))
    d = ds.add_target(df, HORIZON, "c2_ok")
    d = ds.add_target(d, HORIZON, "dead").reset_index(drop=True)
    d["y"] = 1.0 - d[f"y_c2_ok_h{int(HORIZON)}"]
    dead_tgt = d[f"y_dead_h{int(HORIZON)}"].fillna(0) > 0.5
    d = d[d["y"].notna() & (d["c2_ok"] == 1) & ~d["dead"]
          & ~dead_tgt].reset_index(drop=True)
    feats = ds.feature_columns(d, GROUPS)
    ds.assert_terrain_blind(feats)
    print(f"onset rows {len(d):,}  fail {100*d['y'].mean():.1f}%  "
          f"{len(feats)} features", flush=True)

    for terr in sorted(d["terrain"].unique()):
        tr = (d["terrain"] != terr).values
        Xtr, ytr = d.loc[tr, feats], d.loc[tr, "y"].values
        cal = inner_time_split(d.loc[tr, ds.META])
        inner = make_linear_geo(feats).fit(Xtr[~cal], ytr[~cal])
        platt = fit_platt(inner.predict_proba(Xtr[cal])[:, 1], ytr[cal])
        model = make_linear_geo(feats).fit(Xtr, ytr)
        p = OUT / f"surrogate_heldout_{terr}.joblib"
        joblib.dump({"model": model, "platt": platt, "protocol": PROTOCOL,
                     "features": feats, "horizon_s": HORIZON,
                     "hist_cols": HIST_COLS}, p)
        print(f"  {terr:<11} trained on {int(tr.sum()):,} rows -> {p.name}",
              flush=True)

    (OUT / "features.json").write_text(json.dumps(
        {"features": feats, "groups": GROUPS, "horizon_s": HORIZON,
         "hist_cols": HIST_COLS, "protocol": PROTOCOL,
         "estimator": "linear_geo (L1 logistic + geometry splines) + Platt",
         "onset_rows": int(len(d)), "trained": time.strftime("%F %T")},
        indent=1))
    print(f"done ({time.time()-t0:.0f}s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
