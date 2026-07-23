# Worlds Runbook — running & troubleshooting the SAR sim

How to launch the SAR A2G stack in a chosen Gazebo world (open field or urban
NLoS), how the world selection propagates to the radio, and how to diagnose the
common failures. This is the operational companion to `SYSTEM_ARCHITECTURE.md`
and `A2G_CHANNEL.md`.

---

## 0. Easiest path — the control centre (`sar.py`)

If you don't want to remember env vars and flags, run the interactive
control centre — it wraps everything in this runbook (pick a terrain, start
the stack, run patrols, stream, fly the UAV, check status, stop):

```bash
./sar.py                 # interactive menu
```

It also takes direct commands for scripting:

```bash
./sar.py start   --world sar_forest
./sar.py patrol  --mode both --duration 1800 --max-radius 110
./sar.py stream  --kind qos --robot both --duration 120
./sar.py fly     --nomadic
./sar.py collect --world sar_urban --duration 1800   # QoS stream + patrol
./sar.py status
./sar.py stop
```

The rest of this document explains what each of those does under the hood.

---

## 1. TL;DR — pick a world and launch

The launcher `start_full_stack.sh` reads **one** environment variable,
`SAR_WORLD`, to choose the world. It defaults to `sar_open_field`.

```bash
# Open field (default) — flat ground, line-of-sight everywhere
./start_full_stack.sh

# Urban — NLoS building world; buildings also attenuate the 5G link
SAR_WORLD=sar_urban ./start_full_stack.sh
```

`SAR_WORLD` is a **name**, not a path — it maps to `worlds/<SAR_WORLD>.sdf`.
Five worlds ship today, each with a matching channel model in
`channel_bridge.py`:

| `SAR_WORLD`      | Channel effect | Coverage lever | Generator |
|------------------|----------------|----------------|-----------|
| `sar_open_field` | Flat, probabilistic LoS (default) | elevation angle | — (hand-written) |
| `sar_urban`      | Building NLoS (deterministic ray block) | climb over rooftops | `make_urban_world.py` |
| `sar_forest`     | Foliage attenuation, graded by canopy depth (Weissberger) | climb / reach a clearing | `make_forest_world.py` |
| `sar_hills`      | Terrain shadowing (ITU-R P.526 knife-edge diffraction) | climb above the ridge line | `make_hills_world.py` |
| `sar_indoor`     | Enterable building; 3GPP O2I penetration + depth loss | robot **exits** the building (not UAV altitude) | `make_indoor_world.py` |

Run any of them the same way, e.g. `SAR_WORLD=sar_forest ./start_full_stack.sh`.
The generated worlds share their obstacle geometry between the SDF and the
radio (one Python module per world), so visuals and channel always agree; if
a world's SDF is missing the launcher regenerates it automatically.

**`sar_indoor` note:** the building is rendered *open-top* so you can watch
the robot search from above, but the channel model still applies a roof-
penetration loss as if it were enclosed — so the link degrades when the robot
enters and worsens the deeper it goes, and recovers when it drives back out.
Best viewed via the robot's onboard camera (`stream_camera.sh`).

To go back to open field after using urban, `unset SAR_WORLD` (or set it back
explicitly). The variable only lives for the shell you set it in — a plain
`./start_full_stack.sh` in a fresh shell is always open field.

> **Prerequisite reminder:** the stack needs `SAR_SUDO_PASS` exported (or
> passwordless sudo) — see `start_full_stack.sh:22`. Unrelated to world choice
> but it's the first thing that stops a launch.

---

## 2. What `SAR_WORLD` actually changes

Setting one variable changes **two** subsystems, which is the whole point —
the visuals and the radio stay consistent:

1. **Gazebo** loads `worlds/$SAR_WORLD.sdf` as its server world
   (`start_full_stack.sh:129` — `gz sim -r -s .../worlds/$WORLD.sdf`).
