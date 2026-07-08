#!/usr/bin/env bash
#
# soak_test_alpha.sh — long-duration data collection (alpha-only uplink)
#
# Uses the over-the-air setup (setup_over_air_test.sh setup) to route
# alpha's uplink traffic through the 5G NR stack. The response path is
# loopback (no over-the-air downlink), but uplink RAN metrics are valid.
#
# Usage:
#   ./scripts/setup_over_air_test.sh setup   # first, apply config
#   ./soak_test_alpha.sh                      # 45 min
#   ./soak_test_alpha.sh 600                  # 10 min shakedown
#   ./scripts/setup_over_air_test.sh cleanup  # after, revert config

set -uo pipefail

DURATION="${1:-2700}"
BRIDGE_SCRIPT="$HOME/sar_sim/channel_bridge.py"
UE_ALPHA_TUN=oaitun_ue1
UE_ALPHA_LOG=/tmp/ue_alpha.log
GNB_LOG=/tmp/gnb.log
IPERF_PORT=5201
IPERF_BURST_S=10
IPERF_PERIOD_S=60

RUN_DIR="$HOME/sar_sim/logs/soak_$(date +%Y%m%d_%H%M%S)"
mkdir -p "$RUN_DIR"

ALPHA_IP=$(ip -4 addr show "$UE_ALPHA_TUN" | grep -oP 'inet \K[\d.]+')
TEST_IP="10.0.0.200"

PIDS=()
cleanup() {
  echo ""
  echo "== Stopping all collectors =="
  for pid in "${PIDS[@]:-}"; do kill "$pid" 2>/dev/null; done
  pkill -f "iperf3 -c $TEST_IP" 2>/dev/null
  pkill -f channel_bridge.py 2>/dev/null
  sleep 2
  write_summary
  echo "Data collected in: $RUN_DIR"
}
trap cleanup EXIT INT TERM

# ---------- Pre-flight ------------------------------------------------------
echo "== Pre-flight checks =="

command -v iperf3 >/dev/null 2>&1 || { echo "FATAL: iperf3 not installed"; exit 1; }
if ! iperf3 --help 2>&1 | grep -q "bind-dev"; then
  echo "FATAL: iperf3 lacks --bind-dev (needs >= 3.10)"
  exit 1
fi
[[ -f "$BRIDGE_SCRIPT" ]] || { echo "FATAL: $BRIDGE_SCRIPT not found"; exit 1; }

ip addr show "$UE_ALPHA_TUN" >/dev/null 2>&1 || { echo "FATAL: $UE_ALPHA_TUN missing — stack not up?"; exit 1; }

echo "  alpha: $UE_ALPHA_TUN $ALPHA_IP"
echo "  test server: $TEST_IP"

pgrep -f "gz sim" >/dev/null 2>&1 || echo "  WARNING: no 'gz sim' process found"

# Verify over-the-air path is configured
TEST_OK=false
if ping -c 2 -W 2 -I "$UE_ALPHA_TUN" "$TEST_IP" >/dev/null 2>&1; then
  TEST_OK=true
  echo "  connectivity OK (RTT via air)"
else
  echo "  FATAL: alpha -> $TEST_IP ping failed"
  echo "  Run: ./scripts/setup_over_air_test.sh setup"
  exit 1
fi

grep -oP "UE \K[0-9a-f]{4}" "$GNB_LOG" 2>/dev/null | sort -u > "$RUN_DIR/rntis_before.txt"
echo "  starting RNTIs: $(tr '\n' ' ' < "$RUN_DIR/rntis_before.txt")"

