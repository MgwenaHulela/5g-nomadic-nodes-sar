---
name: xyz-flyoff-result
description: Nov 10-11 2026 8-run x-y-z fly-off (hover/centroid/centroid+bandit/model+bandit, open+forest) — no learned arm beat the simple baselines
metadata:
  type: project
---
8-run fly-off (run_xyz_flyoff.sh, log logs/xyz_flyoff_20261110_172251*, summary CSV ..._summary.csv), one 90-min run per arm per world, time-weighted:
- open: B centroid@40 85.8% C2 / 57% all-4; C centroid+bandit 79.2/58; A hover 77.7/50; D model+bandit 71.0/47.
- forest: A hover@40 66.6/58; B 58.0/43; D 55.5/37; C 51.0/33. All-4 block-bootstrap CIs ~±15 pts, overlap.
- Bandit altitude inconsistent: open run 5 climbed to 55 m, open run 7 (arm D) settled 35 m; forest 40 and ~30 m. Don't claim "bandit reliably climbs in open field".
- Forest: hovering beat moving (matches [[nomadic-gnb-terrain-results]]). Model x-y no better than centroid.
**Why:** single runs, robot paths not reproducible across seeds, so arm effects are confounded with patrol differences.
**How to apply:** any firm claim needs repeats (≥3/arm) or reproducible robot paths; see [[surrogate-cannot-learn-altitude]].

2026-11-12, capped forest re-flight (SAR_UL_CAP=adaptive, 1 run per arm). C2 / all-4-up: A 89.1/70.5, B 88.9/67.0, C 84.7/53.1, D 82.4/43.3. None-up dropped from 25–57% to about 0. The ranking is unchanged: A≈B, and the learned arms are worse (D significantly). The prober climbed to 45–55 m on noise. See docs/CAPPED_FOREST_FLYOFF_REPORT.md.
