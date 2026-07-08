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
ALPHA_IP="10.45.0.2"
ALPHA_TUN="oaitun_ue1"
BRAVO_IP="10.45.0.3"
BRAVO_TUN="oaitun_ue1p2"
OGSTUN="ogstun"
TEST_IP_A="10.0.0.200"
TEST_IP_B="10.0.0.201"
UPF_SRC="127.0.0.7"

# ----- Helpers ---------------------------------------------------------------
sudo_sysctl() { echo "Makhubela1" | sudo -S sysctl -w "$1=$2" 2>/dev/null; }
sudo_ip()     { echo "Makhubela1" | sudo -S ip "$@" 2>/dev/null; }
sudo_ipt()    { echo "Makhubela1" | sudo -S iptables "$@" 2>/dev/null; }

# ----- Setup -----------------------------------------------------------------
setup() {
  echo "== Setting up over-the-air test path =="

  # 1. Create local loopback IPs for test servers
  for ip in "$TEST_IP_A" "$TEST_IP_B"; do
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
  for ip in "$TEST_IP_A" "$TEST_IP_B"; do
    sudo_ipt -t mangle -C PREROUTING -i "$OGSTUN" -d "$ip" -j MARK --set-mark 1 2>/dev/null ||
      sudo_ipt -t mangle -A PREROUTING -i "$OGSTUN" -d "$ip" -j MARK --set-mark 1
  done
  sudo_ip rule add fwmark 1 lookup main priority 9999
  echo "  [fwmark] packets from $OGSTUN -> dst {TEST_IP_A, TEST_IP_B} -> main table"

  # 4. Start test servers if not running
  for ip in "$TEST_IP_A" "$TEST_IP_B"; do
    if ! ss -tlnp 2>/dev/null | grep -q ":$ip:5201"; then
      pkill -f "iperf3 -s -B $ip" 2>/dev/null || true
      iperf3 -s -B "$ip" -p 5201 -D 2>&1
      echo "  [iperf3] server started on $ip:5201"
    else
      echo "  [iperf3] server already running on $ip:5201"
    fi
  done

  echo ""
  echo "=== Verification ==="
  ping -c 2 -W 2 -I "$ALPHA_TUN" "$TEST_IP_A" 2>&1
  ping -c 2 -W 2 -I "$BRAVO_TUN" "$TEST_IP_B" 2>&1
  echo ""
  echo "IMPORTANT: Use --bind-dev <iface> or -I <iface> to force air interface."
  echo "  alpha: --bind-dev $ALPHA_TUN  / -I $ALPHA_TUN"
  echo "  bravo: --bind-dev $BRAVO_TUN / -I $BRAVO_TUN"
}

# ----- Cleanup ---------------------------------------------------------------
cleanup() {
  echo "== Cleaning up over-the-air test path =="

  # Remove stale rules
  sudo_ip rule del from "$ALPHA_IP/32" table 10000 2>/dev/null
  sudo_ip route flush table 10000 2>/dev/null

  # Remove fwmark rule
  sudo_ip rule del fwmark 1 2>/dev/null
  for ip in "$TEST_IP_A" "$TEST_IP_B"; do
    sudo_ipt -t mangle -D PREROUTING -i "$OGSTUN" -d "$ip" -j MARK --set-mark 1 2>/dev/null
  done
  echo "  [clean] fwmark rule removed"

  # Reset accept_local
  sudo_sysctl "net.ipv4.conf.$OGSTUN.accept_local" 0
  sudo_sysctl "net.ipv4.conf.all.accept_local" 0
  echo "  [clean] accept_local = 0"

  # Remove test IPs from lo
  for ip in "$TEST_IP_A" "$TEST_IP_B"; do
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
  for ip in "$TEST_IP_A" "$TEST_IP_B"; do
    local status="not installed"
    sudo_ipt -t mangle -C PREROUTING -i "$OGSTUN" -d "$ip" -j MARK --set-mark 1 2>/dev/null && status="installed"
    echo "  [$ip] $status"
  done
  echo ""
  echo "--- iperf3 servers ---"
  ss -tlnp 2>/dev/null | grep -E "$TEST_IP_A|$TEST_IP_B" | sed 's/.*//'
  echo ""
  echo "--- Quick tests ---"
  echo "  alpha:"
  ping -c 1 -W 2 -I "$ALPHA_TUN" "$TEST_IP_A" 2>&1 | tail -2
  echo "  bravo:"
  ping -c 1 -W 2 -I "$BRAVO_TUN" "$TEST_IP_B" 2>&1 | tail -2
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
