# System Architecture — 5G Nomadic Nodes SAR Testbed

*Last validated: 2026-07-09. This is the authoritative description of how the
full stack fits together; `README.md` gives the research framing, and
`docs/A2G_CHANNEL.md` / `docs/RUN_BRIDGE.md` cover the channel model and
bridge in isolation.*

---

## 1. Big picture

One UAV-mounted 5G base station (gNB) serves two ground robots (UEs) while
everything — robot motion, radio channel, network traffic — is simulated but
*coupled*: robot movement in Gazebo changes the RF channel that the real OAI
PHY layer experiences, which changes the throughput/latency that real IP
traffic gets.

```
┌────────────────────────  Gazebo Harmonic (sar_open_field)  ───────────────────────┐
│  sar_uav (PX4 x500, static @ 0,0,40)                                              │
│  sar_ground_alpha / sar_ground_bravo (TurtleBot3 burger, moved via set_pose)      │
└──────────────┬─────────────────────────────────────────────────────────────────────┘
               │ /world/sar_open_field/pose/info (gz-transport, ~every sim step)
               ▼
     channel_bridge.py  ── every 0.5s ──►  logs/channel_bridge_log.csv
               │ A2G path-loss model (FSPL + Al-Hourani LoS/NLoS sigmoid)
               │ telnet "channelmod modify <idx> ploss <dB>"
               ▼
┌───────────  OAI RAN (rfsimulator)  ───────────┐      ┌──────  Open5GS core  ──────┐
│ nr-softmodem (gNB)      telnet :9091          │◄────►│ AMF/SMF/UPF/... + MongoDB   │
│ nr-uesoftmodem alpha    telnet :9092          │ N2/N3│ ogstun 10.45.0.1/16         │
│ nr-uesoftmodem bravo    telnet :9093          │      └─────────────────────────────┘
└───────┬──────────────────────────┬────────────┘
        │ tun fd                   │ tun fd
        ▼                          ▼
┌─ netns robot_alpha ─┐    ┌─ netns robot_bravo ─┐
│ oaitun_ue1          │    │ oaitun_ue1p2        │     ◄── per-robot network
│ 10.45.0.2           │    │ 10.45.0.3           │         namespaces (§4)
└─────────────────────┘    └─────────────────────┘

     gazebo_viz.py: pose/info + /tmp/ch_bridge.log ──► /marker beams+labels,
                                                       /sar/metrics topic
```

Three loosely-coupled loops:

| Loop | Rate | Path |
|---|---|---|
| Channel | 0.5s | Gazebo pose → bridge → path-loss model → telnet → OAI channelmod |
| Traffic | ~9s/sample | `auto_patrol.py` moves robots + runs iperf/ping inside the robot netns |
| Visualisation | 0.5s | `gazebo_viz.py` → Gazebo `/marker` beams + labels |

---

## 2. One-command bring-up: `./start_full_stack.sh`

Each step gates hard (dies on failure) before the next:

1. **Prerequisites** — MongoDB, all 7 Open5GS NFs, ≥2 subscribers in DB.
2. **RAN** via `restart_ran.sh` — kills stale OAI procs, starts gNB → UE alpha
   → UE bravo *in that order* (channel-model indices follow rfsim connection
   order; swapping the order applies path loss to the wrong UE).
3. **Over-air routing + iperf servers** (`scripts/setup_over_air_test.sh`) and
   root-namespace connectivity check (runs while the TUNs are still in the
   root ns — order matters, see §4).
4. **Gazebo** — always kills any existing `gz sim` first and starts exactly
   one instance it owns. `GZ_SIM_RESOURCE_PATH` (PX4 + TurtleBot3 model dirs)
   is exported **here**, on the server process: the gz sim *server* resolves
   `model://` URIs at spawn time, so exporting it around a later client call
   does nothing.
