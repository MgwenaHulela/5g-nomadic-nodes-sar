---
name: msc-proposal-status
description: MSc proposal RQ1-RQ5 status as of 2026-11-10 — sim side done; CU/DU split DONE on hardware (Pi 5 + USRP B210, outside this repo); NR-U partial; remaining work list
metadata:
  type: project
---

Proposal: `Makhubela_Innocent_MSc_Research_Proposal_Topic_2026_pdf___Joyce_Copy.pdf` in the repo root
(supervisor Prof. Joyce Mwangama, 18-month plan). Status per the user, 2026-11-10:

- RQ1 positioning: done in sim (learned predictive planner). Not on hardware.
- RQ2 terrain: terrain half done; near-ground antenna height NOT modelled (fixed 0.5 m).
- RQ3 nomadic vs static: done in sim (SATNAC 2026: +16.7 open, ~0 urban, -10.6 hills, -22.0 forest).
- RQ4 sim-to-hardware: IN PROGRESS. **CU/DU split is built and running on a Raspberry Pi 5
  with F1 up and the cell on air via a USRP B210; a USRP UE attaches.** This lives OUTSIDE
  sar_sim, so a repo search will not find it — don't report the split as missing. Open:
  Pi 5 misses real-time deadlines at 51 PRB (25 PRB being tested), Jetson benchmark pending,
  no like-for-like sim-vs-hardware comparison yet.
- RQ5 NR vs NR-U: partial — honours work (WCNC 2026) + students attached a UE on n96. No Wi-Fi
  in the n96 test, no throughput, LBT threshold uncalibrated.
- Declared deviations: learned (not deterministic) positioning; OAI+Open5GS+Gazebo instead of
  ns-3/5G-LENA; PPO and IAB in the abstract are not covered (F1 midhaul, not IAB).
- Timeline: months 12-15 (experimental validation) in progress; MSc start date unknown.

**How to apply:** in-sim testbed fixes only matter insofar as they make the RQ1/RQ3 sim result
defensible; the critical path is RQ4 hardware validation. See [[field-test-follows-sim]].
