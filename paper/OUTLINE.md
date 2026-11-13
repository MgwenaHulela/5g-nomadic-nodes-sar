# Outline and page budget (Step 4)

**Budget:** 8.0 pages of main text; total including references and appendix ≤ 12.

**Format:** IEEEtran conference, two columns. One column-width float costs about 0.2–0.3 pages; a full-width float about 0.4.

Evidence IDs refer to `paper/EVIDENCE.md`; claims to `paper/CLAIMS.md`.

**Working title:** *Bounded Queues Before Learned Placement: Autonomous Management of a UAV-Borne 5G Cell for Search-and-Rescue Robots* [author to confirm]

## Page budget

| Section | Pages |
|---|---|
| 1 Introduction | 1.00 |
| 2 Background and related work | 0.50 |
| 3 System and method | 1.50 |
| 4 Experimental setup | 0.75 |
| 5 Results | 3.50 |
| 6 Limitations and threats to validity | 0.40 |
| 7 Conclusion | 0.35 |
| **Main text total** | **8.00** |
| References (~25 entries) | ~0.9 |
| Appendix | ≤ 3.0 |
| **Total** | **≤ 12** |

## 1. Introduction (1.00 p)

1. **Problem.** SAR robots need mission-critical C2 where infrastructure is gone. A UAV-borne gNB must be managed autonomously, without knowing the terrain.
2. **Gap.**
   - Placement work assumes channel knowledge and a clean transport.
   - Learning-based link management is evaluated open loop, on held-out time rather than held-out terrain.
   - Silent failures of the managed system are rarely examined.
3. **What SATNAC 2026 showed, and what is new.** SATNAC showed the effect of mobility depends on terrain. This paper adds proactive, learning-based management in closed loop with 4 robots. Cite SATNAC once, precisely.
4. **The four claims** in one sentence each (C1 headline).
5. **Contributions list**, naming NOMS topics where they truly apply:
   - wireless/5G
   - mission-critical networks
   - proactive and autonomic management
   - AI-based management
   - monitoring and measurements
   - testbed experimentation

No floats.

## 2. Background and related work (0.50 p)

- **A2G placement and channel models:** Al-Hourani, Bor-Yaliniz, Kalantari, Mozaffari, Zeng. Two sentences.
- **Excess buffering in cellular uplinks and its remedies:** bufferbloat, Jiang et al., CoDel, LEDBAT, BBR, Sprout, AIMD. [VERIFY each by search]
- **Learning-based link prediction and proactive network management**, and why held-out-terrain validation differs. [TODO: 2–3 references to be found and verified by search]

No floats.

## 3. System and method (1.50 p)

### 3.1 Platform (0.35 p)
- OAI rfsim gNB + 4 UEs in network namespaces, Open5GS, Gazebo, the A2G bridge.
- One sentence plus a citation for the channel model (detail lives in SATNAC).
- **Fig. 1** (column): the closed management loop. Bridge → RAN → probes → forecaster → planner/pilot → UAV, with the uplink cap at each robot. Redrawn from the v2 diagram and made smaller.

### 3.2 Measurement integrity: dead-process detector (0.20 p)
- The mechanism, and the conjunction rule: frozen gNB counters for ≥ 3 cycles **and** C2 down.
- The watchdog that removes it at source.
- Extent figures go to the appendix (**Table A1**).

### 3.3 Terrain-blind learning layer (0.55 p)
- Forbidden inputs, enforced by a guard in code.
- Onset label at h = 20 s; `radio_blind` features; L1 logistic + Platt calibration.
- Planner: soft-min over worst-robot risk; absolute targets on a 25–55 m grid; 10 m per decision.
- On-the-spot altitude learner (prober); the cautious variant with its home altitude, 1.5 SE and +0.02 rule.

### 3.4 Delay-adaptive uplink cap (0.40 p)
- HTB at each robot's TUN: C2 in a priority class, video in a short FIFO.
- ICMP RTT probe at 5 Hz; AIMD with slow start.
- **Algorithm 1** (column, ~0.15 p).

## 4. Experimental setup (0.75 p)

- **Data:** 20 missions, 54,572 robot-cycles, 4 terrains (forecasting); fly-off campaigns (placement).
- **Validation protocol:** leave-one-terrain-out; worst-fold reporting; block bootstrap; same-policy null control; fresh seeds (D8); dead-process censoring; congestion removal.
- **Metrics:** time-weighted mean C2 up, all 4 up, none up; video p90 latency; PR lift and ROC for forecasting.
- **Arms and baselines:**
  - A: fixed hover, 40 m
  - B: robot centroid, 40 m (the proposal baseline)
  - C: centroid + learned altitude
  - D: model x-y + learned altitude
  - plus SATNAC's two_phase, and static vs adaptive cap
