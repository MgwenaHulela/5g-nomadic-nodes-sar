# Phase 1 — PX4 x500 in the Open Field World (COMPLETE)

**Status: confirmed working.** A real PX4 SITL x500 quadrotor flies an
autonomous circular patrol (60m radius, 30m altitude, 90s period) in a
custom Gazebo Harmonic world, with pose data verified flowing correctly
on `/model/sar_uav/pose` — the exact topic `channel_bridge.py` expects.

Verified 2026-07-02: 150 pose samples at 10Hz showed the drone tracking
a genuine circle (radius held at ~60.0-60.5m throughout, altitude
climbing to and holding 30.0m, angular position sweeping ~59° over 15s
— consistent with the configured 90s full-circle period).

## Files

| File | Purpose |
|---|---|
| `sar_open_field.sdf` | 500x500m world — ground plane, lighting, magnetic field, spherical coordinates, sensor system plugins |
| `install_px4.sh` | One-time PX4-Autopilot clone + build |
| `apply_x500_patch.sh` | Adds PosePublisher plugin to the x500 model (idempotent, called automatically by launch_phase1.sh) |
| `launch_phase1.sh` | Starts Gazebo headless, patches x500, spawns it named `sar_uav`, attaches PX4 SITL |
| `uav_patrol_mission.py` | MAVSDK script: takeoff to 30m, circle continuously |
| `test_pose_subscribe.py` | Diagnostic: direct gz.transport13 pose subscription, prints live x/y/z |
| `diagnose_gps.py` | Diagnostic: raw GPS fix type + individual health flags |

## Sequence

```bash
cd ~/sar_sim/worlds
chmod +x install_px4.sh launch_phase1.sh apply_x500_patch.sh
./install_px4.sh                          # one-time, ~10-20 min build

./launch_phase1.sh ./PX4-Autopilot        # world + patched UAV + PX4 SITL
sleep 10

python3 test_pose_subscribe.py             # confirm pose data at rest first
python3 uav_patrol_mission.py              # takeoff + circle
```

While it's patrolling, run `test_pose_subscribe.py` in a second terminal
to watch real movement — x/y should sweep through a circle, z should
hold near 30.

## Every fix that was needed, in the order we found them

Kept here because each one is a real, non-obvious Harmonic/PX4
integration gotcha that will resurface in Phase 2 for the ground robots:

1. **Missing `PX4_GZ_WORLD` env var.** Without it PX4 polls forever for
   a world named `default` instead of your actual world name — silent
   infinite "Waiting for Gazebo world..." with no error.

2. **Missing Magnetometer system plugin.** `gz-sim-magnetometer-system`
   must be explicitly added at world level or PX4 reports "Found 0
   compass" and refuses to arm.

3. **Missing `<magnetic_field>` world element.** Even with the
   Magnetometer plugin loaded, it has nothing to simulate a reading
   against without this — surfaces as "Strong magnetic interference" /
   "no heading reference" preflight failures.

4. **Missing `<spherical_coordinates>` world element.** Without a
   geographic anchor, NavSat and Magnetometer both fail outright
   ("Spherical coordinates not set" in gz_sim.log) — no GPS fix is ever
   possible. This one blocks EVERYTHING downstream (arming, offboard,
   the works) with a hang on "Waiting for global position + home lock"
   that has no PX4-side error message at all — you have to check
   gz_sim.log specifically to find it.

5. **PosePublisher plugin scope.** Must be nested inside the specific
   `<model>` whose pose you want published — NOT at world level, despite
   `<publish_model_pose>true</publish_model_pose>` implying it should
   work globally. World-level attachment fails with "PosePublisher
   plugin should be attached to a model entity" (visible in gz_sim.log,
   NOT px4_sitl.log — easy to miss). Since the x500 model is spawned at
   runtime from PX4's own files rather than defined in our world SDF,
   the plugin has to be patched into PX4's `model.sdf` directly —
   handled by `apply_x500_patch.sh`.

6. **Not a bug, but a major time sink:** a topic showing up in
   `gz topic -l` does NOT mean anything is publishing to it. `gz topic
   -e` and direct `gz.transport13` subscription will both hang
   indefinitely (no timeout, no error) if a topic is advertised but has
   no active publisher. This looks IDENTICAL to the UCT multicast
   blocking issue from the earlier Fortress project and cost significant
   debugging time chasing network fixes (`GZ_IP`, multicast routes) that
   were never the actual problem here. **The `/clock` topic is a
   reliable canary** — if `gz topic -e -t /clock -n 1` works but your
   target topic doesn't, the problem is a missing publisher (like the
   above), not network/transport.

7. **Disk space.** Each PX4 relaunch writes a full-mode ULog flight log.
   PX4's own auto-cleanup only triggers once free space exceeds ~47GB,
   which may never happen on a smaller/fuller disk — logs will
   accumulate silently across every test run. Worth periodically
   clearing `PX4-Autopilot/build/px4_sitl_default/rootfs/0/log/`.

## What NOT to worry about (cosmetic, seen during testing)

- `[Wrn] XML Element[gz_frame_id]... not defined in SDF` — harmless SDF
  schema mismatch between PX4's shipped model and this Harmonic point
  release.
- `<motorNumber> is deprecated, please use <actuator_number>` — cosmetic,
  PX4's own model file, not something we need to fix.
- `[Err] Failed to load system plugin [MotorFailurePlugin]` — an
  optional failure-injection plugin PX4's model references; irrelevant
  unless you specifically want to simulate motor failures.
- `Preflight Fail: No connection to the GCS` — expected, MAVSDK doesn't
  need QGroundControl.

## Next: Phase 2

Add TurtleBot3 Burger `sar_ground_alpha` / `sar_ground_bravo` into this
same running world. Expect to need the SAME PosePublisher-on-model
treatment (fix #5 above) for each robot's own model.sdf — this will not
be a surprise a second time. `channel_bridge.py` needs no changes; it
already expects exactly these three topic names.
