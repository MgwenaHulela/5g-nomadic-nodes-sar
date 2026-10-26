#!/usr/bin/env python3
"""
dashboard.py — local web view of SAR campaign progress.

Answers "how far along is the experiment?" at a glance: which runs are done,
what the current run is doing, and whether every robot still has a link.

Runs against the live log tree, so it is read-only and safe to start or stop
at any time — it never touches the campaign. Standard library only, no
install step:

    python3 scripts/dashboard.py            # http://127.0.0.1:8742
    python3 scripts/dashboard.py --port 9000 --host 0.0.0.0

Design notes (why it looks the way it does):
  * Robot identity is carried by a NAME LABEL on every panel, never by colour
    alone. The four series colours are documented palette slots validated
    all-pairs in both modes (worst CVD dE 13.0 light / 6.9 dark, worst
    normal-vision dE 19.6 / 19.3). The dark yellow/green pair sits in the
    CVD warn band and two light slots are sub-3:1 contrast, both of which are
    legal only with that secondary encoding — hence the always-on labels.
  * Link health is a STATUS colour (good/warning/critical), which is a
    separate reserved palette, and always ships with an icon and a word.
  * One chart form only: a per-robot sparkline, one series each (small
    multiples). Four overlaid series would need a 4-hue comparison that
    fails the normal-vision floor; separate labelled panels avoid it.
"""

import argparse
import csv
import io
import json
import os
import re
import time
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOGS = ROOT / "logs"

# Documented palette slots: blue, yellow, magenta, green.
# Chosen over slots 1-4 because orange+yellow fails the normal-vision floor
# (dE 13.7 < 15) when robots are compared pairwise.
SERIES = {
    "alpha":   {"light": "#2a78d6", "dark": "#3987e5"},
    "bravo":   {"light": "#eda100", "dark": "#c98500"},
    "charlie": {"light": "#e87ba4", "dark": "#d55181"},
    "delta":   {"light": "#008300", "dark": "#008300"},
}
COLS = {"alpha": "a", "bravo": "b", "charlie": "c", "delta": "d"}


def tail_lines(path, max_bytes=200_000):
    """Last chunk of a file as lines, without reading the whole thing.

    The fast-radio log reaches hundreds of thousands of rows in a long run;
    re-reading it every refresh would make the dashboard the heaviest process
    on a machine that is busy running the experiment.
    """
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as f:
            if size > max_bytes:
                f.seek(size - max_bytes)
                f.readline()  # discard the partial first line
            return f.read().decode("utf-8", "replace").splitlines()
    except OSError:
        return []


def read_header(path):
    try:
        with open(path, "r") as f:
            return next(csv.reader(f))
    except (OSError, StopIteration):
        return []


def campaigns():
    """Every campaign directory, newest first."""
    out = []
    for d in sorted(LOGS.glob("campaign_*"), reverse=True):
        clog = d / "campaign.log"
        if not clog.exists():
            continue
        txt = clog.read_text(errors="replace")
        m = re.search(r"worlds=\[([^\]]*)\]", txt)
        worlds = m.group(1).split() if m else []
        policy = (re.search(r"policy=(\S+)", txt) or [None, "two_phase"])[1]
        rows = []
        man = d / "manifest.csv"
        if man.exists():
            try:
                rows = list(csv.DictReader(io.StringIO(man.read_text(errors="replace"))))
            except Exception:
                rows = []
        done = [r for r in rows if r.get("status") == "ok"]
        failed = [r for r in rows if r.get("status") not in ("ok", None, "")]
        out.append({
            "name": d.name,
            "dir": str(d),
            "worlds": worlds,
            "policy": policy,
            "planned": len(worlds),
            "done": len(done),
            "failed": len(failed),
            "rows": rows,
            "complete": "CAMPAIGN COMPLETE" in txt,
            "mtime": d.stat().st_mtime,
        })
    return out


