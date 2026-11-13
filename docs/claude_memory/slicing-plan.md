---
name: slicing-plan
description: Agreed design for adding eMBB/URLLC/mMTC slices to the sar_sim 5G stack (decided 2026-11-12, not yet built)
metadata:
  type: project
---
User approved (2026-11-12): slices under PLMN 999/70 — SST1 eMBB DNN `embb` 10.47.0.0/24 ogstun3; SST2 URLLC DNN `urllc` 10.47.1.0/24 ogstun4; SST3 MIoT DNN `iot` 10.47.2.0/24 ogstun5. Existing `internet` 10.45.0.0/16/ogstun and the MOCN smf2/upf2 (tenantb, ogstun2, 10.46/16) stay untouched. All FOUR robot UEs (IMSI …780–783) get all three slices in addition to `internet`. URLLC tries 5QI 82, fallback 80. New gNB config as a `_slices` copy, never edit live configs. Live Open5GS configs are /etc/open5gs (systemd); live OAI build is 09c9bb998f (supports uicc pdu_sessions list).
**Why:** reviewers asked why not use 5G-native separation instead of the robot-side uplink cap; slicing is the NOMS-relevant comparison (no cap / cap / slice / slice+cap).
**How to apply:** do NOT touch the stack while a campaign runs (shared stack; restarts kill runs). Backup to ~/sar_sim/backups/slices_<ts>/ first. Never claim QoS guarantees — OAI MAC shares radio capacity; measure. Open question: which "45-min baseline" for the soak comparison. See [[research-campaign-tooling]], [[mocn-multi-tenant-experiment]].
