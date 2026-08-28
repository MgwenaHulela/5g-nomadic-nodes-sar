#!/usr/bin/env bash
#
# run_research_campaign.sh — run the --research two-phase experiment
# (static gNB then nomadic gNB) across every terrain, back to back.
#
# For each world it: (re)starts the full stack in that world, runs the
# research patrol for the given duration, copies the resulting CSV into a
# per-campaign folder tagged with the world name, and records a manifest
# row. A failed world is logged and skipped — the campaign continues.
#
# Usage:
#   export SAR_SUDO_PASS='...'                     # start_full_stack needs root
#   ./run_research_campaign.sh [duration_s] [phase1_s] [world ...]
#
#   ./run_research_campaign.sh                     # FULL: 6h (3h+3h) x 5 worlds
#   ./run_research_campaign.sh 300 120             # SMOKE: 5min (2+3) x 5 worlds
#   ./run_research_campaign.sh 21600 10800 sar_indoor   # one world, full
#
# Unattended (survives logout):
#   setsid nohup ./run_research_campaign.sh >logs/campaign.out 2>&1 < /dev/null &
#
set -uo pipefail
SELF="$(cd "$(dirname "$0")" && pwd)"
cd "$SELF"

if [ "$#" -ge 2 ]; then DUR="$1"; P1="$2"; shift 2; else DUR=21600; P1=10800; fi
if [ "$#" -ge 1 ]; then WORLDS=("$@"); else
  WORLDS=(sar_open_field sar_urban sar_forest sar_hills sar_indoor); fi

if [ -z "${SAR_SUDO_PASS:-}" ] && ! sudo -n true 2>/dev/null; then
  echo "FATAL: export SAR_SUDO_PASS (start_full_stack.sh needs root)"; exit 1
fi

# Single-campaign lock. TWO campaigns share ONE physical stack (Gazebo, RAN,
# robots, bridge) and silently corrupt each other's data — both drive the same
# robots and gNB. (Learned the hard way 2026-07-28: a duplicate launch
# contaminated a 3h run.) PID-file lock, not flock: start_full_stack backgrounds
# daemons (gz/bridge/RAN) that would inherit an flock fd and hold the lock long
# after the campaign exits. A PID file we check with `kill -0` self-heals from a
# crashed run (stale PID isn't alive) and isn't inherited by the daemons.
LOCK=/tmp/sar_research_campaign.pid
if [ -f "$LOCK" ] && kill -0 "$(cat "$LOCK" 2>/dev/null)" 2>/dev/null; then
  echo "FATAL: another research campaign is already running (pid $(cat "$LOCK"))."
  echo "       Wait for it to finish, or stop it first:"
  echo "         pkill -f '[r]un_research_campaign'"
  exit 1
fi
echo "$$" > "$LOCK"
trap 'rm -f "$LOCK"' EXIT

STAMP=$(date +%Y%m%d_%H%M%S)
OUT="$SELF/logs/campaign_$STAMP"
mkdir -p "$OUT"
MANIFEST="$OUT/manifest.csv"
echo "world,status,csv,rows,started,ended" > "$MANIFEST"
CLOG="$OUT/campaign.log"
log(){ echo "[$(date +%F_%T)] $*" | tee -a "$CLOG"; }

log "campaign start: dur=${DUR}s phase1=${P1}s worlds=[${WORLDS[*]}] -> $OUT"

for W in "${WORLDS[@]}"; do
  log "=== $W: bringing up stack ==="
  if ! timeout 900 env SAR_WORLD="$W" bash start_full_stack.sh \
         > "$OUT/${W}_stackup.log" 2>&1; then
    log "  $W STACK BRINGUP FAILED (see ${W}_stackup.log) — skipping"
    echo "$W,stack_failed,,0,$(date +%F_%T)," >> "$MANIFEST"
    continue
  fi
  log "  $W stack up — starting research run (dur=${DUR}s, phase1=${P1}s)"
  started=$(date +%F_%T)
  timeout $((DUR + 600)) env SAR_WORLD="$W" \
      python3 scripts/auto_patrol.py --research --duration="$DUR" --phase1="$P1" \
      > "$OUT/${W}_research.log" 2>&1
  rc=$?
  ended=$(date +%F_%T)
  csv=$(ls -t logs/patrol_*.csv 2>/dev/null | head -1)
  if [ -n "$csv" ]; then
    dest="$OUT/${W}.csv"; cp "$csv" "$dest"
    rows=$(( $(wc -l < "$dest") - 1 ))
    log "  $W DONE (rc=$rc) — $rows rows -> ${W}.csv"
    echo "$W,ok,${W}.csv,$rows,$started,$ended" >> "$MANIFEST"
  else
    log "  $W produced NO csv (rc=$rc)"
    echo "$W,no_csv,,0,$started,$ended" >> "$MANIFEST"
  fi
done

log "=== CAMPAIGN COMPLETE -> $OUT ==="
column -t -s, "$MANIFEST" | tee -a "$CLOG"
