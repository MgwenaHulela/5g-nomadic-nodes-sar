# OAI NR Simulation — Channel Model & Telnet Guide

## Overview

This document describes how the RF simulator channel model works in your OAI gNB/UE setup, how to control it at runtime via telnet, and how to interpret the RSRP/SNR behavior.

## Architecture

### How the Channel Model Works

The RF simulator (`--rfsim`) connects the gNB and UE via a TCP socket on port **4043**. When channel modelling is enabled via `options = ("chanmod")`, every signal sample sent between gNB and UE passes through a configurable channel model before reaching the receiver.

```
gNB TX → TCP socket → UE RX → [UE channel model applied] → UE PHY
UE TX → TCP socket → gNB RX → [gNB channel model applied] → gNB PHY
```

Each process applies the channel model **on the receive side**:

| Side | Model Name | Direction | Where Defined |
|------|-----------|-----------|---------------|
| UE | `rfsimu_channel_enB0` | **DL** (gNB→UE) | UE config file |
| gNB | `rfsimu_channel_ue0` | **UL** (UE0→gNB) | gNB config file |
| gNB | `rfsimu_channel_ue1` | **UL** (UE1→gNB) | gNB config file |

### Telnet Servers

Both gNB and UE run independent telnet servers for runtime control:

| Process | Telnet Port | Controls |
|---------|------------|----------|
| gNB | **9091** | UL channel models (`ue0`, `ue1`) |
| UE alpha | **9092** | DL channel model (`enB0`) |
| UE bravo | **9093** | DL channel model (`enB0`) |

---

## Config Files

### `gnb.sa.band78.rfsim.conf`

| Model | Type | ploss_dB | noise_power_dB | forgetfact | Purpose |
|-------|------|----------|----------------|------------|---------|
| `rfsimu_channel_enB0` | AWGN | 20 | -6 | 0 | DL (gNB side, unused) |
| `rfsimu_channel_ue0` | Rayleigh1 | 25 | -6 | 0.99 | UL from UE alpha |
| `rfsimu_channel_ue1` | Rayleigh1 | 30 | -4 | 0.99 | UL from UE bravo |

### `nrue_alpha.conf`

| Model | Type | ploss_dB | noise_power_dB | forgetfact |
|-------|------|----------|----------------|------------|
| `rfsimu_channel_enB0` | Rayleigh1 | 20 | -6 | 0.99 |

### `nrue_bravo.conf`

| Model | Type | ploss_dB | noise_power_dB | forgetfact |
|-------|------|----------|----------------|------------|
| `rfsimu_channel_enB0` | Rayleigh1 | 25 | -4 | 0.99 |

---

## Running

### Start the 5GC (already running)

The Open5GS core network runs as system services. Verify:

```bash
ps aux | grep open5gs
```

### Start the gNB

```bash
cd /home/innocentmsc/openairinterface5g
./cmake_targets/ran_build/build/nr-softmodem \
  --rfsim \
  -O /home/innocentmsc/sar_sim/oai_configs/gnb.sa.band78.rfsim.conf \
  --telnetsrv
```

### Start UE alpha

```bash
cd /home/innocentmsc/openairinterface5g
./cmake_targets/ran_build/build/nr-uesoftmodem \
  --telnetsrv \
  --rfsim \
  -O /home/innocentmsc/sar_sim/oai_configs/nrue_alpha.conf \
  -r 106 --numerology 1 --band 78 -C 3319680000 --ssb 516
```

### Start UE bravo

```bash
cd /home/innocentmsc/openairinterface5g
./cmake_targets/ran_build/build/nr-uesoftmodem \
  --telnetsrv \
  --rfsim \
  -O /home/innocentmsc/sar_sim/oai_configs/nrue_bravo.conf \
  -r 106 --numerology 1 --band 78 -C 3319680000 --ssb 516
```

---

## Runtime Control via Telnet

### Show Current Channel Models

```bash
echo "channelmod show current" | nc 127.0.0.1 9092
```

### Commands

Telnet to the **UE** (port 9092 or 9093) to control **DL** RSRP/SNR.  
Telnet to the **gNB** (port 9091) to control **UL**.

```
channelmod modify <model_index> <param> <value>
```

| Parameter | Description | Example |
|-----------|-------------|---------|
| `ploss` | Path loss in dB (higher = weaker signal) | `channelmod modify 0 ploss 40` |
| `noise_power_dB` | Noise power in dB (higher = more noise) | `channelmod modify 0 noise_power_dB -2` |
| `forgetf` | Temporal correlation (0=random, 0.99=slow, 1=frozen) | `channelmod modify 0 forgetf 0.5` |
| `riceanf` | Ricean K factor (0=Rayleigh, >0=Ricean) | `channelmod modify 0 riceanf 5` |
| `offset` | Channel delay offset in samples | `channelmod modify 0 offset 10` |

### Example: Simulating a Moving UE

Script to simulate a UE moving away from and back toward the gNB:

```bash
for ploss in 20 25 30 35 40 45 50 45 40 35 30 25 20; do
  echo "channelmod modify 0 ploss $ploss" | nc 127.0.0.1 9092
  sleep 2
done
```

### Example: Changing Fading Speed

```bash
# Slow fading (pedestrian, ~3 km/h)
echo "channelmod modify 0 forgetf 0.99" | nc 127.0.0.1 9092

# Medium fading
echo "channelmod modify 0 forgetf 0.9" | nc 127.0.0.1 9092

# Fast fading (vehicular)
echo "channelmod modify 0 forgetf 0.5" | nc 127.0.0.1 9092
```

### Available Channel Model Types

