# Phase 2 — TurtleBot3 Ground Robots in the Open Field World (COMPLETE)

**Status: confirmed working.** Two TurtleBot3 Burger ground robots
(`sar_ground_alpha`, `sar_ground_bravo`) patrol the same `sar_open_field`
world as Phase 1's UAV, driven by real force/torque physics (not
teleportation), with pose data verified flowing on `/model/<name>/pose`
for all three bodies simultaneously.

Verified 2026-07-03: alpha tracked a clean lawnmower leg from (30,-40)
toward (100,-40) with a small overshoot to ~107 before curving into the
turn — genuine inertial behavior, not scripted motion. Bravo showed a
brief settling wobble at spawn (see Known Issues) then tracked cleanly
from (-60,50) toward (-57,47), correct direction for its diamond
pattern's second waypoint.

## Files

| File | Purpose |
|---|---|
| `install_turtlebot3.sh` | Clones official ROBOTIS-GIT `humble` branch. **The colcon build step fails and can be ignored** — see Known Issues #1 |
| `apply_burger_patch.sh` | Adds PosePublisher to the stock burger model (only needed if spawning it unpatrolled) |
| `create_patrol_variants.sh` | Makes `turtlebot3_burger_alpha`/`_bravo` copies, each with PosePublisher + TrajectoryFollower baked in, distinct waypoints |
| `spawn_ground_robot.sh` | Spawns a named robot into the already-running world from an explicit model.sdf path |
| `test_pose_subscribe.py` | Diagnostic: takes model name as CLI arg, works for any of the three bodies |

## Sequence

With Phase 1's world already running (`launch_phase1.sh`):

```bash
cd ~/sar_sim/worlds
chmod +x install_turtlebot3.sh apply_burger_patch.sh create_patrol_variants.sh spawn_ground_robot.sh

./install_turtlebot3.sh ~/turtlebot3_ws   # colcon build will fail — ignore, see below
./create_patrol_variants.sh ~/turtlebot3_ws

./spawn_ground_robot.sh sar_ground_alpha 20 -40 ~/turtlebot3_ws \
  ~/turtlebot3_ws/src/turtlebot3_simulations/turtlebot3_gazebo/models/turtlebot3_burger_alpha/model.sdf

./spawn_ground_robot.sh sar_ground_bravo -60 50 ~/turtlebot3_ws \
  ~/turtlebot3_ws/src/turtlebot3_simulations/turtlebot3_gazebo/models/turtlebot3_burger_bravo/model.sdf

python3 test_pose_subscribe.py sar_ground_alpha --seconds 30
python3 test_pose_subscribe.py sar_ground_bravo --seconds 30
```

`turtlebot3_ws` is a sibling directory to `sar_sim`, not nested inside
it — never gets swept into this repo, no gitignore entry needed.

## Fixes / lessons found this phase

1. **The official `humble` branch of `turtlebot3_simulations` is still
   Gazebo-Classic-dependent at the build level.** Its `turtlebot3_gazebo`
   ROS package's `CMakeLists.txt` hard-requires `find_package(gazebo)`
   (Classic), and `rosdep install` tries to satisfy that via
   `ros-humble-gazebo-ros-pkgs`, which **directly conflicts** with
   `gz-tools2` (Harmonic) already on the system — installing it risks
   breaking the working Phase 1 UAV setup. **Don't force this through.**
   We don't need it: Gazebo Harmonic only needs the raw `model.sdf` file
   to spawn and simulate a model — no ROS2/colcon build required at all.
   `install_turtlebot3.sh`'s build failure is expected and safe to
   ignore; only the `git clone` step actually matters.

2. **Entity name collisions block respawns silently at the log level.**
   `[Wrn] Entity named [X] already exists and [allow_renaming] is false.
   Entity not spawned.` only shows in `gz_sim.log`, not in the spawn
   command's own stdout (which reports `data: true` regardless). If a
   respawn produces zero pose data, check for this line before assuming
   a plugin/config problem. Fix: remove the stale entity first —
   ```bash
   gz service -s /world/sar_open_field/remove \
     --reqtype gz.msgs.Entity --reptype gz.msgs.Boolean --timeout 3000 \
     --req 'name: "sar_ground_alpha", type: MODEL'
   ```

3. **Session-wide transport can degrade after enough churn.** After
   several spawn/remove/respawn cycles on a long-running world (on top
   of Phase 1's multi-hour uptime), we hit a state where `gz topic -i`
   showed a live publisher with correct message type, yet `gz topic -e`
   and direct `gz.transport13` subscription both hung indefinitely —
   for EVERY topic in the session, including the previously-solid
   `sar_uav` one. Not root-caused; the working fix was a full clean
   restart (`pkill -9 -f px4`, `pkill -9 -f "gz sim"`, relaunch). If
   pose data goes silent for a topic that was previously working, and a
   fresh entity also fails, suspect this before re-debugging plugin
   config — check `gz topic -e -t /model/sar_uav/pose -n 1` (or whatever
   topic was last known-good) as a canary before spending time
   elsewhere.

4. **TrajectoryFollower's `<link_name>` must be a link with actual
   mass**, not a massless reference frame. TurtleBot3 Burger's
   `base_footprint` is a zero-mass anchor (REP-120 convention, connected
   via a `fixed` joint) — the real body with inertia is `base_link`
   (0.826kg). Force/torque applied to a massless link would do nothing
   or misbehave. Check with:
   ```bash
   grep -n '<link name=\|<mass>' model.sdf
   ```

5. **Waypoints are static SDF config, not a runtime parameter** — two
   robots needing different patrol patterns need genuinely separate
   `model.sdf` files, not shared config with per-instance overrides.
   `create_patrol_variants.sh` handles this by copying the stock model
   directory once per robot and patching each independently.

6. **`force: 15, torque: 15`** (scaled from the official demo's 1kg box
   at force=10 to the burger's 0.826kg `base_link`) produced smooth,
   purposeful motion with reasonable inertial overshoot on corners — no
   tuning needed so far, but flagged here in case later patterns (tighter
   turns, higher speed requirements) need adjustment.

## Known issues (minor, not blocking)

- **Settling wobble at spawn.** Spawning a robot very close to its own
  first waypoint causes a brief jitter (small oscillation) before it
  "escapes" toward the next leg — the force direction is nearly
  undefined at near-zero distance-to-target. Bravo showed ~130 samples
  of wobble before clean motion began. Minor fix for later: spawn robots
  slightly offset from their first waypoint rather than exactly on it.
- **Stock burger model references Gazebo Classic ROS bridge plugins**
  (`libgazebo_ros_diff_drive.so` etc.) that don't exist on Harmonic —
  cosmetic `[Err] Failed to load system plugin` lines, harmless since
  motion comes from TrajectoryFollower instead.

## Next: Phase 3

Re-point `channel_bridge.py` at this world and re-run the soak test.
No code changes needed — the bridge already expects exactly these three
topic names (`sar_uav`, `sar_ground_alpha`, `sar_ground_bravo`). This
closes the loop on the original flat-RSRP finding from the first soak
test: this time all three bodies are genuinely moving, so path loss
should visibly track real distance changes instead of sitting flat.
