---
name: watchdog-roster-keyerror
description: "UE_LOG_OF in auto_patrol.py was alpha/bravo-only since 2026-08-28, killing the RLF watchdog thread in 20 of 24 October training runs"
metadata:
  node_type: memory
  type: project
  originSessionId: 30a2c230-eb87-4138-8520-68e653b73eed
  modified: 2026-11-09T18:04:27.464Z
---

`scripts/auto_patrol.py:581` `UE_LOG_OF` was `{ALPHA_MODEL: ..., BRAVO_MODEL: ...}`
from commit 00afc5e (2026-08-28) onward, despite a comment beneath it saying the
roster fix had been made. The first time charlie or delta needed a reattach,
`reattach_ue()` raised `KeyError: 'sar_ground_charlie'|'sar_ground_delta'` and the
watchdog THREAD died silently while the run continued — no robot was ever
relaunched after that, and the robot being reattached shows a stuck `<reattaching>`
marker for the rest of the run.

Found 2026-11-09 during the round-4 fly-off: 20 of the 24 October research logs
(campaign_2026102[3-6], the dataset behind every model and the paper) contain the
traceback; round-4 runs 1 and 2 hit it too.

**Why:** much of the dead-UE contamination ([[coverage-rlf-kills-ue-process]]) and
its long tail (mean dead stretch ~1003 s, p90 ~4605 s) is this bug, not radio
physics — Section V of the paper attributes it to RLF alone.

**How to apply:** grep a run's `*_research.log` for `Exception in thread` /
`KeyError: 'sar_ground` before trusting dead-UE statistics or closed-loop results.
Treat October dead-UE rates as inflated by unrecovered process deaths. See
[[sar-silent-failure-checks]].
