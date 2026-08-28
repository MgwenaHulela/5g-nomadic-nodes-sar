#!/usr/bin/env python3
"""paper_line_figs.py — LINE-graph result set (no bar charts) for the SATNAC
paper: A2G model vs injected vs measured path loss per terrain, path loss as the
UAV moves, video/throughput/coverage vs distance, link adaptation, and recovery
CDFs. Reuses the terrain channel functions for the model curve."""
import sys, math
from pathlib import Path
import numpy as np, pandas as pd
import matplotlib; matplotlib.use("Agg")
import matplotlib.pyplot as plt

ROOT = Path.home()/"sar_sim"; FIG = ROOT/"docs/paper/figures"
sys.path.insert(0, str(ROOT/"worlds"))
from urban_buildings import any_blocked
from forest_trees import foliage_depth, foliage_loss_db, DEPTH_NLOS_M
from terrain_hills import terrain_diffraction_db, DIFFRACTION_NLOS_DB

OI = dict(blue="#0072B2", orange="#E69F00", green="#009E73", sky="#56B4E9",
          vermillion="#D55E00", purple="#CC79A7", grey="#888888")
PH = {"1": ("Static gNB", OI["blue"]), "2": ("Roaming gNB", OI["vermillion"])}
plt.rcParams.update({"font.family":"serif","font.size":9,"axes.titlesize":8.5,
 "axes.labelsize":8.5,"legend.fontsize":7,"xtick.labelsize":7.5,"ytick.labelsize":7.5,
 "axes.grid":True,"grid.alpha":0.25,"grid.linewidth":0.5,"axes.axisbelow":True,
 "figure.dpi":150,"savefig.bbox":"tight","pdf.fonttype":42,"legend.frameon":False})

C1 = ROOT/"logs/campaign_20260728_210058"; C2 = ROOT/"logs/campaign_20260729_122440"
WORLDS = [("Open field","open_field",C1/"sar_open_field.csv"),
          ("Urban","urban",C2/"sar_urban.csv"),
          ("Forest","forest",C2/"sar_forest.csv"),
          ("Hills","hills",C2/"sar_hills.csv")]

def load(p):
    d = pd.read_csv(p)
    for c in d.columns:
        if c not in ("timestamp","phase","a_event","b_event"):
            d[c] = pd.to_numeric(d[c], errors="coerce")
    d["phase"] = d["phase"].astype(str); return d
data = {n: load(p) for n,_,p in WORLDS}

# ---- A2G model (mirror of bridge) for the model curve ----
FREQ, UEH, A_C, B_C, ETA_L, ETA_N = 3.31968, 0.5, 9.61, 0.16, 1.0, 20.0
BASE_B = 13.0
def model_pl(world, ux, uy, uz, rx, ry):
    ue = (rx, ry, UEH); uav = (ux, uy, uz)
    dh = math.hypot(ux-rx, uy-ry)
    if world == "urban": blocked, exc = any_blocked(uav, ue), 0.0
    elif world == "forest":
        dep = foliage_depth(uav, ue); blocked, exc = dep > DEPTH_NLOS_M, foliage_loss_db(dep)
    elif world == "hills":
        L = terrain_diffraction_db(uav, ue); blocked, exc = L > DIFFRACTION_NLOS_DB, L
    else: blocked, exc = False, 0.0
    d3 = max(math.hypot(dh, uz-UEH), 1.0)
    fspl = 20*math.log10(d3)+20*math.log10(FREQ*1e9)-147.55
    th = math.degrees(math.atan2(uz-UEH, max(dh,0.1)))
    plos = 0.0 if blocked else 1/(1+A_C*math.exp(-B_C*(th-A_C)))
    return fspl + plos*ETA_L + (1-plos)*ETA_N + exc
PL_REF = model_pl("open_field",0,0,30,50,0)

def binline(ax, x, y, nb, color, label, marker="o", ls="-"):
    m = ~(np.isnan(x)|np.isnan(y)); x,y = x[m],y[m]
    if len(x) < 6: return
    bins = np.linspace(np.nanpercentile(x,2), np.nanpercentile(x,98), nb)
    idx = np.digitize(x,bins); xs,ys=[],[]
    for b in range(1,len(bins)):
        s=y[idx==b]
        if len(s)>=3: xs.append((bins[b-1]+bins[b])/2); ys.append(np.mean(s))
    ax.plot(xs,ys,ls,marker=marker,color=color,ms=3,lw=1.4,label=label)

# ============ FIG 1: path loss per terrain — MODEL vs INJECTED vs MEASURED ====
fig, axes = plt.subplots(2,2,figsize=(5.4,4.0),sharex=True,sharey=True)
for ax,(nice,w,_) in zip(axes.ravel(),WORLDS):
    df = data[nice]
    d = df["a_d_slant_m"].values
    inj = df["a_ploss_dB"].values                              # injected (applied)
    meas = (-37.0 - df["a_ss_rsrp_dBm"].values)               # measured, from SS-RSRP
    mod = np.array([BASE_B + (model_pl(w,r.uav_x_m,r.uav_y_m,r.uav_z_m,r.ax_m,r.ay_m)-PL_REF)
                    for r in df.itertuples()])                 # A2G model
    binline(ax,d,mod,8,OI["grey"],"A2G model",marker="",ls="--")
    binline(ax,d,inj,8,OI["blue"],"Injected",marker="o")
    binline(ax,d,meas,8,OI["vermillion"],"Measured (SS-RSRP)",marker="s")
    ax.text(0.03,0.93,nice,transform=ax.transAxes,fontsize=8,fontweight="bold",va="top")
    ax.set_ylim(0,45)
