# Peer Review Report

## Manuscript Information
- **Title**: Bounded Queues Before Learned Placement: Autonomous Management of a UAV-Borne 5G Cell for Search-and-Rescue Robots
- **Manuscript ID**: N/A (simulated NOMS 2027 full-paper submission)
- **Review Date**: 2026-11-12
- **Review Round**: Round 1 review

---

## Reviewer Information

### Reviewer Role
Peer Reviewer 3 (Perspective): Cross-disciplinary / Practical

### Reviewer Identity
Field-robotics and public-safety communications practitioner: ground-robot deployments in SAR exercises, first-responder broadband (private LTE/5G, mission-critical QoS), and operational command-and-control requirements.

### Review Focus
Whether the metrics mean anything to an incident commander or a robot operator; whether constant-rate video and the uplink cap represent how real robot video and C2 behave; and whether the system could be deployed: UAV endurance, running the cap on robot hardware, and the terrain-blind premise. Above all, whether the lessons would change what a responder or system designer does.

---

## Overall Assessment

### Recommendation
- [ ] Accept
- [ ] Minor Revision
- [x] **Major Revision**
- [ ] Reject

### Confidence Score
4. Operational and public-safety networking is my core area. ML forecasting methodology is adjacent, and I leave its detailed critique to the other reviewers.

### Summary Assessment
The paper builds a terrain-blind management layer (failure forecaster, risk planner, on-line altitude learner) for a UAV-borne OAI gNB serving four emulated robots and flies it in closed loop. The headline result is not learned. Forest "outages" were minutes of uplink queuing inside the OAI UE under 1.5 Mb/s constant-rate video, and a per-robot RTT-driven AIMD cap with strict C2 priority removed them (C2 66.8 % to 91.8 %, no-robot-up time 17.4 % to 0.9 %). Learned placement did not beat hover or the centroid. From a practitioner's standpoint this is an honest and useful paper. "Check your queues and your process liveness before you learn where to fly" is a lesson I would pass to a system integrator, and the negative results are stated plainly. Its weaknesses are operational. The C2 metric (ping with under 50 % loss) is not a C2 requirement. The cap is evaluated with the very probe traffic it prioritises. The paper never compares the cap with the standard network-native remedy, separate 5G QoS flows/DRBs with UE logical-channel prioritisation for C2. "Video" at 0.1 Mb/s with 1 s latency is not checked against operator needs. The UAV endurance and terrain-blind premises are asserted rather than argued. Each is repairable without new hardware, mostly by re-analysis and a small number of added runs, so I recommend major revision.

---

## Strengths

### S1: The diagnosis is the kind of finding practitioners actually need
Re-attributing "coverage outages" to unbounded buffering inside the UE is a real and transferable lesson. In my experience, field teams routinely blame "the radio" for what is a buffering or application-rate problem. The static load test isolates this cleanly: robots are parked, the no-load phases are clean, the kernel qdisc showed no backlog while about 79 MB sat in the UE, and recovery took 14–18+ min after the video stopped.
**Evidence Anchor**: `table: Table (tab:static) — "Recovery after video 14--18+ min" (no cap) vs "none" (fixed and adaptive cap)`

### S2: The fix uses only signals available on a real robot and needs no RAN change
The cap's inputs are RTT and bytes sent, and the mechanism is HTB on the robot's own interface. An integrator could deploy this on a robot companion computer without touching the network operator's equipment, and that matters operationally when the cell is brought by a different agency than the robots.
**Evidence Anchor**: `text: §III-D "Its only inputs, RTT and bytes sent, are available on real hardware."`

### S3: Negative and null results are reported with matched controls
The same-policy null control (Table A3), which shows that urban and hills comparisons are uninterpretable, and the statement that no learned arm beat a baseline protect a reader from deploying a learned planner on weak evidence. That is the right posture for a safety-relevant system.
**Evidence Anchor**: `table: Table A3 — null control +18.7 (urban) and -21.7 (hills), marked "Usable: no"`

### S4: The "operational" metrics go beyond mean availability
Reporting "all four robots up" and "no robot up" alongside the mean is closer to how a mission commander thinks than a single average: a team-level outage is what stops a search. This is a good foundation (but see W1).
**Evidence Anchor**: `table: Table A5 — "No robot up 17.4 % / 0.9 %", "All 4 up 52.0 % / 72.7 %"`