cp ~/sar_sim/oai_configs/*.conf "$RUN_DIR/" 2>/dev/null
{
  echo "start: $(date -Iseconds)"
  echo "duration_s: $DURATION"
  echo "alpha_ip: $ALPHA_IP"
  echo "test_server: $TEST_IP"
  echo "mode: alpha-only uplink (downlink via loopback)"
  echo "iperf: ${IPERF_BURST_S}s burst every ${IPERF_PERIOD_S}s"
} > "$RUN_DIR/run_meta.txt"

# ---------- Collectors ------------------------------------------------------
echo "== Starting collectors (run dir: $RUN_DIR) =="

# 1. channel bridge
python3 "$BRIDGE_SCRIPT" > "$RUN_DIR/bridge_stdout.log" 2>&1 &
PIDS+=($!)
echo "  [1] channel_bridge.py (pid ${PIDS[-1]})"

# 2. continuous ping alpha -> test server, 1 Hz
(
  echo "timestamp,seq,rtt_ms" > "$RUN_DIR/ping_a2s.csv"
  ping -I "$UE_ALPHA_TUN" -i 1 "$TEST_IP" 2>/dev/null | while read -r line; do
    if [[ "$line" =~ icmp_seq=([0-9]+).*time=([0-9.]+) ]]; then
      echo "$(date -Iseconds),${BASH_REMATCH[1]},${BASH_REMATCH[2]}" >> "$RUN_DIR/ping_a2s.csv"
    fi
  done
) &
PIDS+=($!)
echo "  [2] 1 Hz ping alpha->server (pid ${PIDS[-1]})"

# 3. iperf3 burst: 10 s TCP every 60 s
(
  echo "timestamp,mbps,retransmits" > "$RUN_DIR/iperf_bursts.csv"
  while true; do
    TS=$(date -Iseconds)
    J=$(iperf3 -c "$TEST_IP" -B "$ALPHA_IP" --bind-dev "$UE_ALPHA_TUN" \
         -p "$IPERF_PORT" -t "$IPERF_BURST_S" -J 2>/dev/null)
    MBPS=$(echo "$J" | python3 -c "
import sys, json
try:
    d = json.load(sys.stdin)
    bps = d['end']['sum_received']['bits_per_second']
    rt  = d['end']['sum_sent'].get('retransmits', -1)
    print(f'{bps/1e6:.3f},{rt}')
except Exception:
    print('-1,-1')
")
    echo "$TS,$MBPS" >> "$RUN_DIR/iperf_bursts.csv"
    sleep $((IPERF_PERIOD_S - IPERF_BURST_S))
  done
) &
PIDS+=($!)
echo "  [3] iperf3 bursts every ${IPERF_PERIOD_S}s (pid ${PIDS[-1]})"

# 4. SINR/RSRP sampler, 2 s
(
  echo "timestamp,rsrp_dbm,sinr_db" > "$RUN_DIR/sinr_samples.csv"
  while true; do
    TS=$(date -Iseconds)
    RSRP=$(grep -oP 'SS-RSRP: \K[-\d]+(?= dBm/RE)' "$UE_ALPHA_LOG" 2>/dev/null | tail -1)
    SINR=$(grep -oP 'SS-SINR: \K[-\d.]+(?= dB)' "$UE_ALPHA_LOG" 2>/dev/null | tail -1)
    [[ -n "$SINR" ]] && echo "$TS,${RSRP:-},${SINR}" >> "$RUN_DIR/sinr_samples.csv"
    sleep 2
  done
) &
PIDS+=($!)
echo "  [4] SINR sampler @ 2 s (pid ${PIDS[-1]})"

# 5. gNB stats sampler, 10 s
(
  echo "timestamp|line" > "$RUN_DIR/gnb_stats.log"
  while true; do
    TS=$(date -Iseconds)
    grep -E "UE.*(ulsch_rounds|dlsch_rounds)" "$GNB_LOG" 2>/dev/null | tail -4 | \
      while read -r line; do echo "$TS|$line" >> "$RUN_DIR/gnb_stats.log"; done
    sleep 10
  done
) &
PIDS+=($!)
echo "  [5] gNB stats sampler @ 10 s (pid ${PIDS[-1]})"

# ---------- Summary writer --------------------------------------------------
write_summary() {
  S="$RUN_DIR/summary.txt"
  {
    echo "SOAK TEST SUMMARY (alpha-only) — $(date -Iseconds)"
    echo "run dir: $RUN_DIR"
    echo "planned duration: ${DURATION}s"
    echo ""

    echo "== Link survival =="
    grep -oP "UE \K[0-9a-f]{4}" "$GNB_LOG" 2>/dev/null | sort -u > "$RUN_DIR/rntis_after.txt"
    echo "RNTIs before: $(tr '\n' ' ' < "$RUN_DIR/rntis_before.txt" 2>/dev/null)"
    echo "RNTIs after:  $(tr '\n' ' ' < "$RUN_DIR/rntis_after.txt" 2>/dev/null)"
    if diff -q "$RUN_DIR/rntis_before.txt" "$RUN_DIR/rntis_after.txt" >/dev/null 2>&1; then
      echo "RNTI churn: NONE — links survived"
    else
      echo "RNTI churn: DETECTED"
    fi
    echo ""

    echo "== Sync-loss sentinels (SINR <= -70 dB) =="
    if [[ -f "$RUN_DIR/sinr_samples.csv" ]]; then
      TOTAL=$(($(wc -l < "$RUN_DIR/sinr_samples.csv") - 1))
      BAD=$(awk -F, 'NR>1 && $3 <= -70 {c++} END{print c+0}' "$RUN_DIR/sinr_samples.csv")
      echo "alpha: $BAD sentinel samples out of $TOTAL"
      echo ""
      echo "== SINR stats (dB) =="
      awk -F, 'NR>1 && $3 > -70 {s+=$3; n++; if(min==""||$3<min)min=$3; if(max==""||$3>max)max=$3} \
        END{if(n>0) printf "alpha: n=%d min=%.1f max=%.1f mean=%.1f\n", n, min, max, s/n; else print "alpha: no valid samples"}' \
        "$RUN_DIR/sinr_samples.csv"
    fi
    echo ""

    echo "== Ping alpha->server =="
    if [[ -f "$RUN_DIR/ping_a2s.csv" ]]; then
      N=$(($(wc -l < "$RUN_DIR/ping_a2s.csv") - 1))
      EXPECTED=$DURATION
      awk -F, 'NR>1 {s+=$3; n++; if(min==""||$3<min)min=$3; if(max==""||$3>max)max=$3} \
        END{if(n>0) printf "replies=%d min=%.1fms max=%.1fms mean=%.1fms\n", n, min, max, s/n}' \
        "$RUN_DIR/ping_a2s.csv"
      echo "approx loss: $(( (EXPECTED - N) > 0 ? (EXPECTED - N) * 100 / EXPECTED : 0 ))% (expected ~${EXPECTED} @1Hz, got $N)"
    fi
    echo ""

    echo "== iperf3 throughput (per burst) =="
    if [[ -f "$RUN_DIR/iperf_bursts.csv" ]]; then
      awk -F, 'NR>1 && $2 > 0 {s+=$2; n++; if(min==""||$2<min)min=$2; if(max==""||$2>max)max=$2} \
        END{if(n>0) printf "bursts=%d min=%.2f max=%.2f mean=%.2f Mbps\n", n, min, max, s/n}' \
        "$RUN_DIR/iperf_bursts.csv"
    fi
    echo ""
    echo "Files: sinr_samples.csv, ping_a2s.csv, iperf_bursts.csv, gnb_stats.log,"
    echo "       bridge_stdout.log, + channel_bridge_log.csv"
  } | tee "$S"
}

# ---------- Run -------------------------------------------------------------
echo ""
echo "== Running for ${DURATION}s ($(( DURATION / 60 )) min) — Ctrl+C to stop early =="
END_TS=$(( $(date +%s) + DURATION ))
while (( $(date +%s) < END_TS )); do
  sleep 30
  REMAIN=$(( END_TS - $(date +%s) ))
  LAST_SINR=$(tail -1 "$RUN_DIR/sinr_samples.csv" 2>/dev/null | cut -d, -f3)
  LAST_MBPS=$(tail -1 "$RUN_DIR/iperf_bursts.csv" 2>/dev/null | cut -d, -f2)
  echo "$(date +%H:%M:%S)  remaining ${REMAIN}s | SINR ${LAST_SINR:-?} dB | last burst ${LAST_MBPS:-?} Mbps"
done
echo "Duration reached."
