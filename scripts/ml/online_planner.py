#!/usr/bin/env python3
"""Live waypoint chooser for the Phase 5 fly-off.

`auto_patrol.py` already assembles a wide per-cycle row with every feature
the surrogate wants. This takes that row, keeps a few cycles of history, and
answers one question: **where should the UAV go next so the worst-connected
robot is as well connected as possible 20 s from now?**

The horizon is read from the surrogate's own metadata rather than written here,
so the two cannot drift apart: round 4 shortened it from 60 s to 20 s because
that is the only horizon at which the corrected offline protocol finds real
skill. Less lead time is a genuine constraint on what the planner can achieve.

Feature construction deliberately reuses `dataset.add_history` and
`planner.rebuild_geometry` rather than reimplementing lags online. A second
implementation of the lag logic would be a second thing to keep in step with
the offline results, and the first time the two drifted every fly-off number
would quietly stop being comparable.

MODEL SELECTION. `SAR_PLANNER_MODEL` picks the risk model; unset or
`single` is the round-4 single-head C2 surrogate, unchanged. `multitask`
loads the Phase 7 bundle (scripts/ml/multitask_risk.py) and plans on its
calibrated `union` head, risk_i = P(C2 down OR video unusable at t+20).
That head is learned on the observed union label rather than assembled as
1-(1-a)(1-b), because the two failures share one radio link and the
independence product overstates mission risk by two-thirds
(docs/MULTITASK_REPORT.md). Everything downstream of the risk -- candidate
grid, hold candidate, soft-min/beta, rate limit, the caller's geofence -- is
the same code path for both models.

In multitask mode every decision also runs the hard all-robot check: is
there any candidate that keeps EVERY robot under the head's calibrated
threshold? When there is none the soft-min still picks the least-bad
candidate (holding still is not safer), but the decision is counted in
`n_infeasible` and logged, so a run that spent its time trading one robot
away says so instead of looking like ordinary steering. The threshold does
not transfer well across terrains, so the flag is advisory.

`geo` loads the leave-one-terrain-out linear_geo surrogates
(logs/ml/surrogates_geo/, scripts/ml/train_geo_surrogates.py): the best
PREDICTOR found by scripts/ml/altitude_model_select.py.

ALTITUDE. `SAR_PLANNER_ALT` picks who decides the altitude. Unset or `model`:
the surrogate's own argmin over the grid, as in rounds 1-4. `bandit`: the
target is learned on the spot from measured connectivity (altitude_bandit.py)
and the surrogate only chooses the horizontal offset AT that altitude. No
surrogate tried learns where to stop climbing -- they rank the benefit of
climbing in the wrong order across terrains -- so in bandit mode they are
not asked to. `SAR_PLANNER_ALT_LEARNER` picks the learner: `cautious`
(default since 2026-11-12: AltitudeProber with home = SAR_PLANNER_ALT_FIXED_M;
leaving home needs 1.5 SE and +0.02, probes every 4th decision, every 20th once
neighbours look worse -- see docs/ALTITUDE_LEARNER_REPORT.md), `prober` (the
same learner without the home bias, flown in the 2026-11-11/12 fly-offs) or
`ucb` (the original AltitudeBandit, flown in the 2026-11-10 x-y-z fly-off).

HORIZONTAL. `SAR_PLANNER_XY`: `model` (default) lets the surrogate pick the
lateral offset; `centroid` steps toward the robots' centroid (the proposal's
centroid baseline); `hold` never moves laterally (the fixed-hover baseline).
`SAR_PLANNER_ALT=fixed` holds `SAR_PLANNER_ALT_FIXED_M` (default 40 m, the
UAV's start altitude). Running every fly-off arm through this one planner
keeps the rate limit, geofence, dwell and logging identical across arms, so
the arms differ ONLY in how x-y and z are chosen.

A misspelt or unloadable model raises in the constructor. The caller then
falls back to the excitation policy and says so; it never falls back to the
OTHER model, which would make a multitask run silently a single-head one.

Safety: `choose()` never raises. If anything at all goes wrong -- a short
history, a missing column, a model that will not load -- it returns None and
the caller falls back to its normal behaviour. A planner that crashes the
patrol driver mid-campaign would cost more than one that occasionally
declines to steer.
"""
from __future__ import annotations

import json
import os
import sys
from collections import deque
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import dataset as ds                                  # noqa: E402
from planner import (candidate_targets, rebuild_geometry,   # noqa: E402
                     rate_limit, soft_min_risk, Z_MIN, Z_MAX,
                     Z_TARGETS, MAX_DZ_STEP)

