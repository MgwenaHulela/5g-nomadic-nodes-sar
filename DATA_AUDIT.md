# DATA_AUDIT.md

Audit of `/home/innocentmsc/sar_sim` performed 2026-10-16, before any model code
was written (brief §3). Every number below was computed from the files named;
nothing is carried over from the draft companion paper.

Reproduce with the scripts in §9.

---

## 1. What is in this directory

| Path | What it is |
|---|---|
| `NOMADIC5G_TERRAIN_RESULTS_2026/` | **The curated dataset.** 5 per-terrain CSVs + `summary_stats.json` + `README.txt` |
| `logs/campaign_*/` (9 dirs) | Raw campaign output: per-world CSV, `*_research.log`, `*_stackup.log`, `manifest.csv`, `campaign.log` |
| `logs/patrol_*.csv` (~70 files) | Pre-campaign ad-hoc patrol runs (Jul 8–13), 0–380 KB, inconsistent schemas |
| `logs/analysis_*/`, `logs/paper_figs/` | Figure/analysis output from the SATNAC paper |
| `logs/channel_bridge_log.csv` | **2.4 GB**, 13.03 M rows, 0.5 s cadence channel-bridge trace (see §5) |
| `channel_bridge.py` | Gazebo→OAI telnet path-loss injector; per-world channel models; AR(1) shadowing |
| `scripts/auto_patrol.py` (1966 ln) | Robot patrol + `--research` two-phase experiment driver |
| `scripts/metrics_collector.py` | Parses RSRP/SINR/BLER out of OAI UE logs (see §4.3) |
| `run_research_campaign.sh` | Campaign orchestrator, one world at a time, PID-file lock |
| `worlds/sar_{open_field,urban,forest,hills,indoor}.sdf` | The 5 Gazebo worlds |
| `worlds/{urban_buildings,forest_trees,terrain_hills,indoor_building}.py` | Per-world geometry → LoS-block + excess-loss models |
| `docs/paper/` | SATNAC paper sources (`satnac_final.tex`, PDFs) — untracked by git |
| `ml/`, `results/` | **Do not exist yet** |

Dependencies already installed: pandas 2.3.3, numpy 1.26.4, scikit-learn 1.7.2,
lightgbm 4.7.0, xgboost 3.2.0, torch 2.14.0, gymnasium 1.3.0,
stable-baselines3 2.9.0. **`onnx` is missing.** `nvidia-smi` fails with a
driver/library version mismatch, so **plan for CPU-only training**.
Disk: 237 GB free on `/`.

### Provenance is verified
MD5 checks confirm the bundled dataset is byte-identical to the campaign output
the `README.txt` names:

| Bundled file | Identical to |
|---|---|
| `open_field_results.csv` | `logs/campaign_20260728_210058/sar_open_field.csv` |
| `urban_results.csv` | `logs/campaign_20260729_122440/sar_urban.csv` |
| `forest_results.csv` | `logs/campaign_20260729_122440/sar_forest.csv` |
| `hills_results.csv` | `logs/campaign_20260729_122440/sar_hills.csv` |
| `indoor_results.csv` | `logs/campaign_20260730_112334/sar_indoor.csv` |

One extra unused run exists: `logs/campaign_20260728_210058/sar_urban.csv`
(253,903 B, a second urban 6 h run) was **not** bundled. It is a candidate for
extra training data.

---

## 2. Measurement schema (per cycle)

65 columns, one row per measurement cycle. Layout: shared fields, then a
mirrored `a_*` / `b_*` block per robot (alpha = stronger link, bravo = weaker).

- **Time/phase:** `timestamp` (UTC, ISO8601), `t_elapsed_s`, `phase` (1 = static gNB, 2 = nomadic gNB)
- **Geometry:** `uav_x_m/uav_y_m/uav_z_m`, `ax_m/ay_m`, `bx_m/by_m`, `{a,b}_d_horiz_m`, `{a,b}_d_slant_m`
- **Simulator ground truth (NOT model inputs):** `{a,b}_ploss_dB`, `{a,b}_rsrp_computed_dBm`
- **Radio:** `{a,b}_ss_rsrp_dBm`, `{a,b}_ss_sinr_dB`, `{a,b}_pbch_rsrp`, `{a,b}_dl_bler`, `{a,b}_dl_harq_ok/nok`, `{a,b}_ul_bits_per_sym`, `{a,b}_ul_code_rate`, `{a,b}_ul_nb_rbs`, `{a,b}_ul_nb_sym`
- **Traffic:** `{a,b}_tcp_tp_mbps`, `{a,b}_tcp_retransmits`, `{a,b}_udp_tp_mbps`, `{a,b}_udp_jitter_ms`, `{a,b}_udp_lost_pct`
- **Latency/C2:** `{a,b}_rtt_min/avg/max_ms`, `{a,b}_ping_loss_pct`, `{a,b}_c2_rtt_ms`, `{a,b}_c2_loss_pct`
- **Events:** `{a,b}_event` ∈ {reattaching, rlf_recovered, reattach_failed}, `{a,b}_outage_s`

**Not present, and needed per brief §6.4:** `seed`, `run_id`, `terrain`, and an
explicit `action` label. Terrain is only encoded in the filename.

### Terrains, phases, rows

| Terrain | Rows | Phase 1 (static) | Phase 2 (nomadic) | Wall-clock | Phase-2 UAV path |
|---|---|---|---|---|---|
| open_field | 733 | 354 | 379 | 6.01 h | 9,885 m |
| urban | 721 | 431 | 290 | 6.00 h | 6,856 m |
| hills | 974 | 546 | 428 | 6.01 h | 11,174 m |
| forest | 482 | 325 | 157 | 6.00 h | 3,281 m |
| indoor | **17** | 4 | 13 | **0.25 h** | 97 m |
| **Total** | **2,927** | 1,660 | 1,267 | 24.3 h | |

