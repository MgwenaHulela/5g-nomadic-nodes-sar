---
name: sar-silent-failure-checks
description: "SAR testbed fails silently in several ways that produce normal-looking data; check ploss!=0, ss_stale!=1, UE count, gNB alive, TUN in netns before trusting any run"
metadata:
  node_type: memory
  type: project
  originSessionId: 30a2c230-eb87-4138-8520-68e653b73eed
  modified: 2026-10-23T09:39:50.496Z
---

The SAR stack has several failure modes that produce **data that looks
completely normal**. None raises an error. Found and fixed 2026-10-21/23, but
any roster/traffic/logging change can reintroduce them, so **validate every
campaign run** before trusting it:

1. **`ploss_dB` identically 0.00 across a whole run ⇒ the run is VOID.** The
   channel bridge died (it used to open telnet to all four robots regardless
   of `SAR_ROBOTS`, failed on charlie:9094, exited). No pathloss means no
   terrain and no distance effect; `corr(distance, throughput)` goes
   *positive*. Healthy runs show ploss spanning ~0–28 dB and corr ≈ −0.33.
2. **TUNs must be INSIDE `robot_<name>` netns, not the root ns.** `sudo`
   strips `SAR_ROBOTS` (env_reset), so `setup_robot_netns.sh` fell back to 4
   robots, aborted, and left every TUN in root. The stack looks healthy and
   host pings succeed while the robots are isolated — and this also defeats
   the C2 watchdog, because a root-ns TUN answers a host ping.
3. **Count the UEs.** A dead UE used to be "stood down" and the run continued
   a robot short; those rows look exactly like terrain outage. Now the run
   aborts (exit 4). A dead gNB aborts too (exit 3) — it used to just zero
   every radio column for hours.
4. **Never `pkill -9` a UE while the gNB runs.** SIGKILL drops the rfsim TCP
   stream mid-packet and the gNB dies on a `beam_map` AssertFatal in
   `radio/rfsimulator/simulator.cpp`. Use SIGTERM.
5. **A dead UE often cannot re-attach in rfsimulator at all** (`synch Failed`
   forever — the gNB's rfsim server holds the dead client's slot). Only a full
   stack restart recovers it; that is why an unrecoverable UE ends the run.

6. **`phy_log_level = "error"` SILENTLY EMPTIES ALL RSRP/SINR COLUMNS.** OAI
   emits the SS-RSRP line from `nr_ue_measurements.c` under `LOG_D(PHY, ...)`,
   so it exists ONLY at `"debug"` — there is no intermediate level. Setting the
   UE configs to `"error"` on 2026-10-23 (to stop a 1.6 GB flood and fix UE
   reattach) emptied `ss_rsrp_dBm`/`ss_rsrp_dBre`/`ss_sinr_dB`/`pbch_rsrp` in
   the per-cycle CSV **and every column of every `*_fastradio.csv`** for the
   whole 4-round campaign — 1.56 M rows of nothing. The rows still appear, at
   the right rate, with the right headers. The tell is **`ss_stale = 1` and
   `ss_samples = 0` on every row**: check those before trusting any radio
   column. The surviving RSRP (`rsrp_computed_dBm`) is a FORBIDDEN input.
   **Fallback that needs no config change:** the UE logs its CSI report
   (`SINR = %d dB, CQI = %d`) at ~7 Hz at the *current* level —
   `scripts/csi_sidecar.py` samples it at 5 Hz into `logs/csi_<ts>.csv`.
   SINR/CQI are UE-measured, so they are allowed inputs; RSRP is not
   recoverable without `"debug"`.

**Why:** every one of these yields a plausible-looking CSV, so a 4-day
campaign can be entirely worthless without a single error message.

**How to apply:** before analysing any run, check ploss variance, UE/TUN
placement, and the exit code. Keep `phy_log_level = "error"` — at `debug` the
gNB wrote 1.6 GB / 21.7M lines from inside the bottleneck thread (it was waste,
not the throughput limiter). Greedy/uncapped traffic is *worse* than capped:
it collapses the link, giving 2.37 Mb/s mean and 15% C2 versus ~6 Mb/s and
82–100% capped. See [[iperf3-abi-clash-and-cell-capacity]],
[[sar-sim-stack-operation]], [[research-campaign-tooling]].
