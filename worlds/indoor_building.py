"""indoor_building.py — single source of truth for the sar_indoor world.

A warehouse-scale enclosed building a ground robot can DRIVE INTO and search
room by room. The SAME wall list drives (a) the Gazebo world geometry, via
make_indoor_world.py, and (b) the air-to-ground channel model, via
channel_bridge.py — which applies a 3GPP TR 38.901 outdoor-to-indoor
penetration loss (a fixed roof/wall penetration term plus 0.5 dB per metre
of depth into the building) whenever the robot is inside the footprint.

Layout (axis-aligned, footprint x:[-30,30] y:[-20,20], walls 4 m tall):
  Warehouse — 6 rooms around a central corridor:

                      NORTH (y=20)
         ┌───────────┬───────────┬───────────┐
         │ back-left │back-center│ back-right │
         │(-30,-10)  │(-10,10)   │ (10,30)   │ y[10,20]
         │  victim   │           │            │
    ─────┤  door x=0 ├─ door x=0 ├─ door x=0 ├───── y=10
         │           │  CENTRE   │            │
         │ back-left │    +      │ back-right │
         │(-30,-10)  │ corridor  │ (10,30)   │ y[0,10]
         │           │           │            │
    ═════╪═══════════╪═══door════╪═══════════╪═════ y=0
         │           │           │            │
         │front-left │  FRONT    │front-right │
         │(-30,-10)  │   HALL    │ (10,30)   │ y[-20,0]
         │           │(-10,10)   │            │
         │  door x=0 │           │  door x=0  │
         └─────door──┴────door───┴─────door───┘ y=-20
                     SOUTH (y=-20)

  - south wall: 3 m entry door (gap at x in [-1.5, 1.5])
  - interior partition at y=0, doorway at x in [-2.5, 2.5]
  - back partition at y=10, doorway at x in [-2.5, 2.5]
  - left/right partitions at x=±10, doorway at y in [-2.5, 2.5]
  - victim (thermal source) in the back-left room at (-20, 15)

The building is rendered OPEN-TOP so you can watch the search from above.
The channel model applies roof-penetration loss as if enclosed — realistic
for an overhead UAV serving a robot inside a structure.  The gNB drone
stays OUTSIDE the building footprint so the O2I penetration path is real.
"""

# Outer footprint bounds (metres): xmin, xmax, ymin, ymax.
OUTER = (-30.0, 30.0, -20.0, 20.0)
WALL_H = 4.0
_T = 0.2   # wall thickness

# Each wall: (cx, cy, size_x, size_y, height). Axis-aligned boxes; door and
# doorway gaps are simply absent segments the robot drives through.
WALLS = [
    # ── outer shell ───────────────────────────────────────────────────────
    (  0.0, 20.0, 60.0 + _T, _T, WALL_H),   # north
    ( 30.0,  0.0, _T, 40.0 + _T, WALL_H),   # east
    (-30.0,  0.0, _T, 40.0 + _T, WALL_H),   # west
    (-15.75, -20.0, 28.5, _T, WALL_H),      # south-left  (door gap x[-1.5,1.5])
    ( 15.75, -20.0, 28.5, _T, WALL_H),      # south-right
    # ── interior partition at y=0 (doorway x[-2.5, 2.5]) ─────────────────
    (-16.25,  0.0, 27.5, _T, WALL_H),       # left  x[-30,-2.5]
    ( 16.25,  0.0, 27.5, _T, WALL_H),       # right x[2.5, 30]
    # ── back partition at y=10 (doorway x[-2.5, 2.5]) ────────────────────
    (-16.25, 10.0, 27.5, _T, WALL_H),       # left  x[-30,-2.5]
    ( 16.25, 10.0, 27.5, _T, WALL_H),       # right x[2.5, 30]
    # ── left/right partitions at x=±10 (doorway y[-2.5, 2.5]) ────────────
    (-10.0, -11.25, _T, 17.5, WALL_H),      # left-bottom  y[-20,-2.5]
    (-10.0,  11.25, _T, 17.5, WALL_H),      # left-top     y[2.5, 20]
    ( 10.0, -11.25, _T, 17.5, WALL_H),      # right-bottom y[-20,-2.5]
    ( 10.0,  11.25, _T, 17.5, WALL_H),      # right-top    y[2.5, 20]
]

# Victim in the back-left room (deep inside, high O2I penetration).
VICTIM = (-20.0, 15.0)

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
    for pt in [(0, -15, "front hall"), (20, 5, "east room"),
               (-20, 15, "back-left (victim)"), VICTIM + ("victim",)]:
        x, y, label = pt
        b, e = indoor_loss((0, 0, 40), (x, y, 0.5))
        print(f"  ({x:>3},{y:>2}) {label:<24} inside={b}  excess={e:.1f} dB")
