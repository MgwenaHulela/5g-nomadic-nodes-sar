---
name: forest-outages-are-ue-queue-congestion
description: Nov 11 2026 test — forest cell-wide C2 outages = 1.5 Mb/s CBR video overflowing weak NLoS uplinks; OAI UE buffers queue minutes of data (no drops), recovery 14-18+ min
metadata:
  type: project
---
Video-load test (docs/CONGESTION_TEST_REPORT.md, logs/congestion_test_20261111/): forest, robots static at spawn (NLoS 24-30 dB), pings clean with no load; with the four 1.5 Mb/s video feeds, RTT grows linearly to minutes, goodput 0.13-0.49 Mb/s; after video stops links stay dead 14-18+ min. Kernel TUN qdisc backlog 0 / drops 0, so the queue is inside the OAI UE.
**Why:** CBR video never backs off; low-MCS NLoS links fall below 1.5 Mb/s; the unbounded UE buffer gives outages with minutes of memory. Explains "post-reattach uplink 100% loss" (the reattach flushes the buffer, the video refills it) and why outages don't track instantaneous ploss.
**How to apply:** treat forest C2-down labels and forest policy comparisons as congestion-contaminated until the queue is bounded (tc limit on TUN, adaptive video, or OAI buffer cap = config change, ask first). Supersedes the "outages unexplained" note in [[xyz-flyoff-result]] and [[altitude-prober-fix]].

Follow-up 2026-11-11: an HTB cap of 100 kbit/s on each robot TUN (inside the netns; C2 in a priority class; video in a 10-pkt FIFO) removed the outages entirely at the same placement. 0% ping loss, 70–90 ms RTT, no recovery tail. It drops about 94% of video. A fixed cap must stay below link capacity, so campaigns need an adaptive cap or an OAI buffer bound. Data is in logs/congestion_test_cap_20261111/.

Follow-up 2 (2026-11-11): a delay-adaptive AIMD cap (scratch `adaptive_cap.py`, archived in logs/congestion_test_adaptive_20261111/) also gave 0% ping loss. Video got 0.30/0.37/0.42/0.10 Mb/s, 85–94% of the uncapped drain rate, at 0.3–0.4 s median latency. Steps 2 (open field) and 3 (moving robots) are pending, and the cap is not yet in campaign code.
Step 2 (open field, 2026-11-11): the cap ramped 200→1600 kbit in about 52 s, made 0 cuts, and carried the full 1.55 Mb/s of video at about 85 ms latency with 0% ping loss. The ramp is slow and could use a multiplicative start. Step 3 (moving robots) is pending.
Step 3 (forest patrol, arm A settings, 30 min, 2026-11-12): v2 controller with slow start and TUN-rebuild-on-reattach. C2 91.8% vs 66.8% uncapped; all-4-up 72.7% vs 52.0%; none-up 0.9% vs 17.4%; video p90 latency 1.6 s vs 73 s. Data in logs/congestion_test_patrol_20261112/. Next: put the cap in campaign code behind an off-by-default switch, then re-fly the forest fly-off arms.
2026-11-12: the cap is now in campaign code: scripts/adaptive_uplink_cap.py, enabled by SAR_UL_CAP=adaptive in run_research_campaign.sh (default off; each world writes ${W}_ulcap.csv). run_capped_forest_flyoff.sh re-flies forest arms D, B, C, A at 5400 s each with the cap and the prober. It started at 00:25 on 2026-11-12 and logs to logs/capped_forest_flyoff_<ts>.log. The 2-min smoke run is logs/campaign_20261112_001911 and is not a fly-off run.
