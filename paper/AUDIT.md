# Step 7: claim-versus-evidence audit (2026-11-12)

Draft audited: `paper/main.pdf`. The main text ends on page 6; the full PDF is 8 pages. Every number in the main text was traced to a source file. Values marked "re-run" were recomputed on 12 Nov during Step 5, Step 7, or both.

## 1. Claims and the files that support them

| Claim | Supporting number(s) in text | Source file | Verified value |
|---|---|---|---|
| **C1** queueing, not coverage | UL ping loss 64–91 %; all probes lost 64 % (on) / 100 % (off) | `logs/congestion_test_20261111/analyse.py` output → `figs/tab_static_load.csv` | 64.4–90.8; 64.4 / 100 (re-run) |
| | RTT about 60 ms → peak 350–444 s | `ul_*.txt` (no-load UL 61–68 ms; max 350/360/375/444 s) | re-run |
| | drain 0.13–0.49 Mb/s | analyse.py video goodput medians | 0.13–0.49 (re-run) |
| | recovery 14 → 18+ min | `recovery.txt` (stop 16:10:20; first recovery 16:24:18; delta not recovered at 16:28:32) | 14.0 / 18.2 min |
| | kernel queue empty; ~79 MB held by the UE | `docs/CONGESTION_TEST_REPORT.md` **only** | **doc-only**: no raw `tc` output saved for the uncapped test. See open item 1 |
| | fixed cap: 0 % loss, 69–87 ms | `congestion_test_cap_20261111` analyse.py | re-run |
| | adaptive: medians 105–429 kb/s; 32–40 cuts | `congestion_test_adaptive_20261111/cap_log.csv` (video window) | 105 / 305 / 386 / 429; cuts 40 / 32 / 33 / 33 (re-run) |
| | adaptive video 0.10–0.42 Mb/s; latency 0.30–0.42 s (3 robots), 1.08 s (delta) | analyse.py | re-run |
| | open field: 0 cuts, 1.6 Mb/s ceiling, 1.55 Mb/s, 82–89 ms | `congestion_test_adaptive_open_20261111/cap_log.csv`, `analysis.txt` | 0 cuts, max 1600, 1.55, 82–89 (re-run) |
| | patrol: C2 66.8 → 91.8; none 17.4 → 0.9; p90 73 → 1.6 s | `congestion_test_patrol_20261112/comparison.txt` | 66.8 / 91.8, 17.4 / 0.9, 73.4 / 1.6 |
| | fly-off: none-up 25–35 → 0–0.3; mean C2 51–67 → 82–89 | `capped_forest_flyoff_20261112_002402_summary.csv` (sets xyz, capped) | 25.4–34.9 → 0–0.31; 51.0–66.6 → 82.4–89.1 |
| **C2** modest forecasting skill; capacity hurts | lift 1.79, ROC 0.667, worst fold 0.538 | `logs/ml/phase2c_model_class.csv` (radio_blind, n = 38303, logistic_L1) | 1.792 / 0.6671 / 0.5384 |
| | trend model ROC 0.650 at 20 s, 0.552 at 60 s | `phase2b_onset.csv` (POOLED, radio_blind\|trend) | 0.6497 / 0.5515 |
| | MLP-large ROC 0.503; worst 0.404 | phase2c | 0.5033 / 0.4036 |
| | exception: medium MLP 1.55 vs 1.21–1.22; worst 0.456 vs 0.550 | phase2c (n = 38803 block, trend6) | 1.551 vs 1.213–1.221; 0.4557 vs 0.5505 |
| | TCN 0.493, GRU 0.523, summary 0.540; 3,115 cycles | `phase6_seq.csv` | means 0.493 / 0.523 / 0.540; 1640 + 1119 + 356 |
| | dead-process 12.4 %; hills 72.2 % | `logs/ml/dead_process_extent.csv` | 6760 / 54572; 0.7224 (re-run) |
| **C3** no recovery of the terrain effect | measured open − forest +0.208 (cycle), +0.167 (60 s) | `phase4b_interactions.csv` (measured); `phase4c_timebase.csv` | 0.2080 / 0.1674 |
| | recovered +0.006 (p 0.365); +0.010 (60 s); +0.021\* at 120 s vs measured +0.117 | `phase4b_interactions.csv` (raw); `phase4d_interactions.csv` (recovered_raw) | 0.0057 / 0.0105 / 0.0210 [0.0032, 0.0383]; 0.1170 |
| | two of the other three inverted per cycle | phase4b raw: urban − forest −0.020\*, hills − forest −0.031\* | yes |
| | surrogates choose about 55 m everywhere; LightGBM about 47 m in forest and open field | `logs/ml/altsel_run.log` | linear 55.0 ×4; linear_geo 53.7–54.9; LightGBM 46.6 / 46.7 |
| **C4** learned placement does not beat the baselines | A 70.5, B 67.0; B − A −4.0 [−20.4, +11.2]; C − A −18.1 [−34.1, −2.6]; D − A −27.4 [−43.0, −10.7] | `flyoff_forest_capped_analyse.py` (re-run) | identical |
| | −6.6 [−14.9, +0.9]; within-run +4.6 [−9.1, +12.0], +5.9 [−10.7, +14.9] | `logs/ml/forest_altitude_effect_capped.csv` | identical (re-run) |
| | cautious 65.2 / 72.0; C − A −5.1 [−17.9, +6.8]; D − A +1.6 [−13.4, +17.7]; D vs exploring D +28.8 [+11.9, +45.1]; 83–84 % at 40 m | `logs/cautious_forest_flyoff_20261112_102709_summary.txt` | identical |
| | planner vs two_phase: open +10.5 [+7.0, +13.2]; forest −15.3 [−24.8, −5.0]; nulls −0.5 / −5.0; urban/hills nulls +18.7 / −21.7 | `logs/ml/planner_vs_twophase_nullctl.csv` | identical (re-run) |
| **Method / Setup** | 20 missions, 54,572 cycles, 90–200 min, 5 per terrain | `scripts/ml/dataset.py` `build()` | 20 runs; 54572; 90–200 min (re-run) |
| | planner β = 10, grid 25–55 / 5 m, offsets 0 / 15 m, 10 m/decision | `scripts/ml/planner.py` | yes |
| | prober: 240 s blocks, z 0.5; cautious 1.5 SE + 0.02, probes every 4 / 20 | `altitude_bandit.py`, `online_planner.py` CAUTIOUS | yes |
| | cap: 50 / 1600 / 200 kb/s, ×0.7, +50, 2 s, 150 / 50 ms, 60 s; queues 20 / 10 packets | `scripts/adaptive_uplink_cap.py` | yes |
| | planner model ROC 0.659 | `altsel_run.log` (linear_geo pooled) | 0.659 |
| **Intro** | SATNAC forest roaming costs about 22 pp | published SATNAC text (author-supplied) | "about 22 percentage points" |

