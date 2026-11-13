# Peer Review Report

## Manuscript Information
- **Title**: Bounded Queues Before Learned Placement: Autonomous Management of a UAV-Borne 5G Cell for Search-and-Rescue Robots
- **Manuscript ID**: n/a (pre-submission, target IEEE/IFIP NOMS 2027 full paper)
- **Review Date**: 2026-11-12
- **Review Round**: Round 1 review

---

## Reviewer Information

### Reviewer Role
Peer Reviewer 1 (Methodology)

### Reviewer Identity
Experimental-networking methodologist who designs testbed evaluations and statistical analyses of networked systems (repeatability, confounding, block-bootstrap inference, held-out validation of learned models).

### Review Focus
Whether the experimental design and the inference supports the four claims as worded. Specifically: (1) one run per arm, within-run block-bootstrap CIs and before/after comparisons across nights; (2) the validity of the controls (dead-process censoring rule, same-policy null control, uniform time base); (3) leave-one-terrain-out (LOTO) with four terrains, worst-fold reporting, leakage, and congestion-contaminated pre-cap labels. I pay particular attention to whether "the cap improves every arm" and "a cautious learner is not distinguishable from baselines" are established.

---

## Overall Assessment

### Recommendation
- [ ] Accept
- [ ] Minor Revision
- [x] **Major Revision**
- [ ] Reject

**Score: 5/10** (borderline. The diagnosis in C1 is a real, publishable finding. The closed-loop inference as written does not yet support several of the headline sentences. With the 3-seed campaign and a re-analysis, the paper could become a 7.)

### Confidence Score
4: core expertise in testbed methodology and resampling inference; somewhat less on OAI UE internals.

### Summary Assessment
The paper builds a terrain-blind management layer for a UAV-borne OAI 5G cell serving four emulated robots: an onset forecaster, a risk planner and an on-the-spot altitude learner. Its strongest result is diagnostic. Long forest outages are unbounded uplink queuing inside the OAI UE, and an RTT-driven per-robot AIMD cap removes them. The learning results are mostly negative: modest LOTO skill, capacity hurts, terrain-dependent altitude effect not recovered, and no learned placement beats hover or centroid.

Methodologically, the paper is unusually candid. It states one run per arm, introduces a same-policy null control, reports worst-fold skill, and moves to a uniform time base. These are real strengths. However, the inference machinery does not match the claims. Closed-loop and cap comparisons are single runs on different nights, with 95% CIs from roughly 9 (fly-off) or 3 (patrol) ten-minute blocks. The paper's own null control shows same-policy night-to-night swings of about 20 points. The "cap improves every arm" comparison changes the learner algorithm in arms C and D at the same time. "Not distinguishable" is asserted without any equivalence margin, from intervals about ±15 points wide. The cautious learner was designed after seeing the forest result and was evaluated on the same forest seed. Forecasting AUCs carry no uncertainty, and the planner's risk model may have been trained on the terrain it then flew. The C1 mechanism survives these problems. Most quantitative closed-loop sentences in the abstract do not yet survive them. Major revision.

---

## Strengths

### S1: Mechanistic, multi-observable diagnosis of the queueing failure
The static load test does more than show an outcome difference. It shows the mechanism: RTT grows to minutes, probes are delayed rather than dropped, the kernel TUN queue holds no backlog, roughly 79 MB per robot is accepted by the UE, and there is a 14–18+ min drain tail. Two interventions (a fixed 100 kb/s cap and the adaptive cap) both remove the effect. This is a convergent-evidence design, and it is the most credible result in the paper.
**Evidence Anchor**: `text: §V-A "The kernel queue on each TUN held no backlog and dropped nothing, so the excess, about 79\,MB per robot, was accepted and held by the OAI UE"`

### S2: Explicit threat-to-control mapping, including a same-policy null control
The validation protocol pairs each threat with a control. The same-policy null control for cross-night comparisons is a good idea that is rarely seen in testbed papers. It is used honestly to declare the urban and hills planner comparisons uninterpretable.
**Evidence Anchor**: `table: Table A3 — urban null +18.7 [+12.9, +24.4]* and hills null −21.7 [−32.1, −12.2]* marked "Usable: no"`

