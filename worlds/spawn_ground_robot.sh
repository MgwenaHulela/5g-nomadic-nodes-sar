#!/usr/bin/env bash
# spawn_ground_robot.sh (v2) — spawns a ground robot into the already
# running sar_open_field world. Now accepts an explicit model.sdf path
# as a 5th arg (for the alpha/bravo patrol variants from
# create_patrol_variants.sh) — if omitted, falls back to the stock
# turtlebot3_burger model (Phase 2a behavior, no patrol motion).
#
# Usage:
#   ./spawn_ground_robot.sh <name> <x> <y> [turtlebot3_ws] [model_sdf_path]
#
# Example (Phase 2a, stationary):
#   ./spawn_ground_robot.sh sar_ground_alpha 60 0
#
# Example (Phase 2b, patrol variant):
#   ./spawn_ground_robot.sh sar_ground_alpha 20 -40 ~/turtlebot3_ws \
#       ~/turtlebot3_ws/src/turtlebot3_simulations/turtlebot3_gazebo/models/turtlebot3_burger_alpha/model.sdf

set -euo pipefail

NAME="${1:?Usage: ./spawn_ground_robot.sh <name> <x> <y> [ws] [model_sdf_path]}"
X="${2:?missing x coordinate}"
Y="${3:?missing y coordinate}"
WS_DIR="${4:-$HOME/turtlebot3_ws}"
MODEL_SDF_OVERRIDE="${5:-}"
WORLD_NAME="sar_open_field"

if [ -n "$MODEL_SDF_OVERRIDE" ]; then
  MODEL_SDF="$MODEL_SDF_OVERRIDE"
  if [ ! -f "$MODEL_SDF" ]; then
    echo "ERROR: $MODEL_SDF not found. Run create_patrol_variants.sh first?"
    exit 1
  fi
  echo "Using explicit model: $MODEL_SDF"
else
  echo "== No model override given — ensuring stock burger has PosePublisher =="
  "$(dirname "${BASH_SOURCE[0]}")/apply_burger_patch.sh" "$WS_DIR"
  MODEL_SDF="$(find "$WS_DIR/src/turtlebot3_simulations" -path '*turtlebot3_burger' -name 'model.sdf' | grep -v alpha | grep -v bravo | head -1)"
fi

echo "Spawning $NAME at ($X, $Y) from $MODEL_SDF"

gz service -s "/world/${WORLD_NAME}/create" \
  --reqtype gz.msgs.EntityFactory \
  --reptype gz.msgs.Boolean \
  --timeout 5000 \
  --req "sdf_filename: \"$MODEL_SDF\", name: \"${NAME}\", pose: {position: {x: $X, y: $Y, z: 0.1}}"

sleep 2
echo "== Verifying pose topic =="
gz topic -l | grep "model/${NAME}/pose" || {
  echo "WARNING: /model/${NAME}/pose not found — check /tmp/gz_sim.log"
}

echo "Done. Verify with:"
echo "  python3 test_pose_subscribe.py ${NAME} --seconds 30"
