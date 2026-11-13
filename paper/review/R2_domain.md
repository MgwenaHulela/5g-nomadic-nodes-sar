# Peer Review Report

## Manuscript Information
- **Title**: Bounded Queues Before Learned Placement: Autonomous Management of a UAV-Borne 5G Cell for Search-and-Rescue Robots
- **Manuscript ID**: n/a (simulated NOMS 2027 full-paper submission)
- **Review Date**: 2026-11-12
- **Review Round**: Round 1 review

---

## Reviewer Information

### Reviewer Role
Peer Reviewer 2 (Domain)

### Reviewer Identity
Senior researcher in UAV-assisted cellular networks and the 5G RAN. Works hands-on with OpenAirInterface (gNB/nrUE, rfsimulator, RLC/PDCP entities, MAC scheduler) and follows RAN-side latency work (bufferbloat in cellular, L4S/ECN in the RAN, delay-based rate control for real-time media).

### Review Focus
(1) Whether the diagnosis "long forest outages are unbounded uplink queuing inside the UE" is plausible and correctly established for OAI, and whether a 3GPP-compliant commercial modem would behave the same way. (2) Coverage of related work on learning-based UAV placement, RAN buffering/L4S and 5G link-failure prediction. (3) Emulation realism (AWGN-only rfsim channel, fixed terminal height, injected path loss) and, above all, whether the headline result transfers beyond an OAI-rfsim artefact.

---

## Overall Assessment

### Recommendation
- [ ] Accept
- [ ] Minor Revision
- [x] **Major Revision** — Substantial revisions needed, re-review required after revision
- [ ] Reject

### Confidence Score
4 — the RAN/OAI/buffering and UAV-placement aspects are core expertise; the statistics of the learning evaluation are adjacent (left mainly to Reviewer 1).

### Summary Assessment
The paper builds a terrain-blind learning layer (20-s C2-failure forecaster, soft-min risk planner, on-the-spot altitude learner) for a UAV-borne OAI gNB serving four emulated robots, and reports that the biggest gain came from a robot-side, RTT-driven AIMD uplink cap that removed minutes-long forest outages attributed to UE-internal queuing (forest patrol C2 66.8 % -> 91.8 %). The learning results are honest negatives: modest forecasting skill (ROC-AUC 0.667) that degrades with capacity, no recovery of the terrain-by-altitude interaction, and no learned placement beating hover or centroid. The experimental hygiene (leave-one-terrain-out, same-policy null controls, forbidden-feature guard, intervention test with a fixed cap) is better than typical in this space, and the "fix the managed system before learning" message suits NOMS. From the domain side, however, the central claim C1 is under-identified: the paper does not show *where* in the OAI UE the backlog lives (PDCP, RLC SDU queue, or the TUN reader with no back-pressure), does not report the RLC/PDCP configuration (RLC mode, PDCP discardTimer, buffer limits) that a 3GPP modem would use to bound it, and its key "79 MB per robot" figure exceeds the total video offered in the test. The 3GPP-native remedy (a separate QoS flow/DRB for C2 with logical-channel priority) and standard alternatives (fq_codel/CAKE with back-pressure, delay-based video rate control such as SCReAM/GCC) are neither discussed nor compared. Radio configuration (band, bandwidth, numerology, TDD pattern, SNR at the stated attenuation) is absent, so transfer cannot be judged. These are repairable, hence Major Revision.

---

## Strengths

### S1: Interventional test of the queueing hypothesis
The fixed 100 kb/s cap below every link's drain rate, at the same placement, removed all C2 loss and the recovery tail. This is a genuine intervention rather than a correlation, and it is the strongest piece of evidence that the outage is load-induced rather than coverage-induced in this testbed.
**Evidence Anchor**: `table: Table (tab:static) — "UL ping loss, video on" 64--91 % (no cap) vs 0 % (fixed 100 kb/s) vs 0 % (adaptive)`

### S2: Cap uses only signals available on real hardware and needs no RAN change
Driving the cap from ICMP RTT and bytes sent, with C2/ICMP in a high-priority HTB class and video in a short drop-tail class, is deployable on any robot with a commercial modem and is orthogonal to the RAN vendor. This is exactly the kind of autonomic, edge-side management action NOMS readers can reuse.
**Evidence Anchor**: `text: §III-D "Its only inputs, RTT and bytes sent, are available on real hardware."`

