#!/usr/bin/env bash
#
# install_requirements.sh — one-shot installer for the SAR 5G nomadic-nodes testbed.
#
# Installs EVERYTHING the pipeline needs on a fresh Ubuntu 22.04 machine:
#   ROS 2 Humble · Gazebo Harmonic · MongoDB 6.0 · Open5GS · OpenAirInterface 5G
#   · iperf3 3.16 (source) · TurtleBot3 models · PX4 x500 model · Python packages
#
# Usage:
#   ./install_requirements.sh            # install all components
#   ./install_requirements.sh ros gz     # install only the named components
#
# Components: base ros gz core oai iperf models python
#
# Safe to re-run: each step skips work that is already done where it can detect it.
# Requires sudo. Targets Ubuntu 22.04 (Jammy).
#
set -euo pipefail

# ---- config: where the external trees get cloned/built (must match start_full_stack.sh) ----
OAI_DIR="${OAI_DIR:-$HOME/openairinterface5g}"
IPERF_DIR="${IPERF_DIR:-$HOME/iperf}"
TB3_WS="${TB3_WS:-$HOME/turtlebot3_ws}"
PX4_MODELS_DIR="${PX4_MODELS_DIR:-$HOME/PX4-gazebo-models}"
IPERF_VERSION="3.16"

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

say()  { printf '\n\033[1;36m==> %s\033[0m\n' "$*"; }
ok()   { printf '\033[1;32m  ✓ %s\033[0m\n' "$*"; }
skip() { printf '\033[1;33m  ↷ %s\033[0m\n' "$*"; }

need_ubuntu_2204() {
  if ! grep -q '22.04' /etc/os-release 2>/dev/null; then
    echo "WARNING: this installer is written for Ubuntu 22.04; other releases may need tweaks." >&2
  fi
}

# ------------------------------------------------------------------ components

install_base() {
  say "Base build tools"
  sudo apt-get update
  sudo apt-get install -y git build-essential cmake ninja-build curl gnupg \
                          lsb-release software-properties-common python3-pip
  ok "base tools installed"
}

install_ros() {
  say "ROS 2 Humble + colcon"
  if [ -f /opt/ros/humble/setup.bash ]; then skip "ROS 2 Humble already present"; return; fi
  sudo add-apt-repository universe -y
  sudo curl -sSL https://raw.githubusercontent.com/ros/rosdistro/master/ros.key \
       -o /usr/share/keyrings/ros-archive-keyring.gpg
  echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/ros-archive-keyring.gpg] http://packages.ros.org/ros2/ubuntu $(. /etc/os-release && echo "$UBUNTU_CODENAME") main" \
       | sudo tee /etc/apt/sources.list.d/ros2.list > /dev/null
  sudo apt-get update
  sudo apt-get install -y ros-humble-desktop ros-dev-tools python3-colcon-common-extensions
  grep -qxF "source /opt/ros/humble/setup.bash" ~/.bashrc || \
    echo "source /opt/ros/humble/setup.bash" >> ~/.bashrc
  ok "ROS 2 Humble installed"
}

install_gz() {
  say "Gazebo Harmonic + ROS–Gazebo bridge"
  sudo curl -sSL https://packages.osrfoundation.org/gazebo.gpg \
       --output /usr/share/keyrings/pkgs-osrf-archive-keyring.gpg
  echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/pkgs-osrf-archive-keyring.gpg] http://packages.osrfoundation.org/gazebo/ubuntu-stable $(lsb_release -cs) main" \
       | sudo tee /etc/apt/sources.list.d/gazebo-stable.list > /dev/null
  sudo apt-get update
  sudo apt-get install -y gz-harmonic ros-humble-ros-gzharmonic
  ok "Gazebo Harmonic installed (gz CLI + gz-transport Python bindings)"
}

install_core() {
  say "5G core — MongoDB 6.0 + Open5GS"
  # MongoDB 6.0
  curl -fsSL https://pgp.mongodb.com/server-6.0.asc \
       | sudo gpg -o /usr/share/keyrings/mongodb-server-6.0.gpg --dearmor --yes
  echo "deb [ signed-by=/usr/share/keyrings/mongodb-server-6.0.gpg ] https://repo.mongodb.org/apt/ubuntu jammy/mongodb-org/6.0 multiverse" \
       | sudo tee /etc/apt/sources.list.d/mongodb-org-6.0.list > /dev/null
  sudo apt-get update
  sudo apt-get install -y mongodb-org mongodb-mongosh
  sudo systemctl enable --now mongod
  # Open5GS
  sudo add-apt-repository ppa:open5gs/latest -y
  sudo apt-get update
  sudo apt-get install -y open5gs
  ok "Open5GS + MongoDB installed (open5gs-* systemd services)"
  echo "    Note: register the two UE subscribers (see README step B5) before first run."
}

