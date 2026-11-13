# Final repeats: interim report (2026-11-13)

The 3-seed campaign (`run_final_repeats.sh`) was stopped at 13:20 on 13 Nov
when the host ran out of memory, after 14 of 20 runs. This report gives the
13 usable runs. Nothing in README or PROGRESS is changed by it.

**Summary.**
- With the adaptive uplink cap, every open-field arm keeps all four robots up
  82–87 % of the time. Like-for-like uncapped runs of the same arms got 50–57 %.
- In forest, hover (arm A) is the steadiest arm (68–73 % on every seed).
- No arm that moves the UAV beat hover on average. Each moving arm lost
  16–18 points to hover on one seed.
- With 2–3 runs per arm, none of the forest differences is established.

## Setup

- 90-min missions. `SAR_UL_CAP=adaptive`, `SAR_PLANNER_ALT_LEARNER=cautious`.
- Arms: A hover 40 m; B centroid 40 m; C centroid + cautious altitude learner;
  D model x-y + cautious altitude learner.
- Forest seed 1 is taken from the capped (00:24) and cautious (10:27) runners
  of 12 Nov, flown with the same settings.
- Metric: time-weighted, C2 up = probe loss < 50 %. The range is a 95 % block
  bootstrap over 10-min blocks *within* the run.
- **Caveat:** the same seed does **not** reproduce robot trajectories (see
  `docs/MULTITASK_REPORT.md`). "Paired by seed" means same seed and night
  block, not matched paths.
- Analysis: `scripts/ml/final_repeats_interim.py` →
  `logs/ml/final_repeats_interim.csv`.

## Per-run results

| Run | World | Arm | Seed | Mean C2 % | All 4 up % (95 % range) | None up % | Directory |
|---|---|---|---|---|---|---|---|
| 1 | open | A | 1 | 95.8 | 84.2 (81–87) | 0.0 | campaign_20261112_161427 |
| 12 | open | A | 2 | 96.0 | 85.5 (83–88) | 0.0 | campaign_20261113_073549 |
| 3 | open | B | 1 | 96.2 | 85.3 (84–87) | 0.0 | campaign_20261112_191735 |
| 10 | open | B | 2 | 96.0 | 85.8 (84–87) | 0.0 | campaign_20261113_043240 |
| 2 | open | C | 1 | 96.5 | 87.1 (84–90) | 0.0 | campaign_20261112_174603 |
| 11 | open | C | 2 | 94.2 | 82.5 (75–89) | 0.0 | campaign_20261113_060411 |
| 9 | open | D | 2 | 96.3 | 86.6 (84–90) | 0.0 | campaign_20261113_030105 |
| (12 Nov) | forest | A | 1 | 89.1 | 70.5 (61–80) | 0.3 | campaign_20261112_050243 |
| 7 | forest | A | 2 | 92.4 | 73.2 (61–84) | 0.0 | campaign_20261112_235522 |
| 14 | forest | A | 3 | 88.8 | 67.9 (55–77) | 0.5 | campaign_20261113_104008 |
| (12 Nov) | forest | B | 1 | 88.9 | 67.0 (53–78) | 0.0 | campaign_20261112_015702 |
| 5 | forest | B | 2 | 86.7 | 55.2 (45–65) | 0.0 | campaign_20261112_204955 |
| (12 Nov) | forest | C | 1 | 85.2 | 65.2 (57–73) | 2.1 | campaign_20261112_102709 |
| 8 | forest | C | 2 | 92.4 | 72.9 (69–77) | 0.0 | campaign_20261113_012811 |
| 13 | forest | C | 3 | 85.1 | 52.1 (40–65) | 0.0 | campaign_20261113_090721 |
| (12 Nov) | forest | D | 1 | 89.7 | 72.0 (60–83) | 0.3 | campaign_20261112_115951 |
| 6 | forest | D | 2 | 81.2 | 56.5 (44–70) | 4.5 | campaign_20261112_222239 |

The watchdog logged 0 failed reattaches and 0 aborts in every run of this campaign.

## Per-arm summary

| World | Arm | Runs | All 4 up, mean (min–max) | Arm − hover, by seed |
|---|---|---|---|---|
| open | A hover | 2 | 84.8 (84.2–85.5) | — |
| open | B centroid | 2 | 85.6 (85.3–85.8) | +1.1, +0.3 |
| open | C cautious | 2 | 84.8 (82.5–87.1) | +2.9, −3.0 |
| open | D planner | 1 | 86.6 | +1.1 |
| forest | A hover | 3 | **70.5** (67.9–73.2) | — |
| forest | B centroid | 2 | 61.1 (55.2–67.0) | −3.5, −18.0 |
| forest | C cautious | 3 | 63.4 (52.1–72.9) | −5.3, −0.3, −15.8 |
| forest | D planner | 2 | 64.2 (56.5–72.0) | +1.5, −16.7 |

## Against the uncapped baseline

These are like-for-like arms (A and B do not learn), but from a different
night. Uncapped numbers are from `logs/xyz_flyoff_20261110_172251_summary.csv`
(seed 1, 10 Nov).

| World | Arm | Uncapped all 4 up % | Capped all 4 up % (by seed) |
|---|---|---|---|
| open | A | 50.2 (39–61) | 84.2, 85.5 |
| open | B | 57.0 (50–65) | 85.3, 85.8 |
| forest | A | 58.4 (43–75) | 70.5, 73.2, 67.9 |
| forest | B | 42.8 (23–64) | 67.0, 55.2 |

The cap comparison is across nights. Its size in open field (about +30
points, with non-overlapping within-run ranges) is far above the open-field
same-policy null swing measured earlier (−0.5, 95 % range −4.3 to +3.0;
`logs/ml/planner_vs_twophase_nullctl.csv`). In forest the null range is wide
(−17.6 to +6.4), so the forest cap gain needs the seeds, not one run.

## What it means for the paper claims

| Claim | Status from these runs |
|---|---|
| C1: the cap removes the queue outages and lifts like-for-like arms | Supported, strongly in open field |
| C4: learned placement does not beat hover or centroid | Supported: no moving arm beats hover on average in either world |
| "Moving the UAV in forest adds risk" | Suggestive only: each moving arm has one large loss, n = 2–3 |

## What is missing

| Run | World | Arm | Seed | Why |
|---|---|---|---|---|
| 4 | open | D | 1 | Gazebo failed at start-up (0 rows), flagged NOT COMPARABLE |
| 15 | forest | D | 3 | Killed at about 66 min by host memory pressure; no cycle CSV written |
| 16 | forest | B | 3 | Not flown |
| 17–20 | open | A, D, C, B | 3 | Not flown |

`run_final_repeats_rerun.sh` re-flies any list of these, for example:
`./run_final_repeats_rerun.sh "sar_forest 1794000003 D" "sar_forest 1794000003 B" ...`

Before restarting, free host memory: at the crash only about 0.9 GB of 32 GB
was available, with browsers holding 6+ GB and `open5gs-amfd` 1.4 GB after 27 days of uptime.
