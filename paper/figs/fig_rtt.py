"""Fig. 2: uplink C2 ping RTT per robot, static forest load test, (a) no cap,
(b) adaptive cap. Sources: logs/congestion_test_20261111/ul_*.txt,
logs/congestion_test_adaptive_20261111/ul_*.txt (send time = seq - 1 s)."""
import re
import numpy as np
import matplotlib.pyplot as plt
from style import setup, FULL_W, GREYS, LINES, OUT

setup()
ROB = ["alpha", "bravo", "charlie", "delta"]


def pings(f):
    rx = {}
    for line in open(f):
        m = re.match(r"\[([\d.]+)\].*icmp_seq=(\d+).*time=([\d.]+)", line)
        if m:
            rx[int(m.group(2))] = float(m.group(3))
    return rx


def vtimes(T):
    v = open(f"{T}/video.txt").read()
    return (float(re.search(r"video running ([\d.]+)", v).group(1)),
            float(re.search(r"video stopped ([\d.]+)", v).group(1)))


fig, axes = plt.subplots(1, 2, figsize=(FULL_W, 1.9), sharey=True)
for ax, (T, title) in zip(axes, [("logs/congestion_test_20261111", "(a) no cap"),
                                 ("logs/congestion_test_adaptive_20261111", "(b) adaptive cap")]):
    t0 = float(open(f"{T}/t_start").read()); von, voff = vtimes(T)
    ax.axvspan(von - t0, voff - t0, color="0.9", lw=0)
    for r, g, ls in zip(ROB, GREYS, LINES):
        rx = pings(f"{T}/ul_{r}.txt"); s = np.array(sorted(rx))
        ax.plot(s - 1, np.array([rx[n] for n in s]) / 1000, color=g, ls=ls, lw=0.8, label=r)
    ax.set_yscale("log"); ax.set_ylim(0.02, 600); ax.set_xlim(0, 620)
    ax.set_xlabel("time since test start (s)"); ax.set_title(title, fontsize=8)
    ax.text(voff - t0 - 8, 250, "video on", ha="right", fontsize=7)
axes[0].set_ylabel("uplink C2 RTT (s)")
lg = fig.legend(*axes[0].get_legend_handles_labels(), loc="upper center", ncol=4,
                bbox_to_anchor=(0.5, 1.08))
for h in lg.get_lines():
    h.set_linewidth(1.6)
fig.savefig(f"{OUT}/fig_rtt.pdf")
print("fig_rtt.pdf")
