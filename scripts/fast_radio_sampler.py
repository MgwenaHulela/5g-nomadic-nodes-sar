#!/usr/bin/env python3
"""
fast_radio_sampler.py — high-cadence per-UE radio sampling, decoupled from the
traffic cycle.

WHY THIS EXISTS
---------------
`auto_patrol.py` writes one row per measurement cycle, and that cycle is a
*sequential* battery of iperf3-TCP, iperf3-UDP and ping probes. Two
consequences, both fatal to a predictive model (DATA_AUDIT.md 4.1, 4.2):

  1. The median cycle is 22.8 s, so a 30 s history window spans ~1.3 rows and
     horizons of 1-10 s are shorter than a single sample.
  2. A bad link makes the probes take LONGER, so the sampling rate is
     correlated with the thing being predicted (median 22.1 s when C2 is up,
     35.8 s when it is down). Row-weighted averages silently under-weight the
     bad periods.

But the radio data itself is not slow. `nr-uesoftmodem` prints a combined
SS-RSRP/SS-SINR line every 2 NR frames — 20 ms, 50 Hz, measured over
4608/4608 consecutive intervals with zero jitter (DATA_AUDIT.md 4.1b). The
cadence ceiling was never the stack; it was the collector sampling that stream
once per traffic cycle and carrying the value forward.

This sampler reads that stream directly at a fixed wall-clock rate. It touches
no OAI or Open5GS configuration — it only reads the UE log files that are
already being written. Run it alongside `auto_patrol.py`; the two write
separate CSVs that join on `run_id` + timestamp.

WHAT IT RECORDS
---------------
Per tick, per UE, over the lines that arrived since the previous tick:
  last / mean / min / max of the full-precision RSRP, last SINR, the number of
  fresh samples in the window, and the age of the newest measurement. The
  aggregates matter: at 10 Hz each tick covers ~5 of the 50 Hz measurements, so
  `rsrp_mean` is a real average rather than a decimated point sample, and
  `rsrp_min` preserves the fades that a point sample would miss.

`n_samples == 0` means nothing fresh arrived in that window and every value is
carried over — always filter on it before training.

USAGE
-----
  python3 scripts/fast_radio_sampler.py --run-id r001 --terrain sar_forest \\
      --seed 42 --rate 10

  # replay a captured log instead of a live one (for testing)
  python3 scripts/fast_radio_sampler.py --replay logs/archive/ue_alpha_phy.log

Output: logs/fastradio_<run_id>.csv  (never overwritten; refuses to clobber)
"""

import argparse
import csv
import os
import signal
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from metrics_collector import tail_file, parse_ue_metrics, RE_SS_COMBINED

LOG_DIR = Path.home() / "sar_sim" / "logs"

# Roster-driven, matching ROBOT_SPEC in auto_patrol.py and robots.env.
# SAR_ROBOTS selects which subset to sample, so a 2-robot run samples two
# logs and a 4-robot run samples four, with no flag changes.
ALL_UE_LOGS = {
    "alpha":   "/tmp/ue_alpha.log",
    "bravo":   "/tmp/ue_bravo.log",
    "charlie": "/tmp/ue_charlie.log",
    "delta":   "/tmp/ue_delta.log",
}
_roster = [r.strip() for r in
           os.environ.get("SAR_ROBOTS", "alpha,bravo,charlie,delta").split(",")
           if r.strip()]
for _r in _roster:
    if _r not in ALL_UE_LOGS:
        sys.exit(f"FATAL: unknown robot '{_r}' in SAR_ROBOTS — "
                 f"known: {', '.join(ALL_UE_LOGS)}")
DEFAULT_LOGS = {r: ALL_UE_LOGS[r] for r in _roster}

# Per-UE columns, in write order.
UE_COLS = [
    "rsrp_dBm",       # integer dBm, 1 dB truncation (backward compatible)
    "rsrp_dBre",      # full-precision RSRP, last sample in window
    "rsrp_dBre_mean", # mean over the window (not a decimated point sample)
    "rsrp_dBre_min",  # worst sample in the window — preserves fades
    "rsrp_dBre_max",
    "sinr_dB",
    "n_samples",      # fresh SS lines in this window; 0 => everything is stale
    "age_s",          # seconds since the newest fresh measurement
]


