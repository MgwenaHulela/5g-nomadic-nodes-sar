#!/usr/bin/env python3
"""Phase 0 data layer for the terrain-blind predictive ML work.

Everything downstream (baselines, forecasters, the sign-flip test) loads its
data through here, so the project's hard rules are enforced in ONE place:

  * **Forbidden inputs are enforced in code, not by discipline.**
    `ploss_dB` is the injected simulator ground truth; `rsrp_computed_dBm` is
    algebraically the same quantity (`rsrp_computed + ploss` is constant at
    -37.0 dB, corr -1.000); `terrain` is recorded for evaluation and
    stratification only. `assert_terrain_blind()` raises if any of them
    reaches a feature matrix.
  * **History windows never span a run boundary.** Each (run_id, robot) is an
    independent series; lags and targets are computed within the group only.
  * **Splits are never random rows** — a random split leaks across the history
    window. Only leave-one-terrain-out, by-run-seed and by-time-block.

The per-cycle CSVs are WIDE (one row per cycle, four robots side by side with
`a_`/`b_`/`c_`/`d_` prefixes). Everything here works in LONG format: one row
per (run_id, cycle, robot), which is what the models want and what makes the
per-robot soft-min objective expressible.

Usage:
    from dataset import build, leave_one_terrain_out, FEATURE_GROUPS
    df = build()                       # long format, all runs, cached
    X, y = features_targets(df, horizon_s=60, groups=["geom","radio"])
"""
from __future__ import annotations

import glob
import os
import re
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parents[2]
LOG_DIR = REPO / "logs"
CACHE = REPO / "logs" / "ml_cache"

ROBOT_OF = {"a": "alpha", "b": "bravo", "c": "charlie", "d": "delta"}

# --------------------------------------------------------------------------
# The rule that the whole project rests on.
# --------------------------------------------------------------------------
FORBIDDEN = re.compile(
    r"(^|_)(ploss_dB|rsrp_computed_dBm|terrain)(_|$)", re.IGNORECASE
)


def assert_terrain_blind(columns) -> None:
    """Raise if a forbidden input is about to reach a model."""
    bad = [c for c in columns if FORBIDDEN.search(str(c))]
    if bad:
        raise ValueError(
            "FORBIDDEN model inputs present: "
            f"{bad}\nThese are simulator ground truth or terrain labels; they "
            "may be used for evaluation and stratification ONLY "
            "(configs/experiment.json: forbidden_model_inputs)."
        )


# --------------------------------------------------------------------------
# Feature groups — the unit of ablation.
# --------------------------------------------------------------------------
FEATURE_GROUPS: dict[str, list[str]] = {
    # Geometry the UAV knows about itself and its robots.
    "geom": ["d_horiz_m", "d_slant_m", "uav_x_m", "uav_y_m", "uav_z_m"],
    # gNB-side link adaptation + error rates. Present in EVERY run.
    "radio": ["ul_bits_per_sym", "ul_code_rate", "ul_nb_rbs", "ul_nb_sym",
              "dl_harq_ok", "dl_harq_nok", "dl_bler", "tcp_retransmits"],
    # UE-measured RSRP/SINR. Only the 4 recovery runs (dataset B).
    "rsrp": ["ss_rsrp_dBm", "ss_rsrp_dBre", "ss_sinr_dB"],
    # The label's OWN history: what persistence uses. Legitimate for a
    # forecaster (autoregression is not leakage -- these are observations at
    # time t, not at t+h), but kept in its own group so "forecast the failure
    # without ever observing a failure" is a runnable ablation.
    "c2hist": ["c2_ok", "c2_loss_pct"],
    # LEAKY: ping loss is close to the C2 label. Brief requires reporting
    # every forecaster result with AND without this group.
    "ping": ["rtt_min_ms", "rtt_avg_ms", "rtt_max_ms", "ping_loss_pct",
             "c2_rtt_ms"],
    # LEAKY: user-plane quality degrades with the link, so nearly as close to
    # the label as ping loss. Not in the brief; added because the leak is real.
    "video": ["vid_goodput_mbps", "vid_frame_delivery_pct", "vid_pkt_loss_pct",
              "vid_latency_ms", "vid_jitter_ms"],
    # What the UAV just did — the action context the planner will condition on.
    "action": ["action_dx_m", "action_dy_m", "action_dz_m", "phase"],
}
LEAKY_GROUPS = ("ping", "video")

# Metadata: carried through for evaluation and stratification, NEVER features.
META = ["run_id", "seed", "terrain", "policy", "robot", "t_elapsed_s",
        "timestamp", "action", "campaign"]


@dataclass
class RunMeta:
    campaign: str
    terrain: str
    policy: str
    seed: str
    run_id: str
    cycle_csv: Path
    fast_csv: Path | None
    action_csv: Path | None


