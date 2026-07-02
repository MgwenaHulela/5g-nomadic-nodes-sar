#!/bin/bash
echo "=== Experiment 3: SAR Search Pattern ==="
for y in -30 -20 -10 0 10 20 30; do
    for x in 10 30 50 70 90; do
        gz service -s /world/sar_world/set_pose \
            --reqtype gz.msgs.Pose \
            --reptype gz.msgs.Boolean \
            --timeout 2000 \
            --req "name: \"sar_ground_alpha\" position: {x: $x, y: $y, z: 0.1}"
        sleep 4
    done
done
echo "Experiment 3 complete"