### S3: Explicit leakage control for terrain-blindness
Forbidding the injected path loss, the derived received power and terrain labels, with a guard function on every feature matrix, is a sound practice that many learning-for-RAN papers in emulation omit.
**Evidence Anchor**: `text: §III-C "Three inputs are forbidden: the simulator's injected path loss, the computed received power (algebraically equivalent to it), and any terrain label."`

### S4: Same-policy null control for cross-night closed-loop comparisons
Reporting that the null control is as large as the effect in urban and hills, and declaring those comparisons uninterpretable, is unusually candid and methodologically valuable for OAI testbed work, where night-to-night drift is real.
**Evidence Anchor**: `table: Table A3 — urban null +18.7 [+12.9, +24.4], hills null -21.7 [-32.1, -12.2], marked "Usable: no"`

### S5: Clear negative results on learned placement
The paper states that no learned arm beat hover or centroid and explains why (one mission cannot sign the forest altitude slope). Negative results of this type are rare in the UAV-placement literature, which is dominated by analytic-channel optimisation.
**Evidence Anchor**: `text: §V-D "No learned arm beat a baseline."`

---

## Weaknesses

### W1: The location and mechanism of the "UE-internal" queue are not established
**Problem**: The paper infers that the OAI UE holds the backlog because the TUN qdisc shows no backlog and no drops. In OAI's nrUE, packets are read from the TUN by a user-space thread and pushed into PDCP/SDAP and the RLC SDU queue; there is no netdev flow control from RLC back to the TUN, so the kernel qdisc will *always* look empty. The paper does not identify which entity holds ~tens of MB (PDCP, RLC TX SDU buffer, an internal ITTI/message queue), whether it has a size bound (OAI's NR RLC entities are created with TX/RX maximum-size parameters; the exact constant should be checked), or whether the RLC bearer is AM or UM. "Unbounded" is therefore an assertion, not a measurement; the 64--91 % ping loss also suggests that something does drop or that "loss" means "exceeded a timeout" (see W3).
**Evidence Anchor**: `text: §V-A "The kernel queue on each TUN held no backlog and dropped nothing, so the excess, about 79 MB per robot, was accepted and held by the OAI UE (E1)."`
**Why it matters**: C1 is the paper's headline and its first contribution. Without the mechanism, a reader cannot tell whether this is a generic UE behaviour or an OAI implementation choice (no back-pressure + very large RLC buffer + infinite PDCP discard timer), which is decisive for transfer.
**Suggestion**: Instrument the UE: log RLC TX buffer occupancy (OAI exposes RLC stats per logical channel) and the BSR values reported to the gNB over time during the static load test; state the OAI commit, RLC mode, the RLC TX max-size constant and the PDCP discardTimer used. Replace "unbounded" with the measured bound (or "effectively unbounded at these rates: X MB") unless the code shows no bound.
**Severity**: Major
**Confidence**: 4 — core expertise: OAI RLC/PDCP data path (exact OAI constant names to be checked by authors)

### W2: Transfer to commercial modems is asserted as a limitation but not argued from 3GPP behaviour
**Problem**: The paper limits generality to one sentence ("a commercial modem may bound or drop uplink data"). The 3GPP mechanisms that determine this are not discussed: (i) the PDCP discardTimer (TS 38.323), configured by the gNB per DRB, which bounds sojourn time in the UE; (ii) finite per-bearer modem buffers with host-side flow control (e.g. rmnet/MBIM/QMI drivers stop the netdev queue), which would move the backlog into the host qdisc, where a default fq_codel would drop video and protect ping; (iii) logical-channel prioritisation if C2 were on its own DRB. On commercial hardware the outage could thus be shorter, or relocate to the host, in which case the proposed cap's value changes. Deep modem buffers in cellular uplinks are well documented, so the effect is plausible, but its magnitude (minutes, 14--18 min recovery) is likely OAI-specific.
**Evidence Anchor**: `text: Appendix C "The UE buffering behaviour is that of the OAI UE; a commercial modem may bound or drop uplink data."`
**Why it matters**: The paper's main lesson ("bound queues before learning") is framed as general advice for autonomous management of such cells; reviewers at NOMS will ask whether it is an OAI artefact.
**Suggestion**: (a) Rerun the static forest load test with a finite PDCP discardTimer configured at the gNB (e.g. 100--300 ms, if the OAI UE honours it; if it does not, say so — that is itself the finding). (b) If any COTS 5G module or the authors' B210/Pi CU-DU setup is available, a single static load test with a COTS UE at matched SINR would largely settle transfer; otherwise reframe C1 explicitly as "in OAI nrUE" in title-level claims and add a reasoned paragraph on 3GPP bounds.
**Severity**: Major
**Confidence**: 4 — core expertise: 3GPP L2 and modem data path

