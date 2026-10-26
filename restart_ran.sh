#!/usr/bin/env bash
#
# restart_ran.sh — clean restart of the OAI RAN (gNB + UE alpha + UE bravo)
# with hard gates at every step. Core network (Open5GS) is left untouched.
#
# Usage: ./restart_ran.sh
#
# Exits non-zero at the first failed gate rather than continuing blind.
#
set -uo pipefail

OAI_DIR=/home/innocentmsc/openairinterface5g
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/scripts/robots.env"
CONF_DIR=/home/innocentmsc/sar_sim/oai_configs
UE_ARGS="-r 106 --numerology 1 --band 78 -C 3319680000 --ssb 516"

die() { echo "ABORT: $1"; exit 1; }
gate() { echo "  gate: $1"; }

echo "== 1. Kill all OAI, iperf, and bridge processes =="
pkill -9 -f nr-uesoftmodem 2>/dev/null
pkill -9 -f nr-softmodem 2>/dev/null
pkill -9 -f iperf3 2>/dev/null
pkill -9 -f channel_bridge.py 2>/dev/null
sleep 3

N=$(pgrep -c -f "nr-uesoftmodem|nr-softmodem" || true)
[[ "${N:-0}" -eq 0 ]] || die "OAI processes still alive after kill: $(pgrep -af 'nr-')"
gate "no OAI processes running"

echo "== 2. Clean stale TUN interfaces =="
sudo ip link delete oaitun_ue1 2>/dev/null
sudo ip link delete oaitun_ue1p2 2>/dev/null
gate "TUNs cleared"

echo "== 3. Gazebo sanity =="
# count only SERVER instances — the GUI (gz sim -g) attached via
# view_gui.sh is a legitimate second "gz sim" process
GZ_COUNT=$(pgrep -af "gz sim" 2>/dev/null | grep -cv -- " -g" || true)
if [[ "${GZ_COUNT:-0}" -eq 0 ]]; then
  echo "  WARNING: no Gazebo running. RAN will start fine, but the bridge will"
  echo "  have no poses. Start it with:"
  echo "    gz sim -r -s ~/sar_sim/worlds/sar_open_field.sdf -v 1 &"
elif [[ "${GZ_COUNT:-0}" -gt 1 ]]; then
  echo "  WARNING: $GZ_COUNT Gazebo instances running — this eats RAM and floods"
  echo "  pose topics. Strongly recommended before continuing:"
  echo "    pkill -9 -f 'gz sim'   # then start exactly ONE instance"
  read -rp "  Continue anyway? [y/N] " ans
  [[ "$ans" == "y" || "$ans" == "Y" ]] || die "fix Gazebo first"
else
  gate "exactly 1 Gazebo instance"
fi

echo "== 4. Start gNB =="
cd "$OAI_DIR" || die "OAI dir not found"
nohup ./cmake_targets/ran_build/build/nr-softmodem \
  --rfsim -O "$CONF_DIR/gnb.sa.band78.rfsim.conf" --telnetsrv \
  > /tmp/gnb.log 2>&1 &
sleep 10
pgrep -f nr-softmodem >/dev/null || die "gNB died on startup — check /tmp/gnb.log"
ss -ltn | grep -q ":4043 " || die "gNB up but rfsim port 4043 not listening"
gate "gNB running, rfsim listening"

# UEs are started one at a time, each waiting for its own TUN before the
# next begins. Attach order matters: the core assigns 10.45.0.x addresses
# sequentially, and ROBOT_UE_IP in scripts/robots.env assumes that order.
step=5
for r in "${ROBOT_NAMES[@]}"; do
  tun="${ROBOT_TUN[$r]}"; conf="${ROBOT_CONF[$r]}"; log="${ROBOT_LOG[$r]}"
  echo "== $step. Start UE $r =="
  nohup ./cmake_targets/ran_build/build/nr-uesoftmodem \
    --telnetsrv --rfsim -O "$CONF_DIR/$conf" $UE_ARGS \
    > "$log" 2>&1 &
  # wait up to 120s for PDU session (TUN appearing) — rfsim runs at ~2% realtime
  for i in $(seq 1 60); do
    sleep 2
    ip addr show "$tun" >/dev/null 2>&1 && break
    [ $((i % 10)) -eq 0 ] && echo "  still waiting for $r TUN ... (${i}s)"
  done
  ip addr show "$tun" >/dev/null 2>&1 || \
    die "$r TUN ($tun) never appeared — check $log (tail -30)"
  gate "$r attached ($(ip -4 addr show "$tun" | grep -oP 'inet \K[\d.]+'))"
  step=$((step + 1))
done

echo "== 7. Final process-count gate =="
# NOTE: this OAI build forks a companion process per softmodem (observed:
# every launch yields exactly 2 processes with consecutive PIDs). So one
# gNB = 2 processes, and N UEs = 2N processes.
UE_EXPECT=$((ROBOT_COUNT * 2))
GNB_N=$(pgrep -c nr-softmodem)
UE_N=$(pgrep -c nr-uesoftmodem)
[[ "$GNB_N" -eq 2 ]] || die "expected 2 gNB processes (1 launch x fork pair), found $GNB_N"
[[ "$UE_N" -eq "$UE_EXPECT" ]] || \
  die "expected $UE_EXPECT UE processes ($ROBOT_COUNT launches x fork pair), found $UE_N"
gate "1 gNB launch + $ROBOT_COUNT UE launches (fork pairs accounted for), no extras"

echo "== 8. Full verification =="
cd ~/sar_sim || die "sar_sim dir not found"
./verify_stack.sh
