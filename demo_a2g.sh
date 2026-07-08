#!/usr/bin/env bash
# demo_a2g.sh — A2G channel effects demo
#
# Measures throughput at different path loss levels (simulating UAV range).
# Requires: gNB + UEs running, setup_over_air_test.sh setup already applied.
#
# Usage:
#   ./demo_a2g.sh

set -uo pipefail

GNB_TELNET="127.0.0.1:9091"
ALPHA_TUN="oaitun_ue1"
BRAVO_TUN="oaitun_ue1p2"
TEST_IP_A="10.0.0.200"
TEST_IP_B="10.0.0.201"
DURATION=8

send_chan() {
  local idx="$1" param="$2" val="$3"
  echo "channelmod modify $idx $param $val" | nc -w 1 "$GNB_TELNET" > /dev/null 2>&1
  sleep 1
}

run_test() {
  local label="$1" ue_ip="$2" tun="$3" chan_spec="$4"
  local idx="${chan_spec%:*}" ploss="${chan_spec#*:}"
  echo ""
  echo "── $label ──"
  send_chan "$idx" "ploss" "$ploss"
  sleep 2
  echo -n "  ping RTT (avg): "
  ping -c 3 -W 3 -I "$tun" "$ue_ip" 2>&1 | tail -1 | grep -oP '= [\d.]+/[\d.]+/[\d.]+' | cut -d/ -f2
  echo -n "  throughput:     "
  iperf3 -c "$ue_ip" --bind-dev "$tun" -t "$DURATION" 2>&1 | grep "receiver" | grep -oP '[\d.]+ Mbits/sec'
}

# Pre-flight
for tun in "$ALPHA_TUN" "$BRAVO_TUN"; do
  ip link show "$tun" > /dev/null 2>&1 || { echo "FATAL: $tun missing"; exit 1; }
done
nc -z -w 1 127.0.0.1 9091 2>/dev/null || { echo "FATAL: gNB telnet port 9091 not reachable"; exit 1; }

echo "=============================================="
echo "  A2G Channel Effects — Over-the-Air Demo"
echo "=============================================="
echo ""
echo "gNB telnet:  $GNB_TELNET"
echo "alpha TUN:   $ALPHA_TUN ($(ip -4 addr show "$ALPHA_TUN" | grep -oP 'inet \K[\d.]+')) -> $TEST_IP_A"
echo "bravo TUN:   $BRAVO_TUN ($(ip -4 addr show "$BRAVO_TUN" | grep -oP 'inet \K[\d.]+')) -> $TEST_IP_B"
echo "iperf:       ${DURATION}s per test point"
echo ""

# ── Alpha UL (model 1 = rfsimu_channel_ue0) ──
echo "▸ Alpha uplink — varying path loss (simulates UAV range)"

run_test "Close range  (ploss=20dB)" "$TEST_IP_A" "$ALPHA_TUN" "1:20"
run_test "Medium range (ploss=30dB)" "$TEST_IP_A" "$ALPHA_TUN" "1:30"
run_test "Far range    (ploss=38dB)" "$TEST_IP_A" "$ALPHA_TUN" "1:38"
run_test "Edge of link (ploss=42dB)" "$TEST_IP_A" "$ALPHA_TUN" "1:42"

# Recover link baseline
send_chan "1" "ploss" "20"
sleep 3

# ── Delay offset effect ──
echo ""
echo "▸ Delay offset effect on ping RTT (ploss=20dB, alpha)"
for offset in 0 1000 5000 10000; do
  send_chan "1" "offset" "$offset"
  us=$(( offset * 1000000 / 30720000 ))
  sleep 2
  avg=$(ping -c 3 -W 3 -I "$ALPHA_TUN" "$TEST_IP_A" 2>&1 | tail -1 | grep -oP '= [\d.]+/[\d.]+/[\d.]+' | cut -d/ -f2)
  echo "  offset=$offset (~${us}us)  ping RTT avg: ${avg:-N/A} ms"
done
send_chan "1" "offset" "0"

# ── Link dropout threshold ──
echo ""
echo "▸ Link dropout threshold"
echo "  ploss=45dB (testing)..."
send_chan "1" "ploss" "45"
sleep 4
ping -c 1 -W 3 -I "$ALPHA_TUN" "$TEST_IP_A" > /dev/null 2>&1 && \
  echo "  LINK SURVIVED at 45dB" || echo "  LINK LOST at 45dB"

# Restore
send_chan "1" "ploss" "20"
echo ""
echo "Done. If link was lost, restart UEs:"
echo "  pkill -9 nr-uesoftmodem; # then start both UEs"
