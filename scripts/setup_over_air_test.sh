#!/usr/bin/env bash
# setup_over_air_test.sh
#
# Routes UE alpha (10.45.0.2) uplink traffic through the 5G NR stack
# so the soak test captures real over-the-air RAN metrics.
#
# Flow:
#   alpha app -> oaitun_ue1 (TUN) -> alpha UE -> rfsim -> gNB -> UPF -> N6
#   -> ogstun -> kernel routes to local test server (10.0.0.200)
#   -> response back via loopback (fast, doesn't pollute air metrics)
#
# Usage:
#   ./scripts/setup_over_air_test.sh setup    # apply (idempotent)
#   ./scripts/setup_over_air_test.sh cleanup  # revert
#   ./scripts/setup_over_air_test.sh status   # show current state

set -uo pipefail
ACTION="${1:-status}"
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/robots.env"
# TEST_IPS / UE_IPS / TUNS are derived from the shared roster, so adding a
# robot needs no edit here. The ALPHA_*/BRAVO_* names are kept because a few
# messages below still refer to the first two robots by name.
TEST_IPS=(); UE_IPS=(); TUNS=()
for _r in "${ROBOT_NAMES[@]}"; do
  TEST_IPS+=("${ROBOT_TEST_IP[$_r]}")
  UE_IPS+=("${ROBOT_UE_IP[$_r]}")
  TUNS+=("${ROBOT_TUN[$_r]}")
done
ALPHA_IP="${ROBOT_UE_IP[alpha]}"
ALPHA_TUN="${ROBOT_TUN[alpha]}"
BRAVO_IP="${ROBOT_UE_IP[bravo]}"
BRAVO_TUN="${ROBOT_TUN[bravo]}"
OGSTUN="ogstun"
TEST_IP_A="${ROBOT_TEST_IP[alpha]}"
TEST_IP_B="${ROBOT_TEST_IP[bravo]}"
UPF_SRC="127.0.0.7"

# ----- Helpers ---------------------------------------------------------------
sudo_sysctl() { echo "${SAR_SUDO_PASS:-}" | sudo -S sysctl -w "$1=$2" 2>/dev/null; }
sudo_ip()     { echo "${SAR_SUDO_PASS:-}" | sudo -S ip "$@" 2>/dev/null; }
sudo_ipt()    { echo "${SAR_SUDO_PASS:-}" | sudo -S iptables "$@" 2>/dev/null; }

# ----- Setup -----------------------------------------------------------------
setup() {
  echo "== Setting up over-the-air test path =="

  # 1. Create local loopback IPs for test servers
  for ip in "${TEST_IPS[@]}"; do
    sudo_ip addr add "$ip/32" dev lo
    echo "  [$ip] added to lo"
  done

  # 2. accept_local on ogstun — allows packets from local src IPs
  #    arriving from UPF via N6 to be accepted (prevents martian drops)
  sudo_sysctl "net.ipv4.conf.$OGSTUN.accept_local" 1
  sudo_sysctl "net.ipv4.conf.all.accept_local" 1
  echo "  [sysctl] accept_local = 1"

  # 3. fwmark packets from UPF destined for test IPs → sends to main/local table
  #    (breaks the routing loop that would send them back through the TUN)
  for ip in "${TEST_IPS[@]}"; do
    sudo_ipt -t mangle -C PREROUTING -i "$OGSTUN" -d "$ip" -j MARK --set-mark 1 2>/dev/null ||
      sudo_ipt -t mangle -A PREROUTING -i "$OGSTUN" -d "$ip" -j MARK --set-mark 1
  done
  sudo_ip rule add fwmark 1 lookup main priority 9999
  echo "  [fwmark] packets from $OGSTUN -> dst {TEST_IP_A, TEST_IP_B} -> main table"

  # 4. Start test servers if not running
  # Two servers per robot. An iperf3 server runs ONE test at a time per
  # port, so the persistent TCP and UDP flows (scripts/traffic_streams.py)
  # need a port each or the second to start dies "server is busy".
  #
  # --logfile matters for UDP: loss and jitter are only known to the
  # RECEIVER. A UDP client's own interval lines report what it sent, and its
  # receiver summary only arrives when the test ends cleanly — which never
  # happens for a flow we kill at the end of a run. Reading the server's log
  # gives per-interval loss while the flow is live.
  for ip in "${TEST_IPS[@]}"; do
    for pt in 5201 5202; do
      # ALWAYS restart, never reuse. This used to skip the restart when a
      # server was already listening, so a WEDGED server survived every stack
      # restart. An iperf3 server runs one test at a time; if a client dies
      # without closing (which happens constantly when a link degrades), the
      # server can keep refusing new clients. The flows then relaunch forever
      # against a server that rejects them, their logs stay empty, and the
      # whole tcp_tp_mbps column silently reads as "not measured" for the
      # entire run. One such server had logged 97 accepted connections.
      # Also truncate the log: it is the sampler's data source for UDP loss
      # and jitter, and a stale one feeds OLD intervals into live rows.
      pkill -f "iperf3 -s -B $ip -p $pt" 2>/dev/null || true
      sleep 0.2
      : > "/tmp/iperfsrv_${ip}_${pt}.log" 2>/dev/null || true
      iperf3 -s -B "$ip" -p "$pt" -D \
        --logfile "/tmp/iperfsrv_${ip}_${pt}.log" --forceflush 2>&1
      echo "  [iperf3] server (re)started on $ip:$pt"
    done
  done

  echo ""
  echo "=== Verification ==="
  for _i in "${!ROBOT_NAMES[@]}"; do
    ping -c 2 -W 2 -I "${TUNS[$_i]}" "${TEST_IPS[$_i]}" 2>&1
  done
  echo ""
  echo "IMPORTANT: Use --bind-dev <iface> or -I <iface> to force air interface."
  for _i in "${!ROBOT_NAMES[@]}"; do
    echo "  ${ROBOT_NAMES[$_i]}: --bind-dev ${TUNS[$_i]} / -I ${TUNS[$_i]}"
  done
}

