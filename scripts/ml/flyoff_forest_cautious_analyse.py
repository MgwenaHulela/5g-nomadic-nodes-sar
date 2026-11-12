"""Cautious-learner forest runs (2026-11-12) vs the capped fly-off arms. Same metric as
flyoff_forest_capped_analyse.py: time-weighted, C2 up = loss < 50 %, 10-min block bootstrap."""
import sys, re, numpy as np, pandas as pd
rng = np.random.default_rng(0)
def load(dr):
    d = pd.read_csv(f"{dr}/sar_forest.csv"); t = pd.to_datetime(d.timestamp, errors="coerce")
    ts = (t - t.iloc[0]).dt.total_seconds().values
    dt = np.clip(np.diff(ts, append=ts[-1] + np.median(np.diff(ts))), 0, 120)
    up = np.column_stack([d[f"{c}_c2_loss_pct"].values for c in "abcd"]) < 50
    return d, ts, dt, up, up.sum(1)
def boot(ts, dt, up, n, f, B=2000):
    blk = (ts // 600).astype(int); ub = np.unique(blk); v = []
    for _ in range(B):
        s = rng.choice(ub, len(ub)); i = np.concatenate([np.where(blk == b)[0] for b in s])
        v.append(f(dt[i], up[i], n[i]))
    return np.array(v)
A4 = lambda dt, up, n: 100*np.sum(dt*(n==4))/dt.sum()
C2 = lambda dt, up, n: 100*np.sum(dt*up.mean(1))/dt.sum()
runs = {"capA": "logs/campaign_20261112_050243", "capB": "logs/campaign_20261112_015702",
        "capC_prober": "logs/campaign_20261112_032955", "cautC": "logs/campaign_20261112_102709", "capD_prober": "logs/campaign_20261112_002402", "cautD": "logs/campaign_20261112_115951"}
runs.update(dict(a.split("=") for a in sys.argv[1:]))
S = {}
for k, dr in runs.items():
    d, ts, dt, up, n = load(dr); w = dt/dt.sum(); z = d.uav_z_m.values; h = ts >= ts[-1]/2
    lat = np.column_stack([d[f"{c}_vid_latency_ms"] for c in "abcd"])
    R = open(f"{dr}/sar_forest_research.log").read()
    S[k] = (ts, dt, up, n)
    bs = boot(ts, dt, up, n, A4, 1000)
    print(f"{k:12s} rows {len(d)} C2 {C2(dt,up,n):.1f} all4 {A4(dt,up,n):.1f} ({np.percentile(bs,2.5):.0f}-{np.percentile(bs,97.5):.0f}) "
          f"ge3 {100*np.sum(w*(n>=3)):.1f} none {100*np.sum(w*(n==0)):.1f} z2nd {np.sum(w[h]*z[h])/w[h].sum():.1f} "
          f"z_at40 {100*np.sum(w*(np.abs(z-40)<2.5)):.0f}% vidp90 {np.nanpercentile(lat,90)/1000:.1f}s "
          f"rec {len(re.findall('rlf_recovered after',R))} fail {len(re.findall('reattach_failed after',R))}")
for k in [k for k in S if k.startswith("caut")]:
    for b in (["capA","capB","capC_prober"] if k=="cautC" else ["capA","capB","capD_prober","cautC"]):
        for nm, f in [("all4", A4), ("C2", C2)]:
            dif = boot(*S[k], f) - boot(*S[b], f)
            print(f"  {k}-{b} {nm}: {dif.mean():+.1f} [{np.percentile(dif,2.5):+.1f}, {np.percentile(dif,97.5):+.1f}]")
