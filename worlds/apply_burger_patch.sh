#!/usr/bin/env bash
# apply_burger_patch.sh — adds the PosePublisher plugin to the
# turtlebot3_burger model, same lesson as apply_x500_patch.sh from
# Phase 1: on Gazebo Harmonic 8.14.0, PosePublisher must be nested
# inside the specific <model> whose pose you want published, not at
# world level.
#
# Idempotent: safe to run multiple times.
#
# Usage: ./apply_burger_patch.sh /path/to/turtlebot3_ws

set -euo pipefail

WS_DIR="${1:?Usage: ./apply_burger_patch.sh /path/to/turtlebot3_ws}"
MODEL_SDF="$(find "$WS_DIR/src/turtlebot3_simulations" -path '*turtlebot3_burger*' -name 'model.sdf' | head -1)"

if [ -z "$MODEL_SDF" ]; then
  echo "ERROR: couldn't find turtlebot3_burger/model.sdf under $WS_DIR"
  echo "Search manually: find $WS_DIR -iname 'model.sdf' | grep -i burger"
  exit 1
fi
echo "Found: $MODEL_SDF"

if grep -q "gz-sim-pose-publisher-system" "$MODEL_SDF"; then
  echo "PosePublisher plugin already present — nothing to do."
  exit 0
fi

# Find the <model name="..."> opening line — burger's model file may name
# it 'turtlebot3_burger' or 'burger' depending on the exact repo revision,
# so grep for the pattern rather than assuming the exact name.
MODEL_LINE="$(grep -m1 -n '<model name=' "$MODEL_SDF" | cut -d: -f1)"
if [ -z "$MODEL_LINE" ]; then
  echo "ERROR: couldn't find a <model name=...> line in $MODEL_SDF"
  exit 1
fi

echo "Patching $MODEL_SDF at line $MODEL_LINE..."
sed -i "${MODEL_LINE}a\\
    <plugin filename=\"gz-sim-pose-publisher-system\" name=\"gz::sim::systems::PosePublisher\">\\
      <publish_model_pose>true</publish_model_pose>\\
      <publish_link_pose>false</publish_link_pose>\\
      <publish_collision_pose>false</publish_collision_pose>\\
      <publish_visual_pose>false</publish_visual_pose>\\
      <publish_nested_model_pose>false</publish_nested_model_pose>\\
      <update_frequency>10</update_frequency>\\
    </plugin>" "$MODEL_SDF"

if grep -q "gz-sim-pose-publisher-system" "$MODEL_SDF"; then
  echo "Patch applied successfully."
else
  echo "WARNING: patch may not have applied — check $MODEL_SDF manually."
  exit 1
fi
