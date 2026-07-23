"""terrain_hills.py — single source of truth for the sar_hills world.

The SAME hill list drives (a) the Gazebo world geometry, via
make_hills_world.py, and (b) the air-to-ground channel model, via
channel_bridge.py — which treats each hilltop that intrudes into the
UAV->UE path as a knife edge and applies ITU-R P.526 single-knife-edge
diffraction loss. So the radio and the visuals agree on where the ridges
are, and climbing the UAV to clear a ridge restores line of sight.

Each hill: (cx, cy, radius, height) in metres — rendered as a half-buried
ellipsoid (a smooth dome of `height` standing on a circular base of
`radius`). Hills are solid: a ground robot drives around them, and the UAV
must climb above the ridge line to serve a robot in the shadow behind one.

NOTE: this is an obstacle-mound approximation of hilly terrain, not a full
DEM heightmap — robots go *around* the domes, not *over* graded slopes.
It is enough to study terrain shadowing / the altitude coverage lever; a
heightmap upgrade is a documented follow-on.
"""
import math

# (cx, cy, radius, height) — kept clear of the (0,0) and (+/-15,0) spawns.
# The first hill is a ridge sited between the central UAV and the victim so
# the victim is shadowed at low altitude and clears as the UAV climbs.
HILLS = [
    ( 45,   0, 20, 18),   # ridge shadowing the victim from the central UAV
    ( 55,  35, 24, 16),
    (-50,  30, 24, 15),
    ( 35, -55, 26, 20),
    (-45, -45, 25, 16),
    (  0,  70, 30, 22),
    (  0, -68, 27, 14),
    (-72,  10, 24, 17),
]

# Victim (thermal source) in the valley behind the eastern ridge, so the
# UAV is shadowed until it climbs / repositions overhead.
VICTIM = (70.0, 6.0)

# Diffraction loss (dB) at/above which the fading model switches LoS->NLoS.
DIFFRACTION_NLOS_DB = 6.0
DIFFRACTION_MAX_DB = 40.0
_C = 3e8


def _knife_edge_loss(v):
    """ITU-R P.526 single knife-edge diffraction loss J(v) in dB."""
    if v <= -0.78:
        return 0.0
    return 6.9 + 20.0 * math.log10(math.sqrt((v - 0.1) ** 2 + 1.0) + v - 0.1)


def terrain_diffraction_db(p0, p1, freq_ghz=3.32):
    """Worst-case single-knife-edge diffraction loss (dB) over all hills
    that intrude into the UAV->UE path p0->p1. For each hill whose base the
    ray passes over, the hilltop is the knife edge: h is its height above
    the straight LoS line, and v is the Fresnel-Kirchhoff parameter."""
    lam = _C / (freq_ghz * 1e9)
    ax, ay = (p1[0] - p0[0], p1[1] - p0[1])
    horiz = math.hypot(ax, ay)
    if horiz < 1e-6:
        return 0.0
    worst = 0.0
    for hx, hy, radius, height in HILLS:
        # project the hill centre onto the ray in the horizontal plane
        t = ((hx - p0[0]) * ax + (hy - p0[1]) * ay) / (horiz * horiz)
        if t <= 0.0 or t >= 1.0:                 # edge not between endpoints
            continue
        cx, cy = (p0[0] + t * ax, p0[1] + t * ay)
        if math.hypot(hx - cx, hy - cy) > radius:  # ray passes to the side
            continue
        los_z = p0[2] + t * (p1[2] - p0[2])
        h = height - los_z                        # obstruction above LoS
        if h <= 0.0:
            continue
        d1 = max(t * horiz, 1.0)
        d2 = max((1.0 - t) * horiz, 1.0)
        v = h * math.sqrt(2.0 / lam * (1.0 / d1 + 1.0 / d2))
        worst = max(worst, _knife_edge_loss(v))
    return min(worst, DIFFRACTION_MAX_DB)


# ── SDF generation ────────────────────────────────────────────────────────────
def _hill_sdf(i, cx, cy, radius, height):
    """Half-buried ellipsoid: dome of `height` on a circular base `radius`."""
    return (
        f'    <model name="hill_{i}">\n'
        f'      <static>true</static>\n'
        f'      <pose>{cx} {cy} 0 0 0 0</pose>\n'
        f'      <link name="link">\n'
        f'        <collision name="collision">\n'
        f'          <geometry><ellipsoid><radii>{radius} {radius} {height}</radii></ellipsoid></geometry>\n'
        f'        </collision>\n'
        f'        <visual name="visual">\n'
        f'          <geometry><ellipsoid><radii>{radius} {radius} {height}</radii></ellipsoid></geometry>\n'
        f'          <material><ambient>0.34 0.40 0.24 1</ambient>'
        f'<diffuse>0.40 0.47 0.28 1</diffuse><specular>0.05 0.05 0.05 1</specular></material>\n'
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
    """SDF <model> blocks for every hill plus the victim, for the world."""
    out = [_hill_sdf(i, *h) for i, h in enumerate(HILLS)]
    out += [_victim_sdf()]
    return "\n".join(out)


if __name__ == "__main__":
    print(f"{len(HILLS)} hills, heights "
          f"{min(h[3] for h in HILLS)}-{max(h[3] for h in HILLS)} m")
    # sanity: the central UAV is shadowed by the ridge until it climbs
    for uav_h in (20, 40, 60):
        loss = terrain_diffraction_db((0, 0, uav_h), (VICTIM[0], VICTIM[1], 0.5))
        print(f"  UAV (0,0,h={uav_h}) -> victim {VICTIM}: {loss:.1f} dB diffraction")
