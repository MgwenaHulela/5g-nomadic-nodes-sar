"""Table 3: measured vs model-recovered between-terrain contrasts of the
altitude effect (change in C2 availability per +10 m), per measurement cycle
and on a uniform 60-s time base. * = 95 % block-bootstrap CI excludes 0.
Sources: logs/ml/phase4b_interactions.csv (per cycle, h = 60 s run: kinds
measured, raw), logs/ml/phase4b_h20/phase4b_interactions.csv (per cycle,
h = 20 s run: kind raw), logs/ml/phase4c_timebase.csv (measured, kind
interaction, 60-s bins). Recovered = LightGBM onset model (fit_calibrated,
onset_forecast.py), uncalibrated risk, LOTO. Scripts:
scripts/ml/sign_flip_calibrated.py, sign_flip_timebase.py,
sign_flip_recovered_timebase.py. Writes tab_contrasts.tex / .csv."""
import pandas as pd
from style import tex_table, OUT

PAIRS = ["open_field-forest", "open_field-hills", "urban-forest", "hills-forest"]
NICE = {"open_field-forest": "Open $-$ forest", "open_field-hills": "Open $-$ hills",
        "urban-forest": "Urban $-$ forest", "hills-forest": "Hills $-$ forest"}
b = pd.read_csv("logs/ml/phase4b_interactions.csv")
c = pd.read_csv("logs/ml/phase4c_timebase.csv"); c = c[c.kind == "interaction"].rename(columns={"terrain": "pair", "effect_per_10m": "diff_per_10m"})
d = pd.read_csv("logs/ml/phase4d_interactions.csv")
b20 = pd.read_csv("logs/ml/phase4b_h20/phase4b_interactions.csv")
BIN = 60


def get(df, pair):
    r = df[df.pair == pair].iloc[0]
    return r.diff_per_10m, bool(r.lo > 0 or r.hi < 0)


rows, out = [], []
for p in PAIRS:
    vals = [get(b[b.kind == "measured"], p), get(c[c.bin_s == BIN], p),
            get(b20[b20.kind == "raw"], p), get(b[b.kind == "raw"], p)]
    rows.append([NICE[p]] + [f"${v:+.3f}${'$^*$' if s else ''}" for v, s in vals])
    out.append(dict(pair=p, meas_cycle=vals[0][0], meas_60bin=vals[1][0], rec_h20=vals[2][0], rec_h60=vals[3][0],
                    sig=[s for _, s in vals]))
tex_table(f"{OUT}/tab_contrasts.tex",
          ["Contrast", "Cycle", "60-s bins", "$h{=}20$\\,s", "$h{=}60$\\,s"], rows, "@{}lcccc@{}",
          note="measured: per cycle and 60-s uniform bins; recovered (LightGBM): per cycle at horizons 20 s and 60 s")
pd.DataFrame(out).to_csv(f"{OUT}/tab_contrasts.csv", index=False)
print(pd.DataFrame(out).round(4).to_string(index=False))
r120 = d[(d.kind == "recovered_raw") & (d.bin_s == 120) & (d.pair == "open_field-forest")].iloc[0]
print(f"NOTE 120-s recovered_raw open-forest {r120.diff_per_10m:+.4f} [{r120.lo:+.4f},{r120.hi:+.4f}] p={r120.p_two_sided}")
