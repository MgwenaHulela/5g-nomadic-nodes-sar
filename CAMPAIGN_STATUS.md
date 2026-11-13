# Campaign Status — where the project is right now

**Snapshot: Wednesday 2026-10-28, 00:45 SAST — ALL ML PHASES 0–5 DONE; PHASE 5 FLOWN THREE TIMES, OBJECTIVE FIXED**
*(first written Sun 01:15; revised Sun 12:55, Mon 06:15 and Mon 11:10)*
**Deadline being worked to: Monday 2026-10-26, 10:00 — start of model training**

This document explains what the machine is doing at this moment, what has
already been banked, and what still has to happen. It is written to be read
cold, so each section says *why* the thing matters, not just what it is.

---

## 1. The one-paragraph version

**Data collection is finished and validated.** The four-round campaign ran
Fri 18:00 → Sun 23:51 without a single failed world-run, and a four-terrain
RSRP-recovery set ran Sun 23:59 → Mon 06:07, also clean. **13,665 measurement
cycles, 2.14 M fast-radio samples and 5,317 labelled actions** are on disk;
every run passes the pathloss sanity check. **ML Phase 0 (the data layer) is built and
validated** (§4.3.6); Phases 1–5 are today's work.

Two results are already settled. **Naive roaming reduces C2 availability in
every terrain** (§3.2), which is the baseline the predictive layer must beat.
And **in structured terrain, distance explains almost nothing about link
quality** (§3.4) — the clearest possible motivation for a terrain-blind
predictor.

One defect cost real data: measured RSRP/SINR is empty for all 16 campaign
runs (§5). The recovery set restores it for 1,125 cycles / 218,759 fast-radio
samples across all four terrains.

---

## 2. What was run

### 2.1 What the campaign actually is

`run_full_campaign.sh 4 12000` — four rounds, each visiting four terrain
worlds, each world run for 12,000 s (3 h 20 m) of measurement. With stack
bring-up and teardown that is **3 h 22 m per world, 13 h 28 m per round, 54 h
total.**

Each world run boots the complete stack from cold: Gazebo with the terrain,
the PX4 x500 UAV carrying the gNB, four TurtleBot3 ground robots, the OAI gNB
and four `nr-uesoftmodem` UEs, the Open5GS core, the channel bridge that
couples Gazebo geometry to radio pathloss, and per-robot network namespaces so
traffic genuinely crosses the air.

### 2.2 Why the rounds alternate policy

Rounds alternate between two UAV behaviours, and they answer two different
questions:

| Round | Policy | What it is for |
|---|---|---|
| 1, 3 | `two_phase` | **Evidence.** First half the UAV holds station (static), second half it roams (nomadic). Same terrain, same seed, same traffic — so the static half is a controlled baseline for the nomadic half. This is what answers *"does roaming actually help the robots?"* |
| 2, 4 | `excite` | **Training data.** The UAV executes a randomised action policy (`hold`, `step`, `climb`, `descend`, `toward_weak`, `away_weak`) with short dwell times. The point is not to fly well — it is to cover the state-action space so a model can learn *what a move does to the link*. A well-behaved UAV would only ever show you good states. |

Running both means the same campaign produces the comparison figures for the
paper *and* the training set for the predictor, from identical conditions.

### 2.3 Progress

| Round | Policy | Started | Status |
|---|---|---|---|
| 1 | two_phase | Fri 18:00 | ✅ complete (Sat 07:28) |
| 2 | excite | Sat 07:28 | ✅ complete (Sat 20:56) |
| 3 | two_phase | Sat 20:56 | ✅ complete (Sun 10:24) |
| 4 | excite | Sun 10:24 | ✅ complete (Sun 23:51) |
| RSRP recovery | excite, `phy_log_level="debug"` | Sun 23:59 | ✅ complete (Mon 06:07) |

All 16 campaign world-runs and all 4 recovery world-runs returned `rc=0`.
The only failure in the whole exercise was one `stack_failed` on Thu 17:52,
which predates round 1 and was retried successfully.

World order within a round is fixed: `open_field → urban → forest → hills`.

**Actual finish: Sunday 23:51:43**, one minute off the projection made 23
hours earlier. The recovery set then ran to 06:07 Monday, leaving
≈3 h 50 m before the 10:00 training slot.

### 2.4 What one measurement cycle does

Repeating roughly every 20–25 s, for each of the four robots:

1. Read true geometry from Gazebo (UAV and robot positions, horizontal and
   slant range).
2. Read the radio state the UE actually reports — RSRP, SINR, CQI, MCS, BLER,
   timing advance.
3. Measure the **C2 link** with a 10-packet ping (`-c 10 -i 0.2 -W 2`). The
   label *"C2 available"* is `loss < 50%` over that window.
4. Measure **user-plane carrying capacity** with a live RTP-style video stream
   at 1.5 Mb/s per robot — goodput, frame-delivery ratio, packet loss, one-way
   latency, jitter.
5. Write one row per cycle.

In parallel, `fast_radio_sampler.py` logs the radio state at **10 Hz**
independently of the cycle loop. This matters: the per-cycle CSV has a ~22 s
cadence, so a 30 s history window spans barely one row. Short-horizon
forecasting (1 s, 3 s, 5 s, 10 s) is only possible because of the 10 Hz log.

### 2.5 What each world run writes

| File | Contents | Typical size |
|---|---|---|
| `sar_<world>.csv` | One row per measurement cycle — geometry, radio, C2, video, modem counters | 500–1,200 rows |
| `sar_<world>_fastradio.csv` | 10 Hz radio samples | ~120,000 rows |
| `sar_<world>_actions.csv` | One row per UAV action, with boundaries | 45–480 rows |

Plus `manifest.csv` recording the seed, policy, world and run ID for each run,
so every row is traceable to its conditions.

### 2.6 Health during the run (sample, Sun 12:55)

```
campaign running    elapsed 1d 18:54    disk free ~240 G
stack:  gNB=2  UEs=8  bridge=1              (full roster attached)

current run — sar_open_field, excite, 644 cycles so far
            goodput    frames    C2 up
  alpha     1.71 Mb/s   99.9%    82.5%
  bravo     1.72 Mb/s   99.9%    70.0%
  charlie   1.72 Mb/s   99.9%    83.6%
  delta     1.71 Mb/s   99.9%    74.1%

  pathloss  0.0 – 38.2 dB
```

Two things to read here. **The pathloss spread proves the channel bridge is
alive** — a flat 0.00 dB would silently void the whole run, which is the single
most dangerous failure mode this testbed has. And **the per-robot spread is
real signal**: delta at 63.9% C2 with degraded frames while alpha holds 90.6%,
in the same cell at the same moment. That variation is exactly what the
predictor has to learn to anticipate.

All twelve completed world-runs pass the pathloss sanity check (max 38–45 dB).

---

## 3. What is already banked

### 3.1 Volume

| Round | Cycles | 10 Hz samples | Labelled actions | RSRP? |
|---|---|---|---|---|
| 1 (two_phase) | 3,226 | 480,876 | 498 | — |
| 2 (excite) | 3,114 | 480,778 | 1,727 | — |
| 3 (two_phase) | 3,037 | 480,859 | 540 | — |
| 4 (excite) | 3,163 | 480,587 | 1,763 | — |
| RSRP recovery (4 terrains) | 1,125 | 218,759 | 789 | ✅ 100% |
| **TOTAL** | **13,665** | **2,141,859** | **5,317** | 8.2% of cycles |

The CSI side-car additionally captured **284,421 rows (38 MB)** of UE-measured
SINR and CQI at 5 Hz, covering round 4 from urban onward and the whole
recovery set — `logs/csi_20261025_141701.csv`.

Round 2 produces 3.5× the labelled actions of round 1 from the same wall-clock
time, because the excitation policy uses short dwells. That is the training
set; round 1 and 3 are the evidence set.

### 3.2 Headline result — does roaming help? **No, not naively.**

Both `two_phase` rounds are complete, with different seeds. C2 availability,
static half vs nomadic half of the same run:

