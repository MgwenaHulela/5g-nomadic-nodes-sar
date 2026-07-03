#!/usr/bin/env bash
# install_turtlebot3.sh — clones and builds the OFFICIAL ROBOTIS-GIT
# turtlebot3 packages on the `humble` branch (not `humble-devel`, which
# is the older Gazebo Classic version). This branch is ros_gz-based and
# documented against Gazebo Harmonic tutorials — matches our stack.
#
# Minimal package set for simulation-only use (no real hardware, no
# manipulator, no Dynamixel SDK needed): turtlebot3, turtlebot3_msgs,
# turtlebot3_simulations.

set -euo pipefail

WS_DIR="${1:-$HOME/turtlebot3_ws}"
echo "== Setting up workspace at $WS_DIR =="
mkdir -p "$WS_DIR/src"
cd "$WS_DIR/src"

for repo in turtlebot3_simulations turtlebot3 turtlebot3_msgs; do
  if [ -d "$repo" ]; then
    echo "$repo already cloned, skipping"
  else
    git clone -b humble "https://github.com/ROBOTIS-GIT/${repo}.git"
  fi
done

echo "== Installing rosdep dependencies =="
cd "$WS_DIR"
source /opt/ros/humble/setup.bash
rosdep install --from-paths src --ignore-src -r -y || true

echo "== Building (colcon) =="
colcon build --symlink-install

cat <<EOF

== Done ==

Add to your shell (or source manually each session):
  source $WS_DIR/install/setup.bash
  export TURTLEBOT3_MODEL=burger

Next: run apply_burger_patch.sh to add the PosePublisher plugin to the
burger model BEFORE spawning it — we already know from Phase 1 that
Harmonic won't publish /model/<name>/pose without it, so no need to
rediscover that here.
EOF