Phase 1 is exactly 0–10800 s, phase 2 exactly 10800–21600 s, in all four full
runs. In phase 1 the UAV has **exactly one** unique `(x,y,z)` — it is a true
hold at `(0, 0, 40)` (indoor: `z=18`).

---

## 3. The headline result reproduces

Recomputed from the bundled CSVs, C2 available ⇔ `c2_loss_pct < 50`:

| Terrain | bravo (weak UE) P1 → P2 | Δ | alpha P1 → P2 | Δ |
|---|---|---|---|---|
| open_field | 56.5% → 87.9% | **+31.4 pp** | 96.3% → 97.6% | +1.3 pp |
| urban | 95.1% → 94.8% | −0.3 pp | 97.4% → 96.9% | −0.5 pp |
| hills | 98.5% → 92.5% | −6.0 pp | 97.1% → 82.0% | −15.1 pp |
| forest | 94.5% → 72.6% | **−21.9 pp** | 95.7% → 75.2% | −20.5 pp |
| indoor | 25.0% → 15.4% | −9.6 pp | 50.0% → 0.0% | −50.0 pp |

The sign flip is real and present in the data. (The dataset `README.txt` quotes
open-field 56.5% → **88.7%**; I get **87.9%** with the `c2_loss_pct < 50` rule.
Minor, but the README's exact rule is undocumented — worth pinning down before
any number is quoted in the paper.)

---

## 4. Data problems

### 4.1 BLOCKER — the sampling cadence cannot support the intended forecaster

Median inter-cycle interval over the whole dataset is **22.8 s**.

> A 30 s history window (brief §5.1) spans **≈1.3 rows**.
> Horizons of 1, 3, 5, 10 s (brief §5.2) are all **shorter than a single cycle**.

Per terrain/phase median cycle time:

| Terrain | Phase 1 | Phase 2 | Max gap |
|---|---|---|---|
| open_field | 23.5 s | 21.9 s | 104 s |
| urban | 19.8 s | 35.2 s | 93 s |
| hills | 19.0 s | 20.6 s | 91 s |
| forest | 27.1 s | **62.8 s** | 171 s |
| indoor | 74.5 s | 50.2 s | 97 s |

The existing per-cycle logs can support forecasting at roughly **30 s / 60 s /
120 s** horizons with a window of a few cycles, nothing finer. Fine-grained
work needs new data collection (§6) — **but that collection is far cheaper than
it looks. See §4.1b.**

### 4.1b The radio data already exists at 50 Hz — the collector throws it away

Measured from the OAI UE logs (`/tmp/ue_alpha.log`, 2026-09-09 run, now archived
as `logs/archive/ue_{alpha,bravo}_phy_metrics_20260909.log.gz`):

`nr-uesoftmodem` emits a combined SS-RSRP / SS-SINR line **every 2 NR frames =
20 ms = 50.0 Hz**. Over 4,609 consecutive samples, **4,608 of 4,608 intervals
were exactly 2 frames** — no jitter, no exceptions:

```
[PHY] [UE 0] ssb 0 SS-RSRP: -42 dBm/RE (51.263523 dB/RE), SS-SINR: 51.099998 dB
[PHY] Frame 586, Slot 0, Symbol 5, SSB Index 0     <- then 588, 590, 592, ...
```

Two consequences, both good:

1. **The 1/3/5/10 s horizons of brief §5.2 are reachable without touching the
   OAI or Open5GS configuration.** The bottleneck is not the radio stack, it is
   `metrics_collector.py` sampling this stream once per ~22.8 s traffic cycle
   and carrying the value forward. A separate 1–10 Hz tail-reader on the UE log,
   running alongside the existing traffic cycle, fixes **§4.1 and §4.2 and
   §4.3 at once** — fast radio sampling, decoupled from the slow iperf probe,
   with no carry-forward. This is a new-logger change, not a stack change, so it
   does not need the §13 sign-off that an OAI config change would.

2. **The quantisation in §4.5 is a parser artifact, not an OAI limit.**
   `RE_SS_RSRP` (`metrics_collector.py:35`) captures only the integer
   `dBm/RE` field. In the same 4,609-sample slice the integer field held **4
   distinct values** while the float `dB/RE` in parentheses held **765**:

   | field | regex captures | unique values in slice | range |
   |---|---|---|---|
   | `-42 dBm/RE` | yes | **4** | −45 … −42 |
   | `(51.263523 dB/RE)` | **no** | **765** | 48.2468 … 51.2671 |
   | `SS-SINR: 51.099998 dB` | yes | 22 | 41.10 … 51.10 |

   Capturing the float costs one regex change and recovers roughly two orders of
   magnitude of resolution. The 36-level staircase in §4.3/§4.5 disappears.

Caveat: this was measured on the 2026-09-09 run, not on the July campaign runs,
whose UE logs no longer exist. The emission rate is set by OAI's SSB
periodicity and the config is unchanged between the two, so it should hold —
but it is worth re-confirming on the first new run rather than assumed.

### 4.2 Informative sampling — cycle duration depends on the thing we predict

The cycle is a sequential battery of iperf3/ping probes, so a bad link makes the
cycle take longer. The sampling rate is therefore **correlated with the target**:

- median cycle time with C2 OK: **22.1 s**; with C2 lost: **35.8 s** (alpha)
- Spearman(cycle duration, `c2_loss_pct`) = **+0.20** (alpha), **+0.28** (bravo)
- Spearman(cycle duration, `ss_rsrp_dBm`) = **−0.24** (alpha), **−0.31** (bravo)