# ----- Cleanup ---------------------------------------------------------------
cleanup() {
  echo "== Cleaning up over-the-air test path =="

  # Remove stale rules
  sudo_ip rule del from "$ALPHA_IP/32" table 10000 2>/dev/null
  sudo_ip route flush table 10000 2>/dev/null

  # Remove fwmark rule
  sudo_ip rule del fwmark 1 2>/dev/null
  for ip in "${TEST_IPS[@]}"; do
    sudo_ipt -t mangle -D PREROUTING -i "$OGSTUN" -d "$ip" -j MARK --set-mark 1 2>/dev/null
  done
  echo "  [clean] fwmark rule removed"

  # Reset accept_local
  sudo_sysctl "net.ipv4.conf.$OGSTUN.accept_local" 0
  sudo_sysctl "net.ipv4.conf.all.accept_local" 0
  echo "  [clean] accept_local = 0"

  # Remove test IPs from lo
  for ip in "${TEST_IPS[@]}"; do
    sudo_ip addr del "$ip/32" dev lo 2>/dev/null
    pkill -f "iperf3 -s -B $ip" 2>/dev/null || true
  done
  echo "  [clean] test IPs removed, iperf3 servers stopped"

  echo "  Done."
}

# ----- Status ----------------------------------------------------------------
status() {
  echo "== Current over-the-air test state =="
  echo ""
  echo "--- IP rules ---"
  sudo_ip rule show | grep -E "10000|9999" || echo "  (none applied)"
  echo ""
  echo "--- accept_local ---"
  echo "  ogstun:  $(sudo_sysctl net.ipv4.conf.$OGSTUN.accept_local 2>&1 | awk '{print $3}')"
  echo "  all:     $(sudo_sysctl net.ipv4.conf.all.accept_local 2>&1 | awk '{print $3}')"
  echo ""
  echo "--- fwmark rule ---"
  for ip in "${TEST_IPS[@]}"; do
    local status="not installed"
    sudo_ipt -t mangle -C PREROUTING -i "$OGSTUN" -d "$ip" -j MARK --set-mark 1 2>/dev/null && status="installed"
    echo "  [$ip] $status"
  done
  echo ""
  echo "--- iperf3 servers ---"
  ss -tlnp 2>/dev/null | grep -E "$(IFS="|"; echo "${TEST_IPS[*]}")" | sed 's/.*//'
  echo ""
  echo "--- Quick tests ---"
  echo "  alpha:"
  for _i in "${!ROBOT_NAMES[@]}"; do
    echo "  ${ROBOT_NAMES[$_i]}:"
    ping -c 1 -W 2 -I "${TUNS[$_i]}" "${TEST_IPS[$_i]}" 2>&1 | tail -2
  done
}

# ----- Main ------------------------------------------------------------------
case "$ACTION" in
  setup)   setup ;;
  cleanup) cleanup ;;
  status)  status ;;
  *)
    echo "Usage: $0 {setup|cleanup|status}"
    exit 1
    ;;
esac
