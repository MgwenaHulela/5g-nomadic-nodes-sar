#!/usr/bin/env bash
#
# soak_test_both.sh — dual-UE data collection for SAR mission
#
# Both alpha and bravo stream uplink traffic through the 5G NR stack.
# RAN metrics are collected from both UEs and the gNB.
#
# Prerequisites:
#   ./scripts/setup_over_air_test.sh setup
#
# Usage:
#   ./soak_test_both.sh              # 45 min
#   ./soak_test_both.sh 600          # 10 min shakedown

set -uo pipefail

DURATION="${1:-2700}"
BRIDGE_SCRIPT="$HOME/sar_sim/channel_bridge.py"
UE_ALPHA_TUN="oaitun_ue1"
UE_BRAVO_TUN="oaitun_ue1p2"
UE_ALPHA_LOG=/tmp/ue_alpha.log
UE_BRAVO_LOG=/tmp/ue_bravo.log
GNB_LOG=/tmp/gnb.log
IPERF_PORT=5201
IPERF_BURST_S=10
IPERF_PERIOD_S=60

RUN_DIR="$HOME/sar_sim/logs/soak_dual_$(date +%Y%m%d_%H%M%S)"
mkdir -p "$RUN_DIR"

ALPHA_IP=$(ip -4 addr show "$UE_ALPHA_TUN" | grep -oP 'inet \K[\d.]+')
BRAVO_IP=$(ip -4 addr show "$UE_BRAVO_TUN" | grep -oP 'inet \K[\d.]+')
TEST_IP_A="10.0.0.200"
TEST_IP_B="10.0.0.201"

PIDS=()
cleanup() {
  echo ""
  echo "== Stopping all collectors =="
  for pid in "${PIDS[@]:-}"; do kill "$pid" 2>/dev/null; done
  pkill -f "iperf3 -c $TEST_IP_A\|iperf3 -c $TEST_IP_B" 2>/dev/null
  pkill -f channel_bridge.py 2>/dev/null
  sleep 2
  write_summary
  echo "Data collected in: $RUN_DIR"
}
trap cleanup EXIT INT TERM

# ---------- Pre-flight ------------------------------------------------------
echo "== Pre-flight checks =="
command -v iperf3 >/dev/null 2>&1 || { echo "FATAL: iperf3 not installed"; exit 1; }
ip link show "$UE_ALPHA_TUN" >/dev/null 2>&1 || { echo "FATAL: $UE_ALPHA_TUN missing"; exit 1; }
ip link show "$UE_BRAVO_TUN" >/dev/null 2>&1 || { echo "FATAL: $UE_BRAVO_TUN missing"; exit 1; }

echo "  alpha: $UE_ALPHA_TUN $ALPHA_IP -> $TEST_IP_A"
echo "  bravo: $UE_BRAVO_TUN $BRAVO_IP -> $TEST_IP_B"

pgrep -f "gz sim" >/dev/null 2>&1 || echo "  WARNING: no 'gz sim' process"

for t in "$TEST_IP_A" "$TEST_IP_B"; do
  ss -tlnp 2>/dev/null | grep -q "$t:$IPERF_PORT" || {
    echo "  FATAL: iperf3 server for $t not running on :$IPERF_PORT"
    echo "  Run: ./scripts/setup_over_air_test.sh setup"
    exit 1
  }
done

# Pre-flight pings through air interfaces
FAIL=0
echo "  Checking alpha connectivity..."
ping -c 2 -W 3 -I "$UE_ALPHA_TUN" "$TEST_IP_A" >/dev/null 2>&1 || {
  echo "  FAIL: alpha -> $TEST_IP_A"; FAIL=1
}
echo "  Checking bravo connectivity..."
ping -c 2 -W 3 -I "$UE_BRAVO_TUN" "$TEST_IP_B" >/dev/null 2>&1 || {
  echo "  FAIL: bravo -> $TEST_IP_B"; FAIL=1
}
[[ "$FAIL" -eq 1 ]] && exit 1
echo "  connectivity OK (both UEs via air)"

# Record starting RNTIs
grep -oP "UE \K[0-9a-f]{4}" "$GNB_LOG" 2>/dev/null | sort -u > "$RUN_DIR/rntis_before.txt"
echo "  starting RNTIs: $(tr '\n' ' ' < "$RUN_DIR/rntis_before.txt")"

