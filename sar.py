#!/usr/bin/env python3
"""sar.py — control centre for the SAR 5G nomadic-node simulation.

One front door to choose the terrain (world), start the stack, run patrols,
stream over 5G, fly the UAV, check status, and stop everything. Wraps the
existing scripts (start_full_stack.sh, auto_patrol.py, stream_*.sh) — it does
not reimplement them.

Two ways to use it:

  Interactive menu (just run it):
      ./sar.py

  Direct commands (scriptable):
      ./sar.py start  --world sar_forest
      ./sar.py patrol --mode both --duration 1800 --max-radius 110
      ./sar.py stream --kind qos --robot both --duration 120
      ./sar.py fly    --nomadic
      ./sar.py collect --world sar_urban --duration 1800   # stream + patrol
      ./sar.py status
      ./sar.py stop

The full stack needs sudo (Open5GS/netns): export SAR_SUDO_PASS or configure
passwordless sudo. Patrols/streaming/fly need the stack already running.
"""
import argparse
import os
import subprocess
import sys
import time
from getpass import getpass

ROOT = os.path.dirname(os.path.abspath(__file__))

# ── Catalogue ─────────────────────────────────────────────────────────────────
WORLDS = {
    "sar_open_field": "Flat ground, full line-of-sight (baseline)",
    "sar_urban":      "Building NLoS — climb over rooftops to restore LoS",
    "sar_forest":     "Foliage attenuation (Weissberger) — graded by canopy depth",
    "sar_hills":      "Terrain shadowing (ITU-R P.526 knife-edge diffraction)",
    "sar_indoor":     "Enterable building — robot searches inside; O2I penetration",
}

# label -> auto_patrol.py flag ("" = default linear single-robot sweep)
PATROL_MODES = {
    "linear":  ("", "single robot, out-and-back sweep 10->200 m"),
    "random":  ("--random", "single robot, 2 m random walk"),
    "both":    ("--both", "alpha + bravo, independent random walks"),
    "free":    ("--free", "both robots roam continuously (cmd_vel)"),
    "circle":  ("--circle", "circular sweep around the origin"),
    "search":  ("--search", "structured search pattern"),
    "nomadic": ("--vary-uav", "UAV flies a 9-waypoint x/y/z schedule; robots follow"),
}

# ── ANSI helpers ──────────────────────────────────────────────────────────────
def _c(code, s):
    return f"\033[{code}m{s}\033[0m" if sys.stdout.isatty() else s

def bold(s):  return _c("1", s)
def green(s): return _c("32", s)
def cyan(s):  return _c("36", s)
def red(s):   return _c("31", s)
def dim(s):   return _c("2", s)


# ── Process helpers ───────────────────────────────────────────────────────────
def sudo_env():
    """Return an env dict with SAR_SUDO_PASS populated (prompting once if the
    stack needs it and it isn't already set / passwordless)."""
    env = dict(os.environ)
    if env.get("SAR_SUDO_PASS"):
        return env
    if subprocess.run(["sudo", "-n", "true"], capture_output=True).returncode == 0:
        return env                       # passwordless sudo configured
    try:
        env["SAR_SUDO_PASS"] = getpass("  sudo password (for Open5GS/netns): ")
    except (EOFError, KeyboardInterrupt):
        print()
    return env


def run(cmd, env=None, background=False):
    """Run a command from the repo root. Foreground inherits the terminal
    (so live output + Ctrl-C work); background is detached."""
    print(dim(f"  $ {' '.join(cmd)}"))
    if background:
        return subprocess.Popen(cmd, cwd=ROOT, env=env,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                start_new_session=True)
    try:
        return subprocess.run(cmd, cwd=ROOT, env=env)
    except KeyboardInterrupt:
        print(cyan("\n  (interrupted — back to menu)"))
        return None


# ── Actions ───────────────────────────────────────────────────────────────────
def act_start(world):
    if world not in WORLDS:
        print(red(f"unknown world: {world}")); return
    print(bold(f"\nStarting full stack in {green(world)} ..."))
    env = sudo_env()
    env["SAR_WORLD"] = world
    run(["bash", "start_full_stack.sh"], env=env)


