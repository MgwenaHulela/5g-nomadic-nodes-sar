#!/usr/bin/env bash
# Round 4 fly-off: the planner driven by the CORRECTED surrogate.
#
# Rounds 1-3 flew a surrogate trained under the contaminated protocol: all rows
# (not onset), dead-UE stretches included, 60 s horizon, LightGBM, +c2hist. The
# offline analysis has since replaced all four choices. Section VIII of the
# write-up therefore describes a model that no longer exists, and this campaign
# is what replaces those numbers.
#
# WHAT IS FLYING NOW (scripts/ml/train_surrogates.py):
#   onset task · dead-UE censored at t and t+h · horizon 20 s · L1 logistic
#   (C=0.1) + isotonic · radio_blind 65 features · leave-one-terrain-out
#
# WHAT THE CLOSED-LOOP TEST ALREADY PREDICTS. The surrogate's coefficient on
# uav_z_m is NEGATIVE in both folds (-0.473 forest, -0.182 open_field): higher
# is safer, everywhere, with no terrain conditioning. test_planner_loop.py
# accordingly settles at 55 m -- the grid ceiling -- in forest and open field
# alike, from either start altitude. So the expected outcome is open field
# holding up and forest degrading. Round 1 climbed in forest because of an
# objective bug; round 4 climbs there because the honest model believes it
# should. That distinction is the result, and it needs to be flown rather than
# argued, because the magnitude of the forest cost is what Section VIII reports.
#
# SCOPE: two worlds, not four. open_field and forest are where the measured
# altitude effect is strongest and opposite in sign, so they carry the contrast;
# hills and urban were shown uninterpretable by the same-policy null control and
# would cost half the night to re-confirm as such.
#
# ARMS (6 runs x 5400 s ~ 9 h):
#   planner A / excite A per world  -> the policy contrast
#   planner B per world             -> within-policy variance, i.e. how much of
#                                      any contrast is just run-to-run noise
# Ordered so the policy contrast in BOTH worlds completes first: if the night
# dies at run 4 the campaign still answers its main question.
#
# No OAI/Open5GS/Gazebo configuration is changed. The surrogate reads geometry,
# gNB link adaptation and action context -- not the RSRP group -- so
# phy_log_level stays at "error" and nothing needs restoring afterwards.
set -uo pipefail
cd "$(dirname "$0")"

DUR="${FLYOFF_DUR:-5400}"
STAMP=$(date +%Y%m%d_%H%M%S)
LOG="logs/planner_flyoff_r4_$STAMP.log"
log(){ echo "[$(date +%F_%T)] $*" | tee -a "$LOG"; }

SEED_A=1794000001
SEED_B=1794000002

# policy:world:seed:label
RUNS=(
  "planner:sar_open_field:$SEED_A:A"
  "excite:sar_open_field:$SEED_A:A"
  "planner:sar_forest:$SEED_A:A"
  "excite:sar_forest:$SEED_A:A"
  "planner:sar_open_field:$SEED_B:B"
  "planner:sar_forest:$SEED_B:B"
)

log "=== Round 4 fly-off: corrected surrogate, ${#RUNS[@]} runs x ${DUR}s ==="
for r in "${RUNS[@]}"; do
  IFS=: read -r pol w seed lab <<<"$r"
  log "  $pol $w seed=$seed ($lab)"
done

# ---- surrogate provenance ----------------------------------------------
# Refuse to fly if the artifacts on disk are not the corrected ones. The whole
# point of the round is which model flies, so this is checked before anything
# costs time.
PROTO=$(python3 -c "import json;print(json.load(open('logs/ml/surrogates/features.json'))['protocol'])" 2>/dev/null)
if [ "$PROTO" != "onset_censored_h20_logisticL1_isotonic" ]; then
  log "FATAL: surrogates are protocol '$PROTO', not the corrected one."
  log "       run: python3 scripts/ml/train_surrogates.py"
  exit 1
