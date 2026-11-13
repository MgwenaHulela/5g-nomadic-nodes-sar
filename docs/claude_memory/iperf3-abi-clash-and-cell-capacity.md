---
name: iperf3-abi-clash-and-cell-capacity
description: "rfsim cell caps at ~18 Mb/s uplink total (RU thread bound); iperf3 3.16/3.9 ABI clash crashed flows, repaired to distro 3.9 with --bind-dev dropped"
metadata:
  node_type: memory
  type: project
  originSessionId: 30a2c230-eb87-4138-8520-68e653b73eed
  modified: 2026-10-20T18:38:19.421Z
---

Two durable facts about the SAR testbed, established 2026-10-20.

**Cell capacity is ~18 Mb/s uplink AGGREGATE, not per robot.** One robot alone
gets 18.5 Mb/s; four robots get ~4.4 Mb/s each (~17.7 total). The cell shares
evenly. This is *not* a 5G config limit — during a healthy flow the gNB runs
MCS 28 / Qm 6 (64QAM) / NPRB 106 at 23–25 dB SNR with zero BLER, the top of the
table. The limit is the **rfsimulator RU thread pegged at ~93% of one core**
while the box overall sits near 31%. No config change moves it; more per-robot
throughput means fewer robots or real radio hardware. So ~15 Mb/s per robot with
4 robots is unreachable here. Traffic is **uplink** (iperf3 client runs inside
the robot netns, robots are senders). Frequent `NPRB 5 / Qm 2` lines in the gNB
log are the *idle* periodic grant (`min_grant_prb` default 5), NOT a cap —
misreading them as a cap wastes hours. `do_SRS` takes a STRING
(`"none"|"periodic"|"aperiodic"`); passing `1` crashes the gNB, and it does not
need changing.

**iperf3 on this box had an ABI clash that silently corrupted throughput data.**
`/usr/bin/iperf3` (Ubuntu 3.9) was loading `/usr/local/lib/libiperf.so.0` from a
source-built 3.16 — same soname, different ABI → `*** stack smashing detected
***` / SIGSEGV. Flows died mid-measurement and were relaunched; that, not UE
reattach, caused the throughput restart artifacts. Repaired: 3.16 backed up to
`logs/archive/iperf3_local_backup_20261020/`, removed from `/usr/local`,
`ldconfig` re-run. `--bind-dev` was dropped from `traffic_streams.py`,
`auto_patrol.py`, `gazebo_viz.py` (3.9 predates it; it is redundant inside the
robot netns where the UE tun is the only route). `install_requirements.sh` no
longer builds iperf3 from source. Legacy `soak_test*.sh` still gate on
`--bind-dev` and will refuse to run.

**Why:** both facts invalidate naive readings of the data — absolute Mb/s from
the archive are unusable (slow-start + CSI-RS off + crashing iperf3), so compare
on *fraction of offered load served*.

**How to apply:** keep campaign traffic at TCP 2.0 + UDP 0.8 Mb/s per robot
(~62% of cell) so throughput still responds to link quality rather than
congestion. See [[sar-sim-stack-operation]], [[research-campaign-tooling]],
[[field-test-follows-sim]].
