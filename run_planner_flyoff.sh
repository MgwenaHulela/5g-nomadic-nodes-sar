#!/usr/bin/env bash
# Phase 5 fly-off: run the planner policy on all four worlds with the SAME
# seeds as the most recent `excite` runs, so the comparison is matched.
#
# Baseline for comparison (already on disk, 5400 s each, excite):
#   open_field 1792965139  campaign_20261025_235902
#   urban      1792965140  campaign_20261026_013033
#   forest     1792965141  campaign_20261026_030216
#   hills      1792965142  campaign_20261026_043517
#
# No OAI/Open5GS/Gazebo configuration is changed. The planner's surrogate uses
# geometry, gNB link adaptation, action context and C2 history -- not the RSRP
# group -- so phy_log_level stays at "error" and nothing needs restoring
# afterwards.
#
# A 300 s smoke run gates the campaign: if the planner does not actually steer
# (no climb/descend/step actions logged) the run stops rather than burning six
# hours collecting excitation data under a planner label.
set -uo pipefail
cd "$(dirname "$0")"

DUR="${FLYOFF_DUR:-5400}"
LOG="logs/planner_flyoff_$(date +%Y%m%d_%H%M%S).log"
log(){ echo "[$(date +%F_%T)] $*" | tee -a "$LOG"; }

declare -A SEED=( [sar_open_field]=1792965139 [sar_urban]=1792965140
                  [sar_forest]=1792965141   [sar_hills]=1792965142 )
WORLDS=(sar_open_field sar_urban sar_forest sar_hills)

log "=== Phase 5 fly-off: planner policy, ${#WORLDS[@]} worlds x ${DUR}s ==="
for w in "${WORLDS[@]}"; do log "  $w  seed=${SEED[$w]}"; done

# ---- smoke test ---------------------------------------------------------
log "--- smoke test (300 s, sar_open_field) ---"
SAR_POLICY=planner SAR_SEED=${SEED[sar_open_field]} \
  ./run_research_campaign.sh 300 0 sar_open_field >>"$LOG" 2>&1
SMOKE=$(ls -dt logs/campaign_* 2>/dev/null | head -1)
ACTS="$SMOKE/sar_open_field_actions.csv"
if [ ! -f "$ACTS" ]; then
  log "FATAL: smoke run produced no action log ($ACTS) — aborting"; exit 1
fi
STEER=$(awk -F, 'NR>1 && ($7=="climb"||$7=="descend"||$7=="step")' "$ACTS" | wc -l)
TOTAL=$(( $(wc -l < "$ACTS") - 1 ))
RLOG="$SMOKE/sar_open_field_research.log"
log "smoke: $SMOKE  actions=$TOTAL steering=$STEER"

# Positive confirmation that the planner LOADED, not just that the UAV moved:
# the excitation fallback also emits climb/descend/step, so counting steering
# actions alone cannot tell the two apart. The first fly-off attempt fell back
# to excitation on a pandas import error and was caught only by luck.
if grep -q "planner unavailable" "$RLOG" 2>/dev/null; then
  log "FATAL: planner failed to load — fell back to excitation. Reason:"
  grep "planner unavailable" "$RLOG" | tail -2 | tee -a "$LOG"
  exit 1
fi
if ! grep -q "\[planner\] surrogate held out" "$RLOG" 2>/dev/null; then
  log "FATAL: no planner start line in $RLOG — aborting"
  exit 1
fi
log "confirmed: planner loaded and is steering"
if [ "$STEER" -lt 1 ]; then
  log "FATAL: planner loaded but never steered — aborting"
  exit 1
fi
log "planner is steering — proceeding with ${#WORLDS[@]} x ${DUR}s"

# ---- the fly-off --------------------------------------------------------
i=0
for w in "${WORLDS[@]}"; do
  i=$((i+1))
  log "--- world $i/${#WORLDS[@]}: $w (${DUR}s, planner, seed=${SEED[$w]}) ---"
  SAR_POLICY=planner SAR_SEED=${SEED[$w]} \
    ./run_research_campaign.sh "$DUR" 0 "$w" >>"$LOG" 2>&1
  rc=$?
  log "$w finished (rc=$rc)"
done
log "=== Phase 5 fly-off COMPLETE ==="
