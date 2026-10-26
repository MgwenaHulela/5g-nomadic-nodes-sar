#!/usr/bin/env python3
"""Phase 4 — the sign-flip test. The scientific claim of the project.

Two halves, and the second is the one that matters:

  (a) MEASURE the effect. Does moving the UAV up help or hurt the link? The
      answer must depend on the terrain -- positive where climbing buys
      line-of-sight, negative where it does not -- or there is no sign to
      rediscover and the rest of the project is a forecasting exercise.

  (b) RECOVER it blind. Take the Phase 2 model trained leave-one-terrain-out,
      which has never seen the held-out terrain and is forbidden the terrain
      label, and ask what IT thinks a climb does there. If its implied effect
      tracks the measured one across terrains -- including the sign -- then
      the terrain's character was recoverable from signal history alone.

Design notes that matter for believing the numbers:

  * **First differences, not levels.** Every effect is estimated on changes
    within a (run_id, robot) series, which removes any fixed difference
    between robots, runs or terrains. A level regression would mostly be
    measuring that forest is worse than open field.
  * **Horizontal distance is controlled.** Climbing also changes slant range.
    The coefficient reported is on d(altitude) with d(horizontal distance)
    held constant, so it is not a restatement of "further is worse".
  * **Block bootstrap.** Link state is strongly autocorrelated and there are
    only ~5 runs per terrain, so neither row resampling (too optimistic) nor
    run resampling (too few units) is right. Contiguous blocks of cycles are
    resampled within each run instead.
  * **The `excite` policy is the clean estimate.** There the UAV's moves are
    random excitation, independent of how the link is doing, so the effect is
    causal. Under `two_phase` the UAV moves *because* of the link, so that
    estimate is confounded and is reported separately, never pooled.

Usage:
    python3 scripts/ml/sign_flip.py
"""
from __future__ import annotations

import glob
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import dataset as ds            # noqa: E402

OUT_DIR = ds.REPO / "logs" / "ml"
BLOCK = 20          # cycles per bootstrap block (~4 min of a run)
NBOOT = 600

# Outcomes. All oriented so that POSITIVE = the link got BETTER, so a
# positive climb coefficient always means "climbing helped".
OUTCOMES = {
    "c2_ok":       ("d_c2_ok",       +1, "C2 availability"),
    "c2_loss_pct": ("d_c2_loss_pct", -1, "C2 ping loss (inverted)"),
    "dl_bler":     ("d_dl_bler",     -1, "downlink BLER (inverted)"),
}


def first_differences(df: pd.DataFrame) -> pd.DataFrame:
    df = df.sort_values(["run_id", "robot", "t_elapsed_s"]).reset_index(
        drop=True)
    g = df.groupby(["run_id", "robot"], sort=False)
    for c in ["uav_z_m", "d_horiz_m", "c2_ok", "c2_loss_pct", "dl_bler"]:
        if c in df.columns:
            df["d_" + c] = df[c] - g[c].shift(1)
    # Block id for the bootstrap: contiguous cycles within one series.
    df["_k"] = g.cumcount() // BLOCK
    df["block"] = (df["run_id"].astype(str) + "|" + df["robot"].astype(str)
                   + "|" + df["_k"].astype(str))
    return df[df["d_uav_z_m"].notna()].drop(columns="_k")


def block_bootstrap_ols(sub: pd.DataFrame, ycol: str, xcols: list[str],
                        nboot: int = NBOOT, seed: int = 0):
    """OLS with a block bootstrap over contiguous cycle blocks."""
    sub = sub.dropna(subset=[ycol] + xcols)
    if len(sub) < 300:
        return None
    blocks = sub["block"].values
    uniq = pd.unique(blocks)
    if len(uniq) < 20:
        return None
    idx = {b: np.where(blocks == b)[0] for b in uniq}

    def fit(rows):
        X = np.column_stack([np.ones(len(rows))]
                            + [sub[c].values[rows] for c in xcols])
        return np.linalg.lstsq(X, sub[ycol].values[rows], rcond=None)[0]

    base = fit(np.arange(len(sub)))
    rng = np.random.default_rng(seed)
    bs = []
    for _ in range(nboot):
        pick = rng.choice(uniq, len(uniq), replace=True)
        rows = np.concatenate([idx[b] for b in pick])
        try:
            bs.append(fit(rows))
        except np.linalg.LinAlgError:
            pass
    bs = np.asarray(bs)
    return dict(coef=base[1], lo=np.percentile(bs[:, 1], 2.5),
                hi=np.percentile(bs[:, 1], 97.5), n=len(sub),
                n_blocks=len(uniq))


