#!/usr/bin/env python3
"""
SAR 5G Integration Node — Nhlanhla Makhubela, UCT MSc 2026
Reads robot poses DIRECTLY from gz-transport (bypasses ros_gz_bridge),
computes A2G path loss, measures UE latency, logs metrics to CSV.
"""
import rclpy
from rclpy.node import Node
from std_msgs.msg import Float32MultiArray, String
import math
import csv
import os
import subprocess
import threading
from datetime import datetime

# gz-transport direct imports
from gz.transport13 import Node as GzNode
from gz.msgs10.pose_v_pb2 import Pose_V

# ── Simulation parameters ──────────────────────────────────────
FREQ_GHZ      = 3.5
TX_POWER_DBM  = 30.0
NOISE_FLOOR   = -95.0
UE_HEIGHT     = 0.5
UAV_DEFAULT_H = 30.0
A_COEFF       = 9.61
B_COEFF       = 0.16
ETA_LOS       = 1.0
ETA_NLOS      = 20.0

UE_INTERFACES = {
    'sar_ground_alpha': 'uesimtun0',
    'sar_ground_bravo': 'uesimtun1',
}

# ── Path loss model ─────────────────────────────────────────────
def a2g_path_loss(d_horiz, h_uav, h_ue=UE_HEIGHT, freq_ghz=FREQ_GHZ):
    d_3d  = math.sqrt(d_horiz**2 + (h_uav - h_ue)**2)
    d_3d  = max(d_3d, 1.0)
    fspl  = 20*math.log10(d_3d) + 20*math.log10(freq_ghz*1e9) - 147.55
    theta = math.degrees(math.atan2(h_uav - h_ue, max(d_horiz, 0.1)))
    los_p = 1.0 / (1.0 + A_COEFF * math.exp(-B_COEFF * (theta - A_COEFF)))
    pl    = fspl + los_p * ETA_LOS + (1.0 - los_p) * ETA_NLOS
    return pl, los_p, d_3d, theta

def estimate_rsrp(pl): return TX_POWER_DBM - pl
def estimate_throughput_mbps(sinr_db, bw_mhz=20.0):
    """Shannon capacity: C = BW * log2(1 + SINR)"""
    sinr_linear = 10 ** (sinr_db / 10.0)
    capacity_bps = bw_mhz * 1e6 * math.log2(1 + sinr_linear)
    return capacity_bps / 1e6  # return in Mbps

def estimate_throughput_mbps(sinr_db, bw_mhz=20.0):
    """Shannon capacity: C = BW * log2(1 + SINR)"""
    sinr_linear = 10 ** (sinr_db / 10.0)
    return bw_mhz * math.log2(1 + sinr_linear)  # Mbps

def estimate_sinr(rsrp):
    return 10 * math.log10(10**(rsrp/10) / 10**(NOISE_FLOOR/10))

def measure_latency(target_ip, iface, count=3):
    try:
        r = subprocess.run(
            ['ping', '-I', iface, '-c', str(count), '-W', '1', target_ip],
            capture_output=True, text=True, timeout=10)
        for line in r.stdout.splitlines():
            if 'rtt' in line:
                return float(line.split('=')[1].strip().split('/')[1])
    except Exception:
        pass
    return -1.0

