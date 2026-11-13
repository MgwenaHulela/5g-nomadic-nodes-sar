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
# Roster-driven: a UE log that is not listed here grows without bound.
# When the testbed went from 2 to 4 robots on 2026-10-19 this list was not
# updated, so ue_charlie.log and ue_delta.log were uncapped — ~2 GB/h each,
# which over a 100 h campaign is ~400 GB against 241 GB free. The janitor
# exists precisely to stop that (it was written after logs filled the root
# filesystem on 2026-07-11), so it has to follow the roster.
SAR_ROBOTS="${SAR_ROBOTS:-alpha,bravo,charlie,delta}"
FILES="/tmp/gnb.log"
IFS=',' read -r -a _ROBOTS <<< "$SAR_ROBOTS"
for _r in "${_ROBOTS[@]}"; do FILES="$FILES /tmp/ue_${_r}.log"; done

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
