"""Clean (capped, watchdog-protected) campaign data for retraining.

Every campaign before 2026-11-12 was flown without the uplink cap, so forest
C2-down labels there are partly UE queue backlog (docs/CONGESTION_TEST_REPORT.md).
This loader reads ONLY runs flown with SAR_UL_CAP=adaptive, taken from the
runner lists (never by globbing), and converts them with dataset._wide_to_long
so columns, forbidden-input stripping and the c2_ok label are identical to the
original data layer.

Sources (rc == 0 only):
  logs/capped_forest_flyoff_20261112_002402_runs.csv     forest A B C D (prober)
  logs/cautious_forest_flyoff_20261112_102709_runs.csv   forest C D (cautious)
  logs/final_repeats_*_runs.csv                          forest + open field, 3 seeds
  logs/campaign_20261111_234332                          30-min forest patrol, arm A, cap v2

    from clean_dataset import build_clean
    df = build_clean()          # long format, one row per (run, cycle, robot)
"""
from __future__ import annotations

import glob
from pathlib import Path

import pandas as pd

import dataset as ds

REPO = ds.REPO
LISTS = [("capped_flyoff", "logs/capped_forest_flyoff_20261112_002402_runs.csv", 6),
         ("cautious_flyoff", "logs/cautious_forest_flyoff_20261112_102709_runs.csv", 6)]
# patrol run: the cap ran from the test harness, its log is in the test folder
EXTRA = [("patrol_step3", "logs/campaign_20261111_234332", "sar_forest",
          "logs/congestion_test_patrol_20261112/cap_log.csv")]
CAP_LOG = {d: c for _, d, _, c in EXTRA}


def clean_runs() -> pd.DataFrame:
    rows = []
    for src, f, ncol in LISTS:
        r = pd.read_csv(REPO / f, header=None,
                        names=["i", "arm", "world", "dir", "rc", "rows"])
        r["source"] = src
        rows.append(r)
    for f in sorted(glob.glob(str(REPO / "logs/final_repeats_*_runs.csv"))):
        r = pd.read_csv(f, header=None, names=["i", "arm", "world", "seed", "dir", "rc", "rows"])
        r["source"] = "final_repeats"
        rows.append(r.drop(columns="seed"))
    for src, d, w, _ in EXTRA:
        rows.append(pd.DataFrame([dict(i=0, arm="A", world=w, dir=d, rc=0, rows=0, source=src)]))
    runs = pd.concat(rows, ignore_index=True)
    return runs[runs.rc == 0].reset_index(drop=True)


def build_clean() -> pd.DataFrame:
    frames = []
    for r in clean_runs().itertuples():
        csv = REPO / r.dir / f"{r.world}.csv"
        if not csv.exists():
            continue
        wide = pd.read_csv(csv, low_memory=False)
        if len(wide) < 50:
            continue
        cap = REPO / CAP_LOG.get(r.dir, f"{r.dir}/{r.world}_ulcap.csv")
        if not cap.exists():              # never admit an uncapped run
            raise RuntimeError(f"{r.dir}: no {r.world}_ulcap.csv -- not a capped run")
        meta = ds.RunMeta(campaign=Path(r.dir).name,
                          terrain=str(wide["terrain"].iloc[0]).replace("sar_", ""),
                          policy=str(wide["policy"].iloc[0]), seed=str(wide["seed"].iloc[0]),
                          run_id=str(wide["run_id"].iloc[0]), cycle_csv=csv,
                          fast_csv=None, action_csv=None)
        long = ds._wide_to_long(wide, meta)
        long["arm"] = r.arm
        long["source"] = r.source
        frames.append(long)
    df = pd.concat(frames, ignore_index=True)
    return df.sort_values(["run_id", "robot", "t_elapsed_s"], kind="stable").reset_index(drop=True)


if __name__ == "__main__":
    df = build_clean()
    print(f"{df.run_id.nunique()} runs, {len(df):,} robot-cycles")
    print(df.groupby(["terrain", "seed"]).run_id.nunique().to_string())
    print("C2-down share:", round(1 - df.c2_ok.mean(), 3))
