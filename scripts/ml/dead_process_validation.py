"""Validate the dead-process rule (onset_forecast.dead_ue_mask) against the
watchdog's own record (review item K8 / R1-W9 / DA-M7).

Ground truth proxy: the per-cycle `<robot>_event` column written by the
research loop. `reattaching` marks a robot the watchdog had found with its UE
process dead or its TUN gone and was relaunching; `reattach_failed` a relaunch
that failed. In most October runs the watchdog thread died mid-reattach
(roster KeyError), so the flag stays set while the UE stays down. dataset.py
coerces columns to numbers and drops this text column, so it is re-read from
the raw CSVs and joined on (run_id, robot, t_elapsed_s).

Reports, over C2-down cycles (where the rule can fire):
  precision = P(watchdog says dead | rule flags dead)
  recall    = P(rule flags dead | watchdog says dead)
and the C2-down cycles the rule censors although the watchdog saw a live UE.

    python3 scripts/ml/dead_process_validation.py
writes logs/ml/dead_process_validation.csv
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import numpy as np
import pandas as pd
import dataset as ds
from onset_forecast import dead_ue_mask

raw = ds.build().sort_values(["run_id", "robot", "t_elapsed_s"], kind="stable").reset_index(drop=True)
raw["dead"] = dead_ue_mask(raw)
ev = []
for m in ds.discover_runs():
    w = pd.read_csv(m.cycle_csv, low_memory=False)
    for pfx, robot in ds.ROBOT_OF.items():
        if f"{pfx}_event" in w:
            ev.append(pd.DataFrame({"run_id": m.run_id, "robot": robot,
                                    "t_elapsed_s": pd.to_numeric(w["t_elapsed_s"], errors="coerce"),
                                    "event_txt": w[f"{pfx}_event"].astype(str).replace("nan", "")}))
ev = pd.concat(ev, ignore_index=True)
d = raw.merge(ev, on=["run_id", "robot", "t_elapsed_s"], how="left")
assert len(d) == len(raw), "join changed row count"
d["wd_dead"] = d.event_txt.isin(["reattaching", "reattach_failed"])
down = d[d.c2_ok == 0]
rows = []
for t, g in list(down.groupby("terrain")) + [("ALL", down)]:
    tp = (g.dead & g.wd_dead).sum(); fp = (g.dead & ~g.wd_dead).sum(); fn = (~g.dead & g.wd_dead).sum()
    rows.append(dict(terrain=t, c2_down=len(g), rule_flags=int(g.dead.sum()), watchdog_dead=int(g.wd_dead.sum()),
                     both=int(tp), rule_only=int(fp), watchdog_only=int(fn),
                     precision=tp / max(tp + fp, 1), recall=tp / max(tp + fn, 1)))
o = pd.DataFrame(rows)
print(o.round(3).to_string(index=False))
print("event coverage: rows with any event text", (d.event_txt != "").mean().round(3))
o.to_csv("logs/ml/dead_process_validation.csv", index=False)

# The watchdog thread crashed in most October runs, so "no event" often means
# "no watchdog", not "UE alive". Restrict to (run, robot) series in which the
# watchdog wrote at least one event, i.e. it was demonstrably running.
cov = d.groupby(["run_id", "robot"]).event_txt.apply(lambda s: (s != "").any()).rename("wd_ever")
d = d.join(cov, on=["run_id", "robot"])
dn = d[(d.c2_ok == 0) & d.wd_ever]
tp = (dn.dead & dn.wd_dead).sum(); fp = (dn.dead & ~dn.wd_dead).sum(); fn = (~dn.dead & dn.wd_dead).sum()
cv = dict(terrain="ALL, watchdog-covered series", c2_down=len(dn), rule_flags=int(dn.dead.sum()),
          watchdog_dead=int(dn.wd_dead.sum()), both=int(tp), rule_only=int(fp), watchdog_only=int(fn),
          precision=tp / max(tp + fp, 1), recall=tp / max(tp + fn, 1),
          series_covered=int(cov.sum()), series_total=len(cov),
          rule_flags_uncheckable=int(d[(d.c2_ok == 0) & ~d.wd_ever].dead.sum()))
o = pd.concat([o, pd.DataFrame([cv])], ignore_index=True)
print(pd.DataFrame([cv]).round(3).to_string(index=False))
o.to_csv("logs/ml/dead_process_validation.csv", index=False)
