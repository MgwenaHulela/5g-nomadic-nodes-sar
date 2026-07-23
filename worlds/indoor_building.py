"""indoor_building.py — single source of truth for the sar_indoor world.

A single enclosed building a ground robot can DRIVE INTO and search room by
room. The SAME wall list drives (a) the Gazebo world geometry, via
make_indoor_world.py, and (b) the air-to-ground channel model, via
channel_bridge.py — which applies a 3GPP TR 38.901 outdoor-to-indoor
penetration loss (a fixed roof/wall penetration term plus 0.5 dB per metre
of depth into the building) whenever the robot is inside the footprint.

Layout (axis-aligned, footprint x:[-10,10] y:[-8,8], walls 3 m tall):
  - south wall with a 3 m entry door (gap at x in [-1.5, 1.5])
  - an interior partition at y=0 (doorway at x in [3, 5]) splitting the
    front hall from the back
  - a back partition at x=0 (doorway at y in [5, 7]) splitting the back
    into two rooms; the victim (thermal source) sits in the back-left room
So a robot enters the south door -> front hall -> east doorway -> back-right
room -> north doorway -> back-left room to reach the victim.

The building is rendered OPEN-TOP so you can watch the search from above in
the GUI (or via the robot's onboard camera). The channel model still
applies a roof-penetration loss as if it were enclosed — realistic for an
overhead UAV serving a robot inside a structure. Because the loss is
penetration-limited (not a rooftop block), the coverage lever here is the
robot leaving the building, NOT the UAV climbing — the opposite of the
urban/hills worlds, which is the point of having it.
"""

# Outer footprint bounds (metres): xmin, xmax, ymin, ymax.
OUTER = (-10.0, 10.0, -8.0, 8.0)
WALL_H = 3.0
_T = 0.2   # wall thickness

# Each wall: (cx, cy, size_x, size_y, height). Axis-aligned boxes; door and
# doorway gaps are simply absent segments the robot drives through.
WALLS = [
    # outer shell
    (  0.0,  8.0, 20.0 + _T, _T, WALL_H),   # north
    ( 10.0,  0.0, _T, 16.0 + _T, WALL_H),   # east
    (-10.0,  0.0, _T, 16.0 + _T, WALL_H),   # west
    (-5.75, -8.0, 8.5, _T, WALL_H),         # south-left  (door gap x[-1.5,1.5])
    ( 5.75, -8.0, 8.5, _T, WALL_H),         # south-right
    # interior partition at y=0, doorway x[3,5]
    (-3.5,   0.0, 13.0, _T, WALL_H),
    ( 7.5,   0.0, 5.0, _T, WALL_H),
    # back partition at x=0 (y[0,8]), doorway y[5,7]
    (  0.0,  2.5, _T, 5.0, WALL_H),
    (  0.0,  7.5, _T, 1.0, WALL_H),
]

# Victim in the back-left room.
VICTIM = (-5.0, 4.0)

# 3GPP TR 38.901 outdoor-to-indoor: penetration term + 0.5 dB per metre of
# depth into the building.
PENETRATION_DB = 9.0
DEPTH_LOSS_DB_PER_M = 0.5
INDOOR_MAX_DB = 40.0


def inside_footprint(x, y):
    xmin, xmax, ymin, ymax = OUTER
    return xmin <= x <= xmax and ymin <= y <= ymax


def _depth_into_building(x, y):
    """Metres from the UE to the nearest exterior wall — how deep inside."""
    xmin, xmax, ymin, ymax = OUTER
    return max(0.0, min(x - xmin, xmax - x, y - ymin, ymax - y))


def indoor_loss(uav, ue):
    """(los_blocked, excess_db) for the indoor world.

    Outside the building: (None, 0) -> open-field LoS model. Inside:
    deterministic NLoS plus O2I penetration + depth loss."""
    x, y = ue[0], ue[1]
    if not inside_footprint(x, y):
        return (None, 0.0)
    excess = PENETRATION_DB + DEPTH_LOSS_DB_PER_M * _depth_into_building(x, y)
    return (True, min(excess, INDOOR_MAX_DB))


# ── SDF generation ────────────────────────────────────────────────────────────
def _wall_sdf(i, cx, cy, sx, sy, h):
    return (
        f'    <model name="wall_{i}">\n'
        f'      <static>true</static>\n'
        f'      <pose>{cx} {cy} {h/2.0:.2f} 0 0 0</pose>\n'
        f'      <link name="link">\n'
        f'        <collision name="collision">\n'
        f'          <geometry><box><size>{sx} {sy} {h}</size></box></geometry>\n'
        f'        </collision>\n'
        f'        <visual name="visual">\n'
        f'          <geometry><box><size>{sx} {sy} {h}</size></box></geometry>\n'
        f'          <material><ambient>0.72 0.70 0.66 1</ambient>'
        f'<diffuse>0.78 0.76 0.72 1</diffuse><specular>0.1 0.1 0.1 1</specular></material>\n'
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
    """SDF <model> blocks for every wall plus the victim, for the world."""
    out = [_wall_sdf(i, *w) for i, w in enumerate(WALLS)]
    out += [_victim_sdf()]
    return "\n".join(out)


if __name__ == "__main__":
    print(f"{len(WALLS)} walls; sample indoor loss (UAV overhead):")
    for pt in [(0, -6, "front hall"), (4, 3, "back-right"), VICTIM + ("victim (back-left)",)]:
        x, y, label = pt
        b, e = indoor_loss((0, 0, 40), (x, y, 0.5))
        print(f"  ({x:>3},{y:>2}) {label:<20} inside={b}  excess={e:.1f} dB")
