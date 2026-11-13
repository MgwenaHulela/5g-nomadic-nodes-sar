---
name: altitude-prober-fix
description: Nov 11 2026 — raw-mean altitude bandit fooled by time drift; AltitudeProber (block fixed effects + probes) is the default learner; flown live in 4 runs: consistent 55 m in open field, forest swamped by outages
metadata:
  type: project
---
Raw-mean UCB bandit blamed altitude for drift. Fix: AltitudeProber in scripts/ml/altitude_bandit.py, default for SAR_PLANNER_ALT=bandit (SAR_PLANNER_ALT_LEARNER=ucb = old). Offline bench scripts/ml/altitude_offline_eval.py: same mean, half the bad runs.
Live 4 runs (run_prober_flyoff.sh, logs/prober_flyoff_20261111_092251*): open field climbed to 55 m both runs (bandit 49/37 m); C2 80.0/80.8 vs baseline B 85.8. Forest: 40 m and 50 m (weak evidence); forest D run 28.9% C2, cell-wide outage (all-4 down 57%). Report docs/ALTITUDE_LEARNER_REPORT.md.
**Why:** learned altitude is now consistent but doesn't beat fixed-40 centroid; forest dominated by unexplained cell-wide outages.
**How to apply:** next lever is the forest outage cause (video-load test), not altitude learning. Related: [[xyz-flyoff-result]], [[surrogate-cannot-learn-altitude]].

2026-11-12: `cautious` is now the planner default (SAR_PLANNER_ALT_LEARNER). It is AltitudeProber with home_z equal to the fixed altitude, z_conf_away=1.5, min_gain=0.02, probe_every=4 and probe_every_slow=20. Why: in the capped forest a 90-min run can't sign the altitude effect (within-run CI ±10 pts/10m around −6.6), and leaving 40 m costs on average. On fresh-seed bench validation it about halves the worst-case loss and gives 3–5× fewer runs losing >2 pts, at the price of about half the climbing gain. The bench now has BENCH_HET (per-robot slope noise); HET results are only fair on traces recorded at 40 m. The learner has not flown live yet. See docs/ALTITUDE_LEARNER_REPORT.md.

2026-11-12 live (capped forest, logs/cautious_forest_flyoff_20261112_102709*): cautious C 65.2 % all-4-up, cautious D 72.0 %; both tie hover A 70.5 / centroid B 67.0; D +28.8 [+11.9,+45.1] over prober D. Incumbent stayed 40 m; ~16 % of time on probes (C up, D down). Learned placement now safe, not better. Draft paper: claude.ai/artifact/2cxzXYc5bz4VWHdRKUqM7B (Claude Docs).