## 2. Sentences that overstated their evidence, and the fixes

| Location | Before | After | Why |
|---|---|---|---|
| Abstract | "the layer gained less than two silent failures … cost" | "the largest gain came not from learning but from fixing a silent failure of the managed system" | The original comparison was not quantified |
| Abstract | "a cautious one tied the baselines" | "was not distinguishable from them" | CIs about ±12–16 pts |
| Abstract, Intro | "12.4 % of labels" | "12.4 % of observations" | The detector flags rows, not labels |
| Intro, Concl. | "helps / lifted every placement policy" | "improved each of four placement policies, one run each" | Single runs |
| Results 5.1 | "the time the slowest link needed to drain" | "consistent with each link slowly draining its backlog" | Interpretation |
| Results 5.1 | "where links are strong, the cap stays out of the way" | "in this test, where links were strong, …" | One open-field test |
| Results 5.2 | "worst-fold skill … is the figure of merit, and it rewards …" | "we take … as the figure of merit; our reading is that …" | Interpretation |
| Results 5.2 | "1.55 against 1.21" | "1.55 against 1.21–1.22" | Three linear models |
| Results 5.3 | "linear models choose 55 m in every terrain" | "about 55 m" | linear_geo urban 53.7 m |
| Results 5.4 | "A cautious learner is safe … tying the baselines" | "avoids that loss … not distinguishable from the baselines, though the intervals are wide" | CI lower bounds −17.9 / −13.4 |
| Conclusion | "made cautious, it at least does no harm" | "not distinguishable from them within wide intervals" | Same |
| Related | "Those remedies change the RAN or the transport protocol" | "change network queues, the RAN or the transport protocol" | CoDel is AQM in a queue |
| Claim 3 (Step 5) | "n.s. at every bin width" | n.s. per cycle, 30 s and 60 s; small but significant at 120 s | `phase4d_interactions` |