---

## Weaknesses

### W1: "C2 up" is not an operational C2 criterion, and time-share metrics hide outage durations
**Problem**: A robot counts as connected when fewer than 50 % of its pings are lost, and no latency bound applies. A teleoperated robot whose C2 has 40 % loss or 1–2 s latency is not under control: most robot stacks trigger their e-stop or loss-of-link behaviour on a heartbeat timeout of a few hundred milliseconds to about 1 s. The paper also reports only time shares ("all four connected 70 % of the time"), never how long outages last. Thirty percent spread over many 2-s blips and the same thirty percent as one 27-min outage are opposite outcomes for a rescue mission.
**Evidence Anchor**: `text: §IV "a robot's C2 is up when fewer than 50 % of its probes are lost"`
**Why it matters**: A system designer cannot translate "70.5 % all-four-up" into a decision (is a 40 m hover acceptable for a 90-min search?) without the outage-duration distribution and a latency-qualified definition. The paper's own key phenomenon, outages that last 14–18 min, is precisely a duration effect that time shares under-represent.
**Suggestion**: (a) Redefine or add a C2 criterion with both a loss and a latency bound, e.g. at least 90 % of heartbeats delivered within 500 ms over a 2-s window, and justify the bound from robot fail-safe timeouts. (b) For every arm and the patrol, report the outage-duration distribution (median, p95, longest), the number of outages per hour, and the time to re-establish C2 after an outage. (c) Translate one result into mission terms, e.g. "expected robot-minutes stalled per 90-min mission". The data appear to be time-indexed already, so this is re-analysis.
**Severity**: Major
**Confidence**: 5 — core expertise: robot C2 and loss-of-link behaviour

### W2: The cap is evaluated with the probe traffic it prioritises
**Problem**: The cap puts "C2 and ICMP" in a high-priority class, and C2 is measured by ping. The C2 metric therefore measures the latency of the exact packet class the shaper protects. Real robot C2 is not ICMP. It is typically ROS 2/DDS (UDP with reliability and heartbeats), MAVLink, or TCP/WebSocket teleop, with bursts (map updates, point clouds, parameter syncs) that may not fit a 20-packet queue. The cap also adds 5 Hz pings per robot to the core, on top of the C2 probes.
**Evidence Anchor**: `text: §III-D "C2 and ICMP use a high-priority class with a 20-packet queue; video uses a low-priority class"`
**Why it matters**: The 66.8 % to 91.8 % C2 claim (C1, the main contribution) may overstate what real C2 traffic would see. Classification of real C2 also needs per-application marking (DSCP or ports) that the paper does not describe.
**Suggestion**: Repeat at least the static forest load test and the 30-min patrol with a realistic C2 workload (e.g. a ROS 2 teleop/heartbeat topic at 10–20 Hz plus periodic bursty status/map messages) measured end to end at the application layer. Report its delivery ratio and latency next to ping. State how C2 is classified and what happens when C2 bursts exceed the high-priority queue. Quantify the cap's own probe overhead as a fraction of a weak link's 100 kb/s.
**Severity**: Major
**Confidence**: 4 — core expertise: robot middleware traffic; adjacent on HTB details

### W3: The standard 5G remedy, separate QoS flows/DRBs for C2, is not considered
**Problem**: The related-work section rules out network-side remedies because "the UE's buffer is not ours to change". In a private 5G cell brought by the responders, though, the operator controls the core policy. The standard practice for mission-critical data is to map C2 to its own QoS flow (a GBR or high-priority 5QI) on a separate DRB. UE MAC logical-channel prioritisation then serves C2 ahead of video regardless of the video backlog. A public-safety designer would try this first. The paper neither compares against it nor explains why it is unavailable (for example, if the OAI UE/gNB supports only one DRB, say so).
**Evidence Anchor**: `text: §II "because constant-rate robot video does not back off and the UE's buffer is not ours to change"`
**Why it matters**: If multi-DRB QoS solves the C2 half of the problem natively, the cap's contribution narrows to video latency and recovery-tail behaviour. The design guidance ("put an AIMD cap on every robot") might then be the wrong first recommendation for an integrator with standard equipment. A per-bearer queue still bloats video, so the cap may remain valuable for video. That needs to be argued.
**Suggestion**: Add a paragraph, and ideally an experiment, comparing (i) single bearer without a cap, (ii) single bearer with the cap, and (iii) C2 on a separate prioritised QoS flow/DRB, with and without the video cap. If OAI cannot do (iii), state the limitation explicitly in §II and §VI, and position the cap as the remedy for single-bearer or commodity-modem deployments.
**Severity**: Major
**Confidence**: 4 — core expertise: public-safety LTE/5G QoS; uncertain about current OAI multi-DRB support

