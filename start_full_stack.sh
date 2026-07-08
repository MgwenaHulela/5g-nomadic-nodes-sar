#!/usr/bin/env bash
#
# start_full_stack.sh — One-command launcher for the full A2G test stack.
#
# Usage: ./start_full_stack.sh
#
# What it does:
#   1. Verifies prerequisites (MongoDB, Open5GS core)
#   2. Kills stale OAI/Gazebo/bridge/iperf processes
#   3. Starts gNB + UE alpha + UE bravo (via restart_ran.sh)
#   4. Sets up over-air routing + iperf3 servers
#   5. Starts Gazebo (headless) if not already running
#   6. Spawns sar_uav, sar_ground_alpha, sar_ground_bravo
#   7. Starts channel_bridge.py (background)
#   8. Runs a quick verification
#
set -uo pipefail

SELF_DIR="$(cd "$(dirname "$0")" && pwd)"
OAI_DIR="$HOME/openairinterface5g"
CONF_DIR="$SELF_DIR/oai_configs"

die() { echo "FATAL: $1"; exit 1; }
info() { echo "==> $1"; }
ok()   { echo "  OK: $1"; }

# ── 1. Prerequisites ──────────────────────────────────────────────────────────

info "Checking prerequisites..."

# 1a. MongoDB
if pgrep -x mongod >/dev/null 2>&1; then
  ok "MongoDB running"
else
  echo "  Starting MongoDB..."
  echo "Makhubela1" | sudo -S systemctl start mongod 2>/dev/null || \
    die "Could not start MongoDB"
  sleep 2
  ok "MongoDB started"
fi

# 1b. Open5GS core (minimum: NRF, AMF, SMF, UPF, AUSF, UDM, UDR)
for svc in nrfd amfd smfd upfd ausfd udmd udrd; do
  if systemctl is-active --quiet "open5gs-${svc}" 2>/dev/null; then
    ok "open5gs-${svc} running"
  else
    echo "  Restarting open5gs-${svc}..."
    echo "Makhubela1" | sudo -S systemctl restart "open5gs-${svc}" 2>/dev/null || \
      die "Could not restart open5gs-${svc}"
  fi
done
ok "Open5GS core services running"

# 1c. Subscribers in MongoDB
SUB_COUNT=$(echo "Makhubela1" | sudo -S mongosh --quiet open5gs --eval 'db.subscribers.countDocuments()' 2>/dev/null)
if [[ "${SUB_COUNT:-0}" -lt 2 ]]; then
  die "Expected at least 2 subscribers in Open5GS, found ${SUB_COUNT:-0}"
fi
ok "Open5GS subscribers: $SUB_COUNT"

# ── 2. Run restart_ran.sh (kills stale procs, starts gNB + alpha + bravo) ─────
info "Starting RAN (gNB + both UEs)..."
cd "$SELF_DIR" || die "Can't cd to $SELF_DIR"
bash restart_ran.sh || die "restart_ran.sh failed"
ok "RAN running"

# ── 3. Setup over-air routing + iperf servers ────────────────────────────────
info "Setting up over-air routing..."
bash scripts/setup_over_air_test.sh setup 2>&1 | grep -v "^$" || \
  die "setup_over_air_test.sh failed"
ok "Routing + iperf servers ready"

# Quick connectivity check
timeout 5 ping -c 1 -I oaitun_ue1 10.0.0.200 >/dev/null 2>&1 || \
  die "Alpha cannot reach 10.0.0.200 over air"
timeout 5 ping -c 1 -I oaitun_ue1p2 10.0.0.201 >/dev/null 2>&1 || \
  die "Bravo cannot reach 10.0.0.201 over air"
ok "Over-air ping OK"

# ── 4. Gazebo ────────────────────────────────────────────────────────────────
info "Starting Gazebo..."
if pgrep -f "gz sim.*sar_open_field" >/dev/null 2>&1; then
  ok "Gazebo already running"
else
  gz sim -r -s "$SELF_DIR/worlds/sar_open_field.sdf" -v 1 &>/tmp/gz_sim.log &
  GZ_PID=$!
  for i in $(seq 1 15); do
    sleep 1
    gz topic -l 2>/dev/null | grep -q "/clock" && break
  done
  if gz topic -l 2>/dev/null | grep -q "/clock"; then
    ok "Gazebo started (PID $GZ_PID)"
  else
    die "Gazebo failed to start — check /tmp/gz_sim.log"
  fi
fi

# ── 5. Spawn models ──────────────────────────────────────────────────────────
info "Spawning 3D models..."