| Terrain | R1 static | R1 nomadic | R1 Δ | R3 static | R3 nomadic | R3 Δ |
|---|---|---|---|---|---|---|
| hills | 99.6% | 27.1% | **−72.5** | 99.4% | 65.3% | **−34.1** |
| forest | 61.9% | 16.8% | **−45.2** | 63.8% | 21.8% | **−42.1** |
| urban | 78.7% | 78.2% | −0.5 | 66.1% | 50.5% | **−15.6** |
| open_field | 82.1% | 73.8% | −8.3 | 82.6% | 74.8% | −7.8 |

**The sign is negative in all eight cells.** Magnitudes vary with seed — hills
−72.5 then −34.1, urban −0.5 then −15.6 — so individual numbers are not
reproducible to the percentage point, but the direction is. Open field is the
most stable (−8.3 / −7.8).

#### Distance matching — resolving the forest confound

Round 1's forest comparison was unusable because the nomadic half averaged
20.8 m further from the robots, so some of the loss could have been plain
distance. Pooling both rounds and comparing **within slant-range bins** removes
that objection. Only bins with ≥ 80 samples in both phases are used:

| Terrain | Range band | Static | Nomadic | Δ |
|---|---|---|---|---|
| hills | 40–60 m | 99.6% | 40.9% | −58.7 |
| | 60–80 m | 99.4% | 47.5% | −51.9 |
| | **matched** | **99.5%** | **43.9%** | **−55.6** |
| forest | **40–60 m** | **62.9%** | **20.0%** | **−42.9** |
| urban | 40–60 m | 81.2% | 67.1% | −14.1 |
| | 60–80 m | 59.1% | 62.4% | +3.3 |
| | 80–120 m | 63.6% | 58.8% | −4.8 |
| | **matched** | **72.0%** | **63.5%** | **−8.5** |
| open_field | 40–60 m | 98.5% | 88.1% | −10.5 |
| | 60–80 m | 90.8% | 78.1% | −12.7 |
| | 80–120 m | 66.5% | 59.0% | −7.6 |
| | **matched** | **82.3%** | **74.7%** | **−7.7** |

**The forest confound is resolved and the result stands.** At matched range the
forest penalty is −42.9 pp, essentially identical to the raw −45.2 / −42.1.
The degradation was never distance — it is the movement.

#### How to read this

- Ranked by harm: **hills −55.6, forest −42.9, urban −8.5, open field −7.7.**
  The two terrains with real 3-D structure are the two where roaming is
  catastrophic, which is physically sensible: moving the node across a ridge
  line or a tree canopy crosses shadowing boundaries the robots cannot ride out.
- Urban's 60–80 m band is the **one positive cell in the whole table** (+3.3).
  Worth a look during analysis — it may be the street-canyon geometry where
  repositioning genuinely finds line of sight.
- **This is the case for the project, not against it.** The thesis is that a
  *predictive* nomadic node beats a static one. What these rounds establish is
  that an *unpredictive* one is clearly worse — which is exactly why a
  forecaster is needed, and gives the model a real, non-trivial baseline to
  beat rather than a straw man.

### 3.3 Recovered RSRP — quality check

The four recovery runs carry full-precision RSRP at 100% fill. Checked against
the simulator's own geometry:

| Terrain | RSRP range | corr(slant range, RSRP) | corr(slant range, ploss) |
|---|---|---|---|
| open_field | −73 … −42 dBm | **−0.724** | +0.715 |
| urban | −77 … −42 dBm | −0.063 | +0.172 |
| forest | −74 … −42 dBm | −0.071 | +0.024 |
| hills | −78 … −42 dBm | −0.078 | +0.303 |

The measured RSRP mirrors the injected pathloss in every terrain (open field
almost exactly: −0.724 against +0.715), so the measurement is behaving
correctly. **Two caveats for training:** RSRP is *censored above* at −42 dBm
— it saturates at close range, compressing the top of the distribution. And
the four terrains are unevenly sampled (open_field 374 cycles, hills 444,
urban 193, forest 92), because cycles take longer when the link is bad, so
the worst terrain yields the fewest rows. Stratify accordingly.

### 3.4 Distance barely predicts link quality in structured terrain

This falls out of the table above and deserves to be stated on its own,
because it is the clearest motivation for the whole project.

In **open field**, range explains link quality well: corr(range, RSRP) =
−0.72. In **urban, forest and hills it collapses to −0.06 … −0.08** —
essentially nothing. The simulator's own pathloss shows the same collapse
(+0.72 open field vs +0.02 … +0.30 elsewhere), so this is real terrain
shadowing, not a measurement artifact.

**What it means:** a robot 50 m away behind a ridge or a canopy can have a
worse link than one 120 m away in the clear. Any policy that reasons about
range alone — including "move closer to the weakest robot" — is working with
a feature that carries almost no information in exactly the terrains where
the UAV matters most. That is why the node has to *predict* from signal
history rather than react to geometry, and it is consistent with §3.2, where
naive roaming did most damage in hills and forest.

---

## 4. What still has to be done

### 4.1 Finish the campaign — ✅ DONE

Complete. 16 campaign world-runs (Fri 18:00 → Sun 23:51) plus 4 RSRP-recovery
world-runs (Sun 23:59 → Mon 06:07), all `rc=0`. UE configs were restored to
`phy_log_level = "error"` automatically and the CSI side-car was stopped.

### 4.2 Validate the data before training — ✅ DONE 2026-10-26 06:10

Run across all 20 world-runs; results in the inventory below. All pass the
pathloss check; the 16 campaign runs are `ss_stale` (no RSRP), the 4 recovery
runs are live at 100% fill. The testbed has several failure modes that produce
**data that looks completely normal** and raises no error, so re-run these
checks on any future run:

1. **`ss_stale` / `ss_samples`.** `a_ss_stale = 1` and `a_ss_samples = 0`
   on every row means the UE's radio measurements never arrived and every
   RSRP/SINR column is empty. **This is the state of the entire campaign**
   (§5) — check it first, because the rows look perfectly normal otherwise.
2. **Pathloss variance per run.** `ploss_dB` identically 0.00 across a run
   means the channel bridge died — no terrain, no distance effect, and
   `corr(distance, throughput)` flips *positive*. That run is void. Healthy:
   0–45 dB spread.
3. **TUNs inside the `robot_<name>` namespaces**, not the root namespace. If
   they leak to root, host pings succeed while the robots are isolated, and
   the C2 watchdog is defeated — the run looks healthy and means nothing.
4. **UE count = 4 and gNB alive** for the whole run. A short-handed run looks
   exactly like terrain outage.
5. **Exit code.** 3 = dead gNB, 4 = unrecoverable UE. Either aborts the run
   now rather than producing hours of zeroed columns.

### 4.3 Build the ML layer — the plan

Environment is ready: numpy 1.26, pandas 2.3, scipy 1.15, scikit-learn 1.7,
**lightgbm 4.7**, xgboost 3.2, torch 2.14, matplotlib 3.10. Nothing to install.

#### 4.3.0 Two facts that shape the design

**C2 availability is 71.5% pooled**, so a classifier that always predicts
"connected" scores 71.5% — 83.0% in hills. **Accuracy is not a usable metric.**
Everything is reported as PR-AUC and balanced accuracy with the majority-class
baseline printed beside it. Forest is the only terrain where *unavailable* is
the majority class.

| terrain | C2 available | majority-class baseline |
|---|---|---|
| hills | 83.0% | 83.0% |
| open_field | 78.5% | 78.5% |
| urban | 69.6% | 69.6% |
| forest | 37.9% | 62.1% |
| **pooled** | **71.5%** | **71.5%** |

**There are three datasets, not one**, and A and B need different feature sets,
so they are different models rather than one model with missing values:

| | Size | Coverage | RSRP | Supports |
|---|---|---|---|---|
| **A — broad** | 13,665 cycles | 4 terrains × 2 policies | ✗ | cycle horizons 30/60/120 s |
| **B — deep** | 1,125 cycles / 218,759 fast rows | 4 terrains, excite only | ✓ 100% | fast horizons 1/3/5/10 s |
| **C — bridge** | 284,421 rows @ 5 Hz | round 4 urban-on + recovery | SINR/CQI | fast horizons, ~3× B's span |