def discover_runs(include_smoke: bool = False) -> list[RunMeta]:
    """Every usable world-run on disk, newest campaigns included."""
    out: list[RunMeta] = []
    for d in sorted(glob.glob(str(LOG_DIR / "campaign_2026102[3-6]_*/"))):
        if any(x in d for x in ("SUPERSEDED", "PARTIAL", "ABORTED")):
            continue
        for f in sorted(glob.glob(os.path.join(d, "sar_*.csv"))):
            if "_fastradio" in f or "_actions" in f:
                continue
            head = pd.read_csv(f, nrows=1)
            if head.empty:
                continue
            n = sum(1 for _ in open(f)) - 1
            if not include_smoke and n < 50:     # the 300 s smoke run
                continue
            base = f[:-4]
            out.append(RunMeta(
                campaign=Path(d).name,
                terrain=str(head["terrain"].iloc[0]).replace("sar_", ""),
                policy=str(head["policy"].iloc[0]),
                seed=str(head["seed"].iloc[0]),
                run_id=str(head["run_id"].iloc[0]),
                cycle_csv=Path(f),
                fast_csv=Path(base + "_fastradio.csv")
                if os.path.exists(base + "_fastradio.csv") else None,
                action_csv=Path(base + "_actions.csv")
                if os.path.exists(base + "_actions.csv") else None,
            ))
    return out


def _wide_to_long(wide: pd.DataFrame, meta: RunMeta) -> pd.DataFrame:
    """One row per (cycle, robot). Drops forbidden columns at the source."""
    frames = []
    for pfx, robot in ROBOT_OF.items():
        cols, ren = [], {}
        for c in wide.columns:
            if c.startswith(f"{pfx}_"):
                stem = c[len(pfx) + 1:]
                if FORBIDDEN.search(stem):
                    continue                     # never even loaded
                cols.append(c); ren[c] = stem
            elif c in (f"{pfx}x_m", f"{pfx}y_m"):
                cols.append(c); ren[c] = "robot_" + c[len(pfx):]
        if not cols:
            continue
        sub = wide[cols].rename(columns=ren).copy()
        for g in ("t_elapsed_s", "timestamp", "uav_x_m", "uav_y_m", "uav_z_m",
                  "phase", "action", "action_tx_m", "action_ty_m",
                  "action_tz_m"):
            if g in wide.columns:
                sub[g] = wide[g]
        sub["robot"] = robot
        frames.append(sub)

    long = pd.concat(frames, ignore_index=True)
    long["run_id"] = meta.run_id
    long["seed"] = meta.seed
    long["terrain"] = meta.terrain          # metadata only
    long["policy"] = meta.policy
    long["campaign"] = meta.campaign

    # Action as a displacement the UAV commanded, not an absolute waypoint.
    for ax in "xyz":
        tgt, cur = f"action_t{ax}_m", f"uav_{ax}_m"
        long[f"action_d{ax}_m"] = (
            pd.to_numeric(long[tgt], errors="coerce")
            - pd.to_numeric(long[cur], errors="coerce")
            if tgt in long and cur in long else np.nan)

    for c in long.columns:
        if c not in META and c != "robot":
            long[c] = pd.to_numeric(long[c], errors="coerce")

    # The label (brief 5.2): C2 is available when ping loss < 50% in the window.
    long["c2_ok"] = (long["c2_loss_pct"] < 50).astype(float)
    long.loc[long["c2_loss_pct"].isna(), "c2_ok"] = np.nan
    return long


def build(cache: bool = True, include_smoke: bool = False) -> pd.DataFrame:
    """Long-format table of every cycle of every run."""
    CACHE.mkdir(parents=True, exist_ok=True)
    cpath = CACHE / f"cycles_long{'_smoke' if include_smoke else ''}.pkl"
    if cache and cpath.exists():
        return pd.read_pickle(cpath)

    runs = discover_runs(include_smoke)
    frames = []
    for m in runs:
        wide = pd.read_csv(m.cycle_csv, low_memory=False)
        if wide.empty:
            continue
        frames.append(_wide_to_long(wide, m))
    df = pd.concat(frames, ignore_index=True)
    df = df.sort_values(["run_id", "robot", "t_elapsed_s"],
                        kind="stable").reset_index(drop=True)
    if cache:
        df.to_pickle(cpath)
    return df


# --------------------------------------------------------------------------
# History and targets — strictly within a (run_id, robot) series.
# --------------------------------------------------------------------------
def add_history(df: pd.DataFrame, cols: list[str],
                lags: tuple[int, ...] = (1, 2, 3)) -> pd.DataFrame:
    """Lagged values, deltas and rolling stats. Never spans a run boundary."""
    df = df.sort_values(["run_id", "robot", "t_elapsed_s"],
                        kind="stable").copy()
    g = df.groupby(["run_id", "robot"], sort=False)
    new = {}
    for c in cols:
        if c not in df.columns:
            continue
        for L in lags:
            new[f"{c}_lag{L}"] = g[c].shift(L)
        new[f"{c}_d1"] = df[c] - g[c].shift(1)
        new[f"{c}_roll3_mean"] = g[c].transform(
            lambda s: s.rolling(3, min_periods=1).mean())
        new[f"{c}_roll3_std"] = g[c].transform(
            lambda s: s.rolling(3, min_periods=2).std())
    return pd.concat([df, pd.DataFrame(new, index=df.index)], axis=1)