def active_run(camp):
    """The run currently in flight inside a campaign, if any."""
    d = Path(camp["dir"])
    for rlog in sorted(d.glob("*_research.log"), key=lambda p: p.stat().st_mtime,
                       reverse=True):
        txt = tail_lines(rlog, 40_000)
        # `Logging to:`, `run_id=` and `duration=` are printed ONCE at the
        # top of the research log. A six-hour run grows that file well past
        # the tail window, so they must be read from the HEAD — tailing
        # alone silently yields no CSV path and the dashboard then reports
        # zero rows for the whole run.
        head = []
        try:
            with open(rlog, "r", errors="replace") as f:
                for _ in range(60):
                    line = f.readline()
                    if not line:
                        break
                    head.append(line)
        except OSError:
            pass
        world = rlog.name.replace("_research.log", "")
        # The patrol writes to logs/patrol_<stamp>.csv and the campaign only
        # copies it into the campaign dir when the run FINISHES, so during a
        # run the only pointer to the live file is this line in the research
        # log. Reading the campaign dir instead shows 0 rows for the entire
        # six hours a run is actually in progress.
        csv_path = None
        for line in head + txt:
            m = re.search(r"Logging to:\s*(\S+)", line)
            if m:
                csv_path = m.group(1)
        if csv_path and not os.path.exists(csv_path):
            csv_path = None
        dur = None
        for line in head + txt:
            m = re.search(r"duration=(\d+)s", line)
            if m:
                dur = int(m.group(1))
        run_id = None
        for line in head + txt:
            m = re.search(r"run_id=(\S+)", line)
            if m:
                run_id = m.group(1)
        return {"world": world, "log": str(rlog), "csv": csv_path,
                "duration_s": dur, "run_id": run_id, "tail": txt[-14:]}
    return None


def robot_state(csv_path, roster):
    """Latest per-robot metrics plus a short RSRP history, from the cycle CSV."""
    if not csv_path or not os.path.exists(csv_path):
        return {}, 0, None
    hdr = read_header(csv_path)
    if not hdr:
        return {}, 0, None
    lines = tail_lines(csv_path, 400_000)
    data = [l for l in lines if l and not l.startswith("timestamp")]
    rows = list(csv.DictReader(io.StringIO("\n".join([",".join(hdr)] + data))))
    if not rows:
        return {}, 0, None

    def fnum(v):
        try:
            return float(v)
        except (TypeError, ValueError):
            return None

    state = {}
    for r in roster:
        c = COLS.get(r)
        if not c or f"{c}_c2_loss_pct" not in hdr:
            continue
        last = rows[-1]
        c2 = fnum(last.get(f"{c}_c2_loss_pct"))
        tcp = fnum(last.get(f"{c}_tcp_tp_mbps"))
        pl = fnum(last.get(f"{c}_ploss_dB"))
        hist = [fnum(x.get(f"{c}_ss_rsrp_dBre")) for x in rows[-60:]]
        hist = [h for h in hist if h is not None]
        # NB: `fnum(v) or 100` would be wrong here — 0.0 is falsy in Python,
        # so a row with PERFECT C2 (zero loss) would be scored as unavailable.
        # Check for None explicitly.
        _c2s = [fnum(x.get(f"{c}_c2_loss_pct")) for x in rows]
        avail = sum(1 for v in _c2s if v is not None and v < 50)
        n_known = sum(1 for v in _c2s if v is not None)
        # Status is derived from BOTH C2 and throughput: a link can report
        # C2 fine while carrying nothing, which is the stuck state that cost
        # an entire 3-hour indoor run before the watchdog was fixed.
        # `tcp` is None when the flow had just been relaunched and had no
        # interval line yet (probe_skipped=2). That is UNKNOWN, not zero:
        # `(tcp or 0) == 0` would be True for None and paint a perfectly
        # healthy robot critical — the same falsy-zero trap as the C2 check
        # above. Only treat throughput as stuck when it was actually measured.
        tcp_stuck = tcp is not None and tcp == 0
        if c2 is None:
            status = "unknown"
        elif c2 >= 50 and tcp_stuck:
            status = "critical"
        elif c2 >= 50 or tcp_stuck:
            status = "warning"
        else:
            status = "good"
        state[r] = {
            "c2_loss": c2, "tcp": tcp, "ploss": pl, "status": status,
            "avail_pct": round(100 * avail / n_known, 1) if n_known else None,
            "cycles": len(rows), "hist": hist[-40:],
        }
    t_elapsed = None
    try:
        t_elapsed = float(rows[-1].get("t_elapsed_s"))
    except (TypeError, ValueError):
        pass
    return state, len(rows), t_elapsed