Measured cycle cadence is better than `experiment.json` assumed — median 11.0 s
(hills) to 22.1 s (forest) — so a 30 s horizon spans 1.4–2.7 rows. **60 s and
120 s are the primary cycle horizons**; 30 s is marginal because forest's p90
cadence of 36.9 s sometimes exceeds it.

#### 4.3.1 Phases

| Phase | Deliverable | Status | Notes |
|---|---|---|---|
| **0** | **Data layer** | ✅ **built + validated** | One loader joining per-cycle + fastradio + actions + CSI. Windowing that never spans a run boundary. The forbidden-input rule **enforced in code** — a whitelist that raises if `ploss_dB`, `rsrp_computed_dBm` or `terrain` reaches a model. Three split generators. |
| **1** | **Baselines** | ✅ **done** (§4.3.7) | *persistence* (the real bar), *range-only* (§3.4 predicts this is near-useless in structured terrain — a falsifiable prediction stated in advance), *majority class*, and *terrain-oracle* as an upper bound that quantifies what terrain blindness costs. |
| **2** | **Cycle-horizon forecaster** (dataset A) | ✅ **done** (§4.3.8) | C2 availability at t+h. Features: geometry, gNB link adaptation (`ul_bits_per_sym`, `ul_code_rate`, `ul_nb_rbs`), BLER/HARQ, C2 RTT + loss history, video QoS, action context. LightGBM — 13 k tabular rows, and the sign-flip argument needs interpretable effects. |
| **3** | **Fast-horizon forecaster** (B + C) | ✅ **done** (§4.3.10) — *mixed; see §4.3.10* | RSRP margin above the floor, and SINR. Secondary result, scoped honestly given N. |
| **4** | **Sign-flip test** — **the headline** | ✅ **done** (§4.3.9) | (a) establish the effect reverses sign between terrains from the action windows (≈120 climbs and ≈115 descents *per terrain*, enough for real CIs); (b) show a terrain-blind model trained leave-one-terrain-out recovers that reversal from signal history alone. |
| **5** | **Planner** | ✅ **done** (§4.3.11) — *partial; see §4.3.11* | Surrogate + soft-min objective over the worst robot. |

#### 4.3.2 Ablations — not optional

1. **With and without ping-loss history.** Ping loss is close to the C2 label;
   the brief requires both.
2. **With and without user-plane features.** Video goodput and frame delivery
   degrade with the link, so they are nearly as leaky as ping loss. Not in the
   brief; added because the leak is real.
3. **Terrain-oracle vs terrain-blind.** Quantifies the cost of blindness.

#### 4.3.3 Splits

Leave-one-terrain-out is the **primary** split — it directly tests terrain
blindness. Then by `run_seed` (across rounds), then by `time_block` within a
run. **Never random rows**: they leak across the history window.

#### 4.3.4 Standing caveats

RSRP is **censored above at −42 dBm** (saturates at close range). The terrains
are **unevenly sampled** (hills 4,703 cycles vs forest 2,226), so stratify
rather than pooling. Dataset B is excite-only, so it carries no static-phase
behaviour.

#### 4.3.5 If time runs short

**Cut the planner, not the sign-flip.** The planner is an engineering
deliverable; the sign-flip rediscovery is the scientific claim.

#### 4.3.6 Phase 0 — BUILT AND VALIDATED (2026-10-26 10:50)

`scripts/ml/dataset.py`. Loads every run into **long format** (one row per
cycle per robot, which is what the per-robot soft-min objective needs), joins
the fast-radio and action logs, and enforces the project's hard rules in one
place so nothing downstream can violate them by accident.

| Check | Result |
|---|---|
| long table | **54,572 rows × 58 cols** — 20 runs × 4 robots = 80 series |
| C2 available | **71.5%** — matches the independent count exactly |
| rows with measured RSRP | **4,412** — matches the §4.4 floor-derivation count exactly |
| forbidden inputs | `ploss_dB` / `rsrp_computed_dBm` absent from the table; guard **raises** if either reaches a feature matrix |
| run boundaries | `lag1` is NaN on the first row of all 80 series — no window crosses a run |
| target coverage | 30 s **98.9%** · 60 s **99.6%** · 120 s **99.4%** |
| LOTO fold sizes | forest 8,828 · hills 18,748 · open_field 15,328 · urban 11,424 |

Target coverage is better than the ragged cadence suggested: the 60 s horizon
matches 99.6% of rows at a median actual gap of 58.6 s, so the horizons are
honest rather than nominal. A match further than half the horizon from the
requested time is rejected as NaN rather than silently accepted.

**One thing the validation surfaced:** the LOTO folds are unevenly sized
(hills 18,748 vs forest 8,828), so held-out scores are not directly comparable
across terrains without stratifying — see §4.3.4.

API: `build()` → long DataFrame; `add_history()`; `add_target(horizon_s)`;
`features_targets(df, horizon_s, groups)`; and the three split generators
`leave_one_terrain_out` / `by_run_seed` / `by_time_block`. Feature groups are
the unit of ablation: `geom`, `radio`, `rsrp`, `ping` (leaky), `video` (leaky),
`action`.

#### 4.3.7 Phase 1 — BASELINES, DONE (2026-10-26 12:30)

`scripts/ml/baselines.py` → `logs/ml/phase1_baselines.csv`,
`logs/ml/phase1_range_sign.csv`.

**The task, fixed here and used by every later phase:** given everything
observable up to *t*, predict whether C2 to **this robot** is **DOWN** at
*t + h*. The positive class is the failure. C2 is up 71.5% of the time, so
"predict up" scores 71.5% accuracy while being useless; the whole point is
the other 28.5%. Headline metric is PR-AUC with the no-skill rate printed
beside it, plus ROC-AUC because the terrains have very different base rates.
Decision thresholds come from the **train** block only — never tuned on test.

##### The bar, h = 60 s, leave-one-terrain-out, pooled over folds

| Baseline | PR-AUC | ×no-skill | ROC-AUC | bal. acc |
|---|---|---|---|---|
| no-skill (DOWN rate) | 0.305 | 1.00 | 0.500 | 0.500 |
| majority class | — | — | — | 0.500 |
| **persistence** | 0.537 | 1.76 | 0.748 | **0.748** |
| **persistence (3-cycle mean)** | **0.628** | **2.06** | **0.771** | 0.723 |
| range-only | 0.236 | 0.78 | **0.334** | 0.473 |
| geometry-only | 0.253 | 0.83 | 0.362 | 0.501 |
| *oracle: terrain identity* | *0.466* | *1.53* | *0.679* | *0.636* |
| *oracle: range refit in-terrain* | *0.535* | *1.76* | *0.693* | *0.637* |

**Phase 2 has to beat 0.628 PR-AUC / 0.771 ROC-AUC.** Anything below that is
not a forecaster, it is a slower way of saying "the same as now".

##### Three results that change what comes next

**1. Range-only is not weak — it is inverted.** ROC-AUC **0.334**, well below
the 0.500 of a coin flip. A model trained on three terrains and applied to the
fourth gets the sign of distance *wrong*. The per-terrain fit says why:

| terrain | coef on slant range (h=60 s) | DOWN rate | median range |
|---|---|---|---|
| forest | **+0.432** | 63.2% | 44.6 m |
| open_field | **+0.269** | 25.6% | 71.9 m |
| urban | +0.066 | 32.5% | 62.5 m |
| hills | **−0.150** | 17.7% | 61.8 m |
| **pooled** | **−0.112** | 30.5% | 61.2 m |

Three terrains say *further is worse*; hills says *further is better*; and the
pooled slope agrees with **hills**, not with the majority. That is Simpson's
paradox, driven by hills being 34% of the rows while having the longest median
range (61.8 m) and by far the lowest failure rate (17.7%). This is the §3.4
prediction confirmed in a stronger form than it was stated: distance is not
merely uninformative in structured terrain, **a distance model learned off-
terrain is actively harmful**, and the harm is a sign error. It is also the
first independent sighting of the sign flip that Phase 4 has to recover.

