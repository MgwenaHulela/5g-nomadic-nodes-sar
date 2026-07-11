# What the Testbed Says: Findings on UAV-Mounted 5G for Multi-Robot SAR

*Innocent Nhlanhla Makhubela — MSc Electrical Engineering (Telecommunications), UCT.*
*Findings from the 2026-07-10/11 measurement campaigns on the sar_sim testbed
(OAI 5G SA over rfsimulator + Open5GS core, Gazebo-coupled A2G channel).
Method and system details: [`SYSTEM_ARCHITECTURE.md`](SYSTEM_ARCHITECTURE.md).*

---

## The experiment in one paragraph

One UAV-mounted gNB serves two ground robots (UEs) while everything —
robot motion, the air-to-ground radio channel, and real IP traffic — is
simulated *and coupled*: robot and UAV movement in Gazebo continuously
re-shapes the path loss the real OAI PHY layer experiences, which changes
the throughput, latency and reliability that real traffic gets. Four
campaigns were run: a fixed-altitude envelope survey (90 min), an
edge-crossing run that deliberately drove robots out of coverage, a second
clean 90-minute survey, and a 3-hour **nomadic** run in which the UAV flew
a 9-waypoint schedule varying altitude (20–60 m) and ground position while
the robots roamed freely. Corpus: ≈2,100 link measurements spanning
6–140 m of range, each carrying geometry, channel state, TCP/UDP/video
throughput, latency, and command-channel (C2) health.

**Credibility baseline:** across the full 3-D geometry range, the emulated
channel matched the analytic A2G model (FSPL + Al-Hourani LoS/NLoS) with a
mean absolute error of **0.5 dB or better** — the behaviors below arise
from the physics under study, not from simulator artifacts.
(Figure: `logs/analysis_patrol_20260711_020453/1_ploss_vs_distance.png` —
1,060 points on the perfect-agreement diagonal.)

---

## Finding 1 — Altitude is a range lever, and it is nearly free

Measured with the same robots at the same horizontal distances while the
UAV hovered at different heights (3-hour nomadic run):

| Robot distance | UAV 20 m | UAV 40 m | UAV 60 m |
|---|---|---|---|
| 20–50 m (close) | 18.8 dB / 10.5 Mbps | 15.6 dB / 11.4 Mbps | 17.0 dB / 10.3 Mbps |
| 60–90 m (mid)   | **32.0 dB / 7.1 Mbps** | 25.7 dB / 9.3 Mbps | **22.0 dB / 10.8 Mbps** |
| 90–120 m (far)  | — (outside safe radius) | 31.9 dB / 7.0 Mbps | 27.0 dB / 9.7 Mbps |

*(cells: path loss / TCP goodput, robot alpha)*

When users are close, altitude barely matters. At mid range, climbing from
20 m to 60 m buys **10 dB of link margin** and lifts TCP throughput by
~50%. The physics is elevation angle: height converts the link from
NLoS-dominated to line-of-sight. Expressed as a coverage rule, the safe
operating radius (worst-budget UE held ~5 dB under the radio-link-failure
cliff) nearly doubles with altitude:

> **safe radius ≈ 86 m at 20 m altitude → 117 m at 40 m → 149 m at 60 m**

**Implication:** a nomadic aerial base station should not chase its ground
users horizontally — *climbing* is the cheaper, faster coverage control,
and it costs nothing when users are close.

---

## Finding 2 — Same field, different radios, different worlds

The two UEs differ only in link budget (bravo runs a 5 dB higher baseline
path loss — a proxy for a weaker antenna/power class). The consequence is
qualitative, not just quantitative:

- **Alpha** (stronger budget) is *distance-limited*: throughput starts at
  ~11 Mbps and degrades smoothly with range (10.8 → 6.1 Mbps across the
  distance bins).
