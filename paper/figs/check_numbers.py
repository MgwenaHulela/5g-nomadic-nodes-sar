"""Step 5 check: every number quoted in paper/CLAIMS.md and paper/EVIDENCE.md that a
figure or table carries is recomputed from its source file and compared at the
precision quoted. Prints PASS / MISMATCH. Run from repo root."""
import pandas as pd

R = []


def chk(name, quoted, actual, tol):
    R.append((("PASS" if abs(quoted - actual) <= tol else "MISMATCH"), name, quoted, round(actual, 4)))


s = pd.read_csv("paper/figs/tab_static_load.csv").set_index("test")
chk("E1 UL ping loss lo (no cap)", 64, s.loc["No cap", "ul_loss_lo"], 0.5)
chk("E1 UL ping loss hi (no cap)", 91, s.loc["No cap", "ul_loss_hi"], 0.5)
chk("E1 goodput lo", 0.13, s.loc["No cap", "gp_lo"], 0.005)
chk("E1 goodput hi", 0.49, s.loc["No cap", "gp_hi"], 0.005)
chk("E3 adaptive RTT hi (claimed 109, UL-only table)", 109, s.loc["Adaptive", "rtt_hi_ms"], 0.5)
chk("E3 adaptive video latency hi (claimed 0.42 excl. delta)", 0.42, s.loc["Adaptive", "lat_hi_s"], 0.005)
f = pd.read_csv("logs/capped_forest_flyoff_20261112_002402_summary.csv").set_index(["set", "arm"])
for arm, v in zip("ABCD", [70.5, 67.0, 53.1, 43.3]):
    chk(f"D5 capped all4 {arm}", v, f.loc[("capped", arm), "all4"], 0.05)
chk("E6 none-up uncapped max (claimed 57, includes prober D)", 57, f.xs("prober", level=0).none.max(), 0.5)
chk("E6 none-up uncapped min", 25, f.xs("xyz", level=0).none.min(), 0.5)
m = pd.read_csv("logs/ml/phase2c_model_class.csv")
m = m[(m.terrain == "POOLED") & (m.features == "radio_blind") & (m.n == 38303)].set_index("model")
chk("A6 L1 lift", 1.79, m.loc["logistic_L1", "pr_lift"], 0.005)
chk("A6 L1 ROC", 0.667, m.loc["logistic_L1", "roc_auc"], 0.0005)
chk("A6 L1 worst", 0.538, m.loc["logistic_L1", "roc_min_fold"], 0.0005)
chk("A6 MLP-large worst", 0.404, m.loc["mlp_large", "roc_min_fold"], 0.0005)
c = pd.read_csv("paper/figs/tab_contrasts.csv").set_index("pair")
chk("B3 recovered open-forest per cycle", 0.006, c.loc["open_field-forest", "rec_cycle"], 0.0005)
chk("B2 measured open-forest 60 s", 0.167, c.loc["open_field-forest", "meas_60"], 0.0005)
d = pd.read_csv("logs/ml/phase4d_interactions.csv")
r = d[(d.kind == "recovered_raw") & (d.pair == "open_field-forest") & (d.bin_s == 120)].iloc[0]
R.append(("MISMATCH" if (r.lo > 0) else "PASS", "B4 claim 'recovered open-forest n.s. at every bin' (raw, 120 s)", "n.s.",
          f"{r.diff_per_10m:+.4f} [{r.lo:+.4f},{r.hi:+.4f}]"))
a = pd.read_csv("paper/figs/fig_arms_seeds.csv")
for arm, v in zip("ABCD", [70.5, 67.0, 65.2, 72.0]):
    x = a[(a.world == "sar_forest") & (a.arm == arm) & (a.seed == 1794000001)].all4
    chk(f"D7/D5 forest seed-1 all4 {arm}", v, x.iloc[0], 0.05)
for row in R:
    print(f"{row[0]:9} {row[1]:62} quoted {row[2]!s:>8}  actual {row[3]}")
