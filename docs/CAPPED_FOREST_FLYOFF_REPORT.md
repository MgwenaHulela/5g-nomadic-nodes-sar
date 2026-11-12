# Capped forest fly-off (2026-11-12)

All four placement arms re-flown in the forest with the adaptive uplink cap
(`SAR_UL_CAP=adaptive`, `scripts/adaptive_uplink_cap.py`; why: docs/CONGESTION_TEST_REPORT.md).
Runner `run_capped_forest_flyoff.sh`, order D, B, C, A, 5400 s each, seed 1794000001,
learned-altitude arms use AltitudeProber. All four runs rc 0, arm confirmed in the planner log.

The runner's log flags every run "uplink cap NOT active (0 rows)". That flag is wrong: the
check's `$`-anchored grep missed the `\r\n` line endings csv.writer produces. Each run's
`sar_forest_ulcap.csv` holds ~21,000 controller rows with 1,700–2,400 cuts. The check is now fixed;
the original log is left unchanged.

| Arm | | Runs | Mean C2 up | All 4 up (95 % CI) | ≥3 up | None up | Alt, 2nd half | Video p90 latency | Reattach ok / failed |
|---|---|---|---|---|---|---|---|---|---|
| A hover 40 m | **capped** | 1 | **89.1** | **70.5** (61–79) | 89.7 | 0.3 | 40 | 1.4 s | 3 / 0 |
| | uncapped | 1 | 66.6 | 58.4 (42–75) | 62.7 | 25.4 | 40 | 65 s | 13 / 2 |
| B centroid 40 m | **capped** | 1 | **88.9** | **67.0** (54–78) | 89.4 | 0.0 | 40 | 1.6 s | 4 / 0 |
| | uncapped | 1 | 58.0 | 42.8 (23–64) | 55.6 | 25.9 | 40 | 90 s | 12 / 4 |
| C centroid + learned alt | **capped** | 1 | **84.7** | **53.1** (41–65) | 87.0 | 0.0 | 48 | 2.3 s | 6 / 0 |
| | uncapped (bandit / prober) | 2 | 51.0 / 47.4 | 33.3 / 33.6 | 49.5 / 46.4 | 34.9 / 41.1 | 39 / 51 | 59 / 74 s | 16/3, 19/2 |
| D model + learned alt | **capped** | 1 | **82.4** | **43.3** (32–56) | 87.3 | 0.0 | 46 | 2.2 s | 4 / 1 |
| | uncapped (bandit / prober) | 2 | 55.5 / 28.9 | 36.8 / 17.0 | 52.7 / 24.4 | 30.2 / 56.6 | 32 / 41 | 59 / 114 s | 19/3, 20/8 |

Metric: time-weighted share, C2 up = loss < 50 %, CI = 10-min block bootstrap
(`scripts/ml/flyoff_forest_capped_analyse.py`; table in
`logs/capped_forest_flyoff_20261112_002402_summary.csv`).

**Capped arms against capped baselines** (difference, 95 % block-bootstrap CI):

| | all 4 up | mean C2 up |
|---|---|---|
| B − A | −4.0 [−20.4, +11.2] | −0.2 [−6.1, +5.7] |
| C − A | **−18.1 [−34.1, −2.6]** | −4.4 [−10.3, +1.7] |
| C − B | −14.1 [−31.0, +4.0] | −4.2 [−10.4, +2.2] |
| D − A | **−27.4 [−43.0, −10.7]** | **−6.8 [−12.3, −1.1]** |
| D − B | **−23.4 [−39.6, −4.5]** | **−6.6 [−12.1, −0.6]** |

## Findings

1. **The cap changes the forest from "mostly down" to "mostly up" for every arm.**
   Robots with no link fall from 25–57 % of the time to 0–0.3 %, and mean C2 rises
   from 29–67 % to 82–89 %. Video p90 latency falls from about 1–2 min to about 2 s.
   Failed reattaches drop from 2–8 per run to 0–1.
2. **The ranking of arms does not change.** Hover (A) and centroid (B) are
   indistinguishable. Both learned-altitude arms are worse; D is significantly worse than
   both baselines, and C is significantly worse than hover on all-4-up.
3. **The learned arms lose by climbing.** The prober spent much of the run at 45–55 m (C:
   15 % of the time at 55 m; D: 31 % at 45 m). The forest's measured altitude effect is about
   zero to slightly negative (−0.015 per 10 m). Its contrasts stayed within ±0.05 with
   standard errors of 0.05–0.1, so with z_conf 0.5 it moved on noise. The scheduled probes
   to worse altitudes also cost connectivity. D's model-chosen x-y did not help on top of that.
4. Earlier forest conclusions about the *ordering* still hold. Their *magnitudes* were
   congestion artefacts and should not be quoted.

## Caveats

* One run per arm. The CIs are within-run block bootstraps, so they do not capture
  run-to-run variation.
* The same seed gives the same robot paths, but radio shadowing and timing differ between runs.
* Row counts differ (245–356) because the cycle is slower when links are worse; the
  metric is time-weighted.
* Open-field arms have not been re-flown with the cap. Step 2 showed the cap does not throttle
  strong links once ramped, so the open-field results should change little, but this is untested.
