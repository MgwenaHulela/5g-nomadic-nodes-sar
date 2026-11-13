"""Round-3 risk planner vs two_phase (the SATNAC baseline), with a same-policy
null control: the identical geometry-matched comparison applied to the two
two_phase campaigns (same policy, different nights). If the method measured
policy, the null would be ~0. Helpers: flyoff_round2.py (bin on d_horiz,
reweight onto the comparator's exposure, block bootstrap).

    python3 scripts/ml/planner_vs_twophase_nullctl.py
writes logs/ml/planner_vs_twophase_nullctl.csv
"""
import sys
sys.path.insert(0, "scripts/ml")
import pandas as pd
from flyoff_round2 import stack, matched, boot

R3 = {"open_field": "campaign_20261027_182943", "urban": "campaign_20261027_200123",
      "forest": "campaign_20261027_213259", "hills": "campaign_20261027_230553"}
TP = ["campaign_20261023_180030", "campaign_20261024_205615"]


def stack_many(dirs, terr):
    out = []
    for i, d in enumerate(dirs):
        s = stack(d, terr)
        s["blk"] = s["blk"] + i * 100000
        out.append(s)
    return pd.concat(out, ignore_index=True)


rows = []
for t in R3:
    A, B = stack(R3[t], t), stack_many(TP, t)
    lo, hi = boot(A, B)
    rows.append(dict(terrain=t, comparison="planner_r3 - two_phase", delta_pp=matched(A, B),
                     lo=lo, hi=hi, n_a=len(A), n_b=len(B)))
    N1, N2 = stack(TP[0], t), stack(TP[1], t)
    lo, hi = boot(N1, N2)
    rows.append(dict(terrain=t, comparison="null: two_phase night1 - night2", delta_pp=matched(N1, N2),
                     lo=lo, hi=hi, n_a=len(N1), n_b=len(N2)))
o = pd.DataFrame(rows)
o["significant"] = (o.lo > 0) | (o.hi < 0)
print(o.round(1).to_string(index=False))
o.to_csv("logs/ml/planner_vs_twophase_nullctl.csv", index=False)