def parse_window(lines):
    """Aggregate every SS measurement in `lines`.

    Returns (last_dBm, last_dBre, mean_dBre, min_dBre, max_dBre, last_sinr, n).
    All-None and n=0 when the window held no fresh measurement.
    """
    dbre = []
    last_dbm = last_sinr = None
    for line in lines:
        m = RE_SS_COMBINED.search(line)
        if m:
            last_dbm = float(m.group(1))
            dbre.append(float(m.group(2)))
            last_sinr = float(m.group(3))
    if not dbre:
        return (None, None, None, None, None, None, 0)
    return (last_dbm, dbre[-1], sum(dbre) / len(dbre),
            min(dbre), max(dbre), last_sinr, len(dbre))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rate", type=float, default=10.0,
                    help="Samples per second (default 10). The underlying "
                         "stream is 50 Hz, so rates above 50 gain nothing.")
    ap.add_argument("--duration", type=float, default=0,
                    help="Seconds to run (0 = until interrupted)")
    ap.add_argument("--run-id", default=None,
                    help="Run identifier, joins this CSV to the patrol CSV")
    ap.add_argument("--seed", default="",
                    help="RNG seed of the run (recorded, not used here)")
    ap.add_argument("--terrain", default="",
                    help="World name. EVALUATION ONLY — never a model input")
    ap.add_argument("--ue-log", action="append", metavar="LABEL=PATH",
                    help="Override a UE log, e.g. alpha=/tmp/ue_alpha.log")
    ap.add_argument("--out", default=None, help="Output CSV path")
    ap.add_argument("--replay", default=None,
                    help="Parse one captured log start-to-finish and exit "
                         "(offline test; ignores --rate)")
    args = ap.parse_args()

    if args.replay:
        return replay(args)

    ue_logs = dict(DEFAULT_LOGS)
    if args.ue_log:
        ue_logs = {}
        for spec in args.ue_log:
            if "=" not in spec:
                ap.error(f"--ue-log needs LABEL=PATH, got {spec!r}")
            label, path = spec.split("=", 1)
            ue_logs[label] = path

    run_id = args.run_id or datetime.now().strftime("%Y%m%d_%H%M%S")
    out = Path(args.out) if args.out else LOG_DIR / f"fastradio_{run_id}.csv"
    out.parent.mkdir(parents=True, exist_ok=True)
    # Never clobber: a re-run with a duplicate --run-id would otherwise
    # silently destroy a completed capture.
    if out.exists():
        sys.exit(f"FATAL: {out} already exists — pick a different --run-id")

    period = 1.0 / args.rate
    # Start at each log's CURRENT end, so a pre-existing multi-GB log is not
    # replayed into the first row.
    seen = {}
    for label, path in ue_logs.items():
        try:
            seen[label] = os.path.getsize(path)
        except OSError:
            seen[label] = 0
            print(f"[fastradio] warning: {path} not readable yet", flush=True)

    header = ["timestamp", "t_elapsed_s", "run_id", "seed", "terrain"]
    for label in ue_logs:
        header += [f"{label}_{c}" for c in UE_COLS]

    running = [True]

    def stop(_sig, _frm):
        running[0] = False
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)

    last_fresh = {label: None for label in ue_logs}
    carried = {label: (None,) * 6 for label in ue_logs}
    # A UE log that never appears, or a modem that stops printing, otherwise
    # produces a CSV of empty rows that only looks wrong hours later. Warn on
    # a rising schedule (5s, 30s, 60s, then every 60s) per UE.
    STALL_WARN_S = [5.0, 30.0, 60.0]
    warned = {label: 0.0 for label in ue_logs}
    nrows = 0
    t0 = time.time()
    next_tick = t0

    print(f"[fastradio] {args.rate:g} Hz -> {out}", flush=True)
    print(f"[fastradio] run_id={run_id} terrain={args.terrain or '-'} "
          f"seed={args.seed or '-'}", flush=True)

    with open(out, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(header)
        try:
            while running[0]:
                next_tick += period
                sleep_for = next_tick - time.time()
                if sleep_for > 0:
                    time.sleep(sleep_for)
                else:
                    # Fell behind (disk stall, huge backlog). Resync rather
                    # than spinning to catch up on a schedule we cannot meet.
                    next_tick = time.time()

                now = time.time()
                if args.duration and (now - t0) > args.duration:
                    break

                row = [datetime.now(timezone.utc).isoformat(),
                       round(now - t0, 3), run_id, args.seed, args.terrain]

                for label, path in ue_logs.items():
                    # max_read caps a catch-up burst; max_backlog (inherited)
                    # guards the sparse-truncated-log OOM that killed two
                    # patrols on 2026-07-12.
                    lines, seen[label] = tail_file(path, seen[label],
                                                   max_read=20000)
                    vals = parse_window(lines)
                    n = vals[6]
                    if n > 0:
                        last_fresh[label] = now
                        carried[label] = vals[:6]
                    else:
                        vals = carried[label] + (0,)

                    # Stall detection: nothing fresh since start, or since
                    # the last measurement, for longer than expected.
                    quiet_for = now - (last_fresh[label] or t0)
                    nxt = next((w for w in STALL_WARN_S if w > warned[label]),
                               warned[label] + 60.0)
                    if quiet_for >= nxt:
                        warned[label] = quiet_for
                        why = ("no data yet" if last_fresh[label] is None
                               else "stream stopped")
                        print(f"[fastradio] WARNING: {label} {why} — "
                              f"{quiet_for:.0f}s without a fresh sample "
                              f"({path})", flush=True)
                    elif n > 0:
                        warned[label] = 0.0

                    age = ("" if last_fresh[label] is None
                           else round(now - last_fresh[label], 3))
                    row += [
                        "" if vals[0] is None else vals[0],
                        "" if vals[1] is None else round(vals[1], 6),
                        "" if vals[2] is None else round(vals[2], 6),
                        "" if vals[3] is None else round(vals[3], 6),
                        "" if vals[4] is None else round(vals[4], 6),
                        "" if vals[5] is None else vals[5],
                        n,
                        age,
                    ]

                w.writerow(row)
                nrows += 1
                if nrows % 200 == 0:
                    f.flush()
        finally:
            f.flush()

    dur = time.time() - t0
    print(f"[fastradio] {nrows} rows in {dur:.1f}s "
          f"({nrows / dur if dur else 0:.1f} rows/s) -> {out}", flush=True)
    for label in ue_logs:
        if last_fresh[label] is None:
            print(f"[fastradio] WARNING: {label} produced NO fresh samples "
                  f"for the whole run — check the UE log path", flush=True)


def replay(args):
    """Parse a captured log end-to-end and report what it contains.

    Offline sanity check — proves the regex matches a real log and shows the
    measured emission rate, without needing a live stack.
    """
    import gzip
    import re
    path = args.replay
    opener = gzip.open if path.endswith(".gz") else open
    n = 0
    dbre = []
    frames = []
    re_frame = re.compile(r"Frame (\d+), Slot \d+, Symbol \d+, SSB Index")
    pend = False
    with opener(path, "rt", errors="ignore") as f:
        for line in f:
            m = RE_SS_COMBINED.search(line)
            if m:
                n += 1
                dbre.append(float(m.group(2)))
                pend = True
                continue
            if pend:
                fm = re_frame.search(line)
                if fm:
                    frames.append(int(fm.group(1)))
                    pend = False
    print(f"replay: {path}")
    print(f"  SS measurements parsed : {n}")
    if not n:
        print("  NO MATCHES — the log format has changed; fix RE_SS_COMBINED")
        return 1
    print(f"  rsrp dB/RE unique      : {len(set(dbre))}")
    print(f"  rsrp dB/RE range       : {min(dbre):.4f} .. {max(dbre):.4f}")
    if len(frames) > 1:
        from collections import Counter
        d = Counter((frames[i + 1] - frames[i]) % 1024
                    for i in range(len(frames) - 1))
        per, cnt = d.most_common(1)[0]
        print(f"  frame period           : {per} frames = {per * 10} ms "
              f"= {1000 / (per * 10):.1f} Hz ({cnt}/{sum(d.values())} intervals)")
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
