#!/usr/bin/env python3
"""Offline bench for the altitude learners, on REAL connectivity noise.

Why a bench and not a replay of the fly-off logs: a log only says what
happened at the altitude flown, never what would have happened at another.
So the true altitude effect has to be assumed -- and since it is exactly the
thing we do not know (Phase 4 says open field +0.169 per 10 m; the constant-
altitude runs disagree, 0.80 at 55 m vs 0.85 at 40 m), the learners are
scored over a FAMILY of effects, from strongly-climb to strongly-descend,
including flat and a cliff at the ceiling.

What is real is the noise. Each episode replays one of the constant-altitude
research runs (|std z| < 2 m) cycle by cycle: its timestamps, which robots
had C2 up, which were mid-reattach. That carries the things that broke the
live bandit -- minutes-long cell-wide outages in forest, robots wandering in
and out of shadow -- with their real time structure. The altitude effect is
added on top: per robot, P(up) moves by delta(z) - delta(z_trace), applied
through a slowly varying per-robot uniform (AR(1), 60 s), so a robot pushed
into shadow by altitude stays there for a while rather than flickering.

Scoring uses the EXPECTED connectivity under the true effect (no draw noise),
over the whole 90-min episode, against:
  fixed40 -- the current fixed-hover baseline;
  oracle  -- the best fixed altitude in hindsight for that effect.
Also the second-half mean altitude, to see where each learner settles and how
consistently.

    python3 scripts/ml/altitude_offline_eval.py            # full bench
    BENCH_SEEDS=4 python3 scripts/ml/altitude_offline_eval.py   # quick
    BENCH_TRACES=campaign_20261112 ...     # only traces from matching runs
    BENCH_EFFECTS=flat,forest_capped ...   # only these true effects
"""
from __future__ import annotations

import glob
import os
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import altitude_bandit as ab                                 # noqa: E402
from planner import Z_TARGETS, rate_limit                   # noqa: E402

REPO = Path(__file__).resolve().parents[2]
OUT = REPO / "logs" / "ml"
DWELL_S = 30.0
# Per-robot, time-varying altitude response (2026-11-12). Live, climbing helps
# a robot in the open and hurts one under canopy, and that changes as robots
# move; one 90-min capped forest run could not even sign the effect (within-
# run slope CI about +-10 points per 10 m around a true -6.6). With HET > 0
# each robot's slope is effect'(z) + HET * AR(1) noise (time constant HET_TAU),
# so the per-moment contrast is noisy while the mean effect is unchanged.
HET = float(os.environ.get("BENCH_HET", "0"))          # per metre
HET_TAU = float(os.environ.get("BENCH_HET_TAU", "300"))
DUR_S = 5400.0
Z0 = 40.0
ROBOTS = "abcd"

# True altitude effect on each robot's P(C2 up), relative to 40 m.
EFFECTS = {
    "climb_strong": lambda z: 0.0169 * (z - 40),        # Phase 4 open field
    "climb_mild":   lambda z: 0.005 * (z - 40),
    "flat":         lambda z: 0.0 * z,
    "descend_mild": lambda z: -0.005 * (z - 40),
    "peak_40":      lambda z: -0.0015 * (z - 40) ** 2,
    "peak_30":      lambda z: -0.0012 * (z - 30) ** 2 + 0.0012 * 100,
    "cliff_55":     lambda z: -np.clip(z - 45, 0, None) ** 2 * 0.003,
    "descend_strong": lambda z: -0.012 * (z - 40),
    # measured live under the uplink cap (2026-11-12): capped forest arm C vs
    # arm B (same x-y rule, same robot paths) -> -6.6 points per robot per 10 m
    "forest_capped": lambda z: -0.0066 * (z - 40),
}