### W4: Constant-rate video and the "goodput" metric do not reflect what an operator sees
**Problem**: The video model is 1.5 Mb/s CBR UDP. Real robot video (H.264/H.265, GStreamer/WebRTC) is variable rate with I-frame bursts, and modern stacks adapt their rate (e.g. WebRTC congestion control). Under the cap, the weak robots get 0.10–0.42 Mb/s with tail-drop from a 10-packet queue. For an encoder still producing 1.5 Mb/s, that means losing roughly 70–93 % of packets, which in practice yields undecodable or frozen video, not "0.1 Mb/s of video". The weakest robot's 1.08 s latency, and a patrol p90 of 1.6 s, are also beyond what remote driving tolerates. The claim that an adaptive encoder "reacts more slowly than the cap" is not supported by any experiment or citation.
**Evidence Anchor**: `text: App. C "Constant-rate video is a worst case; an adaptive encoder would congest less but reacts more slowly than the cap."`
**Why it matters**: The paper says the cap keeps "85–94 % of video" in some settings and "stays out of the way" on strong links, but the operational value of the video it delivers on weak links is unknown. A responder would rather have a 200 kb/s low-resolution stream that decodes than a 1.5 Mb/s stream with 80 % packet loss. The cap only helps if it is coupled to the encoder.
**Suggestion**: (a) Report a video-usability metric (frame delivery or decode rate, freeze time, or the share of time video latency is under an operational bound such as 300–500 ms). (b) Add a run with a rate-adaptive encoder, either a simple one driven by the cap value or WebRTC/GCC, and compare it with CBR plus the cap. If not possible, remove the unsupported claim and state that coupling the cap to the encoder bitrate is the obvious deployment design. (c) Discuss frame-level effects of packet drop.
**Severity**: Major
**Confidence**: 4 — core expertise: robot video teleoperation; adjacent on encoder congestion control

### W5: UAV endurance and the mission time scale are unaddressed
**Problem**: Missions are 90 min, and the closed-loop analysis concludes that a 90-min mission is too short to learn the forest altitude effect. A multirotor carrying a gNB, radio and power typically flies about 20–40 min per battery. Tethered systems avoid this but constrain the x–y moves the planner relies on. The paper never states the platform assumption, battery swaps (and the cell outage during each swap), or the energy cost of the exploring learner's climbs and probes.
**Evidence Anchor**: `absence: §IV Setup, §VI Limitations, App. C — expected a statement of UAV platform/endurance, handling of battery swaps, or energy cost of altitude moves; checked abstract, §I, §III, §IV, §V-D, §VI, App. C`
**Why it matters**: Endurance changes the conclusions in two practical directions. (i) If the real mission is a sequence of 20–30-min sorties, the learner has even less time per sortie, which strengthens the "don't learn altitude on the spot" lesson, but also raises whether learned values can carry over between sorties. (ii) If the platform is tethered, lateral placement (arms B, D) is largely moot and hover is the default. Either way, a designer reading the paper needs this framing.
**Suggestion**: State the assumed platform (free-flying versus tethered) and its endurance. Discuss handover between sorties (warm-starting the altitude learner across battery swaps) and the outage during a swap. Report the time spent climbing or probing per arm as an energy proxy. One paragraph in §VI plus a sentence in §IV would suffice.
**Severity**: Major
**Confidence**: 4 — core expertise: UAV field operations