### W3: The headline backlog figure is arithmetically inconsistent with the test
**Problem**: Each robot offered 1.5 Mb/s for 6 min = 540 Mb = 67.5 MB in total. The paper reports an *excess* (offered minus drained at 0.13--0.49 Mb/s, i.e. roughly 45--62 MB) of "about 79 MB per robot", which exceeds the total offered load. Separately, the backlog/drain ratio implied by those numbers (~750--3800 s) does not match the measured RTT peak of 350--444 s, and "probes were delayed rather than dropped" sits uneasily with 64--91 % ping loss.
**Evidence Anchor**: `text: §V-A "the excess, about 79 MB per robot, was accepted and held by the OAI UE (E1)"`
**Why it matters**: This number is the quantitative evidence for the UE-queue mechanism; an inconsistent figure invites the conclusion that the mechanism is not understood. If the RTT peak is lower than the backlog would imply, part of the excess was dropped somewhere, contradicting "unbounded".
**Suggestion**: Recompute the excess from application send counters and receiver counters (stating units, MB vs MiB, and whether IP/UDP/GTP-U overhead is included); define "lost" probes (timeout value) vs never-returned; reconcile peak RTT, residual backlog and recovery time in one short calculation.
**Severity**: Major
**Confidence**: 4 — direct arithmetic from the manuscript's own numbers

### W4: Missing 3GPP-native and standard-AQM baselines for the cap
**Problem**: The cap is compared only with no cap and a fixed 100 kb/s cap. The obvious 5G remedy — mapping C2 to a separate QoS flow (different 5QI / GBR or higher-priority non-GBR) on its own DRB, so that the UE MAC's logical-channel prioritisation serves C2 ahead of video — is not mentioned, although Open5GS supports per-flow PCC rules and OAI supports multiple DRBs to some degree. Nor are host-side alternatives compared: fq_codel/CAKE on the TUN (which would work if back-pressure existed), or delay-based real-time media rate control designed for cellular (SCReAM, GCC, Sprout-like).
**Evidence Anchor**: `absence: §II "Buffering in cellular links" and §V-A — expected discussion or comparison of a separate C2 QoS flow/DRB with LCP, and of fq_codel/CAKE or SCReAM/GCC; checked §II, §III-D, §V-A, §VI, Appendix C`
**Why it matters**: Without these, the novelty of an AIMD-on-RTT cap is hard to assess; a NOMS reviewer will see it as re-deriving delay-based congestion control. The QoS-flow route also directly answers "why not fix it in 5G".
**Suggestion**: At minimum, discuss why C2/video separation via QoS flows was not used (OAI support, priority inversion when the shared radio grant is too small, BSR behaviour). Ideally add one static-load run with C2 on a dedicated DRB, and position the cap against SCReAM (RFC 8298) and L4S-style RAN marking as the "network-side" vs "endpoint-side" options.
**Severity**: Major
**Confidence**: 4 — core expertise

### W5: Radio configuration and absolute link budget are not reported
**Problem**: The paper never states carrier/band, channel bandwidth, numerology/SCS, TDD pattern, MCS table, UL power control, maximum UE TX power, BSR/SR configuration, the rfsim noise level, or the SNR/SINR corresponding to "24--30 dB uplink attenuation". Only relative attenuation is given. The drain rates (0.13--0.49 Mb/s) cannot be checked against expected UL spectral efficiency, nor can the reader judge whether the 45 dB attenuation clamp (Appendix C) produces a coverage cliff.
**Evidence Anchor**: `absence: §III-A Platform and §IV Setup — expected band, bandwidth, numerology, TDD pattern, SNR at the stated attenuations, OAI version; checked §III-A, §IV, Appendix A--C`
**Why it matters**: Transfer and reproducibility both hinge on the operating point; "forest" at 24--30 dB means nothing without the reference SNR. The compute-bound aggregate capacity of an rfsim cell (if any) also needs stating, since a CPU-bound UL would change the queueing interpretation.
**Suggestion**: Add a short radio-configuration table (OAI commit, band, BW, SCS, TDD, MCS table, PDCP/RLC config, rfsim noise, SNR range per terrain), and report the measured unloaded UL capacity per robot at forest attenuation, and the aggregate cell UL capacity on this host.
**Severity**: Major
**Confidence**: 5 — core expertise