cp ~/sar_sim/oai_configs/*.conf "$RUN_DIR/" 2>/dev/null
{
  echo "start: $(date -Iseconds)"
  echo "duration_s: $DURATION"
  echo "alpha: $ALPHA_IP via $UE_ALPHA_TUN -> $TEST_IP_A"
  echo "bravo: $BRAVO_IP via $UE_BRAVO_TUN -> $TEST_IP_B"
  echo "mode: dual-UE simultaneous uplink (downlink via loopback)"
  echo "iperf: ${IPERF_BURST_S}s burst every ${IPERF_PERIOD_S}s per UE"
} > "$RUN_DIR/run_meta.txt"

# ---------- Collectors ------------------------------------------------------
echo "== Starting collectors =="

# 1. Channel bridge
python3 "$BRIDGE_SCRIPT" > "$RUN_DIR/bridge_stdout.log" 2>&1 &
PIDS+=($!)
echo "  [1] channel_bridge.py (pid ${PIDS[-1]})"

# 2. Ping alpha -> server A, 1 Hz
(
  echo "timestamp,seq,rtt_ms" > "$RUN_DIR/ping_a2s.csv"
  ping -I "$UE_ALPHA_TUN" -i 1 "$TEST_IP_A" 2>/dev/null | while read -r line; do
    if [[ "$line" =~ icmp_seq=([0-9]+).*time=([0-9.]+) ]]; then
      echo "$(date -Iseconds),${BASH_REMATCH[1]},${BASH_REMATCH[2]}" >> "$RUN_DIR/ping_a2s.csv"
    fi
  done
) &
PIDS+=($!)
echo "  [2] ping alpha (pid ${PIDS[-1]})"

# 3. Ping bravo -> server B, 1 Hz
(
  echo "timestamp,seq,rtt_ms" > "$RUN_DIR/ping_b2s.csv"
  ping -I "$UE_BRAVO_TUN" -i 1 "$TEST_IP_B" 2>/dev/null | while read -r line; do
    if [[ "$line" =~ icmp_seq=([0-9]+).*time=([0-9.]+) ]]; then
      echo "$(date -Iseconds),${BASH_REMATCH[1]},${BASH_REMATCH[2]}" >> "$RUN_DIR/ping_b2s.csv"
    fi
  done
) &
PIDS+=($!)
echo "  [3] ping bravo (pid ${PIDS[-1]})"

# 4. iperf3 bursts — both UEs simultaneously
(
  echo "timestamp,ue,mbps,retransmits" > "$RUN_DIR/iperf_bursts.csv"

  parse_iperf() {
    local F="$1"
    python3 -c "
import sys, json
with open('$F') as f:
    d = json.load(f)
bps = d['end']['sum_received']['bits_per_second']
rt  = d['end']['sum_sent'].get('retransmits', -1)
print(f'{bps/1e6:.3f},{rt}')
" 2>/dev/null || echo "-1,-1"
  }

  while true; do
    TS=$(date -Iseconds)

    # Launch both bursts simultaneously
    iperf3 -c "$TEST_IP_A" -B "$ALPHA_IP" --bind-dev "$UE_ALPHA_TUN" \
      -p "$IPERF_PORT" -t "$IPERF_BURST_S" -J > "$RUN_DIR/.iperf_a.json.tmp" 2>/dev/null &
    PID_A=$!
    iperf3 -c "$TEST_IP_B" -B "$BRAVO_IP" --bind-dev "$UE_BRAVO_TUN" \
      -p "$IPERF_PORT" -t "$IPERF_BURST_S" -J > "$RUN_DIR/.iperf_b.json.tmp" 2>/dev/null &
    PID_B=$!

    # Wait for both to complete
    wait "$PID_A" "$PID_B" 2>/dev/null

    MBPS_A=$(parse_iperf "$RUN_DIR/.iperf_a.json.tmp")
    MBPS_B=$(parse_iperf "$RUN_DIR/.iperf_b.json.tmp")
    echo "$TS,alpha,$MBPS_A" >> "$RUN_DIR/iperf_bursts.csv"
    echo "$TS,bravo,$MBPS_B" >> "$RUN_DIR/iperf_bursts.csv"

    sleep $((IPERF_PERIOD_S - IPERF_BURST_S))
  done
) &
PIDS+=($!)
echo "  [4] iperf3 bursts (pid ${PIDS[-1]})"

# 5. SINR/RSRP sampler, 2 s, both UEs
(
  echo "timestamp,ue,rsrp_dbm,sinr_db" > "$RUN_DIR/sinr_samples.csv"
  while true; do
    TS=$(date -Iseconds)
    for name in "alpha:$UE_ALPHA_LOG" "bravo:$UE_BRAVO_LOG"; do
      UE="${name%%:*}"; LOG="${name##*:}"
      RSRP=$(grep -oP 'SS-RSRP: \K[-\d]+(?= dBm/RE)' "$LOG" 2>/dev/null | tail -1)
      SINR=$(grep -oP 'SS-SINR: \K[-\d.]+(?= dB)' "$LOG" 2>/dev/null | tail -1)
      [[ -n "$SINR" ]] && echo "$TS,$UE,${RSRP:-},${SINR}" >> "$RUN_DIR/sinr_samples.csv"
    done
    sleep 2
  done
) &
PIDS+=($!)
echo "  [5] SINR sampler @ 2 s (pid ${PIDS[-1]})"

# 6. gNB stats sampler, 10 s
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
echo "  [6] gNB stats sampler @ 10 s (pid ${PIDS[-1]})"

# ---------- Summary writer --------------------------------------------------
write_summary() {
  S="$RUN_DIR/summary.txt"
  {
    echo "SOAK TEST SUMMARY (dual-UE) — $(date -Iseconds)"
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
      for ue in alpha bravo; do
        TOTAL=$(grep -c ",$ue," "$RUN_DIR/sinr_samples.csv" 2>/dev/null || echo 0)
        BAD=$(awk -F, -v u="$ue" '$2==u && $4 <= -70 {c++} END{print c+0}' "$RUN_DIR/sinr_samples.csv")
        echo "$ue: $BAD sentinel samples out of $TOTAL"
      done
      echo ""
      echo "== SINR stats (dB) =="
      for ue in alpha bravo; do
        awk -F, -v u="$ue" '$2==u && $4 > -70 {s+=$4; n++; if(min==""||$4<min)min=$4; if(max==""||$4>max)max=$4} \
          END{if(n>0) printf "%s: n=%d min=%.1f max=%.1f mean=%.1f\n", u, n, min, max, s/n; else print u": no valid samples"}' \
          "$RUN_DIR/sinr_samples.csv"
      done
    fi
    echo ""

    echo "== Ping stats =="
    for pair in "alpha:a2s" "bravo:b2s"; do
      ue="${pair%%:*}"; tag="${pair##*:}"
      CSV="$RUN_DIR/ping_${tag}.csv"
      if [[ -f "$CSV" ]]; then
        N=$(($(wc -l < "$CSV") - 1))
        awk -F, -v u="$ue" 'NR>1 {s+=$3; n++; if(min==""||$3<min)min=$3; if(max==""||$3>max)max=$3} \
          END{printf "%s: replies=%d min=%.1fms max=%.1fms mean=%.1fms loss=%d%%\n", u, n, min, max, s/n, (EXPECTED>n ? (EXPECTED-n)*100/EXPECTED : 0)}' \
          "EXPECTED=$DURATION" "$CSV"
      fi
    done
    echo ""

    echo "== iperf3 throughput (Mbps per burst) =="
    if [[ -f "$RUN_DIR/iperf_bursts.csv" ]]; then
      for ue in alpha bravo; do
        awk -F, -v u="$ue" '$2==u && $3>0 {s+=$3; n++; if(min==""||$3<min)min=$3; if(max==""||$3>max)max=$3} \
          END{if(n>0) printf "%s: bursts=%d min=%.2f max=%.2f mean=%.2f Mbps\n", u, n, min, max, s/n}' \
          "$RUN_DIR/iperf_bursts.csv"
      done
    fi
    echo ""
    echo "Files: sinr_samples.csv, ping_a2s.csv, ping_b2s.csv,"
    echo "       iperf_bursts.csv, gnb_stats.log, bridge_stdout.log"
  } | tee "$S"
}

# ---------- Run -------------------------------------------------------------
echo ""
echo "== Running for ${DURATION}s ($(( DURATION / 60 )) min) =="
END_TS=$(( $(date +%s) + DURATION ))
while (( $(date +%s) < END_TS )); do
  sleep 30
  REMAIN=$(( END_TS - $(date +%s) ))
  LAST_SINR=$(tail -1 "$RUN_DIR/sinr_samples.csv" 2>/dev/null | cut -d, -f4)
  LAST_MBPS=$(tail -1 "$RUN_DIR/iperf_bursts.csv" 2>/dev/null | cut -d, -f3)
  echo "$(date +%H:%M:%S)  remaining ${REMAIN}s | SINR ${LAST_SINR:-?} dB | burst ${LAST_MBPS:-?} Mbps"
done
echo "Duration reached."
