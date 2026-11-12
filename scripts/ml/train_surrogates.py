#!/usr/bin/env python3
"""Train and save the leave-one-terrain-out surrogates the live planner flies.

One model per terrain, each trained on the OTHER three. Flying in forest loads
the model that never saw forest, so terrain blindness is preserved in the
fly-off exactly as it was offline. Selecting the file by world name is not a
leak: it is the LOTO protocol: the model's INPUTS never include the terrain,
and its TRAINING never included this terrain's data.

ROUND 4 -- this now trains the model the offline analysis actually endorses,
which differs from the round-1-to-3 surrogate in four ways:

  1. ONSET TASK.  Training rows are restricted to cycles where C2 is currently
     UP, and the label is "will it fail within h". The old surrogate trained on
     "is C2 down at t+h" over all rows, where more than half the positives were
     rows already down, so it was optimised to recognise a state rather than
     anticipate a transition. A planner cannot act on the former.

  2. DEAD-UE CENSORING at both t and t+h.  Coverage RLF exits nr-uesoftmodem
     and the gNB counters then repeat their last value verbatim. That freeze is
     trivially learnable (`dl_bler_d1 == 0` exactly) and accounted for most of
     the old model's apparent skill. Censored here, not just in evaluation.

  3. HORIZON 20 s, not 60.  The horizon sweep put the only real skill at 20 s;
     at 60 s the corrected lift is 1.23 against 1.52. The planner therefore
     gets a third of the lead time it used to think it had, which is a real
     constraint on what it can achieve and not something to paper over.

  4. L1 LOGISTIC, not gradient boosting, and CALIBRATED.  On leave-one-terrain-
     out the capacity ladder is monotone downwards: logistic_L1 scores PR-AUC
     0.153 / ROC 0.667 / worst fold 0.538 against LightGBM's 0.111 / 0.557 /
     0.460. Isotonic calibration is fitted on an inner BY-TIME split of the
     training terrains only. Calibration matters more here than offline: the
     soft-min objective compares risks ACROSS robots, so a monotone-but-skewed
     score would weight the wrong robot.

The feature set is `radio_blind` (geom + radio + action), dropping the c2hist
group the old surrogate used. c2hist is legitimate for a forecaster, but it
scored worse than radio_blind at every horizon under the corrected protocol,
and leaving it out makes the flown model the same one the paper reports.

    python3 scripts/ml/train_surrogates.py
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import joblib
import numpy as np
from sklearn.impute import SimpleImputer
from sklearn.isotonic import IsotonicRegression
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

sys.path.insert(0, str(Path(__file__).resolve().parent))
import dataset as ds                              # noqa: E402
from forecast_cycle import HIST_COLS              # noqa: E402
from onset_forecast import dead_ue_mask, inner_time_split   # noqa: E402

OUT = ds.REPO / "logs" / "ml" / "surrogates"
GROUPS = ["geom", "radio", "action"]              # radio_blind
HORIZON = 20.0
PROTOCOL = "onset_censored_h20_logisticL1_isotonic"


def estimator():
    """The rung the capacity ladder selects. Imputation is part of the model:
    the live planner builds candidate rows with columns that can be absent, and
    a median learned on the training terrains is the honest stand-in."""
    return make_pipeline(
        SimpleImputer(strategy="median"),
        StandardScaler(),
        LogisticRegression(max_iter=4000, C=0.1, penalty="l1",
                           solver="liblinear"))


def main() -> int:
    t0 = time.time()
    OUT.mkdir(parents=True, exist_ok=True)

    raw = ds.build()
    raw["dead"] = dead_ue_mask(raw)
    df = ds.add_history(raw, HIST_COLS, lags=(1, 2, 3))

    # Targets are computed on the FULL frame, then rows are dropped -- doing it
    # the other way round would let a censored row silently become the t+h
    # neighbour of the row before it.
    d = ds.add_target(df, HORIZON, "c2_ok")
    d = ds.add_target(d, HORIZON, "dead").reset_index(drop=True)
    d["y"] = 1.0 - d[f"y_c2_ok_h{int(HORIZON)}"]
    dead_tgt = d[f"y_dead_h{int(HORIZON)}"].fillna(0) > 0.5
    n_all = len(d)
    d = d[d["y"].notna() & (d["c2_ok"] == 1) & ~d["dead"]
          & ~dead_tgt].reset_index(drop=True)

    feats = ds.feature_columns(d, GROUPS)
    ds.assert_terrain_blind(feats)
    print(f"onset rows {len(d):,} of {n_all:,}   "
          f"FAIL within {int(HORIZON)}s {100*d['y'].mean():.1f}%   "
          f"{len(feats)} features")

    for terr in sorted(d["terrain"].unique()):
        tr = (d["terrain"] != terr).values
        ytr = d.loc[tr, "y"].values
        Xtr = d.loc[tr, feats]
        meta_tr = d.loc[tr, ds.META]

        # Isotonic on an inner BY-TIME split of the training terrains, never on
        # the held-out one. A random inner split would leak across the history
        # window; the last 30 % of each run is clean.
        cal = inner_time_split(meta_tr)
        iso = None
        if cal.sum() >= 200 and len(np.unique(ytr[~cal])) > 1 \
                and len(np.unique(ytr[cal])) > 1:
            inner = estimator().fit(Xtr[~cal], ytr[~cal])
            p_in = inner.predict_proba(Xtr[cal])[:, 1]
            iso = IsotonicRegression(out_of_bounds="clip").fit(p_in, ytr[cal])
        model = estimator().fit(Xtr, ytr)

        p = OUT / f"surrogate_heldout_{terr}.joblib"
        joblib.dump({"model": model, "iso": iso, "protocol": PROTOCOL,
                     "features": feats, "horizon_s": HORIZON}, p)
        # The old LightGBM text model must not survive beside the new one: the
        # planner would load whichever its loader happened to prefer.
        old = OUT / f"surrogate_heldout_{terr}.txt"
        if old.exists():
            old.rename(old.with_suffix(".txt.superseded"))
        print(f"  {terr:<11} trained on {int(tr.sum()):,} rows "
              f"({d.loc[tr,'terrain'].nunique()} terrains), "
              f"base rate {100*ytr.mean():.1f}%, "
              f"isotonic {'yes' if iso is not None else 'NO'} -> {p.name}")

    (OUT / "features.json").write_text(json.dumps(
        {"features": feats, "groups": GROUPS, "horizon_s": HORIZON,
         "hist_cols": HIST_COLS, "protocol": PROTOCOL,
         "estimator": "logistic_L1(C=0.1)+isotonic",
         "onset_rows": int(len(d)), "trained": time.strftime("%F %T")},
        indent=1))
    print(f"\n{len(feats)} features -> {OUT/'features.json'}  "
          f"({time.time()-t0:.0f}s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