### S3: Worst-fold reporting and a feature-leakage guard for LOTO
Leakage paths are named and blocked: injected path loss, the algebraically equivalent received power, and terrain labels. A guard function enforces this. Thresholds and calibration are chosen on an inner time split. Worst-fold ROC is reported next to pooled lift.
**Evidence Anchor**: `text: §III-C "Three inputs are forbidden: the simulator's injected path loss, the computed received power (algebraically equivalent to it), and any terrain label."`

### S4: Recognition of outcome-dependent sampling
The authors note that cycle length depends on the outcome, because probes block on failure. They re-express the altitude contrasts on 60-s and 120-s uniform time bases, and they report the 120-s result even though it partly cuts against C3.
**Evidence Anchor**: `text: Appendix C "cycle length depends on the outcome (longer when C2 is down, and longest in forest)"`

### S5: Negative results reported as such
Capacity-hurts, non-recovery of the altitude interaction, and no learned arm beating baselines are all stated plainly. The one counter-example (the medium MLP at 60 s) is disclosed.
**Evidence Anchor**: `text: §V-B "One exception should be stated: on six trend features at a 60-s horizon, the medium network beats the linear models on pooled lift"`

---

## Weaknesses

### W1: "The cap improves every placement policy" confounds the cap with a change of learner in arms C and D
**Problem**: The uncapped and capped versions of arms C and D did not run the same altitude algorithm. Uncapped C and D used a UCB bandit; capped C and D used the prober learner. For two of the four arms, the before/after difference is therefore a joint cap + algorithm effect. The abstract nevertheless says the cap "improved every placement policy", and Contribution 1 says it "improved each of four placement policies".
**Evidence Anchor**: `text: Fig. 3 caption "Arms C and D used a UCB altitude bandit uncapped and the prober learner capped."`
**Why it matters**: "Every policy" is presented as a replication across four policies. In fact, only A and B are like-for-like. Two like-for-like pairs, each a single cross-night run, are thin support for a generalisation stated in the abstract.
**Suggestion**: Restrict the claim to arms A and B, or re-fly C and D uncapped with the prober learner. Reword the abstract and Contribution 1 to "improved both fixed-policy arms; arms C and D changed learner as well".
**Severity**: Major
**Confidence**: 5 — core expertise: experimental design / confounding

### W2: Cross-night, single-run cap comparisons are not tested against the paper's own night-to-night variance
**Problem**: Every capped-vs-uncapped fly-off comparison is one run per arm on different nights. The protocol says cross-night comparisons are "accepted only where a same-policy null control is near zero". No null control exists for the capped/uncapped forest fly-off nights. The only forest null available (Table A3) is −5.0 [−17.6, +6.4], an interval of about 24 points. Same-policy swings in other terrains reached 18.7 and 21.7 points. The fly-off rise in mean C2 ("from 51–67 % to 82–89 %") could be as small as about 15 points for some arm pairings, which is within that range. The section also offers "the direction is the same in all four" as evidence, but four same-sign results have a probability of 1/16 under a null of random sign. That is not a formal test, and two of the four are confounded (W1).
**Evidence Anchor**: `text: §V-A "Each comparison is one run per arm on different nights, but the direction is the same in all four."`
**Why it matters**: The headline magnitude claims (mean C2 rise, the 25–35 % → 0–0.3 % no-robot-up drop) are the paper's main quantitative contribution. The direction is very plausibly real given S1. The magnitudes are not established against run-to-run variation, and the protocol's own acceptance rule is not applied to them.
**Suggestion**: Apply the protocol consistently. (a) Run a forest same-policy null pair under each condition, or better, interleave capped and uncapped runs of the same arm within a night and with ≥3 seeds (the pending campaign should be designed as paired seeds × {cap, no cap}). (b) Report run-level means with between-run CIs, and analyse arm × cap with seed as a blocking factor. (c) Until then, state per-arm deltas with CIs, and mark the magnitudes as single-run.
**Severity**: Major
**Confidence**: 5 — core expertise: repeatability of testbed experiments