Consequences:
1. **This fully explains the "missing nomadic rows" in forest.** Forest phase 2
   has only 157 rows but a complete **3.00 wall-clock hours** — rows are not
   missing, the cycle slowed from 27.1 s to 62.8 s because links were bad. Same
   for urban phase 2 (290 rows, 35.2 s). The brief's §3.3 hypothesis is wrong;
   no data was dropped.
2. Any per-row average silently **under-weights bad periods**. Every aggregate
   in the paper should be recomputed **time-weighted**, not row-weighted.
3. Train/test metrics must be reported per unit time, not per row.

### 4.3 `ss_rsrp` / `ss_sinr` hold constant for long stretches

**Corrected 2026-10-16.** My first reading of this blamed carry-forward in
`scripts/metrics_collector.py:93-95` (`prev.get(...)`). That path exists, but
it is **not** the main cause: `auto_patrol.py:fresh_l1()` re-reads the tail of
the UE log every cycle and *overrides* the windowed parse, a fix made
2026-07-11 for exactly this reason. Carry-forward only survives when that tail
read fails.

The real cause is **1 dB truncation** (§4.1b): `fresh_l1`'s regex captured only
the integer `dBm/RE` field, so a genuinely varying signal was rounded onto a
20-35 level staircase and sat on one level for many cycles. Both mechanisms are
now instrumented and fixed — see §4.1b and §7.

The plateaus are real either way:

Measured fraction of cycles where the value actually changed, and the longest
constant run:

| Terrain | a_ss_rsrp changed | longest hold | b_ss_rsrp changed | longest hold |
|---|---|---|---|---|
| open_field | 88.9% | 1.7 min | 74.2% | 19.4 min |
| urban | 72.4% | 2.4 min | 84.5% | 1.3 min |
| hills | **27.1%** | **44.3 min** | 39.4% | 22.1 min |
| forest | 69.5% | 11.9 min | 66.8% | 10.3 min |
| indoor | 47.1% | 5.9 min | 58.8% | 1.4 min |

`ss_rsrp_dBm` is also integer-quantised: only 20–35 distinct levels per terrain,
37 across the whole dataset, range −78 … −42 dBm.

**Action (done 2026-10-16):** `fresh_l1` now also captures the full-precision
`dB/RE` field, and every writer emits `*_ss_rsrp_dBre`, `*_ss_samples` and
`*_ss_stale` so a carried value can be told from a measured one. The hills run
remains the worst case in the *existing* data — 44 minutes on one held value —
and those rows should be masked, not trusted, when training on it.

### 4.4 `pbch_rsrp` is unusable — drop it

| Terrain | a_pbch_rsrp unique | changed | b_pbch_rsrp unique | changed |
|---|---|---|---|---|
| open_field | 2 | 0.1% | 2 | 0.1% |
| urban | **0 (all NaN)** | 0% | 4 | 0.6% |
| hills | 5 | 0.6% | 2 | 0.1% |
| forest | 2 | 0.2% | 2 | 0.4% |
| indoor | **0 (all NaN)** | 0% | 1 | 0% |

Overall NaN: `a_pbch_rsrp` 57.6%, `b_pbch_rsrp` 31.4%. It carries essentially no
information. Exclude it from the feature set.

### 4.5 `rsrp_computed_dBm` is the injected path loss in disguise — must be excluded

`{a,b}_rsrp_computed_dBm + {a,b}_ploss_dB` is **constant at −37.0 dB**
(11 distinct values spanning −37.05 … −36.95, i.e. rounding only), and
`corr(ploss, rsrp_computed) = −1.000` exactly.

It is an algebraic restatement of simulator ground truth. Under brief §5.1 it is
**forbidden as a model input**, exactly like `ploss_dB`. The honest
measured channel quantities are `ss_rsrp_dBm`, `ss_sinr_dB`, `dl_bler`,
HARQ counts, throughput, RTT and loss.

### 4.6 The −80 dBm RSRP floor is never crossed — RESOLVED, use −68 dBm

**Resolved 2026-10-19.** Derived from 5,854 pooled (RSRP, C2) observations
across all five terrains; regenerate with `python3 scripts/derive_thresholds.py`
and recorded in `configs/experiment.json`.

C2 availability has a clear knee:

| RSRP bin | n | C2 available |
|---|---|---|
| < −75 | 46 | **13.0%** |
| −75 … −70 | 201 | 28.9% |
| −70 … −65 | 178 | 64.0% |
| −65 … −60 | 344 | 73.8% |
| −60 … −55 | 365 | 90.1% |
| > −55 | 4,720 | ~96% |

A floor is useful only if it both **predicts trouble** and **happens**.
Maximising the availability gap alone selects the extreme (at −78 dBm the gap
looks enormous but only 0.2% of rows are below it, leaving the margin term as
degenerate as before). Subject to ≥5% coverage, the best floor is
**−68 dBm**: 34.5% availability below vs 93.8% above, a **59 pp gap**, with
5.6% of rows below. It separates consistently per terrain — forest 65.7/90.8,
hills 19.4/94.6, open_field 18.4/92.5, urban 42.3/97.1 — which is what a
terrain-blind objective needs.

SINR gives a comparable floor at **22 dB** (31.4% vs 93.7%, 62.3 pp, 5.1%
below) and is recorded as the alternative margin basis.

Two caveats carried into the config:
1. Derived from the **integer** dBm field, a 1 dB truncation. `ss_rsrp_dBre`
   now logs full precision; re-derive from new campaign data.
