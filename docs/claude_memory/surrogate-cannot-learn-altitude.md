---
name: surrogate-cannot-learn-altitude
description: 2026-11-10 — no terrain-blind surrogate learns where to stop climbing; round-4 linear model pins 55 m; altitude now learned on the spot (bandit), surrogate only does x-y
metadata:
  type: project
---

`scripts/ml/altitude_model_select.py` (LOTO, onset/censored h20) replayed 4 surrogates
through the planner on each held-out terrain. None ranks the climb benefit in the measured
order (Spearman -0.2 to -0.8 vs Phase 4: open +0.169, urban +0.030, hills +0.012,
forest -0.015 per 10 m). Linear/linear_geo/linear_int pin at 55 m ~100 % everywhere;
LightGBM picks ~47 m in BOTH forest and open field. Round 3's "forest holds 40.9 m" came
from that LightGBM tendency, not terrain understanding — don't claim it as such.
Forest is ~98 % outside the training box (radio domain shift).

Best PREDICTOR: linear_geo (pooled ROC 0.659, worst 0.566) → `SAR_PLANNER_MODEL=geo`,
bundles in logs/ml/surrogates_geo/ (train_geo_surrogates.py).
Altitude now: `SAR_PLANNER_ALT=bandit` (altitude_bandit.py, kernel-smoothed discounted UCB
on fraction of robots with C2 up). Synthetic tests pass; offline replay can't evaluate it
(recorded altitude isn't the bandit's) — only live flights can.

x-y: the surrogate's lateral moves go toward the robot centroid 70-89 % of the time but
toward the highest-risk robot only ~50-63 % (coin-flip in open field) — effectively a
centroid follower. User's goal is full x-y-z placement to keep ALL robots connected.

Related: [[msc-proposal-status]], [[nomadic-gnb-terrain-results]].
