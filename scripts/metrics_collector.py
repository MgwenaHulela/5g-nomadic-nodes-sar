#!/usr/bin/env python3
"""
metrics_collector.py — Parses OAI UE logs to extract PHY metrics in real time.

Tails the UE log files for alpha and bravo, extracts periodic stats blocks,
estimates SINR from spectral efficiency, tracks BLER, and writes a CSV.

Usage:
  python3 scripts/metrics_collector.py                         # both UEs
  python3 scripts/metrics_collector.py --duration 60            # run 60s
  python3 scripts/metrics_collector.py --ue-log /tmp/ue_alpha.log  # single UE

Output: logs/metrics_<timestamp>.csv
"""

import csv
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

LOG_DIR = Path.home() / "sar_sim" / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)

DEFAULT_LOGS = {
    "alpha": "/tmp/ue_alpha.log",
    "bravo": "/tmp/ue_bravo.log",
}
OUTPUT = LOG_DIR / f"metrics_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"

# Regex patterns
RE_PBCH_RSRP = re.compile(r"pbch rx ok\. rsrp:([\d.]+) dB/RE")
RE_SS_RSRP = re.compile(r"SS-RSRP:\s*([-\d]+)\s*dBm/RE")
RE_SS_SINR = re.compile(r"SS-SINR:\s*([-\d.]+)\s*dB")
RE_DL_HARQ = re.compile(r"DL harq:\s*([\d]+)/([\d]+)")
RE_UL_HARQ = re.compile(
    r"UL harq:\s*[\d]+(?:/[\d]+)*\s+"
    r"avg code rate ([\d.]+),\s*"
    r"avg bit/symbol ([\d.]+),\s*"
    r"avg per TB:\s*\(nb RBs ([\d.]+),\s*nb symbols ([\d.]+)\)"
)


def tail_file(path, seen_bytes=0, max_read=50000, max_backlog=4_000_000):
    """Read new lines from a file since last read (max `max_read` lines).

    Never reads more than `max_backlog` bytes behind the file's end: a
    truncated-while-open log becomes SPARSE (the writer keeps its old
    offset), and readline() scanning a multi-GB hole of zeros for a
    newline inflates it all into RAM — this OOM-killed two patrols
    (14.7GB RSS) on 2026-07-12.
    """
    try:
        size = os.path.getsize(path)
        if size < seen_bytes:
            seen_bytes = 0
        if size - seen_bytes > max_backlog:
            seen_bytes = size - max_backlog
        with open(path, "r", errors="replace") as f:
            f.seek(seen_bytes)
            lines = []
            for _ in range(max_read):
                l = f.readline()
                if not l:
                    break
                lines.append(l)
            seen_bytes = f.tell()
        return lines, seen_bytes
    except (FileNotFoundError, PermissionError):
        return [], seen_bytes