### W6: AWGN-only channel makes gNB link-adaptation features near-deterministic functions of injected path loss
**Problem**: Under rfsim AWGN with no small-scale fading, Doppler or frequency selectivity, BLER, MCS and `ul_bits_per_sym` are essentially deterministic functions of the injected mean SNR. Forbidding path loss and received power therefore does not make the model "terrain-blind" in the intended sense: path loss is recoverable from the radio features. Conversely, the absence of fading removes the HARQ/BLER fluctuations that real failure-prediction work (e.g. the cited 5G RAN RLF predictors) exploits, so the forecasting skill (C2) and the "capacity hurts" finding may not transfer either way. The paper notes the smooth channel "may itself limit" learning but not the leakage direction.
**Evidence Anchor**: `text: Appendix C "there is no small-scale fading, Doppler or frequency selectivity"`
**Why it matters**: C2 and C3 interpretations depend on what information the features carry; reviewers in RLF prediction will question results from a fading-free channel.
**Suggestion**: Report how well path loss can be regressed from the allowed radio features (R^2) to quantify leakage; discuss explicitly that fading-free BLER is a step function of SNR; if feasible, repeat a small forecasting run using OAI rfsim's channel models (rfsim supports configurable channel models such as TDL/CDL-type profiles in recent versions — authors to verify for their commit) to test robustness of the ordering of model classes.
**Severity**: Major
**Confidence**: 4 — core expertise (exact rfsim channel-model support depends on OAI version)

### W7: Dead-process "measurement-integrity detector" is an OAI artefact presented as a general contribution
**Problem**: The OAI nrUE exiting on radio-link failure is an implementation behaviour; a 3GPP UE performs RLF detection (T310/N310), then RRC re-establishment or returns to idle and reselects. Commercial gNB counters would not freeze for this reason. The detector is valuable testbed hygiene but is listed as contribution 2 with no statement that it is specific to OAI UEs.
**Evidence Anchor**: `text: §III-B "In OAI, a radio-link failure terminates the UE process."`
**Why it matters**: Inflating an artefact-fix into a contribution weakens the paper with domain reviewers and obscures the transferable point (always check process liveness/counter freshness before trusting labels).
**Suggestion**: Reframe as a lesson for OAI-based ML datasets (which is useful and citable to the OAI community), and add one sentence on what a COTS UE does after RLF and whether a frozen-counter signature would ever appear there (e.g., stale E2/KPM reports).
**Severity**: Minor
**Confidence**: 5 — core expertise

### W8: Missing literature on learning-based UAV placement, cellular uplink/real-time rate control, RAN buffer sizing and O-RAN-based closed-loop management
**Problem**: The UAV-learning line is represented by a single DRL paper (still carrying a [VERIFY] note in refs.bib) and the RAN-buffering line by two very recent 2025 works. Missing: RL trajectory/placement for flying base stations (Bayerlein et al. 2018; Liu, Liu, Chen 2019), radio-map/learned-channel placement (Chen & Gesbert 2017), the Proc. IEEE UAV tutorial (Zeng et al. 2019), 3GPP aerial work (TR 36.777); delay-based cellular rate control (Verus, PBE-CC, ABC, SCReAM RFC 8298, GCC), and RAN buffer sizing in OAI specifically (Irazabal et al., to verify); closed-loop learning platforms on OAI (OpenRAN Gym/Colosseum, O-RAN survey). The "worst-fold" / terrain distribution-shift framing would also benefit from a pointer to domain-generalisation evaluation practice.
**Evidence Anchor**: `absence: §II Background and Related Work — expected learning-based UAV placement beyond one DRL paper, endpoint delay-based rate control for cellular, OAI RLC buffer sizing, O-RAN closed-loop testbeds; checked §II, refs.bib`
**Why it matters**: The novelty statement ("the UE's buffer is not ours to change", endpoint AIMD cap) needs positioning against endpoint delay-based schemes that do exactly this; the learning claims need positioning against RL/radio-map UAV placement that also faces unknown terrain.
**Suggestion**: Add the references below (verify items marked "to verify"); one paragraph each on endpoint delay-based control and on radio-map/online learning for UAV placement, stating what the paper adds (closed-loop, real stack, held-out terrain, negative result).
**Severity**: Major
**Confidence**: 4 — core expertise; specific bibliographic details flagged where uncertain

