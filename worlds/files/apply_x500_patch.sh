#!/usr/bin/env bash
# apply_x500_patch.sh — adds the PosePublisher plugin to PX4's x500 model.
#
# Why this exists: Gazebo Harmonic's PosePublisher plugin must be nested
# inside the specific <model> whose pose you want published — it cannot
# sit at world level (confirmed against /usr/share/gz/gz-sim8/worlds/
# pose_publisher.sdf on Harmonic 8.14.0; world-level attachment fails
# with "PosePublisher plugin should be attached to a model entity").
# The x500 model itself isn't tracked in this git repo (it lives inside
# the ~3.5GB PX4-Autopilot clone, which is gitignored), so this patch
# needs reapplying any time PX4-Autopilot is freshly cloned.
#
# Idempotent: safe to run multiple times, checks for existing plugin
# before inserting.
#
# Usage: ./apply_x500_patch.sh /path/to/PX4-Autopilot

set -euo pipefail

PX4_DIR="${1:?Usage: ./apply_x500_patch.sh /path/to/PX4-Autopilot}"
MODEL_SDF="$(find "$PX4_DIR/Tools/simulation/gz/models/x500" -maxdepth 1 -name 'model.sdf' | head -1)"

if [ -z "$MODEL_SDF" ]; then
  echo "ERROR: couldn't find x500/model.sdf under $PX4_DIR"
  exit 1
fi

if grep -q "gz-sim-pose-publisher-system" "$MODEL_SDF"; then
  echo "PosePublisher plugin already present in $MODEL_SDF — nothing to do."
  exit 0
fi

echo "Patching $MODEL_SDF..."
sed -i "/<model name='x500'>/a\\
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
  echo "The <model name='x500'> line format may have changed between PX4 versions."
  exit 1
fi
