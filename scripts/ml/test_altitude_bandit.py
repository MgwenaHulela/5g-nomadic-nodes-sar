"""Closed-loop checks for AltitudeBandit on synthetic worlds.

Recorded data cannot say what WOULD have happened at an altitude the UAV did
not fly, so these worlds stand in: four robots, each connected with a
probability that depends on altitude, slopes taken from the measured Phase 4
climb effects (logs/ml/phase4_climb_effect.csv). The loop mirrors the live
pilot: one decision per 30 s dwell, ~1.5 measurement cycles per dwell, a
10 m/decision rate limit, 90 minutes.

    python3 -m pytest -q scripts/ml/test_altitude_bandit.py
"""
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import functools                                           # noqa: E402

import pytest                                              # noqa: E402
from altitude_bandit import AltitudeBandit, AltitudeProber  # noqa: E402
from online_planner import CAUTIOUS                        # noqa: E402
from planner import Z_TARGETS, rate_limit                  # noqa: E402

DECISIONS = 180          # 90 min at 30 s
SEEDS = 20


def fly(p_of_z, seed, z0=40.0, cls=AltitudeBandit):
    rng = np.random.default_rng(seed)
    b = cls(Z_TARGETS)
    z, t, zs = z0, 0.0, []
    for _ in range(DECISIONS):
        target = b.select(t, z)
        z = float(np.clip(z + rate_limit(z, target), 20, 60))
        for _ in range(rng.choice([1, 2])):
            # robots wander: the whole world drifts a little each cycle
            p = np.clip(p_of_z(z) + rng.normal(0, 0.05), 0, 1)
            b.observe(t, z, rng.binomial(4, p) / 4.0)
            t += 20.0
        zs.append(z)
    return np.array(zs)


def settled(p_of_z, cls):
    return np.array([fly(p_of_z, s, cls=cls)[DECISIONS // 2:]
                     for s in range(SEEDS)])


# the planner's default: AltitudeProber homed at the fixed-hover altitude
Cautious = functools.partial(AltitudeProber, home_z=40.0, **CAUTIOUS)
LEARNERS = pytest.mark.parametrize("cls", [AltitudeBandit, AltitudeProber,
                                           Cautious],
                                   ids=["ucb", "prober", "cautious"])


def test_cautious_holds_home_when_flat():
    """No altitude effect at all: the cautious learner should stay at home
    (40 m) nearly all the time instead of wandering on noise."""
    z = settled(lambda z: 0.80 + 0 * z, Cautious)
    assert np.mean(np.abs(z - 40) <= 5) > 0.8, np.mean(np.abs(z - 40) <= 5)


@LEARNERS
def test_open_field_climbs(cls):
    z = settled(lambda z: 0.70 + 0.169 * (z - 40) / 10, cls)
    assert np.median(z.mean(1)) >= 50, z.mean(1)


@LEARNERS
def test_forest_does_not_pin_at_ceiling(cls):
    z = settled(lambda z: 0.45 - 0.015 * (z - 40) / 10, cls)
    assert np.mean(z >= 55) < 0.5, np.mean(z >= 55)


@LEARNERS
def test_interior_optimum_found(cls):
    z = settled(lambda z: 0.80 - 0.0015 * (z - 40) ** 2, cls)
    assert abs(np.median(z.mean(1)) - 40) <= 7, z.mean(1)


@LEARNERS
def test_descends_when_low_is_better(cls):
    z = settled(lambda z: 0.85 - 0.12 * (z - 25) / 10, cls)
    # The cautious learner trades speed for safety: leaving home needs 1.5 SE
    # and +0.02, so in 90 min it settles ~35 m here rather than ~30 m. It must
    # still be clearly below home; see docs/ALTITUDE_LEARNER_REPORT.md.
    limit = 36 if cls is Cautious else 33
    assert np.median(z.mean(1)) <= limit, z.mean(1)


@LEARNERS
def test_respects_grid_and_rate_limit(cls):
    b = cls(Z_TARGETS)
    for z in (20.0, 37.0, 60.0):
        tgt = b.select(0.0, z)
        assert Z_TARGETS[0] <= tgt <= Z_TARGETS[-1]
        assert abs(rate_limit(z, tgt)) <= 10.0 + 1e-9
