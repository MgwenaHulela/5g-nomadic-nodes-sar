# Altitude learner: diagnosis and fix (2026-11-11)

## What went wrong in the x-y-z fly-off

The `AltitudeBandit` (UCB over the raw mean connectivity seen at each
altitude) settled at 55 m in one open-field run and 35 m in the other. Re-
estimating each learned-altitude run with time-blocked fixed effects (only
comparisons made within the same 4-min window count) shows why:

| Run | Raw mean, 55 m vs 40 m | Drift-cancelled, 55 m vs 40 m |
|---|---|---|
| open C (run 5) | 0.87 vs 0.80 | +0.11 ± 0.10 |
| open D (run 7) | 0.73 vs 0.79 | +0.01 ± 0.14 |
| forest C (run 2) | 0.35 vs 0.68 | +0.02 ± 0.15 |
| forest D (run 4) | 0.54 vs 0.65 | −0.15 ± 0.15 |

The raw means blamed altitude for whatever else was happening while the UAV
sat there (robots wandering into shadow; minutes-long cell-wide outages in
forest). Drift-cancelled, no altitude effect in these four runs is
distinguishable from zero in 90 minutes.

## Offline bench

`scripts/ml/altitude_offline_eval.py`. Noise is real: each episode replays one
of 9 constant-altitude research runs (6 open field, 2 forest, 1 hills) cycle by
cycle, with circular time shifts and robot permutations. The true altitude
effect is unknown, so learners are scored over 8 assumed effects (strong/mild
climb, flat, mild/strong descend, peak at 40 m, peak at 30 m, cliff above
45 m), against a fixed 40 m hover and the best fixed altitude in hindsight.
The UAV's 3 m/s glide makes a 10 m probe take ~6 s, so moves are treated as
immediate.

## The fix: `AltitudeProber` (altitude_bandit.py)

Holds an incumbent altitude, spends every 2nd decision at a neighbour (±5 or
±10 m), and estimates connectivity = f(altitude) + g(4-min block), with
standard errors inflated for lag-1 autocorrelation. It moves only when the
lower bound (z = 0.5) of neighbour − incumbent is above zero. Parameters were
chosen on seeds 0–1 (`logs/ml/altitude_bench_tune.csv`) and checked on fresh
seeds 100–104 (`logs/ml/altitude_bench_validate.csv`, 360 episodes per
learner):

| Learner | Mean regret vs hindsight-best (pts) | Mean vs fixed 40 m | 5th pct vs fixed 40 m | Worst vs fixed 40 m | Runs losing > 2 pts to fixed 40 m |
|---|---|---|---|---|---|
| fixed 40 m | 7.52 | 0 | 0 | 0 | — |
| AltitudeBandit (flown) | 2.24 | +5.28 | −3.58 | −22.1 | 8.1 % |
| **AltitudeProber** | **2.09** | **+5.43** | **−1.72** | **−6.4** | **4.4 %** |

Paired difference in regret, prober − bandit: −0.15 ± 0.35 points: no
difference on average. The gain is in the tail. It stays put when staying is
right (peak at 40 m: −0.8 vs −3.6 points vs fixed 40 m), and it has no
catastrophic case. The bandit's −22 points was an episode where it wandered
off a 40 m optimum and stayed.

## Limits

* Neither learner settles consistently when the true effect is small; that is
  inherent in 90 minutes of this much noise, and it is also when the choice
  matters least.
* The bench assumes the altitude effect; if real effects are as small as the
  drift-cancelled fly-off estimates suggest, a learned altitude cannot gain
  much over a sensible fixed one in these two worlds.
* Not yet flown live.

## Use

`SAR_PLANNER_ALT=bandit` now uses the prober by default;
`SAR_PLANNER_ALT_LEARNER=ucb` restores the flown bandit. Tests:
`python3 -m pytest -q scripts/ml/test_altitude_bandit.py` (both learners, 10
pass) and `scripts/ml/test_planner_loop.py` with either learner (PASS).

## Live check (2026-11-11, `run_prober_flyoff.sh`)

The two learned-altitude arms re-flown with the prober, with the same seed, worlds and
90 min as the x-y-z fly-off. Time-weighted; the all-4 range is a 10-min
block bootstrap. Summary: `logs/prober_flyoff_20261111_092251_summary.csv`.

