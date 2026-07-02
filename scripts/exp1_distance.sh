#!/bin/bash
echo "=== Experiment 1: Distance vs Signal Quality ==="
echo "UAV fixed at z=30m, alpha moving 10m to 150m"
for x in 10 20 30 40 50 60 70 80 90 100 110 120 130 140 150; do
    gz service -s /world/sar_world/set_pose \
        --reqtype gz.msgs.Pose \
        --reptype gz.msgs.Boolean \
        --timeout 2000 \
        --req "name: \"sar_ground_alpha\" position: {x: $x, y: 0, z: 0.1}"
    echo "Alpha at x=${x}m"
    sleep 8
done
echo "Experiment 1 complete"