def add_target(df: pd.DataFrame, horizon_s: float, col: str = "c2_ok",
               tol_frac: float = 0.5) -> pd.DataFrame:
    """Value of `col` at t + horizon_s, matched within the same series.

    A match must land within `tol_frac * horizon_s` of the requested time,
    otherwise the target is NaN — the cycle cadence is 11-22 s and ragged, so
    silently accepting a far-away row would fabricate the horizon.
    """
    df = df.sort_values(["run_id", "robot", "t_elapsed_s"],
                        kind="stable").copy()
    tgt = np.full(len(df), np.nan)
    dt = np.full(len(df), np.nan)
    tol = tol_frac * horizon_s
    for _, idx in df.groupby(["run_id", "robot"], sort=False).indices.items():
        t = df["t_elapsed_s"].values[idx]
        v = df[col].values[idx]
        want = t + horizon_s
        j = np.searchsorted(t, want)
        j = np.clip(j, 0, len(t) - 1)
        # take whichever neighbour is closer
        jm = np.clip(j - 1, 0, len(t) - 1)
        pick = np.where(np.abs(t[j] - want) <= np.abs(t[jm] - want), j, jm)
        err = np.abs(t[pick] - want)
        ok = err <= tol
        tgt[idx] = np.where(ok, v[pick], np.nan)
        dt[idx] = np.where(ok, t[pick] - t, np.nan)
    df[f"y_{col}_h{int(horizon_s)}"] = tgt
    df[f"y_dt_h{int(horizon_s)}"] = dt
    return df


def feature_columns(df: pd.DataFrame, groups: list[str],
                    with_history: bool = True) -> list[str]:
    base = []
    for gname in groups:
        base += FEATURE_GROUPS[gname]
    cols = []
    for c in df.columns:
        stem = re.sub(r"_(lag\d+|d1|roll3_mean|roll3_std)$", "", c)
        if stem in base and (with_history or stem == c):
            cols.append(c)
    assert_terrain_blind(cols)
    return cols


def features_targets(df: pd.DataFrame, horizon_s: float,
                     groups: list[str], target: str = "c2_ok"):
    ycol = f"y_{target}_h{int(horizon_s)}"
    if ycol not in df.columns:
        df = add_target(df, horizon_s, target)
    cols = feature_columns(df, groups)
    m = df[ycol].notna()
    X, y = df.loc[m, cols], df.loc[m, ycol]
    assert_terrain_blind(X.columns)
    return X, y, df.loc[m, META]


# --------------------------------------------------------------------------
# Splits. Never random rows.
# --------------------------------------------------------------------------
def leave_one_terrain_out(meta: pd.DataFrame):
    for t in sorted(meta["terrain"].unique()):
        yield t, (meta["terrain"] != t).values, (meta["terrain"] == t).values


def by_run_seed(meta: pd.DataFrame, n_folds: int = 4):
    seeds = sorted(meta["campaign"].unique())
    for i in range(min(n_folds, len(seeds))):
        held = seeds[i::n_folds]
        te = meta["campaign"].isin(held).values
        yield ",".join(held), ~te, te


def by_time_block(meta: pd.DataFrame, test_frac: float = 0.3):
    """Last `test_frac` of each run is the test block — no window leakage."""
    te = np.zeros(len(meta), dtype=bool)
    for _, idx in meta.groupby("run_id", sort=False).indices.items():
        t = meta["t_elapsed_s"].values[idx]
        te[idx[t >= np.quantile(t, 1 - test_frac)]] = True
    yield f"last_{int(test_frac*100)}pct", ~te, te


if __name__ == "__main__":
    import sys
    df = build(cache="--no-cache" not in sys.argv)
    print(f"long table: {len(df):,} rows x {df.shape[1]} cols "
          f"({df['run_id'].nunique()} runs, {df['robot'].nunique()} robots)")
    print("\nrows per terrain x policy:")
    print(df.pivot_table(index="terrain", columns="policy",
                         values="c2_ok", aggfunc="size", fill_value=0))
    print(f"\nC2 available: {100*df['c2_ok'].mean():.1f}%  "
          f"(n={int(df['c2_ok'].notna().sum()):,})")
    rs = df["ss_rsrp_dBre"].notna().sum() if "ss_rsrp_dBre" in df else 0
    print(f"rows with measured RSRP: {rs:,}")
    try:
        assert_terrain_blind(df.columns)
        print("\n!! forbidden columns NOT blocked — check the regex")
    except ValueError:
        print("\nforbidden-input guard: metadata still present (expected);")
    cols = feature_columns(df, ["geom", "radio", "action"], with_history=False)
    print(f"guard on a feature matrix: PASS ({len(cols)} cols, no forbidden)")
