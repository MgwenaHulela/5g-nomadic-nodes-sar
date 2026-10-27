#!/usr/bin/env bash
# Phase 5 fly-off: run the planner policy on all four worlds.
#
# ROUND 2 — the objective is now a global argmin over ABSOLUTE altitude targets
# with a rate-limited move, not a relative hill-climb. Round 1 ratcheted into
# the 60 m ceiling in every terrain (74-96 % of the time) and lost 12.0 pp in
# forest for climbing where Phase 4 says not to. See "WHY ABSOLUTE TARGETS" in
# scripts/ml/planner.py and CAMPAIGN_STATUS section 4.3.12.
#
# NOTE ON "MATCHED SEEDS": the seeds below are reused, but a replayed seed does
# NOT replay the robot paths — the robot walk is closed-loop on the UAV
# (auto_patrol.py:2415-2460), so changing the gNB policy changes which guard
# fires and how far rng_bot is consumed. These are four independent runs of a
# closed-loop system, and the analysis must control for robot geometry rather
# than assume pairing. Round 1 measured robot-x correlation of +0.01..+0.67
# against the matched-seed baseline.
#
# Comparators already on disk (5400 s each):
#   round 1, planner with the RELATIVE objective, 2026-10-26/27:
#     open_field campaign_20261026_222941   urban campaign_20261027_000120
#     forest     campaign_20261027_013304   hills campaign_20261027_030546
#   excite baseline:
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
# Round 2 gate: confirm the ABSOLUTE-target objective is the one flying. Without
# this the run would silently repeat round 1 if an import resolved to a stale
# planner module.
if ! grep -q "absolute z targets" "$RLOG" 2>/dev/null; then
  log "FATAL: planner loaded but is NOT using absolute altitude targets —"
  log "       this would repeat round 1. Start line was:"
  grep "\[planner\] surrogate held out" "$RLOG" | tail -1 | tee -a "$LOG"
  exit 1
fi
log "confirmed: planner loaded, using absolute altitude targets, and steering"
grep "absolute z targets" "$RLOG" | tail -1 | sed 's/^/    /' | tee -a "$LOG"
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
