#!/usr/bin/env python3
"""Train and save the leave-one-terrain-out surrogates the live planner flies.

One model per terrain, each trained on the OTHER three. Flying in forest
loads the model that never saw forest, so terrain blindness is preserved in
the fly-off exactly as it was offline. Selecting the file by world name is
not a leak: it is the LOTO protocol: the model's INPUTS never include the
terrain, and its TRAINING never included this terrain's data.

    python3 scripts/ml/train_surrogates.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import lightgbm as lgb

sys.path.insert(0, str(Path(__file__).resolve().parent))
import dataset as ds                       # noqa: E402
from forecast_cycle import HIST_COLS, PARAMS   # noqa: E402

OUT = ds.REPO / "logs" / "ml" / "surrogates"
GROUPS = ["geom", "radio", "action", "c2hist"]
HORIZON = 60.0


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    df = ds.add_history(ds.build(), HIST_COLS)
    d = ds.add_target(df, HORIZON, "c2_ok").reset_index(drop=True)
    d["y"] = 1.0 - d[f"y_c2_ok_h{int(HORIZON)}"]
    d = d[d["y"].notna()].reset_index(drop=True)
    feats = ds.feature_columns(d, GROUPS)
    ds.assert_terrain_blind(feats)

    for terr in sorted(d["terrain"].unique()):
        tr = d["terrain"] != terr
        m = lgb.LGBMClassifier(**PARAMS).fit(d.loc[tr, feats], d.loc[tr, "y"])
        p = OUT / f"surrogate_heldout_{terr}.txt"
        m.booster_.save_model(str(p))
        print(f"  {terr:<11} trained on {int(tr.sum()):,} rows "
              f"({d.loc[tr,'terrain'].nunique()} terrains) -> {p.name}")
    (OUT / "features.json").write_text(json.dumps(
        {"features": feats, "groups": GROUPS, "horizon_s": HORIZON,
         "hist_cols": HIST_COLS}, indent=1))
    print(f"\n{len(feats)} features -> {OUT/'features.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
