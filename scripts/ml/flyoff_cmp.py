import pandas as pd, numpy as np, sys

ROBOTS = ["a","b","c","d"]
PAIRS = {  # terrain: (planner_dir, excite_dir, world)
 "open_field": ("campaign_20261026_222941","campaign_20261025_235902","sar_open_field"),
 "urban":      ("campaign_20261027_000120","campaign_20261026_013033","sar_urban"),
 "forest":     ("campaign_20261027_013304","campaign_20261026_030216","sar_forest"),
 "hills":      ("campaign_20261027_030546","campaign_20261026_043517","sar_hills"),
}

def load(d, w):
    df = pd.read_csv(f"logs/{d}/{w}.csv", low_memory=False)
    for r in ROBOTS:
        df[f"{r}_c2_ok"] = (df[f"{r}_c2_loss_pct"] < 50).astype(float)
        df.loc[df[f"{r}_c2_loss_pct"].isna(), f"{r}_c2_ok"] = np.nan
    ok = df[[f"{r}_c2_ok" for r in ROBOTS]]
    df["worst"] = ok.min(axis=1)          # 1 only if ALL robots up
    df["n_up"]  = ok.sum(axis=1)
    # cycle duration: forward diff of elapsed time, last cycle gets the median
    dt = df["t_elapsed_s"].diff().shift(-1)
    dt.iloc[-1] = dt.median()
    df["dt"] = dt.clip(lower=0)
    return df

def wavg(df, col):
    m = df[col].notna() & df["dt"].notna()
    return float(np.average(df.loc[m, col], weights=df.loc[m, "dt"]))

rows=[]
for terr,(pd_dir, ex_dir, w) in PAIRS.items():
    P, E = load(pd_dir, w), load(ex_dir, w)
    rows.append(dict(terrain=terr,
        pl_avail=100*wavg(P,"worst"), ex_avail=100*wavg(E,"worst"),
        pl_nup=wavg(P,"n_up"),        ex_nup=wavg(E,"n_up"),
        pl_cyc=len(P), ex_cyc=len(E),
        pl_dt=P["dt"].median(), ex_dt=E["dt"].median(),
        pl_span=P["t_elapsed_s"].max(), ex_span=E["t_elapsed_s"].max(),
        pl_z=P["uav_z_m"].mean(), ex_z=E["uav_z_m"].mean()))
R=pd.DataFrame(rows).set_index("terrain")
R["d_avail"]=R.pl_avail-R.ex_avail
R["d_nup"]=R.pl_nup-R.ex_nup

pd.set_option("display.width",200)
print("=== worst-robot C2 availability (all 4 robots up), TIME-WEIGHTED % ===")
print(R[["pl_avail","ex_avail","d_avail"]].round(2).to_string())
print("\n=== mean robots connected (of 4), time-weighted ===")
print(R[["pl_nup","ex_nup","d_nup"]].round(3).to_string())
print("\n=== run shape (confounds) ===")
print(R[["pl_cyc","ex_cyc","pl_dt","ex_dt","pl_span","ex_span","pl_z","ex_z"]].round(1).to_string())