install_oai() {
  say "OpenAirInterface 5G (gNB + UE, rfsimulator) — this is the long one"
  if [ ! -d "$OAI_DIR/.git" ]; then
    git clone https://gitlab.eurecom.fr/oai/openairinterface5g.git "$OAI_DIR"
  else
    skip "OAI tree already cloned at $OAI_DIR"
  fi
  cd "$OAI_DIR"
  git checkout develop
  # shellcheck disable=SC1091
  source oaienv
  cd cmake_targets
  ./build_oai -I                         # external deps (first run installs, later runs no-op)
  ./build_oai -w SIMU --gNB --nrUE --ninja
  cd "$REPO_DIR"
  ok "OAI built → $OAI_DIR/cmake_targets/ran_build/build/"
}

install_iperf() {
  say "iperf3 $IPERF_VERSION from source (--bind-dev support)"
  if iperf3 --version 2>/dev/null | grep -q "$IPERF_VERSION"; then
    skip "iperf3 $IPERF_VERSION already installed"; return
  fi
  [ -d "$IPERF_DIR/.git" ] || git clone https://github.com/esnet/iperf.git "$IPERF_DIR"
  cd "$IPERF_DIR"
  git checkout "$IPERF_VERSION"
  ./configure && make -j"$(nproc)" && sudo make install && sudo ldconfig
  cd "$REPO_DIR"
  ok "iperf3 $(iperf3 --version | head -1)"
}

install_models() {
  say "Robot models — TurtleBot3 (UGV) + PX4 x500 (UAV)"
  mkdir -p "$TB3_WS/src"
  [ -d "$TB3_WS/src/turtlebot3/.git" ] || \
    git clone -b humble https://github.com/ROBOTIS-GIT/turtlebot3.git "$TB3_WS/src/turtlebot3"
  [ -d "$TB3_WS/src/turtlebot3_simulations/.git" ] || \
    git clone -b humble https://github.com/ROBOTIS-GIT/turtlebot3_simulations.git "$TB3_WS/src/turtlebot3_simulations"
  # shellcheck disable=SC1091
  source /opt/ros/humble/setup.bash
  ( cd "$TB3_WS" && colcon build --symlink-install )
  grep -qxF "export TURTLEBOT3_MODEL=burger" ~/.bashrc || \
    echo "export TURTLEBOT3_MODEL=burger" >> ~/.bashrc

  [ -d "$PX4_MODELS_DIR/.git" ] || \
    git clone https://github.com/PX4/PX4-gazebo-models.git "$PX4_MODELS_DIR"
  ok "TurtleBot3 workspace + PX4 x500 model ready"
}

install_python() {
  say "Python packages (requirements.txt)"
  python3 -m pip install -r "$REPO_DIR/requirements.txt"
  ok "Python deps installed (gz-transport bindings come from Gazebo, not pip)"
}

# ------------------------------------------------------------------ driver

ALL=(base ros gz core oai iperf models python)
TARGETS=("$@")
[ ${#TARGETS[@]} -eq 0 ] && TARGETS=("${ALL[@]}")

need_ubuntu_2204
for c in "${TARGETS[@]}"; do
  case "$c" in
    base)   install_base ;;
    ros)    install_ros ;;
    gz)     install_gz ;;
    core)   install_core ;;
    oai)    install_oai ;;
    iperf)  install_iperf ;;
    models) install_models ;;
    python) install_python ;;
    *) echo "Unknown component: $c (valid: ${ALL[*]})" >&2; exit 1 ;;
  esac
done

say "Done. Next steps:"
cat <<EOF
  1. Open a new shell (or 'source ~/.bashrc') so ROS/TurtleBot3 env vars load.
  2. Register the two UE subscribers in Open5GS      -> README step B5
  3. Point start_full_stack.sh at your tool paths    -> README step B4
  4. cp .env.example .env  &&  set SAR_SUDO_PASS      -> README step B6
  5. ./start_full_stack.sh  &&  ./verify_stack.sh
EOF
