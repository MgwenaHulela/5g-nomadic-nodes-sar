#!/usr/bin/env python3
"""Phase 6 data layer — the 10 Hz UE-measured radio traces.

The cycle CSVs sample every 11-22 s, so every model so far has worked from
hand-engineered summaries (lag1-3, rolling mean/std) of a very coarse series.
Four runs in the final campaigns carry the UE's own RSRP/SINR at **10 Hz** --
100x the cycle rate -- and, by luck of scheduling, they are one per terrain:

    campaign_20261025_235902   open_field   53,074 samples
    campaign_20261026_013033   urban        54,187
    campaign_20261026_030216   forest       54,284
    campaign_20261026_043517   hills        54,129

That is ~90 minutes per terrain at 0.1 s resolution, and it preserves the
leave-one-terrain-out structure the whole project is evaluated under. It is
the only place in this dataset where a sequence model has raw temporal
structure to learn from rather than four lag columns.

Permitted inputs: `ss_rsrp_dBre`, `ss_sinr_dB` and friends are UE MEASUREMENTS
(dataset.FEATURE_GROUPS["rsrp"]). `rsrp_computed_dBm` and `ploss_dB` are the
simulator's algebraic ground truth and stay forbidden; `terrain` is a fold
label only. assert_terrain_blind() runs on everything that leaves here.

Labels come from the cycle CSVs at cycle rate and are carried onto the 10 Hz
grid with a backward as-of join, so a label is never interpolated forward from
the future. Dead-UE stretches (onset_forecast.dead_ue_mask) are censored at
both t and t+h, exactly as in Phase 2b.

Usage:
    from fastradio_dataset import build_fast, make_windows
    tr = build_fast()                       # long, 10 Hz, labelled
    W, y, meta = make_windows(tr, win_s=30, horizon_s=20)
"""
from __future__ import annotations

import glob
import os
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import dataset as ds                                      # noqa: E402
from onset_forecast import dead_ue_mask                   # noqa: E402

CACHE = ds.CACHE
FS = 10.0                       # Hz, the fastradio cadence

# Per-robot channels available at 10 Hz. Ordered; this IS the channel axis.
CHANNELS = ["rsrp_dBre", "sinr_dB", "rsrp_spread", "n_samples", "age_s"]


def discover_fast() -> list[tuple[str, Path, Path]]:
    """(terrain, fastradio_csv, cycle_csv) for every run with real 10 Hz RSRP."""
    out = []
    for d in sorted(glob.glob(str(ds.LOG_DIR / "campaign_2026102[3-6]_*/"))):
        if any(x in d for x in ("SUPERSEDED", "PARTIAL", "ABORTED")):
            continue
        for f in sorted(glob.glob(os.path.join(d, "sar_*_fastradio.csv"))):
            cyc = Path(f.replace("_fastradio.csv", ".csv"))
            if not cyc.exists():
                continue
            head = pd.read_csv(f, nrows=2000, low_memory=False)
            if pd.to_numeric(head.get("alpha_rsrp_dBre"),
                             errors="coerce").notna().sum() == 0:
                continue                     # empty RSRP pipeline, skip
            terr = str(pd.read_csv(cyc, nrows=1)["terrain"].iloc[0]
                       ).replace("sar_", "")
            out.append((terr, Path(f), cyc))
    return out


def _fast_to_long(path: Path, terrain: str) -> pd.DataFrame:
    w = pd.read_csv(path, low_memory=False)
    frames = []
    for robot in ds.ROBOT_OF.values():
        cols = {f"{robot}_rsrp_dBre": "rsrp_dBre",
                f"{robot}_sinr_dB": "sinr_dB",
                f"{robot}_rsrp_dBre_min": "_min",
                f"{robot}_rsrp_dBre_max": "_max",
                f"{robot}_n_samples": "n_samples",
                f"{robot}_age_s": "age_s"}
        have = {k: v for k, v in cols.items() if k in w.columns}
        if "rsrp_dBre" not in have.values():
            continue
        sub = w[list(have)].rename(columns=have).apply(
            pd.to_numeric, errors="coerce")
        sub["rsrp_spread"] = sub.get("_max", np.nan) - sub.get("_min", np.nan)
        sub = sub.drop(columns=[c for c in ("_min", "_max") if c in sub])
        sub["t_elapsed_s"] = pd.to_numeric(w["t_elapsed_s"], errors="coerce")
        sub["robot"] = robot
        sub["run_id"] = str(w["run_id"].iloc[0])
        sub["terrain"] = terrain
        frames.append(sub)
    return pd.concat(frames, ignore_index=True)