### W6: The terrain-blind premise is asserted, not justified, and the hover baseline is not terrain-blind
**Problem**: The paper assumes "no map of a terrain that may have just been changed". Responders, however, almost always have satellite imagery, DEM/DSM, canopy-height or land-cover products, and can do a quick UAV mapping pass, which many SAR teams fly as standard. A coarse terrain class (forest vs open) is cheap to obtain. Meanwhile, the winning baseline (fixed 40 m hover) and the cautious learner's home altitude (40 m) are fixed design constants presumably informed by the prior study, which makes them effectively terrain-informed priors.
**Evidence Anchor**: `text: §I "On site there is no operator to tune it and no map of a terrain that may have just been changed"`
**Why it matters**: If a terrain label is cheap, the natural designer question is whether a terrain-conditioned policy (e.g. hover in forest, climb in open field, as the prior study and Table III suggest) beats hover. The paper's framing excludes that option by fiat. It also does not say how 40 m was chosen, which affects whether "hover wins" generalises to a deployment where nobody knows the right hover height.
**Suggestion**: (a) Justify terrain-blindness with operational sources, or reframe it as "terrain-agnostic generalisation" while acknowledging that coarse terrain information is usually available. (b) State how the 40 m hover altitude and the 20–60 m geofence were chosen. (c) If feasible, add a "terrain-label oracle" arm or a replay analysis giving the upper bound that a coarse terrain label would buy. This tells designers whether gathering terrain information is worth the effort.
**Severity**: Major
**Confidence**: 4 — core expertise: SAR pre-deployment practice

### W7: The RTT baseline r0 can drift under persistent congestion, and controller robustness is not shown
**Problem**: r0 is the minimum RTT over a 60-s window. On a link that stays congested for more than 60 s (the regime that produced 14–18 min outages), the minimum RTT inside the window itself includes queuing delay, so the cut threshold r0 + 150 ms rises and the controller can stop cutting. This is a known failure mode of delay-based controllers (cf. the LEDBAT base-delay discussion). Similarly, RTT also rises with downlink congestion and core-side delay, which the cap would misread as uplink backlog. Hardware also introduces HARQ/scheduling jitter that the AWGN emulation lacks.
**Evidence Anchor**: `text: §III-D "With r0 the minimum RTT over 60 s and r̃ the median over the last second"`
**Why it matters**: The cap is the paper's deployable contribution. A practitioner needs to know when it fails, especially as the authors propose hardware trials of exactly this controller next.
**Suggestion**: Discuss the base-delay drift and either show that it does not occur in the traces (r0 trace over time in the static test) or use a longer or reset baseline. Add a short sensitivity analysis of the 150/50 ms thresholds and a discussion of downlink-induced RTT rise. Note that, with no multipath or fading in the channel, RTT is much smoother than on hardware.
**Severity**: Minor
**Confidence**: 4 — core expertise: delay-based rate control in practice

### W8: The design guidance is not stated in actionable form
**Problem**: The conclusion's lesson ("verify that the managed system is measured and loaded soundly ... before learning where to fly") is correct but generic. The paper does not tell a system designer what to configure: cap bounds, the queue sizes for C2 and video, the hover altitude to choose, the liveness check to run on a real modem (the dead-process rule uses OAI gNB counters; commercial UEs reattach on their own), or the residual risk (with the cap, all four robots were still up only about 70 % of the time in forest, so about 30 % of the mission had at least one robot down).
**Evidence Anchor**: `text: §VII "the lesson is to verify that the managed system is measured and loaded soundly, bounding its queues and checking that its processes are alive"`
**Why it matters**: NOMS readers include operators and integrators. The paper's practical value depends on the lesson being concrete enough to act on.
**Suggestion**: Add a short "Implications for deployment" paragraph or table: (i) what to configure on the robot (cap, priority classes, encoder coupling), (ii) what to configure in the core (QoS flows, see W3), (iii) the default UAV policy (hover at a stated altitude, centroid offers no gain), (iv) the liveness and measurement checks to run in the field, and (v) the residual outage exposure, with its implication that robots still need autonomous loss-of-link behaviour, store-and-forward of findings, or a relay.
**Severity**: Minor
**Confidence**: 5 — core expertise: system-integration practice

### W9: The closed-loop placement evidence is one run per arm (pending)
**Problem**: Per-arm comparisons use one 90-min run per arm, flown on different nights. The three-seed repeat is marked \todo. The cap-lifts-every-arm claim (Fig. cap_arms) also compares capped and uncapped runs from different nights, with different altitude learners in C/D.
**Evidence Anchor**: `text: Fig. cap_arms caption "Arms C and D used a UCB altitude bandit uncapped and the prober learner capped."`
**Why it matters**: The direction is consistent across four arms and the static test supports the mechanism, so C1 survives. The C4 "no learned arm beat baseline" claim, however, is what a designer would act on, and it currently rests on single runs with ±15-point intervals. Other reviewers will cover the statistics. I flag it because the practical recommendation ("just hover") depends on it.
**Suggestion**: Complete the repeat campaign as planned. For the cap-versus-arm figure, note in the text that C and D are not like-for-like and compare A and B only for the clean contrast.
**Severity**: Minor (given it is already flagged as pending; would be Major if the paper were submitted without it)
**Confidence**: 3 — adjacent: experimental statistics