5. **Models** — spawned via the EntityFactory service with a `name:` override,
   so the entities land on `pose/info` as exactly `sar_uav`,
   `sar_ground_alpha`, `sar_ground_bravo` regardless of the SDF-internal
   names. UAV = `worlds/x500_static_hold.sdf` (real x500 geometry, static so
   it holds 40m without PX4 SITL); ground = stock `turtlebot3_burger`
   (deliberately *not* the TrajectoryFollower patrol variants — those would
   fight `auto_patrol.py`'s set_pose motion).
6. **Channel bridge** (`channel_bridge.py`, §3).
7. **Visualisation** (`scripts/gazebo_viz.py`).
8. **Robot namespaces** (`scripts/setup_robot_netns.sh setup`, §4) — gates on
   both robots verifying uplink *and* downlink over the air.

Steps 4/6/7 kill their own stale instances first; the whole script is safe to
re-run at any time.

---

## 3. Channel pipeline

`channel_bridge.py` subscribes to `/world/sar_open_field/pose/info`, computes
per-UE horizontal distance to the UAV, and every 0.5s:

- **Path loss**: FSPL at 3.31968 GHz + Al-Hourani LoS-probability sigmoid
  (A=9.61, B=0.16, η_LoS=1, η_NLoS=20). `ploss = baseline + (pl − pl_ref)`,
  EMA-smoothed (α=0.3), sent over telnet when it moves ≥0.5 dB.
- **Reference**: `pl_ref` at 50m horizontal. **Negative deltas are allowed**
  (robot closer than 50m → sub-baseline ploss, floored at `PLOSS_MIN=10`).
  A 2026-07-03 precaution clamped negatives to 0, which froze ploss at
  baseline for *all* d<50m and erased channel variation from the ML data —
  do not reintroduce it. The high-side cap (`MAX_DELTA_DB`) protects the
  documented **45 dB RLF cliff** (RNTI churn, BLER≈0.9 beyond it).
- **Delay**: propagation offset in samples from 3D distance (30.72 Msps).
- **Doppler proxy**: speed → `forgetfact` mapping (logged; OAI runtime only
  accepts ploss/offset via telnet, the rest is config-time).

Baselines per link (dB): gNB←alpha UL 20, gNB←bravo UL 25, alpha DL 15,
bravo DL 20.

**Measured distance→ploss mapping (alpha UL, 2026-07-09):**

| d_horiz | ploss | TCP (steady) | notes |
|---|---|---|---|
| 10 m | ~13 dB | ~20 Mbps | sub-baseline (negative delta) |
| 50 m | ~17 dB | ~20 Mbps | |
| 100 m | ~31 dB | ~6 Mbps | |
| 145 m | ~37 dB | ~1 Mbps, RTT >100ms | heavy HARQ |
| ≥160 m | ≥39 dB | 0 — link drops | recovers on return, RNTI rotates |

Keep continuous-link patrols within ~150 m; beyond that you are collecting
labelled outages (which is also valid data). After a big teleport, give the
EMA ~15–20 s before judging the link.

---

## 4. User-plane traffic paths and the per-robot namespaces

**The problem**: gNB, UEs, core, and test tools all run on one host. The UE
tunnel IPs (10.45.0.2/.3) were therefore *local addresses of the host*, and
Linux delivers locally-destined traffic via loopback. Consequence: any
base→robot packet (C2 command, e-stop, TCP ACK, `iperf3 -R`) **never touched
the radio**. Historical symptom: "downlink" iperf reporting 1–5 *Gbit/s*
while the gNB's own `dlsch` counter showed 0.15 Mbps; UDP `--reverse` columns
in old patrol CSVs pinned at ~100 Mbps at every distance. Treat all
pre-2026-07-09 DL/UDP-reverse numbers as loopback artifacts.

**The fix** (`scripts/setup_robot_netns.sh`, final step of the stack): move
each UE's TUN interface into a per-robot network namespace. A tun fd stays
valid when its netdev changes namespace, so the nr-uesoftmodem process (root
ns) is untouched — but the robot IPs stop being host-local, and the kernel
must route base→robot traffic via `ogstun → UPF → GTP → gNB → rfsim air →
UE → tun (inside the ns)`. Both directions verified over the air: during a
DL iperf the gNB shows `MCS 27, goodput ~53 Mbps` where the loopback path
showed 0.15.

| Traffic | Command | Path |
|---|---|---|
| Uplink (robot → base) | `sudo ip netns exec robot_alpha iperf3 -c 10.0.0.200 ...` | tun → air → gNB → UPF → host |
| Downlink / C2 (base → robot) | `ping 10.45.0.2` from root ns | ogstun → UPF → gNB → air → tun |

Old root-ns commands (`ping -I oaitun_ue1 ...`) **no longer work** once the
namespaces are up. `setup_robot_netns.sh teardown` restores them.

Approximate capacity (rfsim, n78 106 PRB, this host): **UL ~20–24 Mbps,
DL ~30–50 Mbps** at close range.

---

## 5. Data collection: `scripts/auto_patrol.py`

Modes: `linear` (out-and-back sweep on x), `--both` (2m random-walk steps),
`--free` (recommended — both robots roam continuously at `--speed` m/s with
smooth random headings; a mover thread updates poses every 0.5s while the
metrics loop samples in parallel). In `--free`, each CSV row is tagged with
the robot position at the **midpoint of its measurement window** (~9s of
travel), and the bridge CSV independently records exact geometry every 0.5s.

Per sample, per robot (all uplink tests run inside the robot's netns):

| Column(s) | Test | Meaning |
|---|---|---|
| `*_tcp_tp_mbps`, `*_tcp_retransmits` | iperf3 TCP, 2s + `-O 1` warm-up | Bulk sensor-upload goodput (post-slow-start) |
| `*_udp_tp_mbps`, `*_udp_jitter_ms`, `*_udp_lost_pct` | iperf3 UDP **uplink** at 8 Mbps offered | Live video-feed QoS. Delivered rate = primary feature: the link *throttles* (rate drops, loss stays 0) before it *drops* |
| `*_rtt_min/avg/max_ms`, `*_ping_loss_pct` | ping robot→base | Small-packet uplink-path latency/PDR |
| `*_c2_rtt_ms`, `*_c2_loss_pct` | 5× 64B ping base→robot | **Command/e-stop delivery** — true bidirectional over-air round trip. Observed: 30–70ms/0% loss idle; loss bursts + RTT spikes while the uplink is saturated (C2 competes with telemetry) |
| `*_ss_rsrp_dBm`, `*_ss_sinr_dB`, `*_ul_bits_per_sym`, `*_ul_code_rate`, `*_ul_nb_rbs`, `*_dl_harq_ok/nok`, `*_dl_bler` | parsed from UE logs | Modem-side radio view |
| `*_ploss_dB`, `*_rsrp_computed_dBm`, geometry columns | from bridge log / mover state | Channel ground truth |

**Terminology (thesis-relevant):** OAI's `nrMAC_stats.log` label *goodput* is
correct — post-HARQ successfully delivered MAC bytes. Goodput ≤ throughput;
they coincide at BLER≈0 and diverge at range (retransmissions burn
throughput without adding goodput). iperf numbers are *application-layer
goodput* (~3–4% below MAC due to headers). Reserve "throughput" for offered
load / raw radio capacity.

**gNB-side live stats**: `~/openairinterface5g/nrMAC_stats.log` — per-UE
dlsch/ulsch goodput, HARQ round distribution, MCS, PRB, SNR, overwritten
continuously. Far cheaper to read than tailing the multi-GB process logs.
RNTIs rotate after an RLF — match UEs by CU-UE-ID or freshness, not by
remembered RNTI.

---

## 6. Verification playbook

```bash
./start_full_stack.sh                       # full bring-up, every step gated
scripts/setup_robot_netns.sh status         # namespaces active?
sudo ip netns exec robot_alpha ping -c2 10.45.0.1    # uplink over air
ping -c2 10.45.0.2                          # downlink over air (C2 path)
tail -f /tmp/ch_bridge.log                  # live ploss tracking distance
# move a robot and watch ploss follow:
gz service -s /world/sar_open_field/set_pose --reqtype gz.msgs.Pose \
  --reptype gz.msgs.Boolean --timeout 2000 \
  --req 'name: "sar_ground_alpha" position: {x: 100, y: 0, z: 0.1}'
# is traffic really on the air? watch the gNB's own counters mid-test:
grep -E "dlsch|ulsch" ~/openairinterface5g/nrMAC_stats.log
```

---

## 7. Known limitations / gotchas

- **TCP sampling window**: patrol TCP uses 2s tests (+1s omitted warm-up);
  steady-state on a clean link is ~2× the sampled value. Increase
  `IPERF_DURATION` for accuracy at the cost of sampling cadence.
- **Burger physics creep**: the stock burger drifts ~0.5 m/min in Gazebo
  Harmonic (its `gazebo_ros_*` plugins are Classic-only and fail to load —
  harmless; motion is via set_pose). Position columns come from commanded
  state, so data is unaffected.
- **Bridge `d=` lag**: values scraped from `/tmp/ch_bridge.log` trail the
  commanded position by roughly one EMA step.
- **Log growth**: `/tmp/gnb.log` and UE logs grow to multi-GB during long
  runs. Truncate on RAN restart or lower verbosity before soaks.
- **sudo**: netns test commands need root; scripts follow the existing
  repo convention (`echo <pass> | sudo -S`), and `auto_patrol.py` feeds
  sudo per-call so it can keep running as the normal user (CSV ownership,
  gz-transport partition stay correct).
- **UAV is a static stopgap** (`x500_static_hold.sdf` +
  `scripts/uav_hold_position.py` for teleport-hold experiments). Real PX4
  SITL offboard flight is future work once RPC/CPU contention with the RAN
  is resolved.
