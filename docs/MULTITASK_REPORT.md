# Phase 7 — multi-head link-risk model

2026-11-09. Built while round 4 of the fly-off was running; nothing it uses was
touched. Numbers here are new and are not reflected in README, PROGRESS or the
paper.

## What was built

One terrain-blind model with a shared `SimpleImputer + StandardScaler` trunk and
three calibrated heads at a 20 s horizon, plus one declared-but-untrained head:

| Head | Label at t+20 s | Onset rows | Fail rate |
|---|---|---|---|
| `c2` | ping loss ≥ 50 % | 38,303 | 8.5 % |
| `video` | frame delivery < 90 % or latency > 500 ms | 37,897 | 8.3 % |
| `union` | c2 **or** video — **the planner's risk** | 37,399 | 9.2 % |
| `tcp` | goodput below target | — | architected only |

Protocol matches the deployed single-head surrogate except for the calibrator:
`radio_blind` 65 features, `assert_terrain_blind` on every matrix, onset rows
only, dead-UE censored at *t* and *t*+20, labels matched within ±10 s,
leave-one-terrain-out, calibration and thresholds fitted on an inner by-time
split of the training terrains. Per head: L1 logistic (C = 0.1) + **Platt
scaling**. Labels are never imputed. The `c2` head's population and feature
list are asserted equal to the deployed model's.

## Why a union head

On the planner-relevant rows (both services usable now):

| | Rate |
|---|---|
| P(C2 down at t+20) | 0.082 |
| P(video unusable at t+20) | 0.077 |
| P(both) | 0.068 |
| P(either), observed | **0.092** |
| P(either), independence `1−(1−a)(1−b)` | **0.153** |
| P(either), `max(a, b)` | 0.082 |

The independence formula overstates mission risk by two-thirds. Judged against
the observed union label on held-out terrains:

| Combiner | ROC | Brier | Brier skill | Cal. intercept |
|---|---|---|---|---|
| union head | 0.663 | 0.083 | +0.007 | −0.69 |
| max(c2, video) | 0.663 | 0.083 | +0.006 | −0.69 |
| independence product | 0.660 | **0.089** | **−0.072** | **−1.24** |

Ranking is the same for all three; the product is clearly worst calibrated.
The union head and `max` are effectively tied, so `max` is an acceptable
fallback; the product is not.

## Why Platt, not isotonic

Decided on held-out evidence. Union head, linear model:

| Held-out | ROC isotonic | ROC Platt | Distinct risk values, isotonic |
|---|---|---|---|
| forest | 0.595 | **0.604** | 28 |
| hills | **0.565** | 0.547 | 50 |
| open field | 0.705 | **0.751** | 41 |
| urban | 0.612 | 0.611 | 54 |
| pooled Brier skill | −0.002 | **+0.007** | |
| pooled cal. slope | 0.58 | **0.67** | |

Platt is strictly monotone, so within each terrain it reproduces the raw
ranking exactly; isotonic is a step function and cost 0.046 ROC in open field —
the one terrain where the planner currently helps. Isotonic also emits only
28–54 distinct values per terrain, so candidate waypoints tie and the soft-min
loses resolution; Platt is continuous.

**The cost:** worst-fold ROC drops for the `union` head (0.565 → 0.547) and
`video` head (0.563 → 0.533). The worst fold is hills in both cases, where
isotonic's tie-collapsing happened to help. `c2` worst-fold improves
(0.529 → 0.538). Accepted because the hills gain is not a property a
calibrator should be relied on for, and the open-field loss is.

## Results — pooled over held-out terrains