### W3: Block-bootstrap CIs with 3–9 blocks are unreliable and are being used as inferential evidence
**Problem**: Missions use 10-min blocks, so a 90-min run has about 9 blocks and the 30-min patrol about 3. Percentile block-bootstrap intervals from so few blocks badly under-cover and are highly discrete. The patrol's "All 4 up (95 % CI) 52.0 % (16–76)" is the visible symptom. The headline patrol numbers (mean C2 66.8 → 91.8, no-robot-up 17.4 → 0.9, p90 latency) carry no interval at all. For between-arm differences (e.g. C−A, D−A), the paper does not say how blocks from two independent runs are combined (time-matched pairing vs independent resampling).
**Evidence Anchor**: `table: Table A5 — "All 4 up (95 % CI) 52.0 % (16–76)" vs "72.7 % (67–77)"; no CI on Mean C2 up, No robot up, or latency rows`
**Why it matters**: Every closed-loop "established / not established" verdict in §V-D rests on these intervals. With so few blocks, both false "established" and false "not distinguishable" verdicts are likely. The overlapping all-4-up CIs in Table A5 actually fail to separate capped and uncapped on that metric.
**Suggestion**: Report the block length's justification (autocorrelation decay of the C2 series) and the effective number of blocks. Use a moving/circular block bootstrap with shorter blocks chosen from the measured autocorrelation, or a stationary bootstrap, and check coverage on the null-control pair. Say explicitly how differences between independent runs are resampled. Above all, make the 3-seed between-run variance the primary interval and treat within-run CIs as secondary. Give CIs for every patrol row and state why only the first 30 min are reported (pre-specified, or truncation of a longer run?).
**Severity**: Major
**Confidence**: 5 — core expertise: resampling inference for time series

### W4: "A cautious learner is not distinguishable from the baselines" is absence of evidence and is promoted to a claim
**Problem**: The C−A and D−A intervals for the cautious learner are about 25–31 points wide ([−17.9, +6.8], [−13.4, +17.7]). They would also fail to exclude a 13–18-point loss. C4 is worded as a positive finding ("a cautious one does not [lose]"), and the abstract and conclusion repeat it. No equivalence margin is defined. In addition, the learner spent 83–84 % of the mission at 40 m, the hover altitude. Arm C cautious is therefore close to arm B by construction, and similarity to the baselines is expected rather than learned.
**Evidence Anchor**: `text: §I C4 "an exploring altitude learner loses to them and a cautious one does not."`
**Why it matters**: A NOMS reader will take C4 as "caution fixes the problem". The data show only that a learner which mostly hovers was not detectably worse than hovering in one run.
**Suggestion**: Pre-declare an equivalence margin (e.g. ±5 points of all-4-up) and run a TOST on the 3-seed between-run estimates. If equivalence is not shown, reword to "not shown to differ; the design cannot exclude a loss of up to ~18 points". Also report what fraction of the cautious learner's decisions were probes and whether any probe changed the incumbent. That shows whether any learning occurred at all.
**Severity**: Major
**Confidence**: 5 — core expertise: hypothesis testing / equivalence

### W5: The cautious learner was designed after seeing the forest result and evaluated on the same terrain and seed
**Problem**: The cautious variant has a home altitude equal to "the hover altitude of 40 m", the arm that won the capped forest fly-off. Its offline bench includes "Capped forest" traces (Table A4). It was then flown on the same forest world and seed (Fig. 4 data: seed 1 only), and the +28.8-point gain over "its exploring version" is a cross-night comparison of runs on that seed. This is tuning on the test set. The home altitude encodes the answer that the earlier run revealed, and that knowledge is not terrain-blind.
**Evidence Anchor**: `table: Table A4 — row "Capped forest, .03" (exploring −1.9 / cautious −0.8) used to bench the learner that is then flown in capped forest`
**Why it matters**: The +28.8 [+11.9, +45.1] improvement and "avoids that loss" cannot be read as out-of-sample. It also undercuts the terrain-blind framing: the learner's prior was chosen from forest outcomes.
**Suggestion**: State that the cautious design was post hoc. Evaluate it on unseen seeds and on terrains not used in its design (open field, urban, hills). Ideally, choose the home altitude by a rule that does not use forest outcomes, and show sensitivity to the home altitude (e.g. 30, 40 and 50 m). Remove the capped-forest traces from the bench used to select its parameters, or report the bench without them.
**Severity**: Major
**Confidence**: 4 — core expertise: held-out evaluation; inference about the design sequence from the manuscript only

