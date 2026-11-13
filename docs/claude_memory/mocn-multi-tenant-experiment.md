---
name: mocn-multi-tenant-experiment
description: "21 Sep 2026 MOCN / two-tenant (PLMN 999-70 + 999-71) shared-gNB experiment — where the configs, OAI patch and evidence live"
metadata:
  node_type: memory
  type: project
  originSessionId: c868ad38-ca1d-43d6-b95e-4442fd5ea0c6
  modified: 2026-11-02T12:35:21.080Z
---

On **2026-09-21** a MOCN (Multi-Operator Core Network) multi-tenancy experiment was run:
one OAI gNB broadcasting two PLMNs, served by one shared Open5GS core with a separate,
isolated user plane per tenant. It worked: both tenants' UEs registered and got PDU sessions.

**Tenants**
- Tenant A: PLMN 999/70, S-NSSAI sst 1 / sd 0x000001, DNN `internet`, `smf` (127.0.0.4) → `upf` (127.0.0.7), pool 10.45.0.0/16 on `ogstun`. Test UE imsi-999700000000001.
- Tenant B: PLMN 999/71, sst 1 / sd 0x000002, DNN `tenantb`, `smf2` (127.0.0.24) → `upf2` (127.0.0.27), pool 10.46.0.0/16 on `ogstun2`. Test UE imsi-999710000000001.
- Shared: AMF (two guami/tai entries), NRF serving both PLMNs, NSSF `nsi` for both SDs.
- Isolation: iptables FORWARD DROP between 10.45/16 and 10.46/16; separate MASQUERADE for 10.46 out of non-ogstun2.

**Where things are**
- Evidence + config snapshots: `~/mocn-evidence/` (numbered `01_`–`08_` excerpts; `final/` holds the clean 17:43 rerun logs). `final/gnb_run.log` is ~650 MB — trim it if disk space matters.
- gNB config: `~/gnb-mocn.conf` (`plmn_list` with both PLMNs, each with both SDs; SIB1 shows PLMN 1=999.70, PLMN 2=999.71).
- Live core configs: `/etc/open5gs/{amf,nrf,nssf,smf,smf2,upf,upf2}.yaml`, with `.bak` copies from before. Services `open5gs-smf2d` / `open5gs-upf2d` are enabled.
- OAI patch: `~/openairinterface5g` branch `mocn`, commit `62fafd5860` "RRC: accept DU if its PLMN is any configured PLMN (MOCN)" in `openair2/RRC/NR/rrc_gNB_du.c`. Stock OAI rejects the DU when the CU has more than one PLMN (`num_plmn == 1`, "F1 supports only one"). The patch accepts the DU if its single PLMN matches any CU PLMN.
- **Radio was over-the-air, not rfsim:** gNB on a USRP B200 (UHD 4.1, n78 3619.2 MHz, 106 PRB); UEs were **SDR-based OAI nr-uesoftmodem** (user-confirmed).
- Second OAI change: the multi-PLMN SIB1 patch (gnb_config.c + NR_MAC_gNB config/radio_config) was never committed; it is uncommitted working-tree diff in `~/openairinterface5g` on branch `mocn`. Don't `git checkout .`/stash-drop there.
- **Not part of the SAR sim** — separate project; belongs to the UCT multi-tenant testbed (`UCT-EEE-department/5g-multi-tenant-testbed`, owner Tariro Mukute; user has READ only → contribute via fork + PR). Local clone `~/5g-multi-tenant-testbed`, branch `mocn-single-gnb-two-plmn`, commit 5d6f1a4 adds `mocn/`; pushed to fork MgwenaHulela/5g-multi-tenant-testbed and opened as PR #1 on 2026-11-02. Only overlap with SAR: same host Open5GS install. Don't push MOCN to the SAR repo (was done by mistake and deleted).
- Known gap: gNB logs selected PLMN 999.70 for the tenant-B UE too (selectedPLMN-Identity not propagated); AMF still routed it correctly via SUCI.

**Gotchas**
- 05_amf_ngsetup shows repeated NG-Setup failures (BPLMN list) before success at 17:22. The AMF/gNB PLMN+slice lists must match exactly.
- This reconfiguration is what broke the SAR stack later: see [[stack-dead-sept-2026-core-drift]]. The SAR gNB/robots use a bare `sst: 1`, so the core now also carries bare-sst entries alongside the two tenant slices. Keep both when editing.

Related: [[sar-sim-stack-operation]]
