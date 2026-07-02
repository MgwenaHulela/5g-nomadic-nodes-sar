#!/bin/bash
echo "=== Experiment 2: UAV Altitude vs Coverage ==="
echo "Alpha fixed at x=50m, UAV altitude varying"
for z in 10 20 30 40 50 60 80 100; do
    gz service -s /world/sar_world/set_pose \
        --reqtype gz.msgs.Pose \
        --reptype gz.msgs.Boolean \
        --timeout 2000 \
        --req "name: \"sar_uav\" position: {x: 0, y: 0, z: $z}"
    echo "UAV at z=${z}m"
    sleep 8
done
gz service -s /world/sar_world/set_pose \
    --reqtype gz.msgs.Pose \
    --reptype gz.msgs.Boolean \
    --timeout 2000 \
    --req "name: \"sar_uav\" position: {x: 0, y: 0, z: 30}"
echo "Experiment 2 complete"