SURR = ds.REPO / "logs" / "ml" / "surrogates"
ROBOT_OF = {"a": "alpha", "b": "bravo", "c": "charlie", "d": "delta"}
KEEP_CYCLES = 6          # enough for lag3 + roll3 with room to spare
MODEL_KINDS = ("single", "multitask", "geo")
ALT_MODES = ("model", "bandit", "fixed")
ALT_LEARNERS = ("cautious", "prober", "ucb")
# Validated cautious settings (scripts/ml/altitude_offline_eval.py, fresh
# seeds 100-109; logs/ml/altitude_bench_cautious_validate_*.csv).
CAUTIOUS = dict(z_conf_away=1.5, min_gain=0.02, probe_every=4,
                probe_every_slow=20)
XY_MODES = ("model", "centroid", "hold")
XY_STEP_M = 15.0        # max lateral step per decision, = the candidate radius
SURR_GEO = ds.REPO / "logs" / "ml" / "surrogates_geo"


class OnlinePlanner:
    def __init__(self, world: str, beta: float = 10.0, log=print):
        terr = world.replace("sar_", "")
        self.model_kind = (os.environ.get("SAR_PLANNER_MODEL", "single")
                           .strip().lower() or "single")
        if self.model_kind not in MODEL_KINDS:
            raise ValueError(f"SAR_PLANNER_MODEL must be one of "
                             f"{MODEL_KINDS}, got {self.model_kind!r}")
        if self.model_kind == "multitask":
            self._load_multitask(terr)
        elif self.model_kind == "geo":
            self._load_geo(terr)
        else:
            self._load_single(terr)
        self.alt_mode = (os.environ.get("SAR_PLANNER_ALT", "model")
                         .strip().lower() or "model")
        if self.alt_mode not in ALT_MODES:
            raise ValueError(f"SAR_PLANNER_ALT must be one of {ALT_MODES}, "
                             f"got {self.alt_mode!r}")
        self.xy_mode = (os.environ.get("SAR_PLANNER_XY", "model")
                        .strip().lower() or "model")
        if self.xy_mode not in XY_MODES:
            raise ValueError(f"SAR_PLANNER_XY must be one of {XY_MODES}, "
                             f"got {self.xy_mode!r}")
        if self.xy_mode != "model" and self.alt_mode == "model":
            raise ValueError("SAR_PLANNER_XY=centroid|hold needs "
                             "SAR_PLANNER_ALT=bandit|fixed")
        self.z_fixed = float(os.environ.get("SAR_PLANNER_ALT_FIXED_M", 40.0))
        self.bandit = None
        self.alt_learner = None
        if self.alt_mode == "bandit":
            from altitude_bandit import AltitudeBandit, AltitudeProber
            self.alt_learner = (os.environ.get("SAR_PLANNER_ALT_LEARNER",
                                               "cautious").strip().lower()
                                or "cautious")
            if self.alt_learner not in ALT_LEARNERS:
                raise ValueError("SAR_PLANNER_ALT_LEARNER must be one of "
                                 f"{ALT_LEARNERS}, got {self.alt_learner!r}")
            if self.alt_learner == "cautious":
                self.bandit = AltitudeProber(Z_TARGETS, home_z=self.z_fixed,
                                             **CAUTIOUS)
            elif self.alt_learner == "prober":
                self.bandit = AltitudeProber(Z_TARGETS)
            else:
                self.bandit = AltitudeBandit(Z_TARGETS)
        self.n_decisions = 0
        self.n_infeasible = 0
        self.last_risk = None          # per-robot risk at the chosen candidate
        self.last_feasible = None
        self.offs = candidate_targets()
        self.beta = beta
        self.buf: deque[pd.DataFrame] = deque(maxlen=KEEP_CYCLES)
        self.n_seen = 0
        self.last_target = None
        self.log = log
        self.log(f"[planner] surrogate held out {terr}; "
                 f"{len(self.feats)} features, {len(self.offs)} candidates "
                 f"(absolute z targets {Z_TARGETS[0]:.0f}-{Z_TARGETS[-1]:.0f} m, "
                 f"rate limit {MAX_DZ_STEP:.0f} m/decision), beta={beta}")
        self.log(f"[planner] protocol {self.protocol}; "
                 f"horizon {self.horizon_s:.0f}s; "
                 f"calibration {self.calibration}")
        if self.model_kind == "multitask":
            self.log(f"[planner] model multitask: head {self.head}, "
                     f"all-robot threshold {self.thr:.3f} (advisory)")
        alt_txt = {"bandit": f"learned on the spot ({self.alt_learner} over "
                             f"{Z_TARGETS[0]:.0f}-{Z_TARGETS[-1]:.0f} m)",
                   "fixed": f"fixed {self.z_fixed:.0f} m",
                   "model": "surrogate argmin"}[self.alt_mode]
        self.log(f"[planner] altitude: {alt_txt}")
        self.log(f"[planner] xy: {self.xy_mode}")

    def _load_single(self, terr: str) -> None:
        import joblib
        meta = json.loads((SURR / "features.json").read_text())
        self.feats = meta["features"]
        self.hist_cols = meta["hist_cols"]
        self.horizon_s = float(meta.get("horizon_s", 20.0))
        self.protocol = meta.get("protocol", "unknown")
        bundle = joblib.load(SURR / f"surrogate_heldout_{terr}.joblib")
        self.model = bundle["model"]
        # Isotonic matters more here than offline. soft_min_risk compares risks
        # ACROSS robots, so a score that is monotone but skewed would protect
        # the wrong robot; the offline metrics are rank-based and would not
        # have noticed.
        self.iso = bundle.get("iso")
        if bundle.get("protocol") != self.protocol:
            raise RuntimeError(
                f"surrogate protocol {bundle.get('protocol')!r} does not match "
                f"features.json {self.protocol!r} -- retrain the surrogates")
        self.calibration = "isotonic" if self.iso is not None else "NONE"

    def _load_geo(self, terr: str) -> None:
        import joblib
        meta = json.loads((SURR_GEO / "features.json").read_text())
        b = joblib.load(SURR_GEO / f"surrogate_heldout_{terr}.joblib")
        if b.get("protocol") != meta.get("protocol"):
            raise RuntimeError(f"geo surrogate protocol {b.get('protocol')!r} "
                               f"!= features.json {meta.get('protocol')!r}")
        ds.assert_terrain_blind(b["features"])
        self.model, self.platt = b["model"], b["platt"]
        self.feats, self.hist_cols = b["features"], b["hist_cols"]
        self.horizon_s = float(b["horizon_s"])
        self.protocol = b["protocol"]
        self.calibration = "platt"
        self.iso = None

    def _load_multitask(self, terr: str) -> None:
        import multitask_risk as mr
        self._mr = mr
        self.bundle = mr.load_bundle(terr)     # schema, protocol, terrain-blind
        self.feats = self.bundle["features"]
        self.hist_cols = self.bundle["hist_cols"]
        self.horizon_s = float(self.bundle["horizon_s"])
        self.protocol = self.bundle["protocol"]
        self.head = self.bundle.get("planner_head", "union")
        self.thr = mr.threshold(self.bundle, self.head)
        self.calibration = "platt"

    def _risk(self, X: pd.DataFrame) -> np.ndarray:
        """Calibrated per-row failure probability at t+horizon."""
        if self.model_kind == "multitask":
            probs = self._mr.score_heads(self.bundle, X)
            return self._mr.mission_risk(probs, self.head)
        if self.model_kind == "geo":
            from multitask_risk import apply_calibration
            return apply_calibration(
                self.platt, self.model.predict_proba(X[self.feats])[:, 1])
        p = self.model.predict_proba(X[self.feats])[:, 1]
        if self.iso is not None:
            p = self.iso.predict(p)
        return p

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
            if self.bandit is not None:
                self._observe(w, d)
        except Exception as e:                         # never break the run
            self.log(f"[planner] ingest skipped: {type(e).__name__}: {e}")

    def _observe(self, w: dict, d: pd.DataFrame) -> None:
        """One bandit observation per completed cycle: the altitude flown and
        the fraction of robots with C2 up, leaving out any robot whose modem
        is being restarted (down whatever the altitude)."""
        ok = []
        for pfx in ROBOT_OF:
            ev = str(w.get(f"{pfx}_event", "") or "")
            loss = pd.to_numeric(w.get(f"{pfx}_c2_loss_pct"), errors="coerce")
            if ev == "reattaching" or not np.isfinite(loss):
                continue
            ok.append(float(loss < 50))
        z = pd.to_numeric(w.get("uav_z_m"), errors="coerce")
        t = pd.to_numeric(w.get("t_elapsed_s"), errors="coerce")
        if ok:
            self.bandit.observe(t, z, float(np.mean(ok)))

    # -- decide -----------------------------------------------------------
    def choose(self, pose=None):
        """(dx, dy, dz, predicted_gain) or None if it cannot decide yet.

        `pose` is the caller's LIVE (x, y, z). Pass it. The buffered CSV row
        describes where the UAV was when that measurement cycle closed, which
        is not where it is when the next decision is taken -- measured mean
        gap 23.5 m in altitude over a forest run. Evaluating candidates in
        one frame and applying the answer in another is harmless for a
        relative "climb 10 m" but inverts the sign of a move toward an
        ABSOLUTE target whenever the two disagree, which is what turned the
        round-2 forest run into an oscillation between the geofence bounds
        (57.6 % of commanded targets were clamped to 20 or 60 m, altitudes
        this planner's own 25-55 m grid cannot produce).

        So the live pose overrides the buffered UAV position before any
        candidate is built, and the rate limit is applied against it too.
        Robot positions necessarily remain as of the last cycle.

        `dz` is the rate-limited step toward the chosen target, so the
        caller's contract is unchanged.
        """
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
            if pose is not None:
                # One frame for the whole decision -- see the docstring.
                block["uav_x_m"] = float(pose[0])
                block["uav_y_m"] = float(pose[1])
                block["uav_z_m"] = float(pose[2])
            z_now = float(pose[2]) if pose is not None else float(
                block["uav_z_m"].iloc[0])
            offs = self.offs
            z_t = None
            if self.bandit is not None:
                # Altitude is the bandit's; the surrogate picks only the
                # horizontal offset at that altitude.
                z_t = self.bandit.select(float(last_t), z_now)
                if self.n_decisions % 10 == 0:
                    self.log(f"[planner] {self.alt_learner} -> {z_t:.0f} m  "
                             f"[{self.bandit.summary(float(last_t))}]")
            elif self.alt_mode == "fixed":
                z_t = self.z_fixed
            if self.xy_mode != "model":
                return self._baseline_xy(block, pose, z_now, z_t)
            if z_t is not None:
                offs = self.offs[np.isclose(self.offs[:, 2], z_t)]
            X = pd.concat([rebuild_geometry(block, *o) for o in offs],
                          ignore_index=True)
            for c in self.feats:
                if c not in X.columns:
                    X[c] = np.nan
            p = self._risk(X).reshape(len(offs), len(block))
            j = np.array([soft_min_risk(r, self.beta) for r in p])
            i_best = int(j.argmin())
            self.n_decisions += 1
            self.last_risk = p[i_best].copy()
            if self.model_kind == "multitask":
                _, any_ok = self._mr.feasibility(p, self.thr)
                self.last_feasible = any_ok
                if not any_ok:
                    self.n_infeasible += 1
                    robots = block["robot"].tolist()
                    worst = int(p[i_best].argmax())
                    self.log(f"[planner] INFEASIBLE: no candidate keeps all "
                             f"{len(robots)} robots under {self.thr:.3f}; "
                             f"least-bad leaves {robots[worst]} at "
                             f"{p[i_best][worst]:.3f} "
                             f"({self.n_infeasible}/{self.n_decisions})")
            i_hold = int(np.argmin(np.hypot(offs[:, 0], offs[:, 1])
                                   * 1e3
                                   + np.abs(offs[:, 2] - z_now)))
            dx, dy, z_star = offs[i_best]
            self.last_target = float(z_star)
            return (float(dx), float(dy), rate_limit(z_now, float(z_star)),
                    float(j[i_hold] - j[i_best]))
        except Exception as e:
            self.log(f"[planner] choose failed: {type(e).__name__}: {e}")
            return None

    def _baseline_xy(self, block, pose, z_now, z_t):
        """Centroid / hold lateral move at altitude z_t. The surrogate is
        still evaluated at the chosen point so every arm logs a comparable
        per-robot risk, but it does not influence the move."""
        ux, uy = float(block["uav_x_m"].iloc[0]), float(block["uav_y_m"].iloc[0])
        dx = dy = 0.0
        if self.xy_mode == "centroid":
            rx = block["robot_x_m"].astype(float)
            ry = block["robot_y_m"].astype(float)
            ok = rx.notna() & ry.notna()
            if ok.any():
                vx, vy = rx[ok].mean() - ux, ry[ok].mean() - uy
                dist = float(np.hypot(vx, vy))
                k = min(1.0, XY_STEP_M / dist) if dist > 0 else 0.0
                dx, dy = vx * k, vy * k
        X = rebuild_geometry(block, dx, dy, z_t)
        for c in self.feats:
            if c not in X.columns:
                X[c] = np.nan
        self.last_risk = np.asarray(self._risk(X), float)
        self.n_decisions += 1
        self.last_target = float(z_t)
        return (float(dx), float(dy), rate_limit(z_now, float(z_t)), 0.0)