def climb_effect(d: pd.DataFrame, outcome: str, sign: int, policy: str | None):
    """Per-terrain effect of a 10 m climb, horizontal distance held constant."""
    rows = []
    for t in sorted(d["terrain"].unique()):
        sub = d[d["terrain"] == t]
        if policy:
            sub = sub[sub["policy"] == policy]
        r = block_bootstrap_ols(sub, outcome, ["d_uav_z_m", "d_d_horiz_m"])
        if r is None:
            continue
        lo, hi = sorted((sign * 10 * r["lo"], sign * 10 * r["hi"]))
        rows.append(dict(terrain=t, policy=policy or "ALL", outcome=outcome,
                         effect_per_10m=sign * 10 * r["coef"], lo=lo, hi=hi,
                         n=r["n"], n_blocks=r["n_blocks"],
                         significant=not (lo < 0 < hi)))
    return pd.DataFrame(rows)


def interaction_test(d: pd.DataFrame, ycol: str, sign: int, t1: str, t2: str,
                     nboot: int = 2000, seed: int = 1):
    """Bootstrap the DIFFERENCE in climb effect between two terrains.

    The claim this project makes is not "climbing hurts in forest" -- it is
    that the effect is not the same in both places. Testing that difference
    directly is both the right question and much more powerful than testing
    each arm against zero, because the two arms' noise is independent and
    cancels in the contrast. Blocks are resampled within each terrain.
    """
    out = {}
    for t in (t1, t2):
        s = d[d["terrain"] == t].dropna(
            subset=[ycol, "d_uav_z_m", "d_d_horiz_m"])
        if len(s) < 300:
            return None
        b = s["block"].values
        u = pd.unique(b)
        out[t] = (s, {x: np.where(b == x)[0] for x in u}, u)

    def fit(s, rows):
        X = np.column_stack([np.ones(len(rows)), s["d_uav_z_m"].values[rows],
                             s["d_d_horiz_m"].values[rows]])
        return np.linalg.lstsq(X, s[ycol].values[rows], rcond=None)[0][1]

    base = {t: fit(out[t][0], np.arange(len(out[t][0]))) for t in (t1, t2)}
    rng = np.random.default_rng(seed)
    diffs = []
    for _ in range(nboot):
        v = {}
        for t in (t1, t2):
            s, idx, u = out[t]
            rows = np.concatenate(
                [idx[x] for x in rng.choice(u, len(u), replace=True)])
            v[t] = fit(s, rows)
        diffs.append(v[t1] - v[t2])
    diffs = np.asarray(diffs) * 10 * sign
    obs = (base[t1] - base[t2]) * 10 * sign
    lo, hi = np.percentile(diffs, 2.5), np.percentile(diffs, 97.5)
    return dict(pair=f"{t1}-{t2}", diff_per_10m=obs, lo=lo, hi=hi,
                p_two_sided=2 * min((diffs <= 0).mean(), (diffs >= 0).mean()),
                significant=not (lo < 0 < hi))


PAIRS = [("open_field", "forest"), ("open_field", "hills"),
         ("open_field", "urban"), ("urban", "forest"), ("hills", "forest")]


def load_actions(run_ids) -> pd.DataFrame:
    fs = sorted(glob.glob(str(ds.LOG_DIR / "campaign_2026102[3-6]_*"
                              / "sar_*_actions.csv")))
    a = pd.concat([pd.read_csv(f) for f in fs], ignore_index=True)
    a["terrain"] = a["terrain"].str.replace("sar_", "", regex=False)
    for c in ["from_z_m", "to_z_m"]:
        a[c] = pd.to_numeric(a[c], errors="coerce")
    a["dz"] = a["to_z_m"] - a["from_z_m"]
    return a[a["run_id"].isin(run_ids)].copy()


