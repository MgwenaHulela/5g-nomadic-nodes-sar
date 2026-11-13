#!/usr/bin/env python3
"""Planner-side scoring for the Phase 7 multi-head link-risk bundle.

Side-effect free: load a bundle, score its heads, turn them into one per-robot
mission risk, and answer two questions the planner needs answered honestly --
is any candidate feasible for EVERY robot, and is the model being asked about
geometry it never saw in training.

THE PER-ROBOT RISK is the calibrated `union` head:

    risk_i = P(C2 down OR video unusable at t+20 | robot i, candidate u)

learned directly on the observed union label rather than assembled from the
c2 and video heads. The two failures share one radio link, so they are far
from independent: on the training rows the independence product
1-(1-a)(1-b) predicts 0.466 mission failure where 0.302 is observed. `max`
and `product` remain available for ablation, never as the default.

CALIBRATION is Platt scaling (a two-parameter sigmoid on the raw logit),
not isotonic. Measured on held-out terrains for the union head: isotonic cut
open-field ROC from 0.750 to 0.698 and emitted only 28-54 distinct risk values
per terrain, so many candidates tied and the soft-min lost resolution. Platt
is strictly monotone -- ranking is exactly the raw score's -- and continuous.
Neither calibrator transfers well to an unseen terrain (Brier skill stays near
zero on hills and urban), so treat these as rankings first, and the
feasibility threshold as advisory.

`predict -> plan -> act` stays separated: nothing here picks a waypoint. The
planner still owns the candidate grid, soft-min, rate limit and geofence.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import joblib
import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import dataset as ds                                   # noqa: E402

BUNDLE_DIR = ds.REPO / "logs" / "ml" / "surrogates_multitask"
SCHEMA_VERSION = 2
COMBINERS = ("union", "max", "product")


def _logit(p: np.ndarray) -> np.ndarray:
    p = np.clip(np.asarray(p, float), 1e-6, 1 - 1e-6)
    return np.log(p / (1 - p)).reshape(-1, 1)


def fit_platt(p_raw: np.ndarray, y: np.ndarray) -> dict:
    """Platt scaling, stored as plain sklearn so any module can unpickle it."""
    from sklearn.linear_model import LogisticRegression
    lr = LogisticRegression(C=1e6, max_iter=1000).fit(_logit(p_raw), y)
    return {"kind": "platt", "model": lr}


def apply_calibration(cal: dict | None, p_raw: np.ndarray) -> np.ndarray:
    if cal is None:
        return np.asarray(p_raw, float)
    if cal.get("kind") == "platt":
        return cal["model"].predict_proba(_logit(p_raw))[:, 1]
    raise ValueError(f"unknown calibrator {cal.get('kind')!r}")


def load_bundle(terrain: str, root: Path = BUNDLE_DIR) -> dict:
    """The bundle that held `terrain` out (LOTO), with its schema checked."""
    meta = json.loads((root / "features.json").read_text())
    b = joblib.load(root / f"multitask_heldout_{terrain}.joblib")
    if b.get("schema_version") != SCHEMA_VERSION or \
            meta.get("schema_version") != SCHEMA_VERSION:
        raise RuntimeError(f"multitask bundle schema {b.get('schema_version')}"
                           f"/{meta.get('schema_version')}, expected "
                           f"{SCHEMA_VERSION} -- rerun multitask_model.py")
    if b.get("protocol") != meta.get("protocol"):
        raise RuntimeError(f"bundle protocol {b.get('protocol')!r} != "
                           f"features.json {meta.get('protocol')!r}")
    ds.assert_terrain_blind(b["features"])
    return b


def score_heads(bundle: dict, X: pd.DataFrame) -> dict[str, np.ndarray]:
    """Calibrated probability of FAILURE at t+horizon, per trained head.

    The TCP head is architected but untrained and is never scored."""
    Xf = X.reindex(columns=bundle["features"])
    Z = bundle["trunk"].transform(Xf)
    out = {}
    for h, spec in bundle["heads"].items():
        p = apply_calibration(spec.get("calibrator"),
                              spec["model"].predict_proba(Z)[:, 1])
        out[h] = np.clip(p, 0.0, 1.0)
    return out


def mission_risk(probs: dict[str, np.ndarray],
                 combiner: str = "union") -> np.ndarray:
    """One per-robot risk from the head probabilities (see module doc)."""
    if combiner == "union":
        return probs["union"]
    if combiner == "max":
        return np.fmax(probs["c2"], probs["video"])
    if combiner == "product":
        return 1.0 - (1.0 - probs["c2"]) * (1.0 - probs["video"])
    raise ValueError(f"combiner must be one of {COMBINERS}, got {combiner!r}")


def threshold(bundle: dict, head: str | None = None) -> float:
    """Calibrated decision threshold for a head, fitted on the inner by-time
    split of the training terrains (F1-optimal there)."""
    return float(bundle["heads"][head or bundle["planner_head"]]["threshold"])


def feasibility(risk: np.ndarray, thr: float):
    """risk is (n_candidates, n_robots). Returns (per-candidate bool, any).

    A candidate is feasible only if EVERY robot is below threshold. When no
    candidate is, the caller must record that rather than let the soft-min
    quietly trade one robot away."""
    ok = np.all(np.asarray(risk) < thr, axis=1)
    return ok, bool(ok.any())


def out_of_support(bundle: dict, X: pd.DataFrame) -> np.ndarray:
    """True per row when any finite feature lies outside the training box
    (0.5-99.5 percentiles of the training terrains)."""
    f = bundle["features"]
    lo = pd.Series(bundle["support"]["lo"])[f].values
    hi = pd.Series(bundle["support"]["hi"])[f].values
    v = X.reindex(columns=f).values.astype(float)
    return (np.isfinite(v) & ((v < lo) | (v > hi))).any(axis=1)