def build_fast(cache: bool = True, horizon_s: float = 20.0) -> pd.DataFrame:
    """10 Hz long table with the C2 label and dead-UE flag joined on."""
    CACHE.mkdir(parents=True, exist_ok=True)
    cpath = CACHE / f"fastradio_long_h{int(horizon_s)}.pkl"
    if cache and cpath.exists():
        return pd.read_pickle(cpath)

    cyc = ds.build().sort_values(["run_id", "robot", "t_elapsed_s"],
                                 kind="stable").reset_index(drop=True)
    cyc["dead"] = dead_ue_mask(cyc)
    cyc = ds.add_target(cyc, horizon_s, "c2_ok")
    cyc = ds.add_target(cyc, horizon_s, "dead").reset_index(drop=True)
    ycol, dcol = f"y_c2_ok_h{int(horizon_s)}", f"y_dead_h{int(horizon_s)}"

    frames = []
    for terr, fpath, _ in discover_fast():
        lf = _fast_to_long(fpath, terr)
        c = cyc[cyc["run_id"] == lf["run_id"].iloc[0]]
        if c.empty:
            continue
        keep = ["run_id", "robot", "t_elapsed_s", "c2_ok", "dead",
                ycol, dcol, "d_slant_m", "uav_z_m"]
        c = c[[k for k in keep if k in c.columns]]
        parts = []
        for robot, g in lf.groupby("robot", sort=False):
            cg = c[c["robot"] == robot].sort_values("t_elapsed_s").copy()
            if cg.empty:
                continue
            # Carry the CYCLE's own timestamp through the join. Every 10 Hz
            # sample inheriting one cycle's label shares a `cycle_t`, so the
            # evaluator can collapse them -- 10 Hz rows are not independent
            # observations, the cycles are.
            cg["cycle_t"] = cg["t_elapsed_s"].values
            g = g.sort_values("t_elapsed_s")
            m = pd.merge_asof(g, cg.drop(columns=["run_id", "robot"]),
                              on="t_elapsed_s", direction="backward",
                              tolerance=30.0)
            parts.append(m)
        if parts:
            frames.append(pd.concat(parts, ignore_index=True))

    out = pd.concat(frames, ignore_index=True)
    out = out.sort_values(["run_id", "robot", "t_elapsed_s"],
                          kind="stable").reset_index(drop=True)
    out["y"] = 1.0 - out[ycol]
    out["dead_tgt"] = out[dcol].fillna(0) > 0.5
    if cache:
        out.to_pickle(cpath)
    return out


def make_windows(df: pd.DataFrame, win_s: float = 30.0,
                 stride_s: float = 1.0, anchor: str = "stride"):
    """Index windows lazily: returns (starts, series, y, meta).

    `series` is one float32 array per (run, robot) of shape (T, C); `starts`
    indexes the END of each window into it. Materialising every window would
    be ~0.5 GB, and the model only ever needs a batch at a time.

    **The independence trap.** The C2 label updates once per cycle, and a
    cycle is 11 s in hills but 55 s in forest, so one label is inherited by
    110-550 consecutive 10 Hz samples. Striding at 1 s therefore manufactures
    up to 55 near-duplicate rows per real observation. `meta["group"]` names
    the originating cycle so evaluation can collapse them; `anchor="cycle"`
    emits exactly one window per cycle and is what any reported score uses.
    Striding is for TRAINING augmentation only.
    """
    W = int(round(win_s * FS))
    S = max(1, int(round(stride_s * FS)))
    series, starts, ys, meta = [], [], [], []
    for (rid, robot), g in df.groupby(["run_id", "robot"], sort=False):
        g = g.sort_values("t_elapsed_s").reset_index(drop=True)
        arr = g[CHANNELS].to_numpy(np.float32)
        med = np.nanmedian(arr, axis=0)
        med = np.where(np.isfinite(med), med, 0.0)
        arr = np.where(np.isfinite(arr), arr, med)
        k = len(series)
        series.append(arr)
        ok = (g["y"].notna() & (g["c2_ok"] == 1)
              & ~g["dead"].astype(float).fillna(1.0).astype(bool)
              & ~g["dead_tgt"]).to_numpy()
        if anchor == "cycle":
            # first 10 Hz sample of each cycle, once the window is full
            ct = g["cycle_t"].to_numpy()
            first = np.flatnonzero(np.r_[True, ct[1:] != ct[:-1]])
            idx = first[(first >= W) & ok[first]]
        else:
            idx = np.arange(W, len(g), S)
            idx = idx[ok[idx]]
        if not len(idx):
            continue
        starts.append(np.stack([np.full(len(idx), k), idx], 1))
        ys.append(g["y"].to_numpy(np.float32)[idx])
        meta.append(pd.DataFrame({
            "run_id": rid, "robot": robot,
            "terrain": g["terrain"].iloc[0],
            "t_elapsed_s": g["t_elapsed_s"].to_numpy()[idx],
            "group": [f"{rid}|{robot}|{c:.1f}"
                      for c in g["cycle_t"].to_numpy()[idx]]}))
    if not starts:
        return np.zeros((0, 2), int), series, np.zeros(0, np.float32), \
            pd.DataFrame(columns=["run_id", "robot", "terrain",
                                  "t_elapsed_s", "group"])
    return (np.concatenate(starts), series, np.concatenate(ys),
            pd.concat(meta, ignore_index=True))


if __name__ == "__main__":
    runs = discover_fast()
    print(f"{len(runs)} runs with 10 Hz RSRP:")
    for t, f, _ in runs:
        print(f"  {t:<12}{f.parent.name}")
    d = build_fast(cache="--no-cache" not in sys.argv)
    print(f"\n10 Hz long table: {len(d):,} rows x {d.shape[1]} cols")
    print(d.groupby("terrain").agg(n=("y", "size"),
                                   labelled=("y", lambda s: int(s.notna().sum())),
                                   dead=("dead", "mean"),
                                   fail_rate=("y", "mean")).round(3).to_string())
    for anchor in ("cycle", "stride"):
        st, ser, y, meta = make_windows(d, anchor=anchor)
        print(f"\nwindows ({anchor}): {len(st):,}  "
              f"(30 s @ 10 Hz = 300 x {len(CHANNELS)} ch)"
              f"  FAIL {100*y.mean():.1f}%  "
              f"independent cycles {meta['group'].nunique():,}")
        print(meta.groupby("terrain").agg(
            windows=("group", "size"),
            cycles=("group", "nunique")).to_string())