def climb_descend_contrast(d: pd.DataFrame, acts: pd.DataFrame):
    """The explicit action contrast: what happens around a climb vs a descend.

    Each action is matched to the last cycle at or before it and the first
    cycle strictly after it, per robot, so a 'window' is one cycle step. The
    cycle cadence (11-22 s) is close to the gap between actions (median 27 s),
    so wider windows would span the next action and measure something else.
    """
    out = []
    cyc = d.sort_values(["run_id", "robot", "t_elapsed_s"])
    for (run, robot), s in cyc.groupby(["run_id", "robot"], sort=False):
        t = s["t_elapsed_s"].values
        q = s["c2_ok"].values
        aa = acts[acts["run_id"] == run]
        aa = aa[aa["action"].isin(["climb", "descend"])]
        if aa.empty or len(t) < 3:
            continue
        for ta, kind, dz in zip(aa["t_elapsed_s"].values,
                                aa["action"].values, aa["dz"].values):
            j = np.searchsorted(t, ta)
            if j == 0 or j >= len(t):
                continue
            before, after = q[j - 1], q[j]
            if not (np.isfinite(before) and np.isfinite(after)):
                continue
            out.append(dict(run_id=run, robot=robot,
                            terrain=s["terrain"].iloc[0],
                            policy=s["policy"].iloc[0], action=kind, dz=dz,
                            delta_c2=after - before))
    return pd.DataFrame(out)