fi
if ls logs/ml/surrogates/surrogate_heldout_*.txt >/dev/null 2>&1; then
  log "FATAL: stale LightGBM surrogates (.txt) still present alongside .joblib"
  exit 1
fi
log "surrogate protocol confirmed: $PROTO"

# ---- smoke test ---------------------------------------------------------
log "--- smoke test (300 s, sar_open_field) ---"
SAR_POLICY=planner SAR_SEED=$SEED_A \
  ./run_research_campaign.sh 300 0 sar_open_field >>"$LOG" 2>&1
SMOKE=$(ls -dt logs/campaign_* 2>/dev/null | head -1)
ACTS="$SMOKE/sar_open_field_actions.csv"
RLOG="$SMOKE/sar_open_field_research.log"
if [ ! -f "$ACTS" ]; then
  log "FATAL: smoke run produced no action log ($ACTS) — aborting"; exit 1
fi
STEER=$(awk -F, 'NR>1 && ($7=="climb"||$7=="descend"||$7=="step")' "$ACTS" | wc -l)
TOTAL=$(( $(wc -l < "$ACTS") - 1 ))
log "smoke: $SMOKE  actions=$TOTAL steering=$STEER"

# The excitation fallback also emits climb/descend/step, so counting steering
# actions cannot tell the two apart. Confirm the planner LOADED.
if grep -q "planner unavailable" "$RLOG" 2>/dev/null; then
  log "FATAL: planner failed to load — fell back to excitation. Reason:"
  grep "planner unavailable" "$RLOG" | tail -2 | tee -a "$LOG"
  exit 1
fi
for want in "surrogate held out" "absolute z targets" \
            "onset_censored_h20_logisticL1_isotonic" "calibration isotonic"; do
  if ! grep -q "$want" "$RLOG" 2>/dev/null; then
    log "FATAL: planner start lines lack '$want' — wrong model or stale module."
    grep "\[planner\]" "$RLOG" 2>/dev/null | tail -3 | tee -a "$LOG"
    exit 1
  fi
done
if [ "$STEER" -lt 1 ]; then
  log "FATAL: planner loaded but never steered — aborting"; exit 1
fi
log "confirmed: corrected surrogate loaded, calibrated, steering"
grep "\[planner\]" "$RLOG" | head -2 | sed 's/^/    /' | tee -a "$LOG"

# ---- the fly-off --------------------------------------------------------
i=0
for r in "${RUNS[@]}"; do
  IFS=: read -r pol w seed lab <<<"$r"
  i=$((i+1))
  log "--- run $i/${#RUNS[@]}: $pol $w seed=$seed ($lab), ${DUR}s ---"
  SAR_POLICY="$pol" SAR_SEED="$seed" \
    ./run_research_campaign.sh "$DUR" 0 "$w" >>"$LOG" 2>&1
  rc=$?
  D=$(ls -dt logs/campaign_* 2>/dev/null | head -1)
  ROWS=$(wc -l < "$D/$w.csv" 2>/dev/null || echo 0)
  log "  done (rc=$rc) dir=$D rows=$ROWS"
  # ploss==0 across a whole run means the channel bridge never applied terrain:
  # the run is void and must not be analysed as if it were data.
  if [ -f "$D/$w.csv" ]; then
    python3 - "$D/$w.csv" <<'PY' 2>&1 | tee -a "$LOG"
import sys, csv
f = sys.argv[1]
rows = list(csv.DictReader(open(f)))
cols = [c for c in (rows[0].keys() if rows else []) if c.endswith("ploss_dB")]
vals = [float(r[c]) for r in rows for c in cols
        if r.get(c) not in (None, "", "nan")]
nz = sum(1 for v in vals if v > 0)
print(f"    ploss check: {nz}/{len(vals)} non-zero"
      + ("" if nz else "   *** VOID RUN: channel never applied ***"))
PY
  fi
done
log "=== Round 4 fly-off COMPLETE ==="
log "analyse with: python3 scripts/ml/flyoff_round2.py  (update its dir map first)"
