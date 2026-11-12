#!/usr/bin/env bash
# Final-paper repeats: every arm x 3 seeds in forest and open field, all with the
# adaptive uplink cap and the cautious altitude learner for C/D.
#
#   arm A  fixed hover        xy=hold      alt=fixed 40 m
#   arm B  centroid           xy=centroid  alt=fixed 40 m   (proposal baseline)
#   arm C  centroid + learned xy=centroid  alt=bandit (cautious)
#   arm D  model + learned    xy=model     alt=bandit (cautious, linear_geo surrogate)
#
# Forest seed 1794000001 already exists (capped A/B 2026-11-12 00:24 runner,
# cautious C/D 10:27 runner), so forest flies seeds ...02 and ...03 here; open field
# flies all three. Within a seed every arm shares the seed; worlds alternate and
# arm order is shuffled per block so arm is not confounded with time or stack drift.
#
# Stop cleanly between runs: touch logs/final_repeats.STOP
set -uo pipefail
cd "$(dirname "$0")"

DUR="${FLYOFF_DUR:-5400}"
LOG="logs/final_repeats_$(date +%Y%m%d_%H%M%S).log"
log(){ echo "[$(date +%F_%T)] $*" | tee -a "$LOG"; }

declare -A XY=([A]=hold [B]=centroid [C]=centroid [D]=model)
declare -A ALT=([A]=fixed [B]=fixed [C]=bandit [D]=bandit)
declare -A MODEL=([A]=single [B]=single [C]=geo [D]=geo)
# world seed arm
ORDER=(
  "sar_open_field 1794000001 A" "sar_open_field 1794000001 C" "sar_open_field 1794000001 B" "sar_open_field 1794000001 D"
  "sar_forest 1794000002 B"     "sar_forest 1794000002 D"     "sar_forest 1794000002 A"     "sar_forest 1794000002 C"
  "sar_open_field 1794000002 D" "sar_open_field 1794000002 B" "sar_open_field 1794000002 C" "sar_open_field 1794000002 A"
  "sar_forest 1794000003 C"     "sar_forest 1794000003 A"     "sar_forest 1794000003 D"     "sar_forest 1794000003 B"
  "sar_open_field 1794000003 A" "sar_open_field 1794000003 D" "sar_open_field 1794000003 C" "sar_open_field 1794000003 B"
)

log "=== final repeats: ${#ORDER[@]} runs x ${DUR}s, cap adaptive, learner cautious ==="
i=0
for spec in "${ORDER[@]}"; do
  i=$((i+1)); set -- $spec; W=$1; SEED=$2; ARM=$3
  if [ -e logs/final_repeats.STOP ]; then log "STOP file found — stopping before run $i"; break; fi
  log "--- run $i/${#ORDER[@]}: arm $ARM ($W) seed $SEED xy=${XY[$ARM]} alt=${ALT[$ARM]} model=${MODEL[$ARM]} ---"
  SAR_POLICY=planner SAR_SEED=$SEED \
  SAR_PLANNER_XY=${XY[$ARM]} SAR_PLANNER_ALT=${ALT[$ARM]} \
  SAR_PLANNER_MODEL=${MODEL[$ARM]} SAR_PLANNER_ALT_LEARNER=cautious SAR_UL_CAP=adaptive \
    ./run_research_campaign.sh "$DUR" 0 "$W" >>"$LOG" 2>&1
  rc=$?
  D=$(ls -dt logs/campaign_* 2>/dev/null | head -1)
  R="$D/${W}_research.log"
  ROWS=$(wc -l < "$D/$W.csv" 2>/dev/null || echo 0)
  log "  done (rc=$rc) dir=$D arm=$ARM seed=$SEED rows=$ROWS"
  if grep -q "\[planner\] xy: ${XY[$ARM]}" "$R" 2>/dev/null \
     && grep -q "\[planner\] altitude: .*$( [ "${ALT[$ARM]}" = bandit ] && echo 'learned on the spot (cautious' || echo 'fixed')" "$R"; then
    log "  arm $ARM confirmed in planner log"
  else
    log "  *** arm $ARM NOT confirmed in $R — run is NOT COMPARABLE ***"
  fi
  # csv.writer ends rows with \r\n: strip it or the $ anchors never match
  CAPN=$(tr -d '\r' < "$D/${W}_ulcap.csv" 2>/dev/null | grep -c ',cut$\|,up$\|,up_x2$\|,at_max$\|,hold$')
  if [ "${CAPN:-0}" -gt 1000 ]; then
    log "  uplink cap active: $CAPN controller rows in ${W}_ulcap.csv"
  else
    log "  *** uplink cap NOT active (${CAPN:-0} rows) — run is NOT COMPARABLE ***"
  fi
  log "  watchdog: recovered=$(grep -c 'rlf_recovered after' "$R" 2>/dev/null)" \
      "failed=$(grep -c 'reattach_failed after' "$R" 2>/dev/null)" \
      "self_recovered=$(grep -c 'recovered on its own' "$R" 2>/dev/null)" \
      "aborts=$(grep -c 'ABORTING' "$R" 2>/dev/null)" \
      "crash_relaunches=$(grep -c 'UE process died during attach' "$R" 2>/dev/null)"
  echo "$i,$ARM,$W,$SEED,$D,$rc,$ROWS" >> "${LOG%.log}_runs.csv"
done
log "=== final repeats COMPLETE — runs listed in ${LOG%.log}_runs.csv ==="
