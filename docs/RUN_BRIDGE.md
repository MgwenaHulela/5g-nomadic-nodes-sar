# Running the A2G Channel Bridge with Gazebo

## Overview

```
Gazebo (UAV + UGVs) → gz-transport pose topics → channel_bridge.py → OAI telnet port
                                                       ↓
                                                 ploss (path loss)
                                                 offset (delay)
```

The bridge reads `/model/sar_uav/pose`, `/model/sar_ground_alpha/pose`,
`/model/sar_ground_bravo/pose` from Gazebo, computes A2G path loss from
UAV-UGV distance and elevation, then pushes `ploss` and `offset` changes
to the OAI gNB and UE telnet ports in real time.

## Prerequisites

1. All OAI processes running (gNB + both UEs)
2. `./scripts/setup_over_air_test.sh setup` applied
3. Gazebo world at `~/sar_sim/worlds/sar_open_field.sdf`
4. PX4-Autopilot available at `~/PX4-Autopilot`

## Step-by-Step

### 1. Start Gazebo + UAV + UGVs

Use the phase 1 launcher:

```bash
~/sar_sim/worlds/launch_phase1.sh ~/PX4-Autopilot
```

Or manually:

```bash
# Start Gazebo headless
export GZ_SIM_RESOURCE_PATH="$HOME/PX4-Autopilot/Tools/simulation/gz/models:$HOME/PX4-Autopilot/Tools/simulation/gz/worlds"
gz sim -s -r -v 1 ~/sar_sim/worlds/sar_open_field.sdf &
sleep 10

# Spawn UAV named "sar_uav"
gz service -s /world/sar_open_field/create \
  --reqtype gz.msgs.EntityFactory \
  --reptype gz.msgs.Boolean \
  --timeout 5000 \
  --req "sdf_filename: \"$HOME/PX4-Autopilot/Tools/simulation/gz/models/x500/model.sdf\", name: \"sar_uav\", pose: {position: {x: 0, y: 0, z: 0.3}}"
sleep 3

# Verify pose topic exists
gz topic -l | grep model/sar_uav/pose
```

> **Note:** The ground robots (`sar_ground_alpha`, `sar_ground_bravo`) must also
> be spawned with matching names. Use the experiment scripts or spawn them
> via `gz service`:
>
> ```bash
> gz service -s /world/sar_open_field/create \
>   --reqtype gz.msgs.EntityFactory \
>   --reptype gz.msgs.Boolean \
>   --timeout 2000 \
>   --req "sdf_filename: \"path/to/ground_robot/model.sdf\", name: \"sar_ground_alpha\", pose: {position: {x: 0, y: 0, z: 0.1}}"
> ```

### 2. Verify Pose Topics

```bash
gz topic -e /model/sar_uav/pose -n 3
gz topic -e /model/sar_ground_alpha/pose -n 3
gz topic -e /model/sar_ground_bravo/pose -n 3
```

Each should print position (x, y, z) updates every few ms.

### 3. Start the Channel Bridge

```bash
python3 ~/sar_sim/channel_bridge.py
```

With optional arguments:

```bash
python3 ~/sar_sim/channel_bridge.py --dry-run        # log only, no telnet
python3 ~/sar_sim/channel_bridge.py --rate 1.0        # update every 1s (default 0.5s)
python3 ~/sar_sim/channel_bridge.py --ref-distance 50 # reference horiz distance (m)
```

The bridge will:
- Subscribe to all three pose topics
- When UAV and UGV poses arrive, compute ploss + offset
- Send `channelmod modify 0/1/2 ploss <dB>` to gNB port 9091
- Send `channelmod modify 0 offset <samples>` to gNB port 9091
- Log every iteration to `~/sar_sim/logs/channel_bridge_log.csv`

### 4. Control UAV Position

Use the experiment scripts to move the UAV around and watch ploss change:

```bash
# Move UAV to (0, 0, 30m)
gz service -s /world/sar_open_field/set_pose \
  --reqtype gz.msgs.Pose \
  --reptype gz.msgs.Boolean \
  --timeout 2000 \
  --req "name: \"sar_uav\" position: {x: 0, y: 0, z: 30}"

# Move ground_alpha to various distances
gz service -s /world/sar_open_field/set_pose \
  --reqtype gz.msgs.Pose \
  --reptype gz.msgs.Boolean \
  --timeout 2000 \
  --req "name: \"sar_ground_alpha\" position: {x: 100, y: 0, z: 0.1}"
```

Or run the automated experiment:

```bash
~/sar_sim/scripts/exp1_distance.sh    # vary alpha distance 10→150m
~/sar_sim/scripts/exp2_altitude.sh    # vary UAV height
```

### 5. Monitor the Effect

The bridge prints each change:

```
  gnb_ue0_alpha_ul (:9091:0) -> ploss 32.45dB (d=85.3m, v=2.1m/s)
  gnb_ue1_bravo_ul (:9091:1) -> ploss 38.12dB (d=120.7m, v=0.3m/s)
```

Watch throughput change in real time with iperf:

```bash
iperf3 -c 10.0.0.200 --bind-dev oaitun_ue1 -t 30 -i 2
```

## Architecture Notes

```
bridge              Gazebo pose topics          OAI telnet ports
channel_bridge.py ─── /model/*/pose ─────────── 127.0.0.1:9091  (gNB)
     │                                          127.0.0.1:9092  (UE alpha)
     │                                          127.0.0.1:9093  (UE bravo)
     │
     ├─ EMA-smooths ploss changes
     ├─ only sends when |Δploss| ≥ 0.5 dB
     ├─ sends offset proportional to distance
     └─ logs all params to CSV
```

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| Bridge hangs at "waiting for pose" | Gazebo not running, or models not spawned with matching names |
| Bridge prints telnet send failures | OAI processes not running on the expected ports |
| No throughput change | Wrong model index — alpha UL is model 1 on gNB |
| Link drops at high ploss | Restart UEs: `pkill -9 nr-uesoftmodem` then restart |
| Alpha fails RRC | Start alpha first, wait 30s, then bravo |