def parse_ue_metrics(lines, prev=None):
    """Parse OAI UE log lines and return latest metrics dict.

    If `prev` is provided, missing fields are carried over from it,
    so sparse metrics (e.g. PBCH RSRP) persist across calls.

    Returns: {
        "pbch_rsrp": float | None,
        "ss_rsrp": float | None,
        "ss_sinr": float | None,
        "dl_harq_ok": int, "dl_harq_nok": int,
        "ul_code_rate": float, "ul_bits_per_sym": float,
        "ul_nb_rbs": float, "ul_nb_sym": float,
        "dl_bler": float, "ul_bler": float,
    }
    """
    prev = prev or {}
    m = {}
    pbch_rsrp = prev.get("pbch_rsrp")  # carry over sparse metrics
    ss_rsrp = prev.get("ss_rsrp")
    ss_sinr = prev.get("ss_sinr")
    dl_ok, dl_nok = 0, 0  # fresh cumulative from this chunk only
    ul_stats = []

    for line in lines:
        # Continuous PBCH RSRP
        r = RE_PBCH_RSRP.search(line)
        if r:
            pbch_rsrp = float(r.group(1))

        # SS-RSRP (soak test format, at initial sync)
        r = RE_SS_RSRP.search(line)
        if r:
            ss_rsrp = float(r.group(1))

        # SS-SINR (soak test format, at initial sync)
        r = RE_SS_SINR.search(line)
        if r:
            ss_sinr = float(r.group(1))

        # Stats block: DL harq
        if "DL harq:" in line:
            r = RE_DL_HARQ.search(line)
            if r:
                dl_ok += int(r.group(1))
                dl_nok += int(r.group(2))

        # Stats block: UL harq with code rate / bit/symbol / RB / sym
        if "avg code rate" in line:
            r = RE_UL_HARQ.search(line)
            if r:
                ul_stats.append({
                    "code_rate": float(r.group(1)),
                    "bits_per_sym": float(r.group(2)),
                    "nb_rbs": float(r.group(3)),
                    "nb_sym": float(r.group(4)),
                })

    m["pbch_rsrp"] = pbch_rsrp
    m["ss_rsrp"] = ss_rsrp
    m["ss_sinr"] = ss_sinr
    m["dl_harq_ok"] = dl_ok
    m["dl_harq_nok"] = dl_nok
    total_dl = dl_ok + dl_nok
    m["dl_bler"] = dl_nok / total_dl if total_dl > 0 else 0.0

    if ul_stats:
        latest = ul_stats[-1]
        m["ul_code_rate"] = latest["code_rate"]
        m["ul_bits_per_sym"] = latest["bits_per_sym"]
        m["ul_nb_rbs"] = latest["nb_rbs"]
        m["ul_nb_sym"] = latest["nb_sym"]
        # UL BLER (cumulative over run)
        ul_ok = sum(u["code_rate"] for u in ul_stats)  # dummy
        ul_nok = 0  # UL harq only has success count in this format
        m["ul_bler"] = 0.0
    else:
        m["ul_code_rate"] = 0.0
        m["ul_bits_per_sym"] = 0.0
        m["ul_nb_rbs"] = 0.0
        m["ul_nb_sym"] = 0.0
        m["ul_bler"] = 0.0

    return m


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--duration", type=int, default=0,
                        help="Run duration in seconds (0 = forever)")
    parser.add_argument("--ue-log", action="append",
                        help="Path to UE log (can be given twice: alpha + bravo)")
    args = parser.parse_args()

    ue_logs = {}
    if args.ue_log:
        for i, path in enumerate(args.ue_log):
            label = f"ue{i}"
            ue_logs[label] = path
    else:
        ue_logs = {"alpha": DEFAULT_LOGS["alpha"], "bravo": DEFAULT_LOGS["bravo"]}

    seen = {label: 0 for label in ue_logs}
    prev_metrics = {label: {} for label in ue_logs}

    with open(OUTPUT, "w", newline="") as f:
        w = csv.writer(f)
        header = ["timestamp"]
        for label in ue_logs:
            header += [
                f"{label}_pbch_rsrp",
                f"{label}_ss_rsrp_dBm",
                f"{label}_ss_sinr_dB",
                f"{label}_ul_bits_per_sym",
                f"{label}_ul_code_rate",
                f"{label}_ul_nb_rbs", f"{label}_ul_nb_sym",
                f"{label}_dl_harq_ok", f"{label}_dl_harq_nok",
                f"{label}_dl_bler",
            ]
        w.writerow(header)

        t0 = time.time()
        try:
            while True:
                if args.duration and (time.time() - t0) > args.duration:
                    break

                row = [datetime.now(timezone.utc).isoformat()]

                for label, log_path in ue_logs.items():
                    lines, seen[label] = tail_file(log_path, seen[label])
                    m = parse_ue_metrics(lines)
                    if m:
                        prev_metrics[label] = m
                    m = prev_metrics[label]

                    row += [
                        m.get("pbch_rsrp", ""),
                        m.get("ss_rsrp", ""),
                        m.get("ss_sinr", ""),
                        m.get("ul_bits_per_sym", ""),
                        m.get("ul_code_rate", ""),
                        m.get("ul_nb_rbs", ""),
                        m.get("ul_nb_sym", ""),
                        m.get("dl_harq_ok", ""),
                        m.get("dl_harq_nok", ""),
                        m.get("dl_bler", ""),
                    ]

                w.writerow(row)
                f.flush()
                time.sleep(5)

        except KeyboardInterrupt:
            print("\nStopped.")

    print(f"Metrics saved: {OUTPUT}")


if __name__ == "__main__":
    main()
