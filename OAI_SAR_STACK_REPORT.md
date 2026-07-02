# SAR 5G Simulation — Stack Operation Report

**Date:** 1 July 2026
**Author:** Nhlanhla Makhubela

---

## 1. System Overview

The SAR simulation uses a 4-layer 5G standalone architecture:

| Layer | Software | Role |
|-------|----------|------|
| **Core Network** | Open5GS (v2, systemd services) | 5GC: AMF, SMF, UPF, AUSF, UDM, PCF, NRF, UDR |
| **RAN (gNB)** | OAI nr-softmodem | 5G base station, n78 band, RF simulator mode |
| **UEs** | OAI nr-uesoftmodem (×2) | Alpha (IMSI 999700123456780), Bravo (IMSI 999700123456781) |
| **Channel Control** | Telnet (ports 9091–9093) | Runtime path loss, noise, fading control |

---

## 2. Current Running State (as of report)

| Component | PID(s) | IP / Port | Status |
|-----------|--------|-----------|--------|
| Open5GS AMF | 266825 | N2: 38412 (SCTP) | Active |
| Open5GS SMF | 266771 | SBI: 7777 | Active |
| Open5GS UPF | 266772 | N3: GTP-U | Active (2 sessions) |
| gNB | 299732 | Telnet 9091, RFsim 4043 | Running |
| UE alpha | 300306 | Telnet 9092, TUN 10.45.0.17 | Connected |
| UE bravo | 300373 | Telnet 9093, TUN 10.45.0.18 | Connected |

**TUN Interfaces:**

| Interface | UE | IP Address |
|-----------|-----|------------|
| `oaitun_ue1` | alpha | 10.45.0.17/24 |
| `oaitun_ue1p2` | bravo | 10.45.0.18/24 |

**Communication:** Bidirectional ping working (~50ms RTT, 0% loss).

---

## 3. Startup Procedure

### 3.1 Prerequisites

Ensure Open5GS core is running:

```bash
systemctl status open5gs-amfd open5gs-smfd open5gs-upfd
```

Expected: all services `active (running)`.

### 3.2 Start the gNB

```bash
cd /home/innocentmsc/openairinterface5g
nohup ./cmake_targets/ran_build/build/nr-softmodem \
  --rfsim \
  -O /home/innocentmsc/sar_sim/oai_configs/gnb.sa.band78.rfsim.conf \
  --telnetsrv \
  > /tmp/gnb.log 2>&1 &
```

Verify: `ss -ltn | grep -E '(4043|9091)'` — both ports should be listening.

### 3.3 Start UE alpha

```bash
cd /home/innocentmsc/openairinterface5g
nohup ./cmake_targets/ran_build/build/nr-uesoftmodem \
  --telnetsrv --rfsim \
  -O /home/innocentmsc/sar_sim/oai_configs/nrue_alpha.conf \
  -r 106 --numerology 1 --band 78 -C 3319680000 --ssb 516 \
  > /tmp/ue_alpha.log 2>&1 &
```

Wait ~15s for PDU session establishment, then verify:

```bash
ip addr show oaitun_ue1      # Should show 10.45.0.x
ss -ltn | grep 9092           # Telnet port should be listening
```

### 3.4 Start UE bravo

```bash
cd /home/innocentmsc/openairinterface5g
nohup ./cmake_targets/ran_build/build/nr-uesoftmodem \
  --telnetsrv --rfsim \
  -O /home/innocentmsc/sar_sim/oai_configs/nrue_bravo.conf \
  -r 106 --numerology 1 --band 78 -C 3319680000 --ssb 516 \
  > /tmp/ue_bravo.log 2>&1 &
```

Wait ~15s, then verify:

```bash
ip addr show oaitun_ue1p2    # Should show 10.45.0.x
ss -ltn | grep 9093           # Telnet port should be listening
```

### 3.5 Quick-Start Script

Save this as `start_sar_stack.sh`:

```bash
#!/bin/bash
OAI_DIR=/home/innocentmsc/openairinterface5g
CONF_DIR=/home/innocentmsc/sar_sim/oai_configs

echo "=== Starting gNB ==="
nohup $OAI_DIR/cmake_targets/ran_build/build/nr-softmodem \
  --rfsim -O $CONF_DIR/gnb.sa.band78.rfsim.conf --telnetsrv \
  > /tmp/gnb.log 2>&1 &
sleep 5

echo "=== Starting UE alpha ==="
nohup $OAI_DIR/cmake_targets/ran_build/build/nr-uesoftmodem \
  --telnetsrv --rfsim -O $CONF_DIR/nrue_alpha.conf \
  -r 106 --numerology 1 --band 78 -C 3319680000 --ssb 516 \
  > /tmp/ue_alpha.log 2>&1 &

echo "=== Starting UE bravo ==="
nohup $OAI_DIR/cmake_targets/ran_build/build/nr-uesoftmodem \
  --telnetsrv --rfsim -O $CONF_DIR/nrue_bravo.conf \
  -r 106 --numerology 1 --band 78 -C 3319680000 --ssb 516 \
  > /tmp/ue_bravo.log 2>&1 &

echo "Done. Wait ~15s then check: ip addr show | grep oaitun"
```

---

## 4. TUN Interface Naming Fix

### The Problem

Both OAI nr-uesoftmodem instances create their TUN interface using the same default name `oaitun_ue1` (naming pattern: `oaitun_ue<ue_id+1>` where both UEs start with `ue_id=0`).

The second UE to start overwrites the first UE's interface.

### The Solution

Added a **dummy PDU session** to bravo's config (`nrue_bravo.conf`) with a different slice type (`nssai_sst = 2`) that is not in the allowed NSSAI list. This causes:

1. The dummy session is skipped (NSSAI mismatch)
2. The real session is at config index 1 → `is_default = false`
3. The interface name becomes `oaitun_ue1p2` (suffixed with PDU session ID)

**Config change** (`oai_configs/nrue_bravo.conf`):

```
uicc0 = {
  pdu_sessions = (
    { dnn = "dummy"; nssai_sst = 2; },          // skipped — not in allowed NSSAI
    { dnn = "internet"; nssai_sst = 1; }         // real session → oaitun_ue1p2
  );
};
```

**File modified:** `/home/innocentmsc/sar_sim/oai_configs/nrue_bravo.conf`

---

## 5. Verification

### 5.1 Quick Connectivity Test

```bash
# Ping alpha -> bravo
ping -I oaitun_ue1 -c 3 10.45.0.18

# Ping bravo -> alpha
ping -I oaitun_ue1p2 -c 3 10.45.0.17
```

Expected: 0% packet loss, ~50ms RTT.

### 5.2 Core Network Session Count

```bash
# AMF
journalctl -u open5gs-amfd --no-pager | grep "Number of AMF-Sessions"

# SMF
journalctl -u open5gs-smfd --no-pager | grep "Number of SMF-Sessions"

# UPF
journalctl -u open5gs-upfd --no-pager | grep "Number of UPF-Sessions"
```

Each should show 2 sessions (one per UE).

### 5.3 gNB UE Stats

```bash
grep -E "UE.*dlsch_rounds|UE.*ulsch_rounds" /tmp/gnb.log | tail -4
```

Expected: SNR ~22dB, BLER ~0, 0 CCE failures.

### 5.4 Telnet Channel Model Verification

```bash
# gNB channel models
echo "channelmod show current" | timeout 3 nc 127.0.0.1 9091

# UE alpha DL channel
echo "channelmod show current" | timeout 3 nc 127.0.0.1 9092

# UE bravo DL channel
echo "channelmod show current" | timeout 3 nc 127.0.0.1 9093
```

---

## 6. Configuration Files

| File | Purpose |
|------|---------|
| `oai_configs/gnb.sa.band78.rfsim.conf` | gNB: n78 band, 106 PRB, 30kHz SCS, AMF @ 127.0.0.5:38412 |
| `oai_configs/nrue_alpha.conf` | UE alpha: IMSI ...780, Rayleigh1, ploss=20dB, noise=-6dB |
| `oai_configs/nrue_bravo.conf` | UE bravo: IMSI ...781, Rayleigh1, ploss=25dB, noise=-4dB |
| `oai_configs/channelmod_sar.conf` | Standalone channel model reference config |

### Channel Model Mapping

| Side | Model Name | Direction | Type | ploss | noise | Defined In |
|------|-----------|-----------|------|-------|-------|------------|
| gNB | `rfsimu_channel_enB0` | DL (unused) | AWGN | 20dB | -6dB | gNB config |
| gNB | `rfsimu_channel_ue0` | UL from alpha | Rayleigh1 | 25dB | -6dB | gNB config |
| gNB | `rfsimu_channel_ue1` | UL from bravo | Rayleigh1 | 30dB | -4dB | gNB config |
| UE alpha | `rfsimu_channel_enB0` | DL to alpha | Rayleigh1 | 20dB | -6dB | alpha config |
| UE bravo | `rfsimu_channel_enB0` | DL to bravo | Rayleigh1 | 25dB | -4dB | bravo config |

