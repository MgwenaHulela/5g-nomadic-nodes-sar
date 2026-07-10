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

echo "== 5. Start UE alpha =="
nohup ./cmake_targets/ran_build/build/nr-uesoftmodem \
  --telnetsrv --rfsim -O "$CONF_DIR/nrue_alpha.conf" $UE_ARGS \
  > /tmp/ue_alpha.log 2>&1 &
# wait up to 120s for PDU session (TUN appearing) — rfsim runs at ~2% realtime
for i in $(seq 1 60); do
  sleep 2
  ip addr show oaitun_ue1 >/dev/null 2>&1 && break
  [ $((i % 10)) -eq 0 ] && echo "  still waiting for alpha TUN ... (${i}s)"
done
ip addr show oaitun_ue1 >/dev/null 2>&1 || die "alpha TUN never appeared — check /tmp/ue_alpha.log (tail -30)"
gate "alpha attached ($(ip -4 addr show oaitun_ue1 | grep -oP 'inet \K[\d.]+'))"

echo "== 6. Start UE bravo =="
nohup ./cmake_targets/ran_build/build/nr-uesoftmodem \
  --telnetsrv --rfsim -O "$CONF_DIR/nrue_bravo.conf" $UE_ARGS \
  > /tmp/ue_bravo.log 2>&1 &
for i in $(seq 1 60); do
  sleep 2
  ip addr show oaitun_ue1p2 >/dev/null 2>&1 && break
  [ $((i % 10)) -eq 0 ] && echo "  still waiting for bravo TUN ... (${i}s)"
done
ip addr show oaitun_ue1p2 >/dev/null 2>&1 || die "bravo TUN never appeared — check /tmp/ue_bravo.log (tail -30)"
gate "bravo attached ($(ip -4 addr show oaitun_ue1p2 | grep -oP 'inet \K[\d.]+'))"

echo "== 7. Final process-count gate =="
# NOTE: this OAI build forks a companion process per softmodem (observed:
# every launch yields exactly 2 processes with consecutive PIDs). So one
# gNB = 2 processes, two UEs = 4 processes.
GNB_N=$(pgrep -c nr-softmodem)
UE_N=$(pgrep -c nr-uesoftmodem)
[[ "$GNB_N" -eq 2 ]] || die "expected 2 gNB processes (1 launch x fork pair), found $GNB_N"
[[ "$UE_N" -eq 4 ]] || die "expected 4 UE processes (2 launches x fork pair), found $UE_N"
gate "1 gNB launch + 2 UE launches (fork pairs accounted for), no extras"

echo "== 8. Full verification =="
cd ~/sar_sim || die "sar_sim dir not found"
./verify_stack.sh
