# Evidence inventory (Step 1)

Compiled 2026-11-12. Every number below was read from the named file during this
step unless marked otherwise.

## Status key

| Status | Meaning |
|---|---|
| **strong** | Survived its control, and the effect does not depend on one run |
| **weak** | Real but thin: one run per arm, a CI near zero, or few independent units |
| **contaminated** | Labels or outcomes are distorted by a known failure mode |
| **withdrawn** | Superseded or shown wrong; must not be claimed |

## The two cross-cutting failure modes

**D (dead process).** After a radio-link failure the UE process exits. Its counters then freeze and C2 reads "down" forever. Fixed in analysis by censoring with `dead_ue_mask` in `scripts/ml/onset_forecast.py`, and at the source by the watchdog, which relaunches the UE.

**Q (queue congestion).** 1.5 Mb/s constant-rate video overflows weak forest uplinks, and the OAI UE queues the excess for minutes. Every campaign before 2026-11-12 was flown without the uplink cap. Forest C2 labels from those campaigns are therefore partly backlog, not channel. Open field is affected far less, because its links carry 1.55 Mb/s.

> **Consequence.** Every result in sections A–D was computed on pre-cap data. Results that rest on forest labels are marked *contaminated (Q)* even where they survived censoring for D.

## A. Forecasting

Source: `logs/ml/`, produced by `scripts/ml/*`.