2. **Indoor does not separate** at either floor (8.3% vs 18.2% RSRP) because
   everything there was failing. Recheck after the geometry retune (§7b).

The original finding, for the record:



`ss_rsrp_dBm` minimum across all 2,927 rows is **−78 dBm** (alpha) / **−77 dBm**
(bravo); **0.0%** of rows are below −80 dBm, and every per-terrain 5th-percentile
is ≥ −75 dBm. The margin term in brief §5.3 would be **identically positive** —
a degenerate objective. Either the floor needs to be re-set from the observed
distribution, or the margin must be defined against C2 loss / SINR instead.
This needs a decision before the planner objective is written (§8).

### 4.7 Indoor is not a usable terrain

17 rows, 0.25 h, 4 of them in phase 1. `campaign.log` for
`campaign_20260729_122440` shows `sar_indoor` started at 06:31 on 2026-07-30 and
**never logged a DONE line** — the 6 h indoor run died. The bundled file is a
later 15-minute attempt (`campaign_20260730_112334`). Treat indoor as
**anecdotal**; it cannot carry a leave-one-terrain-out fold, and its
50%/100% C2-loss figures rest on 4 and 13 rows.

### 4.8 No seeds — runs are not reproducible

`grep` finds exactly one seed hook in the whole stack:
`channel_bridge.py --shadow-seed`, and `run_research_campaign.sh` never passes
it. Robot patrol headings come from unseeded `random.random()`
(`scripts/auto_patrol.py:1483-1492`), as does the phase-2 gNB random walk.
**Paired counterfactuals (brief §6.3) are impossible with the current code** —
there is no way to replay a robot trajectory under a different UAV policy.

### 4.9 Clean bills of health

No duplicate timestamps, no duplicate `t_elapsed_s`, no duplicate rows, and
`t_elapsed_s` is strictly monotonic in all five files. Apart from
`pbch_rsrp`, the only NaNs are in `{a,b}_event` / `{a,b}_outage_s`, which are
correctly sparse (they fire only on RLF/reattach).

Rows are **not dropped** during RLF — logging continues and the event columns
are populated in place — but the cycle **stretches** (forest: 55.1 s median on
event rows vs 30.3 s otherwise; hills 35.9 s vs 19.5 s), which is the §4.2 bias
again at its sharpest. Recorded outages run 100–415 s, i.e. several cycles, and
`outage_s` is written once at recovery rather than accumulated per cycle — so
the intervening degraded cycles carry no outage marker and must be
reconstructed from the event sequence when labelling.

| Terrain | a: reattaching / recovered / failed | b: reattaching / recovered / failed |
|---|---|---|
| open_field | 1 / 1 / 0 | 102 / 16 / 14 |
| urban | 0 / 0 / 0 | 0 / 3 / 0 |
| hills | 1 / 9 / 0 | 1 / 3 / 0 |
| forest | 22 / 8 / 6 | 14 / 7 / 2 |
| indoor | 6 / 0 / 2 | 5 / 2 / 1 |

---

## 5. `logs/channel_bridge_log.csv` — 2.4 GB, mostly junk, and mislabelled

13,033,584 data rows, 2026-07-03 → 2026-10-16, ~2 Hz.

### 5.1 A stale bridge had been running for 36 days — RESOLVED 2026-10-16
`python3 -u channel_bridge.py` (PID 177614) had been up **36 days** with **no
Gazebo and no RAN running**, appending ~172,000 rows/day of frozen state
(`uav=(0,0,40)`, constant distances) — identical every row.

- rows before 2026-07-28: 2,190,471 (16.8%)
- rows in the campaign window 07-28…07-31: 663,558 (5.1%)
- **rows after 2026-07-31: 10,179,555 (78.1%) — post-campaign, no simulation attached**

**Done (2026-10-16, on request):** PID 177614 stopped (SIGTERM), the log
confirmed frozen, then moved and compressed to
`logs/archive/channel_bridge_log_20260703-20261016.csv.gz` (2.4 GB → 237 MB,
10.4:1). Verified byte-identical after compression — raw MD5
`2a5626abdb1804294699eb6179720b2e`, 13,034,180 lines, `gzip -t` passes.
`logs/archive/README.md` documents the schema traps in §5.2–5.4 alongside the
file. **No data was deleted.**

`scripts/gazebo_viz.py` (PID 177648), also 36 days stale with no Gazebo
attached, was stopped at the same time. It held no data file — only
`/tmp/gz_viz.log` (47 MB of frozen repeated status lines, scratch output,
left in place).

### 5.2 The header row does not match the data rows
The file's header (written 2026-07-03 under the 12-column schema) is:
```
timestamp,uav_x,uav_y,uav_z,d_alpha_horiz,d_bravo_horiz,pl_alpha_delta,pl_bravo_delta,
ploss_gnb_ue0,ploss_gnb_ue1,ploss_ue_alpha_dl,ploss_ue_bravo_dl
```
but `channel_bridge.py:496-505` now writes **18 columns, interleaved per-UE**:
```
timestamp, uav_x, uav_y, uav_z,
alpha_d_horiz, alpha_delta_db, bravo_d_horiz, bravo_delta_db,
alpha_speed, alpha_ch_type, alpha_forgetfact,
bravo_speed, bravo_ch_type, bravo_forgetfact,
ploss_gnb_ue0, ploss_gnb_ue1, ploss_ue_alpha_dl, ploss_ue_bravo_dl
```
Loading this file naively with `pandas.read_csv` **silently misaligns every
column** — `d_bravo_horiz` picks up `alpha_delta_db`, which is why that field
shows impossible negative "distances" (e.g. −5.46). Any future parser must
ignore the stored header and use the 18-column layout.

