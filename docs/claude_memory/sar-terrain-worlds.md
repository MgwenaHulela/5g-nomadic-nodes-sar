---
name: sar-terrain-worlds
description: "The sar_sim terrain worlds (forest/hills/indoor) and the world_channel dispatch pattern coupling each world's geometry to its channel model"
metadata: 
  node_type: memory
  type: project
  originSessionId: 1b170fb1-c728-4fdf-8680-0efca41f2886
---

Added 2026-07-20: three new Gazebo worlds beyond sar_open_field / sar_urban, each following the same "one Python module = source of truth for BOTH the SDF and the channel model" pattern as [[sar-sim-stack-operation]]. Selected via `SAR_WORLD`; `start_full_stack.sh` auto-generates the SDF if missing (WORLD_GEN associative array).

- **sar_forest** — `worlds/forest_trees.py` (359 trees, seed 1789, ~10 m grid) + `make_forest_world.py`. Channel: Weissberger foliage loss from `foliage_depth()` (ray-sphere through canopies), NLoS at ≥2 m canopy. Lever: climb / reach clearing.
- **sar_hills** — `worlds/terrain_hills.py` (8 half-buried `<ellipsoid>` mounds) + `make_hills_world.py`. Channel: ITU-R P.526 knife-edge diffraction `terrain_diffraction_db()`, NLoS ≥6 dB. First hill (45,0,20,18) deliberately shadows victim (70,6) from central UAV; verified 20 m→34.6 dB, 40 m→24.3 dB, 60 m→0 dB (climb clears ridge). NOT a DEM heightmap — robots go AROUND domes; heightmap is a documented follow-on.
- **sar_indoor** — `worlds/indoor_building.py` (enterable 20×16 building, 3 rooms, door gaps, victim in back-left) + `make_indoor_world.py`. Channel: 3GPP TR 38.901 O2I = 9 dB penetration + 0.5 dB/m depth when UE inside footprint. Rendered OPEN-TOP for GUI viewing but channel applies roof loss as if enclosed. Lever is the robot EXITING (not UAV altitude) — opposite of urban/hills. Robots spawn south of the door (0,-14)/(7,-14). Best viewed via `stream_camera.sh`.

**How the coupling works:** `channel_bridge.py` `a2g_path_loss()` gained an `excess_db` param (added on top of FSPL+LoS/NLoS). The old `_URBAN` branch was replaced by a `world_channel(uav, ue) -> (los_blocked, excess_db)` dispatch built once at import from `SAR_WORLD`; open-field returns `(None, 0)` (probabilistic). To retune a terrain's physics, edit its module's loss function — no SDF regen needed for pure channel changes; regen only for geometry. Docs: `docs/WORLDS_RUNBOOK.md` §§1-4.