- **Definitions** that differ from SATNAC, in one sentence: C2 up = loss < 50 %; 4 robots; continuous 1.5 Mb/s.

**Table 1** (column, ~0.2 p): the arms and what each controls.

## 5. Results (3.50 p)

Strongest claim first.

### 5.1 C1: uplink queuing, not coverage, and an autonomic cap (1.25 p)

**Fig. 2** (full width, 0.4 p): uplink C2 RTT per robot, (a) no cap, (b) adaptive cap, static forest. *[exists: fig_rtt]*

**Table 2** (column, 0.25 p): the static load test, no cap / fixed cap / adaptive cap: ping loss, RTT, recovery, video goodput and latency (E1–E3).

**Fig. 3** (column, 0.25 p): none-up and all-4-up per arm, uncapped vs capped, forest. This replaces the patrol table. Patrol numbers (E5) go in text. Open-field cap result (E4) in one sentence.

**Text points:**
1. The mechanism: linear RTT growth; qdisc empty; 14–18 min recovery.
2. The fixed cap proves it.
3. The adaptive cap tracks each link; open field is untouched.
4. The cap lifts every arm.

**Open question, one sentence:** relevance to SATNAC's forest result.

### 5.2 C2: modest forecasting skill; capacity hurts (0.60 p)

**Fig. 4** (column, 0.25 p): PR lift, with worst-fold ROC annotated, for 8 model classes. *[exists: fig_capacity; add worst-fold markers, re-plot from `phase2c_model_class.csv` rather than typed numbers]*

**Text points:**
- 20 s peak (horizon sweep in text; table in the appendix).
- The trend-6 at 60 s exception.
- 10 Hz sequences add nothing.

### 5.3 C3: the model does not recover the terrain-dependent altitude effect (0.60 p)

**Table 3** (column, 0.25 p): measured vs recovered contrasts per +10 m, per cycle and on a 60 s uniform time base (B2–B4), with p-values.

**Text points:**
- The surrogate's altitude choices from D1 (55 m everywhere; LightGBM about 47 m in both terrains).
- Therefore altitude is learned on the spot.

### 5.4 C4: learned placement under the cap (1.05 p)

**Fig. 5** (column, 0.3 p): all-4-up per arm with CIs, forest and open field, **3 seeds** (D8). *[pending; current fig_arms is single-run]*

**Fig. 6** (full width short, or column, 0.25 p): UAV altitude over time, exploring vs cautious. *[exists: fig_altitude]*

**Text points:**
1. The exploring learner loses (D5).
2. Why: within-run slope CI ±10 vs cross-run −6.6 (D6).
3. The cautious learner ties (D7 + D8).
4. Risk planner vs two_phase survives the null control in open field (+10.5) and forest (−15.3) only (C2). Numbers in text; table in the appendix.

## 6. Limitations and threats to validity (0.40 p)

Condensed, one sentence each:
- AWGN, no multipath
- Near-ground height not modelled
- 4 terrains
- Outcome-dependent cycle length
- The OAI UE is not a commercial modem
- Constant-rate video
- Pre-cap training data
- Repeats only for the final fly-off

Full version in **Appendix C**.

## 7. Conclusion (0.35 p)

- Answer each claim in one line.
- The management lesson: bound queues and verify liveness before learning placement.
- Next steps: hardware trial; retrain on capped data.

## Appendix (≤ 3 p, after references)

| Item | Content | Source |
|---|---|---|
| A. Reproducibility | Every table/figure → script → data file; commands; runtimes | Step 9 README |
| Table A1 | Dead-process extent per terrain | `dead_process_extent.csv` |
| Table A2 | Horizon sweep | `phase2b_onset.csv` |
| Table A3 | Planner vs two_phase with null control | `planner_vs_twophase_nullctl.csv` |
| Table A4 | Offline bench, exploring vs cautious | `altitude_bench_cautious_validate_*` |
| Table A5 | Forest patrol, capped vs uncapped | E5 |
| B | Planner defects found only in flight (ratchet, frame inversion) | C3 |
| C | Full threats to validity | `CHANNEL_MODEL_LIMITATIONS.md` etc. |
| D | Cap pseudo-code detail; tc tree | `adaptive_uplink_cap.py` |

## Figure rules (Step 5)

- Vector PDF, greyscale-readable: distinct markers and line styles, not colour alone.
- Each figure has a script in `paper/figs/`.
- Every number is checked against its source file.

## Risks to the budget

- Results at 3.5 p is tight with 3 figures and 2 tables. **First cut if over:** move Table 2 to the appendix and keep Fig. 2.
- D8 lands about 13 Nov 23:30. If open-field repeats change C1's "doesn't starve strong links" part, 5.1 grows by one sentence, not a section.
