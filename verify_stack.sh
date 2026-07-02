#!/usr/bin/env bash
#
# soak_test.sh — long-duration data collection run for the SAR 5G platform
#
# Orchestrates, for DURATION seconds (default 2700 = 45 min):
#   * channel_bridge.py        (Gazebo pose -> OAI channel, live)
#   * continuous 1 Hz ping     alpha -> bravo over the air (RTT time series)
#   * iperf3 bursts            10 s TCP test every 60 s, alternating direction
#   * SINR/RSRP sampler        every 2 s from both UE logs, wall-clock stamped
#   * gNB stats sampler        every 10 s (RNTI / SNR / BLER lines, stamped)
#
# Everything lands in ~/sar_sim/logs/soak_<timestamp>/ plus a summary.txt
# written at the end (RNTI churn, sync-loss sentinels, ping loss, throughput).
#
# PRE-REQS (script checks these and aborts if not met):
#   - Full stack up and green (run ./verify_stack.sh first)
#   - Gazebo running with robots patrolling
#   - iperf3 installed (sudo apt install -y iperf3)
#
# Usage:
#   ./soak_test.sh              # 45 min
#   ./soak_test.sh 600          # 10 min shakedown first (recommended once)
#
set -uo pipefail

DURATION="${1:-2700}"
BRIDGE_SCRIPT="$HOME/sar_sim/channel_bridge.py"
UE_ALPHA_TUN=oaitun_ue1
UE_BRAVO_TUN=oaitun_ue1p2
UE_ALPHA_LOG=/tmp/ue_alpha.log
UE_BRAVO_LOG=/tmp/ue_bravo.log
GNB_LOG=/tmp/gnb.log
IPERF_PORT=5201
IPERF_BURST_S=10
IPERF_PERIOD_S=60
IPERF_RATE_CAP="20M"   # cap even TCP so a misroute can never starve the softmodems
LOOPBACK_SUSPECT_MBPS=500   # >this on a 20MHz link = traffic leaked via loopback

RUN_DIR="$HOME/sar_sim/logs/soak_$(date +%Y%m%d_%H%M%S)"
mkdir -p "$RUN_DIR"

PIDS=()
cleanup() {
  echo ""
  echo "== Stopping all collectors =="
  for pid in "${PIDS[@]:-}"; do kill "$pid" 2>/dev/null; done
  pkill -f "iperf3 -s -B" 2>/dev/null
  pkill -f channel_bridge.py 2>/dev/null
  sleep 2
  write_summary
  echo "Data collected in: $RUN_DIR"
}
trap cleanup EXIT INT TERM

# ---------- Pre-flight ------------------------------------------------------
echo "== Pre-flight checks =="

command -v iperf3 >/dev/null 2>&1 || { echo "FATAL: iperf3 not installed (sudo apt install -y iperf3)"; exit 1; }
if ! iperf3 --help 2>&1 | grep -q "bind-dev"; then
  echo "FATAL: this iperf3 ($(iperf3 --version | head -1)) lacks --bind-dev (needs >= 3.10)."
  echo "Without device binding, traffic between two local TUN IPs short-circuits via"
  echo "loopback: you measure 50+ Gbps of nothing AND the CPU load desyncs the UEs."
  echo "Build a current release:"
  echo "  sudo apt install -y libssl-dev"
  echo "  cd ~ && wget https://github.com/esnet/iperf/archive/refs/tags/3.16.tar.gz"
  echo "  tar xzf 3.16.tar.gz && cd iperf-3.16 && ./configure && make -j4"
  echo "  sudo make install && sudo ldconfig"
  exit 1
fi
[[ -f "$BRIDGE_SCRIPT" ]] || { echo "FATAL: $BRIDGE_SCRIPT not found"; exit 1; }

ip addr show "$UE_ALPHA_TUN" >/dev/null 2>&1 || { echo "FATAL: $UE_ALPHA_TUN missing — stack not up? Run verify_stack.sh"; exit 1; }
ip addr show "$UE_BRAVO_TUN" >/dev/null 2>&1 || { echo "FATAL: $UE_BRAVO_TUN missing — stack not up? Run verify_stack.sh"; exit 1; }

ALPHA_IP=$(ip -4 addr show "$UE_ALPHA_TUN" | grep -oP 'inet \K[\d.]+')
BRAVO_IP=$(ip -4 addr show "$UE_BRAVO_TUN" | grep -oP 'inet \K[\d.]+')
echo "  alpha: $UE_ALPHA_TUN $ALPHA_IP"
echo "  bravo: $UE_BRAVO_TUN $BRAVO_IP"