---

## 7. Runtime Channel Control (Telnet)

### Port Mapping

| Process | Port | Controls |
|---------|------|----------|
| gNB | 9091 | UL channel models |
| UE alpha | 9092 | DL channel (alpha's view) |
| UE bravo | 9093 | DL channel (bravo's view) |

### Key Commands

| Command | Description | Example |
|---------|-------------|---------|
| `channelmod show current` | Show all channel models | |
| `channelmod modify 0 ploss 35` | Set path loss to 35dB | Weaker signal |
| `channelmod modify 0 noise_power_dB -2` | Increase noise | Lower SINR |
| `channelmod modify 0 forgetf 0.5` | Faster fading | Vehicular speed |

### Simulate UE Movement (alpha moving away)

```bash
for ploss in 20 25 30 35 40 45 50; do
  echo "channelmod modify 0 ploss $ploss" | nc 127.0.0.1 9092
  sleep 2
done
```

### Monitoring RSRP/SINR

```bash
watch -n 1 'grep -oP "SS-RSRP: [-\d.]+ dBm/RE \([-\d.]+ dB/RE\), SS-SINR: [-\d.]+ dB" /tmp/ue_alpha.log | tail -3'
```

---

## 8. Troubleshooting

| Symptom | Likely Cause | Fix |
|---------|-------------|-----|
| Connection refused on port 4043 | gNB not running | Start gNB first |
| oaitun_ue1 interface missing | PDU session not established | Wait 15-30s, check AMF/SMF logs |
| Only one UE interface visible | TUN name collision | See Section 4 above |
| Telnet doesn't respond | Process started without `--telnetsrv` | Restart with the flag |
| "Lost socket" errors | Process crashed | Check logs in /tmp/*.log |
| Segfault with EPA/TDL models | Build regression | Use Rayleigh1 instead |

### Log Locations

| Process | Log File |
|---------|----------|
| gNB | `/tmp/gnb.log` |
| UE alpha | `/tmp/ue_alpha.log` |
| UE bravo | `/tmp/ue_bravo.log` |
| Open5GS services | `journalctl -u open5gs-*` |

### Restarting the Stack

```bash
# Kill all OAI processes
pkill -9 nr-softmodem nr-uesoftmodem

# Verify killed
pgrep -a nr-softmodem nr-uesoftmodem  # should be empty

# Remove stale TUN interfaces
ip link delete oaitun_ue1 2>/dev/null
ip link delete oaitun_ue1p2 2>/dev/null

# Restart (see Section 3)
```

---

## 9. Files

All config files are in `/home/innocentmsc/sar_sim/oai_configs/`. Backup originals have `.bak` extension.

```
oai_configs/
├── gnb.sa.band78.rfsim.conf       # gNB config (active)
├── gnb.sa.band78.rfsim.conf.bak   # gNB config (original backup)
├── nrue_alpha.conf                # UE alpha config (active)
├── nrue_alpha.conf.bak            # UE alpha config (original backup)
├── nrue_bravo.conf                # UE bravo config (active, with fix)
├── nrue_bravo.conf.bak            # UE bravo config (original backup)
├── channelmod_sar.conf            # Standalone channel model reference
└── SIMULATION_GUIDE.md            # Original guide (channel model & telnet details)
```

---

## Appendix: The TUN Naming Issue (Technical Detail)

The OAI UE creates TUN interfaces with the pattern:

```
oaitun_ue<instance_id+1>[p<pdu_session_id>]
```

Where:
- `instance_id` is the NAS UE instance (0 for the first UE, 1 for the second, etc.)
- `pdu_session_id` is only appended when `is_default = false`

The `is_default` flag is `true` only for the **first PDU session** in the UICC config (`idx == 0`). Since both UEs have only one PDU session at index 0, they both create `oaitun_ue1`.

The fix adds a dummy PDU session at index 0 with a different NSSAI (SST=2), which is rejected by the NSSAI filter during registration. The real PDU session at index 1 becomes non-default, producing interface name `oaitun_ue1p2`.