2. **`channel_bridge.py`** re-reads the same exported `SAR_WORLD` and switches
   its channel model. In `sar_urban` it imports the building geometry from
   `worlds/urban_buildings.py` and runs a line-of-sight check so buildings
   between a robot and the UAV add NLoS attenuation — not just decoration.

The single source of truth for building geometry is
**`worlds/urban_buildings.py`**. It is imported by *both*:

- `worlds/make_urban_world.py` → generates the SDF visuals, and
- `channel_bridge.py` → the NLoS line-of-sight check for the radio.

So the buildings you see and the buildings that block the signal are the same
list. **Never hand-edit `worlds/sar_urban.sdf`** — it's auto-generated and your
edits will be silently overwritten (see §4).

Every generated world follows this one-module-per-world pattern (edit the
module, re-run the generator — never the `.sdf`):

| World | Geometry module (source of truth) | Channel model |
|-------|-----------------------------------|---------------|
| `sar_urban`  | `worlds/urban_buildings.py` | deterministic LoS ray block |
| `sar_forest` | `worlds/forest_trees.py`    | Weissberger foliage depth → excess dB |
| `sar_hills`  | `worlds/terrain_hills.py`   | ITU-R P.526 knife-edge diffraction |
| `sar_indoor` | `worlds/indoor_building.py` | 3GPP O2I penetration + 0.5 dB/m depth |

Each module exposes `sdf_models()` (for its generator) and the geometry the
channel bridge needs; the bridge's `world_channel(uav, ue)` dispatch picks the
right one from `SAR_WORLD`. To retune a terrain's *physics*, edit the loss
function in its module (e.g. `foliage_loss_db`, `terrain_diffraction_db`,
`indoor_loss`) — no SDF regeneration needed for pure channel-model changes.

---

## 3. Editing / regenerating the urban world

The urban SDF is generated, not authored. To change the city layout:

```bash
# 1. Edit the building list (positions/sizes) — the shared source of truth
$EDITOR worlds/urban_buildings.py

# 2. Regenerate the SDF from it
python3 worlds/make_urban_world.py
# -> "wrote .../worlds/sar_urban.sdf (N buildings)"

# 3. Relaunch
SAR_WORLD=sar_urban ./start_full_stack.sh
```

The generator reuses the open-field preamble (physics, sensors, GPS/mag anchor,
spherical coordinates) so `sar_urban` is physically identical to
`sar_open_field` apart from the added building blocks. If you change the
open-field world's preamble, re-run the generator so urban picks it up.

**Auto-regeneration:** if `worlds/sar_urban.sdf` is *missing* when you launch
with `SAR_WORLD=sar_urban`, the launcher regenerates it for you
(`start_full_stack.sh:43-45`). It does **not** regenerate if the file exists but
is stale — after editing `urban_buildings.py` you must run the generator
yourself.

Robot start poses are placed in the clear zone inside the inner building ring
(`|x|,|y| < ~34 m`): UAV at `0,0,40`, alpha at `15,0`, bravo at `-15,0`.

---

## 4. Adding a brand-new world

1. Create `worlds/<yourworld>.sdf`. Easiest path: copy `sar_open_field.sdf` and
   rename the `<world name="...">` to match the filename stem, then add your
   models. Keep the `<physics>`, `<spherical_coordinates>`, `<magnetic_field>`
   and the sensor/physics `<plugin>` blocks — dropping them breaks GPS, compass
   and PX4 preflight.
