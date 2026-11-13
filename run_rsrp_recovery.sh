#!/usr/bin/env bash
#
# run_rsrp_recovery.sh — recover the SS-RSRP/SS-SINR stream that the main
# 2026-10-23..25 campaign lost.
#
# WHY
#   `phy_log_level` was set to "error" on the UE configs on 2026-10-23 11:30
#   to stop a 1.6 GB log flood and fix a UE reattach failure. OAI emits the
#   SS-RSRP line from nr_ue_measurements.c under LOG_D(PHY, ...), i.e. ONLY at
#   "debug" — there is no intermediate level. So every *_fastradio.csv of the
#   4-round campaign has empty RSRP/SINR columns (ss_stale = 1 on every row),
#   which costs the 1/3/5/10 s forecast horizons and the brief's RSRP-margin
#   objective.
#
#   This script waits for the main campaign to finish, flips the UE logs to
#   "debug", and runs a short excitation pass over all four terrains so the
#   fast horizons have real RSRP to train on. The gNB is left at "error":
#   it is the rfsimulator bottleneck thread and does not emit SS-RSRP anyway.
#
# RISK (accepted, with mitigations)
#   At "debug" a relaunched UE crawls through PHY init and may miss its TUN
#   deadline, aborting a run (exit 4). Mitigations: short runs so a loss is
#   one world not the set; each world is a separate invocation so one failure
#   does not take the others; a smoke test proves RSRP is really flowing
#   before the long runs start; a hard deadline stops the set before the
#   Monday 10:00 training slot; the UE configs are always restored.
#
# Usage:  setsid nohup ./run_rsrp_recovery.sh >logs/rsrp_recovery.out 2>&1 &
set -uo pipefail
cd "$(cd "$(dirname "$0")" && pwd)"

LOG=logs/rsrp_recovery.log
WORLDS=(sar_open_field sar_urban sar_forest sar_hills)
DUR="${RSRP_DUR:-5400}"                       # 1.5 h of measurement per world
DEADLINE=$(date -d 'tomorrow 07:30' +%s)      # never start a world after this
BACKUP="logs/archive/nrue_errorlevel_$(date +%Y%m%d_%H%M%S)"
CONFS=(oai_configs/nrue_alpha.conf oai_configs/nrue_bravo.conf
       oai_configs/nrue_charlie.conf oai_configs/nrue_delta.conf)

log() { echo "[$(date '+%F_%H:%M:%S')] $*" | tee -a "$LOG"; }

restore() {
  log "restoring UE configs from $BACKUP"
  for c in "${CONFS[@]}"; do
    [ -f "$BACKUP/$(basename "$c")" ] && cp -a "$BACKUP/$(basename "$c")" "$c"
  done
  grep -H phy_log_level "${CONFS[@]}" | sed 's/^/    /' | tee -a "$LOG"
}
trap 'log "INTERRUPTED"; restore; exit 130' INT TERM

log "=== RSRP recovery: waiting for the main campaign to finish ==="
while pgrep -f '[r]un_full_campaign\.sh' >/dev/null; do sleep 60; done
log "main campaign has exited"
sleep 30                                      # let the last stack tear down

mkdir -p "$BACKUP"
cp -a "${CONFS[@]}" "$BACKUP"/
log "UE configs backed up to $BACKUP"
for c in "${CONFS[@]}"; do
  sed -i 's/phy_log_level\( *\)= *"error"/phy_log_level\1= "debug"/' "$c"
done
grep -H phy_log_level "${CONFS[@]}" | sed 's/^/    /' | tee -a "$LOG"

BASE=$(date +%s)

# ---- smoke test: prove SS-RSRP is actually flowing before spending hours ----
log "--- smoke test (300 s, sar_open_field) ---"
SAR_POLICY=excite SAR_SEED=$BASE SAR_SEED_ROUND=4 \
  ./run_research_campaign.sh 300 0 sar_open_field >>"$LOG" 2>&1
SMOKE=$(ls -1dt logs/campaign_*/ 2>/dev/null | head -1)
FR=$(ls -1 "$SMOKE"*_fastradio.csv 2>/dev/null | head -1)
FILL=0
if [ -n "$FR" ]; then
  FILL=$(python3 - "$FR" <<'PY'
import csv, sys
rows = list(csv.DictReader(open(sys.argv[1])))
print(sum(1 for r in rows if r.get('alpha_rsrp_dBre') not in (None, '')))
PY
)
fi
log "smoke: $SMOKE  rsrp-filled rows = $FILL"
if [ "${FILL:-0}" -lt 10 ]; then
  log "ABORT: SS-RSRP still absent at phy_log_level=debug — nothing to gain"
  restore
  exit 1
fi
log "SS-RSRP confirmed flowing — proceeding with ${#WORLDS[@]} x ${DUR}s"

# ---- the real runs, one invocation per world so a failure is contained ----
i=0
for w in "${WORLDS[@]}"; do
  i=$((i + 1))
  now=$(date +%s)
  need=$((DUR + 300))
  if (( now + need > DEADLINE )); then
    log "SKIP $w — would run past the $(date -d "@$DEADLINE" '+%a %H:%M') deadline"
    continue
  fi
  log "--- world $i/${#WORLDS[@]}: $w (${DUR}s, excite) ---"
  SAR_POLICY=excite SAR_SEED=$((BASE + i)) SAR_SEED_ROUND=4 \
    ./run_research_campaign.sh "$DUR" 0 "$w" >>"$LOG" 2>&1
  log "$w finished (rc=$?)"
done

restore
log "stopping the CSI side-car"
pkill -f 'csi_sidecar[.]py' 2>/dev/null
log "=== RSRP recovery COMPLETE ==="
ls -1dt logs/campaign_*/ | head -5 | sed 's/^/    /' | tee -a "$LOG"