axes[0,0].legend(loc="lower right",fontsize=6.5)
for ax in axes[-1,:]: ax.set_xlabel("UAV–robot slant distance (m)")
for ax in axes[:,0]: ax.set_ylabel("Path loss (dB)")
fig.suptitle("A2G path loss per terrain: model, injected, measured",fontsize=9,y=1.0)
fig.savefig(FIG/"fig_ploss_model.pdf"); plt.close(fig)

# ============ FIG 2: path loss as the UAV moves (time series) =================
fig, axes = plt.subplots(4,1,figsize=(3.5,6.0),sharex=True)
for ax,(nice,w,_) in zip(axes,WORLDS):
    df = data[nice]; t = df["t_elapsed_s"]/3600.0
    ax.axvspan(3, t.max(), color=OI["grey"], alpha=0.10, lw=0)
    ax.axvline(3, color=OI["vermillion"], ls="--", lw=1)
    for s,c,lab in (("a",OI["blue"],"robot $\\alpha$"),("b",OI["green"],"robot $\\beta$")):
        y = df[f"{s}_ploss_dB"].rolling(9,min_periods=1,center=True).mean()
        ax.plot(t,y,color=c,lw=1.0,label=lab)
    ax.axhline(35,color="black",ls=":",lw=0.7); ax.set_ylim(0,45)
    ax.text(0.02,0.9,nice,transform=ax.transAxes,fontsize=8,fontweight="bold",va="top")
axes[0].legend(loc="lower right",ncol=2,fontsize=6.5)
axes[0].text(1.5,40,"static",ha="center",fontsize=6,color="0.4")
axes[0].text((3+data["Open field"]["t_elapsed_s"].max()/3600)/2,40,"roaming",ha="center",fontsize=6,color="0.4")
axes[-1].set_xlabel("mission time (h)")
fig.suptitle("Path loss as the UAV moves",fontsize=9,y=0.995)
fig.savefig(FIG/"fig_ts_ploss.pdf"); plt.close(fig)

# ============ FIG 3: video loss vs distance (line), static vs roaming ========
def dist_panels(ycol, ylab, title, fname, ylim=None, conn=False):
    fig,axes = plt.subplots(2,2,figsize=(5.4,4.0),sharex=True,sharey=True)
    for ax,(nice,w,_) in zip(axes.ravel(),WORLDS):
        df = data[nice]
        for ph in ("1","2"):
            sub = df[df["phase"]==ph]
            d = pd.concat([sub["a_d_slant_m"],sub["b_d_slant_m"]]).values
            if conn:
                y = (pd.concat([sub["a_c2_loss_pct"],sub["b_c2_loss_pct"]])<100).astype(float).values*100
            else:
                y = pd.concat([sub[f"a_{ycol}"],sub[f"b_{ycol}"]]).values
            binline(ax,d,y,8,PH[ph][1],PH[ph][0])
        ax.text(0.03,0.06,nice,transform=ax.transAxes,fontsize=8,fontweight="bold",va="bottom")
        if ylim: ax.set_ylim(*ylim)
    axes[0,0].legend(loc="upper right",fontsize=6.5)
    for ax in axes[-1,:]: ax.set_xlabel("UAV–robot slant distance (m)")
    for ax in axes[:,0]: ax.set_ylabel(ylab)
    fig.suptitle(title,fontsize=9,y=1.0); fig.savefig(FIG/fname); plt.close(fig)

dist_panels("udp_lost_pct","Video loss (\\%)","Video loss vs distance: static vs roaming",
            "fig_video_distance.pdf",ylim=(0,100))

# ============ FIG 4: link adaptation — spectral efficiency vs SINR (line) =====
fig,ax = plt.subplots(figsize=(3.5,2.5))
for nice,w,_ in WORLDS:
    df = data[nice]
    x = pd.concat([df["a_ss_sinr_dB"],df["b_ss_sinr_dB"]]).values
    y = pd.concat([df["a_ul_bits_per_sym"],df["b_ul_bits_per_sym"]]).values
    binline(ax,x,y,9,{"Open field":OI["blue"],"Urban":OI["orange"],"Forest":OI["green"],
                       "Hills":OI["sky"]}[nice],nice)
ax.set_xlabel("SS-SINR (dB)"); ax.set_ylabel("UL spectral eff. (bits/sym)")
ax.set_title("Link adaptation: MCS follows SINR",fontsize=8.5)
ax.legend(loc="lower right",ncol=2)
fig.savefig(FIG/"fig_linkadapt_line.pdf"); plt.close(fig)

# ============ FIG 5: recovery-time CDF per terrain (line) =====================
fig,ax = plt.subplots(figsize=(3.5,2.5))
for nice,w,_ in WORLDS:
    df = data[nice]; outs=[]
    for s in ("a","b"):
        ev = df[df[f"{s}_event"].astype(str)=="rlf_recovered"]
        outs += list(ev[f"{s}_outage_s"].dropna().values)
    if not outs: continue
    v = np.sort(np.array(outs,float))
    ax.plot(v, np.linspace(0,1,len(v)),lw=1.6,
            color={"Open field":OI["blue"],"Urban":OI["orange"],"Forest":OI["green"],
                   "Hills":OI["sky"]}[nice],label=f"{nice} (n={len(v)})")
ax.set_xlabel("recovery time (s)"); ax.set_ylabel("empirical CDF"); ax.set_ylim(0,1)
ax.set_title("Connectivity recovery time (governor-mediated)",fontsize=8)
ax.legend(loc="lower right")
fig.savefig(FIG/"fig_recovery_cdf.pdf"); plt.close(fig)

print("wrote fig_ploss_model, fig_ts_ploss, fig_video_distance, fig_linkadapt_line, fig_recovery_cdf")