def act_patrol(mode, duration, max_radius, min_radius, speed, extra=None):
    if mode not in PATROL_MODES:
        print(red(f"unknown mode: {mode}")); return
    flag = PATROL_MODES[mode][0]
    cmd = ["python3", "scripts/auto_patrol.py"]
    if flag:
        cmd.append(flag)
    if duration:   cmd.append(f"--duration={duration}")
    if max_radius: cmd.append(f"--max-radius={max_radius}")
    if min_radius: cmd.append(f"--min-radius={min_radius}")
    if speed:      cmd.append(f"--speed={speed}")
    if extra:      cmd += extra
    print(bold(f"\nPatrol: {green(mode)} — {PATROL_MODES[mode][1]}"))
    print(dim("  (Ctrl-C stops the patrol and returns here)"))
    run(cmd)


def act_stream(kind, robot, duration, bitrate, sensor="rgb", background=False):
    env = sudo_env()
    if kind == "camera":
        cmd = ["bash", "stream_camera.sh", robot, str(duration), str(bitrate), sensor]
    elif kind == "video":
        cmd = ["bash", "stream_video.sh", robot, str(duration), str(bitrate)]
    else:  # qos
        cmd = ["bash", "stream_demo.sh", robot, str(duration), str(bitrate)]
    print(bold(f"\nStream ({green(kind)}) — {robot}, {duration}s"))
    return run(cmd, env=env, background=background)


def act_fly(nomadic, duration):
    if nomadic:
        act_patrol("nomadic", duration, None, None, None)
    else:
        print(bold("\nUAV teleport demo (x/y/z tour) ..."))
        run(["bash", os.path.join(ROOT, "_fly_demo.sh")]) if os.path.exists(
            os.path.join(ROOT, "_fly_demo.sh")) else _inline_fly()


def _inline_fly():
    """Teleport the UAV through a small x/y/z tour via gz set_pose."""
    world = _running_world() or "sar_open_field"
    wps = [(0, 0, 40), (0, 0, 60), (30, 0, 60), (30, 30, 50),
           (-30, 30, 45), (-30, -30, 55), (0, 0, 40)]
    for x, y, z in wps:
        subprocess.run(
            ["gz", "service", "-s", f"/world/{world}/set_pose",
             "--reqtype", "gz.msgs.Pose", "--reptype", "gz.msgs.Boolean",
             "--timeout", "2000",
             "--req", f'name: "sar_uav" position: {{x: {x}, y: {y}, z: {z}}}'],
            capture_output=True)
        print(f"  UAV -> x={x} y={y} z={z}")
        time.sleep(2)
    print(green("  flight tour done"))


def act_collect(world, duration, mode="both", max_radius=110, speed=None):
    """The research run: QoS stream (background) + a patrol (foreground),
    both in the given world. Assumes the stack is already up in that world."""
    running = _running_world()
    if running != world:
        print(red(f"stack is in '{running or 'down'}', not '{world}'."))
        print("  Start it first:  ./sar.py start --world " + world)
        return
    env = sudo_env()
    print(bold(f"\nData-collection run in {green(world)} — {duration}s"))
    proc = act_stream("qos", "both", duration, 3, background=True)
    print(green("  QoS stream running in background -> logs/stream_*.csv"))
    act_patrol(mode, duration, max_radius, None, speed)
    if proc:
        proc.terminate()
    print(green("  collection finished — see logs/patrol_*.csv + logs/stream_*.csv"))


def act_status():
    print(bold("\nStack status:"))
    checks = [
        ("Gazebo server", "gz [s]im -"),
        ("Gazebo GUI", "gz sim -[g]"),
        ("channel bridge", "channel_[b]ridge.py"),
        ("gNB (nr-softmodem)", "nr-[s]oftmodem"),
        ("UEs (nr-uesoftmodem)", "nr-[u]esoftmodem"),
        ("patrol", "auto_[p]atrol.py"),
    ]
    for label, pat in checks:
        n = _pgrep_count(pat)
        mark = green("UP  ") if n else red("down")
        print(f"  {mark}  {label}" + (dim(f"  x{n}") if n > 1 else ""))
    w = _running_world()
    print(f"  world: {green(w) if w else red('none (Gazebo down)')}")