def main() -> int:
    df = ds.build()
    d = first_differences(df)
    print(f"first-difference table: {len(d):,} rows, "
          f"{d['block'].nunique():,} bootstrap blocks\n")

    # ---------------- (a) the measured effect --------------------------
    all_eff = []
    for key, (col, sign, label) in OUTCOMES.items():
        if col not in d.columns:
            continue
        for pol in ["excite", "two_phase", None]:
            e = climb_effect(d, col, sign, pol)
            e["outcome_label"] = label
            all_eff.append(e)
    eff = pd.concat(all_eff, ignore_index=True)
    eff.to_csv(OUT_DIR / "phase4_climb_effect.csv", index=False)

    print("=" * 74)
    print("(a) MEASURED EFFECT OF A 10 m CLIMB  (+ = link improved)")
    print("    horizontal distance held constant; block-bootstrap 95% CI")
    print("=" * 74)
    for key, (col, sign, label) in OUTCOMES.items():
        sub = eff[(eff.outcome == col) & (eff.policy == "excite")]
        if sub.empty:
            continue
        print(f"\n  {label}   [policy=excite: UAV moves are random "
              f"excitation, so this is causal]")
        for _, r in sub.iterrows():
            star = " *" if r.significant else "  "
            print(f"    {r.terrain:<11} {r.effect_per_10m:+.4f}  "
                  f"[{r.lo:+.4f}, {r.hi:+.4f}]{star}  n={r.n:,}")

    inter = []
    for t1, t2 in PAIRS:
        r = interaction_test(d[d.policy == "excite"], "d_c2_ok", +1, t1, t2)
        if r:
            r["source"] = "measured"
            inter.append(r)
    print("\n" + "=" * 74)
    print("(a2) INTERACTION TEST: does the climb effect DIFFER by terrain?")
    print("     This, not the per-terrain arms, is the claim being made.")
    print("=" * 74)
    for r in inter:
        star = " *" if r["significant"] else "  "
        print(f"    {r['pair']:<24} {r['diff_per_10m']:+.4f}  "
              f"[{r['lo']:+.4f}, {r['hi']:+.4f}]{star}  p={r['p_two_sided']:.4f}")

    # ---------------- (b) terrain-blind recovery ------------------------
    pf = OUT_DIR / "phase2_predictions.csv.gz"
    if pf.exists():
        p = pd.read_csv(pf)
        p = p[(p.model == "radio_blind") & (p.horizon_s == 60)]
        key = ["run_id", "robot", "t_elapsed_s"]
        m = d.merge(p[key + ["p"]], on=key, how="inner")
        g = m.sort_values(key).groupby(["run_id", "robot"], sort=False)
        m["d_p"] = m["p"] - g["p"].shift(1)
        # p is P(DOWN), so invert to keep "+ = link improved".
        rec = []
        for pol in ["excite", None]:
            r = climb_effect(m, "d_p", -1, pol)
            rec.append(r)
        rec = pd.concat(rec, ignore_index=True)
        rec["outcome_label"] = "model-implied (terrain-blind, LOTO)"
        rec.to_csv(OUT_DIR / "phase4_recovered_effect.csv", index=False)

        print("\n" + "=" * 74)
        print("(b) EFFECT IMPLIED BY THE TERRAIN-BLIND MODEL")
        print("    radio_blind, h=60 s, each terrain scored by a model that")
        print("    never saw it and was never given the terrain label")
        print("=" * 74)
        sub = rec[rec.policy == "excite"]
        for _, r in sub.iterrows():
            star = " *" if r.significant else "  "
            print(f"    {r.terrain:<11} {r.effect_per_10m:+.4f}  "
                  f"[{r.lo:+.4f}, {r.hi:+.4f}]{star}  n={r.n:,}")

        for t1, t2 in PAIRS:
            r = interaction_test(m[m.policy == "excite"], "d_p", -1, t1, t2)
            if r:
                r["source"] = "recovered"
                inter.append(r)
        print("\n  (b2) same interaction test on the model-implied effect:")
        for r in [x for x in inter if x["source"] == "recovered"]:
            star = " *" if r["significant"] else "  "
            print(f"    {r['pair']:<24} {r['diff_per_10m']:+.4f}  "
                  f"[{r['lo']:+.4f}, {r['hi']:+.4f}]{star}  "
                  f"p={r['p_two_sided']:.4f}")

        meas = eff[(eff.outcome == "d_c2_ok") & (eff.policy == "excite")
                   ][["terrain", "effect_per_10m"]].rename(
            columns={"effect_per_10m": "measured"})
        both = meas.merge(sub[["terrain", "effect_per_10m"]].rename(
            columns={"effect_per_10m": "recovered"}), on="terrain")
        print("\n  measured vs recovered, per terrain:")
        print(both.to_string(index=False,
                             float_format=lambda v: f"{v:+.4f}"))
        if len(both) > 2:
            print(f"\n  Pearson r  = {both.measured.corr(both.recovered):+.3f}")
            print(f"  Spearman r = "
                  f"{both.measured.corr(both.recovered, method='spearman'):+.3f}")
            print(f"  sign agreement: "
                  f"{int((np.sign(both.measured)==np.sign(both.recovered)).sum())}"
                  f"/{len(both)} terrains")
    else:
        print(f"\n(b) skipped: {pf} not found")

    # ---------------- the explicit action contrast ----------------------
    acts = load_actions(df["run_id"].unique())
    cc = climb_descend_contrast(d, acts)
    if not cc.empty:
        cc.to_csv(OUT_DIR / "phase4_action_contrast.csv", index=False)
        print("\n" + "=" * 74)
        print("CROSS-CHECK: explicit climb vs descend actions (excite only)")
        print("=" * 74)
        x = cc[cc.policy == "excite"]
        tb = x.pivot_table(index="terrain", columns="action",
                           values="delta_c2", aggfunc=["mean", "size"])
        tb.columns = [f"{a}_{b}" for a, b in tb.columns]
        tb["contrast"] = tb.get("mean_climb", np.nan) - tb.get("mean_descend",
                                                               np.nan)
        print(tb.to_string(float_format=lambda v: f"{v:+.4f}"))
    pd.DataFrame(inter).to_csv(OUT_DIR / "phase4_interaction.csv",
                               index=False)
    print(f"\nwrote phase4_*.csv to {OUT_DIR}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
