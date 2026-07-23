# 5G Nomadic Nodes for Multi-Robot Search & Rescue

MSc research testbed for **AI-assisted 5G nomadic nodes** — UAV-mounted base stations
providing on-demand 5G NR connectivity to ground robots in search-and-rescue (SAR)
scenarios. This repo hosts the OAI 5G SA stack, the Gazebo-to-OAI channel bridge that
ties simulated UAV/UGV mobility to real PHY-layer link conditions, and the soak-test
logs used to validate stability.

> MSc candidate: Electrical Engineering (Telecommunications), University of Cape Town
> Supervisor: Dr. Joyce Mwangama
> Funding: Telkom-NRF FutureTech Programme, DIGITAfrica (Horizon Europe)

---

## What this is

The core research question is about **AI-assisted nomadic 5G communication networks**
— search-and-rescue is the application context, not the contribution itself. The
testbed here validates that a UAV-mounted 5G base station can maintain a stable link
to ground robot UEs while the UAV's position (and therefore the air-to-ground channel)
changes dynamically.

Concretely, this repo implements:

1. A working **OpenAirInterface (OAI) 5G SA stack** (gNB + 2× UE) running over the
   `rfsimulator`, Band n78, 106 PRB, backed by an Open5GS core.
2. A **Gazebo → OAI channel bridge** (`channel_bridge.py`) that subscribes to robot
   pose topics in Gazebo, computes air-to-ground (A2G) path loss from that pose, and
   pushes live `channelmod modify` commands to OAI over telnet — so link quality in
   the RF simulator reflects actual simulated robot movement.
3. A **soak-test harness** (`soak_test.sh`) that runs the full stack under load
   (ping + iperf3 in both directions) for extended periods and logs RF/PHY and
   application-layer metrics for stability validation.

## Current status

**See [`docs/SYSTEM_ARCHITECTURE.md`](docs/SYSTEM_ARCHITECTURE.md) for the full,
current system description** — components, traffic paths, metrics dictionary,
verification playbook, and operational limits.

As of 2026-07-09 the full stack is one command (`./start_full_stack.sh`) and the
whole loop is closed and verified against the gNB's own MAC counters:

- **Real robot models** — PX4 x500 UAV (static hold at 40 m) + 2× TurtleBot3
  burger, spawned into `sar_open_field` with the names the bridge expects.
- **Continuous channel coupling** — robot distance maps monotonically to path
  loss over the whole 10–190 m range (10 m→~13 dB … 190 m→~42 dB alpha UL),
  including sub-baseline values inside the 50 m reference (dead-zone fixed).
- **Honest traffic in both directions** — per-robot network namespaces force
  uplink *and* downlink over the air. Uplink ~20–24 Mbps TCP close range,
  degrading to link loss at ~160 m (recovers on return); C2/e-stop downlink
  probes run 30–70 ms RTT and visibly degrade when the uplink saturates.
- **ML data collection** — `scripts/auto_patrol.py --free` roams both robots
  continuously and logs geometry + channel + TCP/UDP-video/ping/C2 + modem
  metrics per sample.

Earlier milestone: the 45-minute soak test (`logs/soak_20260702_005642/`)
validated link stability (0 RNTI churn, 22/22 iperf bursts, 0% ping loss)
after switching the channel models to **AWGN** — Rayleigh1's slow-fade dwell
had been causing RLF instability.

---

## Repository structure

