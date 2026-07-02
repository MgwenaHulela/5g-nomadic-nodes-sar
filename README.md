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

The 45-minute soak test (`logs/soak_20260702_005642/`) is the latest validated run:

- **0 RNTI churn** — both UEs held their RRC connection for the full run
- **0 sync-loss sentinel events** (SINR never approached the -70 dB RLF threshold)
- **SINR**: alpha 21.0–25.8 dB (mean 22.8), bravo 24.4–29.6 dB (mean 26.5), flat over time
- **iperf3**: 22/22 bursts both directions, a2b ~12.0 Mbps, b2a ~6.5 Mbps, 0 failed bursts
- **Ping**: 0% loss, mean RTT 61.4 ms across the run window

This confirms a key architectural decision (see below): switching all channel models
to **AWGN** resolved earlier RLF instability caused by Rayleigh1's slow-fade dwell.

---

## Repository structure

```
sar_sim/
├── channel_bridge.py        # Gazebo pose -> A2G path loss -> channelmod modify (telnet)
├── restart_ran.sh            # Strict-order RAN restart after RLF (gNB -> alpha -> bravo)
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
│   ├── exp1_distance.sh        # Distance-sweep experiment
│   ├── exp2_altitude.sh        # Altitude-sweep experiment
│   ├── exp3_search_pattern.sh  # Search-pattern trajectory experiment
│   ├── parse_gnb_metrics.py    # gNB log -> structured metrics
│   └── set_channel_loss.sh     # Manual channelmod override for testing
│
├── worlds/
│   └── sar_world.sdf           # Gazebo world definition
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
  (3.31968 GHz, A=9.61, B=0.16), EMA-smoothed (α=0.3), capped at +15 dB per model to
  avoid unrealistic single-step jumps.

---

## Running a soak test

```bash
# 1. Bring up the core + RAN in strict order
cd ~/sar_sim
./restart_ran.sh

# 2. Bring up the Gazebo world + robot models (headless)
gzserver worlds/sar_world.sdf &

# 3. Start the channel bridge (subscribes to Gazebo pose, drives channelmod)
python3 channel_bridge.py

# 4. Run the soak test (ping + iperf3 both directions, configurable duration)
./soak_test.sh --duration 2700

# Results land in logs/soak_<timestamp>/
```

Health check at any point:

```bash
./verify_stack.sh   # expects 2 gNB + 4 UE processes running
```

---

## Known issues / open items

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
