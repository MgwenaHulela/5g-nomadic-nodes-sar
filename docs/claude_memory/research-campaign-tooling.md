---
name: research-campaign-tooling
description: "--research mode + run_research_campaign.sh orchestrator; never run two campaigns (shared stack corrupts data)"
metadata: 
  node_type: memory
  type: project
  originSessionId: 1165f5f8-c23c-4b7d-8e68-f4c8a126e046
  modified: 2026-07-28T19:03:03.168Z
---

Built 2026-07-28 for the multi-terrain experiment.

**`auto_patrol.py --research`** — two-phase run (default 6h: `--phase1` static
gNB then nomadic gNB 3D random walk; robots free-patrol throughout). Watchdog
detects sticky RLF (sustained 100% C2 loss) and does a targeted single-UE
reattach — see [[coverage-rlf-kills-ue-process]] (relaunch is the only cure).
For `sar_indoor` it switches to an O2I waypoint driver: robots start OUTSIDE
(south of the door), drive in through door+doorways to search the rooms; gNB
uses a tighter nomadic box. CSV gains `phase` + per-robot `event`/`outage_s`.
Exits via `os._exit(0)` — the gz.transport13 pybind bindings SIGABRT on normal
interpreter teardown ("scoped_acquire::dec_ref: thread state must be current!").

**`run_research_campaign.sh [dur_s] [phase1_s] [world...]`** — cycles the
research run across worlds (default all 5), restarting the stack per world,
writing `logs/campaign_<ts>/{manifest.csv,<world>.csv,<world>_*.log}`. Run
unattended with `setsid nohup ... </dev/null &`. Needs `SAR_SUDO_PASS`.

**NEVER run two campaigns at once.** There is ONE physical stack (Gazebo, RAN,
robots, bridge); two campaigns both publish cmd_vel to the SAME robots and move
the SAME gNB, silently contaminating each other's data (a duplicate launch
ruined a 3h run on 2026-07-28 — one launched with `bash ...`, the other `./...`).
The script now takes an exclusive `flock` on `/tmp/sar_research_campaign.lock`
and refuses to start if another campaign holds it. Cleanup pkills MUST use the
bracket trick (`pkill -f '[a]uto_patrol.py'`) or they self-match the calling
shell — see [[sar-sim-stack-operation]] (pkill self-match quirk).
