# Final repeats: complete results (2026-11-14)

This report covers the 3-seed capped campaign with all 24 planned runs: 4 arms
× 3 seeds × 2 worlds. It supersedes the numbers in
`FINAL_REPEATS_INTERIM_REPORT.md` (17 runs, 13 Nov). The interim report is kept
unchanged as a record. Nothing in README or PROGRESS is changed by this report.

**How the numbers were made**
- Scripts: `scripts/ml/final_repeats_interim.py` (per run) and
  `scripts/ml/final_repeats_deep.py` (arm comparisons and mechanism).
- Outputs: `logs/ml/final_repeats_interim.csv` and
  `logs/ml/final_repeats_deep_*.csv`.
- Figure: `paper/figs/fig_arms_seeds.py`.

## Setup

- **Uplink cap:** adaptive per-robot cap on every run.
- **Learner:** cautious learner for arms C and D (home 40 m).
- **Mission:** 90-min research mission.
- **Arms:**
  - A: hover at 40 m;
  - B: centroid at 40 m;
  - C: centroid + bandit altitude;
  - D: model x–y + bandit altitude.
- **Seeds:** 1794000001–03. A seed does not reproduce robot trajectories, so
  arms are compared by a two-stage bootstrap: resample runs, then 10-min blocks
  within each run.
- **Decision rule:** fixed before the runs (`paper/CLAIMS.md`). A learned arm
  beats hover only if its 95 % interval excludes 0.
- **Equivalence:** the 90 % interval lies within ±5 points. This margin was
  set after seeds 1–2.

### Runs flown again

Failed attempts are excluded, never imputed.

| Arm, world, seed | What happened | Re-flown in |
|---|---|---|
| D, open field, seed 1 | Gazebo failed to start (0 rows) | `campaign_20261113_195636` |
| D, forest, seed 3 | Killed at about 66 min by the 13 Nov host memory crash | `campaign_20261113_165631` |
| D, forest, seed 3 (first re-flight) | Gazebo failed to start at 15:23 (0 rows) | `campaign_20261113_165631` |

Run lists:
- `logs/final_repeats_20261112_161427_runs.csv`
- `logs/final_repeats_rerun_20261113_152246_runs.csv`
- `logs/final_repeats_rerun_20261113_165631_runs.csv`
- `logs/final_repeats_rerun_20261113_195636_runs.csv`
- Forest seed 1: the 12 Nov capped and cautious fly-off runners.

## Per-run results

All-4-up is the time-weighted % of the mission with all four robots' C2 up
(loss < 50 %). The interval is a within-run 10-min block bootstrap.

| World | Arm | Seed | All-4-up % [95 % CI] | Mean C2 up % | None up % | Run dir |
|---|---|---|---|---|---|---|
| forest | A | 1 | 70.5 [60.7, 80.4] | 89.1 | 0.3 | `logs/campaign_20261112_050243` |
| forest | A | 2 | 73.2 [61.2, 83.5] | 92.4 | 0.0 | `logs/campaign_20261112_235522` |
| forest | A | 3 | 67.9 [55.4, 77.0] | 88.8 | 0.5 | `logs/campaign_20261113_104008` |
| forest | B | 1 | 67.0 [53.8, 77.1] | 88.9 | 0.0 | `logs/campaign_20261112_015702` |
| forest | B | 2 | 55.2 [44.9, 65.1] | 86.7 | 0.0 | `logs/campaign_20261112_204955` |
| forest | B | 3 | 64.2 [50.8, 74.3] | 90.3 | 0.0 | `logs/campaign_20261113_152330` |
| forest | C | 1 | 65.2 [56.9, 72.1] | 85.2 | 2.1 | `logs/campaign_20261112_102709` |
| forest | C | 2 | 72.9 [68.7, 76.9] | 92.4 | 0.0 | `logs/campaign_20261113_012811` |
| forest | C | 3 | 52.1 [39.6, 64.8] | 85.1 | 0.0 | `logs/campaign_20261113_090721` |
| forest | D | 1 | 72.0 [59.5, 83.8] | 89.7 | 0.3 | `logs/campaign_20261112_115951` |
| forest | D | 2 | 56.5 [43.6, 69.9] | 81.2 | 4.5 | `logs/campaign_20261112_222239` |
| forest | D | 3 | 59.0 [51.5, 67.3] | 86.8 | 0.0 | `logs/campaign_20261113_165631` |
| open field | A | 1 | 84.2 [81.4, 86.7] | 95.8 | 0.0 | `logs/campaign_20261112_161427` |
| open field | A | 2 | 85.5 [83.1, 87.7] | 96.0 | 0.0 | `logs/campaign_20261113_073549` |
| open field | A | 3 | 83.0 [80.5, 85.2] | 95.5 | 0.0 | `logs/campaign_20261113_225942` |
| open field | B | 1 | 85.3 [83.9, 86.9] | 96.2 | 0.0 | `logs/campaign_20261112_191735` |
| open field | B | 2 | 85.8 [84.2, 87.2] | 96.0 | 0.0 | `logs/campaign_20261113_043240` |
| open field | B | 3 | 85.2 [82.5, 87.8] | 96.1 | 0.0 | `logs/campaign_20261114_020251` |
| open field | C | 1 | 87.1 [84.0, 90.2] | 96.5 | 0.0 | `logs/campaign_20261112_174603` |
| open field | C | 2 | 82.5 [74.8, 88.6] | 94.2 | 0.0 | `logs/campaign_20261113_060411` |
| open field | C | 3 | 86.1 [83.5, 89.4] | 96.1 | 0.0 | `logs/campaign_20261113_212808` |
| open field | D | 1 | 79.3 [73.0, 83.5] | 93.7 | 0.0 | `logs/campaign_20261113_195636` |
| open field | D | 2 | 86.6 [83.9, 89.5] | 96.3 | 0.0 | `logs/campaign_20261113_030105` |
| open field | D | 3 | 84.8 [82.5, 87.1] | 95.6 | 0.0 | `logs/campaign_20261114_003121` |

