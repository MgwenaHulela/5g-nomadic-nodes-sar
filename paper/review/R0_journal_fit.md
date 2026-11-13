# Peer Review Report

## Manuscript Information
- **Title**: Bounded Queues Before Learned Placement: Autonomous Management of a UAV-Borne 5G Cell for Search-and-Rescue Robots
- **Manuscript ID**: N/A (internal simulated review, target IEEE/IFIP NOMS 2027)
- **Review Date**: 2026-11-12
- **Review Round**: Round 1 review

---

## Reviewer Information

### Reviewer Role *
Journal-Fit Reviewer (EIC seat)

### Reviewer Identity *
NOMS Technical Program Committee member specialising in autonomic and zero-touch network management and AI for network operations; has reviewed many "AI for networking" submissions.

### Review Focus *
Whether this is a network-management contribution (autonomy, proactive control, monitoring) suited to NOMS 2027 ("Intent, Agents, and Autonomy: Shaping the Future of Network Management") or a wireless/robotics paper at the wrong venue; whether the delta over the authors' SATNAC 2026 paper is clear and sufficient; and whether a negative/tied AI result gives the NOMS audience a usable lesson. I do not go deep into methodology.

---

## Overall Assessment *

### Recommendation *
- [ ] **Accept**
- [ ] **Minor Revision**
- [x] **Major Revision**
- [ ] **Reject**

**Score (1-10)**: 5

### Confidence Score *
4 — core expertise in autonomic management and AI-for-operations venues; wireless/UAV propagation detail is adjacent.

### Summary Assessment *
The paper builds a terrain-blind learning layer (C2-failure forecaster, risk planner, on-line altitude learner) for a UAV-borne 5G cell serving four emulated SAR robots, flies it in closed loop over OAI/Open5GS, and finds that the largest gain came from a non-learned fix: a delay-driven AIMD uplink cap at each robot that removes UE-internal queueing, plus a dead-process censoring rule. Learned forecasting is modest (ROC-AUC 0.667) and degrades with capacity, the model does not recover the terrain-dependent altitude effect, and no learned placement beats hover or centroid. For NOMS this is a borderline-good fit: the headline ("verify the managed system is measured and loaded soundly before learning") is exactly the kind of operational lesson the AI-for-management community needs, and the validation discipline (leave-one-terrain-out with worst-fold reporting, same-policy null controls) is better than most AI-for-networking submissions I see. However, the management framing is currently asserted rather than built: there is no management architecture, no intent/objective layer, no agent or loop-composition story, and no engagement with the NOMS/CNSM literature on autonomic networking, intent, or ML-for-networking pitfalls. The delta over SATNAC is real but the strongest contribution (C1) is a bufferbloat diagnosis fixed with classic AIMD, and the learning results rest on pre-cap data that the paper's own lesson says are contaminated. With the three-seed repeats still pending and the C4 claim on one run per arm, I recommend Major Revision: the reframing is repairable within the page budget, and the core lesson would then serve the theme rather than undercut it.

---

## Strengths *

### S1: The negative AI result is turned into an operational lesson rather than buried
The paper states its claims C1-C4 up front with their outcome ("C1 holds; C2 and C4 hold weakly; C3 holds as a negative result") and ends with a procedural lesson. For a NOMS audience saturated with positive ML-for-ops results that rarely survive deployment, a closed-loop account of where learning did not help, and why, is valuable and on-theme for "Autonomy".
**Evidence Anchor**: `text: Conclusion "the lesson is to verify that the managed system is measured and loaded soundly, bounding its queues and checking that its processes are alive, before learning where to fly it"`

### S2: Validation discipline that the AI-for-management community should copy
Each claim is tied to the control matching its threat: leave-one-terrain-out with worst-fold as figure of merit, dead-process censoring, and a same-policy null control for cross-night comparisons, which actually invalidates two of four terrains for the planner comparison. Reporting that urban and hills comparisons are "uninterpretable" is exactly the honesty NOMS reviewers ask for and rarely get.
**Evidence Anchor**: `text: Experimental Setup, Validation protocol "A result is accepted only after it survives the control that matches its threat"`

