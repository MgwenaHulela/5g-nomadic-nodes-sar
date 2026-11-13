"""The `linear_geo` surrogate: L1 logistic with a non-linear geometry block.

Lives in its own module so a pickled model can be loaded by any process that
has scripts/ml on sys.path -- a pipeline whose FunctionTransformer points at a
function defined in `__main__` only unpickles inside the script that made it.

Selected by scripts/ml/altitude_model_select.py (2026-11-10) as the best
PREDICTOR of the candidates tried: pooled ROC 0.659 / PR-AUC 0.135 / worst
fold 0.566, against 0.647 / 0.126 / 0.538 for the round-4 linear model. It is
NOT an altitude chooser -- no surrogate tried learned where to stop climbing
(see altitude_bandit.py) -- so the planner uses it for robot risk and
horizontal placement only.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import (FunctionTransformer, SplineTransformer,
                                   StandardScaler)

GEO = ["uav_z_m", "d_slant_m", "d_horiz_m", "elev_deg"]


def add_elev(X: pd.DataFrame) -> pd.DataFrame:
    """Elevation angle robot->UAV, the variable A2G LoS probability depends
    on. Computed inside the model so candidate rows need nothing new."""
    X = X.copy()
    X["elev_deg"] = np.degrees(np.arctan2(X["uav_z_m"],
                                          X["d_horiz_m"].clip(lower=0.1)))
    return X


def make_linear_geo(feats):
    rest = [f for f in feats if f not in GEO]
    ct = ColumnTransformer(
        [("geo", make_pipeline(SimpleImputer(strategy="median"),
                               SplineTransformer(n_knots=5, degree=3,
                                                 extrapolation="constant")),
          GEO),
         ("rest", SimpleImputer(strategy="median"), rest)])
    return make_pipeline(
        FunctionTransformer(add_elev), ct, StandardScaler(),
        LogisticRegression(max_iter=4000, C=0.1, penalty="l1",
                           solver="liblinear"))
