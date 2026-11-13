---
name: nomadic-gnb-terrain-results
description: Multi-terrain static-vs-nomadic gNB experiment results; nomadic-gNB effect is terrain-dependent
metadata: 
  node_type: memory
  type: project
  originSessionId: 1165f5f8-c23c-4b7d-8e68-f4c8a126e046
  modified: 2026-07-30T10:01:04.983Z
---

Result of the 5-terrain `--research` campaign (3h static gNB then 3h nomadic
3D-random-walk gNB per world), completed 2026-07-30. See
[[research-campaign-tooling]] and [[coverage-rlf-kills-ue-process]].

**Headline: the nomadic-gNB effect on C2 availability is terrain-dependent, and
scales with how PERVASIVE the blockage is:**
- open_field (LoS): nomadic **helps** the weak robot (bravo 56%→89% C2) — a
  wandering gNB drifts closer and rescues robots stranded near the edge.
- urban (localized building NLoS): **neutral** (~96–98% both phases) — the guard
  keeps robots in LoS pockets either way.
- hills (terrain diffraction): nomadic **hurts mildly** (−5 to −16 pp).
- forest (pervasive foliage): nomadic **hurts hard** (−22 pp; 97→73%) — mobility
  means longer paths through trees.
- indoor (O2I): **coverage-limited / near-uncoverable** deep inside (12–18% C2
  even after fixes). The fixed 9 dB penetration + baseline + shadow sits at the
  RLF cliff everywhere inside; each reattach costs ~2 min, so availability
  collapses. Shipped as a qualitative caveat, not a clean world. Retreat-to-door
  is the realistic mitigation.

**Data:** open_field 733 / urban 721 / forest 482 / hills 974 rows in
`logs/campaign_2026072*/`. 4 clean worlds + indoor caveat.

**Key engineering enabler:** the NLoS-aware coverage guard was essential —
without it (distance-only `safe_radius`) all 4 NLoS worlds cascaded (robot sits
NLoS-blocked inside the geometric radius → RLF → reattach can't recover a UE
with no signal). The guard reacts to REAL ploss (read_ploss_fast): steer toward
gNB when elevated, teleport to ~15 m when near the cliff. Caveat for analysis:
the guard keeps robots in coverage rather than free-roaming to the edge, so NLoS
robot trajectories are more gNB-centred than open_field's.
