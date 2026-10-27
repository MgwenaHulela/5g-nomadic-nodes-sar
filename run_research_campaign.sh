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
#   SAR_SEED=1000 ./run_research_campaign.sh        # replay campaign seed 1000
#   SAR_FAST_RATE=20 ./run_research_campaign.sh     # 20 Hz radio sampling
#   SAR_POLICY=excite ./run_research_campaign.sh 3600 0   # 1h excitation/world
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

# Base RNG seed for the campaign. Each world gets BASE_SEED + its index, so a
# campaign is reproducible end to end and two campaigns with different bases
# never collide. Export SAR_SEED to replay a previous campaign's motion.
#   SAR_SEED=1000 ./run_research_campaign.sh      # replay campaign 1000
BASE_SEED="${SAR_SEED:-$(date +%s)}"

# Samples per second for the high-cadence radio log. The underlying
# SS-RSRP/SS-SINR stream from nr-uesoftmodem is 50 Hz (DATA_AUDIT.md 4.1b);
# 10 Hz keeps the file manageable while still resolving the 1-10 s horizons
# the per-cycle CSV cannot (its median cycle is 22.8 s).
FAST_RATE="${SAR_FAST_RATE:-10}"

# gNB behaviour policy. "two_phase" reproduces the published SATNAC campaign
# (3 h hold, then 3 h random walk). "excite" runs the excitation policy
# (brief 6.1) — randomised labelled actions with dwells — which is what the
# action-conditioned forecaster needs and what the two-phase data cannot
# provide, because there `hold` only ever happens at one pose.
#   SAR_POLICY=excite ./run_research_campaign.sh 3600 0
POLICY="${SAR_POLICY:-two_phase}"

# Mixed-dwell collection. For a multi-seed excitation campaign the FIRST seed
# runs the long dwell, so every slow measurement cycle sits inside one action
# and the per-cycle throughput/C2 columns stay cleanly attributable. Later
# seeds run the short dwell, roughly tripling labelled actions per hour for
# the surrogate, which trains off the 10 Hz log where action boundaries are
# exact regardless of dwell. Set SAR_DWELL to force one band for every seed.
#   SAR_DWELL=15,35  -> short for all seeds
DWELL_LONG="${SAR_DWELL_LONG:-30,90}"
DWELL_SHORT="${SAR_DWELL_SHORT:-15,35}"
case "$POLICY" in
  two_phase) PATROL_MODE="--research" ;;
  excite)    PATROL_MODE="--excite" ;;
  planner)   PATROL_MODE="--planner" ;;
  *) echo "FATAL: SAR_POLICY must be two_phase, excite or planner (got '$POLICY')"; exit 1 ;;
esac

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
echo "world,status,csv,rows,started,ended,seed,run_id,policy" > "$MANIFEST"
CLOG="$OUT/campaign.log"
log(){ echo "[$(date +%F_%T)] $*" | tee -a "$CLOG"; }

log "campaign start: dur=${DUR}s phase1=${P1}s worlds=[${WORLDS[*]}] -> $OUT"
log "policy=$POLICY base_seed=$BASE_SEED fast_rate=${FAST_RATE}Hz (replay with SAR_SEED=$BASE_SEED)"

