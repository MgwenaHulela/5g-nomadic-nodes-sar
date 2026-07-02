import subprocess
import re
import time

def get_gnb_metrics():
    result = subprocess.run(
        ['tail', '-50', '/tmp/gnb_debug.log'],
        capture_output=True, text=True
    )
    
    dl_mcs, ul_mcs, dl_tbs, ul_tbs = [], [], [], []
    
    for line in result.stdout.splitlines():
        # Parse DL metrics
        dl = re.search(r'UE PDSCH.*mcs=(\d+).*tbs=(\d+)', line)
        if dl:
            dl_mcs.append(int(dl.group(1)))
            dl_tbs.append(int(dl.group(2)))
        
        # Parse UL metrics  
        ul = re.search(r'UE PUSCH.*tbs=(\d+)', line)
        if ul:
            ul_tbs.append(int(ul.group(1)))
    
    if dl_mcs:
        avg_dl_mcs = sum(dl_mcs) / len(dl_mcs)
        avg_dl_tbs = sum(dl_tbs) / len(dl_tbs)
        # Estimate throughput from TBS (bits per slot, 500 slots/sec for 30kHz SCS)
        dl_throughput_mbps = (avg_dl_tbs * 8 * 500) / 1e6
        return {
            'dl_mcs': round(avg_dl_mcs, 1),
            'dl_tbs_bytes': round(avg_dl_tbs),
            'dl_throughput_mbps': round(dl_throughput_mbps, 1),
            'ul_tbs_bytes': round(sum(ul_tbs)/len(ul_tbs)) if ul_tbs else 0
        }
    return None

# Test
metrics = get_gnb_metrics()
print(metrics)
