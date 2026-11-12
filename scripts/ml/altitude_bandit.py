"""Learn the operating altitude on the spot, from measured connectivity.

WHY NOT LET THE SURROGATE CHOOSE ALTITUDE. scripts/ml/altitude_model_select.py
(2026-11-10) replayed four surrogate families through the planner's candidate
builder on each held-out terrain. None learned where to stop climbing: the
linear ones chose 55 m in ~100 % of decisions in every terrain, LightGBM chose
~47 m in BOTH forest and open field, and every one ranked the benefit of
climbing in the WRONG order against the measured effect (Spearman -0.2 to
-0.8; measured per 10 m: open_field +0.169, urban +0.030, hills +0.012,
forest -0.015). A model trained on three terrains applies their altitude
response to the fourth, and nothing in the radio features tells it the fourth
is different. That is the Phase 4 "failure to recover" seen from the planner.

So altitude is learned where the UAV is, from the outcome it is meant to
improve. Each completed measurement cycle is one observation: the altitude the
UAV flew during the cycle, and the fraction of robots whose C2 was up. The
learner keeps a kernel-smoothed, time-discounted estimate of that fraction per
altitude target and picks the target with the best upper confidence bound.
It never sees the terrain; whether climbing pays is discovered, not assumed.

Design choices, each for a stated reason:
  * kernel smoothing over altitude (bandwidth 5 m): neighbouring altitudes
    share evidence, so it need not visit all seven targets to rank them;
  * exponential time discount (half-life 15 min): the robots move, so what
    was best 40 minutes ago may not be now;
  * a switching margin: it leaves the current target only for a clearly
    better one, so noise does not make it dither;
  * robots mid-reattach are excluded from the reward: a modem being restarted
    is down whatever the altitude, and counting it adds noise, not signal.
"""
from __future__ import annotations

from collections import deque

import numpy as np


class AltitudeBandit:
    def __init__(self, arms, bandwidth_m: float = 5.0,
                 half_life_s: float = 900.0, explore: float = 0.15,
                 prior_mean: float = 0.5, prior_n: float = 0.5,
                 switch_margin: float = 0.02, memory: int = 5000):
        self.arms = np.asarray(arms, float)
        self.bw = bandwidth_m
        self.half_life = half_life_s
        self.c = explore
        self.m0, self.n0 = prior_mean, prior_n
        self.margin = switch_margin
        self.obs: deque = deque(maxlen=memory)     # (t, z, reward)
        self.current = None

    def observe(self, t: float, z: float, reward: float) -> None:
        if np.isfinite(t) and np.isfinite(z) and np.isfinite(reward):
            self.obs.append((float(t), float(z), float(reward)))

    def stats(self, t_now: float):
        """(mean, effective n) per arm."""
        if not self.obs:
            n = np.full(len(self.arms), self.n0)
            return np.full(len(self.arms), self.m0), n
        o = np.array(self.obs)
        wt = 0.5 ** (np.clip(t_now - o[:, 0], 0, None) / self.half_life)
        k = np.exp(-0.5 * ((o[None, :, 1] - self.arms[:, None]) / self.bw) ** 2)
        w = k * wt[None, :]
        n = self.n0 + w.sum(1)
        m = (self.n0 * self.m0 + (w * o[None, :, 2]).sum(1)) / n
        return m, n

    def select(self, t_now: float, z_now: float) -> float:
        m, n = self.stats(t_now)
        ucb = m + self.c * np.sqrt(np.log(1.0 + n.sum()) / n)
        best = int(ucb.argmax())
        here = int(np.abs(self.arms - z_now).argmin())
        if self.current is None:
            self.current = here
        # Hysteresis against the arm we are committed to, not the one we
        # happen to be passing through while climbing toward it.
        if ucb[best] > ucb[self.current] + self.margin:
            self.current = best
        return float(self.arms[self.current])

    def summary(self, t_now: float) -> str:
        m, n = self.stats(t_now)
        return " ".join(f"{a:.0f}:{mm:.2f}/{nn:.0f}"
                        for a, mm, nn in zip(self.arms, m, n))