### W6: Forecasting skill is reported without uncertainty; "better than chance" and "capacity hurts" are not tested
**Problem**: Pooled ROC-AUC 0.667, worst-fold 0.538, the full model-class ordering (Fig. 2) and the horizon sweep (Table A2) are all point estimates. With strongly autocorrelated observations, only four folds and about 20 missions, a worst-fold ROC of 0.538 may not differ from 0.5. Differences of 0.002–0.003 between the three linear models certainly do not. "The ordering is monotone in capacity" also has no uncertainty attached. Hyperparameter tuning budgets for LightGBM, the random forest and the MLPs are not stated, so "capacity hurts" cannot be separated from "under-tuned high-capacity models".
**Evidence Anchor**: `figure: Fig. 2 — worst ROC 0.538 / 0.537 / 0.535 (linear) vs 0.460 / 0.442 (trees) vs 0.443–0.404 (MLPs), no intervals`
**Why it matters**: C2 is stated as a claim ("better than chance, and higher-capacity models do worse"). Neither half is statistically established as written.
**Suggestion**: Give mission-level (cluster) bootstrap CIs for pooled and per-fold ROC-AUC and PR-AUC lift. Give paired CIs for model differences on identical folds (e.g. a bootstrap of ΔAUC, or DeLong with a cluster correction). State per-model tuning grids and the inner-split selection, and report per-fold AUCs for all models, not only the worst.
**Severity**: Major
**Confidence**: 5 — core expertise: evaluation of predictive models under dependence

### W7: Model and horizon selection on held-out results; pooled AUC across fold models
**Problem**: The "best model" (L1 logistic) and the headline 20-s horizon appear to be chosen by comparing held-out LOTO results across 8 model classes, two feature sets and four horizons. The horizon sweep shows the GBM peaking at 10 s and the six-feature logit at 20 s, so the "skill peaks at a 20-s horizon" statement depends on the model. Pooling predictions from four separately calibrated fold models into one ROC also mixes fold-specific score scales and base rates.
**Evidence Anchor**: `table: Table A2 — GBM ROC 0.569 at 10 s vs 0.558 at 20 s; Logit-6 ROC 0.650 at 20 s`
**Why it matters**: Selecting the best of about 60 held-out configurations inflates the reported skill. That matters most when the skill is modest (0.667) and the worst fold is near chance.
**Suggestion**: Either pre-specify the model and horizon (and say so), or nest the selection inside the LOTO loop and report the nested score. Report mean-of-folds AUC alongside pooled AUC. Qualify the "peaks at 20 s" statement by model.
**Severity**: Minor
**Confidence**: 4 — core expertise; extent of selection inferred from what is reported

### W8: Forecasting and the altitude-effect premise rest on pre-cap labels the paper itself shows are congestion artefacts
**Problem**: All 54,572 training cycles predate the cap. The paper's own C1 result is that forest outages under video are UE queue backlog. Forest is the held-out fold behind the headline "Open − forest" contrast (+0.208), and forest labels are "partly queue backlog". Altitude changes link capacity and therefore the queue drain rate. The measured terrain dependence of the altitude effect, which C3 says the model fails to recover, may itself be partly a congestion phenomenon. The forecaster's forest fold is also scored on labels that a 20-s radio-feature forecaster "cannot see".
**Evidence Anchor**: `text: §V-B "Forest labels in this data predate the cap and are partly queue backlog"`
**Why it matters**: C2 and C3 are interpreted as properties of terrain-blind learning on the channel. With contaminated labels, the forest fold measures something else, and C3's ground truth (the "measured" contrast) is not a pure channel effect. The paper acknowledges this only in passing.
**Suggestion**: Quantify the contamination, e.g. the fraction of forest failure labels that coincide with RTT above some threshold while the radio is still up, if RTT was logged. Report C2 and C3 with forest excluded (a 3-terrain LOTO and contrasts without forest) as a sensitivity analysis. Better, re-collect a smaller capped dataset and test whether the Open − forest measured contrast survives. If it is not re-done, demote C2 and C3 to "on pre-cap data" in the abstract.
**Severity**: Major
**Confidence**: 4 — core expertise: label validity; channel-vs-queue split inferred from the paper's own C1