**2. Persistence beats both terrain oracles.** Link history (0.628) is worth
more than knowing which terrain you are in (0.466) and more than a
distance model recalibrated inside the correct terrain (0.535). This reframes
the thesis in our favour: **terrain blindness may cost very little, because
the signal history already carries the terrain's effect.** Phase 4's job is to
show a terrain-blind model recovers the flip — Phase 1 says the information is
plausibly there to recover.

**3. Headroom grows with horizon.** Persistence (3-cycle) scores 0.687 → 0.628
→ 0.601 PR-AUC at h = 30 / 60 / 120 s. The further ahead we forecast, the more
there is for a model to add — which is also where a *predictive* UAV policy
actually needs to win, since 30 s is not enough time to reposition.

##### Cross-checks

Run under all three splits. Under **by-seed** and **by-time** the terrains are
mixed into both sides, so range-only recovers to ≈ no-skill (0.334 / 0.439,
ROC 0.513 / 0.541) instead of inverting — confirming the inversion is a
*transfer* failure across terrain, not a broken feature. Persistence is stable
across all three splits (0.537 / 0.537 / 0.681), so it is a genuine bar and not
a split artifact. The by-time block has a higher failure rate (41.4% vs 30.5%)
because robots patrol outward as a run proceeds.

##### Two artifacts fixed while building this

Both would have flattered or distorted the results if left in. Recorded because
the numbers above are only trustworthy if the scoring is:

* A 0/1 persistence score has no usable prevalence quantile — the (1−p)
  quantile of a mostly-zero vector is 0.0, so `score ≥ 0` flagged every row and
  reported balanced accuracy 0.500 for a predictor that actually scores 0.748.
  Binary scores are now thresholded at their midpoint.
* Pooling LOTO folds for a *constant* predictor ranks rows by which fold they
  came from, not by anything about the row. `majority` is now blanked rather
  than reported with a spurious ROC-AUC of 0.32. The terrain oracle is exempt:
  its per-fold constant is the held-out terrain's own failure rate, so its
  pooled ranking is precisely what the oracle is meant to measure.

#### 4.3.8 Phase 2 — CYCLE-HORIZON FORECASTER, DONE (2026-10-26 18:10)

`scripts/ml/forecast_cycle.py` → `logs/ml/phase2_forecaster.csv`,
`phase2_importance.csv`, `phase2_predictions.csv.gz`. LightGBM, same task,
same metric, same splits and same train-only thresholds as Phase 1, so the
rows sit directly beside the baselines.

##### The result, h = 60 s, leave-one-terrain-out, pooled

| Model | features | PR-AUC | ×no-skill | ROC-AUC | bal. acc |
|---|---|---|---|---|---|
| no-skill | — | 0.305 | 1.00 | 0.500 | 0.500 |
| persistence | — | 0.537 | 1.76 | 0.748 | 0.748 |
| **persistence (3-cycle) — the bar** | — | **0.628** | **2.06** | **0.771** | 0.723 |
| **radio_blind** | 65 | **0.690** | **2.27** | 0.762 | 0.700 |
| **+c2hist** | 79 | **0.727** | **2.39** | **0.788** | **0.727** |
| +ping *(leaky)* | 96 | 0.731 | 2.40 | 0.795 | 0.730 |
| +video *(leaky)* | 119 | 0.753 | 2.47 | 0.815 | 0.742 |

**The bar is cleared.** `+c2hist` — the honest, non-leaky model — beats
3-cycle persistence by **+0.099 PR-AUC** (0.727 vs 0.628) on the primary
split, and also on ROC-AUC and balanced accuracy. This is the first point in
the project where a *predictive* claim is supported rather than asserted.

##### Four things worth more than the headline number

**1. `radio_blind` beats persistence without ever observing a C2 failure.**
It gets no ping loss, no RTT, no video, and not even the label's own history —
only geometry, gNB link adaptation and action context. It still scores 0.690
against persistence's 0.628. Degradation is visible in the radio *before* it
appears in the C2 link. The honest caveat: on ROC-AUC it is a shade *below*
persistence (0.762 vs 0.771), so the win is on the rare-class metric, which
is the one that matters here, not on both.

**2. The leaky features barely matter — the required ablation (§4.3.2) comes
out in our favour.** Over `+c2hist`, adding ping loss and RTT is worth
**+0.004** PR-AUC and adding video QoS **+0.026**. The result does not rest
on features that are near-copies of the label, which was the main threat to
its credibility.

**3. Split ordering behaves exactly as it should** — a coherence check on the
whole evaluation, not a result. Pooled PR-AUC for `+c2hist` at h = 60 s:
by_time **0.886** > by_seed **0.789** > LOTO **0.727**. Predicting the end of
a run you have already seen is easy; a new seed is harder; a terrain the model
has never met is hardest. If this had come out in any other order the splits
would have been leaking.

**4. Per held-out terrain, the gain is very unevenly distributed:**

| held-out | n | DOWN rate | PR-AUC | ×no-skill | vs persistence |
|---|---|---|---|---|---|
| hills | 18,748 | 17.7% | 0.811 | **4.57** | +0.050 |
| urban | 11,424 | 32.5% | 0.699 | 2.15 | +0.115 |
| forest | 8,828 | 63.2% | 0.933 | 1.48 | +0.054 |
| open_field | 15,328 | 25.6% | **0.336** | **1.31** | +0.073 |

**open_field is the hard transfer target** (ROC 0.593, barely above chance),
and that is consistent rather than disappointing: Phase 1 showed open_field is
the *one* terrain where plain distance predicts the link, so it is the least
like the three terrains the model trains on. The structured terrains transfer
to each other; the open one does not transfer in.

##### What the model actually uses

Mean gain importance, h = 60 s, LOTO:

| `radio_blind` | | `+c2hist` | |
|---|---|---|---|
| `dl_bler_roll3_std` | **25.2%** | `c2_loss_pct_roll3_mean` | 38.3% |
| `dl_harq_nok` | 8.4% | `c2_ok_roll3_mean` | 13.0% |
| `dl_bler_d1` | 8.3% | `dl_bler_roll3_std` | 6.4% |
| `ul_nb_rbs` | 7.3% | `ul_nb_rbs` | 4.7% |
| *BLER/HARQ family* | **62%** | *C2 history* | 55% |
| *UL scheduling* | 21% | *geometry* | 17% |
| *geometry* | 16% | *BLER/HARQ* | 14% |

The single most informative feature available to the terrain-blind model is
**the volatility of the block error rate** (`dl_bler_roll3_std`), not any
position or distance. Given the label's own history the model leans on it
(55%) and BLER drops to 14% — which is precisely why `radio_blind` is the
more interesting of the two: it is forced to use the physical-layer signal.

This matters for Phase 4. A *volatility* feature is the kind of quantity that
can carry a terrain's character across terrains, whereas Phase 1 showed
distance transfers with the **wrong sign**. If the sign flip is recoverable
terrain-blind, this is the family of features it will be recovered from.

##### Horizons

Pooled LOTO PR-AUC, `+c2hist` against the bar:

| h | persistence (3-cycle) | +c2hist | gain | ratio |
|---|---|---|---|---|
| 30 s | 0.687 | 0.791 | +0.104 | 1.15× |
| 60 s | 0.628 | 0.727 | +0.099 | 1.16× |
| 120 s | 0.601 | 0.683 | +0.082 | 1.14× |

The advantage holds at a roughly constant ratio out to 120 s. That is the
operationally important horizon — 30 s is not enough time for the UAV to
reposition — and at 120 s the model still scores 2.20× no-skill.

##### Model selection, and the overfit that was nearly reported

Hyperparameters were chosen on an **inner by-time split** (first 70% of each
run trains, last 30% validates), never on the LOTO folds that report the
result: every terrain sits on both sides of that split, so the choice never
saw a held-out terrain.

