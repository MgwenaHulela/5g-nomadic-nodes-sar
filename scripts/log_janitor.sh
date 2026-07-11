#!/usr/bin/env bash
# log_janitor.sh — keep OAI's runaway logs from filling the disk.
#
# The gNB writes ~5 GB/h of PHY spam and each UE ~2 GB/h; an unattended
# overnight campaign (2026-07-11) grew them to 158+58+56 GB, filled the
# root filesystem to 100%, and crashed the channel bridge and the patrol
# mid-run. This loop truncates any of them whose REAL disk usage (du,
# not apparent size — truncated live logs become sparse) exceeds the
# cap. Losing log history is fine: all experiment data lives in
# ~/sar_sim/logs CSVs; the only live consumer of these files is
# fresh_l1()'s tail read, which recovers within seconds of a truncation.
#
# Usage: ./log_janitor.sh &   (start_full_stack.sh launches it)
set -u
CAP_BYTES=$((5 * 1024 * 1024 * 1024))   # 5 GB per file
FILES="/tmp/gnb.log /tmp/ue_alpha.log /tmp/ue_bravo.log"

while true; do
  for f in $FILES; do
    [ -f "$f" ] || continue
    used=$(du -sb "$f" 2>/dev/null | cut -f1)
    if [ "${used:-0}" -gt "$CAP_BYTES" ]; then
      truncate -s 0 "$f"
      echo "$(date '+%F %T') truncated $f (was $((used / 1024 / 1024 / 1024)) GB)"
    fi
  done
  sleep 600
done
