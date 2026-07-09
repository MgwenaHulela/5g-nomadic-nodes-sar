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

# Clean stale Python bytecache
find "$(cd "$(dirname "$0")" && pwd)/scripts" -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true

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

# Model resource paths must be exported on the gz sim SERVER process — the
# server is what resolves model:// URIs when models are spawned later via
# the EntityFactory service. Exporting them only in a spawn script (around
# the gz service client call) does nothing.
PX4_MODELS="$HOME/Desktop/Msc-Research/simulation/PX4-gazebo-models/models"
TB3_MODELS="$HOME/turtlebot3_ws/src/turtlebot3_simulations/turtlebot3_gazebo/models"
TB3_DESC="$HOME/turtlebot3_ws/src/turtlebot3/turtlebot3_description"
[[ -d "$PX4_MODELS" ]] || die "PX4 models dir not found: $PX4_MODELS"
[[ -d "$TB3_MODELS" ]] || die "TurtleBot3 models dir not found: $TB3_MODELS"
export GZ_SIM_RESOURCE_PATH="${PX4_MODELS}:${TB3_MODELS}:${TB3_DESC}:${GZ_SIM_RESOURCE_PATH:-}"

# Always kill any stray/stale gz sim instance first — a manually-started
# instance (wrong flags, wrong world path, or just stale state) would
# otherwise get silently reused, and the rest of this script would think
# it owns a Gazebo it doesn't actually control. Exactly one instance,
# always launched fresh by this script, matching restart_ran.sh's own
# "exactly ONE Gazebo instance" requirement.
if pgrep -f "gz sim" >/dev/null 2>&1; then
  echo "  Killing existing Gazebo instance(s)..."
  pkill -9 -f "gz sim" 2>/dev/null
  sleep 2
fi

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

# ── 5. Spawn real robot models (PX4 x500 UAV + TurtleBot3 Burger UGVs) ──────
info "Spawning 3D models..."

# The EntityFactory `name:` field overrides whatever <model name> is inside
# the SDF file, so all three land on pose/info under the sar_* names the
# channel bridge and viz expect (same pattern as worlds/spawn_ground_robot.sh).
# Stock burger (no TrajectoryFollower) — auto_patrol.py moves robots via
# set_pose, which would fight a baked-in waypoint follower.
UAV_SDF="$SELF_DIR/worlds/x500_static_hold.sdf"
BURGER_SDF="$TB3_MODELS/turtlebot3_burger/model.sdf"
[[ -f "$UAV_SDF" ]] || die "missing $UAV_SDF"
[[ -f "$BURGER_SDF" ]] || die "missing $BURGER_SDF"

spawn_model() {
  local name="$1" sdf_file="$2" x="$3" y="$4" z="$5"
  if gz model --list 2>/dev/null | grep -q "$name"; then
    echo "  $name already exists"
    return 0
  fi
  gz service -s /world/sar_open_field/create \
    --reqtype gz.msgs.EntityFactory \
    --reptype gz.msgs.Boolean \
    --timeout 5000 \
    --req "sdf_filename: \"$sdf_file\", name: \"$name\", pose: {position: {x: $x, y: $y, z: $z}}" \
    >/dev/null 2>&1
  echo "  $name spawned"
}

spawn_model sar_uav          "$UAV_SDF"    0 0 40
sleep 1
spawn_model sar_ground_alpha "$BURGER_SDF" 15 0 0.1
sleep 1
spawn_model sar_ground_bravo "$BURGER_SDF" 30 0 0.1
sleep 1

for m in sar_uav sar_ground_alpha sar_ground_bravo; do
  gz model --list 2>/dev/null | grep -q "$m" || \
    die "$m failed to spawn — check /tmp/gz_sim.log"
done
ok "All models spawned"

# ── 6. Start channel bridge (background) ─────────────────────────────────────
info "Starting channel bridge..."
pkill -f channel_bridge.py 2>/dev/null
sleep 1
mkdir -p "$SELF_DIR/logs"
nohup python3 -u "$SELF_DIR/channel_bridge.py" &>/tmp/ch_bridge.log &
sleep 4
if pgrep -f channel_bridge.py >/dev/null 2>&1; then
  ok "Channel bridge running (logs: /tmp/ch_bridge.log)"
else
  die "Channel bridge failed to start"
fi

# ── 7. Start Gazebo visualisation (background) ──────────────────────────────
info "Starting Gazebo visualisation..."
pkill -f gazebo_viz.py 2>/dev/null
sleep 1
nohup python3 -u "$SELF_DIR/scripts/gazebo_viz.py" &>/tmp/gz_viz.log &
sleep 2
if pgrep -f "gazebo_viz.py" >/dev/null 2>&1; then
  ok "Visualisation running (logs: /tmp/gz_viz.log)"
else
  echo "  WARNING: gazebo_viz.py failed to start"
fi

# Quick bridge sanity check
tail -3 /tmp/ch_bridge.log | grep -q "ploss" || \
  echo "  NOTE: bridge may still be waiting for first pose update"

# ── 8. Summary ────────────────────────────────────────────────────────────────
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
echo "  Viz log:             tail -f /tmp/gz_viz.log"
echo "══════════════════════════════════════════════════════════════"
