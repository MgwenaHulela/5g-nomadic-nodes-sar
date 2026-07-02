#!/bin/bash
# Usage: ./set_channel_loss.sh <ploss_dB>
# Updates OAI channel model with new path loss and restarts gNB

PLOSS=$1
if [ -z "$PLOSS" ]; then
    echo "Usage: $0 <ploss_dB>"
    exit 1
fi

echo "Setting path loss to ${PLOSS}dB"

# Update channelmod config
sed -i "s/ploss_dB      = [0-9.]*;/ploss_dB      = ${PLOSS};/g" \
    ~/sar_sim/oai_configs/channelmod_sar.conf

# Kill current gNB
sudo pkill -f nr-softmodem
sleep 2

# Restart gNB with new channel config
cd ~/openairinterface5g/cmake_targets/ran_build/build
sudo ./nr-softmodem --rfsim \
    -O ~/sar_sim/oai_configs/gnb.sa.band78.rfsim.conf \
    2>&1 | tee /tmp/oai_gnb.log &

sleep 8
# Wait for UE to reconnect
echo "gNB restarted with ploss=${PLOSS}dB"
