import pandas as pd, numpy as np
from scipy import stats
rng=np.random.default_rng(7)
ROBOTS=["a","b","c","d"]
PAIRS={"open_field":("campaign_20261026_222941","campaign_20261025_235902","sar_open_field"),
 "urban":("campaign_20261027_000120","campaign_20261026_013033","sar_urban"),
 "forest":("campaign_20261027_013304","campaign_20261026_030216","sar_forest"),
 "hills":("campaign_20261027_030546","campaign_20261026_043517","sar_hills")}
BINS=[(0,25),(25,50),(50,75),(75,100),(100,150)]
BLOCK=20   # cycles; C2 is strongly serially correlated

def stack(d,w):
    df=pd.read_csv(f"logs/{d}/{w}.csv",low_memory=False)
    out=[]
    for r in ROBOTS:
        out.append(pd.DataFrame(dict(cyc=np.arange(len(df)), dh=df[f"{r}_d_horiz_m"],
                                     ok=(df[f"{r}_c2_loss_pct"]<50).astype(float),
                                     loss=df[f"{r}_c2_loss_pct"])))
    s=pd.concat(out).dropna(subset=["dh","loss"])
    s["blk"]=s.cyc//BLOCK
    return s

def matched(A,B,ia=None,ib=None):
    a=A if ia is None else ia; b=B if ib is None else ib
    num=den=0.0
    for lo,hi in BINS:
        x=a[(a.dh>=lo)&(a.dh<hi)]; y=b[(b.dh>=lo)&(b.dh<hi)]
        if len(x)<40 or len(y)<40: continue
        w=len(y); num+=(x.ok.mean()-y.ok.mean())*100*w; den+=w
    return num/den if den else np.nan

def boot(A,B,n=600):
    ba,bb=[g for _,g in A.groupby("blk")],[g for _,g in B.groupby("blk")]
    out=[]
    for _ in range(n):
        ia=pd.concat([ba[i] for i in rng.integers(0,len(ba),len(ba))])
        ib=pd.concat([bb[i] for i in rng.integers(0,len(bb),len(bb))])
        v=matched(None,None,ia,ib)
        if np.isfinite(v): out.append(v)
    return np.percentile(out,[2.5,97.5]) if out else (np.nan,np.nan)

PH4={"open_field":0.1641,"urban":0.0370,"hills":0.0085,"forest":-0.0120}  # Phase 4 measured 10m-climb effect on C2
res={}
print("=== geometry-matched planner-vs-excite delta, block bootstrap (BLOCK=20 cycles, 600 reps) ===")
for t,(p,e,w) in PAIRS.items():
    A,B=stack(p,w),stack(e,w)
    d=matched(A,B); lo,hi=boot(A,B)
    res[t]=d
    sig="*" if (lo>0 or hi<0) else " "
    print(f"  {t:11} {d:+6.1f} pp  [{lo:+6.1f}, {hi:+6.1f}] {sig}    Phase4 climb effect {PH4[t]:+.4f}")

ks=list(PH4); x=[PH4[k] for k in ks]; y=[res[k] for k in ks]
print(f"\n  vs Phase 4 climb-effect:  Pearson {stats.pearsonr(x,y)[0]:+.3f}   Spearman {stats.spearmanr(x,y)[0]:+.3f}")
print(f"  sign agreement: {sum(1 for a,b in zip(x,y) if np.sign(a)==np.sign(b))}/4")
