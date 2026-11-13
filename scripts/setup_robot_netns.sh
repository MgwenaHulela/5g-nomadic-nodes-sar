#!/usr/bin/env bash
#
# setup_robot_netns.sh — move each UE's TUN interface into a per-robot
# network namespace so application traffic in BOTH directions is forced
# over the air interface.
#
# Why: with both oaitun_* interfaces in the root namespace, the UE IPs
# (10.45.0.x) are local addresses of this host, so any host->UE traffic
# (downlink: C2 commands, e-stop, TCP ACKs, iperf -R) is delivered via
# loopback and never touches the radio. Moving only the TUN device fixes
# this: the nr-uesoftmodem process keeps its open fd across the move (tun
# fds remain valid when the netdev changes namespace), so the RAN itself
# is completely untouched.
#
# After setup:
#   - robot-side (uplink) tests:  ip netns exec robot_alpha iperf3 -c ...
#   - base-side (downlink/C2):    ping/iperf3 to the UE IP from the root
#     namespace — now genuinely traverses ogstun -> UPF -> gNB -> air.
#
# Usage:
#   sudo ./setup_robot_netns.sh setup      # after UEs are attached
#   sudo ./setup_robot_netns.sh teardown   # move TUNs back to root ns
#   ./setup_robot_netns.sh status
#
set -uo pipefail

# tun-in-root-ns -> namespace name
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/robots.env"
# Built from the shared roster so adding a robot needs no edit here.
declare -A NS_OF=()
for _r in "${ROBOT_NAMES[@]}"; do NS_OF[${ROBOT_TUN[$_r]}]=${ROBOT_NS[$_r]}; done
# fallback IPs if detection fails (SMF assigns these to the 2 subscribers)
# Duplicate-IP guard. Open5GS allocates from session.subnet WITHOUT checking
# statically-assigned subscriber addresses (slice.session.ue.ipv4), so a
# subscriber missing a static IP silently gets a pool address that can collide
# with another robot's static one. Both TUNs then carry the same address in
# different namespaces, every reachability check still passes, and every
# per-robot measurement for the colliding pair is quietly wrong. Observed
# 2026-10-19: charlie and delta duplicated alpha and bravo.
check_unique_ips() {
  local dupes
  dupes=$(for _r in "${ROBOT_NAMES[@]}"; do
            ip netns exec "${ROBOT_NS[$_r]}" ip -4 -o addr show "${ROBOT_TUN[$_r]}" 2>/dev/null \
              | grep -oP 'inet \K[\d.]+'
          done | sort | uniq -d)
  if [[ -n "$dupes" ]]; then
    echo "  FATAL: duplicate UE IPs across robots: $dupes"
    echo "         Every robot needs a distinct slice.session.ue.ipv4 in its"
    echo "         Open5GS subscriber record. Check with:"
    echo "           mongosh open5gs --eval 'db.subscribers.find({},{imsi:1,\"slice.session.ue.ipv4\":1})'"
    return 1
  fi
  echo "  UE IPs unique across ${#ROBOT_NAMES[@]} robots"
  return 0
}

declare -A FALLBACK_IP=()
for _r in "${ROBOT_NAMES[@]}"; do FALLBACK_IP[${ROBOT_TUN[$_r]}]=${ROBOT_UE_IP[$_r]}/16; done

die() { echo "FATAL: $1"; exit 1; }

setup() {
  [[ $EUID -eq 0 ]] || die "setup needs root (sudo)"
  for tun in "${!NS_OF[@]}"; do
    local ns="${NS_OF[$tun]}"

    if ip netns exec "$ns" ip link show "$tun" &>/dev/null; then
      echo "  $tun already in $ns"
      continue
    fi
    ip link show "$tun" &>/dev/null || die "$tun not found in root ns — is the UE attached?"

    # capture IP + MTU before the move (the move wipes interface config)
    local ip_cidr mtu
    ip_cidr=$(ip -o -4 addr show dev "$tun" | awk '{print $4}' | head -1)
    [[ -n "$ip_cidr" ]] || ip_cidr="${FALLBACK_IP[$tun]}"
    mtu=$(cat "/sys/class/net/$tun/mtu" 2>/dev/null || echo 1500)

    ip netns add "$ns" 2>/dev/null || true
    ip link set "$tun" netns "$ns" || die "failed to move $tun into $ns"
    ip -n "$ns" link set lo up
    ip -n "$ns" addr add "$ip_cidr" dev "$tun"
    ip -n "$ns" link set "$tun" mtu "$mtu" up
    # everything the robot sends goes over the air
    ip -n "$ns" route add default dev "$tun"
    echo "  $tun -> $ns ($ip_cidr, mtu $mtu)"
  done

  echo "== verify =="
  for tun in "${!NS_OF[@]}"; do
    local ns="${NS_OF[$tun]}" ue_ip
    ue_ip=$(ip -n "$ns" -o -4 addr show dev "$tun" | awk '{print $4}' | cut -d/ -f1)
    # uplink: robot -> base over air
    if ip netns exec "$ns" ping -c 1 -W 3 10.45.0.1 >/dev/null 2>&1; then
      echo "  $ns uplink OK (-> 10.45.0.1)"
    else
      echo "  WARNING: $ns cannot reach 10.45.0.1 over air"
    fi
    # downlink: base -> robot over air (this is the new capability)
    if ping -c 1 -W 3 "$ue_ip" >/dev/null 2>&1; then
      echo "  $ns downlink OK (root ns -> $ue_ip)"
    else
      echo "  WARNING: root ns cannot reach $ue_ip (downlink broken)"
    fi
  done
  # Run AFTER the reachability checks: a duplicate address passes every one
  # of them, so uniqueness has to be asserted separately.
  check_unique_ips || return 1
}

teardown() {
  [[ $EUID -eq 0 ]] || die "teardown needs root (sudo)"
  for tun in "${!NS_OF[@]}"; do
    local ns="${NS_OF[$tun]}"
    if ip netns exec "$ns" ip link show "$tun" &>/dev/null; then
      local ip_cidr
      ip_cidr=$(ip -n "$ns" -o -4 addr show dev "$tun" | awk '{print $4}' | head -1)
      ip -n "$ns" link set "$tun" netns 1
      [[ -n "$ip_cidr" ]] && ip addr add "$ip_cidr" dev "$tun" 2>/dev/null
      ip link set "$tun" up 2>/dev/null
      echo "  $tun restored to root ns"
    fi
    ip netns del "$ns" 2>/dev/null && echo "  $ns deleted"
  done
}

status() {
  for tun in "${!NS_OF[@]}"; do
    local ns="${NS_OF[$tun]}"
    if [[ -e "/var/run/netns/$ns" ]] && ip netns exec "$ns" ip link show "$tun" &>/dev/null; then
      echo "  $ns: ACTIVE ($tun inside, $(ip -n "$ns" -o -4 addr show dev "$tun" | awk '{print $4}'))"
    elif ip link show "$tun" &>/dev/null; then
      echo "  $ns: not set up ($tun in root ns)"
    else
      echo "  $ns: $tun missing entirely (UE not attached?)"
    fi
  done
}

case "${1:-status}" in
  setup)    setup ;;
  teardown) teardown ;;
  status)   status ;;
  *) echo "Usage: $0 {setup|teardown|status}"; exit 1 ;;
esac
