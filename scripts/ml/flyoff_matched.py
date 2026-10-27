import pandas as pd, numpy as np
ROBOTS=["a","b","c","d"]
PAIRS={"open_field":("campaign_20261026_222941","campaign_20261025_235902","sar_open_field"),
 "urban":("campaign_20261027_000120","campaign_20261026_013033","sar_urban"),
 "forest":("campaign_20261027_013304","campaign_20261026_030216","sar_forest"),
 "hills":("campaign_20261027_030546","campaign_20261026_043517","sar_hills")}

def stack(d,w,pol):
    df=pd.read_csv(f"logs/{d}/{w}.csv",low_memory=False)
    dt=df["t_elapsed_s"].diff().shift(-1); dt.iloc[-1]=dt.median()
    out=[]
    for r in ROBOTS:
        s=pd.DataFrame(dict(dh=df[f"{r}_d_horiz_m"], ds=df[f"{r}_d_slant_m"],
                            z=df["uav_z_m"], loss=df[f"{r}_c2_loss_pct"], dt=dt.clip(lower=0)))
        out.append(s)
    s=pd.concat(out).dropna(subset=["dh","loss"])
    s["ok"]=(s.loss<50).astype(float); s["pol"]=pol
    return s

print("=== P(C2 up) at MATCHED horizontal distance  (robot-geometry controlled) ===")
print("    bins of d_horiz; planner vs excite; n>=40 each side required\n")
BINS=[(0,25),(25,50),(50,75),(75,100),(100,150),(150,250)]
for t,(p,e,w) in PAIRS.items():
    A,B=stack(p,w,"plan"),stack(e,w,"exc")
    print(f"  {t}")
    tot_a=tot_b=wt=0.0
    for lo,hi in BINS:
        a=A[(A.dh>=lo)&(A.dh<hi)]; b=B[(B.dh>=lo)&(B.dh<hi)]
        if len(a)<40 or len(b)<40:
            print(f"    d {lo:3d}-{hi:3d}m  n plan={len(a):4d} exc={len(b):4d}   -- insufficient overlap")
            continue
        pa,pb=a.ok.mean()*100,b.ok.mean()*100
        # weight by the EXCITE exposure so we reweight planner onto baseline geometry
        wgt=len(b); tot_a+=pa*wgt; tot_b+=pb*wgt; wt+=wgt
        print(f"    d {lo:3d}-{hi:3d}m  n plan={len(a):4d} exc={len(b):4d}   P(up) {pa:5.1f}% vs {pb:5.1f}%   delta {pa-pb:+6.1f}pp")
    if wt: print(f"    -> geometry-matched overall: {tot_a/wt:5.1f}% vs {tot_b/wt:5.1f}%   delta {(tot_a-tot_b)/wt:+6.1f}pp")
    print()
