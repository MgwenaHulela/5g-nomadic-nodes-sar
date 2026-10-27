import pandas as pd, numpy as np
ROBOTS=["a","b","c","d"]
PAIRS={"open_field":("campaign_20261026_222941","campaign_20261025_235902","sar_open_field"),
 "urban":("campaign_20261027_000120","campaign_20261026_013033","sar_urban"),
 "forest":("campaign_20261027_013304","campaign_20261026_030216","sar_forest"),
 "hills":("campaign_20261027_030546","campaign_20261026_043517","sar_hills")}
L=lambda d,w: pd.read_csv(f"logs/{d}/{w}.csv",low_memory=False)

print("=== UAV altitude over time: is the planner saturating the 60 m ceiling? ===")
print(f"{'terrain':11} {'  planner z by elapsed-quintile':38}   {'excite z':20}  %time>=58m")
for t,(p,e,w) in PAIRS.items():
    P,E=L(p,w),L(e,w)
    q=lambda df: " ".join(f"{v:5.1f}" for v in df.groupby(pd.qcut(df.t_elapsed_s,5,labels=False)).uav_z_m.mean())
    sat=100*(P.uav_z_m>=58).mean()
    print(f"{t:11} {q(P):38}   {q(E):20}  {sat:5.1f}%")

print("\n=== seed reproducibility: did the SAME robot trajectories replay? ===")
for t,(p,e,w) in PAIRS.items():
    P,E=L(p,w),L(e,w)
    n=min(len(P),len(E))
    # compare robot positions on the shared elapsed-time grid
    cors=[]
    for r in ROBOTS:
        a=np.interp(np.linspace(0,5400,500),P.t_elapsed_s,P[f"{r}x_m"])
        b=np.interp(np.linspace(0,5400,500),E.t_elapsed_s,E[f"{r}x_m"])
        cors.append(np.corrcoef(a,b)[0,1])
    print(f"  {t:11} robot-x corr vs matched-seed excite: {' '.join(f'{c:+.3f}' for c in cors)}")

print("\n=== channel drift check: ploss vs slant range, planner vs excite ===")
for t,(p,e,w) in PAIRS.items():
    P,E=L(p,w),L(e,w)
    out=[]
    for df,tag in ((P,"plan"),(E,"exc")):
        d=pd.concat([df[[f"{r}_d_slant_m",f"{r}_ploss_dB"]].rename(columns={f"{r}_d_slant_m":"d",f"{r}_ploss_dB":"pl"}) for r in ROBOTS])
        d=d.dropna(); d=d[(d.d>80)&(d.d<120)]
        out.append(f"{tag} n={len(d):5d} median_ploss={d.pl.median():6.2f}")
    print(f"  {t:11} @slant 80-120m:  {out[0]}   {out[1]}")
