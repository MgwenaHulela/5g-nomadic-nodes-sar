import pandas as pd, numpy as np
ROBOTS=["a","b","c","d"]
PAIRS={"open_field":("campaign_20261026_222941","campaign_20261025_235902","sar_open_field"),
 "urban":("campaign_20261027_000120","campaign_20261026_013033","sar_urban"),
 "forest":("campaign_20261027_013304","campaign_20261026_030216","sar_forest"),
 "hills":("campaign_20261027_030546","campaign_20261026_043517","sar_hills")}
L=lambda d,w: pd.read_csv(f"logs/{d}/{w}.csv",low_memory=False)

print("=== robot spatial footprint: same patrol region, or different? ===")
print(f"{'terrain':11} {'robot':6} {'planner x[p5,p50,p95]':26} {'excite x[p5,p50,p95]':26}  range_m plan/exc")
for t,(p,e,w) in PAIRS.items():
    P,E=L(p,w),L(e,w)
    for r in ROBOTS[:2]:
        a,b=P[f"{r}x_m"].dropna(),E[f"{r}x_m"].dropna()
        qa=np.percentile(a,[5,50,95]); qb=np.percentile(b,[5,50,95])
        ra=np.hypot(P[f"{r}x_m"],P[f"{r}y_m"]).median(); rb=np.hypot(E[f"{r}x_m"],E[f"{r}y_m"]).median()
        print(f"{t:11} {r:6} [{qa[0]:7.1f},{qa[1]:7.1f},{qa[2]:7.1f}] [{qb[0]:7.1f},{qb[1]:7.1f},{qb[2]:7.1f}]  {ra:6.1f}/{rb:6.1f}")

print("\n=== drift test: ploss matched on BOTH altitude and horizontal distance ===")
print("    (same z band AND same d_horiz band -> identical geometry; any gap is channel drift)")
for t,(p,e,w) in PAIRS.items():
    P,E=L(p,w),L(e,w)
    def stack(df):
        d=pd.concat([df[["uav_z_m",f"{r}_d_horiz_m",f"{r}_ploss_dB"]].rename(
            columns={f"{r}_d_horiz_m":"dh",f"{r}_ploss_dB":"pl"}) for r in ROBOTS]).dropna()
        return d
    A,B=stack(P),stack(E)
    hits=[]
    for zlo,zhi in [(25,35),(35,45),(45,55)]:
        for dlo,dhi in [(40,80),(80,120)]:
            a=A[(A.uav_z_m>=zlo)&(A.uav_z_m<zhi)&(A.dh>=dlo)&(A.dh<dhi)]
            b=B[(B.uav_z_m>=zlo)&(B.uav_z_m<zhi)&(B.dh>=dlo)&(B.dh<dhi)]
            if len(a)>=30 and len(b)>=30:
                hits.append(f"z{zlo}-{zhi} d{dlo}-{dhi}: {a.pl.median():5.2f} vs {b.pl.median():5.2f} (n {len(a)},{len(b)})")
    print(f"  {t:11} " + ("  |  ".join(hits) if hits else "NO overlapping geometry cells (>=30 samples) — policies explored disjoint space"))
