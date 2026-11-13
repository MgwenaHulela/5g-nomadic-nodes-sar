#!/usr/bin/env bash
# Prober fly-off: the two learned-altitude arms of the x-y-z fly-off, re-flown
# with AltitudeProber (docs/ALTITUDE_LEARNER_REPORT.md) in place of the
# raw-mean bandit. Same seed, worlds and duration as run_xyz_flyoff.sh.
#
#   arm A  fixed hover        xy=hold      alt=fixed 40 m
#   arm B  centroid           xy=centroid  alt=fixed 40 m   (proposal baseline)
#   arm C  centroid + learned xy=centroid  alt=bandit
#   arm D  model + learned    xy=model     alt=bandit  (linear_geo surrogate)
#
# Every arm runs through the SAME planner pilot (SAR_POLICY=planner), so the
# dwell, rate limit, geofence and logging are identical; arms differ only in
# how x-y and z are chosen. Same seed everywhere. Order alternates worlds and
# shuffles arms so arm is not confounded with time of day or stack drift.
#
# Each run is checked after it finishes: the research log must show the
# planner lines for exactly that arm, or the run is flagged NOT COMPARABLE.
set -uo pipefail
cd "$(dirname "$0")"

DUR="${FLYOFF_DUR:-5400}"
SEED=1794000001
LOG="logs/prober_flyoff_$(date +%Y%m%d_%H%M%S).log"
log(){ echo "[$(date +%F_%T)] $*" | tee -a "$LOG"; }

declare -A XY=([A]=hold [B]=centroid [C]=centroid [D]=model)
declare -A ALT=([A]=fixed [B]=fixed [C]=bandit [D]=bandit)
declare -A MODEL=([A]=single [B]=single [C]=geo [D]=geo)
ORDER=("sar_open_field C" "sar_forest D" "sar_forest C" "sar_open_field D")

log "=== prober fly-off: ${#ORDER[@]} runs x ${DUR}s, seed $SEED ==="
i=0
for spec in "${ORDER[@]}"; do
  i=$((i+1)); set -- $spec; W=$1; ARM=$2
  log "--- run $i/${#ORDER[@]}: arm $ARM ($W) xy=${XY[$ARM]} alt=${ALT[$ARM]} model=${MODEL[$ARM]} ---"
  SAR_POLICY=planner SAR_SEED=$SEED \
  SAR_PLANNER_XY=${XY[$ARM]} SAR_PLANNER_ALT=${ALT[$ARM]} \
  SAR_PLANNER_MODEL=${MODEL[$ARM]} SAR_PLANNER_ALT_LEARNER=prober \
    ./run_research_campaign.sh "$DUR" 0 "$W" >>"$LOG" 2>&1
  rc=$?
  D=$(ls -dt logs/campaign_* 2>/dev/null | head -1)
  R="$D/${W}_research.log"
  ROWS=$(wc -l < "$D/$W.csv" 2>/dev/null || echo 0)
  log "  done (rc=$rc) dir=$D arm=$ARM rows=$ROWS"
  if grep -q "\[planner\] xy: ${XY[$ARM]}" "$R" 2>/dev/null \
     && grep -q "\[planner\] altitude: .*$( [ "${ALT[$ARM]}" = bandit ] && echo 'learned on the spot (prober' || echo 'fixed')" "$R"; then
    log "  arm $ARM confirmed in planner log"
  else
    log "  *** arm $ARM NOT confirmed in $R — run is NOT COMPARABLE ***"
  fi
  log "  watchdog: recovered=$(grep -c 'rlf_recovered after' "$R" 2>/dev/null)" \
      "failed=$(grep -c 'reattach_failed after' "$R" 2>/dev/null)" \
      "self_recovered=$(grep -c 'recovered on its own' "$R" 2>/dev/null)" \
      "aborts=$(grep -c 'ABORTING' "$R" 2>/dev/null)" \
      "crash_relaunches=$(grep -c 'UE process died during attach' "$R" 2>/dev/null)"
  echo "$i,$ARM,$W,$D,$rc,$ROWS" >> "${LOG%.log}_runs.csv"
done
log "=== prober fly-off COMPLETE — runs listed in ${LOG%.log}_runs.csv ==="