Row-length histogram: 10,655 rows × 12 fields (old schema), 13,022,928 × 18,
and **1 row × 24 fields** (a torn concurrent write — must be dropped).

### 5.3 Timestamps are local time, the campaign CSVs are UTC
The bridge writes `datetime.now().isoformat()` — naive **SAST (UTC+02:00)**.
The campaign CSVs are UTC with an explicit `+00:00`. Verified against the urban
run: bridge rows at 15:30 local show a moving UAV, matching urban phase 2 which
starts 13:26 **UTC**. **Joining the two requires a +2 h shift**; joining them
naively lines phase 2 up against phase 1.

### 5.4 What it is actually good for
It gives 0.5 s UAV pose, per-UE horizontal distance, UE speed, TDL channel type
and injected path loss **during the exact campaign windows**. It contains **no
RSRP, SINR, BLER, throughput or RTT**, and its path-loss columns are forbidden
as model inputs. So it can supply fine-grained *geometry and action* context to
join onto the 22.8 s radio cycles, and it is a good source for validating a
channel surrogate — but it cannot by itself close the cadence gap in §4.1.

---

## 6. What the existing data can and cannot support

**Can support now**
- Forecasting at ~30/60/120 s horizons with a 2–5 cycle window, on 2,927 rows
  across 4 usable terrains (2,910 rows excluding indoor).
- The forecaster comparison of brief §7 (persistence / LightGBM / GRU-TCN /
  terrain-given ablation) at those coarser horizons.
- Leave-one-terrain-out over {open_field, urban, forest, hills}.
- The with/without ping-loss-history ablation (§5.2). Note
  `corr(ping_loss_pct, c2_loss_pct)` = 0.37 (alpha) / 0.72 (bravo) — related but
  not identical, so both arms are meaningful.

**Cannot support**
- The 1/3/5/10 s horizons and 10–30 s windows of brief §5.1–5.2 (§4.1).
- Action-conditioned forecasting (brief §5.4). Phase 2 *is* a continuous 3-D
  random walk rather than follow-the-robots — better excitation than the brief
  assumed — but **no action is ever labelled**, and `hold` only ever co-occurs
  with phase 1 at a single fixed pose `(0,0,40)`. Hold and roam are therefore
  perfectly confounded with phase, time-of-run and UAV position. A model cannot
  learn "what would holding do here?" from this.
- Paired counterfactuals, multi-seed confidence intervals (§4.8).
- A variable number of robots — the schema is hard-coded to two (§5.3 of brief).
- Indoor as a terrain fold (§4.7).

**Class balance for the C2 classifier** (`c2_loss_pct ≥ 50`): 6.9% positive for
alpha, 12.0% for bravo over 2,927 rows — roughly 200 and 350 positive events.
That is thin for a per-horizon AUC with confidence intervals, and it is
concentrated in forest phase 2 and open-field phase 1.

---

## 7. Recommended next steps (for your decision — nothing started)

1. ~~**Stop the stale bridge** (PID 177614); archive `channel_bridge_log.csv`
   and let it restart clean.~~ **Done 2026-10-16** — see §5.1. `gazebo_viz.py`
   (PID 177648) stopped as well; no simulator processes remain.
2. **Instrument before collecting.** Add to the per-cycle logger: `seed`,
   `run_id`, `terrain`, `action`, and `*_metric_age_s` per radio metric.
   Decouple the fast radio sample from the slow iperf probe so cadence stops
   depending on link quality (§4.2) — a 1–10 Hz RSRP/SINR sampler alongside the
   existing ~20 s traffic cycle fixes §4.1, §4.2 and §4.3 together. **Per §4.1b
   this needs no OAI config change** — the stream is already there at 50 Hz.
   Capture the float `dB/RE` field while doing it (§4.1b point 2).
3. **Seed everything** — robot headings, UAV walk, shadow fading — so brief
   §6.2/§6.3 become possible.
4. ~~**Excitation runs** (brief §6.1): labelled `hold` at *many* poses, not just
   `(0,0,40)`, otherwise hold stays confounded.~~ **Implemented 2026-10-16** —
   `auto_patrol.py --excite` / `SAR_POLICY=excite`. See §7b. **Not yet run**;
   awaiting a go-ahead on hours.
5. **Decide the RSRP floor** (§4.6) before the planner objective is fixed.
6. **Re-derive the paper's aggregates time-weighted** (§4.2).

### 7b. Excitation policy (implemented, not yet run)

`auto_patrol.py --excite` samples a labelled action, commits to it for a dwell
drawn from 30-90 s (above the 22.8 s median cycle, so most rows sit inside one
action), then repeats. Actions and weights:

| action | weight | effect |
|---|---|---|
| `hold` | 0.30 | stay at the current pose |
| `step` | 0.25 | 5-30 m horizontal step on a random bearing |
| `climb` / `descend` | 0.12 each | 3-12 m altitude change |
| `toward_weak` / `away_weak` | 0.12 / 0.09 | 5-30 m toward/away from the robot at greater slant range |

`toward_weak` uses **slant distance**, not the bridge's `ploss_dB`: a behaviour
policy that consulted simulator ground truth could not be reproduced on
hardware, whereas robot position is ordinary telemetry.

Exact action intervals go to `logs/actions_<run_id>.csv` (one row per action,
with from/to poses), which is what the 10 Hz fast-radio log should be joined
against. The per-cycle CSV's `action` column is only the action in force when
that row happened to land.

