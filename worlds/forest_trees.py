"""forest_trees.py — single source of truth for the sar_forest world.

The SAME tree list drives (a) the Gazebo world geometry, via
make_forest_world.py, and (b) the air-to-ground channel model, via
channel_bridge.py — which sums how many metres of canopy the UAV->UE ray
passes through and turns that depth into excess loss with Weissberger's
modified exponential-decay foliage model. Keeping geometry in one file
guarantees the radio and the visuals agree on where the trees are.

Each tree: (cx, cy, trunk_height, canopy_radius) in metres. The trunk is a
thin cylinder (ground collision the robot weaves between); the canopy is a
sphere centred at z ~= trunk_height (the layer that shadows the aerial
link). A ground robot drives between the trunks; the UAV link dims with the
depth of canopy along the slanted path — so climbing the UAV, or the robot
reaching a clearing, thins the foliage and recovers the link.
"""
import math

# A ground robot must not spawn inside a trunk: keep clearings around the
# UAV takeoff point (0,0) and the two burger spawns (+/-15, 0).
_CLEARINGS = [(0.0, 0.0, 6.0), (15.0, 0.0, 4.0), (-15.0, 0.0, 4.0)]
# Victim (thermal source) sits in a small glade so a robot can reach it.
VICTIM = (28.0, 10.0)


def _gen_trees():
    """Deterministic jittered grid of trees over the operating area, minus
    the clearings. Seeded so the forest is identical every run (radio and
    visuals must match, and datasets must be reproducible)."""
    import random
    rng = random.Random(1789)          # fixed seed -> reproducible forest
    trees = []
    step = 10.0
    span = 90.0
    xs = [x for x in _frange(-span, span, step)]
    for gx in xs:
        for gy in xs:
            cx = gx + rng.uniform(-2.5, 2.5)
            cy = gy + rng.uniform(-2.5, 2.5)
            if _in_clearing(cx, cy) or _near_victim(cx, cy):
                continue
            # Mixed trunk heights so the canopy band has depth; radii ~= the
            # 10 m spacing so neighbouring canopies just touch (a near-
            # continuous layer -> a descending A2G ray reliably clips it).
            th = round(rng.uniform(4.5, 8.5), 2)     # trunk height
            cr = round(rng.uniform(3.2, 5.0), 2)     # canopy radius
            trees.append((round(cx, 2), round(cy, 2), th, cr))
    return trees


def _frange(a, b, step):
    x = a
    while x <= b + 1e-9:
        yield x
        x += step


def _in_clearing(x, y):
    return any((x - cx) ** 2 + (y - cy) ** 2 < r ** 2 for cx, cy, r in _CLEARINGS)


def _near_victim(x, y):
    return (x - VICTIM[0]) ** 2 + (y - VICTIM[1]) ** 2 < 3.0 ** 2


# (cx, cy, trunk_height, canopy_radius)
TREES = _gen_trees()

# Canopy depth (m) at/above which the fading model switches LoS -> NLoS.
DEPTH_NLOS_M = 2.0
# Cap accumulated canopy depth so a grazing near-horizontal ray through the
# whole forest can't produce an absurd loss (PLOSS_MAX clamps too).
DEPTH_MAX_M = 70.0


def _canopy_center(t):
    cx, cy, th, cr = t
    return (cx, cy, th + cr * 0.6, cr)     # matches _tree_sdf canopy z


def _segment_sphere_len(p0, p1, center, radius):
    """Length of the segment p0->p1 that lies inside the sphere (center,
    radius). 0 if it misses. Standard ray-sphere, clamped to [0, 1]."""
    dx, dy, dz = (p1[0] - p0[0], p1[1] - p0[1], p1[2] - p0[2])
    seg_len = math.sqrt(dx * dx + dy * dy + dz * dz)
    if seg_len < 1e-9:
        return 0.0
    ux, uy, uz = dx / seg_len, dy / seg_len, dz / seg_len
    ox, oy, oz = (p0[0] - center[0], p0[1] - center[1], p0[2] - center[2])
    b = ox * ux + oy * uy + oz * uz
    c = ox * ox + oy * oy + oz * oz - radius * radius
    disc = b * b - c
    if disc <= 0.0:
        return 0.0
    s = math.sqrt(disc)
    t0, t1 = -b - s, -b + s              # entry/exit along the unit ray
    t0 = max(t0, 0.0)
    t1 = min(t1, seg_len)
    return max(0.0, t1 - t0)


