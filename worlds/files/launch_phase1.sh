#!/usr/bin/env bash
# launch_phase1.sh — bring up sar_open_field headless with a UAV named
# "sar_uav" (so gz-transport topic /model/sar_uav/pose matches exactly
# what channel_bridge.py already expects — no bridge code changes needed
# for Phase 1).
#
# Usage:
#   ./launch_phase1.sh /path/to/PX4-Autopilot
#
# Run from three terminals is easiest the first time so you can see each
# stage's output separately — this script backgrounds everything and
# tails logs, but if something fails, rerun the failing step manually.

set -euo pipefail

PX4_DIR="${1:?Usage: ./launch_phase1.sh /path/to/PX4-Autopilot}"
WORLD_NAME="sar_open_field"
UAV_NAME="sar_uav"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "== Copying world file into PX4's world search path =="
cp "$SCRIPT_DIR/sar_open_field.sdf" "$PX4_DIR/Tools/simulation/gz/worlds/${WORLD_NAME}.sdf"

echo "== Locating the x500 model =="
X500_MODEL_SDF="$(find "$PX4_DIR/Tools/simulation/gz/models/x500" -maxdepth 1 -name 'model.sdf' | head -1)"
if [ -z "$X500_MODEL_SDF" ]; then
  echo "ERROR: couldn't find x500/model.sdf under $PX4_DIR/Tools/simulation/gz/models/"
  echo "Check that PX4's gazebo-models submodule pulled correctly:"
  echo "  cd $PX4_DIR && git submodule update --init --recursive Tools/simulation/gz/models 2>/dev/null || true"
  exit 1
fi
echo "Found: $X500_MODEL_SDF"

echo "== Ensuring x500 has the PosePublisher plugin (idempotent) =="
"$SCRIPT_DIR/apply_x500_patch.sh" "$PX4_DIR"

echo "== Starting Gazebo headless with sar_open_field =="
export GZ_SIM_RESOURCE_PATH="$PX4_DIR/Tools/simulation/gz/models:$PX4_DIR/Tools/simulation/gz/worlds"
gz sim -s -r -v 3 "$PX4_DIR/Tools/simulation/gz/worlds/${WORLD_NAME}.sdf" > /tmp/gz_sim.log 2>&1 &
GZ_PID=$!
echo "gz sim PID: $GZ_PID (log: /tmp/gz_sim.log)"
sleep 8

echo "== Spawning x500 into the world, named '${UAV_NAME}' =="
gz service -s "/world/${WORLD_NAME}/create" \
  --reqtype gz.msgs.EntityFactory \
  --reptype gz.msgs.Boolean \
  --timeout 5000 \
  --req "sdf_filename: \"$X500_MODEL_SDF\", name: \"${UAV_NAME}\", pose: {position: {x: 0, y: 0, z: 0.3}}"

sleep 3
echo "== Verifying the pose topic exists =="
gz topic -l | grep "model/${UAV_NAME}/pose" || {
  echo "WARNING: /model/${UAV_NAME}/pose not found yet — check /tmp/gz_sim.log"
}

echo "== Starting PX4 SITL, standalone mode, bound to '${UAV_NAME}' =="
cd "$PX4_DIR"
PX4_GZ_STANDALONE=1 PX4_SIM_MODEL=gz_x500 PX4_GZ_MODEL_NAME="${UAV_NAME}" \
  PX4_GZ_WORLD="${WORLD_NAME}" \
  ./build/px4_sitl_default/bin/px4 -i 0 > /tmp/px4_sitl.log 2>&1 &
PX4_PID=$!
echo "PX4 SITL PID: $PX4_PID (log: /tmp/px4_sitl.log)"

cat <<EOF

== Phase 1 world is up ==

gz sim PID:  $GZ_PID
PX4 PID:     $PX4_PID

Verify pose is live:
  gz topic -e -t /model/${UAV_NAME}/pose -n 5

MAVSDK/MAVLink is available on udp://:14540 for the patrol script.

To stop everything:
  kill $PX4_PID $GZ_PID
EOF