The first configuration tried — 400 trees × 63 leaves — scored train ROC
**0.987** against validation **0.877**, a +0.110 gap. It had memorised the
training terrains, and under LOTO it scored 0.668 PR-AUC, *below* the final
configuration's 0.727. Tightening to 15 leaves / `min_child_samples` 300 /
λ = 5 closed the gap to +0.018, raised the held-out score, and ran 3.4×
faster. Everything from 200 × 31 downward is tied on validation
(PR 0.884–0.887), so the middle of that flat region was taken rather than its
argmax — the maximum of a flat curve is noise.

**A hypothesis that was tested and rejected,** recorded because it would have
been an attractive thing to assert: the first ladder run won on every
individual terrain yet lost to persistence pooled, which looked like a
cross-terrain calibration problem. Rank-normalising each fold's scores before
pooling — the standard fix — made it **worse** (PR 0.668 → 0.581). The
terrains have genuinely different failure rates (forest 63%, hills 18%) and
the model's absolute probability scale carries that correctly; flattening it
discards real information. The actual cause was the overfit above.

#### 4.3.9 Phase 4 — SIGN-FLIP TEST, DONE (2026-10-26 19:40)

`scripts/ml/sign_flip.py` → `logs/ml/phase4_climb_effect.csv`,
`phase4_interaction.csv`, `phase4_recovered_effect.csv`,
`phase4_action_contrast.csv`. **This is the scientific claim of the project.**

Everything is estimated in **first differences** within a (run, robot)
series, so no fixed difference between robots, runs or terrains can
contribute; **horizontal distance is held constant**, so the altitude
coefficient is not a restatement of "further is worse"; and uncertainty comes
from a **block bootstrap** over contiguous 20-cycle blocks, because link state
is autocorrelated and there are only ~5 runs per terrain. All results below
are the **`excite`** policy, where the UAV's moves are random excitation
independent of link state — under `two_phase` the UAV moves *because* the
link is bad, so that estimate is confounded and is reported separately in the
CSV, never pooled.

##### (a) The effect is real and it is terrain-dependent

Effect of a **10 m climb** on C2 availability (+ = the link improved):

| terrain | effect / 10 m | 95% CI | |
|---|---|---|---|
| **open_field** | **+0.1641** | [+0.1248, +0.1984] | significant |
| urban | +0.0370 | [+0.0006, +0.0811] | significant (marginal) |
| hills | +0.0085 | [−0.0162, +0.0314] | n.s. |
| **forest** | **−0.0120** | [−0.0474, +0.0187] | n.s. |

The point estimates reverse sign between open terrain and forest. But the
claim is **not** "climbing hurts in forest" — forest's arm does not clear
zero on its own. The claim is that **the effect is not the same in both
places**, and that is a difference, so it is tested as one:

| contrast | difference / 10 m | 95% CI | p |
|---|---|---|---|
| **open_field − forest** | **+0.1761** | [+0.1245, +0.2270] | **< 0.0001** |
| open_field − hills | +0.1556 | [+0.1093, +0.2008] | < 0.0001 |
| open_field − urban | +0.1271 | [+0.0700, +0.1795] | < 0.0001 |
| urban − forest | +0.0489 | [−0.0018, +0.1005] | 0.065 |
| hills − forest | +0.0205 | [−0.0207, +0.0603] | 0.351 |

**Established:** altitude helps enormously in open terrain and does not help
in any structured terrain — open_field differs from every other terrain at
p < 0.0001. **Not established:** that forest is individually negative, or
that the three structured terrains differ from one another. The honest
statement of the finding is *open vs structured*, not a four-way ordering.

Corroborated independently by the explicit `climb` vs `descend` actions
(≈450–570 of each per terrain), which never enter the regression above:

| terrain | mean Δ on climb | on descend | contrast |
|---|---|---|---|
| open_field | +0.0071 | −0.0264 | **+0.0335** |
| hills | +0.0055 | −0.0204 | +0.0258 |
| urban | −0.0022 | −0.0259 | +0.0236 |
| **forest** | **−0.0084** | −0.0023 | **−0.0061** |

Forest is the one terrain where climbing is worse than descending — the same
conclusion by a third route.

**A negative result worth recording:** downlink BLER shows essentially no
altitude response in any terrain (all effects ≈ 1 × 10⁻⁴, all n.s.). The
altitude effect lives in C2 availability and ping loss, not in the
block-error rate. This sits beside Phase 2's finding that *BLER volatility*
is the strongest single predictor: BLER volatility signals **when** a link is
deteriorating; altitude governs **whether** it does, through a path the mean
BLER does not express at cycle granularity.

##### (b) A terrain-blind model recovers it

The Phase 2 `radio_blind` model, h = 60 s, trained leave-one-terrain-out —
so for each terrain the scoring model **never saw that terrain** and was
never given the terrain label at all. Its predicted failure risk is put
through the identical first-difference regression. Inverted so that, as
above, **+ = the model expects the link to improve**:

| terrain | measured | model-recovered | 95% CI (recovered) |
|---|---|---|---|
| open_field | +0.1641 | **+0.0379** | [+0.0283, +0.0475] |
| hills | +0.0085 | +0.0044 | [−0.0032, +0.0118] |
| forest | −0.0120 | **−0.0232** | [−0.0342, −0.0130] |
| urban | +0.0370 | **−0.0140** | [−0.0239, −0.0039] |

Pearson **r = +0.901**, Spearman **+0.800**, sign agreement **3 / 4**.

And the same interaction test on the recovered effect:

| contrast | recovered difference | 95% CI | p |
|---|---|---|---|
| **open_field − forest** | **+0.0610** | [+0.0455, +0.0769] | **< 0.0001** |
| open_field − urban | +0.0518 | [+0.0371, +0.0668] | < 0.0001 |
| open_field − hills | +0.0335 | [+0.0212, +0.0471] | < 0.0001 |
| hills − forest | +0.0276 | [+0.0151, +0.0409] | < 0.0001 |
| urban − forest | +0.0092 | [−0.0071, +0.0248] | 0.251 |

**A model that never saw the terrain, and is forbidden the terrain label,
independently reproduces the open-vs-structured split at p < 0.0001 —
including forest's negative sign, which the direct measurement could not
establish on its own.** That is the rediscovery the project set out to
demonstrate.

##### What this does not show

Three limits, stated because the result is strong enough not to need
overselling:

1. **Urban is wrong.** Measured +0.0370, recovered −0.0140 — opposite signs,
   both nominally significant. The model is confidently mistaken about one
   terrain in four. Urban is also the terrain where Phase 1 found range
   carries almost no signal (coef +0.066) and where the §3 static-vs-nomadic
   effect was smallest (−8.5 pp), so it is the least characterful terrain in
   the set; that is an explanation, not an excuse.
2. **The correlation is across four points.** r = +0.90 on n = 4 terrains is
   not strong evidence by itself (Spearman 0.80 at n = 4 is not significant).
   The per-pair interaction tests, each built on thousands of cycle
   transitions, are what carries the claim — the correlation is a summary,
   not the evidence.
3. **Magnitudes are not comparable, only signs and ordering.** The recovered
   effects are ~4× smaller than the measured ones because the model outputs
   P(down at t + 60 s) while the measurement is a contemporaneous change in
   availability. Nothing should be read into the compression.

#### 4.3.10 Phase 3 — FAST-HORIZON FORECASTER, DONE (2026-10-26 21:05)

`scripts/ml/fast_horizon.py` → `logs/ml/phase3_baselines.csv`,
`phase3_baselines_perfold.csv`, `phase3_importance.csv`. Dataset B: the four
overnight recovery runs, 10 Hz, **862,696 robot-ticks**, genuine UE-measured
SS-RSRP parsed from the UE PHY log (`RE_SS_COMBINED`) — not computed from the
channel model. 4.91% of ticks are stale (`n_samples == 0`) and are excluded
from labels.

