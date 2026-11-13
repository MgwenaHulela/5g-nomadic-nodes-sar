#!/usr/bin/env python3
"""Offline checks on the Phase 7 multi-head bundle and its scoring module.

Fast by design: no dataset build, synthetic feature rows taken from each
bundle's own support box. Skips cleanly if the bundles have not been trained.

    python3 -m pytest -q scripts/ml/test_multitask.py
"""
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import dataset as ds                                        # noqa: E402
import multitask_risk as mr                                  # noqa: E402

TERRAINS = ["forest", "hills", "open_field", "urban"]
pytestmark = pytest.mark.skipif(
    not (mr.BUNDLE_DIR / "features.json").exists(),
    reason="multitask bundles not trained (run multitask_model.py)")


def _mid_rows(b, n=8, z=None):
    lo = pd.Series(b["support"]["lo"])[b["features"]]
    hi = pd.Series(b["support"]["hi"])[b["features"]]
    X = pd.DataFrame([((lo + hi) / 2).values] * n, columns=b["features"])
    if z is not None:
        X["uav_z_m"] = z
    return X


@pytest.mark.parametrize("terr", TERRAINS)
def test_bundle_schema_and_blindness(terr):
    b = mr.load_bundle(terr)
    assert b["schema_version"] == 2
    assert b["held_out"] == terr
    assert b["planner_head"] == "union"
    assert set(b["heads"]) == {"c2", "video", "union"}
    ds.assert_terrain_blind(b["features"])
    assert not any(ds.FORBIDDEN.search(f) for f in b["features"])


def test_tcp_head_is_architected_not_trained():
    meta = json.loads((mr.BUNDLE_DIR / "features.json").read_text())
    assert meta["tcp_head"]["status"] == "architected_untrained"
    b = mr.load_bundle("forest")
    assert "tcp" not in b["heads"]


def test_c2_population_matches_deployed_surrogate():
    meta = json.loads((mr.BUNDLE_DIR / "features.json").read_text())
    single = json.loads((ds.REPO / "logs/ml/surrogates/features.json")
                        .read_text())
    assert meta["onset_rows"]["c2"] == single["onset_rows"]
    assert meta["features"] == single["features"]


@pytest.mark.parametrize("terr", TERRAINS)
def test_scores_are_probabilities(terr):
    b = mr.load_bundle(terr)
    p = mr.score_heads(b, _mid_rows(b))
    for h in ("c2", "video", "union"):
        assert p[h].shape == (8,)
        assert np.all((p[h] >= 0) & (p[h] <= 1))


def test_missing_columns_are_tolerated():
    b = mr.load_bundle("open_field")
    X = _mid_rows(b).drop(columns=b["features"][:5])
    p = mr.score_heads(b, X)
    assert np.all(np.isfinite(p["union"]))


def test_combiners():
    a = {"c2": np.array([0.2, 0.5]), "video": np.array([0.1, 0.6]),
         "union": np.array([0.25, 0.65])}
    assert np.allclose(mr.mission_risk(a), a["union"])
    assert np.allclose(mr.mission_risk(a, "max"), [0.2, 0.6])
    assert np.allclose(mr.mission_risk(a, "product"), [0.28, 0.8])
    with pytest.raises(ValueError):
        mr.mission_risk(a, "mean")


def test_feasibility_requires_every_robot():
    risk = np.array([[0.1, 0.1, 0.1, 0.9],      # one robot over -> infeasible
                     [0.2, 0.3, 0.2, 0.2],      # all under -> feasible
                     [0.9, 0.9, 0.9, 0.9]])
    ok, any_ok = mr.feasibility(risk, 0.5)
    assert ok.tolist() == [False, True, False] and any_ok
    ok, any_ok = mr.feasibility(risk[[0, 2]], 0.5)
    assert not any_ok


def test_support_flags_extrapolation():
    b = mr.load_bundle("forest")
    inside = _mid_rows(b, n=1)
    outside = _mid_rows(b, n=1)
    outside["d_slant_m"] = 1e6
    assert not mr.out_of_support(b, inside)[0]
    assert mr.out_of_support(b, outside)[0]


def test_bad_schema_is_refused(tmp_path):
    meta = json.loads((mr.BUNDLE_DIR / "features.json").read_text())
    meta["schema_version"] = 1
    (tmp_path / "features.json").write_text(json.dumps(meta))
    import shutil
    shutil.copy(mr.BUNDLE_DIR / "multitask_heldout_forest.joblib", tmp_path)
    with pytest.raises(RuntimeError):
        mr.load_bundle("forest", root=tmp_path)