pgrep -f "gz sim" >/dev/null 2>&1 || echo "  WARNING: no 'gz sim' process found — is Gazebo running? Bridge will wait for poses."

# quick connectivity gate
ping -I "$UE_ALPHA_TUN" -c 2 -W 2 "$BRAVO_IP" >/dev/null 2>&1 || { echo "FATAL: alpha->bravo ping failed pre-flight"; exit 1; }
echo "  connectivity OK"

# record starting RNTIs for churn detection later
grep -oP "UE \K[0-9a-f]{4}" "$GNB_LOG" 2>/dev/null | sort -u > "$RUN_DIR/rntis_before.txt"
echo "  starting RNTIs: $(tr '\n' ' ' < "$RUN_DIR/rntis_before.txt")"

# snapshot configs + run metadata
cp ~/sar_sim/oai_configs/*.conf "$RUN_DIR/" 2>/dev/null
{
  echo "start: $(date -Iseconds)"
  echo "duration_s: $DURATION"
  echo "alpha_ip: $ALPHA_IP"
  echo "bravo_ip: $BRAVO_IP"
  echo "iperf: ${IPERF_BURST_S}s burst every ${IPERF_PERIOD_S}s, alternating direction"
} > "$RUN_DIR/run_meta.txt"

# ---------- Collectors ------------------------------------------------------
echo "== Starting collectors (run dir: $RUN_DIR) =="

# 1. channel bridge (it writes its own CSV; we also keep stdout)
python3 "$BRIDGE_SCRIPT" > "$RUN_DIR/bridge_stdout.log" 2>&1 &
PIDS+=($!)
echo "  [1] channel_bridge.py (pid ${PIDS[-1]})"

# 2. continuous ping alpha -> bravo, 1 Hz, timestamped CSV
(
  echo "timestamp,seq,rtt_ms" > "$RUN_DIR/ping_a2b.csv"
  ping -I "$UE_ALPHA_TUN" -i 1 "$BRAVO_IP" 2>/dev/null | while read -r line; do
    if [[ "$line" =~ icmp_seq=([0-9]+).*time=([0-9.]+) ]]; then
      echo "$(date -Iseconds),${BASH_REMATCH[1]},${BASH_REMATCH[2]}" >> "$RUN_DIR/ping_a2b.csv"
    fi
  done
) &
PIDS+=($!)
echo "  [2] 1 Hz ping alpha->bravo (pid ${PIDS[-1]})"

# 3. iperf3 servers (device-bound so replies go over the air, not loopback)
iperf3 -s -B "$BRAVO_IP" --bind-dev "$UE_BRAVO_TUN" -p "$IPERF_PORT" --logfile "$RUN_DIR/iperf_server_bravo.log" -D
iperf3 -s -B "$ALPHA_IP" --bind-dev "$UE_ALPHA_TUN" -p "$IPERF_PORT" --logfile "$RUN_DIR/iperf_server_alpha.log" -D
echo "  [3] iperf3 servers up on both UEs (port $IPERF_PORT, device-bound)"

# 4. iperf3 burst scheduler: 10 s TCP test every 60 s, alternating direction
(
  echo "timestamp,direction,mbps,retransmits" > "$RUN_DIR/iperf_bursts.csv"
  n=0
  while true; do
    if (( n % 2 == 0 )); then
      DIR="a2b"; SRC_IP="$ALPHA_IP"; SRC_DEV="$UE_ALPHA_TUN"; DST_IP="$BRAVO_IP"
    else
      DIR="b2a"; SRC_IP="$BRAVO_IP"; SRC_DEV="$UE_BRAVO_TUN"; DST_IP="$ALPHA_IP"
    fi
    TS=$(date -Iseconds)
    J=$(iperf3 -c "$DST_IP" -B "$SRC_IP" --bind-dev "$SRC_DEV" -b "$IPERF_RATE_CAP" \
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
    # loopback-leak guard: a 20 MHz link cannot exceed ~100 Mbps; if it does,
    # traffic bypassed the air interface — mark invalid rather than record garbage
    VAL=$(echo "$MBPS" | cut -d, -f1)
    if python3 -c "import sys; sys.exit(0 if float('$VAL') > $LOOPBACK_SUSPECT_MBPS else 1)" 2>/dev/null; then
      echo "$TS,$DIR,LOOPBACK_LEAK,$VAL" >> "$RUN_DIR/iperf_bursts.csv"
    else
      echo "$TS,$DIR,$MBPS" >> "$RUN_DIR/iperf_bursts.csv"
    fi
    n=$((n+1))
    sleep $((IPERF_PERIOD_S - IPERF_BURST_S))
  done
) &
PIDS+=($!)
echo "  [4] iperf3 burst scheduler (pid ${PIDS[-1]})"

# 5. SINR/RSRP sampler, 2 s, both UEs, wall-clock stamped
(
  echo "timestamp,ue,rsrp_dbm,sinr_db" > "$RUN_DIR/sinr_samples.csv"
  while true; do
    TS=$(date -Iseconds)
    for pair in "alpha:$UE_ALPHA_LOG" "bravo:$UE_BRAVO_LOG"; do
      NAME="${pair%%:*}"; LOG="${pair##*:}"
      LINE=$(grep -oP 'SS-RSRP: \K[-\d]+(?= dBm/RE).*SS-SINR: [-\d.]+' "$LOG" 2>/dev/null | tail -1)
      RSRP=$(grep -oP 'SS-RSRP: \K[-\d]+(?= dBm/RE)' "$LOG" 2>/dev/null | tail -1)
      SINR=$(grep -oP 'SS-SINR: \K[-\d.]+(?= dB)' "$LOG" 2>/dev/null | tail -1)
      [[ -n "$SINR" ]] && echo "$TS,$NAME,${RSRP:-},${SINR}" >> "$RUN_DIR/sinr_samples.csv"
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
    echo "SOAK TEST SUMMARY — $(date -Iseconds)"
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
      echo "RNTI churn: DETECTED — an RLF/re-attach happened during the run"
    fi
    echo ""

    echo "== Sync-loss sentinels (SINR <= -70 dB) during run =="
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

    echo "== Ping alpha->bravo =="
    if [[ -f "$RUN_DIR/ping_a2b.csv" ]]; then
      N=$(($(wc -l < "$RUN_DIR/ping_a2b.csv") - 1))
      EXPECTED=$DURATION
      awk -F, 'NR>1 {s+=$3; n++; if(min==""||$3<min)min=$3; if(max==""||$3>max)max=$3} \
        END{if(n>0) printf "replies=%d min=%.1fms max=%.1fms mean=%.1fms\n", n, min, max, s/n}' \
        "$RUN_DIR/ping_a2b.csv"
      echo "approx loss: $(( (EXPECTED - N) > 0 ? (EXPECTED - N) * 100 / EXPECTED : 0 ))% (expected ~${EXPECTED} @1Hz, got $N)"
    fi
    echo ""

    echo "== iperf3 throughput (Mbps per 10s burst) =="
    if [[ -f "$RUN_DIR/iperf_bursts.csv" ]]; then
      for d in a2b b2a; do
        awk -F, -v dir="$d" '$2==dir && $3>0 {s+=$3; n++; if(min==""||$3<min)min=$3; if(max==""||$3>max)max=$3} \
          END{if(n>0) printf "%s: bursts=%d min=%.2f max=%.2f mean=%.2f Mbps\n", dir, n, min, max, s/n; \
              else print dir": no successful bursts"}' "$RUN_DIR/iperf_bursts.csv"
      done
      FAILED=$(awk -F, 'NR>1 && $3=="-1.000" {c++} END{print c+0}' "$RUN_DIR/iperf_bursts.csv")
      LEAKED=$(awk -F, 'NR>1 && $3=="LOOPBACK_LEAK" {c++} END{print c+0}' "$RUN_DIR/iperf_bursts.csv")
      echo "failed bursts: $FAILED   loopback-leak bursts (invalid, bypassed air): $LEAKED"
    fi
    echo ""
    echo "Files: sinr_samples.csv, ping_a2b.csv, iperf_bursts.csv, gnb_stats.log,"
    echo "       bridge_stdout.log, + channel_bridge_log.csv (bridge's own, in ~/sar_sim/logs/)"
  } | tee "$S"
}

# ---------- Run -------------------------------------------------------------
echo ""
echo "== Running for ${DURATION}s ($(( DURATION / 60 )) min) — Ctrl+C to stop early (summary still written) =="
END_TS=$(( $(date +%s) + DURATION ))
while (( $(date +%s) < END_TS )); do
  sleep 30
  REMAIN=$(( END_TS - $(date +%s) ))
  LAST_SINR=$(awk -F, '$2=="alpha"' "$RUN_DIR/sinr_samples.csv" 2>/dev/null | tail -1 | cut -d, -f4)
  LAST_MBPS=$(tail -1 "$RUN_DIR/iperf_bursts.csv" 2>/dev/null | cut -d, -f3)
  echo "$(date +%H:%M:%S)  remaining ${REMAIN}s | alpha SINR ${LAST_SINR:-?} dB | last burst ${LAST_MBPS:-?} Mbps"
done
echo "Duration reached."
# cleanup + summary run via EXIT trap
