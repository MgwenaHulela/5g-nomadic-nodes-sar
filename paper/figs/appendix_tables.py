"""Appendix tables A1-A5, each from a saved result file:
A1 dead-process extent      logs/ml/dead_process_extent.csv   (scripts/ml/dead_process_extent.py)
A2 horizon sweep            logs/ml/phase2b_onset.csv         (scripts/ml/onset_forecast.py)
A3 planner vs two_phase     logs/ml/planner_vs_twophase_nullctl.csv (scripts/ml/planner_vs_twophase_nullctl.py)
A4 offline altitude bench   logs/ml/altitude_bench_cautious_validate_*.csv (scripts/ml/altitude_offline_eval.py)
A5 forest patrol            logs/congestion_test_patrol_20261112/comparison.txt (compare.py there)"""
import re
import numpy as np
import pandas as pd
from style import tex_table, OUT

# A1
d = pd.read_csv("logs/ml/dead_process_extent.csv")
NAME = {"hills": "Hills", "forest": "Forest", "urban": "Urban", "open_field": "Open field"}
rows = [[NAME[r.terrain], f"{100*r.dead_share_down:.1f}\\,\\%", f"{100*r.c2_raw:.1f}\\,\\%", f"{100*r.c2_censored:.1f}\\,\\%"]
        for r in d[d.terrain != "ALL"].set_index("terrain").loc[list(NAME)].reset_index().itertuples()]
a = d[d.terrain == "ALL"].iloc[0]
tex_table(f"{OUT}/tab_a1_dead.tex", ["Terrain", "Dead share of C2-down", "C2 raw", "C2 censored"], rows, "@{}lccc@{}",
          note=f"all terrains: {int(a.dead_rows)} of {int(a.n)} rows ({100*a.dead_share_rows:.1f} %)")
print("A1", int(a.dead_rows), int(a.n), round(100 * a.dead_share_rows, 1))

# A2
h = pd.read_csv("logs/ml/phase2b_onset.csv"); h = h[h.terrain == "POOLED"]
rows = []
for hz in (10, 20, 30, 60):
    g = h[h.horizon_s == hz].set_index("model")
    lg, tr = g.loc["radio_blind|raw"], g.loc["radio_blind|trend"]
    rows.append([f"{hz}\\,s", f"{lg.pr_lift:.2f}", f"{lg.roc_auc:.3f}", f"{tr.pr_lift:.2f}", f"{tr.roc_auc:.3f}"])
tex_table(f"{OUT}/tab_a2_horizon.tex", ["Horizon", "GBM lift", "GBM ROC", "Logit-6 lift", "Logit-6 ROC"], rows,
          "@{}lcccc@{}", note="GBM = LightGBM on 65 radio_blind features (model radio_blind|raw); Logit-6 = trend logistic")
print("A2", rows)

# A3
n = pd.read_csv("logs/ml/planner_vs_twophase_nullctl.csv")
rows = []
for t in ["open_field", "forest", "urban", "hills"]:
    p = n[(n.terrain == t) & n.comparison.str.startswith("planner")].iloc[0]
    q = n[(n.terrain == t) & n.comparison.str.startswith("null")].iloc[0]
    f = lambda r: f"${r.delta_pp:+.1f}$ [{r.lo:+.1f}, {r.hi:+.1f}]{'$^*$' if r.significant else ''}"
    # usable only if the null interval excludes a swing as large as the effect (both signs)
    usable = not (q.lo <= -abs(p.delta_pp) or q.hi >= abs(p.delta_pp))
    rows.append([t.replace("_", " "), f(p), f(q), "yes" if usable else "no"])
tex_table(f"{OUT}/tab_a3_nullctl.tex", ["Terrain", "Planner $-$ two\\_phase (pp)", "Null control (pp)", "Usable"],
          rows, "@{}llll@{}")
print("A3", rows)

# A4
SETS = [("All, noise 0", "logs/ml/altitude_bench_cautious_validate_h0.csv"),
        ("40-m, noise .03", "logs/ml/altitude_bench_cautious_validate_h0.03_z40.csv"),
        ("Capped forest, .03", "logs/ml/altitude_bench_cautious_validate_h0.03_capped.csv")]
rows = []
for name, f in SETS:
    b = pd.read_csv(f)
    for lrn, lab in [("AltitudeProber", "exploring"), (None, "cautious")]:
        x = b[b.learner == lrn] if lrn else b[b.learner.str.contains("home_z")]
        worst = x.groupby("effect").vs_fixed40.mean().min()
        rows.append([name if lab == "exploring" else "", lab, f"${worst:+.1f}$",
                     f"{100*(x.vs_fixed40 < -2).mean():.1f}\\,\\%",
                     f"${x[x.effect == 'climb_strong'].vs_fixed40.mean():+.1f}$"])
tex_table(f"{OUT}/tab_a4_bench.tex", ["Traces", "Learner", "Worst mean", "Runs $<-2$", "Climb gain"], rows, "@{}llccc@{}",
          note="reward points vs fixed 40 m; fresh seeds")
print("A4", rows)

# A5
c = open("logs/congestion_test_patrol_20261112/comparison.txt").read()
cap = re.search(r"capped \(step 3\)\s+[\d.]+\s+\d+\s+([\d.]+)\s+([\d.]+)\s+(\d+-\d+)\s+([\d.]+).*?([\d.]+)\s+([\d.]+)\s*$", c, re.M)
unc = re.search(r"arm A, same first 30 min\s+[\d.]+\s+\d+\s+([\d.]+)\s+([\d.]+)\s+(\d+-\d+)\s+([\d.]+).*?([\d.]+)\s+([\d.]+)\s*$", c, re.M)
rows = [["Mean C2 up", f"{unc[1]}\\,\\%", f"{cap[1]}\\,\\%"],
        ["All 4 up (95\\,\\% CI)", f"{unc[2]}\\,\\% ({unc[3].replace('-', '--')})", f"{cap[2]}\\,\\% ({cap[3].replace('-', '--')})"],
        ["No robot up", f"{unc[4]}\\,\\%", f"{cap[4]}\\,\\%"],
        ["Video latency median / p90", f"{unc[5]} / {unc[6]}\\,s", f"{cap[5]} / {cap[6]}\\,s"]]
tex_table(f"{OUT}/tab_a5_patrol.tex", ["", "No cap", "Adaptive cap"], rows, "@{}lcc@{}",
          note="forest patrol, hover 40 m, first 30 min, same seed")
print("A5", rows)
