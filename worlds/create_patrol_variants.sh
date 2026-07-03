#!/usr/bin/env bash
# create_patrol_variants.sh — makes two copies of the burger model, one
# per robot, each with its own TrajectoryFollower waypoint pattern baked
# in (waypoints are static SDF config, not a runtime parameter, so alpha
# and bravo need genuinely separate model.sdf files rather than sharing
# the stock one).
#
# Patterns sized to the 15-150m A2G operating radius already validated
# in Phase 1's soak test data, offset from each other and from the UAV's
# default 60m circular patrol so the three don't overlap:
#   alpha: lawnmower sweep, x=[20,100] y=[-40,40], east side of origin
#   bravo: diamond, centered at (-60,0), west side of origin
#
# Both loop continuously (<loop>true</loop>) with no controller process
# needed — Gazebo drives the motion internally via force/torque toward
# each waypoint in turn.
#
# Usage: ./create_patrol_variants.sh /path/to/turtlebot3_ws

set -euo pipefail

WS_DIR="${1:?Usage: ./create_patrol_variants.sh /path/to/turtlebot3_ws}"
SRC_MODEL_DIR="$WS_DIR/src/turtlebot3_simulations/turtlebot3_gazebo/models/turtlebot3_burger"

if [ ! -d "$SRC_MODEL_DIR" ]; then
  echo "ERROR: source model not found at $SRC_MODEL_DIR"
  exit 1
fi

MODELS_ROOT="$(dirname "$SRC_MODEL_DIR")"
ALPHA_DIR="$MODELS_ROOT/turtlebot3_burger_alpha"
BRAVO_DIR="$MODELS_ROOT/turtlebot3_burger_bravo"

make_variant() {
  local dest_dir="$1"
  local model_name="$2"
  local waypoints_xml="$3"

  echo "== Creating $dest_dir =="
  rm -rf "$dest_dir"
  cp -r "$SRC_MODEL_DIR" "$dest_dir"
  local sdf="$dest_dir/model.sdf"

  # Rename the model itself so it's unambiguous in logs/gz commands
  sed -i "0,/<model name=/{s/<model name=\"[^\"]*\"/<model name=\"${model_name}\"/}" "$sdf"
  sed -i "0,/<model name=/{s/<model name='[^']*'/<model name='${model_name}'/}" "$sdf"

  # PosePublisher (same fix as Phase 1 / apply_burger_patch.sh)
  if ! grep -q "gz-sim-pose-publisher-system" "$sdf"; then
    local model_line
    model_line="$(grep -m1 -n '<model name=' "$sdf" | cut -d: -f1)"
    sed -i "${model_line}a\\
    <plugin filename=\"gz-sim-pose-publisher-system\" name=\"gz::sim::systems::PosePublisher\">\\
      <publish_model_pose>true</publish_model_pose>\\
      <publish_link_pose>false</publish_link_pose>\\
      <publish_collision_pose>false</publish_collision_pose>\\
      <publish_visual_pose>false</publish_visual_pose>\\
      <publish_nested_model_pose>false</publish_nested_model_pose>\\
      <update_frequency>10</update_frequency>\\
    </plugin>" "$sdf"
  fi

  # TrajectoryFollower — insert right before the closing </model> tag
  # so it sits alongside the robot's existing links/joints, not nested
  # inside one of them.
  python3 - "$sdf" "$waypoints_xml" << 'PYEOF'
import sys
path, waypoints_xml = sys.argv[1], sys.argv[2]
with open(path) as f:
    content = f.read()
plugin_block = f'''
    <plugin filename="gz-sim-trajectory-follower-system" name="gz::sim::systems::TrajectoryFollower">
      <link_name>base_link</link_name>
      <loop>true</loop>
      <force>15</force>
      <torque>15</torque>
{waypoints_xml}
    </plugin>
'''
idx = content.rfind("</model>")
if idx == -1:
    print("ERROR: no </model> closing tag found", file=sys.stderr)
    sys.exit(1)
content = content[:idx] + plugin_block + content[idx:]
with open(path, "w") as f:
    f.write(content)
print(f"TrajectoryFollower inserted into {path}")
PYEOF

  echo "Variant $model_name ready at $dest_dir"
}

ALPHA_WAYPOINTS='      <waypoints>
        <waypoint>20 -40</waypoint>
        <waypoint>100 -40</waypoint>
        <waypoint>100 -20</waypoint>
        <waypoint>20 -20</waypoint>
        <waypoint>20 0</waypoint>
        <waypoint>100 0</waypoint>
        <waypoint>100 20</waypoint>
        <waypoint>20 20</waypoint>
        <waypoint>20 40</waypoint>
        <waypoint>100 40</waypoint>
      </waypoints>'

BRAVO_WAYPOINTS='      <waypoints>
        <waypoint>-60 50</waypoint>
        <waypoint>-10 0</waypoint>
        <waypoint>-60 -50</waypoint>
        <waypoint>-110 0</waypoint>
      </waypoints>'

make_variant "$ALPHA_DIR" "sar_ground_alpha_model" "$ALPHA_WAYPOINTS"
make_variant "$BRAVO_DIR" "sar_ground_bravo_model" "$BRAVO_WAYPOINTS"

cat <<EOF

== Done ==

Alpha model: $ALPHA_DIR/model.sdf  (lawnmower, x=[20,100] y=[-40,40])
Bravo model: $BRAVO_DIR/model.sdf  (diamond, centered at -60,0)

Spawn with:
  ./spawn_ground_robot.sh sar_ground_alpha 20 -40 $WS_DIR $ALPHA_DIR/model.sdf
  ./spawn_ground_robot.sh sar_ground_bravo -60 50 $WS_DIR $BRAVO_DIR/model.sdf

(spawn_ground_robot.sh needs a small update to accept an explicit
model.sdf path instead of always looking up the stock turtlebot3_burger
— see updated version.)
EOF