def load_traces():
    """Constant-altitude runs. BENCH_TRACES=substr1,substr2 keeps only traces
    whose name contains one of them (e.g. the capped forest runs)."""
    keep = [k for k in os.environ.get("BENCH_TRACES", "").split(",") if k]
    tr = []
    for f in sorted(glob.glob(str(REPO / "logs/campaign_*/sar_*.csv"))):
        if keep and not any(k in f for k in keep):
            continue
        try:
            d = pd.read_csv(f, usecols=lambda c: c in ("uav_z_m", "t_elapsed_s")
                            or c.endswith("_c2_loss_pct")
                            or (len(c) == 7 and c.endswith("_event")))
        except (ValueError, pd.errors.ParserError):
            continue
        if len(d) < 200 or "a_c2_loss_pct" not in d or d.uav_z_m.std() >= 2:
            continue
        t = d.t_elapsed_s.to_numpy(float)
        ok = np.isfinite(t)
        d, t = d[ok], t[ok] - t[ok][0]
        loss = np.column_stack([d[f"{c}_c2_loss_pct"] for c in ROBOTS])
        up = (loss < 50).astype(float)
        excl = ~np.isfinite(loss)
        for i, c in enumerate(ROBOTS):
            if f"{c}_event" in d:
                excl[:, i] |= (d[f"{c}_event"].astype(str) == "reattaching").values
        tr.append(dict(name=Path(f).parent.name + "/" + Path(f).stem,
                       t=t, up=up, excl=excl, z=float(d.uav_z_m.mean()),
                       pbar=np.clip(up[~excl].mean(), 0.05, 0.95)))
    return tr


def episode(trace, effect, learner, seed):
    """Fly one 90-min episode. Returns (expected reward per cycle, z per
    cycle, weights)."""
    rng = np.random.default_rng(seed)
    t0, up0, ex0 = trace["t"], trace["up"], trace["excl"]
    T = t0[-1] + np.median(np.diff(t0))
    # circular shift + robot permutation: many episodes per trace
    shift = rng.uniform(0, T)
    perm = rng.permutation(4)
    order = np.argsort((t0 + shift) % T)
    t = ((t0 + shift) % T)[order]
    up, ex = up0[order][:, perm], ex0[order][:, perm]
    reps = int(np.ceil(DUR_S / T))
    t = np.concatenate([t + k * T for k in range(reps)])
    up, ex = np.tile(up, (reps, 1)), np.tile(ex, (reps, 1))
    keep = t < DUR_S
    t, up, ex = t[keep], up[keep], ex[keep]
    p, base = trace["pbar"], effect(trace["z"])
    # slowly varying per-robot uniforms -> persistent altitude flips
    a = np.exp(-np.diff(t, prepend=t[0]) / 60.0)
    g = np.zeros((len(t), 4))
    e = rng.standard_normal((len(t), 4))
    for k in range(1, len(t)):
        g[k] = a[k] * g[k - 1] + np.sqrt(1 - a[k] ** 2) * e[k]
    g[0] = e[0]
    from scipy.stats import norm
    u = norm.cdf(g)
    hz = np.zeros((len(t), 4))                  # heterogeneous slope noise
    if HET > 0:
        ah = np.exp(-np.diff(t, prepend=t[0]) / HET_TAU)
        eh = rng.standard_normal((len(t), 4))
        hz[0] = eh[0]
        for k in range(1, len(t)):
            hz[k] = ah[k] * hz[k - 1] + np.sqrt(1 - ah[k] ** 2) * eh[k]
        hz *= HET

    z, next_dec = Z0, 0.0
    exp_r, zs = np.empty(len(t)), np.empty(len(t))
    for k in range(len(t)):
        while t[k] >= next_dec:
            if learner is not None:
                tgt = learner.select(next_dec, z)
                z = float(np.clip(z + rate_limit(z, tgt), 20, 60))
            next_dec += DWELL_S
        dlt = effect(z) - base + hz[k] * (z - trace["z"])     # per robot
        qu = np.minimum(1.0, np.clip(dlt, 0, None) / (1 - p))
        qd = np.minimum(1.0, np.clip(-dlt, 0, None) / p)
        e_up = np.where(dlt >= 0, up[k] + (1 - up[k]) * qu, up[k] * (1 - qd))
        r_up = np.where(dlt >= 0,
                        np.where(up[k] > 0, 1.0, (u[k] < qu).astype(float)),
                        np.where(up[k] > 0, (u[k] >= qd).astype(float), 0.0))
        live = ~ex[k]
        exp_r[k] = e_up.mean()
        zs[k] = z
        if learner is not None and live.any():
            learner.observe(t[k], z, float(r_up[live].mean()))
    w = np.diff(t, append=t[-1] + np.median(np.diff(t)))
    return exp_r, zs, np.clip(w, 0, 120)