def act_stop():
    print(bold("\nStopping the simulation ..."))
    patterns = [
        "gz [s]im", "gazebo_[v]iz.py", "channel_[b]ridge.py",
        "nr-[s]oftmodem", "nr-[u]esoftmodem", "log_[j]anitor.sh",
        "auto_[p]atrol.py", "sar_stream_[s]ender.py", "sar_stream_[c]ollector.py",
        "gz_camera_[b]ridge.py",
    ]
    for pat in patterns:
        subprocess.run(["pkill", "-9", "-f", pat], capture_output=True)
    print(green("  simulation processes stopped."))
    print(dim("  (Open5GS core, MongoDB and robot netns are left as-is — they"))
    print(dim("   need sudo to tear down and are harmless idle. Use"))
    print(dim("   'sudo scripts/setup_robot_netns.sh teardown' to remove netns.)"))


# ── Small probes ──────────────────────────────────────────────────────────────
def _pgrep_count(pattern):
    r = subprocess.run(["pgrep", "-af", pattern], capture_output=True, text=True)
    return len([l for l in r.stdout.splitlines() if l.strip()])


def _running_world():
    r = subprocess.run(["pgrep", "-af", "gz [s]im -"], capture_output=True, text=True)
    for line in r.stdout.splitlines():
        for tok in line.split():
            if tok.endswith(".sdf"):
                return os.path.splitext(os.path.basename(tok))[0]
    return None


# ── Interactive menu ──────────────────────────────────────────────────────────
def _choose(title, options):
    """options: list of (key, label). Returns chosen key or None."""
    print(bold("\n" + title))
    for i, (key, label) in enumerate(options, 1):
        print(f"  {cyan(str(i))}) {key:<16} {dim(label)}")
    print(f"  {cyan('0')}) back")
    try:
        raw = input(bold("  choose > ")).strip()
    except (EOFError, KeyboardInterrupt):
        return None
    if raw in ("0", ""):
        return None
    if raw.isdigit() and 1 <= int(raw) <= len(options):
        return options[int(raw) - 1][0]
    print(red("  invalid choice"))
    return None


def _ask(prompt, default):
    try:
        raw = input(f"  {prompt} [{green(str(default))}]: ").strip()
    except (EOFError, KeyboardInterrupt):
        return default
    return raw or default


def menu():
    while True:
        w = _running_world()
        banner = f"  world up: {green(w)}" if w else red("  stack down")
        print(bold("\n══════ SAR simulation control ══════") + "\n" + banner)
        action = _choose("What do you want to do?", [
            ("start",   "launch the full stack in a chosen terrain"),
            ("patrol",  "run a robot patrol (choose mode)"),
            ("stream",  "stream a robot feed over 5G"),
            ("fly",     "fly / move the UAV"),
            ("collect", "research run: QoS stream + patrol together"),
            ("status",  "show what's running"),
            ("stop",    "stop everything"),
        ])
        if action is None:
            print("bye"); return
        if action == "start":
            world = _choose("Choose a terrain:",
                            [(k, v) for k, v in WORLDS.items()])
            if world:
                act_start(world)
        elif action == "patrol":
            mode = _choose("Choose a patrol mode:",
                           [(k, v[1]) for k, v in PATROL_MODES.items()])
            if mode:
                dur = _ask("duration (s)", 300)
                rad = _ask("max radius (m)", 110)
                # Robot speed only drives the continuous-motion modes; the
                # teleport modes (linear/random/both) ignore it.
                spd = (_ask("robot speed (m/s)", 1.5)
                       if mode in ("free", "search", "nomadic") else None)
                act_patrol(mode, dur, rad, None, spd)
        elif action == "stream":
            kind = _choose("Stream type:", [
                ("qos", "measured QoS -> CSV (research data)"),
                ("camera", "robot's real camera POV (rgb/thermal)"),
                ("video", "synthetic H.264 test pattern"),
            ])
            if kind:
                robot = _ask("robot (alpha/bravo/both)",
                             "both" if kind == "qos" else "bravo")
                dur = _ask("duration (s)", 120)
                if kind == "camera":
                    sensor = _ask("sensor (rgb/thermal)", "rgb")
                    act_stream("camera", robot, dur, 1500, sensor)
                elif kind == "video":
                    act_stream("video", robot, dur, 1500)
                else:
                    act_stream("qos", robot, dur, 3)
        elif action == "fly":
            sub = _choose("UAV motion:", [
                ("nomadic", "scripted 9-waypoint flight, robots follow (logs data)"),
                ("tour", "quick x/y/z teleport tour (visual)"),
            ])
            if sub == "nomadic":
                act_fly(True, _ask("duration (s)", 600))
            elif sub == "tour":
                act_fly(False, None)
        elif action == "collect":
            if not w:
                print(red("  start the stack first.")); continue
            cmode = _choose("Patrol pattern for the run:",
                            [(k, v[1]) for k, v in PATROL_MODES.items()]) or "both"
            dur = _ask("duration (s)", 1800)
            spd = (_ask("robot speed (m/s)", 1.5)
                   if cmode in ("free", "search", "nomadic") else None)
            act_collect(w, dur, mode=cmode, speed=spd)
        elif action == "status":
            act_status()
        elif action == "stop":
            act_stop()