### W9: Fixed 0.5 m terminal height, urban LoS coefficients everywhere, and the 45 dB clamp interact with the altitude conclusions
**Problem**: C3/C4 conclude about altitude effects (e.g. "-6.6 points per +10 m" in forest). These slopes are generated by the injected model: Al-Hourani urban sigmoid in every world, Weissberger foliage depth that depends on geometry, and a 45 dB clamp. In forest, the sign of the altitude effect is largely determined by how foliage path length varies with elevation angle in the Weissberger implementation, which is not given here (deferred to the earlier paper).
**Evidence Anchor**: `text: Appendix C "the line-of-sight sigmoid uses the urban coefficients in every world, and the applied attenuation is clamped at 45 dB."`
**Why it matters**: The paper's claims about what learning can recover are claims about a synthetic altitude response; a reader cannot see whether the response is realistic or an emergent property of the injected formulas.
**Suggestion**: Plot (even in the appendix) the injected mean attenuation vs UAV altitude per terrain at representative robot distances, and state the foliage-depth geometry. Phrase C3/C4 as properties of this channel model, and note which conclusions would survive a fading channel.
**Severity**: Minor
**Confidence**: 4 — core expertise: A2G channel modelling

### W10: The cap's RTT signal assumes ICMP shares the UE queue but bypasses the robot queue; this coupling is not validated across conditions
**Problem**: The cap relies on ICMP entering the high-priority HTB class and then the same single UE bearer. RTT therefore measures UE+RAN+core queueing for the shared default flow. With a 150 ms trigger and 0.2 s probing, the controller will also react to scheduling latency spikes, HARQ retransmissions (absent in AWGN) and RRC events; the reported 32--40 cuts per robot in 6 min suggests it oscillates even in a static, fading-free scenario.
**Evidence Anchor**: `text: §V-A "settling at a different rate on each link (medians 105--429 kb/s, 32--40 cuts per robot in 6 min)"`
**Why it matters**: On real radio with fading, the same thresholds may cut far more often and starve video; the claim "without starving strong links" is shown only for open-field AWGN.
**Suggestion**: Show the cap trace (rate vs time) for one forest robot, and discuss expected behaviour under fading; a sensitivity sweep of the 50/150 ms thresholds in the static test would be cheap.
**Severity**: Minor
**Confidence**: 4 — core expertise: delay-based rate control

---

## Detailed Comments

### Title & Abstract
- Title is accurate and memorable. The abstract states "unbounded uplink queuing inside the user equipment"; given W1/W2, write "inside the OAI UE" or "a UE without uplink back-pressure".
- The abstract should give the operating point (one sentence: OAI rfsim, AWGN, band/bandwidth).

### Introduction
- Motivation (no operator, no map) is persuasive for SAR. The label "C2" is used both for command-and-control and for Claim 2, which confuses readers (e.g. "C2: Terrain-Blind Forecasting"); rename claims H1--H4.
- Contribution 1 claims "no change to the radio access network" — true, but the reader should be told the 3GPP-native alternative exists (W4).

### Literature Review / Theoretical Framework
- See W8. The bufferbloat paragraph is good but stops at network/RAN-side remedies; endpoint delay-based schemes for cellular are the directly comparable class.
- The failure-prediction paragraph should note those works use real, fading channels and operator data, which is precisely what this emulation lacks (W6).

### Methodology / Research Design
- Radio configuration missing (W5). OAI and Open5GS versions should be stated.
- Watchdog relaunches the UE after RLF; please state whether any relaunch occurred during the static load test — a relaunch would flush the UE buffer and would contradict the 14--18 min drain-based recovery explanation.

