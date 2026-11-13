---
name: coverage-rlf-kills-ue-process
description: "A real coverage RLF terminates the OAI UE softmodem process; only relaunch recovers C2, not channel restore"
metadata: 
  node_type: memory
  type: project
  originSessionId: 1165f5f8-c23c-4b7d-8e68-f4c8a126e046
  modified: 2026-07-28T13:42:52.443Z
---

In the SAR rfsim stack, a genuine coverage RLF (channel attenuation crossing
the cliff — verified 2026-07-28 by driving bravo to ~260 m, gNB at h=40:
bridge UL ploss hit ~41 dB / DL ~35 dB) **terminates the `nr-uesoftmodem` UE
process**. The UE first drops out of SSB sync and spins in a PSS/SSS re-search
loop (`ue_bravo.log`: "Failed to detect SSS after PSS, metric ~600, threshold
30000"), fails to re-acquire, and the process **exits** — confirmed via
`pgrep -c nr-uesoftmodem` dropping 4→2 and the bravo TUN vanishing from its
netns.

**Consequence:** soft-clamp / channel-restore recovery is IMPOSSIBLE for the
real failure mode — there is no surviving softmodem to re-sync, so putting
`channelmod ploss` back to ~0 does nothing. The ONLY recovery is relaunching
the UE (targeted `reattach_ue` in `scripts/auto_patrol.py`, or the heavier
`restart_ran.sh`). This is the true reason behind the repo's "UE may never
re-attach without a RAN restart" notes. See [[sar-sim-stack-operation]] (45 dB
RLF cliff) and the `--research` mode's watchdog which does the targeted reattach.

**Inducing RLF for tests:** `channel_bridge.py` has `POSITION_MAX_JUMP_M = 200`
— it rejects pose teleports >200 m as Gazebo glitches, silently freezing the
channel at the old position (a 15→300 m jump produced NO ploss change). Move
in hops <200 m (e.g. 15→150→260) past the coverage edge (~130 m at h=40).
Robots hold a teleported pose fine (no snap-back). Possible stale constant to
check: `NOISE_POWER_DB = -6.0` in the bridge while the cliff-calibration comment
assumes -40 (noise is actually set in the rfsim config at startup, not via
telnet — verify which floor is really in effect, since it sets where C2 dies).