def snapshot():
    camps = campaigns()
    cur = camps[0] if camps else None
    run = active_run(cur) if cur else None
    roster = [r.strip() for r in
              os.environ.get("SAR_ROBOTS", "alpha,bravo,charlie,delta").split(",")
              if r.strip()]
    robots, cycles, t_elapsed = ({}, 0, None)
    actions = fastradio = 0
    if run:
        robots, cycles, t_elapsed = robot_state(run.get("csv"), roster)
        # During a run these live in logs/ keyed by run_id; the campaign
        # directory only receives copies once the run finishes. Look in both,
        # or the dashboard reads zero for the entire time it matters most.
        run_id = run.get("run_id")
        cands_a = list(Path(cur["dir"]).glob("*_actions.csv"))
        cands_f = list(Path(cur["dir"]).glob("*_fastradio.csv"))
        if run_id:
            cands_a.append(LOGS / f"actions_{run_id}.csv")
            cands_f.append(LOGS / f"fastradio_{run_id}.csv")
        for p in cands_a:
            if p.exists():
                actions = max(actions, max(0, len(tail_lines(p, 400_000)) - 1))
        for p in cands_f:
            try:
                fastradio = max(fastradio, os.path.getsize(p))
            except OSError:
                pass
    live = bool(run and run.get("csv") and
                time.time() - os.path.getmtime(run["csv"]) < 300
                if run and run.get("csv") and os.path.exists(run["csv"]) else False)
    return {
        "now": datetime.now().strftime("%H:%M:%S"),
        "campaign": {k: cur[k] for k in
                     ("name", "worlds", "policy", "planned", "done", "failed",
                      "complete")} if cur else None,
        "history": [{k: c[k] for k in ("name", "planned", "done", "failed",
                                       "complete", "policy")} for c in camps[:8]],
        "run": {k: run[k] for k in ("world", "duration_s", "run_id", "tail")} if run else None,
        "live": live,
        "cycles": cycles,
        "t_elapsed": t_elapsed,
        "actions": actions,
        "fastradio_mb": round(fastradio / 1e6, 1),
        "roster": roster,
        "robots": robots,
        "series": SERIES,
    }


