---
name: reattach-failure-causes
description: Root causes of round-4 "reattach_failed" (2026-11-10) — whole-roster re-home, 150 s TUN timeout killing slow attaches, stranded-TUN restart loop; fixes in auto_patrol.py
metadata:
  type: project
---

Round 4 (2026-11-09/10) had 31 failed watchdog reattaches in three classes:

- **5 × "re-home rc=1: oaitun_ue1pN not found in root ns"** — `_rehome_tuns()` ran
  setup_robot_netns.sh for the WHOLE roster, which aborts on the first missing TUN,
  so robot X's recovery failed whenever another robot Y was down. Fixed: re-home only
  the reattached robot (`robots=[...]`) and assert its own IP in the netns.
- **10 × "TUN never reappeared"** — SMF log shows several UEs DID attach 216-334 s after
  teardown, past the 150 s `REATTACH_TIMEOUT_S`. The TUN then sat in the root ns, the
  watchdog read it as "modem gone" and killed it again: a self-inflicted restart loop.
  Fixed: timeout 360 s, and the watchdog re-homes a TUN found in the root ns instead of
  reattaching.
- **16 × "post-reattach C2 still 100%"** — diagnosed 2026-11-10 from live forensics
  (forest, delta): the reattach WORKED (setup verified UL+DL), then the forest link
  flapped — RA, 2× RRC re-establishment, RRC Release → IDLE — and C2 came back ON ITS
  OWN ~2 min after the 75 s check. The UE's "ioctl() failed: No such device" when
  bringing the TUN down on release is harmless (TUN lives in the robot netns; the fd
  stays valid). Real harm was the strike counting: these counted toward the 3-strike
  abort, and strikes were never reset on self-recovery. Fixed: soft-failure budget
  (RLF_MAX_SOFT_FAILURES=6) and reset both counters when C2 recovers on its own.

- **UE segfault during cell search** (found 2026-11-10): a relaunched UE sometimes dies in an
  OAI thread-pool worker ("Tpool*: segfault ... in libc.so.6", same offset each time, in
  `dmesg`), with NO line in its own log. 5x in round-4 run 4 (alpha), and in later runs.
  The old wait loop sat out the full timeout on a dead process. Fixed: reattach_ue
  relaunches on process death (REATTACH_MAX_RELAUNCH=2). Check `sudo dmesg -T | grep
  segfault` whenever a UE "never reappeared".

Separate finding: UE `phy_log_level` went debug→error on 2026-10-23, which silently
emptied SS-RSRP/SS-SINR everywhere (fast_radio_sampler, auto_patrol ss_* columns).
fast_radio_sampler now also records CSI-RS SINR/CQI (`csi_*` columns, LOG_I).

Open5GS logs (root-only, `/var/log/open5gs/*.log`, rotated daily) are the only
timestamped record of past attaches — UE/gNB logs in /tmp are truncated at each
stack-up. UPF "Source IP Spoofing" drops with SRC 10.0.0.20x are a harmless side effect
of a fresh TUN briefly in the root ns.

Testing trap: `setup_robot_netns.sh teardown` DELETES the namespace; re-creating it
left the TUN invisible to checks for ~60 s. Production never deletes the ns, so test
stranding with `ip -n robot_X link set <tun> netns 1` instead.

Related: [[watchdog-roster-keyerror]], [[watchdog-reattach-and-sudo-killpg-traps]],
[[coverage-rlf-kills-ue-process]].
