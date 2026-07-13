#!/usr/bin/env bash
#
# view_gui.sh — attach the Gazebo GUI to the running headless server.
#
# The GUI is a separate process that resolves model:// mesh URIs itself,
# so it needs the same GZ_SIM_RESOURCE_PATH the server got — otherwise the
# x500 and TurtleBots render as empty/wireframe while primitives (ground,
# rocks) look fine.
#
# Rendering: uses the Ogre v1 engine over hardware GLX, which works on this
# machine's NVIDIA GTX 1650 (verified: glxinfo reports NVIDIA GL 4.6, and
# `gz sim -g --render-engine ogre` starts with zero EGL errors). Do NOT
# force LIBGL_ALWAYS_SOFTWARE here: the NVIDIA proprietary driver ignores
# it for its own contexts but it can still route GLX through Mesa/llvmpipe,
# which conflicts with the real NVIDIA X display and yields a blank window
# that then fails. The earlier "libEGL: failed to create dri2 screen" is an
# EGL-path warning specific to the Ogre2 engine; the Ogre v1 engine used
# here goes through GLX and is unaffected.
#
# Usage: ./view_gui.sh   (server must already be up via start_full_stack.sh)
set -uo pipefail

SELF_DIR="$(cd "$(dirname "$0")" && pwd)"

PX4_MODELS="$HOME/Desktop/Msc-Research/simulation/PX4-gazebo-models/models"
TB3_MODELS="$HOME/turtlebot3_ws/src/turtlebot3_simulations/turtlebot3_gazebo/models"
TB3_DESC="$HOME/turtlebot3_ws/src/turtlebot3/turtlebot3_description"
export GZ_SIM_RESOURCE_PATH="${SELF_DIR}/models:${PX4_MODELS}:${TB3_MODELS}:${TB3_DESC}:${GZ_SIM_RESOURCE_PATH:-}"

pgrep -f "gz sim -r -s" >/dev/null 2>&1 || \
  echo "WARNING: no gz sim server detected — run ./start_full_stack.sh first"

# --render-engine ogre makes the working GLX engine explicit (also set in
# the GUI config), overriding any ogre2/EGL default.
exec gz sim -g --render-engine ogre --gui-config "$SELF_DIR/worlds/sar_patrol_gui.config"