**Why the mapping is a module-level function.** `action_to_target()` and
`geofence_target()` are shared deliberately: the excitation policy uses them to
*generate* data and the planner (brief §5.4/§8) needs the identical mapping to
*evaluate* candidate moves. If the two disagreed, the forecaster would be
trained on one action semantics and queried with another.

**A flaw found and fixed during testing.** The first implementation clamped
targets to the geofence. Over 20,000 sampled actions that pinned **23.7% of
poses against the altitude bound and 18.1% against the xy bound** — and worse,
those samples were *mislabelled*: a commanded `climb` at z = 60 m does not
move, but was still logged as `climb`, which would teach an action-conditioned
forecaster that climbing does nothing. The mapping is now bounds-aware —
a climb with no headroom becomes a `descend`, a step into a wall reflects off
it, and a `toward_weak` the box would flatten becomes a `hold`. Re-measured:

| | clamped | bounds-aware |
|---|---|---|
| poses at z bound | 23.7% | **0.3%** |
| poses at xy bound | 18.1% | **5.4%** |
| mislabelled no-op moves | — | **0 / 20,000** |
| distinct poses where `hold` observed | — | **4,229** (existing data: **1**) |

That last row is the whole point of the exercise.

### Pilot result (2026-10-19, `sar_indoor`, seed 4242, 20 min)

First live run of the excitation policy, fast sampler, seeding and hardened
watchdog. Source: `logs/campaign_20261019_123510/`.

| check | result |
|---|---|
| fast sampler rate | **10.00 Hz** (median tick 0.100 s, p99 0.100 s, max 0.108 s, no drift) |
| rows | 12,233 fast-radio / 24 per-cycle / 20 actions |
| per-cycle schema | **78/78 columns**, all new fields populated; `phase`=0 under excite |
| dwell | 20/20 inside the 30-90 s band (31.1-89.6, mean 61.4) |
| mislabelled no-op moves | **0 / 16** |
| geofence | respected on every action |
| RSRP resolution | bravo **5,524 distinct dB/RE vs 26 dBm — 212x**; alpha 399 vs 26 (15x) |

**4.1b confirmed on live data.** The dB/RE field resolves the full 34 dB
indoor cliff; the integer dBm flattens it to 26 levels.

**4.3 refined.** Alpha logged **95.1% stale ticks, max age 1163 s** — without
`n_samples`/`age_s` its RSRP would read as a frozen value for 19 minutes and
look like a *stable* link. So the archive's plateaus (hills, 44 min) are
plausibly stuck UEs as well as 1 dB truncation; the two are now
distinguishable, which they were not before.

**New finding — cycle cadence is BIMODAL, not monotonic.** Median 23.4 s but
max 134.4 s. 4.2 assumed bad links simply slow the cycle; in fact a *fully
dead* link returns immediately (probes fail fast) while a *marginal* one times
out. Any time-weighted correction to the published aggregates must handle both
directions, not just the slow tail.

**Two of this audit's own claims were corrected by the run:**

1. ~~Indoor's C2 loss is the watchdog misreading iperf congestion as RLF.~~
   **Wrong.** Bravo reached **83.3% C2 availability at 19-28 ms** once it left
   the building, proving the probe path is healthy; alpha managed 0/24. Indoor
   is genuinely coverage-limited, as the dataset README said.
2. The `probe_busy` gate added for that theory had a **starvation bug** — it
   cleared the RLF timer on every busy poll, so on a degraded link (where
   probes run to timeout and dominate the cycle) the watchdog could never
   accumulate `RLF_CONFIRM_S`. Fixed: it now skips the measurement and leaves
   the timer running. The run supplied the proof it matters — **7 cycles where
   alpha had good path loss (<35 dB) and still carried zero traffic**,
   recoverable by reattach, with only 2 reattach events in 20 minutes.

The watchdog defects found in the July logs (reattaching on the downlink probe
alone; retrying forever after failure) remain real and independently
verifiable — what was wrong was the *trigger*, not the defects.

**Recommendation: drop `sar_indoor` from the excitation campaign.** An
action-conditioned forecaster cannot learn what moving does in a world where
most actions lead to the same dead link. Keep it as a separate coverage-limits
study. Validate on `sar_open_field` before committing the full campaign.

Known wrinkle, not yet fixed: cycle-level `ss_samples` (57-62) badly
undercounts versus the fast log, because `test_one_ue` reports the count from
its final chunk only. Treat the fast-radio log as authoritative for sample
counts.

---

**Superseded status note (2026-10-16): NOT yet run against live hardware.** Four
bringup attempts on `sar_indoor` cleared four blockers in sequence — the AMF,
NSSF and SMF slice mismatches (a 2026-09-21 core reconfiguration left only
SD-qualified slices while the gNB and robot subscribers use a bare `sst: 1`),
after which the 5G stack came up fully: both robots register, both TUNs
appear, over-air ping 0% loss at ~30 ms. The remaining blocker is **Gazebo** —
an NVIDIA kernel-module/userspace mismatch (595.84 vs 595.99.02) breaks GL
context creation, and every world loads `gz-sim-sensors-system` with `ogre2`
while `models/sar_burger` carries a camera, so even the headless server
initialises OGRE and dies. **A host reboot is required.** Forcing software GL
does not help: the X server's GLX extension comes from the mismatched driver.
So nothing in §7b, nor the fast sampler, seeding or watchdog fix, has been
verified against an attached UE yet.

**Still confounded, and not fixed here:** the robot driver wanders an annulus
centred on the *live* gNB ground track, so robot motion depends on where the
gNB went. Replaying one seed under two policies therefore does **not** give
identical robot trajectories, so true paired counterfactuals (brief §6.3) are
still out of reach. Removing that coupling is a further behaviour change and
was not made.

