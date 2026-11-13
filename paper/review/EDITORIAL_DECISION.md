# Editorial Decision: simulated NOMS 2027 review, round 1

**Manuscript:** "Bounded Queues Before Learned Placement: Autonomous Management of a UAV-Borne 5G Cell for Search-and-Rescue Robots" (`paper/main.pdf`, draft of 2026-11-12)

**Panel:**
- Journal-Fit Reviewer (R0)
- Peer Reviewers R1 (methodology), R2 (domain: UAV / 5G RAN), R3 (practical: SAR operations)
- Devil's Advocate (R4)

Reports are in `paper/review/R0–R4_*.md`.

**Calibration:** `NOT_CALIBRATED`.

**Provenance:** this run was simplified from the full ARS protocol, and it matters for how independent the panel is.

| Aspect | What happened |
|---|---|
| Model | All five seats ran on one model family (Claude) |
| Reviewer isolation | Each seat ran in a fresh subagent context, with no visibility of the other reports before writing |
| What reviewers saw | The manuscript only, not the repository's logs |
| Skipped | The sprint-contract two-call split, the provenance validator and the reviewer claim check (#1002) |
| Cross-model reviewer | Off; no consent was given to upload the manuscript |

Errors may be correlated across seats. Agreement between reviewers is corroboration from different angles, not independent confirmation.

## Decision: **MAJOR REVISION**

| Seat | Recommendation | Score |
|---|---|---|
| R0 Journal-Fit | Major Revision | 5 |
| R1 Methodology | Major Revision | 5 |
| R2 Domain | Major Revision | 5 |
| R3 Practical | Major Revision | 6 |
| R4 Devil's Advocate | (no score) | 1 CRITICAL, 10 MAJOR, 7 MINOR |

**The panel agrees on the paper's core value:**
- the queueing diagnosis, backed by the fixed-cap intervention
- the same-policy null control
- the forbidden-feature guard
- the plainly stated negative results

**It also agrees on the gap:** the closed-loop and learning claims go beyond what single cross-night runs and pre-cap labels can support. The transfer argument beyond the OAI UE is not made.

Most of the major issues are either fixable in the text or already in progress (the 3-seed repeats and retraining on capped data). Four need new, small experiments: Roadmap items 6–8 and 10.

## Devil's Advocate CRITICAL issue, adjudicated

**DA-C1.** The abstract states "lost 18–27 points" from cross-night single runs. The paper's own null control shows same-policy swings of +18.7 and −21.7.

- **Status: VALIDATED.** The paper applies its null-control rule to the planner comparison but not to the fly-off arms. R1-W2 and R0-W5 corroborate this.
- **Blocks acceptance:** yes, until the magnitude is either supported by the 3-seed repeats, using a run-level bootstrap, or removed from the abstract.

## Consensus issues (raised by three or more seats)

| # | Issue | Seats | Severity |
|---|---|---|---|
| K1 | Placement and cap comparisons are single runs across nights, with within-run CIs only (3–9 blocks). "Not distinguishable" is reported without an equivalence margin | R0-W5, R1-W2/W3/W4, R3-W9, R4-C1/m3 | Major (C1 critical) |
| K2 | Learning results (C2, C3, and the planner in C4) were trained and judged on pre-cap labels that the paper calls unsound, so a loss in closed loop is confounded with distribution shift | R0-W4, R1-W8, R4-M2/M6 | Major |
| K3 | C2 is measured with ICMP, the same traffic class the cap prioritises, so the metric and the intervention are coupled | R1-W13, R2-W10, R3-W2, R4-m4 | Major |
| K4 | Transfer beyond OAI: where the queue sits (PDCP / RLC / UE thread) is not shown, OAI version and RLC/PDCP settings are not reported, and commercial-modem behaviour is not argued | R2-W1/W2/W7, R4-M1, R0 (implicit) | Major |
| K5 | Missing baselines for C1: a separate QoS flow / DRB for C2, fq_codel/CAKE, delay-based rate control (LEDBAT, SCReAM/GCC) | R2-W4, R3-W3, R4 alt-path 4 | Major |
| K6 | "79 MB per robot" exceeds the about 67.5 MB offered | R2-W3, R4-M10 | Major (factual) |
| K7 | Forest null control [−17.6, +6.4] is called "near zero" and marked usable | R1-W10, R4-M9 | Major |
| K8 | Dead-process rule is not validated against ground truth (watchdog relaunches, UE exit logs) and may censor real outages | R1-W9, R4-M7, R2-W7 (generality) | Major |
| K9 | No CIs on forecasting metrics; horizon and model chosen on the held-out folds | R1-W6/W7, R4-M8/m1 | Major / Minor |
| K10 | Cap vs uncapped for arms C/D is confounded with a change of learner | R1-W1, R4-M3 | Major |

## Single-seat issues retained

| Seat | Issue | Severity |
|---|---|---|
| R0-W1/W2/W6/W7 | The management contribution is asserted, not architected: no loop or agent architecture, no intent layer, no NOMS/CNSM/O-RAN literature. The abstract leads with wireless, not management. The "agents" angle (four interacting per-robot cap controllers) is untouched | Major / Minor |
| R0-W3 | The delta over SATNAC needs a side-by-side statement. C1 is a known bufferbloat class with a standard AIMD fix, so frame it as diagnosis plus fix, not a new method | Major |
| R1-W11 | The paper must state which terrains each deployed model was trained on (terrain-leakage risk in arm D) | Major (easily fixed) |
| R1-W5 | The cautious learner was designed after seeing forest results, and evaluated on the same terrain and seed | Major |
| R1-W12/W14 | "Same robot paths" overstated; the mean-C2 numbers in the text have no figure or table | Minor |
| R2-W5/W6/W9 | Radio configuration not reported; AWGN makes radio features near-deterministic functions of path loss; urban LoS coefficients and the 45 dB clamp | Major / Minor |
| R2-W8 | Literature gaps: learning-based UAV placement and radio maps, real-time cellular rate control, RAN buffer sizing, O-RAN closed loops | Major |
| R3-W1/W4 | C2 metric has no latency bound and no outage-duration distribution; video usability (about 0.1 Mb/s, about 1 s) not assessed | Major |
| R3-W5/W6 | UAV endurance unaddressed; the terrain-blind premise is not argued; how 40 m was chosen is not stated | Major |
| R4-M4/M5 | "Lost by climbing" is not supported by the paper's own slope (−6.6, n.s.); the specification of the additive risk model may cause the C3 null | Major |
| R4-m5/m6/m7 | Cautious learner is in effect "hover with probes"; abstract overclaims null-control validation; "eight probes" unclear | Minor |

## Revision roadmap

Status key:
- **T:** text only
- **A:** analysis on existing data
- **E:** new experiment, needs the stack after the repeats finish about 13 Nov 23:30
- **C:** needs an OAI or Open5GS configuration change, so the author must approve before it is attempted

| # | Action | Resolves | Type | Effort |
|---|---|---|---|---|
| 1 | Remove "79 MB"; report bytes offered and drained from logs, or drop the figure | K6 | T | 10 min |
| 2 | Remove the 18–27 magnitude from the abstract until it is supported. Apply the null-control acceptance rule to every cross-night comparison. Mark the forest planner comparison not interpretable, or adopt a magnitude-bounded null rule | DA-C1, K7, m6 | T | 1 h |
| 3 | 3-seed repeats with a run-level bootstrap, plus a pre-declared equivalence margin for "not distinguishable" (already running) | K1, R3-W9 | A (data due 13 Nov) | 1 day |
| 4 | Retrain on capped data: CIs on every forecasting metric; horizon and model chosen on inner folds; repeat C3 on clean data (already planned) | K2, K9, R4-M6 | A | 2 days |
| 5 | State the training terrains of every deployed model (the forest fly-off used the forest-held-out model; verify in code), the OAI commit, the radio configuration, and how 40 m and the 20–60 m geofence were chosen | R1-W11, R2-W5, R3-W6 | T + A | 2 h |
| 6 | Validate the dead-process rule against watchdog relaunch timestamps and UE exit events in the watchdog-era runs; report precision and recall | K8 | A | 0.5 day |
| 7 | Re-measure C2 with probes **outside** the prioritised class, or with application-level C2 traffic, in one static and one patrol test | K3 | E | 0.5 day |
| 8 | Locate the queue: log UE RLC/PDCP buffer occupancy and BSR during the static load test; report RLC mode, discardTimer and buffer bound | K4 (R2-W1) | E (+C if the logging config changes) | 1 day |
| 9 | Restrict "the cap improves every arm" to the like-for-like arms A and B, or add uncapped runs of the cautious C/D | K10 | T (or E) | 1 h |
| 10 | Baselines: fq_codel on the robot TUN (a cheap test of the paper's own claim that the kernel queue is empty) and a separate DRB/QoS flow for C2 (Open5GS QoS change) | K5 | E (fq_codel) / C (DRB) | 1–2 days |
| 11 | Reframe for NOMS: a loop architecture figure (what runs where, the four per-robot cap agents, the planner), a SATNAC side-by-side table, a management-first abstract, NOMS/CNSM/O-RAN literature | R0-W1/W2/W3/W6/W7, R2-W8 | T | 1 day |
| 12 | Operational metrics: outage-duration CDF, C2 latency bound, video usability statement; argue the terrain-blind premise or reframe it as terrain-agnostic generalisation | R3-W1/W4/W6 | A + T | 0.5 day |
| 13 | Soften C4 wording: "lost by climbing" → "lost while spending more time at 45–55 m"; cautious learner = "hover with probes, no benefit over hover"; drop "same robot paths" | R4-M4, m5, R1-W12 | T | 30 min |
| 14 | Acknowledge the specification limit of the C3 model, or re-run the recovered-contrast test with an interaction-capable model (GBM) | R4-M5 | A | 0.5 day |
| 15 | Define every acronym on first use (UE, RAN, RTT, ROC-AUC, PR-AUC, HTB, ICMP, TUN, RLC, AIMD, UCB, TCN, GRU, UL); explain "eight probes" | acronym check, m7 | T | 30 min |

**Fit to the timeline:** the 22 Nov draft can include items 1–6 and 9–15. Items 7, 8 and 10 need about 3 days of stack time between 14 and 20 Nov. The DRB baseline in item 10 needs your approval for an Open5GS configuration change.

---

## Attachment: Suggested-Reference Check (advisory, #1001)

Script output from `verify_panel_recommendations.py`. It is not a reviewer finding.

- **Found in an index (15 of 21):** Zeng 2019, Bayerlein 2018, Liu 2019, Chen & Gesbert 2017, Carlucci 2016 (GCC), PBE-CC 2020, ABC 2020, Irazabal 2020 (dynamic buffer sizing in 5G, notably relevant to K4), Polese 2023 (O-RAN), Bonati 2023 (OpenRAN Gym), BBR 2016, Murphy 2014, LEDBAT RFC 6817, Chen 2007 (teleoperation).
- **Not matched (no DOI to check):** 3GPP TR 36.777, SCReAM RFC 8298, Verus 2015, 3GPP TS 38.323, 3GPP TS 23.501 QoS. These are standards or RFCs the indexes don't hold. Verify them directly before citing.

"Found" means the work exists. It does not show that the work supports the reviewer's point.

## Attachment: Reviewer Claim Check (advisory, #1002)

Did not run. It was skipped in this simplified run.

## Attachment: Acronym Check (advisory, #849)

**Not checked:** "gNB" and "OAI", because the words before their parentheses don't spell them out.

**Undefined at first use:** UE, AUC/ROC, AI, RAN, RLC, AIMD, TUN, HTB, ICMP, RTT, UDP, PR, UL, UCB, GRU, TCN. "UAV" is defined after its first use. "CA", "DA" and "CLAIMS" are false hits from math notation and the TODO text.