**The task:** will this robot's RSRP be below the −68 dBm floor in *h*
seconds, at h = 1, 3, 5, 10 s? Reported on the **early-warning** subset —
ticks currently *above* the floor — because that is the question the UAV
needs answered. On that subset **persistence is useless by construction**
(every row is currently fine, so "no change" predicts no crossings at all;
PR-AUC equals the base rate and ROC-AUC is undefined). The bar is instead
two physical baselines: **margin to the floor** and **linear extrapolation of
the RSRP slope** over the last second.

##### A useful engineering result that needs no model at all

The two baselines swap places with horizon:

| h | crossings | extrapolate | margin_now |
|---|---|---|---|
| 1 s | 0.468% | **0.221** (47.3×) | 0.097 (20.8×) |
| 3 s | 0.986% | 0.111 (11.3×) | 0.109 (11.0×) |
| 5 s | 1.198% | 0.050 (4.2×) | **0.112** (9.4×) |
| 10 s | 1.487% | 0.037 (2.5×) | **0.113** (7.6×) |

**Short-horizon warning is a trend problem; long-horizon warning is a level
problem.** Below ~3 s, use the RSRP slope; above it, use the margin to the
floor. Extrapolation collapses from 47× to 2.5× no-skill while the margin is
almost flat. In hills at h = 1 s extrapolation alone reaches PR-AUC 0.750.
This is directly implementable and is arguably the most useful thing in this
section.

##### The learned model: wins within a terrain, fails across terrains

Per-fold PR-AUC against the better of the two baselines *on the same fold*
(pooled baselines would not be comparable — fold base rates differ 8×):

| split / fold | h=1 | h=3 | h=5 | h=10 |
|---|---|---|---|---|
| **by_time** (all terrains seen) | **+0.075** | **+0.024** | **+0.034** | **+0.022** |
| LOTO urban | **+0.032** | **+0.047** | **+0.029** | **+0.008** |
| LOTO forest | +0.011 | −0.036 | −0.060 | −0.050 |
| LOTO hills | −0.183 | −0.069 | −0.013 | −0.010 |
| LOTO open_field | −0.019 | −0.012 | −0.008 | −0.009 |

**9 of 20 cells.** But the pattern is not noise: the model wins **4/4** when
trained and tested within the same runs, wins **4/4** on urban, and loses
almost everywhere else. The honest conclusion is the negative one:

> **At fast horizons, a learned terrain-blind model does not beat simple
> physical baselines across terrain. Within a terrain it consistently does.**

The cause is almost certainly **one run per terrain** (§4.3.4): a LOTO fold
here is three UAV trajectories training and a fourth, in an unseen terrain,
testing. The model fits trajectory, not terrain. The physical baselines
transfer perfectly because they are not learned. Note this does **not**
contradict Phase 2 — there, 20 runs across 4 terrains *did* transfer and beat
persistence. The difference is data diversity, not timescale; but with the
data in hand those two explanations cannot be fully separated, so the claim
is left at the weaker form.

**Concrete fix, if this is to be strengthened:** the recovery campaign was one
90-minute `excite` run per terrain. Three or four debug-logged runs per
terrain, with different seeds, would turn this from a negative result into a
real test. That is ~18 hours of overnight runs and no configuration change
beyond `phy_log_level`.

##### The urban question from §4.3.9 — looked at, not resolved

Phase 4's one failure was urban: measured C2 effect **+0.0370**, model-implied
**−0.0140**. Dataset B gives the first chance to measure the altitude effect
on **received power directly**, at 10 Hz and full precision, instead of
inferring it from cycle-level C2:

| terrain | effect of a 10 m climb on RSRP | 95% CI |
|---|---|---|
| open_field | **+1.71 dB** | [+0.57, +2.92] |
| forest | +1.50 dB | [−2.33, +5.60] |
| hills | +0.81 dB | [−1.02, +2.63] |
| **urban** | **−0.38 dB** | [−3.17, +2.20] |

Urban is the only terrain with a negative point estimate — **the same sign the
terrain-blind model reported and the opposite of the cycle-level C2
measurement.** So the model's "error" may not be one. It cannot be called a
vindication either: the urban CI comfortably spans zero, and this is a single
90-minute run.

**The more interesting observation is forest:** +1.50 dB on RSRP (positive)
against −0.0120 on C2 availability (negative). Received power and link
availability do not order the terrains the same way. **The Phase 4 sign flip
is therefore a link-availability phenomenon, not a pure path-loss one** —
something above the physical layer (scheduling, HARQ dynamics, retransmission
behaviour) carries part of it. That is consistent with Phase 2 finding BLER
*volatility* the strongest predictor while mean BLER showed no altitude
response at all, and it is a better research question than the urban
discrepancy it came from.

#### 4.3.11 Phase 5 — PLANNER, DONE (2026-10-26 22:20)

`scripts/ml/planner.py` → `logs/ml/phase5_planner.csv`. The only part of the
project that emits a **control output** rather than a number.

At each decision point it scores 45 candidate waypoints — vertical moves of
{−10, −5, 0, +5, +10} m crossed with horizontal moves of 0 or 15 m on 8
headings — and picks the one minimising a **soft-min over robots**:

    J(u) = (1/β) · log Σᵢ exp(β · riskᵢ(u))        β = 10

which approaches "protect the worst-connected robot" as β grows, against
β = 0 meaning "minimise mean risk". The surrogate is the Phase 2 forecaster
**trained leave-one-terrain-out**, so the model planning in a terrain has
never seen that terrain and never receives the terrain label: terrain
blindness holds all the way through to the control output. Geometry is
recomputed exactly at each candidate (both identities reproduce the logged
values to ~0.03 m, verified first); radio and link history are held fixed,
because moving the UAV does not rewrite the past.

##### The test: does it rediscover the Phase 4 policy?

Mean chosen altitude change, by terrain, never having been told the terrain:

| terrain | planner Δz | Phase 4 measured | Phase 4 recovered |
|---|---|---|---|
| open_field | **+6.28 m** | +0.1641 | +0.0379 |
| hills | +5.03 m | +0.0085 | +0.0044 |
| forest | +3.35 m | −0.0120 | −0.0232 |
| urban | +2.60 m | +0.0370 | −0.0140 |

Against the **model-recovered** effect: Pearson **+0.924**, Spearman
**+0.800**. Against the **measured** effect: Pearson +0.703, Spearman +0.400.

**The agreement with the recovered effect is close to circular** and must not
be reported as independent confirmation — the planner is built on the same
surrogate family that produced the recovered effect, so it would be strange
if they disagreed. What is not circular is that the ordering **survives the
optimisation step**: going from "what does the model think a climb does" to
"what waypoint does an argmin over 45 candidates choose" preserves the
gradient rather than washing it out.

Restricting to the modal 30–40 m altitude band, where all four terrains have
most of their decision points and no clamping against the 60 m ceiling
interferes, the separation is sharper:

| terrain | mean Δz (30–40 m band) | chose to climb |
|---|---|---|
| open_field | +6.24 m | 89.0% |
| hills | +5.59 m | 95.1% |
| forest | +1.91 m | 42.4% |
| urban | +1.10 m | 28.3% |

In open terrain the planner climbs almost always; in forest and urban it
climbs less than half the time, at its usual operating altitude, having been
told nothing about either.

##### What it does not do

**The planner never prefers net descent in any terrain.** Mean Δz is positive
everywhere — it reproduces the Phase 4 *gradient* but not its *sign*. Forest,
the one terrain where the measured and recovered effects are both negative,
still gets +3.35 m overall. The honest claim is "it climbs much less where
climbing does not help", not "it learned that climbing hurts in forest".

**The worst-robot framing is not decoration, but it is not dominant either.**
Soft-min (β = 10) and mean-risk (β = 0) choose an identical waypoint in
**60.8%** of decisions, so the objective changes the control output about two
times in five.

**Reported Δz overstates at the ceiling.** The logged value is the candidate
*offset*, while the applied move is clipped to the 20–60 m envelope, so
decisions taken above ~50 m report a larger climb than was actually applied.
This affects the 50–60 m bin only and not the 30–40 m table above.

##### The limitation that bounds all of the above