```
sar_sim/
├── start_full_stack.sh        # ONE-COMMAND bring-up: core -> RAN -> Gazebo -> models
│                              #   -> bridge -> viz -> robot netns (every step gated)
├── channel_bridge.py          # Gazebo pose -> A2G path loss -> channelmod modify (telnet)
├── restart_ran.sh             # Strict-order RAN restart after RLF (gNB -> alpha -> bravo)
├── soak_test.sh               # Runs stack under sustained ping + iperf3 load, logs metrics
├── verify_stack.sh            # Health check: confirms gNB + UE processes are up
│
├── oai_configs/               # Working OAI gNB / UE configs (the "known good" set)
│   ├── gnb.sa.band78.rfsim.conf
│   ├── nrue_alpha.conf
│   ├── nrue_bravo.conf
│   ├── channelmod_sar.conf    # Channel model definitions (AWGN)
│   └── SIMULATION_GUIDE.md
│
├── configs/                    # Historical / alternative config variants
│                                # (srsRAN, UERANSIM, ZMQ-based experiments)
│
├── logs/
│   ├── channel_bridge_log.csv  # Bridge's own path-loss update log
│   ├── sar_metrics_*.csv       # Per-session SAR metrics
│   └── soak_YYYYMMDD_HHMMSS/   # One directory per soak test run:
│       ├── summary.txt         #   Human-readable run summary
│       ├── sinr_samples.csv    #   Per-UE RSRP/SINR time series
│       ├── ping_a2b.csv        #   alpha->bravo ping log (see note below)
│       ├── iperf_bursts.csv    #   Per-burst throughput, both directions
│       ├── rntis_before.txt / rntis_after.txt   # Link-survival check
│       └── gnb_stats.log, bridge_stdout.log     # Raw process logs
│
├── ros_ws/                     # ROS 2 workspace
│   └── src/sar_integration/    # SAR robot integration node (sar_node.py)
│
├── scripts/
│   ├── auto_patrol.py          # ML data collection: moves robots, logs full metrics
│   │                           #   (--free = continuous roam; adds C2 downlink probes)
│   ├── setup_robot_netns.sh    # Per-robot netns: forces DL/C2 traffic over the air
│   ├── setup_over_air_test.sh  # Over-air routing + iperf3 servers
│   ├── gazebo_viz.py           # Beams + metric labels drawn into the Gazebo scene
│   ├── metrics_collector.py    # UE-log parser used by auto_patrol
│   ├── exp1_distance.sh        # Distance-sweep experiment
│   ├── exp2_altitude.sh        # Altitude-sweep experiment
│   ├── exp3_search_pattern.sh  # Search-pattern trajectory experiment
│   ├── parse_gnb_metrics.py    # gNB log -> structured metrics
│   └── set_channel_loss.sh     # Manual channelmod override for testing
│
├── worlds/
│   ├── sar_open_field.sdf      # Current world: 500m open field + reference markers
│   └── x500_static_hold.sdf    # Real x500 geometry, static (UAV holds 40m w/o PX4 SITL)
│
├── docs/
│   ├── SYSTEM_ARCHITECTURE.md  # THE current full-system description (start here)
│   ├── A2G_CHANNEL.md          # Channel model details
│   └── RUN_BRIDGE.md           # Bridge usage in isolation
│
└── OAI_SAR_STACK_REPORT.md     # Standing technical notes on the OAI setup
```

> **Note on `ping_a2b.csv`**: the ping process is a long-running background logger
> and its CSV is **not reset between soak test runs** — a single file can span many
> sessions. Always filter by the run's timestamp window (from `summary.txt`) before
> computing statistics from it; do not treat row count as reply count for a given run.

---

## Environment

| Component | Version / detail |
|---|---|
| OS | Ubuntu 22.04 |
| ROS 2 | Humble |
| Gazebo | Harmonic 8.14.0 (headless `gzserver` only — GTX 1650 crashes `gzclient`) |
| OAI | `nr-softmodem` (gNB) + `nr-uesoftmodem` (UE), rfsimulator |
| Core | Open5GS 2.8.0 + MongoDB 6.0 |
| Band / config | n78, 106 PRB, 30 kHz SCS |
| PLMN / TAC | 999/70, TAC 1 |
| gNB telnet port | 9091 (9090 is occupied by Open5GS) |
| iperf3 | 3.16, built from source (`--bind-dev` support required) |

UE identities:

| UE | IMSI | Robot model | Tunnel IP |
|---|---|---|---|
| alpha | `999700123456780` | `sar_ground_alpha` | `10.45.0.2` |
| bravo | `999700123456781` | `sar_ground_bravo` | `10.45.0.3` |

---

## Key architectural decisions

- **Channel model: AWGN, not Rayleigh1.** Rayleigh1's slow-fade dwell was causing
  RLF events under the multi-robot bridge; AWGN gives a stable baseline for
  validating link survival. This is a simplification for the current testbed phase,
  not a claim about realistic A2G fading — worth revisiting once basic stability is
  proven at scale.
