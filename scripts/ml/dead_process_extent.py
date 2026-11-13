"""Extent of dead-UE-process contamination (Section V of the paper): share of
all rows, and share of C2-down labels per terrain flagged by dead_ue_mask
(frozen gNB counters for >= FREEZE_MIN cycles while C2 is down), with raw vs
censored C2 availability.

    python3 scripts/ml/dead_process_extent.py
writes logs/ml/dead_process_extent.csv
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import pandas as pd
import dataset as ds
from onset_forecast import dead_ue_mask

raw = ds.build().sort_values(["run_id", "robot", "t_elapsed_s"], kind="stable").reset_index(drop=True)
raw["dead"] = dead_ue_mask(raw)
rows = [dict(terrain="ALL", n=len(raw), dead_rows=int(raw.dead.sum()),
             dead_share_rows=raw.dead.mean(), dead_share_down=float("nan"),
             c2_raw=raw.c2_ok.mean(), c2_censored=raw.loc[~raw.dead, "c2_ok"].mean())]
for t, g in raw.groupby("terrain"):
    dn = g.c2_ok == 0
    rows.append(dict(terrain=t, n=len(g), dead_rows=int(g.dead.sum()), dead_share_rows=g.dead.mean(),
                     dead_share_down=(g.dead & dn).sum() / max(dn.sum(), 1),
                     c2_raw=g.c2_ok.mean(), c2_censored=g.loc[~g.dead, "c2_ok"].mean()))
o = pd.DataFrame(rows)
print(o.round(4).to_string(index=False))
o.to_csv("logs/ml/dead_process_extent.csv", index=False)
