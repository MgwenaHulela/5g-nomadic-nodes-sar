"""urban_buildings.py — single source of truth for the sar_urban world.

The SAME building list drives (a) the Gazebo world geometry, via
make_urban_world.py, and (b) the air-to-ground channel model, via
channel_bridge.py's line-of-sight check. Keeping them in one file
guarantees the radio and the visuals agree on where the walls are.

Each building is an axis-aligned box: (cx, cy, width_x, depth_y, height)
in metres, standing on the ground (z from 0 to height), centred at
(cx, cy). Heights are deliberately mixed so that a UAV at 40 m is
blocked from many ground positions but sees over most of them by 60 m —
this is what makes altitude a genuine NLoS coverage lever here.
"""

# (cx, cy, width_x, depth_y, height)
# Heights are tuned to the 20-60 m nomadic altitude band: at 20 m the UAV
# sits among the rooftops (heavy shadowing), by 60 m it clears them all, so
# climbing is a strong NLoS coverage lever across this range.
BUILDINGS = [
    # inner ring around the UAV's nominal (0,0) hover point
    ( 45,   0, 22, 22, 20),
    (  0,  45, 22, 22, 24),
    (-45,   0, 22, 22, 25),
    (  0, -45, 22, 22, 18),
    # corner blocks — lower, partial shadows
    ( 50,  50, 20, 20, 12),
    (-50,  50, 20, 20, 14),
    (-50, -50, 20, 20, 13),
    ( 50, -50, 20, 20, 22),
    # outer blocks — extend the urban canyon toward the coverage edge
    ( 95,   0, 24, 24, 16),
    (  0,  95, 24, 24, 22),
    (-95,   0, 24, 24, 17),
    (  0, -95, 24, 24, 20),
]


def _bounds(b):
    cx, cy, w, d, h = b
    return (cx - w / 2.0, cx + w / 2.0, cy - d / 2.0, cy + d / 2.0, 0.0, h)


def segment_blocked(p0, p1, b, margin=0.0):
    """True if the 3-D segment p0->p1 intersects axis-aligned box b.

    Slab method for ray/AABB, clamped to the segment parameter [0, 1].
    p0, p1 are (x, y, z). `margin` inflates the box (metres) to model the
    fact that grazing a wall edge is still an obstructed, not clean, path.
    """
    x0, x1, y0, y1, z0, z1 = _bounds(b)
    lo = (x0 - margin, y0 - margin, z0)
    hi = (x1 + margin, y1 + margin, z1)
    tmin, tmax = 0.0, 1.0
    for i in range(3):
        d = p1[i] - p0[i]
        if abs(d) < 1e-9:                       # segment parallel to this slab
            if p0[i] < lo[i] or p0[i] > hi[i]:
                return False
        else:
            t1 = (lo[i] - p0[i]) / d
            t2 = (hi[i] - p0[i]) / d
            if t1 > t2:
                t1, t2 = t2, t1
            tmin = max(tmin, t1)
            tmax = min(tmax, t2)
            if tmin > tmax:
                return False
    return True


def any_blocked(p0, p1, margin=1.0):
    """True if any building blocks the line of sight p0 -> p1."""
    return any(segment_blocked(p0, p1, b, margin) for b in BUILDINGS)


def sdf_models():
    """SDF <model> blocks for every building, for the world file."""
    out = []
    for i, (cx, cy, w, d, h) in enumerate(BUILDINGS):
        # a mild height-graded grey so the skyline reads in the GUI
        g = 0.45 + 0.15 * (h / 40.0)
        out.append(
            f'    <model name="building_{i}">\n'
            f'      <static>true</static>\n'
            f'      <pose>{cx} {cy} {h/2.0:.2f} 0 0 0</pose>\n'
            f'      <link name="link">\n'
            f'        <collision name="collision">\n'
            f'          <geometry><box><size>{w} {d} {h}</size></box></geometry>\n'
            f'        </collision>\n'
            f'        <visual name="visual">\n'
            f'          <geometry><box><size>{w} {d} {h}</size></box></geometry>\n'
            f'          <material>\n'
            f'            <ambient>{g:.2f} {g:.2f} {g+0.05:.2f} 1</ambient>\n'
            f'            <diffuse>{g+0.05:.2f} {g+0.05:.2f} {g+0.1:.2f} 1</diffuse>\n'
            f'          </material>\n'
            f'        </visual>\n'
            f'      </link>\n'
            f'    </model>'
        )
    return "\n".join(out)


if __name__ == "__main__":
    print(f"{len(BUILDINGS)} buildings, heights "
          f"{min(b[4] for b in BUILDINGS)}-{max(b[4] for b in BUILDINGS)} m")