## 3. Numbers in text vs tables vs figures

Every main-text number above matches its table or figure:

- **Table 2:** `tab_static_load`
- **Table 3:** `tab_contrasts`
- **Fig. 3:** `fig_cap_arms`
- **Fig. 4:** `fig_capacity`
- **Tables A1–A5:** `appendix_tables`

`figs/check_numbers.py` gives 19 PASS. Its 4 flags were resolved in Step 5 and in the edits above.

## 4. References

- **28 entries (SATNAC itself, 12 from its reference list, 15 web-verified); all cited; all citations resolve.** BibTeX reports no undefined citations, and none are uncited. Two citations only looked uncited because they span a line break.
- **Author-supplied** (published SATNAC list): 12.
- **Web-verified:** 15. Each was checked on its source page (arXiv abstract, publisher, DOAJ/OpenAlex, author repository), and `refs.bib` names the page in a `verify_note` field, which does not print.
- **Still [VERIFY]: 6.**

| Entry | What is missing |
|---|---|
| `makhubela2026satnac` | last page |
| `nikaein2014oai` | author list differs between SATNAC's printing and the ACM paper |
| `gudmundson1991` | volume / pages (SATNAC paste was truncated) |
| `jiang2012tackling` | pages; proceedings page not opened |
| `qiu2020placement` | first names / pages |
| `moore2024aerpaw` | volume / issue / pages / year (accepted, IEEE Commun. Mag.) |

- **Recent work (2023–2025) added on the author's request:**
  - `wan2025l4span` (CoNEXT 2025)
  - `liu2025fog` (arXiv 2025)
  - `islam2023dnn` (IEEE TNSM 2023)
  - `hasan2024gentrap` (arXiv 2024)
  - `moore2024aerpaw` (IEEE Commun. Mag., accepted)
  - `iye2025owdt` (IEEE Access 2025)
  - `coronado2022zsm` (IEEE COMST 2022)
- **Paraphrase check:** the claims made *about* recent papers in Related Work were checked against their abstracts:
  - L4Span: RLC sojourn time dominates one-way delay
  - Liu et al.: retransmission/uplink-scheduling buffers add delay comparable to congestion
  - Coronado et al.: autonomous operation with human oversight
  - Islam et al. and Hasan et al. learn from a deployed network's history (radio/weather, spatio-temporal context)

## 5. Open items from the audit

1. **"Kernel queue held no backlog / ~79 MB held by the UE"** is supported only by the congestion report. There is no raw `tc` output for the uncapped test (the adaptive-open test does have `tc_stats.txt`). Options:
   - (a) keep it, citing the report as the source;
   - (b) re-run a 6-min uncapped static test that saves `tc -s` (about 15 min, but it needs the stack, which is busy with the repeat campaign until about 13 Nov 23:30);
   - (c) cut the sentence. C1 still stands on the RTT growth and on the fixed cap.
2. **Section 5.4 / Fig. 5 / Setup / Limitations** still carry [TODO] for the three-seed results.
3. **Six [VERIFY] references** (above).
