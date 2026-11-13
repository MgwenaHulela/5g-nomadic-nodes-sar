---
name: watchdog-reattach-and-sudo-killpg-traps
description: RLF watchdog was starved by probe_busy and its reattach stranded TUNs in the default netns; sudo env_reset + use_pty break roster passing and killpg
metadata:
  node_type: memory
  type: project
  originSessionId: 30a2c230-eb87-4138-8520-68e653b73eed
  modified: 2026-10-21T20:43:25.496Z
---

Four non-obvious traps in the SAR stack, all found 2026-10-21 and all of which
silently produced plausible-looking but wrong data.

**The RLF watchdog was completely disabled.** It skipped measuring whenever
`probe_busy` was set. That guard was written when throughput was a short
per-cycle iperf burst; with persistent flows the link is *always* busy —
measured cycle period 16.4 s against a probe window of 16.4 s, a **zero**
idle gap — so "skip while busy" became "never run". A UE died 13 cycles into
a 5.5 h run and was never recovered. Fix: don't skip on `probe_busy`; rely on
`RLF_CONFIRM_S` of sustained loss plus uplink corroboration, and treat a TUN
missing from its netns as immediate, unambiguous evidence.

**A reattached UE's TUN is created in the DEFAULT namespace** and must be
re-homed with `setup_robot_netns.sh setup`. If that fails the robot looks
perfectly attached from the host (pinging its UE IP works, C2 reads healthy)
while being unable to send anything from its own netns — it logs zero traffic
for the rest of the run. Always verify the TUN actually landed in `robot_*`.

**`sudo` has `env_reset`**, so `SAR_ROBOTS` is STRIPPED: any script run via
sudo falls back to the default 4-robot roster. Pass it explicitly:
`sudo env SAR_ROBOTS=... bash script`. And `sudo_run()` does NOT raise on a
non-zero exit, so `try/except Exception` around it catches nothing — check
`returncode` or failures are invisible.

**`sudo` also has `use_pty`**, so it runs the command in its OWN session. The
child does not share sudo's process group and SURVIVES `os.killpg()`. Children
are root-owned too, so an unprivileged `killpg` fails with EPERM. Leaked video
senders then pile up: four generations streaming into one collector, their
independent frame_id sequences interleaving so no frame ever completed
(delivery 0.3%) while goodput read ~3x target. Sweep by name via sudo as well.

**Python's default SIGTERM handler skips `finally`**, so the campaign runner
stopping a patrol leaked its streams. Install a handler that raises.

**Why:** every one of these fails *quietly* and leaves data that looks real.

**How to apply:** after any reattach, check the TUN is in `robot_*` and that
goodput and frame-delivery recovered — not just that the process exists.
See [[coverage-rlf-kills-ue-process]], [[sar-sim-stack-operation]],
[[iperf3-abi-clash-and-cell-capacity]].