### S3: The dead-process detector is a genuine monitoring/measurement-integrity contribution
Identifying that 12.4 % of training cycles record a dead UE process rather than a link failure, and that a lagged model "detects them trivially", is a concrete instance of label leakage from a silent failure of the managed system. This is directly relevant to NOMS's monitoring and AIOps tracks.
**Evidence Anchor**: `text: Section III-B "The rule flags 12.4\,\% of all cycles and 72.2\,\% of hills failure labels"`

### S4: Closed-loop evaluation over a real 5G software stack
Management functions are flown in closed loop against a real OAI gNB/UE and Open5GS core rather than evaluated offline on traces, which is what lets the paper observe the queueing failure at all.
**Evidence Anchor**: `figure: Fig. 1 — closed management loop with cap, forecaster, planner and altitude learner as the managed functions`

---

## Weaknesses *

### W1: The management contribution is asserted, not architected
**Problem**: The paper claims to fall under "proactive and autonomic management" via a CFP-style topic list, but never presents a management architecture: no closed-loop model (e.g., MAPE-K or ZSM closed loops), no statement of where each function runs (robot/UE side, UAV edge, core, a RIC), who owns it (the cap runs on the robot, which is arguably the tenant, not the network operator), what the operator objective or intent is, and how the cap, watchdog, forecaster, planner and altitude learner are composed or arbitrated when they conflict. Fig. 1 shows boxes, but the text treats them as four independent experiments.
**Evidence Anchor**: `text: Introduction, Contributions "The work falls under wireless and 5G networks, mission-critical networks, proactive and autonomic management, AI-based management, monitoring and measurement"`
**Why it matters**: Without this, a NOMS TPC will read the paper as a wireless/UAV-placement study with an ML evaluation, and the theme fit ("Intent, Agents, and Autonomy") rests only on the word "autonomous" in the title. This is the single largest risk of a venue-fit rejection.
**Suggestion**: Delete the topic-list sentence. Add a short subsection (or expand Fig. 1's caption and Section III intro) that casts the system as a layered autonomic loop: monitor (RTT, bytes, gNB counters, liveness), analyse (dead-process rule, forecaster), plan (planner/learner), execute (cap, UAV move, UE relaunch). State the operator objective as an intent (e.g., "keep every robot's C2 up; video best-effort") and show that the cap and watchdog are the inner, fast, model-free loops while placement is the outer, slow, learned loop. Say explicitly which functions map to UE-side, edge, or Near-RT/Non-RT RIC placement. This directly converts the headline into a design principle: inner-loop safety mechanisms before outer-loop learning.
**Severity**: Major
**Confidence**: 5 — core expertise: autonomic management venues

### W2: Related work omits the NOMS/CNSM literature the lesson must be positioned against
**Problem**: The "Learning-based and autonomous management" paragraph cites one zero-touch survey and RAN failure-prediction work. It does not engage with intent-based networking, self-driving/autonomic network surveys, O-RAN closed-loop control (xApps/rApps; only AERPAW is cited, as a testbed), or the well-known literature on pitfalls of ML in networked systems (data leakage, spurious correlations, simple baselines matching ML). The paper's headline is a member of that last family, but is presented as if novel.
**Evidence Anchor**: `text: Section II "Zero-touch management aims at fully autonomous operation under human oversight~\cite{coronado2022zsm}"`
**Why it matters**: NOMS reviewers will ask "how is 'simple feedback beats learning' and 'check your labels for leakage' new?" Without positioning, the lesson looks naive; with it, the paper becomes a concrete, closed-loop, 5G-stack instance of a recognised problem, which is a legitimate NOMS contribution.
**Suggestion**: Add 4-6 references across intent-based/autonomic management, O-RAN closed-loop control (RIC), and ML-for-networking pitfalls and generalisation (e.g., work on data leakage and shortcut learning in network ML, and on out-of-distribution generalisation of network models). Then state precisely what this paper adds: a closed-loop, held-out-terrain demonstration on a real 5G stack, with a mechanistic diagnosis of the two silent failures.
**Severity**: Major
**Confidence**: 4 — core expertise: AI-for-management literature

### W3: The delta over SATNAC 2026 is real but not cleanly drawn, and C1 partly re-opens SATNAC's own result
**Problem**: The paper reuses the SATNAC testbed and channel model ("details in~\cite{makhubela2026satnac}"), the measured terrain dependence of the altitude effect "was reported in" SATNAC, and its metrics are "not comparable across the two papers". Meanwhile C1 suggests SATNAC's forest penalty for a roaming node may itself have been queueing, which the paper notes but leaves "untested". The contributions list says "Beyond the testbed and terrain comparison" but never states SATNAC's findings and this paper's delta side by side.
**Evidence Anchor**: `text: Results V-A "The same mechanism may bear on the forest result of our earlier study~\cite{makhubela2026satnac}... we have not tested this"`
**Why it matters**: A TPC member who reads both papers will see the same platform, terrains and authors; if the delta is not explicit, the paper risks being judged incremental. Worse, leaving SATNAC's headline possibly confounded and untested invites the question of which paper's forest result to believe.
**Suggestion**: Add a two-or-three-line "relation to [SATNAC]" paragraph (or a small table) listing: what SATNAC established (testbed, terrain decides whether moving helps), what is new here (four robots, management functions, cap, liveness detector, terrain-blind learning evaluated held-out and closed-loop), and what is re-used. Either run the SATNAC-style roaming forest case once with the cap, or state explicitly that SATNAC's forest conclusion should be read as conditional on uncapped uplink load. Highlight the dead-process detector and the closed-loop learning evaluation as the novelty; C1's mechanism alone (bufferbloat + AIMD) is not novel and should be sold as a diagnosis-and-fix, not a new algorithm.
**Severity**: Major
**Confidence**: 4 — venue judgement; I have only the citation and this manuscript's description of SATNAC

### W4: The learning results are evaluated on data that the paper's own lesson says are unsound
**Problem**: The headline lesson is to bound queues before learning, yet the forecaster and altitude-effect results (C2, C3) are trained and evaluated on 54,572 robot-cycles "all flown before the cap existed", whose forest labels "are partly queue backlog". So the negative learning results may partly reflect the uncorrected failure rather than a limit of terrain-blind learning.
**Evidence Anchor**: `text: Section VI "The forecasting and altitude-effect data predate the cap, so their forest labels are partly queue backlog."`
**Why it matters**: For theme fit this matters more than for methodology: the usable NOMS message is "fix the managed system, then learning becomes worthwhile (or still isn't)". As written, the paper demonstrates only the first half and cannot say whether learning helps once the system is sound, which leaves the headline as "learning failed on bad data", a much weaker lesson.
**Suggestion**: Either retrain/re-evaluate the forecaster on capped, watchdog-protected data (even a reduced set, e.g., two terrains) and report whether skill changes, or reframe C2/C3 explicitly as "learning on an unsound managed system" and state the before/after experiment as the decisive future work. The conclusion already names this as a next step; promote it to a stated scope limit in the abstract.
**Severity**: Major
**Confidence**: 4 — theme-level judgement; methodology seats should assess the size of the effect

### W5: The closed-loop placement claim (C4) is not yet submission-ready
**Problem**: C4 rests on one 90-min run per arm with within-run intervals; the three-seed repeat results, the multi-seed figure, the reproducibility appendix, and several reference verifications are still marked pending.
**Evidence Anchor**: `text: Results V-D "\todo{replace with the 3-seed forest and open-field results of the repeat campaign"`
**Why it matters**: The "no learned placement beats hover or centroid" half of the headline is the part NOMS readers will quote. A tie claimed from single runs with intervals of roughly +/-15 points is not a usable lesson; it is an absence of evidence.
**Suggestion**: Complete the repeats before submission; report the cross-seed spread alongside within-run CIs; phrase C4 as an equivalence-style statement with a stated margin (e.g., "within X points of hover"), not "not distinguishable". Fill Appendix A (reproducibility) or remove it.
**Severity**: Major
**Confidence**: 4 — venue readiness judgement

### W6: The abstract and introduction lead with the wireless/robotics story, not the management lesson
**Problem**: The abstract opens with a UAV-borne gNB serving SAR robots and "We built a terrain-blind, learning-based management layer", and only reaches the management lesson in its last sentence. The title gets it right; the abstract does not.
**Evidence Anchor**: `text: Abstract "A UAV-borne 5G base station can serve search-and-rescue robots, but it must be managed autonomously, without knowing the terrain."`
**Why it matters**: NOMS bidding and first-pass triage are driven by the abstract; a reviewer skimming it will route the paper toward wireless/UAV expertise.
**Suggestion**: Open with the management problem (autonomous closed-loop management of a cell with no history and no operator), state the lesson in sentence two or three, and present the SAR UAV cell as the case study.
**Severity**: Minor
**Confidence**: 5 — core expertise: NOMS submissions

### W7: The "agents" dimension of the theme is untouched
**Problem**: Four robots, a UAV, a planner and per-robot caps form a natural multi-agent management setting (per-robot controllers acting on a shared cell), but the paper never discusses interaction between the per-robot AIMD loops (fairness, convergence when several cut at once) or between the cap and the placement loop.
**Evidence Anchor**: `absence: Section III-D and Section V-A — expected discussion of interaction/fairness among the four per-robot cap controllers and with the placement loop; checked Sections III, V, VI and Appendix C`
**Why it matters**: A short discussion would connect directly to the 2027 theme at little cost and anticipate an obvious TPC question.
**Suggestion**: Add one paragraph: the cap loops are decentralised agents sharing one uplink; report whether the per-robot medians (105-429 kb/s) reflect link quality rather than contention, and note timescale separation (0.2 s cap vs 30 s placement) as the reason the loops do not fight.
**Severity**: Minor
**Confidence**: 4 — core expertise: autonomic loop composition

---

## Detailed Comments *

### Title & Abstract
- The title is strong and states the lesson; keep it. The abstract should be reordered (W6). "Validated on held-out terrain and against null controls" is a good signal for NOMS; keep it near the front.

### Introduction
- Motivation is clear for SAR; the management motivation (no operator, no map, no history) is there in one sentence and deserves expansion as the framing (W1).
- Claims C1-C4 with explicit outcomes are a model of clarity.
- The CFP-topic sentence in Contributions should be removed (W1).

### Literature Review / Theoretical Framework
- Coverage of UAV placement and bufferbloat is adequate; management literature is thin (W2). The bufferbloat paragraph is well positioned (UE buffer "is not ours to change") and that sentence is a nice management argument about control boundaries; build on it.

### Methodology / Research Design
- Not assessed in depth (other seats). From a fit perspective, the terrain-blind constraint with a guard function enforcing forbidden inputs is a good, transferable practice for leakage control.

### Results / Findings
- C1 is the most convincing and most deployable result; the forest patrol numbers (66.8 % to 91.8 % C2; 17.4 % to 0.9 % no-robot time) are the paper's best headline for operators.
- C2's "more capacity is worse" under worst-fold evaluation is a useful message for AIOps practitioners; consider stating it as a general caution on out-of-distribution evaluation.
- C3/C4 depend on W4 and W5.

### Discussion
- There is no Discussion section; the lesson appears only in the Conclusion. A short Discussion ("Lessons for autonomous network management") of 3-4 generalisable points would be the highest-value addition for NOMS: (i) audit liveness and label integrity before training; (ii) place fast model-free safety loops inside slow learned loops; (iii) evaluate on held-out environments with worst-fold metrics; (iv) use same-policy null controls for cross-run closed-loop comparisons.

### Conclusion
- Does not over-infer; appropriately hedged. The final lesson is the paper's value proposition and should be echoed in the abstract.

### References
- 28 entries, several still marked [VERIFY] in refs.bib. Management-venue references are nearly absent (W2).

---

## Questions for Authors *

1. Where would each management function run in a real deployment (robot/UE, UAV edge, core, RIC), and who is assumed to control the robot-side cap: the network operator or the robot operator? Does the answer change the claim "no change to the RAN or core"?
2. If the forecaster is retrained on capped, watchdog-protected data, does its held-out skill or its recovery of the altitude effect change? Even a partial answer determines whether the headline is "learning is unnecessary" or "learning needs a sound managed system first".
3. Does the SATNAC forest finding (roaming node costs about 22 points) survive with the cap applied? If not, how should readers reconcile the two papers?
4. Does the NOMS 2027 call require double-blind submission? If so, the author block and phrasing such as "Our earlier testbed study" need anonymising.

---

## Minor Issues

### Language / Grammar
- Intro: "C1 holds; C2 and C4 hold weakly; C3 holds as a negative result" — C3 is stated as a negative claim, so "holds" alone suffices; "as a negative result" is confusing.

### Citation Format
- Several bibliography entries carry [VERIFY] notes (including the SATNAC page range); resolve before submission.

### Figures and Tables
- Fig. 1 should label loop timescales (0.2 s cap, 30 s placement) and the monitor/analyse/plan/execute roles to carry the management framing.
- Fig. 6 caption still carries a \todo marker.

### Layout
- Author list is marked as to be confirmed in main.tex; SATNAC had four authors, this has two; confirm.

---

## Criterion-Bound Judgements *

Calibration status: `NOT_CALIBRATED`

| Dimension | Criterion source | Judgement | Evidence anchor(s) | Rationale | Uncertainty / scope limit | Decision bearing? |
|---|---|---|---|---|---|---|
| Originality | NOMS CFP theme; delta vs SATNAC | PARTLY_MEETS | W3; S3 | Dead-process detector and closed-loop learning evaluation are new; C1 mechanism is classic | Have not read SATNAC itself | Yes |
| Methodological Rigor | references/quality_rubrics.md | NOT_ASSESSED | S2 | Deferred to methodology seat; validation protocol looks strong | Out of seat scope | No |
| Evidence Sufficiency | references/quality_rubrics.md | PARTLY_MEETS | W4; W5 | C1 well supported; C2/C3 on pre-cap data; C4 single runs | Repeats pending | Yes |
| Argument Coherence | references/quality_rubrics.md | PARTLY_MEETS | W4 | Lesson and learning evaluation are in tension (learning on unsound data) | — | Yes |
| Writing Quality | references/quality_rubrics.md | MEETS | S1; W6 | Clear, concise, claims explicit; abstract ordering off-venue | — | No |
| Literature Integration | references/quality_rubrics.md | PARTLY_MEETS | W2 | Wireless and bufferbloat covered; management/AIOps literature thin | — | Yes |
| Significance & Impact | NOMS 2027 theme fit | PARTLY_MEETS | W1; S1 | Lesson is valuable to NOMS but the management architecture is not built | Repairable by reframing | Yes |

**Recommendation rationale**: The unresolved decision-bearing criteria are venue framing (W1, W2), novelty articulation over SATNAC (W3), and the coherence between the headline lesson and the pre-cap learning data (W4), plus submission readiness of C4 (W5). W1-W3 are repairable by rewriting within the page budget (the main text currently ends on page 6 of 8). W4 needs either a modest new experiment or an explicit rescoping. The headline that a simple feedback rule beats learning does not undercut theme fit; framed as "build sound inner loops and verify the managed system before adding learned autonomy", it serves the "Autonomy" theme well and is a lesson NOMS rarely receives with closed-loop evidence. Major Revision, score 5/10; with W1-W5 addressed I would expect 7/10 at NOMS.