| World | Arm | Learner | Mean C2 | All 4 up (95% range) | All 4 down | z, 2nd half |
|---|---|---|---|---|---|---|
| open | C | bandit (11-10) | 79.2 | 58.0 (50–66) | 4.3 | 48.8 |
| open | C | **prober** | 80.0 | 50.1 (44–56) | 0.4 | **53.7** |
| open | D | bandit (11-10) | 71.0 | 47.0 (39–55) | 3.4 | 37.2 |
| open | D | **prober** | 80.8 | 49.2 (36–61) | 0.8 | **54.2** |
| forest | C | bandit (11-10) | 51.0 | 33.3 (19–50) | 34.9 | 39.2 |
| forest | C | **prober** | 47.4 | 33.6 (25–41) | 41.1 | 50.5 |
| forest | D | bandit (11-10) | 55.5 | 36.8 (23–54) | 30.2 | 32.3 |
| forest | D | **prober** | 28.9 | 17.0 (8–27) | 56.6 | 40.8 |

Baselines from 11-10: open A 77.7 / B 85.8; forest A 66.6 / B 58.0 mean C2.

* Open field: the prober went to 55 m within ~10 min in both runs and held
  it (the bandit: 49 and 37 m). It is consistent where the bandit was not.
  Arm D gained ~10 points mean C2 over its bandit run, but that is one run each and within the
  noise. Neither beats the fixed-40 m centroid baseline B.
* Forest: one run held 40 m, the other moved up to 50 m on weak evidence
  (50 m best by +0.03 ± 0.16 at the end). The forest D run was dominated by
  a cell-wide outage (all 4 down 89 % of minutes 60–70 at a steady 42 m;
  8 failed reattaches, mostly "uplink 100 % loss"). The outages are not
  altitude-driven, and they swamp any altitude effect.

## 2026-11-12: cautious learner (new planner default)

### Why
The capped forest fly-off (docs/CAPPED_FOREST_FLYOFF_REPORT.md) showed both learned-altitude arms
losing to the 40 m baselines by climbing to 45–55 m. Diagnosis from the capped runs:

* **Under the cap, the forest altitude effect is negative.** Comparing arm C with arm B at matched
  times (same x-y rule, same robot paths) gives −6.6 points of mean C2 per +10 m
  (10-min block bootstrap CI −14.9 to +0.9). C2 was 0 at ~40 m, −3 to −4 at 45–50 m and −11 at 55 m.
* **A single 90-min run cannot sign that effect.** The time-blocked slope the learner itself sees
  is +4.6 for C (CI −9.1 to +12.0) and +5.9 for D (CI −10.7 to +14.9). Its moves were driven by noise.
* **Two other suspects were ruled out.** Excluding robots mid-reattach from the reward is not the
  cause: such robots were 2.5 % of robot-cycles. Lagging the altitude by 1–3 cycles does not flip
  the sign either.

### Bench changes (`scripts/ml/altitude_offline_eval.py`)
* **New effect `forest_capped`:** −0.0066 per m, the measured value above.
* **New option `BENCH_HET`:** each robot's altitude slope gets AR(1) noise (τ 300 s), standing in
  for "climbing helps a robot in the open and hurts one under canopy". With HET 0 the results are
  bit-identical to before (checked). On capped forest traces, HET 0.03 makes the current learner
  lose to hovering (−1.3 mean, p10 −4.1), roughly matching live; HET 0 has it win by +9.
  It does not reproduce the live *climb*: on the bench the learner sits near 39 m, live at 46–48 m.
  With within-run CIs of ±10 points per 10 m, both live runs drifting up may be chance.
* **Fair comparisons need traces recorded at 40 m.** HET penalises distance from the altitude a
  trace was recorded at, so on 55 m traces it unfairly punishes the fixed 40 m hover. HET results
  are therefore read on traces recorded at 40 m only.
* **New filters** `BENCH_TRACES` and `BENCH_EFFECTS`.

### Learner changes
`AltitudeProber` gains `home_z`, `z_conf_away` and `min_gain`. A move further from home must clear
`z_conf_away` SE and beat the incumbent by `min_gain`; a move back toward home needs only `z_conf`.
The defaults leave these off, so the planner learner `prober` behaves exactly as before.

The new planner learner `cautious` is now the default (`SAR_PLANNER_ALT_LEARNER`): home = the
fixed-hover altitude (40 m), z_conf_away 1.5, min_gain 0.02, probe_every 4, probe_every_slow 20.

### Selection
Tuning used seeds 0–3: 9 variants × 9 effects, at HET 0 / 0.03 / 0.05
(`logs/ml/altitude_bench_cautious_tune*.csv`). The rule was fixed before looking at results:
first the best worst case against fixed 40 m, then the most climbing gain. No variant avoided a
small loss under `peak_40`, where 40 m is exactly optimal and any probe costs. The chosen variant
had the best worst case (−0.5) at all three HET levels.

