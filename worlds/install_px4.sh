#!/usr/bin/env bash
# install_px4.sh — one-time PX4 SITL + Gazebo Harmonic toolchain setup
#
# Run this from wherever you want PX4-Autopilot cloned, e.g. ~/sar_sim/
# (or a sibling directory — it's a large repo, keep it out of git).
#
# This does NOT touch your existing OAI/Open5GS/ROS2 install. PX4's own
# ubuntu.sh installs Gazebo Harmonic if it's not already present, but
# you already have Gazebo Harmonic 8.14.0 installed, so it should mostly
# just confirm dependencies and skip the Gazebo install step.

set -euo pipefail

echo "== Cloning PX4-Autopilot (this is a big repo, ~2-3GB with submodules) =="
git clone --branch main --recursive https://github.com/PX4/PX4-Autopilot.git
cd PX4-Autopilot

echo "== Running PX4's Ubuntu setup script =="
bash ./Tools/setup/ubuntu.sh

echo "== Installing MAVSDK-Python (for the patrol mission script) =="
pip3 install --break-system-packages mavsdk

echo "== Building PX4 SITL with the x500 target (first build is slow, ~10-20 min) =="
HEADLESS=1 make px4_sitl gz_x500

cat <<'EOF'

== Done ==

If the build succeeded you should have seen Gazebo start headless and
PX4 print its startup banner, then sat waiting for a GCS/offboard
connection. Ctrl+C to stop it — that was just a build+smoke-test, not
the real Phase 1 launch.

Next: run launch_phase1.sh (in the same directory as this script) to
bring up the actual sar_open_field world with a properly-named UAV.
EOF
