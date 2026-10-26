#!/usr/bin/env bash
#
# run_full_campaign.sh — the multi-seed excitation campaign.
#
# run_research_campaign.sh makes ONE pass over the world list. The ML work
# needs several seeds per terrain (brief 10: multiple seeds with confidence
# intervals), so this drives one pass per seed round, sequentially — never in
# parallel, because every run shares one physical stack.
#
#   ./run_full_campaign.sh                  # 4 rounds x 4 terrains x 6 h
#   ./run_full_campaign.sh 4 21600          # rounds, seconds per run
#   SAR_BASE_SEED=1000 ./run_full_campaign.sh   # reproduce a past campaign
#
# Dwell comes from the seed round (run_research_campaign.sh): round 0 uses the
# long 30-90 s band so every per-cycle row sits inside one action, later rounds
# use 15-35 s to roughly triple labelled actions for the surrogate, which
# trains off the 10 Hz log where action boundaries are exact anyway.
#
# Terrains: sar_indoor is deliberately EXCLUDED. Four robots walking into the
# building produce ~253 s measurement cycles — too slow to be useful whatever
# the link quality — and its coverage is marginal even after the 2026-10-19
# gNB geometry retune. It is a separate study, not a campaign terrain.
set -uo pipefail
SELF="$(cd "$(dirname "$0")" && pwd)"
cd "$SELF"

ROUNDS="${1:-4}"
DUR="${2:-21600}"            # 6 h per run
P1=0                         # excitation has no static/nomadic split
BASE_SEED="${SAR_BASE_SEED:-$(date +%s)}"
WORLDS=(sar_open_field sar_urban sar_forest sar_hills)

STAMP=$(date +%Y%m%d_%H%M%S)
OUT="$SELF/logs/full_campaign_$STAMP"
mkdir -p "$OUT"
LOG="$OUT/full_campaign.log"
log(){ echo "[$(date +%F_%T)] $*" | tee -a "$LOG"; }

TOTAL_RUNS=$((ROUNDS * ${#WORLDS[@]}))
EST_H=$(python3 -c "print(f'{$TOTAL_RUNS*($DUR/3600+25/60):.1f}')")

log "FULL CAMPAIGN start"
log "  rounds=$ROUNDS  worlds=${WORLDS[*]}  dur=${DUR}s/run"
log "  runs=$TOTAL_RUNS  estimated ${EST_H} h  base_seed=$BASE_SEED"
log "  reproduce with: SAR_BASE_SEED=$BASE_SEED ./run_full_campaign.sh $ROUNDS $DUR"
log "  dashboard: python3 scripts/dashboard.py"

# Rounds ALTERNATE between two policies, because they answer different
# questions and a campaign needs both:
#
#   two_phase  the UAV HOLDS for phase1 seconds, then roams for the rest.
#              The `phase` column (0=static, 1=nomadic) makes "does roaming
#              actually help?" a within-run, same-seed, same-terrain
#              comparison. Excitation alone cannot answer this: it has no
#              static control, so a throughput difference cannot be
#              attributed to roaming rather than to where the robots were.
#
#   excite     randomised labelled actions, which is what the action-
#              conditioned forecaster and the planner train on.
#
# Alternating (not "all of one then all of the other") means a campaign that
# is cut short still carries both kinds of data for every terrain.
for ((r=0; r<ROUNDS; r++)); do
  SEED=$((BASE_SEED + r * 1000))
  if (( r % 2 == 0 )); then
    RPOLICY=two_phase
    RP1=$((DUR / 2))       # half static, half roaming
  else
    RPOLICY=excite
    RP1=0                  # excitation has no static/nomadic split
  fi
  log "=== round $((r+1))/$ROUNDS  policy=$RPOLICY  phase1=${RP1}s  seed_round=$r  base_seed=$SEED ==="
  SAR_POLICY="$RPOLICY" SAR_SEED="$SEED" SAR_SEED_ROUND="$r" \
    ./run_research_campaign.sh "$DUR" "$RP1" "${WORLDS[@]}" \
    >> "$OUT/round_$r.out" 2>&1
  rc=$?
  log "=== round $((r+1)) finished (rc=$rc) ==="
  # A round that dies should not silently take the rest of the campaign with
  # it, nor should it loop forever: report and carry on to the next seed.
  [ $rc -ne 0 ] && log "  WARNING: round $((r+1)) returned $rc — see round_$r.out"
done

log "FULL CAMPAIGN COMPLETE -> $OUT"
log "  per-round campaign dirs are under logs/campaign_*; this directory holds"
log "  the round driver logs. Collect results with scripts/derive_thresholds.py"
log "  --include-new and the per-campaign manifests."
