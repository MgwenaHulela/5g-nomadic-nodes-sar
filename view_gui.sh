#!/usr/bin/env bash
#
# view_gui.sh — attach the Gazebo GUI to the running headless server.
#
# The GUI is a separate process that resolves model:// mesh URIs itself,
# so it needs the same GZ_SIM_RESOURCE_PATH the server got — otherwise the
# x500 and TurtleBots render as empty/wireframe while primitives (ground,
# rocks) look fine.
#
# Software rendering (LIBGL_ALWAYS_SOFTWARE) is forced by default: on this
# machine hardware GL fails with "libEGL warning: egl: failed to create
# dri2 screen" (GTX 1650 + DISPLAY=:1 has no working GPU EGL context), so
# the GUI window never appears. llvmpipe renders on the CPU — slower, but
# it actually shows. Pass HW=1 to try hardware GL instead.
#
# NOTE: software rendering is CPU-heavy; don't leave the GUI open during a
# long unattended campaign — the rfsim RAN is timing-sensitive. Open it to
# look, then close it.
#
# Usage: ./view_gui.sh        (server must already be up)
#        HW=1 ./view_gui.sh   (attempt hardware GL)
set -uo pipefail

SELF_DIR="$(cd "$(dirname "$0")" && pwd)"

PX4_MODELS="$HOME/Desktop/Msc-Research/simulation/PX4-gazebo-models/models"
TB3_MODELS="$HOME/turtlebot3_ws/src/turtlebot3_simulations/turtlebot3_gazebo/models"
TB3_DESC="$HOME/turtlebot3_ws/src/turtlebot3/turtlebot3_description"
export GZ_SIM_RESOURCE_PATH="${SELF_DIR}/models:${PX4_MODELS}:${TB3_MODELS}:${TB3_DESC}:${GZ_SIM_RESOURCE_PATH:-}"

if [[ "${HW:-0}" != "1" ]]; then
  export LIBGL_ALWAYS_SOFTWARE=1
fi

pgrep -f "gz sim -r -s" >/dev/null 2>&1 || \
  echo "WARNING: no gz sim server detected — run ./start_full_stack.sh first"

exec gz sim -g --gui-config "$SELF_DIR/worlds/sar_patrol_gui.config"