2. Launch with `SAR_WORLD=<yourworld> ./start_full_stack.sh`.
3. If your world needs a matching channel model, add a branch for its name in
   `channel_bridge.py` (otherwise the bridge falls back to the open-field/LoS
   model regardless of visuals — the radio won't know your obstacles exist).

The `<world name>` inside the SDF **must** equal `SAR_WORLD`: the launcher calls
gz services on `/world/$WORLD/...` (create, set_pose). A mismatch makes every
spawn and teleport silently time out.

---

## 5. Verifying a launch end-to-end

After `start_full_stack.sh` prints `FULL STACK IS UP`, confirm each layer:

```bash
# Gazebo alive and running the right world
gz topic -l | grep /clock                 # clock present => server up
gz model --list                           # sar_uav, sar_ground_alpha/bravo present

# Channel bridge tracking distance -> attenuation
tail -f /tmp/ch_bridge.log                 # look for "-> ploss" lines

# Teleport a robot and watch ploss move (the canonical integration check)
gz service -s /world/$SAR_WORLD/set_pose \
  --reqtype gz.msgs.Pose --reptype gz.msgs.Boolean --timeout 2000 \
  --req 'name: "sar_ground_alpha" position: {x: 100, y: 0, z: 0.1}'
# -> ch_bridge.log ploss should climb toward the ~100m value

# Over-air connectivity (robots live in network namespaces)
sudo ip netns exec robot_alpha ping 10.45.0.1      # uplink
ping 10.45.0.2                                      # downlink / C2 path

# Confirm traffic is really modulated on the air (not looped back)
tail ~/openairinterface5g/nrMAC_stats.log          # live per-UE dlsch/ulsch
```

Rough distance → attenuation map (alpha UL, baseline 20, h=40):
`10m→~14dB, 50m→~17dB, 100m→~31dB, 150m→~38dB`. In `sar_urban`, expect
*higher* attenuation at the same distance whenever a building sits on the
LoS path — that is the world doing its job.

Open the GUI to see it: `./view_gui.sh`.

---

## 6. Troubleshooting

### Launch / world selection

| Symptom | Cause & fix |
|---|---|
| Launch aborts: `set SAR_SUDO_PASS ...` | Export your sudo password: `export SAR_SUDO_PASS='...'` or configure passwordless sudo. |
| Urban launch but world looks like open field | `SAR_WORLD` not exported into the launcher's shell. Use `SAR_WORLD=sar_urban ./start_full_stack.sh` (same line) — not two separate commands. |
| Edited `urban_buildings.py` but city unchanged | The SDF is cached. Run `python3 worlds/make_urban_world.py`, then relaunch. Auto-regen only fires when the SDF is *missing*. |
| Every spawn/teleport times out | `<world name>` inside the SDF ≠ `SAR_WORLD`. gz services target `/world/$SAR_WORLD/...`; make them match. |
| `Gazebo failed to start — check /tmp/gz_sim.log` | Read `/tmp/gz_sim.log`. Common: `Spherical coordinates not set` (you dropped the `<spherical_coordinates>` block), or a bad `<uri>model://...>` the resource path can't resolve. |

### Robots / models

| Symptom | Cause & fix |
|---|---|
| `<model> failed to spawn` | Model URI not on `GZ_SIM_RESOURCE_PATH`. The launcher exports it on the **gz sim server** (`start_full_stack.sh:118`). **Never hand-start Gazebo** — a manually started server lacks this path and every robot include fails. Let the launcher own Gazebo. |
| Robots invisible in GUI but poses update | Known Harmonic quirk: SceneBroadcaster drops merge-included EntityFactory spawns from the scene graph. Physics/pose still work. Robots are includes in the world SDF for exactly this reason. |
| A ground robot slowly drifts / wanders when idle | Dynamic burgers creep ~0.5 m/min via DiffDrive. Overnight this drifted alpha 780 m and killed its UE. Use teleport (`set_pose`) between waypoints, or keep patrols bounded. |

### Radio / channel bridge

| Symptom | Cause & fix |
|---|---|
| `ch_bridge.log` never prints `ploss` | Bridge waiting for first pose update, OR two Gazebo instances / wrong model names froze the pipeline. `channel_bridge.py` keys on the exact names `sar_uav`, `sar_ground_alpha`, `sar_ground_bravo`. |
| Link dies and never recovers after a big move | **45 dB absolute ploss = RLF cliff** (RNTI churn, BLER 0.86). RLF is sticky — the UE may never re-attach. Fix: `bash restart_ran.sh`. Keep patrols inside `--max-radius=110`. |
| High RTT (~1 s) at long range | Normal HARQ behavior at 150 m, not a failure. After a large teleport give the EMA ~15–20 s before judging link health. |
| Downlink / C2 ping works but seems too good | Robots' UE TUNs live in netns `robot_alpha`/`robot_bravo` so both directions cross the air. `ping -I oaitun_ue1 ...` from the root ns no longer works (interface moved into the netns). Use the netns commands in §5. |

### Housekeeping

| Symptom | Cause & fix |
|---|---|
| Disk fills overnight, bridge/patrol crash | OAI logs grow ~5 GB/h (gNB) + ~2 GB/h per UE. `scripts/log_janitor.sh` (auto-launched) caps each at 5 GB. If it died, relaunch the stack. |
| `pkill -f "gz sim"` kills my own shell | Self-match quirk: the pattern matches the wrapping shell's own cmdline. Use a bracket pattern (`"gz [s]im"`) and put the kill and the relaunch in **separate** commands. |

### Logs at a glance

```
/tmp/gz_sim.log        Gazebo server (world load, spawn, sensor errors)
/tmp/ch_bridge.log     channel bridge (ploss tracking, telnet to OAI)
/tmp/gz_viz.log        situational-map visualiser
~/openairinterface5g/nrMAC_stats.log   live per-UE goodput (proof of over-air traffic)
```

---

## 7. Running patrols

A patrol drives the ground robots along a path, fires periodic iperf bursts, and
logs distance / ploss / throughput to a timestamped CSV. This is how the ML
training datasets are produced.

**The stack must already be up** (`start_full_stack.sh` running) before you
start a patrol — the patrol only moves robots and measures; it does not launch
Gazebo or the radio. The patrol inherits whatever world the stack was launched
in: it reads `SAR_WORLD`, or auto-detects the running gz world if the variable
isn't set. So to patrol the urban world, launch the stack with
`SAR_WORLD=sar_urban` first, then run any patrol below.

Entry point: `scripts/auto_patrol.py`. Output: `logs/patrol_<YYYYMMDD_HHMMSS>.csv`.

### Modes

| Command | Mode | What it does |
|---|---|---|
| `python3 scripts/auto_patrol.py` | linear (default) | Single robot (alpha), out-and-back sweep 10→200 m, 180 s |
| `python3 scripts/auto_patrol.py --random` | random | Single robot, 2 m random walk |
| `python3 scripts/auto_patrol.py --both` | both | Alpha + bravo, independent 2 m random walks (teleport steps) |
| `python3 scripts/auto_patrol.py --free` | free | Alpha + bravo roam continuously (smooth headings via cmd_vel, no teleport); rows tagged at the window midpoint |
| `python3 scripts/auto_patrol.py --circle` | circle | Robots follow a circular sweep around the origin |
| `python3 scripts/auto_patrol.py --search` | search | Structured search pattern |
| `python3 scripts/auto_patrol.py --vary-uav` | nomadic | Flies the UAV through a 9-waypoint x/y/z schedule; the robots' annulus recenters on the UAV and follows `safe_radius(alt)`; an emergency teleport guard rescues robots stranded by UAV hops |

### Parameters (combine with any mode)

| Flag | Meaning |
|---|---|
| `--duration=N` | Run length in seconds (default 180) |
| `--step=N` | Metres per waypoint (teleport modes) |
| `--speed=N` | Robot speed in m/s (`--free` continuous roam) |
| `--reach=N` | Waypoint reach/arrival tolerance |
| `--lane=N` | Lane spacing for sweep patterns |
| `--max-radius=N` | Outer patrol radius cap (m) |
| `--min-radius=N` | Inner patrol radius (m) |

### Recommended / known-good invocations

```bash
# Quick smoke run — single robot, 5 minutes
python3 scripts/auto_patrol.py --duration=300

# Continuous roam, both robots, gentle speed
python3 scripts/auto_patrol.py --free --speed=1.5 --duration=1800

# Outage-free 90-min dataset (keep inside coverage edge)
python3 scripts/auto_patrol.py --both --duration=5400 --max-radius=110

# Nomadic flagship-style run (UAV varies altitude 20–60 m, robots follow)
python3 scripts/auto_patrol.py --vary-uav --duration=10800
```

### Coverage limits — read before choosing a radius

These come from measured campaigns and directly determine whether a run stays
outage-free:

- **Coverage edge (h=40):** alpha dies ~135 m, bravo ~130 m. `--max-radius=110`
  keeps 90-min runs outage-free.
- **Edge moves with altitude:** `safe_radius()` gives ≈ 86 m @ 20 m /
  117 m @ 40 m / 149 m @ 60 m. `--vary-uav` applies this automatically.
- **45 dB absolute ploss = RLF cliff** — the UE churns RNTIs (BLER 0.86) and
  RLF is *sticky*; a robot pushed past the edge may never re-attach. Recover
  with `bash restart_ran.sh` before the next run.
- Give the channel EMA **~15–20 s** to settle after a large move before trusting
  a row.

### After a run — analyse it

```bash
python3 scripts/analyze_patrol.py logs/patrol_<YYYYMMDD_HHMMSS>.csv
# -> summary report + 4 figures per run
```

**Dataset hygiene:** on a live link, rows where `tcp==0 AND ping_loss==0` are
iperf server-busy artifacts, not real outages — filter them out. (Fixed in
`auto_patrol.py` with retry+settle gaps; older CSVs may still contain them.)

### Running multiple patrols

`auto_patrol.py` is a single foreground process that writes one timestamped CSV
per invocation, so "all the patrols" means running them **one after another**,
not concurrently — two patrols fighting over the same robots and iperf servers
will corrupt each other's data. To batch several modes back-to-back:

```bash
for mode in "--both" "--free --speed=1.5" "--circle"; do
  python3 scripts/auto_patrol.py $mode --duration=1800 --max-radius=110
  sleep 20            # let the link settle between runs
done
```

Each iteration produces its own `logs/patrol_*.csv`. Keep the stack up the whole
time; only relaunch the RAN (`restart_ran.sh`) if a run hit the RLF cliff.

---

## 8. Camera & video streaming

These stream a live feed from a robot **over the real 5G air interface** — the
encoder/sender runs inside the robot's network namespace, so the same channel
model that shapes iperf shapes the video. Drive the robot NLoS behind a building
(easiest in `sar_urban`) and you watch the feed go blocky, freeze, or its QoS
numbers collapse. That is the whole demo.

**Common requirements for all three:**
- The stack must already be up (`start_full_stack.sh` running).
- `export SAR_SUDO_PASS='...'` — the sender/encoder runs in a netns (needs root).
- The viewer auto-launches if `$DISPLAY` is set; otherwise the exact viewer
  command is printed so you can run it on a machine with a screen.

There are three scripts, for three different purposes:

| Script | What it sends | Use it to… | UDP ports (alpha/bravo) |
|---|---|---|---|
| `stream_camera.sh` | The robot's **actual camera POV** (or thermal sensor) rendered by Gazebo | Watch what the robot really "sees" degrade over 5G | 5604 / 5605 |
| `stream_video.sh` | A **synthetic H.264 test pattern** (moving ball + overlay), camera stand-in | Watchable video demo when no camera sensor is needed | 5602 / 5603 |
| `stream_demo.sh` | **Synthetic packets + per-frame telemetry JSON** | Measure application QoS (goodput, frame-delivery ratio, loss, latency, jitter) → CSV | 5600 / 5601 |

### Which one for your research?

**For quantitative results → `stream_demo.sh`.** It is the only one that emits
measured data: per-second application QoS (goodput, frame-delivery ratio, packet
loss, latency, jitter) written to `logs/stream_<robot>_<ts>.csv`. Those CSVs are
reproducible, plottable, and sit alongside the patrol datasets — they turn
"the link degrades NLoS" into numbers you can put in the paper and correlate
with distance/ploss from the channel bridge. Run it **alongside a patrol** so
each QoS row lines up with a known robot position and attenuation.

**`stream_camera.sh` / `stream_video.sh` are qualitative** — a watchable feed
that visibly stutters and freezes. Invaluable for a demo, a talk, or a figure/
screen-capture that *shows* the impairment, but they don't log metrics, so they
are illustration, not evidence. Between the two, prefer `stream_camera.sh`: it
carries the robot's real simulated POV (and a thermal/victim-heat view), which
is the more compelling and more defensible "this is what the operator sees"
artifact than the synthetic test pattern in `stream_video.sh`.

> **Bottom line:** collect data with `stream_demo.sh` (+ a patrol); demonstrate
> it with `stream_camera.sh`. `stream_video.sh` is a fallback demo for when no
> camera sensor is loaded.

### stream_camera.sh — real robot POV

```bash
export SAR_SUDO_PASS='...'
# ./stream_camera.sh [alpha|bravo] [duration_s] [bitrate_kbps] [rgb|thermal]
./stream_camera.sh bravo 120 1500 rgb        # RGB camera POV
./stream_camera.sh bravo 120 1500 thermal    # thermal / victim-heat view
```

- Reads the Gazebo camera image topic via `scripts/gz_camera_bridge.py`, encodes
  H.264 inside the robot netns, sends RTP/UDP over 5G to the host viewer.
- **The camera-equipped model must be loaded**, which means the stack must have
  been (re)started *after* the camera was added to the model SDF. Verify the
  topic exists first:
  ```bash
  gz topic -l | grep /sar/         # or the full model camera topic path
  ```
  If it's missing, the script warns you to relaunch:
  `SAR_WORLD=<world> ./start_full_stack.sh`.
- **World note:** this script defaults its topic scope to `SAR_WORLD` and falls
  back to **`sar_urban`** if the variable isn't set (unlike the launcher, which
  defaults to open field). If you streamed in open field, export
  `SAR_WORLD=sar_open_field` in the streaming shell so the topic path matches, or
  the camera topic lookup will fail.