spawn_model() {
  local name="$1" sdf="$2"
  gz service -s /world/sar_open_field/create \
    --reqtype gz.msgs.EntityFactory \
    --reptype gz.msgs.Boolean \
    --timeout 5000 \
    --req "sdf: \"$sdf\"" >/dev/null 2>&1
  local rc=$?
  if gz topic -l 2>/dev/null | grep -q "/model/$name/pose"; then
    ok "  $name spawned"
  else
    echo "  WARNING: $name pose topic not found (might already exist)"
  fi
  return 0
}

# Only spawn if not already present
if ! gz topic -l 2>/dev/null | grep -q "/model/sar_uav/pose"; then
  spawn_model sar_uav '<sdf version="1.9"><model name="sar_uav"><pose>0 0 40 0 0 0</pose><link name="link"><inertial><mass>1.0</mass></inertial><collision name="collision"><geometry><sphere><radius>0.3</radius></sphere></geometry></collision><visual name="visual"><geometry><sphere><radius>0.3</radius></sphere></geometry><material><ambient>1 0 0 1</ambient></material></visual></link><plugin filename="gz-sim-pose-publisher-system" name="gz::sim::systems::PosePublisher"><publish_link_pose>true</publish_link_pose><publish_model_pose>true</publish_model_pose></plugin></model></sdf>'
fi

if ! gz topic -l 2>/dev/null | grep -q "/model/sar_ground_alpha/pose"; then
  spawn_model sar_ground_alpha '<sdf version="1.9"><model name="sar_ground_alpha"><pose>50 0 0.1 0 0 0</pose><link name="link"><inertial><mass>2.0</mass></inertial><collision name="collision"><geometry><box><size>0.5 0.5 0.2</size></box></geometry></collision><visual name="visual"><geometry><box><size>0.5 0.5 0.2</size></box></geometry><material><ambient>0 1 0 1</ambient></material></visual></link><plugin filename="gz-sim-pose-publisher-system" name="gz::sim::systems::PosePublisher"><publish_link_pose>true</publish_link_pose><publish_model_pose>true</publish_model_pose></plugin></model></sdf>'
fi

if ! gz topic -l 2>/dev/null | grep -q "/model/sar_ground_bravo/pose"; then
  spawn_model sar_ground_bravo '<sdf version="1.9"><model name="sar_ground_bravo"><pose>-50 0 0.1 0 0 0</pose><link name="link"><inertial><mass>2.0</mass></inertial><collision name="collision"><geometry><box><size>0.5 0.5 0.2</size></box></geometry></collision><visual name="visual"><geometry><box><size>0.5 0.5 0.2</size></box></geometry><material><ambient>0 0 1 1</ambient></material></visual></link><plugin filename="gz-sim-pose-publisher-system" name="gz::sim::systems::PosePublisher"><publish_link_pose>true</publish_link_pose><publish_model_pose>true</publish_model_pose></plugin></model></sdf>'
fi

ok "All models spawned"

# ── 6. Start channel bridge (background) ─────────────────────────────────────
info "Starting channel bridge..."
pkill -f channel_bridge.py 2>/dev/null
sleep 1
mkdir -p "$SELF_DIR/logs"
setsid python3 "$SELF_DIR/channel_bridge.py" &>/tmp/ch_bridge.log & disown
sleep 4
if pgrep -f channel_bridge.py >/dev/null 2>&1; then
  ok "Channel bridge running (logs: /tmp/ch_bridge.log)"
else
  die "Channel bridge failed to start"
fi

# Quick bridge sanity check
tail -3 /tmp/ch_bridge.log | grep -q "ploss" || \
  echo "  NOTE: bridge may still be waiting for first pose update"

# ── 7. Summary ────────────────────────────────────────────────────────────────
echo ""
echo "══════════════════════════════════════════════════════════════"
echo "  FULL STACK IS UP"
echo "══════════════════════════════════════════════════════════════"
echo ""
echo "  Test connectivity:"
echo "    ping -I oaitun_ue1 10.0.0.200"
echo "    ping -I oaitun_ue1p2 10.0.0.201"
echo ""
echo "  Run throughput test:"
echo "    iperf3 -c 10.0.0.200 --bind-dev oaitun_ue1 -t 10"
echo "    iperf3 -c 10.0.0.201 --bind-dev oaitun_ue1p2 -t 10"
echo ""
echo "  Dynamic A2G (move ground alpha to 150m):"
echo "    gz service -s /world/sar_open_field/set_pose \\"
echo "      --reqtype gz.msgs.Pose --reptype gz.msgs.Boolean \\"
echo "      --timeout 2000 \\"
echo "      --req 'name: \"sar_ground_alpha\" position: {x: 150, y: 0, z: 0.1}'"
echo ""
echo "  Channel bridge log:  tail -f /tmp/ch_bridge.log"
echo "  Gazebo log:          tail -f /tmp/gz_sim.log"
echo "══════════════════════════════════════════════════════════════"
