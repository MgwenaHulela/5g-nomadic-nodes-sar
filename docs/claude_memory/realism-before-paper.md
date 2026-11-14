---
name: realism-before-paper
description: "User decision 13 Nov 2026 — do the realism upgrades on the new PC BEFORE finishing the NOMS paper, not after submission"
metadata:
  node_type: memory
  type: project
  originSessionId: 30a2c230-eb87-4138-8520-68e653b73eed
  modified: 2026-11-13T17:44:35.979Z
---

On 13 Nov 2026 the user decided that the realism upgrades come **before** the
paper, not after submission. This reverses the earlier plan, which kept the
simulator frozen until NOMS 2027 was submitted (deadline 30 Nov, submit 29 Nov).

New order on the new PC (Core Ultra 24-core, 32 GB, RTX 50-series GPU):
1. Restore the stack, pass verify_stack.sh plus one smoke run, unchanged from the lab PC.
2. Realism upgrades, each a separate backed-up change with a validation run:
   CU/DU split over F1 with F1 delay, real VBR video with NVENC,
   3GPP fading channel in rfsim, PX4 SITL, real terrain plus Sionna RT,
   and optionally parallel stacks.
3. Then the paper: the retrain, throughput, and the remaining TODOs.

**Why:** the user wants the experiment as realistic as possible before the results are written up.

**How to apply:**
- Don't push the "freeze the simulator until submission" line again.
- Results from the upgraded stack are a new setup. Never pool them with the 24 lab-PC capped runs. Keep the old runs labelled as the lab-PC baseline.
- Flag deadline risk factually when it arises. Related: [[msc-proposal-status]], [[xyz-flyoff-result]].