### stream_video.sh — watchable synthetic video

```bash
export SAR_SUDO_PASS='...'
# ./stream_video.sh [alpha|bravo] [duration_s] [bitrate_kbps]
./stream_video.sh bravo 120 1500
```

Sends a GStreamer test pattern (a moving ball) with a robot-ID + clock overlay —
a stand-in for a camera when you just want a watchable flow that visibly
degrades NLoS. No camera sensor required.

### stream_demo.sh — QoS measurement

```bash
export SAR_SUDO_PASS='...'
# ./stream_demo.sh [alpha|bravo|both] [duration_s] [bitrate_mbps]
./stream_demo.sh both 120 3
```

Streams constant-bitrate UDP video with a JSON sensor blob per frame; the
host-side collector prints live application QoS and writes per-second metrics to
`logs/stream_<robot>_<ts>.csv` for plotting. Best run **alongside a patrol** in
another shell so you see QoS track the robot going LoS ↔ NLoS.

### Putting it together (the money shot)

```bash
# Shell 1 — bring the urban world up
SAR_WORLD=sar_urban ./start_full_stack.sh

# Shell 2 — watch the robot's real camera feed over 5G
export SAR_WORLD=sar_urban SAR_SUDO_PASS='...'
./stream_camera.sh bravo 300 1500 rgb

# Shell 3 — drive bravo behind a building and back
python3 scripts/auto_patrol.py --both --duration=300 --max-radius=110
```

