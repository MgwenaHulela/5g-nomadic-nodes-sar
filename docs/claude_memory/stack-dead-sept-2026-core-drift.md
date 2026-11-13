---
name: stack-dead-sept-2026-core-drift
description: The OAI/Open5GS stack was non-functional from 2026-09-21 to 2026-10-16 (core slice drift + NVIDIA driver mismatch); how to diagnose both
metadata: 
  node_type: memory
  type: project
  originSessionId: 30a2c230-eb87-4138-8520-68e653b73eed
  modified: 2026-10-16T13:20:09.736Z
---

Between **2026-09-21 and 2026-10-16** the SAR stack could not attach a single
UE. Two independent host-level faults, neither visible in the repo:

1. **Core slice drift.** A 21 Sep reconfiguration (amf/nrf/nssf/smf rewritten,
   `smf2`+`upf2` added for a two-tenant slice experiment) left `amf.yaml`,
   `nssf.yaml` and `smf.yaml` advertising only SD-qualified slices
   (`sst:1 sd:'000001'/'000002'`), while `oai_configs/gnb.sa.band78.rfsim.conf`
   and the three robot subscribers use a **bare `sst: 1`** with no SD.
   Fixed 2026-10-16 by adding a bare `sst: 1` entry to all three (additive —
   the two-tenant entries were kept so `smf2`/tenantb still works).

2. **NVIDIA driver mismatch.** Kernel module 595.84 vs userspace library
   595.99.02 — an update without a reboot. Breaks GL context creation, which
   kills `gz sim` even headless, because every world loads
   `gz-sim-sensors-system` with `ogre2` and `models/sar_burger/model.sdf`
   carries a camera. Needs a **reboot**; forcing software GL does NOT help
   (the X server's GLX extension comes from the broken driver itself).

**How to diagnose next time — the symptom is always the same ("TUN never
appeared"), but the cause is layered. Work down the chain:**

- `sudo tail /var/log/open5gs/amf.log` → `NG-Setup failure: Cannot find
  S_NSSAI` means the gNB/AMF slice lists disagree.
- Registration succeeds but `nnssf-nsselection ... HTTP response error [403]`
  means NSSF's `nsi` list is missing the slice.
- Registration + slice selection fine but still no TUN → check SMF `info`.
- RAN fine (`Over-air ping OK`) but bringup still fails → it is Gazebo; check
  `/tmp/gz_sim.log` for `GLX`/`X Error`, then `nvidia-smi`.

A UE reaching `RRC_CONNECTED` and sending a Registration Request proves the
radio is fine — any failure after that is core-side, not coverage.

Related: [[sar-sim-stack-operation]], [[coverage-rlf-kills-ue-process]]
