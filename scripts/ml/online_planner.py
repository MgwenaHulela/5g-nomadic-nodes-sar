#!/usr/bin/env python3
"""Live waypoint chooser for the Phase 5 fly-off.

`auto_patrol.py` already assembles a wide per-cycle row with every feature
the surrogate wants. This takes that row, keeps a few cycles of history, and
answers one question: **where should the UAV go next so the worst-connected
robot is as well connected as possible 60 s from now?**

Feature construction deliberately reuses `dataset.add_history` and
`planner.rebuild_geometry` rather than reimplementing lags online. A second
implementation of the lag logic would be a second thing to keep in step with
the offline results, and the first time the two drifted every fly-off number
would quietly stop being comparable.

Safety: `choose()` never raises. If anything at all goes wrong -- a short
history, a missing column, a model that will not load -- it returns None and
the caller falls back to its normal behaviour. A planner that crashes the
patrol driver mid-campaign would cost more than one that occasionally
declines to steer.
"""
from __future__ import annotations

import json
import sys
from collections import deque
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import dataset as ds                                  # noqa: E402
from planner import (candidate_offsets, rebuild_geometry,   # noqa: E402
                     soft_min_risk, Z_MIN, Z_MAX)

SURR = ds.REPO / "logs" / "ml" / "surrogates"
ROBOT_OF = {"a": "alpha", "b": "bravo", "c": "charlie", "d": "delta"}
KEEP_CYCLES = 6          # enough for lag3 + roll3 with room to spare


class OnlinePlanner:
    def __init__(self, world: str, beta: float = 10.0, log=print):
        import lightgbm as lgb
        terr = world.replace("sar_", "")
        meta = json.loads((SURR / "features.json").read_text())
        self.feats = meta["features"]
        self.hist_cols = meta["hist_cols"]
        self.model = lgb.Booster(
            model_file=str(SURR / f"surrogate_heldout_{terr}.txt"))
        self.offs = candidate_offsets()
        self.beta = beta
        self.buf: deque[pd.DataFrame] = deque(maxlen=KEEP_CYCLES)
        self.n_seen = 0
        self.log = log
        self.log(f"[planner] surrogate held out {terr}; "
                 f"{len(self.feats)} features, {len(self.offs)} candidates, "
                 f"beta={beta}")

    # -- ingest -----------------------------------------------------------
    def ingest(self, header: list[str], row: list) -> None:
        """Take one research-CSV row and append it to the history buffer."""
        try:
            w = dict(zip(header, row))
            frames = []
            for pfx, robot in ROBOT_OF.items():
                rec = {}
                for k, v in w.items():
                    if k.startswith(f"{pfx}_"):
                        stem = k[len(pfx) + 1:]
                        if ds.FORBIDDEN.search(stem):
                            continue          # never even buffered
                        rec[stem] = pd.to_numeric(v, errors="coerce")
                if not rec:
                    continue
                for g in ("t_elapsed_s", "uav_x_m", "uav_y_m", "uav_z_m",
                          "phase", "action_tx_m", "action_ty_m",
                          "action_tz_m"):
                    rec[g] = pd.to_numeric(w.get(g), errors="coerce")
                rec["robot_x_m"] = pd.to_numeric(w.get(f"{pfx}x_m"),
                                                 errors="coerce")
                rec["robot_y_m"] = pd.to_numeric(w.get(f"{pfx}y_m"),
                                                 errors="coerce")
                rec["robot"] = robot
                rec["run_id"] = w.get("run_id", "live")
                frames.append(rec)
            d = pd.DataFrame(frames)
            for ax in "xyz":
                d[f"action_d{ax}_m"] = (d.get(f"action_t{ax}_m")
                                        - d.get(f"uav_{ax}_m"))
            d["c2_ok"] = (d["c2_loss_pct"] < 50).astype(float)
            self.buf.append(d)
            self.n_seen += 1
        except Exception as e:                         # never break the run
            self.log(f"[planner] ingest skipped: {type(e).__name__}: {e}")

    # -- decide -----------------------------------------------------------
    def choose(self):
        """(dx, dy, dz, predicted_gain) or None if it cannot decide yet."""
        try:
            if len(self.buf) < 4:
                return None
            d = pd.concat(list(self.buf), ignore_index=True)
            d = ds.add_history(d, self.hist_cols, lags=(1, 2, 3))
            last_t = d["t_elapsed_s"].max()
            block = d[d["t_elapsed_s"] == last_t].copy()
            if len(block) < 2 or block[["uav_x_m", "uav_y_m",
                                        "uav_z_m"]].isna().any().any():
                return None
            X = pd.concat([rebuild_geometry(block, *o) for o in self.offs],
                          ignore_index=True)
            for c in self.feats:
                if c not in X.columns:
                    X[c] = np.nan
            p = self.model.predict(X[self.feats]).reshape(len(self.offs),
                                                          len(block))
            j = np.array([soft_min_risk(r, self.beta) for r in p])
            i_best = int(j.argmin())
            i_hold = int(np.argmin(np.abs(self.offs).sum(axis=1)))
            dx, dy, dz = self.offs[i_best]
            return float(dx), float(dy), float(dz), float(j[i_hold]
                                                          - j[i_best])
        except Exception as e:
            self.log(f"[planner] choose failed: {type(e).__name__}: {e}")
            return None