When bravo passes behind a building the NLoS attenuation spikes, and the camera
window in shell 2 stutters/freezes in real time.

### Recommended workflow: smoke test, then collect

**1. Smoke test — "does the whole chain work?" (run this first).** A 60 s QoS
stream prints live numbers immediately (no display needed); if goodput /
frame-delivery / latency tick per second for both robots, the entire
Gazebo → 5G → host path is healthy.

```bash
export SAR_WORLD=sar_urban SAR_SUDO_PASS='...'
./stream_demo.sh both 60 3
```

(For a *visual* check instead, and you have a screen:
`./stream_camera.sh bravo 60 1500 rgb`.)

**2. Data collection — the correlated research run.** Two shells: QoS stream +
a patrol that drives the robots LoS ↔ NLoS, so each QoS row aligns with a known
position and attenuation.

```bash
# Shell A — QoS collection (writes logs/stream_<robot>_<ts>.csv)
export SAR_WORLD=sar_urban SAR_SUDO_PASS='...'
./stream_demo.sh both 1800 3

# Shell B — move the robots (writes logs/patrol_<ts>.csv)
python3 scripts/auto_patrol.py --both --duration=1800 --max-radius=110
```

Time-align the two CSVs afterward to get QoS vs. distance/ploss — the dataset
that's actually useful for the paper. Match the durations so both cover the
same window.