# ── ROS 2 Node ──────────────────────────────────────────────────
class SARIntegrationNode(Node):

    def __init__(self):
        super().__init__('sar_integration_node')

        # Robot pose storage {name: (x,y,z)}
        self.positions = {
            'sar_uav':          (0.0, 0.0, UAV_DEFAULT_H),
            'sar_ground_alpha': (15.0, 0.0, 0.0),
            'sar_ground_bravo': (30.0, 0.0, 0.0),
        }
        self._pose_lock = threading.Lock()

        # Start gz-transport subscriber in background thread
        self._gz_node = GzNode()
        self._start_gz_subscriber()

        # ROS publishers
        self.metrics_pub = self.create_publisher(Float32MultiArray, '/sar/network_metrics', 10)
        self.status_pub  = self.create_publisher(String, '/sar/status', 10)

        # CSV log
        log_dir = os.path.expanduser('~/sar_sim/logs')
        os.makedirs(log_dir, exist_ok=True)
        ts = datetime.now().strftime('%Y%m%d_%H%M%S')
        self._csv = open(f'{log_dir}/sar_metrics_{ts}.csv', 'w', newline='')
        self._writer = csv.writer(self._csv)
        self._writer.writerow([
            'timestamp', 'robot',
            'd_3d_m', 'd_horiz_m', 'elevation_deg',
            'path_loss_db', 'los_prob',
            'rsrp_dbm', 'sinr_db', 'throughput_mbps', 'latency_ms',
            'uav_x', 'uav_y', 'uav_z',
            'ue_x', 'ue_y', 'ue_z',
        ])
        self.get_logger().info(f'Logging to: {log_dir}/sar_metrics_{ts}.csv')

        # Latency cache
        self._latency = {'sar_ground_alpha': -1.0, 'sar_ground_bravo': -1.0}
        self._start_latency_thread()

        # Compute every 2 seconds
        self.create_timer(2.0, self._compute)
        self.get_logger().info('═══ SAR Integration Node started (gz-transport direct) ═══')

    # ── gz-transport pose subscriber ────────────────────────────
    def _start_gz_subscriber(self):
        def _gz_thread():
            def _pose_cb(msg):
                with self._pose_lock:
                    for pose in msg.pose:
                        if pose.name in self.positions:
                            self.positions[pose.name] = (
                                pose.position.x,
                                pose.position.y,
                                pose.position.z,
                            )
            self._gz_node.subscribe(
                Pose_V,
                '/world/sar_world/pose/info',
                _pose_cb
            )
            # Keep thread alive
            import time
            while rclpy.ok():
                time.sleep(0.1)

        t = threading.Thread(target=_gz_thread, daemon=True)
        t.start()
        self.get_logger().info('gz-transport subscriber started on /world/sar_world/pose/info')

    # ── Latency thread ───────────────────────────────────────────
    def _start_latency_thread(self):
        def _loop():
            ue_ips = self._get_ue_ips()
            import time
            while rclpy.ok():
                for robot, iface in UE_INTERFACES.items():
                    other_iface = [v for k,v in UE_INTERFACES.items() if k != robot][0]
                    target_ip = ue_ips.get(other_iface)
                    if target_ip:
                        self._latency[robot] = measure_latency(target_ip, iface)
                time.sleep(5)
        threading.Thread(target=_loop, daemon=True).start()

    def _get_ue_ips(self):
        ips = {}
        try:
            r = subprocess.run(['ip', 'addr'], capture_output=True, text=True)
            cur = None
            for line in r.stdout.splitlines():
                if line and not line.startswith(' '):
                    cur = line.split(':')[1].strip().split('@')[0]
                if 'inet ' in line and cur in UE_INTERFACES.values():
                    ips[cur] = line.strip().split()[1].split('/')[0]
        except Exception:
            pass
        return ips

    # ── Metrics computation ──────────────────────────────────────
    def _compute(self):
        with self._pose_lock:
            positions = dict(self.positions)

        uav_x, uav_y, uav_z = positions['sar_uav']
        h_uav = max(uav_z, 1.0)

        for robot in ['sar_ground_alpha', 'sar_ground_bravo']:
            ue_x, ue_y, ue_z = positions[robot]
            d_horiz = math.sqrt((uav_x-ue_x)**2 + (uav_y-ue_y)**2)
            pl, los_p, d_3d, theta = a2g_path_loss(d_horiz, h_uav, ue_z)
            rsrp   = estimate_rsrp(pl)
            sinr   = estimate_sinr(rsrp)
            tput   = estimate_throughput_mbps(sinr)
            lat_ms = self._latency.get(robot, -1.0)

            self.get_logger().info(
                f'[{robot}] '
                f'd={d_3d:.1f}m  el={theta:.1f}°  '
                f'PL={pl:.1f}dB  LoS={los_p:.2f}  '
                f'RSRP={rsrp:.1f}dBm  SINR={sinr:.1f}dB  Tput={tput:.1f}Mbps  '
                f'RTT={lat_ms:.1f}ms'
            )

            self._writer.writerow([
                datetime.now().isoformat(), robot,
                round(d_3d,2), round(d_horiz,2), round(theta,2),
                round(pl,2), round(los_p,3),
                round(rsrp,2), round(sinr,2), round(lat_ms,2),
                round(uav_x,2), round(uav_y,2), round(uav_z,2),
                round(ue_x,2), round(ue_y,2), round(ue_z,2),
            ])
            self._csv.flush()

            msg = Float32MultiArray()
            msg.data = [float(d_3d), float(pl), float(los_p),
                        float(rsrp), float(sinr), float(lat_ms)]
            self.metrics_pub.publish(msg)

        s = String()
        s.data = f'UAV@({uav_x:.1f},{uav_y:.1f},{uav_z:.1f})'
        self.status_pub.publish(s)

    def destroy_node(self):
        self._csv.close()
        super().destroy_node()


def main(args=None):
    rclpy.init(args=args)
    node = SARIntegrationNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()

if __name__ == '__main__':
    main()
# This file already has the implementation
