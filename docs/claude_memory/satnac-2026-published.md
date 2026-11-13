---
name: satnac-2026-published
description: Exact citation and setup of the published SATNAC 2026 paper (differs from local satnac_final.tex draft)
metadata:
  type: reference
---
Published: Makhubela, Mwangama, Nemaangani, Sachan, "Terrain-Aware Positioning of UAV-Mounted Nomadic 5G Nodes for Search-and-Rescue Robotics", Proc. SATNAC 2026, pp. 419–(last page unverified). Author pasted the text 2026-11-12.
The local docs/paper/satnac_final.tex is an OLDER, longer draft with a different title — cite and compare against the published version.
Published setup: 2 robots (bravo 5 dB weaker), 6-h trials (static 0–3 h, random walk 3–6 h), per-cycle 2-s TCP + 2-s 8 Mb/s UDP burst + 5 ICMP echoes, C2 = ≥1 of 5 echoes answered (NOT loss<50% as in the ML-layer work). Key numbers: open bravo C2 56.5→88.7, forest avg 96.2→74.2 (−22 pp), UDP loss >70 % forest roaming.
Its reference list (Bor-Yaliniz ICC'16, Kalantari VTC'16, Wu&Zhang TCOM'18, Al-Hourani GLOBECOM'14, Murphy Disaster Robotics, 3GPP TR 38.811/38.901, ITU-R P.833/P.526, Gudmundson) counts as author-supplied for the NOMS paper. See [[msc-proposal-status]], [[nomadic-gnb-terrain-results]].