def make(kind):
    """'fixed40', a class name, or 'Class:key=val,key=val' for a variant."""
    if kind == "fixed40":
        return None
    name, _, args = kind.partition(":")
    kw = {k: float(v) for k, v in (a.split("=") for a in args.split(";") if a)}
    for k in ("probe_every", "probe_every_slow", "memory"):
        if k in kw:
            kw[k] = int(kw[k])
    return getattr(ab, name)(Z_TARGETS, **kw)


def main():
    seeds = int(os.environ.get("BENCH_SEEDS", "12"))
    seed0 = int(os.environ.get("BENCH_SEED0", "0"))
    kinds = os.environ.get("BENCH_LEARNERS",
                           "fixed40,AltitudeBandit,AltitudeProber").split(",")
    traces = load_traces()
    print(f"{len(traces)} constant-altitude traces: "
          + ", ".join(f"{x['name']}(p={x['pbar']:.2f})" for x in traces))
    t_start = time.time()
    rows = []
    only = [e for e in os.environ.get("BENCH_EFFECTS", "").split(",") if e]
    for ename, eff in EFFECTS.items():
        if only and ename not in only:
            continue
        zz = np.array(Z_TARGETS)
        z_opt = float(zz[np.argmax([eff(z) for z in zz])])
        for ti, tr in enumerate(traces):
            for s in range(seeds):
                seed = 1000 * ti + seed0 + s
                # oracle: best fixed altitude, same noise
                orc = []
                for zf in Z_TARGETS:
                    class Fix:  # noqa: E306
                        def select(self, *_): return zf
                        def observe(self, *_): pass
                    r, _, w = episode(tr, eff, Fix(), seed)
                    orc.append(np.sum(r * w) / w.sum())
                for kind in kinds:
                    r, zs, w = episode(tr, eff, make(kind), seed)
                    h = np.arange(len(zs)) >= len(zs) // 2
                    rows.append(dict(effect=ename, z_opt=z_opt,
                                     trace=tr["name"], seed=s, learner=kind,
                                     reward=np.sum(r * w) / w.sum(),
                                     oracle=max(orc), fixed40=orc[3],
                                     z_half2=np.sum(zs[h] * w[h]) / w[h].sum()))
        print(f"  {ename:<15} done ({time.time()-t_start:.0f}s)", flush=True)
    df = pd.DataFrame(rows)
    df["regret"] = 100 * (df.oracle - df.reward)
    df["vs_fixed40"] = 100 * (df.reward - df.fixed40)
    df["near_opt"] = (df.z_half2 - df.z_opt).abs() <= 5
    tag = os.environ.get("BENCH_TAG", "")
    df.to_csv(OUT / f"altitude_bench{tag}.csv", index=False)
    s = df.groupby(["effect", "learner"]).agg(
        regret=("regret", "mean"), vs40=("vs_fixed40", "mean"),
        vs40_p10=("vs_fixed40", lambda x: np.percentile(x, 10)),
        z2=("z_half2", "mean"), z2_sd=("z_half2", "std"),
        near_opt=("near_opt", "mean"), z_opt=("z_opt", "first"))
    pd.set_option("display.width", 200)
    print(s.round(2).to_string())
    o = df.groupby("learner").agg(regret=("regret", "mean"),
                                  vs40=("vs_fixed40", "mean"),
                                  worst_effect_vs40=("vs_fixed40", "min"))
    print("\noverall\n" + o.round(2).to_string())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
