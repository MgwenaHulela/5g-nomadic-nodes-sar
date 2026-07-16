#!/usr/bin/env bash
#
# stream_demo.sh — REAL 5G video+telemetry streaming demo for SAR robots.
#
# Streams a constant-bitrate UDP video feed (with a JSON sensor blob per
# frame) from a robot's network namespace, over the actual 5G air interface
# (rfsim gNB -> UPF), to a collector on the host. The collector prints live
# application QoS — goodput, frame-delivery ratio, packet loss, latency,
# jitter — which degrades as the robot moves NLoS behind buildings, because
# the same channel model shaping iperf shapes this stream too.
#
# This rides the SAME proven data path iperf uses (UE netns --bind-dev tun
# -> host data-net IP 10.0.0.200/.201), so it needs the stack already up.
#
# Usage:
#   export SAR_SUDO_PASS='...'                 # sender runs in a netns (root)
#   ./stream_demo.sh [alpha|bravo|both] [duration_s] [bitrate_mbps]
#   ./stream_demo.sh both 120 3
#
# Ctrl-C stops cleanly. Per-second metrics are also written to
# logs/stream_<robot>_<ts>.csv for plotting.
set -uo pipefail
SELF_DIR="$(cd "$(dirname "$0")" && pwd)"

ROBOT="${1:-bravo}"
DURATION="${2:-120}"
BITRATE="${3:-3}"

if ! sudo -n true 2>/dev/null && [ -z "${SAR_SUDO_PASS:-}" ]; then
  echo "ERROR: senders run inside a network namespace (need root)." >&2
  echo "       export SAR_SUDO_PASS='...' or configure passwordless sudo." >&2
  exit 1
fi
sudo_run() { echo "${SAR_SUDO_PASS:-}" | sudo -S -p '' "$@"; }

declare -A NS=(   [alpha]=robot_alpha    [bravo]=robot_bravo )
declare -A TUN=(  [alpha]=oaitun_ue1     [bravo]=oaitun_ue1p2 )
declare -A UEIP=( [alpha]=10.45.0.2      [bravo]=10.45.0.3 )
declare -A DST=(  [alpha]=10.0.0.200     [bravo]=10.0.0.201 )
declare -A PORT=( [alpha]=5600           [bravo]=5601 )

mkdir -p "$SELF_DIR/logs"
PIDS=()

cleanup() {
  echo; echo "[stream_demo] stopping..."
  for p in "${PIDS[@]:-}"; do kill "$p" 2>/dev/null; done
  # reap any sender left inside the namespaces
  for r in alpha bravo; do
    sudo_run pkill -f "sar_stream_sender.py.*${DST[$r]}" 2>/dev/null
  done
  wait 2>/dev/null
  echo "[stream_demo] done. CSVs in $SELF_DIR/logs/stream_*.csv"
}
trap cleanup INT TERM EXIT

start_one() {
  local r="$1"
  local ts csv
  ts="$(date +%Y%m%d_%H%M%S)"
  csv="$SELF_DIR/logs/stream_${r}_${ts}.csv"
  # collector on the host (default namespace)
  python3 "$SELF_DIR/scripts/sar_stream_collector.py" \
      --bind "${DST[$r]}" --port "${PORT[$r]}" --robot-id "${r^^}" \
      --csv "$csv" --duration "$DURATION" &
  PIDS+=($!)
  sleep 0.5
  # sender inside the robot's netns -> over the air
  sudo_run ip netns exec "${NS[$r]}" \
      python3 "$SELF_DIR/scripts/sar_stream_sender.py" \
      --dst "${DST[$r]}" --port "${PORT[$r]}" --bind-dev "${TUN[$r]}" \
      --src-ip "${UEIP[$r]}" --robot-id "${r^^}" \
      --bitrate-mbps "$BITRATE" --fps 15 --duration "$DURATION" &
  PIDS+=($!)
  echo "[stream_demo] ${r^^}: ${BITRATE} Mbps video+telemetry over 5G  ->  $csv"
}

case "$ROBOT" in
  both) start_one alpha; start_one bravo ;;
  alpha|bravo) start_one "$ROBOT" ;;
  *) echo "usage: $0 [alpha|bravo|both] [duration_s] [bitrate_mbps]"; exit 2 ;;
esac

echo "[stream_demo] streaming for ${DURATION}s — watch the QoS change as the"
echo "              robot goes LoS <-> NLoS (run a patrol in another shell)."
wait