---

Cost estimate before anything is launched (brief §6.6): the previous campaign
was 4 × 6 h + overhead ≈ 25 h of real time for 2,927 rows. Matching that per
seed across 4 terrains × 3 seeds would be **~75 h of wall-clock**, and I have
not estimated parallel-instance feasibility yet. **I will not start any run
without a go-ahead and an agreed duration.**

---

## 8. Open questions I need answered

1. **RSRP floor** — the −80 dBm default is never reached (§4.6). Re-set it from
   the observed distribution (e.g. −70 dBm), or define margin on SINR/C2 instead?
2. **Horizons** — given §4.1b, I'd now recommend: report 30/60/120 s on the
   existing 2,927 rows as an interim result, and add the 1–10 s horizons from
   new fast-cadence runs. Confirm that split?
3. **Indoor** — drop it, or re-run it properly (6 h)?
4. **New data collection** — approve the logging changes in §7.2–7.4, and how
   many hours of run time are acceptable?
5. **The extra urban run** (`campaign_20260728_210058/sar_urban.csv`) — fold it
   in as a second urban seed?
6. **README discrepancy** — what exact rule produced the 88.7% open-field figure
   (§3)?

---

## 9. How to reproduce this audit

Audit scripts are in this session's scratchpad; the commands used were
read-only (`md5sum`, `pandas.read_csv` on copies in memory, streaming `csv`
reader over the bridge log). **No file in `logs/` or
`NOMADIC5G_TERRAIN_RESULTS_2026/` was modified** (brief §13). Once `ml/` exists
I will land these as `ml/audit.py` so every number above regenerates from one
script.

---

## 10. Cell capacity and the broken `iperf3` (measured 2026-10-20)

### 10.1 What the cell actually carries

Measured with a dependency-free TCP harness (`tp_server.py` / `tp_client.py`),
uplink, 30 s sustained, every figure confirmed independently at the receiver:

| offered load | aggregate | per robot |
|---|---|---|
| 1 robot  | 18.5 Mb/s | **18.5 Mb/s** |
| 4 robots | ~17.7 Mb/s | **~4.4 Mb/s** |

The cell tops out near **18 Mb/s total** and shares it evenly. Capacity is a
property of the *cell*, not of the robot count.

Traffic in this testbed is **uplink** — `traffic_streams.py` runs the iperf3
client inside the robot netns, so the robots are senders (they "upload video").
This matters for interpreting earlier results: the `do_CSIRS` fix raised
*downlink* MCS, and it still roughly doubled throughput because the TCP ACK path
had been pinned at MCS 0–5.

### 10.2 The radio is not the limit

During a healthy sustained flow the gNB reports, dominantly:

```
ulsch ... MCS (0) 28 (Qm 6 deltaMCS 0 dB) NPRB 106    SNR 23-25 dB, BLER 0.00000
```

Full 106 PRB, MCS 28, Qm 6 (64QAM) — the top of the table. Uplink link
adaptation is working correctly and there is **no 5G-side configuration gain
available**. `ul_max_mcs` already defaults to 28, and `do_SRS` does **not** need
changing (it takes `"none" | "periodic" | "aperiodic"`, a string — passing `1`
is what crashed the gNB on 2026-10-19).

The frequent `NPRB 5 / Qm 2` lines are the *idle* periodic grant
(`min_grant_prb` default 5), not a cap. Reading them as a cap is a trap: they
only appear when the robot has nothing to send.

Theoretical ceiling for this TDD pattern (5 DL / 4 UL, 106 PRB @ 30 kHz) is
~68 Mb/s uplink, so the measured ~18 Mb/s is ~27% of it. The binding constraint
is the **rfsimulator RU thread**, pegged at 93%/91% of a single core while the
box as a whole sits near 31%. It is a software-radio sample-processing limit,
not a bandwidth, MCS or scheduling limit, and no config change moves it.

### 10.3 `iperf3` was corrupting its own stack — DEFECT, now repaired

`/usr/bin/iperf3` is Ubuntu's **3.9** binary, but it was loading
`/usr/local/lib/libiperf.so.0` from a locally built **3.16**
(`install_requirements.sh` builds it from source for `--bind-dev`). Same soname,
different ABI:

```
*** stack smashing detected ***: terminated
Segmentation fault (core dumped)     # rc=139
```

Both the mixed pair *and* the matched 3.16 pair crashed. Flows died mid-test and
were relaunched, which is what produced the restart artifacts in the throughput
column — **not** UE reattach, as previously assumed.

**Repair applied:** the source-built 3.16 was backed up to
`logs/archive/iperf3_local_backup_20261020/` and removed from `/usr/local`;
`ldconfig` re-run. `iperf3` is now a consistent 3.9 linking its own 3.9 library,
verified stable (2.08 / 2.26 / 2.10 Mb/s against a 2.0 Mb/s target, rc=0).

Because 3.9 predates `--bind-dev` (added in 3.10), that flag was removed from
`traffic_streams.py`, `auto_patrol.py` and `gazebo_viz.py`. It was redundant:
these clients run **inside the robot netns**, where the UE tun is the only route
to the test IP. Verified — same path, same throughput, no bind.

### 10.4 Consequences for the archive

`tcp_tp_mbps` in `NOMADIC5G_TERRAIN_RESULTS_2026/` now has **three** independent
reasons to under-report, all of which must be stated in the paper:

1. `-t 2 -O 1` measures ~1 s of TCP, i.e. a point on the slow-start ramp;
2. it was collected with `do_CSIRS = 0`, so downlink MCS was pinned 0–5;
3. it was collected with the crashing iperf3, so some flows died mid-measurement.

