# Claims (Step 3)

Evidence IDs refer to `paper/EVIDENCE.md`. Each claim is one falsifiable sentence, and each says what result would falsify it.

## Main contribution (NOMS framing)

> **A closed-loop evaluation of autonomous, proactive management for a UAV-borne 5G cell serving SAR robots, which shows that the decisive management action was not learned placement but keeping the measurement and transport path sound.**

The finding has two parts:

1. **A local, autonomic uplink cap restored C2 for the like-for-like placement arms (A, B); C/D confounded by a learner change (review K10).** The cap is driven only by RTT and bytes sent. It runs at each robot and fixes a silent queueing failure inside the UE.
2. **The terrain-blind learning layer delivers a modest 20 s forecast but cannot choose altitude.** It is safe in closed loop only when it is made cautious.

This fits the NOMS 2027 theme (autonomy and agents) and these topics: proactive management, autonomic management, AI-based management, monitoring and measurements, mission-critical networks, 5G, and testbed experimentation.

Claim 1 is the headline. Claims 2–4 state what learning can and cannot add on top of it.

## Claim 1 (headline): queue congestion, not coverage, and an autonomic fix

**Claim.** Under constant-rate robot video, the long forest C2 outages come from unbounded uplink queuing inside the UE, not from coverage loss. A delay-adaptive cap at each robot, using only RTT and bytes sent, removes them without starving strong links.

**Would falsify it:** at the same placement and channel, capping leaves C2 outages unchanged; or, under the cap, open-field links lose most of their video throughput.

**Evidence:**
- **E1 (mechanism):** ping loss 64–91 % and RTT in minutes under load; kernel qdisc backlog 0 with 0 drops; recovery takes 14–18+ min.
- **E2 (fixed 100 kbit cap):** 0 % ping loss, so the mechanism is confirmed.
- **E3 (adaptive, static forest):** 0 % ping loss; video at 85–94 % of the uncapped drain rate.
- **E4 (open field):** 1.55 Mb/s delivered with 0 cuts.
- **E5 (forest patrol):** C2 66.8 → 91.8 %; none-up 17.4 → 0.9 %.
- **E6 (four-arm fly-off):** none-up falls from 25–57 % to 0–0.3 % for every arm, and mean C2 rises from 29–67 % to 82–89 %.

**Verdict: holds.** The mechanism result is strong. The before/after comparisons rest on single runs, but the effect is large and has the same sign in all 4 arms. Open-field capped arms (D8, 3 seeds): A 84.2, B 85.4 all-4-up against uncapped A 50.2, B 57.0 (D2), so the "without starving strong links" half is not weakened.

## Claim 2: a terrain-blind forecaster has modest skill, and capacity hurts

**Claim.** With dead-process labels censored, a terrain-blind linear model predicts C2-failure onset 20 s ahead on an unseen terrain better than chance (worst held-out terrain ROC > 0.5). Higher-capacity models do worse on the worst held-out terrain.

**Would falsify it:** worst-fold ROC ≤ 0.5 for the linear model, or a higher-capacity model with a better worst fold.

**Evidence:**
- **A4 (contamination removed):** 12.4 % of rows were dead-process artefacts.
- **A6 (model classes):** L1 logistic reaches lift 1.79, ROC 0.667, worst fold 0.538. MLP-large reaches 1.04 / 0.503 with worst fold 0.404, and the ordering is monotone in capacity.
- **A5 (horizon):** skill peaks at 20 s.
- **A7:** 10 Hz sequence models add nothing.

**Verdict: holds weakly.** Skill is above chance but small (worst fold 0.538). Forest labels are pre-cap and therefore partly backlog (Q). The capacity ordering is strong on `radio_blind`, but on the trend-6 set at 60 s the MLPs win on pooled data while losing worst-fold. The text must report that exception.

## Claim 3: the model does not recover the terrain dependence of altitude

**Claim.** The measured altitude effect differs strongly between open field and forest. The same contrast, computed from the terrain-blind model's risk estimates, is indistinguishable from zero. The model therefore cannot choose altitude.

**Would falsify it:** a recovered open-field-minus-forest contrast that is significantly positive.

**Evidence:**
- **B2 (measured, uniform time base):** open − forest +0.184\*, +0.167\*, +0.117\* per +10 m.
- **B3 and B4 (recovered):** open − forest +0.006 (p 0.365) per cycle; n.s. on 30 s and 60 s uniform bases (raw +0.010 at 60 s). At 120 s the raw model's contrast is +0.021 [+0.003, +0.038], significant, but about one sixth of the measured +0.117. The calibrated model is n.s. at every base. Two other contrasts are significantly inverted per cycle. (Corrected in Step 5; the earlier wording "n.s. at every bin width" was true only for the calibrated model.)
- **D1 (offline replay):** no surrogate chooses altitude. Linear models pin at 55 m everywhere; LightGBM picks about 47 m in both terrains.

