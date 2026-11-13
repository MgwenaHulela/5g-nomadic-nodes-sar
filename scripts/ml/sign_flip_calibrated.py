#!/usr/bin/env python3
"""Phase 4b — the sign flip, recovered from a CALIBRATED onset model.

Phase 4's recovered effect had perfect rank correlation with the measured one
(Spearman +1.000) and a uniform downward offset, so two of four terrains came
out with the wrong sign purely because the zero-crossing landed in the wrong
place. That is a calibration defect, not a perception defect, and it is
testable: refit the same terrain-blind model with isotonic calibration on an
inner split of the training terrains, and see whether the zero-crossing moves
back.

Everything else is held fixed against Phase 4b's companion (onset_forecast.py):
same dead-UE censoring, same link-UP onset population, same LOTO protocol,
same radio_blind feature set. `terrain` never reaches a model.

Usage:
    python3 scripts/ml/sign_flip_calibrated.py [--horizon 60]
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import pearsonr, spearmanr

sys.path.insert(0, str(Path(__file__).resolve().parent))
import dataset as ds                                        # noqa: E402
from forecast_cycle import HIST_COLS                        # noqa: E402
from sign_flip import first_differences, climb_effect, interaction_test  # noqa: E402
from onset_forecast import dead_ue_mask, fit_calibrated     # noqa: E402

OUT_DIR = ds.REPO / "logs" / "ml"
GROUPS = ["geom", "radio", "action"]        # radio_blind


def show(title, eff):
    print(f"\n  {title}")
    if eff is None or not len(eff):
        print("    -- insufficient data --")
        return
    for _, r in eff.iterrows():
        s = "*" if r.significant else " "
        print(f"    {r.terrain:<11}{r.effect_per_10m:+8.4f}  "
              f"[{r.lo:+7.4f}, {r.hi:+7.4f}] {s}  n={int(r.n):,}")


def as_map(eff):
    return {r.terrain: r.effect_per_10m for _, r in eff.iterrows()}


def agreement(measured: dict, recovered: dict, label: str):
    terr = [t for t in measured if t in recovered]
    m = np.array([measured[t] for t in terr])
    r = np.array([recovered[t] for t in terr])
    pe = pearsonr(m, r)[0] if len(terr) > 2 else float("nan")
    sp = spearmanr(m, r)[0] if len(terr) > 2 else float("nan")
    agree = int((np.sign(m) == np.sign(r)).sum())
    print(f"\n  {label}")
    print(f"    {'terrain':<12}{'measured':>10}{'recovered':>11}   signs")
    for t in sorted(terr, key=lambda x: -measured[x]):
        ok = "yes" if np.sign(measured[t]) == np.sign(recovered[t]) else "NO"
        print(f"    {t:<12}{measured[t]:>+10.4f}{recovered[t]:>+11.4f}   {ok}")
    print(f"    Pearson {pe:+.3f}   Spearman {sp:+.3f}   "
          f"sign agreement {agree}/{len(terr)}")
    return dict(label=label, pearson=pe, spearman=sp,
                sign_agreement=agree, n_terrain=len(terr))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--horizon", type=float, default=60.0)
    ap.add_argument("--out", default=str(OUT_DIR))
    args = ap.parse_args()
    h = args.horizon
    t0 = time.time()

    raw = ds.build().sort_values(["run_id", "robot", "t_elapsed_s"],
                                 kind="stable").reset_index(drop=True)
    raw["dead"] = dead_ue_mask(raw)
    print(f"rows {len(raw):,}   dead-UE {100*raw['dead'].mean():.1f}%")

    # ---------------- measured, censored ----------------
    print("\n" + "=" * 70)
    print("MEASURED climb effect on C2 availability -- dead-UE censored")
    print("=" * 70)
    d_cen = first_differences(raw[~raw["dead"]].copy())
    e_meas = climb_effect(d_cen, "d_c2_ok", 1, None)
    show("effect per +10 m", e_meas)
    for a, b in [("open_field", "forest"), ("urban", "forest"),
                 ("hills", "forest")]:
        r = interaction_test(d_cen, "d_c2_ok", 1, a, b)
        if r:
            s = "*" if r["significant"] else " "
            print(f"    interaction {a:<11}- {b:<7}{r['diff_per_10m']:+8.4f} "
                  f"[{r['lo']:+7.4f}, {r['hi']:+7.4f}] {s} "
                  f"p={r['p_two_sided']:.4g}")
    measured = as_map(e_meas)

    # ---------------- model-recovered ----------------
    df = ds.add_history(raw, HIST_COLS, lags=(1, 2, 3))
    d = ds.add_target(df, h, "c2_ok")
    d = ds.add_target(d, h, "dead").reset_index(drop=True)
    d["y"] = 1.0 - d[f"y_c2_ok_h{int(h)}"]
    dead_tgt = d[f"y_dead_h{int(h)}"].fillna(0) > 0.5
    d = d[d["y"].notna() & (d["c2_ok"] == 1) & ~d["dead"]
          & ~dead_tgt].reset_index(drop=True)
    feats = ds.feature_columns(d, GROUPS, with_history=True)
    ds.assert_terrain_blind(feats)
    print(f"\nonset rows {len(d):,}   FAIL within {int(h)}s "
          f"{100*d['y'].mean():.1f}%   ({len(feats)} features)")

    d["p_raw"] = np.nan
    d["p_cal"] = np.nan
    for terr, tr, te in ds.leave_one_terrain_out(d[ds.META]):
        if len(np.unique(d.loc[tr, "y"])) < 2:
            continue
        m, iso = fit_calibrated(d.loc[tr, feats], d.loc[tr, "y"].values,
                                d.loc[tr, ds.META])
        pr = m.predict_proba(d.loc[te, feats])[:, 1]
        d.loc[te, "p_raw"] = pr
        d.loc[te, "p_cal"] = iso.predict(pr) if iso is not None else pr
        print(f"  fold {terr:<12} done  [{time.time()-t0:.0f}s]")

    fd = first_differences(d)
    gg = fd.groupby(["run_id", "robot"], sort=False)
    fd["d_p_raw"] = gg["p_raw"].diff()
    fd["d_p_cal"] = gg["p_cal"].diff()

    print("\n" + "=" * 70)
    print("MODEL-RECOVERED effect (radio_blind, onset rows, censored)")
    print("=" * 70)
    # sign -1 so that + means "the model expects the link to IMPROVE"
    e_raw = climb_effect(fd, "d_p_raw", -1, None)
    e_cal = climb_effect(fd, "d_p_cal", -1, None)
    show("UNCALIBRATED, per +10 m (+ = model expects improvement)", e_raw)
    show("CALIBRATED,   per +10 m (+ = model expects improvement)", e_cal)

    # The four-terrain rank correlation is four data points; it swings from
    # +1.000 to -0.200 across defensible analysis choices and cannot carry a
    # claim. The identified test is the INTERACTION, bootstrapped over all
    # rows -- exactly the test the measured effect is reported with above.
    print("\n" + "=" * 70)
    print("RECOVERED INTERACTIONS (the identified test, n in the thousands)")
    print("=" * 70)
    inter = []
    for ycol, tag in (("d_p_raw", "raw"), ("d_p_cal", "calibrated")):
        print(f"\n  {tag}: does the model's risk respond to climb DIFFERENTLY"
              f" between terrains?")
        for a, b in [("open_field", "forest"), ("urban", "forest"),
                     ("hills", "forest"), ("open_field", "hills")]:
            r = interaction_test(fd, ycol, -1, a, b)
            if not r:
                continue
            s_ = "*" if r["significant"] else " "
            print(f"    {a:<11}- {b:<11}{r['diff_per_10m']:+8.4f} "
                  f"[{r['lo']:+7.4f}, {r['hi']:+7.4f}] {s_} "
                  f"p={r['p_two_sided']:.4g}")
            inter.append(dict(kind=tag, **r))
    # Measured counterpart, same test, for the side-by-side.
    print("\n  MEASURED counterpart (same test, same censoring):")
    for a, b in [("open_field", "forest"), ("urban", "forest"),
                 ("hills", "forest"), ("open_field", "hills")]:
        r = interaction_test(d_cen, "d_c2_ok", 1, a, b)
        if not r:
            continue
        s_ = "*" if r["significant"] else " "
        print(f"    {a:<11}- {b:<11}{r['diff_per_10m']:+8.4f} "
              f"[{r['lo']:+7.4f}, {r['hi']:+7.4f}] {s_} "
              f"p={r['p_two_sided']:.4g}")
        inter.append(dict(kind="measured", **r))
    pd.DataFrame(inter).to_csv(Path(args.out) / "phase4b_interactions.csv",
                               index=False)

    fd[["run_id", "robot", "terrain", "t_elapsed_s", "uav_z_m", "d_uav_z_m",
        "c2_ok", "y", "p_raw", "p_cal", "d_p_raw", "d_p_cal", "block"]].to_pickle(
        Path(args.out) / "phase4b_rows.pkl")

    out = [agreement(measured, as_map(e_raw), "uncalibrated vs measured"),
           agreement(measured, as_map(e_cal), "CALIBRATED vs measured")]
    print("\n  for reference, the published Phase 4 figure "
          "(+c2hist, all rows, uncensored):")
    print("    Pearson +0.901   Spearman +0.800   sign agreement 3/4")

    outd = Path(args.out); outd.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(out).to_csv(outd / "phase4b_signflip.csv", index=False)
    e_meas.assign(kind="measured").to_csv(
        outd / "phase4b_effects_measured.csv", index=False)
    pd.concat([e_raw.assign(kind="recovered_raw"),
               e_cal.assign(kind="recovered_cal")]).to_csv(
        outd / "phase4b_effects_recovered.csv", index=False)
    print(f"\nwrote {outd/'phase4b_signflip.csv'}  ({time.time()-t0:.0f}s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
