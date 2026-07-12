#!/usr/bin/env bash
# spawn_models.sh — Spawn real PX4 x500 UAV + TurtleBot3 Burgers in sar_open_field.
#
# Requires:  GZ_SIM_RESOURCE_PATH set to include PX4-gazebo-models/models
#            and turtlebot3_simulations/turtlebot3_gazebo/models
set -e

WORLD=sar_open_field
TMP=/tmp/sar_spawn
mkdir -p $TMP

# Ensure Gazebo can find the model dependencies
PX4_DIR="/home/innocentmsc/Desktop/Msc-Research/simulation/PX4-gazebo-models/models"
TB3_DIR="/home/innocentmsc/turtlebot3_ws/src/turtlebot3_simulations/turtlebot3_gazebo/models"
TB3_DESC_DIR="/home/innocentmsc/turtlebot3_ws/src/turtlebot3/turtlebot3_description"

export GZ_SIM_RESOURCE_PATH="${PX4_DIR}:${TB3_DIR}:${TB3_DESC_DIR}:${GZ_SIM_RESOURCE_PATH}"

do_spawn() {
  local name=$1 sdf=$2
  local file="$TMP/${name}.txt"
  printf 'sdf: "%s"\nname: "%s"\n' "$sdf" "$name" > "$file"
  gz service -s /world/$WORLD/create \
    --reqtype gz.msgs.EntityFactory \
    --reptype gz.msgs.Boolean \
    --timeout 5000 \
    --req "$(cat "$file")" 2>/dev/null
}

echo "Spawning PX4 x500 UAV..."
do_spawn sar_uav '<?xml version="1.0"?>
<sdf version="1.9">
  <model name="sar_uav">
    <static>true</static>
    <pose>0 0 40 0 0 0</pose>
    <plugin filename="gz-sim-pose-publisher-system" name="gz::sim::systems::PosePublisher">
      <publish_model_pose>true</publish_model_pose>
      <publish_link_pose>false</publish_link_pose>
      <publish_collision_pose>false</publish_collision_pose>
      <publish_visual_pose>false</publish_visual_pose>
      <publish_nested_model_pose>false</publish_nested_model_pose>
      <update_frequency>10</update_frequency>
    </plugin>
    <include merge="true">
      <uri>model://x500_base</uri>
    </include>
  </model>
</sdf>'

sleep 2

echo "Spawning TurtleBot3 Burger Alpha..."
do_spawn "sar_ground_alpha" '<?xml version="1.0"?>
<sdf version="1.9">
  <model name="sar_ground_alpha">
    <pose>15 0 0.1 0 0 0</pose>
    <include merge="true">
      <uri>model://turtlebot3_burger</uri>
    </include>
  </model>
</sdf>'

sleep 2

echo "Spawning TurtleBot3 Burger Bravo..."
do_spawn "sar_ground_bravo" '<?xml version="1.0"?>
<sdf version="1.9">
  <model name="sar_ground_bravo">
    <pose>30 0 0.1 0 0 0</pose>
    <include merge="true">
      <uri>model://turtlebot3_burger</uri>
    </include>
  </model>
</sdf>'

sleep 2

echo "=== Available models ==="
gz model --list 2>/dev/null | grep -E "sar|turtlebot|x500"
echo "Done."