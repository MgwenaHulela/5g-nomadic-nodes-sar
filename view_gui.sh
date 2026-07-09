#!/usr/bin/env bash
#
# view_gui.sh — attach the Gazebo GUI to the running headless server.
#
# The GUI is a separate process that resolves model:// mesh URIs itself,
# so it needs the same GZ_SIM_RESOURCE_PATH the server got — otherwise the
# x500 and TurtleBots render as empty/wireframe while primitives (ground,
# rocks) look fine.
#
# Usage: ./view_gui.sh   (server must already be up via start_full_stack.sh)
set -uo pipefail

SELF_DIR="$(cd "$(dirname "$0")" && pwd)"

PX4_MODELS="$HOME/Desktop/Msc-Research/simulation/PX4-gazebo-models/models"
TB3_MODELS="$HOME/turtlebot3_ws/src/turtlebot3_simulations/turtlebot3_gazebo/models"
TB3_DESC="$HOME/turtlebot3_ws/src/turtlebot3/turtlebot3_description"
export GZ_SIM_RESOURCE_PATH="${PX4_MODELS}:${TB3_MODELS}:${TB3_DESC}:${GZ_SIM_RESOURCE_PATH:-}"

pgrep -f "gz sim -r -s" >/dev/null 2>&1 || \
  echo "WARNING: no gz sim server detected — run ./start_full_stack.sh first"

exec gz sim -g --gui-config "$SELF_DIR/worlds/sar_patrol_gui.config"
