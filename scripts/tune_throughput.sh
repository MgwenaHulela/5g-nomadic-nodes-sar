#!/usr/bin/env bash
# tune_throughput.sh — apply rfsimulator throughput optimizations

set -e
CONFIG_DIR="$HOME/sar_sim/oai_configs"

echo "== 1. TDD pattern: 2 DL + 7 UL (70% UL for uplink streaming) =="
sed -i \
  -e 's/nrofDownlinkSlots.*=.*7;/nrofDownlinkSlots                   = 2;/' \
  -e 's/nrofUplinkSlots.*=.*2;/nrofUplinkSlots                     = 7;/' \
  "$CONFIG_DIR/gnb.sa.band78.rfsim.conf"
grep -E 'nrof(Down|Up)linkSlots' "$CONFIG_DIR/gnb.sa.band78.rfsim.conf"

echo ""
echo "== 2. Reduce alpha UE logging: debug -> info =="
sed -i \
  -e 's/global_log_level.*=.*"debug"/global_log_level    = "info"/' \
  -e 's/phy_log_level.*=.*"debug"/phy_log_level       = "info"/' \
  -e 's/pdcp_log_level.*=.*"debug"/pdcp_log_level      = "info"/' \
  -e 's/rlc_log_level.*=.*"debug"/rlc_log_level       = "info"/' \
  -e 's/sdap_log_level.*=.*"debug"/sdap_log_level      = "info"/' \
  "$CONFIG_DIR/nrue_alpha.conf"
grep -E 'log_level' "$CONFIG_DIR/nrue_alpha.conf"

echo ""
echo "== 3. Increase TUN txqueuelen (burst absorption) =="
echo "Makhubela1" | sudo -S ip link set dev oaitun_ue1 txqueuelen 10000 2>/dev/null
echo "Makhubela1" | sudo -S ip link set dev oaitun_ue1p2 txqueuelen 10000 2>/dev/null
ip link show oaitun_ue1 | grep -o 'qlen [0-9]*'
ip link show oaitun_ue1p2 | grep -o 'qlen [0-9]*'

echo ""
echo "== 4. TCP tuning (BBR, larger buffers) =="
echo "Makhubela1" | sudo -S sysctl -w net.core.rmem_max=134217728 2>/dev/null
echo "Makhubela1" | sudo -S sysctl -w net.core.wmem_max=134217728 2>/dev/null
echo "Makhubela1" | sudo -S sysctl -w net.ipv4.tcp_rmem="4096 87380 134217728" 2>/dev/null
echo "Makhubela1" | sudo -S sysctl -w net.ipv4.tcp_wmem="4096 65536 134217728" 2>/dev/null
echo "Makhubela1" | sudo -S sysctl -w net.ipv4.tcp_congestion_control=bbr 2>/dev/null
echo "Makhubela1" | sudo -S sysctl -w net.core.default_qdisc=fq 2>/dev/null
echo "  BBR: $(sysctl net.ipv4.tcp_congestion_control 2>/dev/null | awk '{print $3}')"
echo "  buffers: rmem=$(sysctl net.ipv4.tcp_rmem 2>/dev/null | awk '{print $4,$5,$6}')"

echo ""
echo "Done. Restart gNB + UEs for TDD changes to take effect."
echo "Logging changes take effect on restart."
echo "TCP/queue changes are live immediately."