### W9: The dead-process rule may censor genuine coverage failures, and it is not validated against available ground truth
**Problem**: The rule flags a cycle when the gNB counters (`dl_bler`, `ul_nb_rbs`, `ul_bits_per_sym`) are frozen for ≥3 cycles while C2 is down. A UE that is alive but out of coverage, or that has lost RRC connection, would also stop being scheduled and freeze those counters. The rule therefore cannot tell a dead process from a sustained real outage. It removes 72.2 % of hills failure labels and moves hills censored C2 from 83.0 % to 94.6 %. That censoring is informative, because it removes the worst periods by construction. The process exit is directly observable: the watchdog relaunches exited UEs, and OS process state is available. Yet the rule is described as "not by ground truth".
**Evidence Anchor**: `table: Table A1 — Hills "Dead share of C2-down 72.2 %", C2 raw 83.0 % → censored 94.6 %`
**Why it matters**: All C2/C3 numbers are computed after this censoring, and the protocol applies it to "every label". If real sustained outages are censored, the forecaster is evaluated on survivable failures only, and the terrain contrasts are biased.
**Suggestion**: Validate the rule against process liveness (pid or exit logs, watchdog relaunch timestamps) on any runs where these exist, and report precision/recall. Report C2 and C3 results under the alternative of treating flagged cycles as failures (a bounding analysis). Say whether censoring is applied to the closed-loop placement metrics and the patrol as well.
**Severity**: Major
**Confidence**: 4 — core expertise: measurement validity; adjacent on OAI MAC counter behaviour

### W10: The planner null control is read as "usable" when its interval is as wide as the effect
**Problem**: Forest is declared usable because the null −5.0 [−17.6, +6.4] is not significant. But the planner effect −15.3 [−24.8, −5.0] lies inside the null's interval, and its own interval overlaps the null's. "Null not significant" is not the same as "null near zero". The criterion is effectively whether the null's CI covers zero, which rewards a noisy null. The null is also a single pair of nights per terrain.
**Evidence Anchor**: `table: Table A3 — forest: planner −15.3 [−24.8, −5.0]* vs null −5.0 [−17.6, +6.4], "Usable: yes"`
**Why it matters**: The section heading "survives its null control in two terrains" overstates the forest result. Applied properly, the paper's own control says forest is inconclusive.
**Suggestion**: Use a difference-in-differences (effect minus null) with a joint bootstrap CI, or treat the null spread as a run-to-run variance component. Define "near zero" with an explicit margin before looking at the effect.
**Severity**: Major
**Confidence**: 5 — core expertise: control design

### W11: Possible terrain leakage in the closed-loop planner and an unverified "terrain-blind" premise in arm D
**Problem**: The planner's risk model is reported with a "pooled held-out ROC-AUC 0.659". The manuscript does not say whether the model deployed in the forest fly-off (arm D) was trained with forest data included. If it was trained on all four terrains, the closed-loop forest results are not unseen-terrain evaluations. That conflicts with the framing that the layer works "without knowing the terrain" on an unseen site.
**Evidence Anchor**: `absence: §III-C and §IV "Placement arms" — expected a statement of which terrains the deployed planner model was trained on; checked §III-C, §IV, §V-D, Appendix A–C`
**Why it matters**: It determines whether C4 tests transfer or in-distribution control. In-distribution control failing would be an even stronger negative result, but it would be a different one.
**Suggestion**: State the training set of every deployed model. For closed-loop runs, deploy the forest-held-out model in forest.
**Severity**: Major
**Confidence**: 3 — cannot determine from manuscript; routed as finding because the framing depends on it

### W12: "Same pilot, seed" arms are not matched, which confounds the altitude-slope estimates
**Problem**: The robots' safety guards react to link state and consume the random stream, so patrols differ across arms, and arms flew on different nights. The C-vs-B "matched times, with the same x–y rule and robot paths" comparison (−6.6 per +10 m) therefore compares different robot trajectories under different night conditions. The two arms also differ in a policy that responds to link state, so altitude is endogenous.
**Evidence Anchor**: `text: §V-D "Comparing arm C with arm B at matched times, with the same x--y rule and robot paths"`
**Why it matters**: The "probably harmful" altitude estimate, and the argument that one mission cannot determine the sign, rely on this comparison being causal.
**Suggestion**: Decouple the safety guards' random stream from the pilot's (separate RNG streams), so patrols are identical up to link-triggered stops, and report trajectory divergence between arms. Alternatively, estimate altitude effects from a randomised altitude schedule within one run (a switchback design). Reword "same robot paths".
**Severity**: Minor
**Confidence**: 4 — core expertise: causal inference in closed-loop experiments