- **Bravo** (weaker budget) is *budget-limited*: capped flat at ~7.8 Mbps
  at **every** distance — its own radio, not geometry, sets its ceiling —
  and it reaches the coverage cliff ~5 m sooner.

**Implication:** coverage planning for a heterogeneous robot fleet cannot
use a single number. Each radio class has its own envelope, and the
weakest radio defines the team's operating radius.

---

## Finding 3 — Three kinds of traffic fail in three different ways

| Traffic | Behavior with range | Failure mode |
|---|---|---|
| Live video (8 Mbps UDP) | Full rate everywhere inside the envelope — 100% of samples ≥7.9 Mbps in both long runs | **Binary**: no degraded middle; it works, then it vanishes |
| Bulk upload (TCP) | Bends gracefully: 11 → 5 → 1 Mbps | Gradual, until the cliff |
| Commands / C2 (small packets, downlink) | Flat ~38 ms round trip at every distance *and* altitude | Robust until just before the edge, where RTT explodes (250 ms → 1.2 s) **before** packets drop |

A SAR operator therefore experiences the coverage edge as a fixed
sequence: **video cuts out first, file transfers crawl, and command
latency spikes as the final warning.** The C2 RTT spike is an intrinsic
early-warning signal — the network announces the edge before crossing it.

---

## Finding 4 — The edge is a cliff with memory

Around 38–43 dB of path loss (≈130–135 m at 40 m altitude), the link does
not fade — it **fails**. And the failure is *sticky*: in the edge-crossing
run, a robot that retreated to positions where the link had been perfect
found it still dead; one UE never recovered without a full RAN restart.
Radio-link failure at the PHY/RRC level is not undone by improving the
geometry afterwards.

**Implication:** at the coverage edge you cannot reliably stop, recall, or
re-task a robot. The edge must never be reached *reactively* — prevention
is the only strategy that works. Finding 1 supplies the emergency
maneuver: the UAV climbing extends the edge outward instantly, faster than
any ground robot can drive back in.

---

## Finding 5 — A moving base station endangers its own users

When the UAV repositioned ~50 m laterally, robots at the far periphery
were instantly stranded outside the relocated coverage zone; in an
unguarded trial this destroyed both radio links within minutes (sticky
RLF, per Finding 4). During the guarded 3-hour run, the protective
controller had to intervene **7 times** — each one a link death that
would otherwise have occurred.

Nomadic repositioning is not free: the node's coverage moves with it, and
slow ground assets cannot follow. The data implies the safe recipe
directly:

> **Climb first** (expanding the radius so all users are safely inside),
> **then move laterally, then descend** — coordinating the node's motion
> with its users instead of assuming they keep up.

---

## The one-sentence summary

**Altitude, not horizontal pursuit, is the nomadic node's primary coverage
control — and because link failure at the edge is unrecoverable in place,
that control must be exercised predictively, guided by early-warning
signals the traffic itself provides.**

---

## Data & figures index

| Campaign | File | Content |
|---|---|---|
| Envelope survey (90 min, h=40) | `logs/patrol_20260710_202806.csv` | 268 samples/robot, artifact-free, 5–103 m |
| Edge-crossing | `logs/patrol_20260710_113442.csv` | Cliff, outages, sticky-RLF evidence |
| First survey (90 min) | `logs/patrol_20260710_131330.csv` | Valid after filter: drop rows with `tcp==0 & ping_loss==0` |
| **Nomadic (3 h, z=20–60 m)** | `logs/patrol_20260711_020453.csv` | 530 samples/robot, 0 outages, moving UAV |
| Continuous channel log | `logs/channel_bridge_log.csv` | Geometry + injected ploss every 0.5 s |

Per-campaign reports and figures: `logs/analysis_*/` (generated by
`scripts/analyze_patrol.py`). Known measurement caveats — stale RSRP/SINR
during outages, the pre-fix iperf artifact, EMA lag — are documented in
`SYSTEM_ARCHITECTURE.md` §7 and handled by the analysis tooling.