---

## Detailed Comments

### Title & Abstract
- The title states the lesson well. The abstract's "raised C2 availability from 66.8 % to 91.8 %" should say what C2 availability means (ping-based, under 50 % loss), or readers from robotics will read it as control-link availability in their sense (W1).
- "removed these outages" is true in the static and patrol tests, but the fly-off still shows about 30 % of the time with at least one robot down. Say "removed the long queueing outages".

### Introduction
- The motivation is credible. Citing an operational source for the "no operator, no map" premise (W6) would make it persuasive to responders. The claims list (C1–C4) is clear and helps.

### Literature Review
- Missing: public-safety broadband and mission-critical QoS (3GPP 5QI, MC services), deployable/tethered aerial cells for first responders, and robot teleoperation latency requirements. These are where a practitioner would expect the comparison point for W1, W3 and W5. The bufferbloat paragraph is good. Adding rate-adaptive real-time video congestion control (GCC/SCReAM) would frame W4.

### Methodology
- The platform is honest about injected propagation. The cap description is clear and reproducible. See W2 (traffic classification), W7 (r0 drift).
- The 20–60 m geofence and 40 m hover/home altitude need provenance (W6). State the regulatory context briefly (e.g. typical 120 m AGL limits and BVLOS considerations), since a deployable system must respect them.

### Results
- C1 is the operationally valuable part. Consider promoting outage-duration and video-usability analyses (W1, W4) into the main text at the expense of the forecasting model-class detail, which matters less to a deployment audience.
- The weakest robot's 1.08 s video latency is a structural consequence of a 10-packet queue at 100 kb/s. Making the video queue time-bounded (e.g. CoDel/fq_codel or a byte limit scaled to the cap) would be an easy improvement worth noting.
- The C3/C4 negative results are useful to designers ("don't expect a terrain-blind model to pick altitude"). The forecast skill (ROC-AUC 0.667, 20 s ahead) should be translated into an alerting operating point: what precision and recall at what false-alarm rate per robot-hour? An operator will ignore an alert that fires falsely every few minutes.

### Discussion / Limitations
- The limitations are candid. Add UAV endurance/tethering (W5), single-bearer QoS (W3), the ICMP-as-C2 proxy (W2) and the fixed 0.5 m terminal height (robots in rubble or rooms are lower and more shadowed) to the main-text limitations.

### Conclusion
- The lesson is right but generic; see W8. "Next steps: hardware trial of the cap" is the right priority. I suggest the trial include a commercial 5G modem (e.g. a Quectel-class M.2 module), where uplink buffering behaviour may differ fundamentally from OAI.

### References
- The single preprint/misc entries (liu2025fog, hasan2024gentrap) are acceptable but should be updated to published versions if available. One entry carries a [VERIFY] note in the .bib (qiu2020placement).

---

## Questions for Authors

1. Does the OAI UE/gNB/Open5GS combination in your testbed support a second DRB/QoS flow for C2 with logical-channel prioritisation? If so, what happens to C2 without the cap when C2 is on its own bearer and video on another?
2. What is the distribution of outage durations (median, p95, longest) for each arm and for the capped/uncapped patrol, and how many of those outages exceed a typical robot loss-of-link timeout (e.g. 1 s, 5 s)?
3. In the capped weak-link condition (about 100 kb/s), what fraction of video frames would be decodable for a 1.5 Mb/s encoder, and does coupling the encoder bitrate to the cap value change C2 or video usability?
4. What UAV platform and endurance do you assume, and how was the 40 m hover/home altitude chosen? Was it informed by the earlier terrain study?

---

## Minor Issues

### Language / Grammar
- §V-A: "kept rising for the whole load period, peaking at 350--444 s": clarify that this is RTT in seconds, not time into the run.
- Abstract: "lost 18--27 points of the time with all robots connected" is awkward; consider "reduced the time with all robots connected by 18–27 points".