Treat those absolute Mb/s as unusable. Compare on **fraction of offered load
served**, not absolute rate.

### 10.5 Campaign traffic targets are sound

TCP 2.0 + UDP 0.8 = **2.8 Mb/s per robot**, 11.2 Mb/s offered across four robots
into an ~18 Mb/s cell — about 62% load. End-to-end check of the repaired stack:

| robot | TCP (target 2.0) | UDP (target 0.8) | loss | retr |
|---|---|---|---|---|
| alpha | 2.10 | 0.80 | 0.0% | 0 |
| bravo | 2.10 | 0.80 | 0.0% | 0 |
| charlie | 2.10 | 0.79 | 0.0% | 0 |
| delta | 2.10 | 0.79 | 0.0% | 0 |

Headroom is deliberate: at saturation every robot would be congestion-limited
and the throughput column would stop responding to *link* quality, which is the
signal the forecaster has to learn.

**15 Mb/s per robot is not reachable with four robots on this testbed** — that
needs ~60 Mb/s aggregate against an ~18 Mb/s ceiling. A single robot already
exceeds it at 18.5 Mb/s. Raising the per-robot figure means fewer robots per
cell, or real radio hardware (the planned field test), not a config change.

---

## 11. Silent-failure modes found 2026-10-21/23 (all fixed)

Every defect in this section produced data that looked **completely normal**.
None raised an error, and none would have been visible without checking the
numbers against physics. They are recorded because any future change to the
roster, the traffic model or the logging can reintroduce them.

### 11.1 The channel bridge died whenever the roster was not four robots

`channel_bridge.py` opened a telnet session to every entry in its fixed
four-robot `MODEL_MAP`. Under `SAR_ROBOTS=alpha,bravo` it dialled charlie
(9094), failed, and exited:

```
FATAL: telnet :9094 never became responsive
```

Nothing downstream noticed. The stack came up, both UEs attached, the patrol
logged happily — and **`ploss_dB` stayed 0.00 on every row**, with RSRP pinned
at 51.3 dB/RE from 43 m to 153 m. No pathloss, no terrain, no distance effect
at all. `corr(distance, throughput)` was **+0.265** — the wrong sign, because
the only thing varying was noise.

Fixed by filtering `MODEL_MAP` to the active roster. After the fix: ploss
spans 0–28 dB and `corr(distance, throughput) = -0.333`.

**Any run whose `ploss_dB` column is identically zero is void.** Check this
first, before anything else.

### 11.2 `SAR_ROBOTS` was lost through `sudo`

`start_full_stack.sh` ran `setup_robot_netns.sh` under `sudo`, whose
`env_reset` strips the variable. The script fell back to four robots, demanded
a TUN for a UE that was never launched, died, and left **every TUN in the root
namespace**. The stack still looked healthy — UEs attached, host pings fine —
while the robots had no namespace connectivity whatsoever.

This one also defeats the C2 watchdog, because a root-ns TUN still answers a
ping from the host: C2 reads "fine" while the robot is isolated. Fixed by
passing the roster explicitly through `sudo env`.

### 11.3 A watchdog reattach could kill the whole cell

`reattach_ue()` used `pkill -9`. SIGKILL drops the UE's rfsimulator TCP
connection mid packet; the gNB then parses the tail of a partial block as the
next header and trips

```
AssertFatal(b->th.beam_map == 1ULL || t->beam_ctrl->enable_beams == 1,
            "The transmitter has enabled beam simulation while this receiver has not")
```

in `radio/rfsimulator/simulator.cpp`, which **exits the gNB**. Fixed with a
graceful SIGTERM (SIGKILL only as fallback). Verified: repeated UE kills now
leave the gNB running, zero assertions.

### 11.4 A dead UE cannot reliably be re-attached in rfsimulator

Even with a clean kill, a relaunched UE often cannot re-acquire the cell:

```
[NR_PHY] Starting cell search with center freq: 3319680000 ...
[PHY]    synch Failed:            (forever)
```

The gNB's rfsim server still holds the dead client's slot. The old behaviour
was to stand the robot down and continue, which silently turns an N-robot
experiment into an (N-1)-robot one — and those rows are indistinguishable from
genuine terrain-caused outage. The run now ABORTS instead (exit 4), so
`run_research_campaign.sh` marks it failed and starts the next world from a
clean stack. A dead gNB aborts the same way (exit 3).

### 11.5 PHY debug logging

Both the gNB and every UE config shipped `phy_log_level = "debug"` — a line
per symbol, written from inside the rfsimulator RU thread that is the
testbed's bottleneck. One gNB run produced **1.6 GB / 21.7 million lines**;
UE logs grew at ~2 GB/h. Now `error` (MAC stays `info` so the per-UE MCS/BLER
block still prints): gNB log 84 KB, UE log 104 KB.

Honest result: this did **not** raise throughput. It was waste and a disk
risk, not the bottleneck.

### 11.6 Unlimited (greedy) traffic is worse, not better

Measured with pathloss working, 2 robots:

| offered load | C2 availability | mean throughput |
|---|---|---|
| greedy TCP (no `-b`) | **15%** (22% even at 0–70 m) | **2.37 Mb/s** (peak 18.6) |
| capped | 82–100% | ~6 Mb/s |

Greedy TCP saturates the link, the link collapses, and the control pings die
with it — so removing the cap *lowers* average throughput and destroys the C2
label at the same time. The cap is not throttling the testbed below what it
can do; it keeps the link in the regime where it delivers.

An earlier test appeared to show greedy was harmless (C2 100%). That test ran
with the channel bridge dead (11.1), i.e. on a lossless link. It is void.