def foliage_depth(p0, p1):
    """Total metres of tree canopy the 3-D segment p0->p1 passes through,
    summed over every tree (canopies may overlap; that just means denser
    forest -> more loss). Capped at DEPTH_MAX_M."""
    total = 0.0
    for t in TREES:
        cx, cy, cz, cr = _canopy_center(t)
        total += _segment_sphere_len(p0, p1, (cx, cy, cz), cr)
        if total >= DEPTH_MAX_M:
            return DEPTH_MAX_M
    return total


def foliage_loss_db(depth_m, freq_ghz=3.32):
    """Weissberger's modified exponential-decay foliage loss (dB) for a ray
    that traverses depth_m of canopy at freq_ghz. Two regimes:
        0  < d <= 14 m : 0.45 * f^0.284 * d
        14 < d <= 400 m: 1.33 * f^0.284 * d^0.588
    (Weissberger, 1982 — the standard closed-form foliage attenuation.)"""
    d = max(0.0, min(depth_m, DEPTH_MAX_M))
    if d <= 0.0:
        return 0.0
    f284 = freq_ghz ** 0.284
    if d <= 14.0:
        return 0.45 * f284 * d
    return 1.33 * f284 * (d ** 0.588)


# ── SDF generation ────────────────────────────────────────────────────────────
def _tree_sdf(i, cx, cy, th, cr):
    """Trunk (cylinder, collision so robots weave around it) + canopy
    (sphere, visual only — it's above robot height)."""
    canopy_z = th + cr * 0.6
    return (
        f'    <model name="tree_{i}">\n'
        f'      <static>true</static>\n'
        f'      <pose>{cx} {cy} 0 0 0 0</pose>\n'
        f'      <link name="link">\n'
        f'        <collision name="trunk_c">\n'
        f'          <pose>0 0 {th/2.0:.2f} 0 0 0</pose>\n'
        f'          <geometry><cylinder><radius>0.35</radius><length>{th}</length></cylinder></geometry>\n'
        f'        </collision>\n'
        f'        <visual name="trunk">\n'
        f'          <pose>0 0 {th/2.0:.2f} 0 0 0</pose>\n'
        f'          <geometry><cylinder><radius>0.35</radius><length>{th}</length></cylinder></geometry>\n'
        f'          <material><ambient>0.35 0.25 0.15 1</ambient><diffuse>0.4 0.28 0.17 1</diffuse></material>\n'
        f'        </visual>\n'
        f'        <visual name="canopy">\n'
        f'          <pose>0 0 {canopy_z:.2f} 0 0 0</pose>\n'
        f'          <geometry><sphere><radius>{cr}</radius></sphere></geometry>\n'
        f'          <material><ambient>0.13 0.40 0.16 1</ambient><diffuse>0.17 0.48 0.20 1</diffuse></material>\n'
        f'        </visual>\n'
        f'      </link>\n'
        f'    </model>'
    )


def _victim_sdf():
    vx, vy = VICTIM
    return (
        f'    <model name="victim">\n'
        f'      <static>true</static>\n'
        f'      <pose>{vx} {vy} 0.5 0 0 0</pose>\n'
        f'      <link name="link">\n'
        f'        <visual name="visual">\n'
        f'          <geometry><cylinder><radius>0.3</radius><length>1.0</length></cylinder></geometry>\n'
        f'          <plugin filename="gz-sim-thermal-system" name="gz::sim::systems::Thermal">\n'
        f'            <temperature>310.0</temperature>\n'
        f'          </plugin>\n'
        f'          <material><ambient>0.9 0.5 0.4 1</ambient>'
        f'<diffuse>0.95 0.55 0.45 1</diffuse></material>\n'
        f'        </visual>\n'
        f'      </link>\n'
        f'    </model>'
    )


def sdf_models():
    """SDF <model> blocks for every tree plus the victim, for the world."""
    out = [_tree_sdf(i, *t) for i, t in enumerate(TREES)]
    out += [_victim_sdf()]
    return "\n".join(out)


if __name__ == "__main__":
    print(f"{len(TREES)} trees; sample foliage loss:")
    for depth in (2, 5, 14, 30, 60):
        print(f"  {depth:>2} m canopy -> {foliage_loss_db(depth):.1f} dB")