| Model | Head | PR-AUC | Lift | ROC | Worst fold ROC | Brier skill | Cal. slope |
|---|---|---|---|---|---|---|---|
| **linear** | c2 | 0.135 | 1.58 | 0.658 | 0.538 | +0.006 | 0.63 |
| **linear** | video | 0.129 | 1.56 | 0.669 | 0.533 | +0.000 | 0.64 |
| **linear** | union | 0.144 | 1.57 | 0.663 | 0.547 | +0.007 | 0.67 |
| lightgbm | c2 | 0.094 | 1.10 | 0.536 | 0.458 | −0.062 | 0.16 |
| lightgbm | video | 0.092 | 1.11 | 0.539 | 0.480 | −0.059 | 0.17 |
| lightgbm | union | 0.111 | 1.22 | 0.563 | 0.484 | −0.042 | 0.26 |
| ablation: + own history | c2 | 0.161 | 1.89 | 0.671 | 0.538 | +0.025 | 0.77 |
| ablation: + own history | video | 0.189 | 2.29 | 0.713 | 0.579 | +0.043 | 0.93 |
| ablation: + own history | union | 0.199 | 2.18 | 0.692 | 0.554 | +0.043 | 0.87 |

Per-fold, linear, Platt (`c2` / `video` / `union`):

| Held-out | ROC | PR-AUC | Brier skill |
|---|---|---|---|
| forest | 0.578 / 0.603 / 0.604 | 0.143 / 0.178 / 0.167 | +0.010 / +0.013 / +0.017 |
| hills | 0.538 / 0.533 / 0.547 | 0.069 / 0.056 / 0.073 | −0.038 / −0.077 / −0.040 |
| open field | 0.748 / 0.736 / 0.751 | 0.296 / 0.285 / 0.318 | +0.019 / −0.002 / +0.012 |
| urban | 0.617 / 0.597 / 0.611 | 0.118 / 0.111 / 0.125 | −0.023 / −0.041 / −0.026 |

**LightGBM loses on every head and every measure**, so the linear model is the
bundle. The `c2` head matches or beats the deployed single-head surrogate on
identical rows (PR-AUC 0.135 vs 0.132, ROC 0.658 vs 0.644, Brier 0.0776 vs
0.0780). The deployed model is isotonic; the gap is mostly the calibrator.

## Findings that matter more than the headline numbers

**1. Calibration does not transfer across terrains, and no calibrator fixes
it.** Pooled Brier skill against the base rate is +0.000 to +0.007 for every
linear head. Per fold, the terrains want opposite corrections: the Platt
calibration slope is 0.26 in hills (predictions too spread) and 5.2 in open
field (too compressed). A single calibrator fitted on three terrains cannot
satisfy both. The model ranks risk usefully; its probabilities on an unseen
terrain are close to quoting the base rate. Consequences:

- the planner should treat the risks as rankings first;
- the all-robot feasibility check thresholds a probability that does not
  transfer, so its infeasible flag is advisory, not a guarantee.

The model flying in round 4 has the same property (isotonic, Brier skill
−0.046 to +0.016 per fold).

**2. Out-of-support is mostly domain shift, not the planner.** Fraction of
held-out *recorded* rows outside the training box (0.5–99.5 percentiles of the
other three terrains), before any candidate is built:

| Held-out | Any feature | Radio only | Geometry / action |
|---|---|---|---|
| forest | 0.667 | 0.633 | 0.143 |
| hills | 0.615 | 0.569 | 0.079 |
| open field | 0.095 | 0.025 | 0.070 |
| urban | 0.222 | 0.163 | 0.076 |

Forest and hills are foreign to the model before the planner asks it anything,
driven by `ul_nb_rbs` and `ul_bits_per_sym` and their history. That shift is
itself a terrain signature in the radio layer, but leave-one-terrain-out means
a linear model can only extrapolate across it.

Candidates add to this where the state is in support. Fraction of candidates
with any robot out of support, by altitude target:

| z target (m) | forest | hills | open field | urban |
|---|---|---|---|---|
| 25 | 0.99 | 0.91 | 0.43 | 0.65 |
| 40 | 0.98 | 0.87 | 0.40 | 0.56 |
| 55 | 0.98 | 0.90 | **0.65** | 0.69 |

In open field the 55 m candidate — the one the planner picks every time — is
the least supported, through `d_slant_m_d1` and `action_dz_m`: a jump to an
absolute target implies a commanded climb and slant-range change larger than
anything the excitation runs produced. The "climb to the ceiling" choice is
therefore partly linear extrapolation, not evidence.