# ── CLI ───────────────────────────────────────────────────────────────────────
def main():
    p = argparse.ArgumentParser(description="SAR simulation control centre")
    sub = p.add_subparsers(dest="cmd")

    s = sub.add_parser("start", help="launch the full stack in a world")
    s.add_argument("--world", default="sar_open_field", choices=list(WORLDS))

    pa = sub.add_parser("patrol", help="run a robot patrol")
    pa.add_argument("--mode", default="both", choices=list(PATROL_MODES))
    pa.add_argument("--duration", type=int, default=300)
    pa.add_argument("--max-radius", type=int, default=110)
    pa.add_argument("--min-radius", type=int, default=None)
    pa.add_argument("--speed", type=float, default=None)

    st = sub.add_parser("stream", help="stream a robot feed over 5G")
    st.add_argument("--kind", default="qos", choices=["qos", "camera", "video"])
    st.add_argument("--robot", default="both")
    st.add_argument("--duration", type=int, default=120)
    st.add_argument("--bitrate", type=int, default=None)
    st.add_argument("--sensor", default="rgb", choices=["rgb", "thermal"])

    fl = sub.add_parser("fly", help="move the UAV")
    fl.add_argument("--nomadic", action="store_true", help="scripted flight + follow")
    fl.add_argument("--duration", type=int, default=600)

    co = sub.add_parser("collect", help="QoS stream + patrol together")
    co.add_argument("--world", default="sar_urban", choices=list(WORLDS))
    co.add_argument("--duration", type=int, default=1800)
    co.add_argument("--mode", default="both", choices=list(PATROL_MODES))
    co.add_argument("--speed", type=float, default=None,
                    help="robot speed m/s (free/search/nomadic modes)")

    sub.add_parser("status", help="show what's running")
    sub.add_parser("stop", help="stop everything")

    args = p.parse_args()
    if args.cmd is None:
        menu(); return
    if args.cmd == "start":
        act_start(args.world)
    elif args.cmd == "patrol":
        act_patrol(args.mode, args.duration, args.max_radius,
                   args.min_radius, args.speed)
    elif args.cmd == "stream":
        default_br = {"qos": 3, "camera": 1500, "video": 1500}[args.kind]
        act_stream(args.kind, args.robot, args.duration,
                   args.bitrate or default_br, args.sensor)
    elif args.cmd == "fly":
        act_fly(args.nomadic, args.duration)
    elif args.cmd == "collect":
        act_collect(args.world, args.duration, args.mode, speed=args.speed)
    elif args.cmd == "status":
        act_status()
    elif args.cmd == "stop":
        act_stop()


if __name__ == "__main__":
    main()