class AltitudeProber:
    """Drift-robust altitude learner: hold an incumbent, probe its neighbours,
    and move only on a drift-cancelled, autocorrelation-aware contrast.

    WHY THE BANDIT ABOVE WAS NOT ENOUGH (x-y-z fly-off, 2026-11-10/11). It
    compares the MEAN connectivity seen at each altitude, so whatever else
    was happening while it sat at an altitude is blamed on the altitude. In
    the open-field arm-D run its early visits to 55 m coincided with a bad
    patch (55 m scored 0.67), it settled at 35 m and never went back; in the
    arm-C run the same world sent it to 55 m. Forest is worse: cell-wide
    outages last minutes (lag-5 autocorrelation 0.46 at 40 m), so one outage
    can condemn whichever altitude it landed on.

    What this does instead:
      * time-blocked fixed effects: connectivity = f(altitude) + g(block),
        g free per `block_s` window, so only comparisons made CLOSE IN TIME
        inform f -- a slow drift or a long outage cancels out;
      * scheduled probes: every `probe_every` decisions it spends one dwell
        at a neighbour (+-5 or +-10 m, the most promising by upper bound),
        then returns, so every block holds such a comparison;
      * standard errors inflated for the residual lag-1 autocorrelation,
        and a move only when the lower bound of (neighbour - incumbent) is
        above zero: it does not leave a good altitude on a lucky streak;
      * probes slow down (`probe_every_slow`) once every neighbour is
        confidently worse, which is what makes holding still cheap.
    It never sees the terrain.

    Defaults (z_conf 0.5, probe every 2nd decision) were picked on bench seeds
    0-1 and checked on fresh seeds 100-104 (scripts/ml/altitude_offline_eval.py,
    logs/ml/altitude_bench_validate.csv): same mean regret as AltitudeBandit
    (-0.15 +- 0.35 points) but half the runs that lose >2 points to a fixed
    40 m hover (4.4 % vs 8.1 %) and a worst case of -6.4 vs -22.1 points.

    CAUTION OPTIONS (2026-11-12; the planner's default `cautious` learner).
    The capped forest fly-off showed one 90-min run cannot even sign the
    forest altitude effect (within-run slope CI about +-10 points per 10 m
    around a measured -6.6), and that leaving 40 m costs on average when 91 %
    of robots are already up. `home_z` names the safe altitude; a move FURTHER
    from home must clear `z_conf_away` SE and `min_gain`, a move back needs
    only `z_conf`. With home_z=40, z_conf_away=1.5, min_gain=0.02,
    probe_every=4, probe_every_slow=20, on fresh bench seeds 100-109: worst
    per-effect mean vs fixed 40 m -0.6/-0.7/-0.8 (current -1.0/-1.2/-1.9),
    runs losing >2 points 1.2/3.1/4.4 % (3.7/11.3/22.8 %), for the old bench /
    heterogeneous 40 m traces / capped forest traces; the price is about half
    the climbing gain where climbing helps (climb_strong +9.8 vs +18.9).
    Defaults below leave these off, so `prober` reproduces earlier runs.
    """

    def __init__(self, arms, block_s: float = 240.0,
                 half_life_s: float = 1800.0, probe_every: int = 2,
                 probe_every_slow: int = 10, probe_reach_m: float = 10.0,
                 z_conf: float = 0.5, prior_sd: float = 0.15,
                 smooth: float = 2.0, memory: int = 5000,
                 home_z: float | None = None, z_conf_away: float | None = None,
                 min_gain: float = 0.0):
        self.arms = np.asarray(arms, float)
        self.block_s = block_s
        self.half_life = half_life_s
        self.probe_every = probe_every
        self.probe_every_slow = probe_every_slow
        self.reach = probe_reach_m
        self.zc = z_conf
        self.prior_sd = prior_sd
        self.smooth = smooth
        # Caution (2026-11-12). With home_z set, a move that takes the UAV
        # FURTHER from home must clear z_conf_away and beat the incumbent by
        # min_gain; a move back toward home needs only z_conf. Home is the
        # safe default altitude, so evidence has to be strong to leave it.
        self.home = None if home_z is None else float(home_z)
        self.zc_away = z_conf if z_conf_away is None else z_conf_away
        self.min_gain = min_gain
        self.obs: deque = deque(maxlen=memory)
        self.inc = None            # incumbent arm index
        self.probe = None          # arm index being probed this dwell
        self.since_probe = 0
        self.last_est = None

    def observe(self, t: float, z: float, reward: float) -> None:
        if np.isfinite(t) and np.isfinite(z) and np.isfinite(reward):
            self.obs.append((float(t), float(z), float(reward)))

    def estimate(self, t_now: float):
        """(f - f[inc], se) per arm; f identified only from within-block
        comparisons. se is inf-free: unvisited arms fall back to the prior."""
        k = len(self.arms)
        if len(self.obs) < 3:
            return np.zeros(k), np.full(k, self.prior_sd)
        o = np.array(self.obs)
        t, z, r = o[:, 0], o[:, 1], o[:, 2]
        arm = np.abs(z[:, None] - self.arms[None, :]).argmin(1)
        w = 0.5 ** (np.clip(t_now - t, 0, None) / self.half_life)
        blk = np.floor(t / self.block_s).astype(int)
        X = np.zeros((len(o), k))
        X[np.arange(len(o)), arm] = 1.0
        # within-block demeaning (fixed effect per block)
        Xd, rd = X.copy(), r.copy()
        for b in np.unique(blk):
            m = blk == b
            wb = w[m] / w[m].sum()
            Xd[m] -= wb @ X[m]
            rd[m] -= wb @ r[m]
        resid_sd = 0.25
        if len(o) > 20:
            beta0 = np.linalg.lstsq(Xd * np.sqrt(w)[:, None],
                                    rd * np.sqrt(w), rcond=None)[0]
            res = rd - Xd @ beta0
            resid_sd = max(float(np.sqrt(np.average(res ** 2, weights=w))),
                           0.05)
            same = blk[1:] == blk[:-1]
            if same.sum() > 10 and res.std() > 0:
                rho = np.corrcoef(res[1:][same], res[:-1][same])[0, 1]
            else:
                rho = 0.0
        else:
            rho = 0.0
        rho = float(np.clip(np.nan_to_num(rho), 0.0, 0.9))
        infl = (1 + rho) / (1 - rho)
        s2 = resid_sd ** 2 * infl
        D = np.diff(np.eye(k), 2, axis=0)
        A = (Xd.T * w) @ Xd / s2 + self.smooth * D.T @ D / s2 \
            + np.eye(k) / self.prior_sd ** 2
        A_inv = np.linalg.inv(A)
        f = A_inv @ ((Xd.T * w) @ rd / s2)
        i = self.inc if self.inc is not None else 0
        c = f - f[i]
        e = np.zeros(k)
        e[i] = 1
        se = np.sqrt(np.maximum(
            [(np.eye(k)[j] - e) @ A_inv @ (np.eye(k)[j] - e) for j in range(k)],
            1e-12))
        se[i] = 0.0
        return c, se

    def select(self, t_now: float, z_now: float) -> float:
        if self.inc is None:
            self.inc = int(np.abs(self.arms - z_now).argmin())
        if self.probe is not None:          # probe dwell done -> go home
            self.probe = None
            return float(self.arms[self.inc])
        c, se = self.estimate(t_now)
        self.last_est = (c, se)
        near = [j for j in range(len(self.arms))
                if j != self.inc
                and abs(self.arms[j] - self.arms[self.inc]) <= self.reach + 1e-9]
        lcb = {j: (c[j] - self.zc * se[j]) if self._toward_home(j)
               else (c[j] - self.zc_away * se[j] - self.min_gain) for j in near}
        best = max(near, key=lambda j: lcb[j]) if near else None
        if best is not None and lcb[best] > 0:
            self.inc = best                 # confident improvement: move
            self.since_probe = 0
            return float(self.arms[self.inc])
        self.since_probe += 1
        ucb = {j: c[j] + self.zc * se[j] for j in near}
        every = (self.probe_every_slow
                 if near and max(ucb.values()) < 0 else self.probe_every)
        if near and self.since_probe >= every:
            self.probe = max(near, key=lambda j: ucb[j])
            self.since_probe = 0
            return float(self.arms[self.probe])
        return float(self.arms[self.inc])

    def _toward_home(self, j: int) -> bool:
        if self.home is None:
            return False
        return (abs(self.arms[j] - self.home)
                < abs(self.arms[self.inc] - self.home))

    def summary(self, t_now: float) -> str:
        c, se = self.estimate(t_now)
        return (f"inc {self.arms[self.inc]:.0f} | "
                + " ".join(f"{a:.0f}:{cc:+.2f}±{s:.2f}"
                           for a, cc, s in zip(self.arms, c, se)))
