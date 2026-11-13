#!/usr/bin/env python3
"""Side-car sampler for UE CSI reports (SINR + CQI).

WHY THIS EXISTS
---------------
`fast_radio_sampler.py` parses the UE's SS-RSRP line, which OAI emits from
`nr_ue_measurements.c` under LOG_D(PHY, ...) — i.e. only when
`phy_log_level = "debug"`. On 2026-10-23 the UE configs were set to
`"error"` to stop a 1.6 GB log flood and fix a UE reattach failure. That
silenced the SS line, so every `*_fastradio.csv` in the 2026-10-23..25
campaign has empty RSRP/SINR columns and `ss_stale = 1` on every row.

The UE still logs its CSI report at the *current* level:

    [NR_PHY] RI = 1 i1 = 0.0.0, i2 = 0, SINR = 23 dB, CQI = 15

at roughly 7 Hz per robot. That is a genuine UE-side measurement — an
ALLOWED model input, unlike `ploss_dB` / `rsrp_computed_dBm` — so this
sampler reads it directly. It touches no OAI or Open5GS configuration and
needs no stack restart: it only reads log files that are already written.

`n_samples == 0` means nothing fresh arrived in that window; the value
columns are then left EMPTY rather than carried forward, so a consumer can
never mistake a stale reading for a live one.

Usage:
  python3 scripts/csi_sidecar.py --rate-hz 5
  python3 scripts/csi_sidecar.py --robots alpha,bravo --out /tmp/csi.csv
"""
import argparse
import csv
import os
import re
import signal
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from metrics_collector import tail_file  # noqa: E402  (truncation-safe tailer)

LOG_DIR = Path(os.environ.get("SAR_LOG_DIR", Path.home() / "sar_sim" / "logs"))
DEFAULT_ROBOTS = os.environ.get("SAR_ROBOTS", "alpha,bravo,charlie,delta")

# The CSI report line. ANSI colour codes sit outside the match, so a plain
# search over the raw text is fine.
RE_CSI = re.compile(r"SINR\s*=\s*(-?\d+)\s*dB,\s*CQI\s*=\s*(\d+)")

PER_ROBOT_COLS = [
    "sinr_dB",       # last sample in the window
    "sinr_dB_mean",  # mean over the window, not a decimated point sample
    "sinr_dB_min",   # worst sample — preserves fades a point sample misses
    "sinr_dB_max",
    "cqi",
    "cqi_mean",
    "n_samples",     # fresh CSI lines this window; 0 => every value empty
]

_running = True


def _stop(_sig, _frm):
    global _running
    _running = False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--robots", default=DEFAULT_ROBOTS,
                    help="comma-separated roster (default $SAR_ROBOTS)")
    ap.add_argument("--rate-hz", type=float, default=5.0,
                    help="sampling rate (default 5 Hz; CSI arrives at ~7 Hz)")
    ap.add_argument("--out", default=None,
                    help="output CSV (default logs/csi_<timestamp>.csv)")
    ap.add_argument("--ue-log", action="append", metavar="LABEL=PATH",
                    help="override a UE log, e.g. alpha=/tmp/ue_alpha.log")
    args = ap.parse_args()

    robots = [r.strip() for r in args.robots.split(",") if r.strip()]
    if not robots:
        sys.exit("FATAL: empty roster")

    logs = {r: f"/tmp/ue_{r}.log" for r in robots}
    for spec in args.ue_log or []:
        if "=" not in spec:
            ap.error(f"--ue-log needs LABEL=PATH, got {spec!r}")
        label, path = spec.split("=", 1)
        logs[label] = path

    out = Path(args.out) if args.out else (
        LOG_DIR / f"csi_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv")
    if out.exists():
        sys.exit(f"FATAL: {out} already exists — refusing to clobber")
    out.parent.mkdir(parents=True, exist_ok=True)

    # Start at each log's CURRENT end so a pre-existing backlog is not
    # ingested and stamped with the wrong wall-clock time.
    offs = {}
    for r, p in logs.items():
        try:
            offs[r] = os.path.getsize(p)
        except OSError:
            offs[r] = 0
            print(f"[csi] warning: {p} not readable yet", flush=True)

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)

    header = ["timestamp", "t_elapsed_s"]
    for r in robots:
        header += [f"{r}_{c}" for c in PER_ROBOT_COLS]

    period = 1.0 / args.rate_hz
    t0 = time.time()
    n_rows = 0
    print(f"[csi] {len(robots)} robots -> {out} at {args.rate_hz} Hz", flush=True)

    with open(out, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(header)
        next_tick = time.time()
        while _running:
            next_tick += period
            sleep = next_tick - time.time()
            if sleep > 0:
                time.sleep(sleep)
            else:
                next_tick = time.time()  # fell behind; resync rather than spin

            now = time.time()
            row = [datetime.now(timezone.utc).isoformat(), f"{now - t0:.3f}"]
            for r in robots:
                lines, offs[r] = tail_file(logs[r], offs[r])
                sinr, cqi = [], []
                for ln in lines:
                    m = RE_CSI.search(ln)
                    if m:
                        sinr.append(int(m.group(1)))
                        cqi.append(int(m.group(2)))
                if sinr:
                    row += [sinr[-1],
                            f"{sum(sinr) / len(sinr):.2f}",
                            min(sinr), max(sinr),
                            cqi[-1],
                            f"{sum(cqi) / len(cqi):.2f}",
                            len(sinr)]
                else:
                    row += ["", "", "", "", "", "", 0]
            w.writerow(row)
            n_rows += 1
            if n_rows % 50 == 0:
                fh.flush()

    print(f"[csi] stopped after {n_rows} rows -> {out}", flush=True)


if __name__ == "__main__":
    main()
