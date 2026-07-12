#!/usr/bin/env bash
kill $(pgrep -f channel_bridge 2>/dev/null) 2>/dev/null; sleep 1
cd "$(dirname "$0")"
nohup python3 -u channel_bridge.py > /tmp/ch_bridge.log 2>&1 &
echo "Bridge PID: $!"
sleep 3
head -10 /tmp/ch_bridge.log