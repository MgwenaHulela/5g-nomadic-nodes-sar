#!/usr/bin/env bash
# Read-only recorder: follows /tmp/gnb.log across gNB restarts (tail -F) and
# appends every per-UE MAC statistics line (dlsch_/ulsch_rounds: MCS, NPRB,
# BLER, HARQ rounds, CCE fail, goodput) with a wall-clock timestamp. It never
# writes to the stack. Why: the gNB log is truncated at every stack restart,
# so these lines were lost after each campaign run.
#   ./scripts/record_gnb_mac_stats.sh logs/gnb_mac_stats_<tag>.log
OUT="${1:?output file}"
tail -n0 -F /tmp/gnb.log 2>/dev/null \
  | stdbuf -oL grep --line-buffered -E "UE [0-9a-f]+: (dlsch|ulsch)_rounds|UE RNTI [0-9a-f]+ CU-UE-ID" \
  | while IFS= read -r line; do printf '%s %s\n' "$(date +%s.%N | cut -c1-14)" "$line"; done >> "$OUT"
