---
name: field-test-follows-sim
description: The sar_sim emulation study is phase 1; real-hardware field testing is the planned next phase
metadata: 
  node_type: memory
  type: project
  originSessionId: 1165f5f8-c23c-4b7d-8e68-f4c8a126e046
  modified: 2026-08-03T15:28:26.495Z
---

The protocol-in-the-loop emulation work (see [[nomadic-gnb-terrain-results]] and
[[research-campaign-tooling]]) is explicitly **phase 1**. Nhlanhla plans a
**real-hardware field test** as the follow-on phase — a physical UAV-mounted 5G
node (SDR/OAI on real radios) serving real ground robots across terrains, to
validate the simulation's terrain-dependent hover-vs-roam findings against
measured over-the-air performance.

Implications when advising:
- Frame sim results as *to be validated on hardware*, not final; keep the
  sim-to-hardware gap explicit (the SATNAC paper's limitations/future-work
  already say this).
- Prefer choices that transfer to hardware (real OAI/Open5GS stack, honest
  over-air C2 path, calibratable channel constants) over sim-only shortcuts.
- The emulation's value is partly as a rehearsal/design tool for the field
  campaign (terrain selection, node placement rules, expected coverage edges,
  the recovery-time metric that needs finer logging on hardware).

SATNAC 2026 paper: `docs/paper/satnac_final.tex` ("Where to Fly the Cell
Tower…"), 10 pp, standalone, disaster-response framing.
