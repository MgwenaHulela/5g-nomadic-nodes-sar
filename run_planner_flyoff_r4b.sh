#!/usr/bin/env bash
# Round 4b: re-fly the open-field pair with a working RLF watchdog.
#
# WHY. scripts/auto_patrol.py's UE_LOG_OF was alpha/bravo-only, so the first
# time charlie or delta needed a relaunch the watchdog THREAD died with
# KeyError and nothing was relaunched for the rest of the run. Round 4 runs 1
# (planner) and 2 (excite) in open field both lost their watchdog this way;
# the one-line fix landed at 2026-11-09 ~20:10, so runs 3-6 have it.
#
# This script flies the open-field planner/excite pair again on the SAME seed
# with the fix in place, so every round-4 comparison has a working-watchdog
# pair. Runs 1-2 are kept, not discarded: as a broken-watchdog pair they
# measure what the bug cost, which the October data (20 of 24 runs affected)
# needs a correction for.
#
# It waits for round 4 to finish and release the campaign lock first -- two
# campaigns on one stack corrupt each other's data. It then audits every
# round-4 run for watchdog death, so the morning report does not depend on
# anyone remembering to grep.
set -uo pipefail
cd "$(dirname "$0")"

DUR="${FLYOFF_DUR:-5400}"
SEED_A=1794000001
R4LOG=$(ls -t logs/planner_flyoff_r4_2*.log 2>/dev/null | head -1)
LOG="logs/planner_flyoff_r4b_$(date +%Y%m%d_%H%M%S).log"
LOCK=/tmp/sar_research_campaign.pid
log(){ echo "[$(date +%F_%T)] $*" | tee -a "$LOG"; }

log "=== Round 4b: open-field re-fly with the watchdog fix ==="
log "waiting for round 4 ($R4LOG) to finish"

# ---- wait for round 4 ---------------------------------------------------
while pgrep -f '[r]un_planner_flyoff_r4\.sh' >/dev/null; do sleep 60; done
if grep -q "Round 4 fly-off COMPLETE" "$R4LOG" 2>/dev/null; then
  log "round 4 reported COMPLETE"
else
  log "WARNING: round 4 launcher exited WITHOUT its COMPLETE line — proceeding,"
  log "         but check $R4LOG before trusting runs 3-6"
fi
# the per-run lock is released by run_research_campaign's EXIT trap
for _ in $(seq 1 60); do
  if [ -f "$LOCK" ] && kill -0 "$(cat "$LOCK" 2>/dev/null)" 2>/dev/null; then
    sleep 10
  else
    break
  fi
done
if [ -f "$LOCK" ] && kill -0 "$(cat "$LOCK" 2>/dev/null)" 2>/dev/null; then
  log "FATAL: campaign lock still held by pid $(cat "$LOCK") after 10 min"
  exit 1
fi

# ---- the fix must be present --------------------------------------------
if ! grep -q 'UE_LOG_OF = {ROBOT_SPEC\[r\]\["model"\]' scripts/auto_patrol.py; then
  log "FATAL: roster-driven UE_LOG_OF not found in auto_patrol.py — refusing"
  log "       to fly a 'fixed' pair that is not fixed"
  exit 1
fi
log "watchdog fix confirmed in scripts/auto_patrol.py"

# ---- the two runs -------------------------------------------------------
i=0
for pol in planner excite; do
  i=$((i+1))
  log "--- run $i/2: $pol sar_open_field seed=$SEED_A (A, watchdog fixed), ${DUR}s ---"
  SAR_POLICY="$pol" SAR_SEED="$SEED_A" \
    ./run_research_campaign.sh "$DUR" 0 sar_open_field >>"$LOG" 2>&1
  rc=$?
  D=$(ls -dt logs/campaign_* 2>/dev/null | head -1)
  ROWS=$(wc -l < "$D/sar_open_field.csv" 2>/dev/null || echo 0)
  log "  done (rc=$rc) dir=$D rows=$ROWS"
  if [ "$pol" = planner ]; then
    if grep -q "onset_censored_h20_logisticL1_isotonic" \
         "$D/sar_open_field_research.log" 2>/dev/null; then
      log "  planner loaded the round-4 surrogate"
    else
      log "  *** planner did NOT load the round-4 surrogate — run is not comparable ***"
    fi
  fi
done

# ---- audit every round-4 run --------------------------------------------
log "=== watchdog audit, all round-4 runs ==="
for D in $(grep -ohE "dir=logs/campaign_[0-9_]+" "$R4LOG" "$LOG" | cut -d= -f2); do
  for R in "$D"/sar_*_research.log; do
    [ -f "$R" ] || continue
    died=$(grep -m1 -oE "KeyError: 'sar_ground_[a-z]+'" "$R")
    recov=$(grep -c "rlf_recovered" "$R")
    pol=$(cut -d, -f9 "$D/manifest.csv" 2>/dev/null | tail -1)
    log "  $D  policy=${pol:-?}  recoveries=$recov  ${died:+WATCHDOG DIED ($died)}"
  done
done
log "=== Round 4b COMPLETE ==="