| Type | Description | Taps | Notes |
|------|-------------|------|-------|
| `AWGN` | Additive White Gaussian Noise | 1 | Constant amplitude + noise. No fading. |
| `Rayleigh1` | Single-tap Rayleigh fading | 1 | Amplitude varies randomly (fading). Works well. |
| `Rayleigh8` | 8-tap Rayleigh fading | 8 | Frequency-selective fading. |
| `Rice1` | Single-tap Ricean (LOS + fading) | 1 | Includes a strong LOS path. |
| `Rice8` | 8-tap Ricean | 8 | Frequency-selective with LOS. |
| `EPA` / `EVA` / `ETU` | 3GPP channel models | 7-9 | Standardized profiles. May segfault on some builds. |
| `TDL_A`–`TDL_E` | 3GPP Tapped Delay Line | 12 | 38.901 models. May segfault on some builds. |

> **Note:** `EPA`, `TDL_A`, and `TDL_E` segfault on the current binary (a build regression). Use `Rayleigh1` for reliable fading.

---

## Metrics

### View RSRP and SINR

With `phy_log_level = "debug"` in the config, the UE logs:

```
[UE 0] ssb 0 SS-RSRP: -22 dBm/RE (71.27 dB/RE), SS-SINR: 32.4 dB
```

| Field | Meaning | Behavior |
|-------|---------|----------|
| `SS-RSRP` (dBm/RE) | Received power per RE in dBm | Changes with `ploss`. Stable with Rayleigh1 alone due to AGC. |
| `SS-RSRP` (dB/RE) | Relative received power per RE | Varies slightly with Rayleigh fading. |
| `SS-SINR` (dB) | Signal-to-interference-plus-noise ratio | Changes with both `ploss`, `noise_power_dB`, and fading. |

### Quick monitoring

```bash
watch -n 1 'grep -oP "SS-RSRP: [-\d.]+ dBm/RE \([-\d.]+ dB/RE\), SS-SINR: [-\d.]+ dB" /tmp/ue_alpha.log | tail -3'
```

### Channel Level (raw fading amplitude)

The per-slot raw channel level shows the instantaneous Rayleigh fading amplitude:

```
[PHY]    Channel level: 13535317
[PHY]    Channel level: 2829
[PHY]    Channel level: 13447108
```

Large variations (e.g., 2829 vs 13M) indicate deep fades vs constructive peaks — normal Rayleigh behavior.

---

## Troubleshooting

### Connection refused (errno 111)

The gNB is not running or port 4043 is not listening. Start the gNB first.

### Segmentation fault with EPA/TDL_A

These channel models crash on this build. Use `Rayleigh1` instead — it produces realistic fading without crashing.

### RSRP is constant even with Rayleigh1

This is normal. The Automatic Gain Control (AGC) in the UE adjusts the RF gain to keep the ADC input level constant. The RSRP formula subtracts the current gain:

```
rsrp_dBm = raw_rsrp - rx_gain - offset + 30
```

So RSRP in dBm stays stable even as the signal fades. **SINR** is the metric that varies with channel conditions when gain is compensated.

To force RSRP to change, use telnet to modify `ploss` — this changes the overall path loss faster than the AGC can track.

### "Lost socket"

The gNB or UE closed the TCP connection. This happens if:
- The gNB crashes (check gNB log)
- The gNB's channel model for the UL direction has a different model type
- One side exits unexpectedly

### Telnet doesn't respond

Make sure you used `--telnetsrv` when starting the process. Each process has its own telnet port:
- gNB: 9091
- UE alpha: 9092
- UE bravo: 9093

---

## Common Telnet Recipes

### Move UE alpha away from gNB (increase path loss gradually)

```bash
for ploss in 20 25 30 35 40 45 50; do
  echo "channelmod modify 0 ploss $ploss" | nc 127.0.0.1 9092
  sleep 3
done
```

### Increase noise to simulate interference

```bash
echo "channelmod modify 0 noise_power_dB 0" | nc 127.0.0.1 9092
```

### Reset to defaults without restarting

```bash
echo "channelmod modify 0 ploss 20" | nc 127.0.0.1 9092
echo "channelmod modify 0 noise_power_dB -6" | nc 127.0.0.1 9092
echo "channelmod modify 0 forgetf 0.99" | nc 127.0.0.1 9092
```

### Toggle between AWGN (synthetic) and Rayleigh1 (realistic fading)

```bash
# Synthetic — constant signal + noise, no fading
echo "channelmod modify 0 riceanf 0" | nc 127.0.0.1 9092

# NOTE: Changing model TYPE at runtime requires 'rfsimu setmodel' command
# This is available on the gNB telnet only (port 9091)
```

---

## Files

| File | Purpose |
|------|---------|
| `/home/innocentmsc/sar_sim/oai_configs/gnb.sa.band78.rfsim.conf` | gNB configuration with channel models |
| `/home/innocentmsc/sar_sim/oai_configs/nrue_alpha.conf` | UE alpha configuration |
| `/home/innocentmsc/sar_sim/oai_configs/nrue_bravo.conf` | UE bravo configuration |
| `/home/innocentmsc/sar_sim/oai_configs/channelmod_sar.conf` | Standalone channel model reference |
| `*.bak` | Original (unmodified) backups |

## Build Note

The EPA, EVA, ETU, TDL_A–TDL_E channel models segfault on the current binary. If you need these models (which give frequency-selective fading with standardized delay profiles), rebuild OAI:

```bash
cd /home/innocentmsc/openairinterface5g/cmake_targets
./build_oai --nr-ue --nr-gnb
```

This will recompile both `nr-softmodem` and `nr-uesoftmodem` and may resolve the crash.