The surrogate is an **observational** model: it learned from waypoints the UAV
actually visited, not from a randomised trial over the candidate grid.
Scoring a waypoint the UAV did not take is extrapolation, **11–13% of
candidates fall outside the training support** at every decision point, and
none of these numbers are causal effect estimates. The `excite` policy helps
— it moved the UAV quasi-randomly, which is why the support is as broad as it
is — but it does not make the query causal.

**What would make this a real result:** fly the planner in the simulator and
compare realised worst-robot connectivity against the existing `excite` and
`two_phase` policies, same seeds, same worlds. That is a configuration change
and roughly an overnight campaign. **Not done, and not to be started without
asking** — §5 and the project rules both require it.

### 4.4 Thresholds to re-derive

**Done — the existing floor holds.** Re-derived 2026-10-26 from **4,412
pooled (RSRP, C2) observations** in the recovery runs, which carry
full-precision RSRP from 4 robots (the old −68 dBm came from 5,854
1 dB-truncated observations from 2 robots):

| Floor | C2 below | C2 above | Gap | % rows below |
|---|---|---|---|---|
| −70 dBm | 0.7% | 80.4% | 79.7 | 3.3% |
| **−68 dBm (current)** | **1.7%** | **81.0%** | **79.2** | **4.0%** |
| **−66 dBm (best)** | **2.2%** | **82.0%** | **79.8** | **5.2%** |
| −60 dBm | 11.8% | 85.6% | 73.8 | 10.5% |

−66 dBm separates marginally better (79.8 pp vs 79.2 pp) and leaves more rows
below the floor, which helps the soft-min objective. The difference is small
enough that **−68 dBm is defensible unchanged**; switching to −66 is a
judgement call, not a correction. Either way the archive-derived value is
**validated** against independent, higher-quality data — a good result.

---

## 5. Open items and risks

| Item | Status | Risk |
|---|---|---|
| **Measured RSRP/SINR empty, whole campaign** | ⚠️ **unrecoverable for rounds 1–3** | `phy_log_level` was set to `"error"` on 2026-10-23 11:30, six hours before round 1. OAI emits SS-RSRP only under `LOG_D(PHY,…)`, so `"debug"` is the only level that produces it. All `*_fastradio.csv` (1.56 M rows) and the `ss_*` columns of every per-cycle CSV are empty, with `ss_stale = 1` throughout. **Costs the fast horizons (1/3/5/10 s) and the brief's RSRP-margin objective** — the only surviving RSRP, `rsrp_computed_dBm`, is a forbidden input. |
| **CSI side-car** | ✅ running since 14:17 | Partial recovery. The UE logs its CSI report (`SINR`, `CQI`) at ~7 Hz at the *current* log level, so `scripts/csi_sidecar.py` samples it at 5 Hz into `logs/csi_<ts>.csv` — no OAI change, no restart. Covers round 4 from urban onward (~9.5 h). **Stop it when the campaign ends**, or it fills with empty rows. |
| **`ros_ws/scripts/` duplicate** | 40-file copy of `scripts/` | **Trip hazard.** `scripts/` was found moved into `ros_ws/` on Sat; the running campaign survived only because Python had already loaded the file — the next run would have failed to start. Restored and verified. The duplicate is left as a fallback, but editing *it* changes nothing. Decide whether to delete after the campaign. |
| **Console shows `TCP= n/a`** | cosmetic only | No data impact. The console prints the iperf column, which is empty by design in video mode (`probe_skipped=3`). The real figure is `*_vid_goodput_mbps`. Fix deferred rather than edit the live driver. |
| **Forest confound** | ✅ **resolved** | Round 3 reproduced it (−42.1 with a +14.6 m offset) and distance-matched pooling gives −42.9 in the 40–60 m band. The penalty is movement, not range. Safe to quote. |
| **4-robot vs 2-robot not comparable** | by design | Four UEs share one gNB, so scheduling contention differs from the published 2-robot SATNAC baseline. Treat as separate experiments in the paper. `SAR_ROBOTS=alpha,bravo` reproduces the old configuration exactly. |
| **Cell capacity ceiling ~18 Mb/s aggregate** | measured, immovable | Not a 5G configuration limit — the gNB runs MCS 28 / 64QAM / 106 PRB at 23–25 dB SNR with zero BLER. The binding constraint is the rfsimulator RU thread at 93% of one core. Per-robot throughput of 15 Mb/s is unreachable here; more needs fewer robots or real radio hardware. Report results as *fraction of offered load served*, not absolute Mb/s. |
| **Traffic is deliberately capped** at 1.5 Mb/s/robot video | calibrated | Uncapped traffic is *worse*, not more realistic: greedy TCP collapses the link to 2.37 Mb/s mean and 15% C2, versus ~6 Mb/s and 82–100% capped. At saturation the keep-connected objective is pinned at DISCONNECTED and the planner has no headroom to optimise. |

---

## 6. Timeline to Monday

| When | What |
|---|---|
| Sun 23:51 | ✅ Campaign complete — 16 world-runs, 4 rounds, all `rc=0` |
| Sun 23:59 | ✅ Smoke test confirmed SS-RSRP flowing at `phy_log_level="debug"` |
| Mon 06:07 | ✅ RSRP recovery complete — 4 terrains, all `rc=0`, configs restored |
| Mon 06:10 | ✅ §4.2 validation run across all 20 world-runs — all pass |
| Mon 06:15 | ✅ RSRP floor re-derived — −68 dBm validated (§4.4) |
| **Mon 10:00** | **Begin training** on 13,665 cycles / 2.14 M fast-radio samples / 5,317 actions |

Nothing is outstanding before 10:00. §4.3 is the work that starts then.

### 4.3.12 Phase 5 fly-off — the planner flown live against the excitation baseline

**Run.** `run_planner_flyoff.sh`, 4 worlds x 5400 s, 2026-10-26 22:29 -> 2026-10-27 04:37, all
`rc=0`. Seeds 1792965139-142 reused from the `excite` campaigns of the night before
(20261025_235902, 20261026_013033, 20261026_030216, 20261026_043517). Each world flew a
surrogate **trained on the other three terrains only** — confirmed in every run log
(`[planner] surrogate held out <terrain>; 79 features, 45 candidates`), zero
`planner unavailable`, zero ingest errors. The planner is never told the terrain.

**Raw availability is CONFOUNDED — do not quote it.** Time-weighted worst-robot C2
(all four robots up): open_field 41.5 vs 41.9, urban 54.0 vs 12.5, forest 1.4 vs 2.7,
hills 93.2 vs 72.9. Two confounds make these uninterpretable as a causal effect:

1. **Altitude-ceiling saturation.** The planner pins to the `Z_MAX = 60 m` cap inside the
   first quintile of every run and stays there 74-96 % of the time (mean z 54-59 m vs the
   baseline's 34-42 m). The offline per-decision preference was terrain-differentiated
   (mean dz open_field +6.28 m vs forest +3.35 m, §4.3.11) but **positive everywhere**, so
   integrated over ~180 decisions it reaches the cap in all four terrains and the
   differentiation is destroyed. The flown policy degenerates to "climb to the ceiling".
2. **The robot walk is closed-loop on the UAV, so a replayed seed does NOT replay the
   robot paths.** `scripts/auto_patrol.py:2415-2460`: the hard teleport guard
   (`ploss >= PLOSS_TP_DB`), the far-guard (`r > r_max + 5`, measured from the *UAV*) and
   the soft steer-to-gNB guard all branch on link state, and the first two draw from
   `rng_bot` and `continue`. Changing the gNB policy changes ploss, changes which branch
   fires, changes how far the shared stream is consumed. Measured guard teleports
   planner vs excite: forest 400 vs 219, hills 6 vs 54, urban 16 vs 18, open_field 0 vs 0.
   Robot-x correlation against the matched-seed baseline is +0.01 to +0.67 — i.e. not
   matched. **The docstring at `auto_patrol.py:2049-2053` claims a replayed seed
   reproduces the same robot motion under two policies; that is true only while no guard
   fires, which is exactly not the stressed case.** Treat these as two independent runs of
   a closed-loop system, never as a paired comparison.

**The defensible result: P(C2 up) at matched robot geometry.** Binning on `d_horiz` and
reweighting the planner onto the baseline's exposure removes the "robots happened to be
nearer" confound. Block bootstrap, BLOCK = 20 cycles, 600 reps:

| terrain | matched delta | 95 % CI | Phase 4 10 m-climb effect |
|---|---|---|---|
| open_field | **+17.3 pp** | [+12.3, +22.1] * | +0.1641 |
| urban | **+17.4 pp** | [+8.3, +26.1] * | +0.0370 |
| hills | +3.5 pp | [-0.5, +7.3] | +0.0085 |
| forest | **-12.0 pp** | [-21.5, +1.6] | -0.0120 |

**Sign agreement with Phase 4 is 4/4; Spearman +0.800, Pearson +0.712.** This is an
independent, live, closed-loop reproduction of the sign flip: the ordering
open_field ~ urban > hills > forest recovered from a policy that was never told the
terrain and whose surrogate never saw it. open_field and urban exclude zero; hills and
forest do not.

**Forest is the honest negative, and it is the predicted one.** The planner climbed to the
ceiling in forest too — exactly the move Phase 4 says is harmful there — and was punished
for it, -12.0 pp. The sign is right; the policy did not act on it. This is the clearest
statement of what Phase 5 did and did not achieve: **the surrogate learned the sign flip,
the planner failed to exploit it**, because a uniformly positive dz saturates the box.

**Overlap caveats.** Geometry overlap is partial: hills compares mostly in 25-50 m
(planner) against a baseline spread over 0-150 m; forest overlaps only below 50 m. Channel
drift between the two nights was checked at matched (z, d_horiz) cells — urban 9.66 vs
11.23 dB, hills 8.94 vs 8.56 dB; open_field and forest had no cell with >= 30 samples on
both sides. Urban's 1.6 dB gap is the weakest link in the biggest claimed win.

**What this implies for the next iteration.** The fix is not a better surrogate — it is the
objective. A planner whose per-decision dz is positive everywhere cannot express "do not
climb here" once it is at the cap; it needs either an explicit cost on altitude, a
risk-vs-z argmax over the absolute band rather than a relative offset, or a wider Z_MAX so
the ceiling stops binding. Until then the flown policy is weaker than the offline one.

### 4.3.13 Phase 5 fly-off rounds 2 and 3 — fixing the objective

**Round 2 is discarded in full.** The objective change was right (absolute altitude
targets by global argmin, replacing the relative hill-climb) but the flight code
carried a reference-frame defect of mine: `OnlinePlanner.choose()` rate-limited its
step against the buffered research-CSV row while `_planner_pilot` applied that step to
the UAV's live pose. Measured mean gap 23.5 m, 85.4 % of decisions off by >5 m. A
relative "climb 10 m" is self-consistent in either frame, so round 1 is unaffected; a
move toward an ABSOLUTE target inverts sign whenever the frames disagree. 57.6 % of
forest's commanded targets were clamped to 20 or 60 m, altitudes the 25-55 m grid
cannot produce. Fixed by threading the live pose through `choose()`
(`scripts/ml/online_planner.py`), and guarded by `scripts/ml/test_planner_loop.py`,
which closes the loop the pilot closes. Negative control on the old path, forest from
60 m: 87.7 % of altitudes on a geofence bound, 85.4 % of steps moving AWAY from the
chosen target, tracking error 17.95 m; fixed path, same data, 1.01 m. No open-loop
replay could see this, and every one run before round 2 passed.

**Round 3 (2026-10-27 18:29 -> 2026-10-28 00:37, 4 x 5400 s, all rc=0).** Zero
commanded targets outside the grid in 697 decisions; zero time above 58 m anywhere.

| terrain | mean z | sd | % at grid top (55 m) | round 1 mean z | round 1 % >= 58 m |
|---|---|---|---|---|---|
| hills | 53.4 | 3.25 | 74.1 % | 58.8 | 89.7 % |
| open_field | 51.8 | 5.29 | 68.2 % | 59.2 | 95.9 % |
| urban | 48.3 | 7.23 | 48.9 % | 56.7 | 82.6 % |
| forest | **40.9** | **2.19** | **0.0 %** | 54.2 | 73.8 % |

**The ratchet is gone** and forest holds a genuine interior set point for 90 minutes
(quintiles 40.3, 41.0, 40.9, 40.4, 41.8 m), 7.4 m clear of the next terrain.

**P(C2 up) at matched robot geometry, block bootstrap (BLOCK = 20, 600 reps):**

| terrain | vs round-1 planner | vs excitation |
|---|---|---|
| forest | **+15.4 pp** [+8.3, +23.7] * | +2.6 pp [-10.4, +18.8] |
| urban | **+5.3 pp** [+0.7, +10.0] * | **+23.7 pp** [+15.9, +32.4] * |
| open_field | -3.1 pp [-8.1, +1.1] | **+12.8 pp** [+7.8, +18.5] * |
| hills | -0.9 pp [-2.9, +0.6] | **+4.2 pp** [+1.5, +6.7] * |

**The fix helped exactly where it was predicted to and nowhere else.** Against round 1
it is significantly positive in forest (+15.4 pp, recovering round 1's -12.0 pp loss)
and urban, and indistinguishable from zero in open_field and hills — the two terrains
where climbing was already the right answer and the objective change should therefore
change nothing. Against excitation, all four terrains are now positive (three
significant), where round 1 was significantly NEGATIVE in forest.

**Where the sign-flip evidence now lives — this is the subtle part.** Round 1
reproduced the sign flip *in availability*, 4/4, by being PUNISHED in forest. Round 3
no longer climbs in forest, so it is no longer punished there, and the availability
agreement drops to 3/4 (forest +2.6 instead of -12.0). The two cannot both hold: a
planner that correctly declines to climb in forest stops demonstrating the cost of
climbing in forest. In round 3 the sign flip shows in the **chosen altitude** (forest
40.9 m against 48-53 m elsewhere), not in an availability penalty. The script's
"vs round-1" correlation against the Phase 4 climb effect is -0.677 / Spearman -0.800,
and that NEGATIVE sign is the correct, desirable signature of a fix that repairs the
terrain where climbing was wrong — it is not a sign-flip test and must not be read as
one.

**Honest weakness of round 3.** Three of four terrains spend most of the run at the TOP
of the candidate grid (hills 74.1 %, open_field 68.2 %, urban 48.9 % at 55 m). The
ceiling problem is therefore not eliminated, only moved from 60 m to 55 m, and for
those three the binding constraint is now the support-based grid restriction rather
than the surrogate's own preference. Only forest chooses an interior set point. The
altitude ordering (hills > open_field > urban > forest) ranks hills above open_field,
against Phase 4, giving Spearman +0.4 on mean altitude versus round 1's +0.8 on
availability. Forest-lowest holds; a four-way ordering does not — consistent with
Phase 4 never having supported more than open-vs-structured.

**Forest detail.** Worst-robot availability (all four robots up) is ~0 % in forest for
every policy, which is arithmetic rather than failure: per-robot availability is
15-51 % and the joint event is vanishingly rare. On mean robots connected, round 3 is
the best of the three — **1.49 / 4**, against round 1's 0.85 and excitation's 1.36.

**Still open.** The cross-night drift caveat of §4.3.12 applies unchanged; the
drift-free evidence remains within-run altitude behaviour. A fourth round widening the
grid back toward 60 m, with the surrogate retrained on data that actually covers
55-60 m, is the obvious way to test whether the three terrains at the grid top want to
go higher — not yet run, and not proposed without more data up there first.

---

*First written 2026-10-25 01:15; updated Sun 12:55, Mon 06:15, Mon 11:10, Tue 09:40 and Wed 00:45. Every figure in §2.6 and §3 is
computed directly from the campaign CSVs — nothing is estimated except the
completion times in §2.3 and §6, which extrapolate the observed 3 h 22 m
per-world duration, and the round-4 projections in §3.1, marked with ~.*