### W13: The cap's control signal is the evaluation metric, and the cap trades away video
**Problem**: The cap is driven by ICMP RTT from 0.2-s pings, gives ICMP and C2 a high-priority class, and is evaluated on C2 availability defined by ping loss. Some of the improvement in C2 availability is therefore prioritisation of the probe traffic itself. Video goodput is not shown to improve: static forest goodput is 0.13–0.49 Mb/s uncapped vs 0.10–0.42 Mb/s adaptive. The claim "without starving strong links" rests on a single open-field static test.
**Evidence Anchor**: `table: Table II — Video goodput "0.13--0.49" (No cap) vs "0.10--0.42" (Adaptive)`
**Why it matters**: A reader needs to know the cost side and whether C2 gains reflect actual robot-command traffic rather than probe priority. "Removes outages" should be paired with what was given up.
**Suggestion**: Measure C2 with application-level command traffic (or with probes not in the prioritised class), report video goodput and latency for every fly-off arm, and include an open-field capped fly-off rather than relying on the static test alone.
**Severity**: Minor
**Confidence**: 4 — core expertise: traffic measurement design

### W14: Text–figure mismatch in the cap-across-arms result
**Problem**: The text reports "mean C2 rose from 51–67 % to 82–89 % in every arm", but Fig. 3 plots only "all 4 robots C2 up" and "no robot C2 up". The mean-C2 numbers have no figure, table or CI.
**Evidence Anchor**: `figure: Fig. 3 — panels (a) all 4 robots C2 up and (b) no robot C2 up; no mean-C2 panel`
**Why it matters**: A headline magnitude cannot be checked.
**Suggestion**: Add a per-arm table (mean C2, all-4-up, no-robot-up, each with CI) for capped and uncapped.
**Severity**: Minor
**Confidence**: 5

---

## Detailed Comments

### Title & Abstract
- The title is accurate. The abstract over-reaches in three places relative to the evidence: "it improved every placement policy" (W1, W2), "a cautious one was not distinguishable from them" (W4: an absence-of-evidence statement presented as a finding), and "Validated on held-out terrain and against null controls" (the null control does not cover the cap or cautious-learner comparisons; W2, W5). The abstract should say "single 90-min runs per arm".

### Introduction
- C1–C4 are clear and falsifiable, which is welcome. "C2 and C4 hold weakly" is honest, but C4 includes a claim (cautious ≈ baselines) that cannot hold or fail with the present design. The decision rule "fixed in CLAIMS.md" (§V-D todo) should appear in the paper itself, with the equivalence margin and seed count, so readers can see it was pre-registered.

### Methodology / Research Design
- §III-B (dead-process rule): see W9. The rule's thresholds (three cycles, exact bit-identity) need a sensitivity analysis.
- §III-C: the forecaster is well guarded against feature leakage (S3), but model and horizon selection happen on outer folds (W7), and the deployed model's training set is unstated (W11).
- §IV validation protocol: good in principle. In practice, the cross-night acceptance rule is applied only to the planner (Table A3), not to the cap or cautious comparisons (W2, W5). The criterion "near zero" needs a margin (W10).
- §IV data: the paper does not say how altitude was varied across the 20 training missions (by which policy, randomised or not). The "measured" altitude contrasts in Table III are observational. If altitude was chosen by a link-reactive policy, the measured slope is confounded with robot positions and link state. Please describe the excitation design.

### Results / Findings
- §V-A static test: present it as one run per condition (if so) and give per-robot values, not only ranges. The 64–91 % loss range across four robots is the only dispersion shown.
- §V-A patrol: why the first 30 min? (W3)
- §V-C: the 120-s recovered contrast (+0.021, significant) means the "does not recover" verdict depends on the time base. "Recovers about one sixth" is a more accurate summary than "does not recover". How the CIs and p-values in Table III were obtained (unit of resampling: mission? cycle?) is not stated. With about five missions per terrain, mission-level resampling would give much wider intervals than cycle-level resampling.
- §V-D: no multiplicity control across the many CIs. Acceptable for a descriptive paper, but then "established" should be used sparingly.