- **Channel model indices follow rfsim client connection order.** The gNB assigns
  channel model indices in the order UEs connect, so **alpha must connect before
  bravo** on every restart, or path-loss updates will be applied to the wrong UE.
- **RAN restart order after any RLF is strict:** `gNB → sleep 10s → alpha → sleep 20s
  → bravo → sleep 20s`. Deviating from this order has caused registration failures.
- **rfsimulator config requires array syntax**, e.g.
  `rfsimulator = ({ serveraddr = "server"; });` — the non-array form silently fails.
- **UE config requires `dnn = "internet"`** for the PDU session to establish.
- **A2G path-loss model**: log-distance with FSPL + Al-Hourani LoS/NLoS sigmoid
  (3.31968 GHz, A=9.61, B=0.16), EMA-smoothed (α=0.3). Negative deltas (robot
  closer than the 50 m reference) are allowed and floored at 10 dB; the high side
  is capped by `MAX_DELTA_DB` below the documented ~45 dB RLF cliff.
- **Per-robot network namespaces** (`scripts/setup_robot_netns.sh`): the UE TUNs
  are moved into `robot_alpha`/`robot_bravo` after attach so the UE IPs stop
  being host-local — otherwise base→robot traffic (C2, e-stop, ACKs, any
  `--reverse` test) is delivered via loopback and never crosses the radio.
  Uplink tests run via `sudo ip netns exec robot_<x> ...`; downlink is a plain
  ping/iperf to `10.45.0.2/.3` from the root namespace.

---

## Running

```bash
cd ~/sar_sim
./start_full_stack.sh          # brings up EVERYTHING, gated at every step

# Collect ML training data (both robots roam freely, full metrics per sample):
python3 scripts/auto_patrol.py --free --speed=1.5 --duration=600

# Results: logs/patrol_<timestamp>.csv (+ continuous logs/channel_bridge_log.csv)
```

Health checks at any point:

```bash
./verify_stack.sh                            # gNB + UE processes up
scripts/setup_robot_netns.sh status          # robot namespaces active
sudo ip netns exec robot_alpha ping 10.45.0.1   # uplink over air
ping 10.45.0.2                               # downlink/C2 over air
```

Legacy soak test (pre-dates the netns move — its in-root-ns test commands
need updating before reuse): `./soak_test.sh --duration 2700`.

**Full operational guide:** [`docs/WORLDS_RUNBOOK.md`](docs/WORLDS_RUNBOOK.md) —
selecting the world (open field vs. urban NLoS), running every patrol mode,
camera/video streaming over 5G, and end-to-end troubleshooting.

---

## Known issues / open items

- `soak_test*.sh` still use root-namespace test commands (`-I oaitun_ue1`) that
  stopped working when the robot netns moved the TUNs — port them to
  `ip netns exec` before the next soak.
- Patrol CSVs recorded **before 2026-07-09** have two data-quality caveats:
  UDP columns are loopback artifacts (~100 Mbps constants — the `--reverse`
  test never crossed the air), and ploss is frozen at baseline for d<50 m
  (negative-delta clamp). Filter or regenerate before training on them.
- `/tmp/gnb.log` + UE logs grow to multi-GB during long runs; truncate on RAN
  restart or lower verbosity before soaks.
- `ping_a2b.csv` needs a per-run reset (or a documented time-windowing step baked
  into the summary generator) so future analyses don't need to manually filter it.
- RSRP in the latest soak test was essentially flat throughout the run — worth
  confirming this reflects intended (near-)stationary UAV positioning during the
  soak test rather than a gap in pose-update propagation from Gazebo to the bridge.
- Several `configs/` variants (srsRAN, UERANSIM, ZMQ) are earlier experimental paths
  kept for reference; `oai_configs/` is the current known-good set.

---

## Related work

Undergraduate FYP (SDR-based 5G NR-U/Wi-Fi coexistence testbed, IEEE WCNC 2026,
Kuala Lumpur) is a separate, earlier project and not part of this MSc contribution:
`github.com/MgwenaHulela/Implementing-5G-nr-u-lte-u-USING-sdr`
