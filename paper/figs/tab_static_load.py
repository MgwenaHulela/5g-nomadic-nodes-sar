"""Table 2: static forest load test, no cap / fixed 100 kbit cap / adaptive cap.
Runs each test's own analyser (logs/<test>/analyse.py) and parses its output;
recovery from logs/congestion_test_20261111/recovery.txt.
Writes tab_static_load.tex and tab_static_load.csv."""
import re
import subprocess
import pandas as pd
from style import tex_table, OUT

TESTS = [("No cap", "logs/congestion_test_20261111"),
         ("Fixed 100 kb/s", "logs/congestion_test_cap_20261111"),
         ("Adaptive", "logs/congestion_test_adaptive_20261111")]
rec = []
for name, T in TESTS:
    out = subprocess.run(["python3", f"{T}/analyse.py", T], capture_output=True, text=True).stdout
    on = [(float(a), float(b)) for a, b in re.findall(
        r"^\w+\s+ul\s+loss\s+[\d.]+% rtt\s+\S+ms loss\s+([\d.]+)% rtt\s+([\d.na]+)ms", out, re.M)
        if b != "nan"]
    loss = [a for a, _ in on]; rtt = [b for _, b in on]
    lost = re.search(r"video on\s+all 8 probes lost\s+([\d.]+)%", out).group(1)
    lostoff = re.search(r"video off\s+all 8 probes lost\s+([\d.]+)%", out).group(1)
    vid = re.findall(r"goodput med ([\d.]+) Mb/s\s+latency med (\d+) ms", out)
    gp = [float(g) for g, _ in vid]; lat = [int(l) / 1000 for _, l in vid]
    rec.append(dict(test=name, ul_loss_lo=min(loss), ul_loss_hi=max(loss), rtt_lo_ms=min(rtt),
                    rtt_hi_ms=max(rtt), all_lost_on=float(lost), all_lost_off=float(lostoff),
                    gp_lo=min(gp), gp_hi=max(gp), lat_lo_s=min(lat), lat_hi_s=max(lat)))
d = pd.DataFrame(rec); d.to_csv(f"{OUT}/tab_static_load.csv", index=False)
r = open("logs/congestion_test_20261111/recovery.txt").read()
stop = re.search(r"video stopped at (\d+):(\d+):(\d+); final (\d+):(\d+):(\d+)", r).groups()
s_stop = int(stop[0]) * 3600 + int(stop[1]) * 60 + int(stop[2]); s_fin = int(stop[3]) * 3600 + int(stop[4]) * 60 + int(stop[5])
recs = [int(h) * 3600 + int(m) * 60 + int(s) - s_stop for h, m, s in re.findall(r"recovered at (\d+):(\d+):(\d+)", r)]
unrec = "NOT recovered" in r
recov_txt = f"{min(recs)/60:.0f}--{(s_fin - s_stop)/60:.0f}+\\,min" if unrec else f"{min(recs)/60:.0f}--{max(recs)/60:.0f}\\,min"


def rng(a, b, fmt="{:.0f}", unit=""):
    return (fmt.format(a) if fmt.format(a) == fmt.format(b) else f"{fmt.format(a)}--{fmt.format(b)}") + unit


def rtt(a, b):
    return rng(a / 60000, b / 60000, "{:.0f}", "\\,min") if a > 10000 else rng(a, b, "{:.0f}", "\\,ms")


def lat(a, b):
    return rng(a / 60, b / 60, "{:.1f}", "\\,min") if a > 60 else rng(a, b, "{:.2f}", "\\,s")


rows = [
    ["Uplink ping loss, video on"] + [rng(x.ul_loss_lo, x.ul_loss_hi, unit="\\,\\%") for x in d.itertuples()],
    ["Uplink ping RTT, median"] + [rtt(x.rtt_lo_ms, x.rtt_hi_ms) for x in d.itertuples()],
    ["All probes lost (on / off)"] + [f"{x.all_lost_on:.0f} / {x.all_lost_off:.0f}\\,\\%" for x in d.itertuples()],
    ["Recovery after video"] + [recov_txt, "none", "none"],
    ["Video goodput (Mb/s)"] + [rng(x.gp_lo, x.gp_hi, "{:.2f}") for x in d.itertuples()],
    ["Video latency, median"] + [lat(x.lat_lo_s, x.lat_hi_s) for x in d.itertuples()],
]
tex_table(f"{OUT}/tab_static_load.tex", ["", "No cap", "Fixed 100\\,kb/s", "Adaptive"], rows, "@{}lccc@{}")
print(d.round(2).to_string(index=False)); print("recovery", recov_txt)