### Discussion / Limitations
- §VI states the one-run limitation clearly. It should also list: the learner change between capped and uncapped C and D, the post hoc design of the cautious learner, the absence of a null control for the cap fly-off nights, and the unknown training terrains of the deployed planner.

### Conclusion
- "improved each of four placement policies, one run each" and "made cautious, it is not distinguishable from them within wide intervals" are better hedged than the abstract, but still overstated for the reasons in W1 and W4.

### References
- Not assessed in depth (outside my focus). Citing a block-bootstrap reference (e.g. Künsch 1989; Politis & Romano 1994) and an equivalence-testing reference would support the inference choices.

---

## Questions for Authors

1. Were the uncapped and capped forest fly-off runs for each arm on different nights, and was any same-policy null pair collected for those nights? If not, can the 3-seed campaign interleave cap and no-cap within nights with paired seeds?
2. Which terrains were in the training set of the risk model deployed in arm D (and in the planner-vs-repositioning comparison of Table A3)?
3. Can the dead-process rule be checked against UE process exit or watchdog relaunch timestamps? What are its precision and recall, and how do the C2/C3 results change if flagged cycles are counted as failures?
4. How were the model class, feature set and 20-s horizon chosen: pre-specified, or by comparing outer-fold results? How were altitudes varied during the 20 pre-cap training missions?

---

## Minor Issues

### Language / Grammar
- §V-D: "No learned arm beat a baseline" — add "in one forest run per arm".

### Figures and Tables
- Fig. 2: add bootstrap CIs and per-fold points.
- Fig. 3: add a mean-C2 panel or table (W14). Note that the "no robot up" capped bars are all near zero, so the y-scale hides the 0–0.3 % values.
- Fig. 4: currently one seed and marked partial. With one dot per bar it conveys nothing beyond Fig. 3.
- Table A5: give a CI for every row, and the block length used.
- Table III: state the resampling unit and the number of missions per terrain.

### Layout
- The reproducibility appendix is a placeholder (todo). It is needed for a testbed paper at NOMS.

---

## Criterion-Bound Judgements

Calibration status: `NOT_CALIBRATED`

| Dimension | Criterion source | Judgement | Evidence anchor(s) | Rationale | Uncertainty / scope limit | Decision bearing? |
|---|---|---|---|---|---|---|
| Originality | quality_rubrics.md | MEETS | §V-A, S1 | UE-internal uplink bufferbloat as the cause of SAR forest outages, fixed at the robot, is a useful diagnosis | Domain novelty is for R2 to judge | No |
| Methodological Rigor | quality_rubrics.md | PARTLY_MEETS | W1, W2, W3, W5, W9, W10 | Good control concepts, but single cross-night runs, few-block CIs, a confounded cap comparison and a post hoc learner design | Pending 3-seed campaign may repair much of this | Yes |
| Evidence Sufficiency | quality_rubrics.md | PARTLY_MEETS | W2, W4, W6, W8 | C1 mechanism supported; the cap's magnitudes, "cautious ≈ baselines" and C2 skill lack adequate inference | — | Yes |
| Argument Coherence | quality_rubrics.md | MEETS | §I C1–C4 | Claims explicit and mapped to sections; abstract overstates relative to body | — | No |
| Writing Quality | quality_rubrics.md | MEETS | whole paper | Clear and compact | Not my focus | No |
| Literature Integration | quality_rubrics.md | NOT_ASSESSED | — | Outside methodology seat | — | No |
| Significance & Impact | quality_rubrics.md | MEETS | §V-A, §VI | "Bound queues and check liveness before learning" is a transferable lesson if C1 replicates on hardware | Emulated OAI UE only | No |

The recommendation rests on the decision-bearing Methodological Rigor and Evidence Sufficiency judgements. All are repairable without new ideas: (i) run the 3-seed campaign as paired seeds × {cap, no cap} with interleaved nights and the same learner on both sides; (ii) replace within-run 9-block CIs with between-run intervals and an equivalence test for the cautious learner; (iii) add CIs to the forecasting results and a forest-excluded or capped-data sensitivity analysis; (iv) validate or bound the dead-process censoring; (v) state the deployed models' training terrains. Until then the abstract should be confined to what one run per arm can show: the direction of the cap effect, and the absence of any detected advantage for learned placement.