| # | Source | What it shows | Key number | Control survived? | Status |
|---|---|---|---|---|---|
| A1 | `CAMPAIGN_STATUS.md` ~L494–503 (h = 60 s, written when produced); `phase2_forecaster.csv` now holds a later 30 s rerun | Original forecaster on the full population | 60 s: `+c2hist` ROC 0.788, lift 2.39; `radio_blind` 0.762 / 2.27. 30 s rerun: 0.782 / 2.34 | No: D inflated it | **withdrawn** |
| A2 | `phase1_baselines.csv` | Majority, persistence and range-only baselines, 30 s, full population | Forest persistence lift 1.44, ROC 0.89 | Full population rewards persistent state | **withdrawn** (superseded by the onset formulation) |
| A3 | `phase1_range_sign.csv` | Range coefficient by terrain | Open +0.83, hills −0.15 | Full population, not censored | **withdrawn** |
| A4 | `logs/ml/dead_process_extent.csv` (`scripts/ml/dead_process_extent.py`, uses `dead_ue_mask` from `onset_forecast.py`) | D contamination: 6,760 / 54,572 = 12.4 % of rows; share of C2-down labels: hills 72.2 %, forest 53.4 %, urban 39.0 %, open field 3.9 %; C2 raw → censored: hills 83.0 → 94.6 %, forest 37.9 → 56.7 %, urban 69.6 → 79.0 %, open 78.5 → 79.2 % | Mechanistic detector | **strong** (reproduced 12 Nov) |
| A5 | `phase2b_onset.csv` | Horizon sweep, censored onset population, LOTO | 20 s trend-6 logistic: lift 1.52, ROC 0.650. LightGBM (raw) 20 s: lift 1.29, ROC 0.558. 60 s range-only: ROC 0.375 | D censored, held-out terrain | **weak**; forest folds contaminated (Q) |
| A6 | `phase2c_model_class.csv` | 8 model classes, same folds, 20 s onset | L1 logistic: lift 1.79, ROC 0.667, worst fold 0.538. MLP-large: lift 1.04, ROC 0.503, worst 0.404 | D censored, LOTO, worst-fold reported | **strong** as a capacity ordering on `radio_blind`. **Caveat:** on trend-6 at 60 s the MLPs win pooled (1.55) but lose worst-fold. Worst fold for L1 is **0.538** (v1's "0.535" was the L2 model; corrected) |
| A7 | `phase6_seq.csv` (`seq_forecast.py`) | 10 Hz TCN/GRU vs window summaries; 3 folds, 3,115 cycles | Mean ROC: summary 0.540, TCN 0.493, GRU 0.523. None above chance on its worst fold | LOTO, one window per cycle | **weak** (small n; null result) |
| A8 | `phase3_baselines.csv` | 1 s horizon on 10 Hz rows | `margin_now` ROC 0.987 | [TODO: check whether `margin_now` uses a forbidden input] | **unclear**, do not use |
| A9 | `phase7_multitask.csv`, `phase7_dependence.csv` | Multi-head risk model | P(either) observed 0.092 vs independence 0.153 vs max 0.082; union-head ROC 0.663 | LOTO, Platt on inner split | **strong** for the calibration point; forest folds contaminated (Q) |
| A10 | `phase7_vs_single.csv` | Multitask c2 head vs deployed single head | Forest PR lift 1.58 vs 1.47 | LOTO | **weak** (small differences) |

## B. Terrain sign flip

Source: `logs/ml/phase4*`.

| # | Source | What it shows | Key number | Control survived? | Status |
|---|---|---|---|---|---|
| B1 | `phase4b_effects_measured.csv` | Measured climb effect per +10 m, first-differenced | Open +0.168 [+0.148, +0.190]\*. Urban +0.033 n.s. Hills +0.015 n.s. Forest −0.040 [−0.080, +0.004] n.s. | Block bootstrap, D censored | Open field **strong**. Forest **contaminated (Q)** and n.s. |
| B2 | `phase4c_timebase.csv`, `phase4d_summary.csv` | Same effect on a uniform time base (30, 60, 120 s bins) | Open − forest: +0.184\*, +0.167\*, +0.117\*. Open − hills: +0.162\*, +0.138\*, +0.085\*. Hills − forest n.s. at every width | Outcome-dependent sampling removed | Open-field contrasts **strong**. Hills − forest **withdrawn** |
| B3 | `phase4b_interactions.csv` | Model-recovered contrasts, per cycle | Open − forest +0.006, p 0.365 (null). Urban − forest −0.020, p 0.004 (inverted). Hills − forest −0.031, p < 0.001 (inverted). Open − hills +0.037\* (correct) | Interaction test on all observations | **strong** negative: the model does not recover the flip |
| B4 | `phase4d_interactions.csv`, `phase4d_summary.csv` | Recovered contrasts on a uniform time base | Open − forest −0.0023 / +0.0026 / +0.0070, n.s. at 30 / 60 / 120 s | Uniform time base | **strong** negative. Note: v1 listed this as future work, but it has been done |
| B5 | `phase4b_signflip.csv` | 4-point Spearman, recovered vs measured | +0.40 uncalibrated, −0.20 calibrated | Not identified (n = 4) | **withdrawn** as a test |
| B6 | `phase4_climb_effect.csv` | Climb effect per policy (excite, two_phase) | Excite: open +0.164\*, urban +0.037\*, forest −0.012 n.s. | Block bootstrap | Supports B1. Forest **contaminated (Q)** |

## C. Closed-loop risk planner

Rounds 1–3, uncapped, before the watchdog in early rounds.

| # | Source | What it shows | Key number | Control survived? | Status |
|---|---|---|---|---|---|
| C1 | `CAMPAIGN_STATUS.md` §4.3.12 | Round 1 vs excitation, matched geometry | Open +17.3\*, urban +17.4\*, hills +3.5 n.s., forest −12.0 n.s. | Cross-night drift caveat; no null control | **weak**. Mechanism (ceiling ratchet 74–96 %) **strong** |
| C2 | `logs/ml/planner_vs_twophase_nullctl.csv` (`scripts/ml/planner_vs_twophase_nullctl.py`, regenerated 12 Nov) | Round-3 planner vs two_phase, geometry-matched, with same-policy null control (two_phase night 1 − night 2) | Open +10.5 [+7.0, +13.2]\* (null −0.5 n.s.); forest −15.3 [−24.8, −5.0]\* (null −5.0 n.s.); urban +22.6\* (null +18.7\*); hills +26.3\* (null −21.7\*) | Null control | Open and forest **weak** (survive the null; 1 planner run each; forest **contaminated (Q)**). Urban and hills **withdrawn** (null as large as the effect) |
| C3 | Commit `58150e1` message, `test_planner_loop.py` | Round 2 frame bug | Mean frame gap 23.5 m; 85.4 % of steps moved away from the target; tracking error 17.95 → 1.01 m after the fix | Closed-loop test (negative control) | **strong** as an engineering finding |
| C4 | `CAMPAIGN_STATUS.md` ~L1061–1090, commit `b5def3e` | Round 3 vs round 1, matched | Forest +15.4 [+8.3, +23.7]\*, urban +5.3\*, open −3.1 n.s., hills −0.9 n.s. | Matched geometry; single runs | **weak** (one run per terrain, cross-night). Forest **contaminated (Q)** |
| C5 | `phase5_planner.csv` | Offline planner decisions on round-1 state | Per-decision table | Replay only (open loop) | Supporting |

## D. Altitude learning and x-y-z placement

| # | Source | What it shows | Key number | Control survived? | Status |
|---|---|---|---|---|---|
| D1 | `logs/ml/altsel_run.log` (`altitude_model_select.py`) | No surrogate chooses altitude | Linear: 55 m everywhere, rank −0.2. linear_geo: ~55 m, rank −0.8, ROC 0.659 (worst 0.566). LightGBM: 46.6 m forest / 46.7 m open, rank −0.4 | LOTO replay | **strong** negative (offline). Note: `altsel_summary.csv` holds only the last run (`linear_int`); use the log |
| D2 | `logs/xyz_flyoff_20261110_172251_summary.csv` | Uncapped x-y-z fly-off, 8 runs | Open C2 / all-4: B 85.8 / 57.0, C 79.2 / 58.0, A 77.7 / 50.2, D 71.0 / 47.0. Forest: A 66.6 / 58.4, B 58.0 / 42.8, D 55.5 / 36.8, C 51.0 / 33.3 | 1 run per arm; all-4 CIs overlap | Open **weak**. Forest **contaminated (Q)** |
| D3 | `logs/prober_flyoff_20261111_092251_summary.csv` | Prober learner, 4 runs | Open C 80.0 / 50.1 (z 53.7), D 80.8 / 49.2 (z 54.2). Forest C 47.4 / 33.6, D 28.9 / 17.0 | 1 run each | Open **weak**. Forest **contaminated (Q)** |
| D4 | `logs/ml/altitude_bench_*.csv` / `.log` (`altitude_offline_eval.py`) | Offline bench: prober vs cautious, fresh seeds | Runs losing more than 2 pts vs 40 m: 22.8 % → 4.4 % on capped forest traces with slope noise 0.03 | Fresh seeds (100–109/119); rule fixed before results were seen | **weak** (synthetic effects on replayed traces) |
| D5 | `logs/capped_forest_flyoff_20261112_002402_summary.csv`, `docs/CAPPED_FOREST_FLYOFF_REPORT.md` | Capped forest, 4 arms, exploring learner | All-4: A 70.5, B 67.0, C 53.1, D 43.3. D − A −27.4 [−43.0, −10.7]\*. C − A −18.1 [−34.1, −2.6]\* | Q removed; within-run bootstrap only | **weak** (1 run per arm) |
| D6 | `docs/ALTITUDE_LEARNER_REPORT.md` (scratch analysis) | Forest altitude effect under the cap, C vs B matched | −6.6 per +10 m [−14.9, +0.9]. Learner's within-run slope: C +4.6 [−9.1, +12.0], D +5.9 [−10.7, +14.9] | CI includes 0 | **weak** (effect not established). Source: `logs/ml/forest_altitude_effect_capped.csv` (`scripts/ml/forest_altitude_effect_capped.py`, reproduced 12 Nov) |
| D7 | `logs/cautious_forest_flyoff_20261112_102709_summary.txt` (`flyoff_forest_cautious_analyse.py`) | Cautious learner, live, capped forest | All-4: C 65.2, D 72.0. Cautious D − A +1.6 [−13.4, +17.7]. Cautious D − prober D +28.8 [+11.9, +45.1]\* | Within-run bootstrap; 1 run | **weak**. "Ties baselines" is the most that can be said |
| D8 | `logs/final_repeats_*` (`run_final_repeats.sh`) | 3 seeds × 4 arms, forest + open field, capped | Running: 1 of 20, done ~13 Nov 23:30 | Fresh seeds | [TODO: pending] |

## E. Uplink congestion and the adaptive cap

Source: `docs/CONGESTION_TEST_REPORT.md`.

| # | Source | What it shows | Key number | Control survived? | Status |
|---|---|---|---|---|---|
| E1 | `logs/congestion_test_20261111/` (`analyse.py`) | Mechanism: forest static, video on | Ping loss 64–91 %; RTT minutes; recovery 14–18+ min; kernel qdisc backlog 0, drops 0; goodput 0.13–0.49 Mb/s | No-load phase as control, same placement | **strong** (large, monotone effect). One test |
| E2 | `logs/congestion_test_cap_20261111/` | Fixed 100 kbit cap, same test | Ping loss 0 %; RTT 69–87 ms; no recovery tail; ~94 % of video dropped | Same placement and channel rows | **strong** mechanism confirmation |
| E3 | `logs/congestion_test_adaptive_20261111/` | Adaptive cap, static forest | 0 % ping loss; video 0.30 / 0.37 / 0.42 / 0.10 Mb/s (85–94 % of uncapped drain); latency 0.30–0.42 s | Same placement | **strong**-ish (1 run) |
| E4 | `logs/congestion_test_adaptive_open_20261111/` | Adaptive cap, open field | 0 cuts; 1.55 Mb/s per robot; 82–89 ms | No uncapped open run alongside | **weak** |
| E5 | `logs/congestion_test_patrol_20261112/` (`compare.py`) | Forest patrol 30 min vs uncapped first 30 min, same seed | C2 66.8 → 91.8 %; all-4 52.0 → 72.7 %; none up 17.4 → 0.9 %; video p90 73 → 1.6 s | Paths matched (gap 3–5 m) | **weak→strong** (1 run each, large effect) |
| E6 | D5 vs D2 (same arms, uncapped vs capped) | Cap lifts every arm | None-up 25–57 % → 0–0.3 %; mean C2 29–67 % → 82–89 % | Different nights; 1 run per arm | **strong** (consistent across 4 arms) |

## F. Platform, prior work and limitations

| # | Source | What it shows | Status |
|---|---|---|---|
| F1 | `docs/CHANNEL_MODEL_LIMITATIONS.md` | No near-ground height model; terminal height 0.5 m; urban LoS coefficients everywhere; 45 dB clamp; AWGN only (no multipath) | Limitation (**strong**) |
| F2 | `docs/FINDINGS_A2G_NOMADIC.md`, `NOMADIC5G_TERRAIN_RESULTS_2026/` | SATNAC 2026 result: nomadic +16.7 open, −22.0 forest | Prior work. Overlap to manage in Step 2 |
| F3 | `docs/SYSTEM_ARCHITECTURE.md`, `docs/A2G_CHANNEL.md`, `docs/RUN_BRIDGE.md` | Platform description | Method |
| F4 | `docs/MULTITASK_REPORT.md` | Phase 7 detail | Supports A9 |
| F5 | `DATA_AUDIT.md`, `CAMPAIGN_STATUS.md` | Campaign history, data audit | Supports C1, C4 |

## Mismatches and gaps (resolved 12 Nov after approval)

1. **C2 resolved.** The null-control table was regenerated, and every number matches the v1 draft. Urban and hills are withdrawn because their nulls are as large as their effects.
2. **A4 resolved.** Saved to `logs/ml/dead_process_extent.csv`; every number matches.
3. **A6 corrected.** The L1 logistic's worst fold is 0.538.
4. **D6 resolved.** The script was moved to `scripts/ml/forest_altitude_effect_capped.py` and reproduces −6.6 [−14.9, +0.9], +4.6 and +5.9.
5. **A1 traced.** The 60 s numbers are in `CAMPAIGN_STATUS.md`. The CSV was later overwritten by a 30 s rerun. The result is withdrawn either way.
6. **Q contamination stands.** All forecasting and sign-flip results use pre-cap data, which weakens every forest-dependent claim in A–C.