### Results / Findings
- §V-A: W1, W3. The fixed-cap intervention is strong; strengthen it by adding the BSR/RLC-occupancy trace.
- §V-A "The cap lifts every placement policy": one run per arm on different nights; the null-control logic of §IV should be applied here too (the uncapped and capped forest fly-offs are cross-night comparisons). The effect sizes (25--35 % -> 0--0.3 % no-robot-up) are probably large enough to survive, but say so explicitly.
- §V-B: fine from a domain view; see W6 on what features carry.
- §V-D: with \todo pending (3-seed repeats), C4 cannot be finally assessed.

### Discussion
- There is no separate Discussion; a paragraph on what transfers to COTS hardware (L2 bounds, QoS flows, host qdisc behaviour) would substantially improve impact for NOMS.

### Conclusion
- "bounding its queues and checking that its processes are alive" — the second half is OAI-specific (W7); qualify.

### References
- `qiu2020placement` still carries a [VERIFY] note on authors/pages. Two key 2025 citations (liu2025fog, hasan2024gentrap) are arXiv preprints; acceptable but check for published versions.
- Self-citation `makhubela2026satnac` carries much of the channel model detail; for a double-blind or standalone review, the essential channel parameters should be in this paper.

---

## Questions for Authors

1. Which OAI entity holds the backlog (PDCP, RLC SDU queue, internal message queue), what is its configured maximum, and what RLC mode and PDCP discardTimer were used? Does the OAI nrUE honour a gNB-configured discardTimer?
2. How is "79 MB per robot" computed, given that 6 min at 1.5 Mb/s is 67.5 MB in total, and how do you reconcile it with the 350--444 s peak RTT and 64--91 % probe loss (what timeout defines "lost")?
3. Did you consider carrying C2 on a separate QoS flow/DRB with higher logical-channel priority, and if so, what happened in OAI?
4. What are the band, bandwidth, numerology, TDD pattern and the SNR corresponding to 24--30 dB attenuation, and is the cell UL capacity on your host CPU-bound under rfsim?

---

## Minor Issues

### Language / Grammar
- "C2" overloaded (command-and-control vs Claim 2) throughout §I and §V headings.
- §V-B: "our reading is that it rewards models that learn a direction" — unclear antecedent of "it".

### Citation Format
- refs.bib `qiu2020placement` has an unresolved [VERIFY] note.
- `open5gs`, `itup526` are @misc; give URL/version and ITU-R recommendation version (e.g. P.526-15, to verify which edition used).

### Figures and Tables
- Table (tab:static): add a row with UE-side buffer occupancy or BSR if instrumented.
- Fig. (fig:rtt): mark where each trace ends because replies stop, and annotate the drain-rate per robot.
- Fig. (fig:seeds) and §V-D carry \todo placeholders; the paper is not reviewable in final form for C4.

### Layout
- Paper is 8 pages including appendix; check NOMS 2027 full-paper page limit and whether appendices count.

---

## Criterion-Bound Judgements

Calibration status: `NOT_CALIBRATED`

| Dimension | Criterion source | Judgement | Evidence anchor(s) | Rationale | Uncertainty / scope limit | Decision bearing? |
|---|---|---|---|---|---|---|
| Originality | quality_rubrics (originality) | PARTLY_MEETS | §I contributions; W4 | Closed-loop learned UAV-gNB management on a real stack with negative results is new; the cap itself resembles known delay-based control | Depends on positioning vs SCReAM/QoS-flow | Yes |
| Methodological Rigor | quality_rubrics (rigor) | PARTLY_MEETS | S1, S3, S4; W1, W5 | Good controls; mechanism of C1 and radio configuration not established | Statistics left to R1 | Yes |
| Evidence Sufficiency | quality_rubrics (evidence) | PARTLY_MEETS | W3; Table A5; \todo in §V-D | C1 intervention strong but headline numbers inconsistent; C4 awaiting repeats | Pending repeat campaign | Yes |
| Argument Coherence | quality_rubrics (coherence) | MEETS | §I claims C1--C4 | Claims map cleanly to sections and controls | — | No |
| Writing Quality | quality_rubrics (writing) | MEETS | whole paper | Concise, readable; C2 label overload | — | No |
| Literature Integration | quality_rubrics (literature) | PARTLY_MEETS | W8 | Thin on UAV learning and endpoint delay-based control | — | Yes |
| Significance & Impact | quality_rubrics (significance) | PARTLY_MEETS | W2, W6 | Message is valuable if it transfers; transfer beyond OAI-rfsim currently unargued | Hardware phase planned | Yes |