### Validation (fresh seeds 100–109; capped traces 100–119)

| Condition | Learner | Worst per-effect mean vs 40 m | p10 | Runs losing >2 pts | climb_strong gain | forest_capped z, 2nd half |
|---|---|---|---|---|---|---|
| Old bench, 11 traces, HET 0 | current | −1.0 | −0.5 | 3.7 % | +18.9 | 29 ± 6 m |
| | **cautious** | **−0.6** | −0.3 | **1.2 %** | +9.8 | 36 ± 4 m |
| 40 m traces, HET 0.03 | current | −1.2 | −2.1 | 11.3 % | +6.5 | 36 ± 6 m |
| | **cautious** | **−0.7** | **−0.8** | **3.1 %** | +2.5 | 40 ± 3 m |
| Capped forest traces, HET 0.03 | current | −1.9 | −3.5 | 22.8 % | +2.1 | 37 ± 4 m |
| | **cautious** | **−0.8** | **−1.0** | **4.4 %** | +0.6 | 39 ± 2 m |

### Reading
* The cautious learner roughly halves the worst-case loss and cuts runs that lose >2 points by
  3–5×. The price is about half the gain where climbing truly helps.
* On capped forest traces neither learner beats hovering on average (cautious −0.3, current −0.4).
  It loses less; it does not win. This matches the fly-off: in the forest, the right answer is
  to stay near 40 m.

### Tests
* `test_altitude_bandit.py` now also runs the cautious configuration and adds
  `test_cautious_holds_home_when_flat`; 16 pass.
* The cautious learner gets its own documented limit in `test_descends_when_low_is_better`
  (≤ 36 m vs ≤ 33 m): with a strong descend effect it settles around 35 m in 90 min.
* `test_planner_loop.py` passes for cautious, prober and ucb with centroid and model x-y.
* Replaying arm C's live log, the cautious learner ended with 45 m as its base. That replay is not
  closed loop, but it shows caution reduces wandering rather than eliminating it.

### Not done
No live flight of the cautious learner yet.

## 2026-11-12: cautious learner flown live (capped forest, arms C and D)

Runner `run_cautious_forest_flyoff.sh` (log `logs/cautious_forest_flyoff_20261112_102709.log`),
5400 s each, seed 1794000001, `SAR_UL_CAP=adaptive`, `SAR_PLANNER_ALT_LEARNER=cautious`. Both rc 0,
arm confirmed, cap active (~20,600 controller rows each). Analysis:
`scripts/ml/flyoff_forest_cautious_analyse.py` -> `logs/cautious_forest_flyoff_20261112_102709_summary.txt`.

| Arm (capped forest) | Mean C2 up | All 4 up (95 % CI) | None up | Alt 2nd half | Time at 40 m | Reattach ok/failed |
|---|---|---|---|---|---|---|
| A hover | 89.1 | 70.5 (61–80) | 0.3 | 40.0 | 100 % | 3 / 0 |
| B centroid | 88.9 | 67.0 (54–78) | 0.0 | 40.0 | 100 % | 4 / 0 |
| C prober | 84.7 | 53.1 (41–64) | 0.0 | 48.3 | 26 % | 6 / 0 |
| **C cautious** | 85.2 | 65.2 (57–73) | 2.1 | 40.6 | 84 % | 3 / 0 |
| D prober | 82.4 | 43.3 (31–56) | 0.0 | 46.4 | 20 % | 4 / 1 |
| **D cautious** | 89.7 | 72.0 (60–84) | 0.3 | 39.0 | 83 % | 2 / 0 (2 UE crash relaunches) |

All-4-up differences (95 % block bootstrap): cautious C − A −5.1 [−17.9, +6.8], − B −1.7 [−16.1, +12.9],
− prober C +12.0 [−2.2, +26.4]; cautious D − A +1.6 [−13.4, +17.7], − B +5.1 [−11.9, +22.2],
− prober D **+28.8 [+11.9, +45.1]** (mean C2 +7.3 [+1.3, +13.0]).

* The incumbent stayed at 40 m in both runs; time away was scheduled probes (C mostly 45–50 m,
  D mostly 30–35 m).
* Caution turns both learned arms from losers into ties with the baselines. It does not beat them.
* D's earlier deficit was altitude, not the model's x-y.
* One run per arm; CIs are within-run.
