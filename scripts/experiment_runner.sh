#!/usr/bin/env bash
#
# experiment_runner.sh — Automated A2G distance sweep with CSV logging.
#
# Moves sar_ground_alpha along the x-axis (10m → 200m), at each waypoint
# reads the bridge's current ploss and runs iperf3, saving everything to
# ~/sar_sim/logs/experiment_<timestamp>/ .
#
# Usage:
#   ./scripts/experiment_runner.sh             # full sweep
#   ./scripts/experiment_runner.sh --dry-run   # show waypoints, don't move
#
# Requires: start_full_stack.sh already running (gNB, UE, bridge, Gazebo).

set -uo pipefail

SELF_DIR="$(cd "$(dirname "$0")/.." && pwd)"
LOG_DIR="$SELF_DIR/logs/experiment_$(date +%Y%m%d_%H%M%S)"
CSV="$LOG_DIR/results.csv"
BRIDGE_LOG="/tmp/ch_bridge.log"
DRY_RUN=false

[[ "${1:-}" == "--dry-run" ]] && DRY_RUN=true

# Waypoints: (distance_m)
DISTANCES=(10 20 30 40 50 60 70 80 90 100 110 120 130 140 150 175 200)
UAV_Z=40
IPERF_S=6
SETTLE_S=6

mkdir -p "$LOG_DIR"

echo "════════════════════════════════════════════════════"
echo " A2G Distance Sweep"
echo " Log: $LOG_DIR"
echo "════════════════════════════════════════════════════"
echo "distance_m,d_horiz_m,ploss_dB,throughput_mbps,rtt_ms" > "$CSV"

for d in "${DISTANCES[@]}"; do
  echo ""
  echo "--- Distance: ${d}m ---"

  if ! $DRY_RUN; then
    # Move ground_alpha to x=d, y=0
    gz service -s /world/sar_open_field/set_pose \
      --reqtype gz.msgs.Pose \
      --reptype gz.msgs.Boolean \
      --timeout 2000 \
      --req "name: \"sar_ground_alpha\" position: {x: $d, y: 0, z: 0.1}" >/dev/null 2>&1

    # Wait for bridge EMA to converge
    sleep "$SETTLE_S"
  fi

  # Read ploss from bridge log (last line, grab ploss for alpha UL)
  if [[ -f "$BRIDGE_LOG" ]]; then
    LAST=$(tail -1 "$BRIDGE_LOG")
    P=$(echo "$LAST" | grep -oP 'gnb_ue0_alpha_ul.*?ploss \K[\d.]+' | tail -1)
    DH=$(echo "$LAST" | grep -oP 'd=\K[\d.]+' | tail -1)
  fi
  PLOSS="${P:-0}"
  DHORIZ="${DH:-0}"

  if $DRY_RUN; then
    echo "  [DRY-RUN] would set d=${d}m, settle=${SETTLE_S}s, iperf=${IPERF_S}s"
    MBPS="-"
    RTT="-"
  else
    # Ping RTT
    RTT_MS=$(ping -c 3 -i 0.2 -I oaitun_ue1 10.0.0.200 2>/dev/null | tail -1 | grep -oP '=\K[\d.]+(?=/\d+/\d+)' | cut -d/ -f2)
    RTT="${RTT_MS:-0}"

    # Throughput
    IPERF_OUT=$(iperf3 -c 10.0.0.200 --bind-dev oaitun_ue1 -t "$IPERF_S" 2>/dev/null)
    MBPS=$(echo "$IPERF_OUT" | grep "receiver" | grep -oP '[\d.]+ Mbits' | grep -oP '[\d.]+')
    MBPS="${MBPS:-0}"

    echo "  ploss=${PLOSS}dB  d_horiz=${DHORIZ}m  rtt=${RTT}ms  tp=${MBPS}Mbps"
  fi

  echo "${d},${DHORIZ},${PLOSS},${MBPS},${RTT}" >> "$CSV"
done

# Restore alpha to origin
if ! $DRY_RUN; then
  gz service -s /world/sar_open_field/set_pose \
    --reqtype gz.msgs.Pose --reptype gz.msgs.Boolean --timeout 2000 \
    --req "name: \"sar_ground_alpha\" position: {x: 50, y: 0, z: 0.1}" >/dev/null 2>&1
fi

echo ""
echo "════════════════════════════════════════════════════"
echo " Done — results saved to $CSV"
echo "════════════════════════════════════════════════════"
column -t -s, "$CSV" 2>/dev/null || cat "$CSV"