PAGE = """<!doctype html>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<title>SAR Campaign</title>
<style>
:root{
  color-scheme: light;
  --surface:#fcfcfb; --panel:#ffffff; --line:#e6e4df;
  --ink:#0b0b0b; --ink2:#52514e; --ink3:#86847d;
  --good:#0ca30c; --warning:#fab219; --critical:#d03b3b;
}
@media (prefers-color-scheme: dark){ :root:not([data-theme="light"]){
  color-scheme: dark;
  --surface:#1a1a19; --panel:#232321; --line:#35352f;
  --ink:#f0efec; --ink2:#c3c2b7; --ink3:#8d8c83;
}}
:root[data-theme="dark"]{
  color-scheme: dark;
  --surface:#1a1a19; --panel:#232321; --line:#35352f;
  --ink:#f0efec; --ink2:#c3c2b7; --ink3:#8d8c83;
}
*{box-sizing:border-box}
body{margin:0;background:var(--surface);color:var(--ink);
  font:14px/1.45 ui-sans-serif,system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;
  padding:0 16px env(safe-area-inset-bottom,0px)}
.wrap{max-width:1040px;margin:0 auto;padding-block:20px}
h1{font-size:19px;margin:0 0 2px;letter-spacing:-.01em}
.sub{color:var(--ink2);font-size:13px;margin-bottom:18px}
.dot{display:inline-block;width:8px;height:8px;border-radius:50%;margin-right:6px;vertical-align:1px}
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px;margin-bottom:18px}
.tile{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:12px 14px}
.tile .k{font-size:11px;text-transform:uppercase;letter-spacing:.06em;color:var(--ink3)}
.tile .v{font-size:26px;font-weight:600;margin-top:3px;letter-spacing:-.02em;
  font-variant-numeric:tabular-nums}
.tile .m{font-size:12px;color:var(--ink2);margin-top:1px}
.bar{height:6px;background:var(--line);border-radius:3px;overflow:hidden;margin-top:9px}
.bar>i{display:block;height:100%;background:var(--ink);border-radius:3px}
h2{font-size:12px;text-transform:uppercase;letter-spacing:.07em;color:var(--ink3);
  margin:22px 0 9px;font-weight:600}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(226px,1fr));gap:10px}
.card{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:12px 14px}
.card header{display:flex;align-items:center;gap:7px;margin-bottom:9px}
.swatch{width:9px;height:9px;border-radius:2px;flex:none}
.name{font-weight:600;font-size:13px;letter-spacing:-.01em}
.pill{margin-left:auto;font-size:11px;font-weight:600;padding:2px 7px;border-radius:999px;
  border:1px solid currentColor;display:inline-flex;align-items:center;gap:4px}
.kv{display:flex;justify-content:space-between;font-size:12px;color:var(--ink2);
  padding:2px 0;font-variant-numeric:tabular-nums}
.kv b{color:var(--ink);font-weight:600}
svg{display:block;width:100%;height:34px;margin-top:8px;overflow:visible}
table{width:100%;border-collapse:collapse;font-size:12.5px;
  font-variant-numeric:tabular-nums}
th{text-align:left;font-weight:600;color:var(--ink3);font-size:11px;
  text-transform:uppercase;letter-spacing:.05em;padding:5px 8px 5px 0;
  border-bottom:1px solid var(--line)}
td{padding:6px 8px 6px 0;border-bottom:1px solid var(--line);color:var(--ink2)}
td.n{color:var(--ink);font-weight:600}
pre{background:var(--panel);border:1px solid var(--line);border-radius:10px;
  padding:11px 13px;overflow-x:auto;font-size:11.5px;line-height:1.5;color:var(--ink2);
  margin:0;max-height:200px}
.empty{color:var(--ink3);font-size:13px;padding:16px 0}
</style>
<div class="wrap">
  <h1>SAR campaign</h1>
  <div class="sub" id="sub">loading…</div>
  <div class="tiles" id="tiles"></div>
  <h2>Robot links</h2>
  <div class="cards" id="robots"></div>
  <h2>Runs</h2>
  <div id="runs"></div>
  <h2>Latest output</h2>
  <pre id="tail">—</pre>
</div>
<script>
const dark = () => matchMedia('(prefers-color-scheme: dark)').matches
  && document.documentElement.dataset.theme !== 'light'
  || document.documentElement.dataset.theme === 'dark';
const esc = s => String(s).replace(/[&<>]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));
// Status always ships icon + word, never colour alone.
const STATUS = {
  good:     {c:'var(--good)',     i:'●', t:'linked'},
  warning:  {c:'var(--warning)',  i:'▲', t:'degraded'},
  critical: {c:'var(--critical)', i:'■', t:'down'},
  unknown:  {c:'var(--ink3)',     i:'·', t:'no data'},
};
function spark(vals, color){
  if(!vals || vals.length < 2) return '<svg></svg>';
  const w=200,h=34,lo=Math.min(...vals),hi=Math.max(...vals),r=(hi-lo)||1;
  const pts=vals.map((v,i)=>[i*(w/(vals.length-1)), h-2-((v-lo)/r)*(h-6)]);
  const d=pts.map((p,i)=>(i?'L':'M')+p[0].toFixed(1)+' '+p[1].toFixed(1)).join(' ');
  const last=pts[pts.length-1];
  return `<svg viewBox="0 0 ${w} ${h}" preserveAspectRatio="none" aria-hidden="true">
    <path d="${d}" fill="none" stroke="${color}" stroke-width="2"
      stroke-linejoin="round" stroke-linecap="round" vector-effect="non-scaling-stroke"/>
    <circle cx="${last[0].toFixed(1)}" cy="${last[1].toFixed(1)}" r="2.5"
      fill="${color}" stroke="var(--panel)" stroke-width="2"/></svg>`;
}
function fmt(v,d=1,suf=''){ return (v===null||v===undefined)?'—':(+v).toFixed(d)+suf; }
async function tick(){
  let s; try{ s = await (await fetch('api',{cache:'no-store'})).json(); }
  catch(e){ document.getElementById('sub').textContent='dashboard cannot reach the log tree'; return; }
  const mode = dark()?'dark':'light';
  const c=s.campaign, run=s.run;
  document.getElementById('sub').innerHTML = c
    ? `<span class="dot" style="background:${s.live?'var(--good)':'var(--ink3)'}"></span>`+
      `${s.live?'running':'idle'} · ${esc(c.name)} · policy <b>${esc(c.policy)}</b> · ${s.now}`
    : 'no campaigns found';
  const pct = c&&c.planned ? Math.round(100*c.done/c.planned) : 0;
  const mins = s.t_elapsed!==null&&s.t_elapsed!==undefined ? (s.t_elapsed/60) : null;
  const total = run&&run.duration_s ? run.duration_s/60 : null;
  document.getElementById('tiles').innerHTML = [
    `<div class="tile"><div class="k">Runs complete</div><div class="v">${c?c.done:0}<span style="font-size:15px;color:var(--ink3)">/${c?c.planned:0}</span></div>
      <div class="m">${c&&c.failed?c.failed+' failed':'no failures'}</div>
      <div class="bar"><i style="width:${pct}%"></i></div></div>`,
    `<div class="tile"><div class="k">Current world</div><div class="v" style="font-size:19px">${run?esc(run.world.replace('sar_','')):'—'}</div>
      <div class="m">${mins!==null?fmt(mins,1)+' / '+(total?fmt(total,0):'?')+' min':'not started'}</div>
      <div class="bar"><i style="width:${mins!==null&&total?Math.min(100,100*mins/total):0}%"></i></div></div>`,
    `<div class="tile"><div class="k">Cycles logged</div><div class="v">${s.cycles}</div>
      <div class="m">${s.actions} actions</div></div>`,
    `<div class="tile"><div class="k">Fast radio</div><div class="v">${fmt(s.fastradio_mb,1)}<span style="font-size:15px;color:var(--ink3)"> MB</span></div>
      <div class="m">10 Hz per robot</div></div>`,
  ].join('');
  const rb=s.roster.map(n=>{
    const r=s.robots[n]; const col=(s.series[n]||{})[mode]||'var(--ink)';
    if(!r) return `<div class="card"><header><span class="swatch" style="background:${col}"></span>
      <span class="name">${esc(n)}</span></header><div class="empty">waiting for first cycle</div></div>`;
    const st=STATUS[r.status]||STATUS.unknown;
    return `<div class="card"><header>
        <span class="swatch" style="background:${col}"></span>
        <span class="name">${esc(n)}</span>
        <span class="pill" style="color:${st.c}">${st.i} ${st.t}</span></header>
      <div class="kv"><span>C2 available</span><b>${fmt(r.avail_pct,1,'%')}</b></div>
      <div class="kv"><span>TCP</span><b>${fmt(r.tcp,2,' Mb/s')}</b></div>
      <div class="kv"><span>Path loss</span><b>${fmt(r.ploss,1,' dB')}</b></div>
      ${spark(r.hist,col)}
      <div class="kv" style="color:var(--ink3)"><span>RSRP dB/RE, last ${r.hist.length} cycles</span></div>
    </div>`;
  }).join('');
  document.getElementById('robots').innerHTML = rb ||
    '<div class="empty">no robots reporting</div>';
  document.getElementById('runs').innerHTML = s.history.length
    ? `<table><thead><tr><th>Campaign</th><th>Policy</th><th>Done</th><th>Failed</th><th>State</th></tr></thead><tbody>`+
      s.history.map(h=>`<tr><td class="n">${esc(h.name.replace('campaign_',''))}</td>
        <td>${esc(h.policy)}</td><td class="n">${h.done}/${h.planned}</td>
        <td>${h.failed||'—'}</td><td>${h.complete?'complete':'in progress'}</td></tr>`).join('')+
      `</tbody></table>`
    : '<div class="empty">none</div>';
  document.getElementById('tail').textContent =
    run && run.tail && run.tail.length ? run.tail.join('\\n') : '—';
}
tick(); setInterval(tick, 5000);
matchMedia('(prefers-color-scheme: dark)').addEventListener('change', tick);
</script>
"""


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        path = self.path.split("?")[0].rstrip("/") or "/"
        if path in ("/", "/index.html"):
            body, ctype = PAGE.encode(), "text/html; charset=utf-8"
        elif path == "/api":
            try:
                body = json.dumps(snapshot()).encode()
            except Exception as e:  # never let one bad file kill the page
                body = json.dumps({"error": str(e)}).encode()
            ctype = "application/json"
        else:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass  # the campaign owns the terminal; stay quiet


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8742)
    ap.add_argument("--host", default="127.0.0.1")
    a = ap.parse_args()
    srv = ThreadingHTTPServer((a.host, a.port), Handler)
    print(f"SAR dashboard -> http://{a.host}:{a.port}  (Ctrl-C to stop)")
    print(f"  reading {LOGS}")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped.")


if __name__ == "__main__":
    main()