The decision-bearing gaps are (i) the unestablished mechanism and inconsistent quantification of C1 (W1, W3), (ii) the unargued transfer to 3GPP-compliant modems and the missing QoS-flow/AQM baselines (W2, W4), and (iii) the missing radio configuration (W5). All are repairable with instrumentation, one or two extra static-load runs and rewriting, without new fly-off campaigns; hence Major Revision rather than Reject.

**Overall score: 5 / 10** (borderline; would rise to 7 with W1--W5 addressed and the C4 repeats in).

```yaml suggested-references
- title: "Accessing From the Sky: A Tutorial on UAV Communications for 5G and Beyond"
  authors: "Y. Zeng, Q. Wu, R. Zhang"
  year: 2019
  venue: "Proceedings of the IEEE, vol. 107, no. 12"
- title: "Trajectory Optimization for Autonomous Flying Base Station via Reinforcement Learning"
  authors: "H. Bayerlein, P. De Kerret, D. Gesbert"
  year: 2018
  venue: "IEEE SPAWC 2018"
- title: "Reinforcement Learning in Multiple-UAV Networks: Deployment and Movement Design"
  authors: "X. Liu, Y. Liu, Y. Chen"
  year: 2019
  venue: "IEEE Transactions on Vehicular Technology, vol. 68, no. 8"
- title: "Learning Radio Maps for UAV-Aided Wireless Networks: A Segmented Regression Approach"
  authors: "J. Chen, D. Gesbert"   # to verify author list
  year: 2017
  venue: "IEEE ICC 2017"
- title: "Enhanced LTE Support for Aerial Vehicles"
  authors: "3GPP"
  year: 2017
  venue: "3GPP TR 36.777 (Release 15)"
- title: "Self-Clocked Rate Adaptation for Multimedia (SCReAM)"
  authors: "I. Johansson, Z. Sarker"
  year: 2017
  venue: "IETF RFC 8298"
- title: "Analysis and Design of the Google Congestion Control for Web Real-time Communication (WebRTC)"
  authors: "G. Carlucci, L. De Cicco, S. Holmer, S. Mascolo"
  year: 2016
  venue: "ACM MMSys 2016"
- title: "Adaptive Congestion Control for Unpredictable Cellular Networks (Verus)"
  authors: "Y. Zaki, T. Poetzsch, L. Subramanian, et al."
  year: 2015
  venue: "ACM SIGCOMM 2015"
- title: "PBE-CC: Congestion Control via Endpoint-Centric, Physical-Layer Bandwidth Measurements"
  authors: "Y. Xie, F. Yi, K. Jamieson"
  year: 2020
  venue: "ACM SIGCOMM 2020"
- title: "ABC: A Simple Explicit Congestion Controller for Wireless Networks"
  authors: "P. Goyal, A. Agarwal, R. Netravali, M. Alizadeh, H. Balakrishnan"
  year: 2020
  venue: "USENIX NSDI 2020"
- title: "Dynamic Buffer Sizing and Pacing as Enablers of 5G Low-Latency Services"
  authors: "M. Irazabal, E. Lopez-Aguilera, I. Demirkol, R. Schmidt, N. Nikaein"   # to verify
  year: 2020
  venue: "IEEE Transactions on Mobile Computing (to verify)"
- title: "NR; Packet Data Convergence Protocol (PDCP) specification"
  authors: "3GPP"
  year: 2024
  venue: "3GPP TS 38.323 (discardTimer; use release matching the OAI build)"
- title: "Understanding O-RAN: Architecture, Interfaces, Algorithms, Security, and Research Challenges"
  authors: "M. Polese, L. Bonati, S. D'Oro, S. Basagni, T. Melodia"
  year: 2023
  venue: "IEEE Communications Surveys & Tutorials, vol. 25, no. 2"
- title: "OpenRAN Gym: AI/ML Development, Data Collection, and Testing for O-RAN on PAWR Platforms"
  authors: "L. Bonati, M. Polese, S. D'Oro, S. Basagni, T. Melodia"
  year: 2023
  venue: "Computer Networks, vol. 220"
- title: "BBR: Congestion-Based Congestion Control"
  authors: "N. Cardwell, Y. Cheng, C. S. Gunn, S. H. Yeganeh, V. Jacobson"
  year: 2016
  venue: "ACM Queue, vol. 14, no. 5"
```