**3. Own history helps, especially for video.** Adding each head's own recent
QoS lifts the `video` head from ROC 0.669 to 0.713 and lift from 1.56 to 2.29,
and brings its calibration slope to 0.93. Ablation only by protocol; not in the
bundle.

**4. "All four robots usable" is predicted poorly.** Of cycles with all four
robots usable now, the fraction still all-usable at t+20 s, observed vs the
product of per-robot predictions:

| Held-out | Cycles | Observed | Predicted | Brier |
|---|---|---|---|---|
| forest | 227 | 0.859 | 0.716 | 0.141 |
| hills | 3,104 | 0.849 | 0.779 | 0.130 |
| open field | 1,597 | 0.595 | **0.664** | 0.240 |
| urban | 765 | 0.706 | 0.624 | 0.222 |
| pooled | 5,693 | 0.759 | 0.723 | 0.174 |

Too pessimistic in three terrains, consistent with robots failing together
(the product assumes they don't), and too optimistic in open field — the one
terrain where the planner currently helps.

## TCP head

No valid labels exist. 3,769 archived rows carry TCP numbers, but they are all
per-cycle iperf bursts from July to early October, which `traffic_streams.py`
documents as slow-start artefacts, and they predate the CSI-RS fix that moved
MCS from 0–5 to 28. In the default `SAR_TRAFFIC_MODE=video` the patrol runs no
TCP at all, which is why the display shows `TCP= n/a`. The existing
`SAR_TRAFFIC_MODE=iperf` already runs the persistent 1.8 Mb/s TCP stream;
Phase B only needs to log the target and the shortfall label alongside it.
TCP and video are deliberately never run together (they compete for one cell),
so a TCP campaign produces TCP labels but no video labels — handled by the
per-head masks.

## Status

**Phase A — done.** New files only:

- `scripts/ml/multitask_model.py` — labels, masks, LOTO evaluation, bundles
- `scripts/ml/multitask_risk.py` — planner-side scoring, Platt helpers,
  combiner, feasibility, support check (nothing imports it yet)
- `scripts/ml/test_multitask.py` — 15 offline checks
- `scripts/ml/test_planner_loop.py` — extended with the multitask mode
- `logs/ml/surrogates_multitask/` — four LOTO bundles + `features.json`
  schema v2, protocol `multitask_onset_censored_h20_logisticL1_platt_v2`
  (kept apart from the live `logs/ml/surrogates/features.json`)
- `logs/ml/phase7_{multitask,vs_single,allfour,support,support_features,dependence}.csv`

**Phase B — done 2026-11-10**, after round 4 finished and nothing was running
on the stack. Not flown yet.

- `scripts/ml/online_planner.py`: `SAR_PLANNER_MODEL=single|multitask`
  (unset = `single`, the round-4 model, same code path as before). Multitask
  loads the bundle through `multitask_risk.load_bundle` and plans on the
  `union` head; candidate grid, hold candidate, soft-min (β = 10), 10 m rate
  limit and the caller's geofence are shared. A misspelt value raises, which
  `auto_patrol` turns into the excitation fallback. It never silently falls
  back to the other model. New attributes: `model_kind`, `n_decisions`,
  `n_infeasible`, `last_risk`, `last_feasible`.
- **All-robot check**: every multitask decision asks whether any candidate
  keeps all robots under the union threshold (0.165 for the forest bundle).
  If none does, the soft-min still picks the least-bad candidate, but the
  decision is counted and logged as
  `[planner] INFEASIBLE: ... least-bad leaves <robot> at <risk>`.
- `scripts/auto_patrol.py`: per-robot `tcp_target_mbps` and `tcp_shortfall`
  are appended at the end of the research CSV (175 → 183 columns, first 175
  unchanged). Shortfall = goodput below `SAR_TCP_SHORTFALL_FRAC` (0.8) × the
  target (`SAR_TCP_TARGET_MBPS`, 1.8). Both cells are empty unless a
  persistent TCP flow was actually sampled, which means always empty in video
  mode. A sampled 0.0 is a real measurement and labels 1.
- `auto_patrol.py` also gets a guard so `log_action` no longer writes after
  the action log is closed. Round-4 run 3 ended with that exception at
  shutdown; the data was unaffected.

Closed-loop replay (`test_planner_loop.py`):

| Replay | Model | Decisions | Infeasible | Settled z | Result |
|---|---|---|---|---|---|
| forest, Oct 23 | single | 505 | — | 54.8 m | PASS |
| forest, Oct 23 | multitask | 505 | 14 | 55.0 m | PASS |
| open field, round-4 run 2 | multitask | 398 | **378** | 55.0 m | PASS |

Two things to know before flying it:

1. The multitask planner also pins at the 55 m ceiling. It uses the same
   linear trunk, so it extrapolates past the training altitudes in the same
   way as the single-head model. Changing the risk model alone will not
   change the altitude behaviour.
2. In open field the all-robot check fails on 95 % of decisions. This is
   finding 1 showing up live: the threshold was fitted on the other three
   terrains, and open-field risks are compressed (Platt slope 5.2). Treat
   `n_infeasible` as a cross-terrain diagnostic, not as a safety alarm.

### Validation commands (not run)

TCP labels — a short excitation run in iperf mode (persistent TCP + UDP, no
video), then check the new columns:

```
SAR_TRAFFIC_MODE=iperf SAR_POLICY=excite SAR_SEED=1794000001 \
    ./run_research_campaign.sh 900 0 sar_open_field
python3 -c "import pandas as pd,glob; d=sorted(glob.glob('logs/campaign_*'))[-1]; \
f=pd.read_csv(d+'/sar_open_field.csv'); \
[print(r, f[r+'_tcp_shortfall'].notna().mean().round(2), \
f[r+'_tcp_shortfall'].mean().round(3), f[r+'_tcp_tp_mbps'].median()) for r in 'abcd']"
```

Expect labelled fraction near 1 except right after reattaches, and median
goodput near 1.8 Mb/s on healthy links. A labelled fraction of 0 means the
flows never started.

**TCP validation — run 2026-11-10 08:50,** `logs/campaign_20261110_085049`
(iperf mode, excite, open field, seed A, 15 min, 71 cycles, 183 columns).
Every cycle was labelled for all four robots. Every label matches
`goodput < 0.8 × 1.8`. The video columns are empty, as intended.

| Robot | C2 up | Shortfall rate | Median goodput | Shortfall when C2 down | Shortfall when C2 up |
|---|---|---|---|---|---|
| alpha | 0.87 | 0.03 | 1.89 | 0.11 | 0.02 |
| bravo | 0.85 | 0.07 | 1.89 | 0.45 | 0.00 |
| charlie | 0.51 | 0.38 | 1.68 | 0.77 | 0.00 |
| delta | 0.28 | 0.66 | 0.00 | 0.84 | 0.20 |

The label tracks the link. Per-flow means agree between sender and receiver
(iperf3 server logs: 1.84/1.91/1.46/1.00 vs client 1.76/1.76/1.36/1.00 Mb/s),
so the client-side sample is not measuring a send buffer filling up. Two
caveats for training:

- **Timing skew.** About 8 cycles per robot show C2 down but TCP above the
  threshold. The TCP sample is the last 5 s of interval lines, while ping and
  C2 run at other points in a ~13 s sequential cycle, so the two disagree
  when a link changes between probes. The `probe_t_s` and `c2_t_s` columns
  let you filter for this.
- **Catch-up bursts.** After a stall, the paced flow sends above its target
  (up to 4.8 Mb/s) to catch up. Goodput above 1.8 is therefore not headroom,
  and the label is one-sided by design.

Watchdog: 0 thread exceptions, 2 recoveries, 4 failed reattaches in 15 min.
Delta had C2 for only 28 % of the run.

Multitask planner live:

```
SAR_PLANNER_MODEL=multitask SAR_POLICY=planner SAR_SEED=1794000001 \
    ./run_research_campaign.sh 5400 0 sar_open_field
grep -c INFEASIBLE logs/campaign_*/sar_open_field_research.log | tail -1
grep "model multitask" logs/campaign_*/sar_open_field_research.log | tail -1
```

## Reproduce

```
nice -n 19 python3 -u scripts/ml/multitask_model.py        # ~5 min, 1 thread
python3 -m pytest -q scripts/ml/test_multitask.py           # 15 passed
python3 scripts/ml/test_planner_loop.py sar_forest \
    logs/campaign_20261023_180030/sar_forest.csv 60         # PASS (default)
SAR_PLANNER_MODEL=multitask python3 scripts/ml/test_planner_loop.py \
    sar_forest logs/campaign_20261023_180030/sar_forest.csv 60   # PASS
```

## Tests and lint

- `pytest scripts/ml/test_multitask.py`: 15 passed (rerun after the Platt
  switch).
- `test_planner_loop.py`: PASS in both modes (table above).
- `flake8 --max-line-length 100` on the three new files: clean.
  `test_planner_loop.py` has five pre-existing warnings (semicolon statements,
  one long line) in lines this work did not change.
- No `mypy` or `ruff` installed, so no type check was run.

## Deviations from the prompt

- **Calibrator is Platt, not isotonic** — your decision on 2026-11-09, on the
  evidence above.
- `features.json` schema bump went to `logs/ml/surrogates_multitask/`, not the
  live file, because every round-4 run reads the live one at start-up.
- Label tolerance kept at ±10 s, which satisfies the ≤ 30 s bound and keeps the
  `c2` head comparable with every published number.
- Added `scripts/ml/test_multitask.py` alongside the loop-test extension: the
  multitask closed-loop test cannot run until Phase B, and the scoring module
  needed coverage now.

## First live multitask flight — 2026-11-10

`logs/campaign_20261110_091333`: `SAR_PLANNER_MODEL=multitask`, planner, open
field, seed 1794000001, video mode, 90 min, 424 cycles. It loaded the open-field
bundle (Platt, union head, threshold 0.076) with no fallback, no watchdog
exceptions, 6 recoveries and 0 failed reattaches.

| Run (open field) | Mean C2 | All 4 up | All 4 down | Mean z | Failed reattaches |
|---|---|---|---|---|---|
| **multitask** (this run) | **83.2 %** | **56.3 %** | 0.0 % | 54.8 m | 0 |
| single-head planner, round 4 run 1 (seed A) | 71.7 % | 45.9 % | 10.5 % | 54.7 m | 6 |
| excitation, round 4 run 2 (seed A) | 75.8 % | 41.7 % | 0.4 % | 48.0 m | — |
| single-head planner, round 4 run 5 (seed B) | 79.1 % | 50.4 % | 0.4 % | 54.7 m | — |

Video was usable 79–88 % of the time per robot.

**This is the best open-field run so far, but it does not show that the
multitask model is better.**

- The planner flew the same altitude as the single-head model (54.8 vs
  54.7 m, sd 1.7). The new risk did not change the altitude choice.
- The seed does not reproduce robot motion. Robot positions at the same cycle
  differ between runs, so "same seed" pairs are not matched trajectories.
- Run 1 lost 6 reattaches and had seven spells with all four robots down;
  this run lost none. That difference alone could account for most of the
  11-point gap. Seed-to-seed spread for the same planner is already 7 points
  (71.7 vs 79.1).
- 176 of 177 decisions were infeasible against the 0.076 threshold. The
  least-bad candidate left the exposed robot spread across all four robots
  (alpha 45, bravo 57, charlie 41, delta 33), so the planner was not
  sacrificing one robot. The threshold is simply below what open field can
  achieve (finding 1).

Separating the model from run-to-run luck needs several runs per arm, plus
either reproducible robot paths or an analysis that conditions on reattach
failures.
