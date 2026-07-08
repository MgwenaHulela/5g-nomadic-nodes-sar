#!/usr/bin/env bash
#
# verify_stack.sh — read-only post-launch verification of the OAI RAN.
# Does NOT start, stop, or restart anything. Checks that both UE TUN
# interfaces exist, reports their SMF-assigned IPs, warns if Gazebo isn't
# running (bridge will have no poses), confirms alpha<->bravo connectivity
# over the air, and prints the current set of attached RNTIs from the gNB log.
#
# Called automatically as the final gate in restart_ran.sh, and can also be
# run standalone at any time to check current stack health, e.g. before
# kicking off soak_test.sh.
#
# Usage: ./verify_stack.sh
#
# Exits non-zero (with a FATAL message) at the first hard failure.
#
set -uo pipefail

UE_ALPHA_TUN=oaitun_ue1
UE_BRAVO_TUN=oaitun_ue1p2
GNB_LOG=/tmp/gnb.log

die() { echo "FATAL: $1"; exit 1; }

echo "== Pre-flight checks =="

ip addr show "$UE_ALPHA_TUN" >/dev/null 2>&1 || die "$UE_ALPHA_TUN missing — stack not up? Run restart_ran.sh"
ip addr show "$UE_BRAVO_TUN" >/dev/null 2>&1 || die "$UE_BRAVO_TUN missing — stack not up? Run restart_ran.sh"

ALPHA_IP=$(ip -4 addr show "$UE_ALPHA_TUN" | grep -oP 'inet \K[\d.]+')
BRAVO_IP=$(ip -4 addr show "$UE_BRAVO_TUN" | grep -oP 'inet \K[\d.]+')
[[ -n "$ALPHA_IP" ]] || die "$UE_ALPHA_TUN has no IPv4 address — PDU session incomplete?"
[[ -n "$BRAVO_IP" ]] || die "$UE_BRAVO_TUN has no IPv4 address — PDU session incomplete?"

echo "  alpha: $UE_ALPHA_TUN $ALPHA_IP"
echo "  bravo: $UE_BRAVO_TUN $BRAVO_IP"

if ! pgrep -f "gz sim" >/dev/null 2>&1; then
  echo "  WARNING: no 'gz sim' process found — is Gazebo running? Bridge will wait for poses."
fi

ping -I "$ALPHA_IP" -c 2 -W 2 "$BRAVO_IP" >/dev/null 2>&1 || die "alpha->bravo ping failed — check RRC/RLC state, not just TUN presence"
echo "  connectivity OK"

RNTIS=$(grep -oP "UE \K[0-9a-f]{4}" "$GNB_LOG" 2>/dev/null | sort -u | tr '\n' ' ')
if [[ -z "$RNTIS" ]]; then
  echo "  WARNING: no RNTIs found in $GNB_LOG — gNB log may have rotated or UEs never attached at MAC layer"
else
  echo "  starting RNTIs: $RNTIS"
fi