> Note: `SAR_WORLD` must be exported in each streaming/patrol shell to match the
> world the stack was launched in — the camera topic path and channel model
> depend on it. If the stack is already up (check `gz model --list`), you do not
> relaunch it; just export the variable and run.

### Streaming troubleshooting

| Symptom | Cause & fix |
|---|---|
| `ERROR: ... need root` | `export SAR_SUDO_PASS='...'` (or passwordless sudo). Sender runs in a netns. |
| Camera script: topic-not-found warning | Model has no camera sensor loaded, or wrong world scope. Relaunch the stack with the camera-equipped model, and match `SAR_WORLD` between the launcher and the streaming shell. |
| No viewer window | `$DISPLAY` unset (headless). The script prints the viewer command — run it on a machine with a screen, pointed at the host's data-net IP + port from the table. |
| Feed never degrades | Robot stayed LoS. Move it NLoS (a patrol, or a `set_pose` teleport behind a building in `sar_urban`). In open field there are no obstacles to block it. |
| Port already in use / stale sender | A previous run didn't clean up. Kill leftover `udpsink`/sender processes in the netns, or just relaunch the stack. |

---

## 9. Quick reference

```bash
# Launch, urban world
SAR_WORLD=sar_urban ./start_full_stack.sh

# Launch, open field (default)
./start_full_stack.sh

# Regenerate urban SDF after editing the building list
python3 worlds/make_urban_world.py

# Open the GUI
./view_gui.sh

# Run a patrol (stack must already be up; inherits SAR_WORLD)
python3 scripts/auto_patrol.py --both --duration=1800 --max-radius=110

# Analyse a patrol CSV
python3 scripts/analyze_patrol.py logs/patrol_<YYYYMMDD_HHMMSS>.csv

# Stream a robot's real camera POV over 5G (needs SAR_SUDO_PASS)
./stream_camera.sh bravo 120 1500 rgb

# Measure streaming QoS over 5G -> CSV
./stream_demo.sh both 120 3

# Recover a stuck UE after RLF
bash restart_ran.sh

# Teleport a robot (world name must match $SAR_WORLD)
gz service -s /world/$SAR_WORLD/set_pose \
  --reqtype gz.msgs.Pose --reptype gz.msgs.Boolean --timeout 2000 \
  --req 'name: "sar_ground_alpha" position: {x: 100, y: 0, z: 0.1}'
```