IDX=0
SEED_ROUND="${SAR_SEED_ROUND:-0}"   # 0 = first seed of a campaign (long dwell)
for W in "${WORLDS[@]}"; do
  # Fixed per-world seed, assigned BEFORE anything can fail. Deriving it from
  # a counter incremented after a successful run would mean one stack failure
  # silently shifts every later world's seed, so SAR_SEED would not replay.
  SEED=$((BASE_SEED + IDX))
  # run_id carries the campaign stamp so a replay of the same seed does not
  # collide with the earlier run's output files. Reproducibility lives in
  # SEED, which is recorded in the manifest and in every CSV row.
  RUN_ID="${W}_s${SEED}_${STAMP}"
  IDX=$((IDX + 1))
  # Seed index within this campaign decides the dwell band (see above).
  if [ -n "${SAR_DWELL:-}" ]; then
    DWELL="$SAR_DWELL"
  elif [ "$SEED_ROUND" -eq 0 ]; then
    DWELL="$DWELL_LONG"
  else
    DWELL="$DWELL_SHORT"
  fi
  log "=== $W: bringing up stack ==="
  if ! timeout 900 env SAR_WORLD="$W" bash start_full_stack.sh \
         > "$OUT/${W}_stackup.log" 2>&1; then
    log "  $W STACK BRINGUP FAILED (see ${W}_stackup.log) — skipping"
    echo "$W,stack_failed,,0,$(date +%F_%T),,$SEED,$RUN_ID,$POLICY" >> "$MANIFEST"
    continue
  fi
  if [ "$POLICY" = "excite" ]; then EXTRA_ARGS="--dwell=$DWELL"; else EXTRA_ARGS=""; fi
  log "  $W stack up — starting research run (dur=${DUR}s, phase1=${P1}s, seed=$SEED, run_id=$RUN_ID${EXTRA_ARGS:+, dwell=$DWELL})"
  started=$(date +%F_%T)

  # High-cadence radio sampler, alongside the patrol rather than inside it.
  # It only READS the UE logs the modems already write, so it changes nothing
  # about the stack; it exists because the patrol's own cycle is both too slow
  # (22.8 s median) and biased (slower when links are bad) to train on.
  # Started first so it is already sampling when the patrol's first cycle runs.
  python3 scripts/fast_radio_sampler.py --rate "$FAST_RATE" \
      --run-id "$RUN_ID" --seed "$SEED" --terrain "$W" \
      > "$OUT/${W}_fastradio.log" 2>&1 &
  FAST_PID=$!

  timeout $((DUR + 600)) env SAR_WORLD="$W" \
      python3 scripts/auto_patrol.py "$PATROL_MODE" --duration="$DUR" --phase1="$P1" \
      --seed="$SEED" --run-id="$RUN_ID" ${EXTRA_ARGS} \
      > "$OUT/${W}_research.log" 2>&1
  rc=$?
  ended=$(date +%F_%T)

  # SIGTERM so the sampler flushes and writes its closing summary.
  kill -TERM "$FAST_PID" 2>/dev/null
  wait "$FAST_PID" 2>/dev/null
  act_csv="logs/actions_${RUN_ID}.csv"
  if [ -f "$act_csv" ]; then
    cp "$act_csv" "$OUT/${W}_actions.csv"
    log "  $W actions: $(( $(wc -l < "$act_csv") - 1 )) actions -> ${W}_actions.csv"
  fi
  fast_csv="logs/fastradio_${RUN_ID}.csv"
  if [ -f "$fast_csv" ]; then
    cp "$fast_csv" "$OUT/${W}_fastradio.csv"
    log "  $W fast radio: $(( $(wc -l < "$fast_csv") - 1 )) rows -> ${W}_fastradio.csv"
  else
    log "  $W fast radio: NO CSV produced (see ${W}_fastradio.log)"
  fi

  csv=$(ls -t logs/patrol_*.csv 2>/dev/null | head -1)
  if [ -n "$csv" ]; then
    dest="$OUT/${W}.csv"; cp "$csv" "$dest"
    rows=$(( $(wc -l < "$dest") - 1 ))
    log "  $W DONE (rc=$rc) — $rows rows -> ${W}.csv"
    echo "$W,ok,${W}.csv,$rows,$started,$ended,$SEED,$RUN_ID,$POLICY" >> "$MANIFEST"
  else
    log "  $W produced NO csv (rc=$rc)"
    echo "$W,no_csv,,0,$started,$ended,$SEED,$RUN_ID,$POLICY" >> "$MANIFEST"
  fi
done

log "=== CAMPAIGN COMPLETE -> $OUT ==="
column -t -s, "$MANIFEST" | tee -a "$CLOG"
