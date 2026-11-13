# Novelty versus SATNAC 2026 (Step 2)

Source for SATNAC content: `docs/paper/satnac_final.tex`, "Where to Fly the Cell Tower".

## What SATNAC established

**Published citation:** I. Makhubela, J. Mwangama, T. Nemaangani and P. Sachan, "Terrain-Aware Positioning of UAV-Mounted Nomadic 5G Nodes for Search-and-Rescue Robotics," in *Proc. Southern Africa Telecommunication Networks and Applications Conf. (SATNAC)*, 2026, pp. 419–[VERIFY last page]. The text was pasted by the author on 12 Nov. The local `docs/paper/satnac_final.tex` is an earlier, longer draft with a different title; use the published version.

SATNAC built the protocol-in-the-loop testbed (OAI 5G SA gNB + Open5GS + Gazebo, with a real-time A2G channel bridge).

**Setup:**
- Two robot UEs; bravo has a 5 dB weaker link budget.
- Five terrains. Six-hour trials, with a static hover at (0, 0, 40 m) for 0–3 h, then a continuous 3-D random walk with no information for 3–6 h.
- Each cycle tests the robots in turn, so flows never compete: a 2-s TCP iperf3 upload, a **2-s UDP burst at 8 Mb/s** standing in for video, and 5 ICMP C2 echoes.
- **C2 availability = at least one of 5 echoes answered.**

**Results:**
- Open-field roaming raises bravo's C2 from 56.5 to 88.7 % (+32.2 pp).
- Urban is neutral. Hills shows a mild penalty.
- Forest roaming costs about 22 pp (96.2 → 74.2 % averaged over both robots), with UDP loss above 70 %.
- Indoor is uncoverable.
- Rule of thumb: hover over clutter; move to close distance in open terrain.
- Limitations stated by SATNAC itself: the phases are not counterbalanced; uninformed roaming is compared against a well-placed hover; the coverage guard inflates forest connectivity (574 rescues).

## What this paper adds

None of the following is in SATNAC.

1. **A terrain-blind learning layer for proactive management.**
   - A 20 s link-failure forecaster trained leave-one-terrain-out, never seeing path loss, RSRP or the terrain label.
   - A planner that acts on its forecasts in closed loop.
   - On-the-spot altitude learners, including the cautious learner.
   - SATNAC had only fixed rules and a random walk.
2. **Four robots and a 3-D placement problem**, judged against the proposal's baselines: fixed hover, robot centroid, and SATNAC's own policy (two_phase).
3. **Two silent measurement failures of the emulated system, with a detector or fix for each:**
   - Dead UE processes contaminate labels: 12.4 % of rows, and 72.2 % of hills failure labels. The fix is a detector plus a relaunch watchdog.
   - Unbounded uplink queuing inside the UE: C2 stays dead 14–18+ min after the load stops. The fix is a delay-adaptive uplink cap.
4. **The adaptive uplink cap as a management mechanism.**
   - It is autonomic, runs at the robot, needs no RAN change, and uses only RTT and bytes sent.
   - Forest patrol: C2 66.8 → 91.8 %.
   - It lifts every placement policy tested.
5. **Results about learning-based management:**
   - Model capacity hurts under leave-one-terrain-out validation.
   - The model does not recover the terrain sign flip.
   - One mission is too short to learn the forest altitude effect.
   - The cautious learner turns a significant loss into a tie.

## Where the two papers overlap, and how the text handles it

| Overlap | Handling |
|---|---|
| **Testbed architecture and channel model** (SATNAC's first contribution) | One paragraph and one table, cited to SATNAC. No re-derivation of the channel terms. |
| **The terrain-dependent sign of the altitude effect.** SATNAC showed it as hover vs roam; this paper re-measures it as a per-10 m climb effect on new data (open +0.168\*, open − forest +0.184\* on a uniform time base) | Presented as the *target* the learner must recover, cited to SATNAC, not claimed as new. What is new is the test of whether a terrain-blind model recovers it. It does not. |
| **SATNAC's two_phase policy** | Used only as a baseline. The planner vs two_phase comparison is new, and is reported with its null control. |
| **The coverage governor and safety guards** | Mentioned as part of the platform, cited, not re-described. |
| **The UE watchdog and RLF findings** | Part of the dead-process contribution. The detector and the label-contamination analysis are new. |

## A risk to flag: congestion may affect SATNAC's own forest result

The congestion finding may partly reinterpret SATNAC's forest-roaming result: 73–80 % UDP loss, and C2 down about 22 pp. SATNAC also noted, without explanation, that RTT of answered C2 probes *fell* in forest-nomadic while video loss rose.

SATNAC's traffic was different from this paper's: **2-s bursts at 8 Mb/s per cycle**, not continuous 1.5 Mb/s from four robots. Each burst is 16 Mbit, and at the forest drain rates measured here (0.13–0.49 Mb/s) one burst would take about 30–120 s to drain against cycles of 20–33 s. A backlog *could* therefore have built up, but this is arithmetic only, **not tested**.

The new paper should state it as one open-question sentence and never claim that SATNAC's result is an artefact.

## Definitions differ between the papers

| | SATNAC | This paper |
|---|---|---|
| C2 available | ≥ 1 of 5 echoes answered | ping loss < 50 % |
| Robots | 2 | 4 |
| Video | 2-s 8 Mb/s bursts | continuous 1.5 Mb/s |

Numbers are therefore not directly comparable across the two papers, and the text must say so wherever both appear.

## Verdict on overlap

The overlap is limited to the platform and the measured sign flip, and both can be handled by citation and one short paragraph each. In my assessment, at least 80 % of the results section is new.

SATNAC is published (author confirmed 12 Nov 2026); citation above. Last page number still to be checked.
