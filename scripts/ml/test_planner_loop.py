#!/usr/bin/env python3
"""Closed-loop check on the planner's altitude control.

This exists because round 2 of the fly-off shipped a reference-frame bug that
no open-loop test could see: `choose()` rate-limited its step against the
buffered CSV row while the pilot applied that step to its own live pose, so a
move toward an ABSOLUTE target inverted sign whenever the two disagreed. In
forest the UAV oscillated between the geofence bounds and 57.6 % of commanded
targets were clamped to 20 or 60 m -- altitudes the 25-55 m grid cannot
produce.

So: replay recorded cycles, feed the planner's own output back as the next
pose exactly as `_planner_pilot` does, and assert the loop converges and stays
inside the grid.

Usage:  python3 scripts/ml/test_planner_loop.py sar_forest <research.csv> [z0]
"""
import csv
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from online_planner import OnlinePlanner          # noqa: E402
from planner import Z_TARGETS, MAX_DZ_STEP        # noqa: E402

GEO_LO, GEO_HI = 20.0, 60.0      # the geofence, wider than the target grid


def main():
    world, path = sys.argv[1], sys.argv[2]
    z = float(sys.argv[3]) if len(sys.argv) > 3 else 60.0
    rows = list(csv.reader(open(path)))
    header = rows[0]
    p = OnlinePlanner(world, log=lambda m: print("   " + m))

    x = y = 0.0
    traj, targets, steps = [], [], []
    for r in rows[1:]:
        p.ingest(header, r)
        pick = p.choose(pose=(x, y, z))
        if pick is None:
            continue
        dx, dy, dz, _ = pick
        # exactly what _planner_pilot does, including the geofence clamp
        z = float(np.clip(z + dz, GEO_LO, GEO_HI))
        x, y = x + dx, y + dy
        traj.append(z); targets.append(p.last_target); steps.append(dz)

    t = np.array(traj); tg = np.array(targets); st = np.array(steps)
    tail = t[len(t) // 2:]                      # second half = settled
    print(f"\n  {world}: {len(t)} closed-loop decisions, start z={sys.argv[3] if len(sys.argv)>3 else 60.0}")
    print(f"    target z   : mean {tg.mean():5.1f}  median {np.median(tg):5.1f}")
    print(f"    realised z : mean {t.mean():5.1f}  median {np.median(t):5.1f}  "
          f"sd {t.std():5.2f}")
    print(f"    settled    : mean {tail.mean():5.1f}  sd {tail.std():5.2f}")
    print(f"    tracking err |z - target|: mean {np.abs(t-tg).mean():5.2f} m")

    fails = []
    # 1. the loop must not park on a geofence bound the grid cannot ask for
    at_bound = np.mean((t <= GEO_LO + .01) | (t >= GEO_HI - .01))
    if at_bound > 0.05:
        fails.append(f"{100*at_bound:.1f}% of realised altitudes sit on a "
                     f"geofence bound (grid is {Z_TARGETS[0]:.0f}-"
                     f"{Z_TARGETS[-1]:.0f} m; expected ~0%)")
    # 2. every commanded step must respect the rate limit
    if np.abs(st).max() > MAX_DZ_STEP + 1e-6:
        fails.append(f"step {np.abs(st).max():.1f} m exceeds rate limit "
                     f"{MAX_DZ_STEP:.0f} m")
    # 3. steps must point AT the target, never away from it
    err = tg - (t - st)                      # target minus pre-step altitude
    wrong = np.mean((np.abs(err) > 1.0) & (np.sign(st) != np.sign(err)))
    if wrong > 0.05:
        fails.append(f"{100*wrong:.1f}% of steps move AWAY from the chosen "
                     f"target (the round-2 sign inversion)")
    # 4. once settled it should track the target
    if np.abs(tail - tg[len(tg)//2:]).mean() > MAX_DZ_STEP:
        fails.append(f"settled tracking error "
                     f"{np.abs(tail-tg[len(tg)//2:]).mean():.1f} m exceeds one step")

    if fails:
        print("\n  FAIL")
        for f in fails:
            print(f"    - {f}")
        return 1
    print("\n  PASS: converges, respects the rate limit, steps point at the "
          "target, stays off the geofence")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