**Verdict: holds, as a negative result.** Caveats:
- The forest side of the *measured* effect is not individually significant.
- The terrain sign-flip itself is SATNAC's result and is cited, not claimed as new. What is new is the test of whether a terrain-blind model recovers it.

## Claim 4: closed-loop learned placement does not beat simple baselines, but a cautious learner is safe

**Claim.** With the cap on, no learned placement policy keeps all four robots connected more often than fixed hover or the robot centroid. An altitude learner that explores loses to them, and a cautious learner (home altitude, a higher bar for leaving) does not.

**Would falsify it:** a learned arm whose all-4-up exceeds the best baseline with a CI excluding 0. Separately, the safety part is falsified if the cautious arm loses to the baseline with a CI excluding 0.

**Evidence:**
- **D5 (exploring learner):** C − A −18.1 [−34.1, −2.6]\*; D − A −27.4 [−43.0, −10.7]\*.
- **D7 (cautious learner):** C − A −5.1 [−17.9, +6.8]; D − A +1.6 [−13.4, +17.7]; cautious D − exploring D +28.8 [+11.9, +45.1]\*.
- **D6 (why):** one run cannot sign the forest altitude effect. Cross-run −6.6 [−14.9, +0.9]; within-run +4.6 and +5.9 with CIs about ±10.
- **C2 (risk planner vs two_phase):** open +10.5\* and forest −15.3\*, both surviving the null control. Urban and hills are withdrawn.

**Verdict: holds weakly (D8 final below).** Everything above is one run per arm with within-run CIs. The final verdict will be taken from the 3-seed runs in forest and open field (D8, due about 13 Nov 23:30), using a rule fixed now:

- Pool seeds per arm.
- Bootstrap over runs and blocks.
- A learned arm "beats" a baseline only if its all-4-up CI excludes 0.
- Exploring-learner arms are not in D8: those runs flew only the cautious learner. The exploring-learner loss stays a single-run result.

**D8 final (14 Nov, all 24 runs, 3 seeds × 4 arms × 2 worlds; `scripts/ml/final_repeats_deep.py`):** no learned arm beats hover under the rule above.
- Open field (hover 84.2): B − A +1.2 [−0.9, +3.5], C − A +1.0 [−3.4, +4.5], D − A −0.7 [−5.6, +3.3] (95 % CIs). All equivalent within ±5 on the 90 % interval (margin set after seeds 1–2, stated as such).
- Forest (hover 70.6): B − A −8.4 [−19.4, +2.1], C − A −7.0 [−19.9, +4.7], D − A −8.0 [−18.6, +4.2]. None established.
- Re-flown: open D s1 and forest D s3 (start-up failures, 0 rows) and forest D s3 run killed by the 13 Nov memory crash; failed attempts excluded, not imputed.
- Safety part not falsified: cautious C − A interval includes 0. Its worst run (52.1 %) spent 59 % of the mission at ≥ 45 m.

## Supporting results (not claims)

- **Dead-process detector (A4):** the precondition for Claim 2; described in Method.
- **Planner defects found only in flight (C3):** one paragraph in Method or Limitations, as a lesson for closed-loop AI management.
- **Multi-head union risk (A9):** the appendix at most.

## Deliberately not claimed

| Result | Why it is out |
|---|---|
| "Altitude harms under canopy" | The forest coefficient is not individually significant (B1, B2, D6) |
| Planner gains in urban and hills | Withdrawn by the null control |
| Any forecasting number at 60 s | Withdrawn (A1) |
| That SATNAC's forest result is a congestion artefact | Untested; one open-question sentence only |


## Changes after the mock review (2026-11-12)

- C1: "every placement policy" → "both placement arms flown with and without the cap" (A, B); "unbounded" dropped (queue location not instrumented); 79 MB figure removed.
- C4: "exploring learner lost 18–27 points" removed from the abstract (cross-night single runs; null-control swings reach about 20 points); the cautious learner is described as hover with probes.
- Null-control rule tightened: a cross-night comparison counts only if the null's interval rules out a swing as large as the effect. The forest planner result (−15.3) is now uninterpretable; only open field (+10.5) stands. This rule was set after seeing the data, in response to review K7, and the paper states it.