### Citation Format
- refs.bib qiu2020placement carries an unresolved verification note.

### Figures and Tables
- Table tab:static: add the open-field adaptive column, or a footnote, so the "stays out of the way" claim is visible in the table.
- Fig. caparms: mark visually that the C/D learners differ between the uncapped and capped bars.
- Fig. seeds caption carries a \todo, which is known.

### Layout
- The Contributions paragraph lists six NOMS topic areas. This reads as a submission-form checklist and could be cut.

---

## Criterion-Bound Judgements

Calibration status: `NOT_CALIBRATED`

| Dimension | Criterion source | Judgement | Evidence anchor(s) | Rationale | Uncertainty / scope limit | Decision bearing? |
|---|---|---|---|---|---|---|
| Originality | quality_rubrics.md | MEETS | §V-A, Table static | Diagnosing UE-internal queuing as "coverage" outages in a UAV SAR cell, plus a robot-side fix, is a fresh and practical contribution | Novelty relative to QoS-flow approaches not established (W3) | No |
| Methodological Rigor | quality_rubrics.md | PARTLY_MEETS | W1, W2, W9 | Strong controls (null control, LOTO), but the C2 metric is ping-based and prioritised by the treatment, and placement uses single runs | Statistical detail left to R1 | Yes |
| Evidence Sufficiency | quality_rubrics.md | PARTLY_MEETS | W2, W4, W9 | C1 mechanism well shown in static test; operational sufficiency (real C2, usable video, repeats) missing | Repeat campaign pending | Yes |
| Argument Coherence | quality_rubrics.md | MEETS | §I claims, §VII | Claims C1–C4 are stated, tested and graded honestly | — | No |
| Writing Quality | quality_rubrics.md | MEETS | whole paper | Clear and concise; some dense numeric sentences | — | No |
| Literature Integration | quality_rubrics.md | PARTLY_MEETS | §II | Missing public-safety QoS, teleop requirements, adaptive video congestion control | Domain coverage judged from practitioner side only | No |
| Significance & Impact | quality_rubrics.md | PARTLY_MEETS | W3, W5, W6, W8 | The lesson is valuable, but it is not yet translated into deployable guidance, and competing standard remedies are unexamined | Depends on outcome of W3 comparison | Yes |

**Recommendation rationale**: The decision-bearing gaps are Rigor and Evidence (the C2 metric is a prioritised ping proxy; video usability is unmeasured; placement uses single runs) and Significance (no comparison with QoS-flow separation; endurance and terrain-blind premises unargued). All are repairable. W1 and W8 are re-analysis and writing, W5 and W6 mostly writing, and W2, W3 and W4 need a small number of additional static or patrol runs. None invalidates the core C1 mechanism. Hence Major Revision.

**Overall score: 6 / 10.** A clear accept-worthy core lesson once the operational metrics and the QoS-flow comparison are addressed.

```yaml suggested-references
- title: "Disaster Robotics"
  authors: "Robin R. Murphy"
  year: 2014
  venue: "MIT Press (already cited; use its chapters on communications and operator workload for W1/W6)"
- title: "Low Extra Delay Background Transport (LEDBAT)"
  authors: "S. Shalunov, G. Hazel, J. Iyengar, M. Kuehlewind"
  year: 2012
  venue: "IETF RFC 6817"
- title: "Analysis and Design of the Google Congestion Control for Web Real-time Communication (WebRTC)"
  authors: "Gaetano Carlucci, Luca De Cicco, Stefan Holmer, Saverio Mascolo"
  year: 2016
  venue: "Proc. ACM Multimedia Systems Conference (MMSys)"
- title: "Self-Clocked Rate Adaptation for Multimedia (SCReAM)"
  authors: "I. Johansson, Z. Sarker"
  year: 2017
  venue: "IETF RFC 8298"
- title: "System architecture for the 5G System (5GS) — QoS model (clause 5.7)"
  authors: "3GPP"
  year: 2023
  venue: "3GPP TS 23.501"
- title: "Human Performance Issues and User Interface Design for Teleoperated Robots"
  authors: "Jessie Y. C. Chen, Ellen C. Haas, Michael J. Barnes"
  year: 2007
  venue: "IEEE Transactions on Systems, Man, and Cybernetics, Part C"
```