## Arms against hover

Two-stage bootstrap (runs, then blocks); the intervals are 95 %.

| World | Arm | Mean all-4-up | Arm − hover [95 % CI] | Verdict |
|---|---|---|---|---|
| open field | A (hover) | 84.2 [82.4, 85.9] | – | – |
| open field | B | 85.4 | +1.2 [−0.9, +3.5] | equivalent within ±5 |
| open field | C | 85.2 | +1.0 [−3.4, +4.5] | equivalent within ±5 |
| open field | D | 83.6 | −0.7 [−5.6, +3.3] | equivalent within ±5 (90 % interval) |
| forest | A (hover) | 70.6 [63.3, 76.8] | – | – |
| forest | B | 62.1 | −8.4 [−19.4, +2.1] | not established |
| forest | C | 63.4 | −7.0 [−19.9, +4.7] | not established |
| forest | D | 62.5 | −8.0 [−18.6, +4.2] | not established |

**No learned arm beats hover in either world.** In open field, all arms are
equivalent to hover. In forest, every arm that moves the UAV trails hover by
7–8 points on average, but the gap is not established.

On the same seed, the moving arms' differences from hover were:

| World | Arm | Seed 1 | Seed 2 | Seed 3 |
|---|---|---|---|---|
| forest | B | −3.5 | −18.0 | −3.7 |
| forest | C | −5.3 | −0.3 | −15.8 |
| forest | D | +1.5 | −16.7 | −8.9 |
| open field | D | −4.9 | +1.1 | +1.8 |

The open-field D dip on seed 1 was not repeated on seeds 2 and 3.

## Mechanism (forest, mean over runs)

| Arm | Cap cuts / h | Video goodput Mb/s | Video latency p90 s | Outages / robot-h | Longest outage, mean s |
|---|---|---|---|---|---|
| A | 266 | 1.03 | 1.46 | 11.4 | 153 |
| B | 278 | 1.00 | 1.57 | 11.0 | 155 |
| C | 306 | 1.01 | 2.26 | 11.0 | 138 |
| D | 364 | 0.87 | 2.19 | 13.6 | 184 |

**Moving costs the links.** The planner (D) makes the cap work hardest and
delivers the least video.

**Altitude, arm C:** C spends 25 % of forest mission time at 45 m or higher. Its
worst run (52.1 %) spent 59 % of the mission there.

**Distance doesn't explain C2.** Over 48 forest robot-runs, horizontal
UAV–robot distance is uncorrelated with a robot's C2 up (r = 0.00). What
matters is the canopy between UAV and robot, which the terrain-blind features
cannot see.

## Against the uncapped baseline

The uncapped runs are from `logs/xyz_flyoff_20261110_172251_summary.csv`. They
are single runs, flown on a different night.

| World | Arm | Uncapped all-4-up | Capped all-4-up (3-seed mean) |
|---|---|---|---|
| open field | A | 50.2 | 84.2 |
| open field | B | 57.0 | 85.4 |
| forest | A | 58.4 | 70.6 |
| forest | B | 42.8 | 62.1 |

The cap does not starve strong open-field links.

## For the paper

The paper (C4 in `paper/sections/results.tex`) and `paper/CLAIMS.md` (D8 final)
are updated to these numbers. Claim 4 holds: no learned placement beats hover.
The cautious learner's safety part is not falsified, because no cautious arm
loses to hover with an interval excluding 0.
