#!/usr/bin/env python3
"""
psbp_placements.py — Sign Placement Mapper (prototype, port 8701)

A single-purpose companion to species_manager.py: pin every published plant
on a satellite map of the park and record whether each spot has a sign.

    python3 psbp_placements.py               # start on port 8701
    python3 psbp_placements.py --port 8702   # custom port
    python3 psbp_placements.py --dry-run     # never write placements.json

Then open http://localhost:8701

Why this exists separately
--------------------------
This is a deliberate prototype. The interaction — how many pins a plant really
needs, whether area names earn their keep, which sign states matter — is not
settled yet, and settling it inside a 10,000-line file is expensive. So the
shape gets worked out here, and folds into species_manager.py later:

    render_placements()          <- UI_HTML below
    handle_api_placements_data   <- api_data()
    handle_api_placements_save   <- api_save()

Nothing here reaches outside data/sources/placements.json.

Saving
------
Edits autosave to the real placements.json via psbp_common.write_json_atomic,
the same writer every other PSBP tool uses. Every write first copies the
current file into data/sources/.placements_backups/ (last 20 kept), so a bad
session is always recoverable — and the file is in git besides.

Concurrent edits are refused rather than merged: the browser sends the file
revision it loaded, and a save against a changed file returns 409 instead of
clobbering. Open one tab at a time.

Requires: internet access for map tiles (Esri World Imagery) and Leaflet.
Everything else is stdlib and local JSON.
"""

import argparse
import datetime
import json
import os
import shutil
import sys
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# ── Repo wiring ────────────────────────────────────────────────────────────
# Prefer the shared module so this tool cannot drift from the rest of PSBP.
try:
    from psbp_common import REPO, write_json_atomic
    COMMON_OK = True
except Exception as _e:                                        # noqa: BLE001
    COMMON_OK = False
    _COMMON_ERR = str(_e)
    REPO = Path(os.environ.get("PSBP_REPO", Path(__file__).resolve().parents[2]))

    def write_json_atomic(path, data):
        """Fallback with identical semantics: temp file + os.replace."""
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
            f.write("\n")
        os.replace(str(tmp), str(p))

SOURCES        = Path(REPO) / "data" / "sources"
PLANT_SIGNAGE  = SOURCES / "plant_signage.json"
PLACEMENTS     = SOURCES / "placements.json"
BACKUP_DIR     = SOURCES / ".placements_backups"
KEEP_BACKUPS   = 20
PORT           = 8701
DRY_RUN        = False

PARK_CENTER = [27.51365, -82.65985]


# ╔══════════════════════════════════════════════════════════════════════════╗
# ║  DATA                                                                    ║
# ╚══════════════════════════════════════════════════════════════════════════╝

def _read(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return default
    except json.JSONDecodeError as e:
        raise RuntimeError(f"{path.name} is not valid JSON: {e}") from e


def file_rev(path=PLACEMENTS):
    """Revision token = mtime. Used to detect edits from another tab."""
    try:
        return round(path.stat().st_mtime, 3)
    except FileNotFoundError:
        return 0.0


def load_species():
    """Published plants only — a sign placement for a draft page is premature."""
    data = _read(PLANT_SIGNAGE, {"species": []})
    return [
        {
            "id": s["id"],
            "c": s.get("common_name") or s["id"],
            "b": s.get("botanical_name") or "",
            "k": s.get("category") or "",
        }
        for s in data.get("species", [])
        if s.get("status") == "html"
    ]


def load_payload():
    """Everything the browser needs in one round trip."""
    species = load_species()
    known = {s["id"] for s in species}
    raw = _read(PLACEMENTS, {"placements": []}).get("placements", [])

    pins, unlinked = [], []
    for p in raw:
        rec = {
            "pid":    p.get("placement_id"),
            "sid":    p.get("subject_id", p.get("species_id")),
            "area":   p.get("area"),
            "lat":    p.get("latitude"),
            "lng":    p.get("longitude"),
            "state":  p.get("sign_state"),
            "legacy": p.get("status"),
            "notes":  p.get("notes"),
            "updated": p.get("updated"),
            # ── schema 3.0 ──────────────────────────────────────────────
            # The whole original row rides along untouched. This tool
            # REWRITES the file on every save, rebuilding each row from this
            # dict — so any field not carried here is silently deleted. That
            # would have quietly erased cultivar, planted_on, last_seen and
            # every future field the first time anyone dragged a pin.
            # Carrying the raw record means new fields survive without this
            # file needing to know they exist.
            "_raw":   p,
        }
        # A placement whose species left the signage file would otherwise be
        # invisible in the UI but still ride along on every save. Surface it.
        (pins if rec["sid"] in known else unlinked).append(
            rec if rec["sid"] in known
            else {**rec, "name": p.get("common_name") or rec["sid"]}
        )

    areas = sorted({p["area"] for p in pins if p.get("area")})
    return {
        "species": species,
        "placements": pins,
        "unlinked": unlinked,
        "areas": areas,
        "rev": file_rev(),
        "center": PARK_CENTER,
        "file": str(PLACEMENTS),
        "dry_run": DRY_RUN,
    }


def backup_current():
    """Copy the live file aside before overwriting it. Keep the last N."""
    if not PLACEMENTS.exists():
        return None
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    dest = BACKUP_DIR / f"placements-{stamp}.json"
    shutil.copy2(PLACEMENTS, dest)
    old = sorted(BACKUP_DIR.glob("placements-*.json"))[:-KEEP_BACKUPS]
    for f in old:
        try:
            f.unlink()
        except OSError:
            pass
    return dest


# ╔══════════════════════════════════════════════════════════════════════════╗
# ║  API                                                                     ║
# ╚══════════════════════════════════════════════════════════════════════════╝

def api_data(_body=None):
    return load_payload()


def api_save(body):
    """Replace the placements list wholesale. The browser holds the truth
    during a session; this is the moment it becomes durable."""
    pins = body.get("placements")
    if not isinstance(pins, list):
        return 400, {"error": "placements must be a list"}

    sent_rev = body.get("rev")
    current = file_rev()
    if sent_rev is not None and current and abs(float(sent_rev) - current) > 0.002:
        return 409, {
            "error": "placements.json changed since this page loaded",
            "rev": current,
            "hint": "Another tab or an editor wrote the file. Reload to pick up "
                    "those changes — this save was not applied.",
        }

    out = []
    for p in pins:
        sid = p.get("sid")
        # Start from the untouched original (schema 3.0 and anything added
        # later), then overwrite only what this tool actually edits.
        # `common_name` is deliberately NOT written: it is looked up from
        # subject_id, after two rows drifted out of step with the species
        # record. See LANDMARKS.md §11.1.
        rec = dict(p.get("_raw") or {})
        rec.pop("species_id", None)
        rec.pop("common_name", None)
        rec.update({
            "placement_id": p.get("pid"),
            "subject_id":   sid,
            "kind":         rec.get("kind") or "species",
            "area":         p.get("area") or None,
            "latitude":     p.get("lat"),
            "longitude":    p.get("lng"),
            "sign_state":   p.get("state") or None,
            "status":       p.get("legacy") or "not_started",
            "notes":        p.get("notes") or None,
            "updated":      p.get("updated"),
        })
        out.append(rec)

    placed = sum(1 for p in out if p["latitude"] is not None)
    doc = {
        "meta": {
            "generated": datetime.date.today().isoformat(),
            "source": "psbp_placements.py",
            "placement_count": len(out),
            "placed_count": placed,
            "schema_version": "2.0",
            "note": "sign_state: has_sign | needs_sign | not_needed | null "
                    "(not yet reviewed). latitude/longitude null = location "
                    "not yet recorded.",
            "updated": datetime.datetime.now().isoformat(timespec="seconds"),
        },
        "placements": out,
    }

    if DRY_RUN:
        return 200, {"ok": True, "dry_run": True, "count": len(out),
                     "rev": current, "saved": doc["meta"]["updated"]}

    backup_current()
    write_json_atomic(PLACEMENTS, doc)
    return 200, {"ok": True, "count": len(out), "placed": placed,
                 "rev": file_rev(), "saved": doc["meta"]["updated"]}


API_ROUTES = {
    "/api/placements":      api_data,
    "/api/placements/save": api_save,
}


# ╔══════════════════════════════════════════════════════════════════════════╗
# ║  UI                                                                      ║
# ║                                                                          ║
# ║  Plain string, NOT an f-string — a map tab is mostly JavaScript, and     ║
# ║  brace-doubling several hundred lines of it is how bugs get in.          ║
# ╚══════════════════════════════════════════════════════════════════════════╝

UI_HTML = r"""<!DOCTYPE html>
<html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>PSBP Sign Placement</title>
<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/leaflet.min.css">
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@400;500;700&family=IBM+Plex+Mono:wght@400;500&display=swap">
<script src="https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/leaflet.js"></script>
<style>
:root{--ink:#0E1A16;--panel:#14231E;--panel2:#1B2F28;--line:#274036;--text:#E6F0EA;--dim:#8CA79C;
--has:#5FD3A0;--need:#F5B33F;--skip:#7E8F89;--new:#63B4E8;--warn:#FF8A7A;
--sans:'Space Grotesk',ui-sans-serif,system-ui,sans-serif;--mono:'IBM Plex Mono',ui-monospace,Menlo,monospace}
*{box-sizing:border-box}
html,body{margin:0;height:100%;overflow:hidden;background:var(--ink);color:var(--text);font-family:var(--sans);font-size:14px}
button{font-family:inherit;color:inherit;cursor:pointer}
#app{display:flex;height:100vh}
#side{width:340px;flex:none;background:var(--panel);border-right:1px solid var(--line);display:flex;flex-direction:column}
.brand{padding:13px 16px 10px;border-bottom:1px solid var(--line)}
.brand h1{margin:0;font-size:15px;font-weight:700}
.brand p{margin:3px 0 0;font-size:11px;color:var(--dim);font-family:var(--mono)}
.bar{height:4px;background:var(--panel2);border-radius:2px;margin-top:9px;overflow:hidden}
.bar i{display:block;height:100%;background:var(--has);width:0;transition:width .3s}
#savepill{margin-top:8px;font-family:var(--mono);font-size:10.5px;color:var(--dim);display:flex;align-items:center;gap:6px}
#savepill b{width:7px;height:7px;border-radius:50%;background:var(--has)}
#savepill.busy b{background:var(--need)}#savepill.err b{background:var(--warn)}
.tools{padding:10px 16px;border-bottom:1px solid var(--line);display:flex;flex-direction:column;gap:8px}
input[type=text],input[type=search]{width:100%;background:var(--ink);border:1px solid var(--line);border-radius:6px;padding:7px 9px;color:var(--text);font:inherit;font-size:13px}
input:focus,button:focus-visible{outline:2px solid var(--new);outline-offset:1px}
.chips{display:flex;flex-wrap:wrap;gap:5px}
.chip{background:var(--ink);border:1px solid var(--line);border-radius:999px;padding:4px 9px;font-size:11px;color:var(--dim)}
.chip[aria-pressed=true]{background:var(--panel2);border-color:var(--has);color:var(--text)}
#banner{display:none;padding:10px 16px;background:rgba(255,138,122,.1);border-bottom:1px solid var(--line);font-size:12px;line-height:1.45}
#banner button{margin-top:7px;background:var(--warn);border:none;border-radius:6px;padding:5px 10px;font-size:11px;font-weight:700;color:#2A0B06}
#list{flex:1;overflow-y:auto}
.row{padding:9px 16px;border-bottom:1px solid rgba(39,64,54,.5);display:flex;gap:9px;align-items:flex-start}
.row:hover{background:var(--panel2)}
.row[aria-selected=true]{background:var(--panel2);box-shadow:inset 3px 0 0 var(--new)}
.row .nm{flex:1;min-width:0}
.row .nm b{display:block;font-weight:500;font-size:13px;line-height:1.25}
.row .nm i{display:block;font-size:11px;color:var(--dim);font-style:italic;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.dots{display:flex;gap:3px;padding-top:3px;flex:none}
.dot{width:7px;height:7px;border-radius:50%}
.d-has{background:var(--has)}.d-need{background:var(--need)}.d-skip{background:var(--skip)}.d-new{background:var(--new)}
.d-un{background:transparent;border:1.5px dashed var(--new);width:8px;height:8px}
.none{font-size:10px;color:var(--dim);font-family:var(--mono);padding-top:3px}
#mapwrap{flex:1;position:relative}
#map{position:absolute;inset:0;background:#0B1512}
.float{position:absolute;z-index:600}
#hud{top:12px;left:56px;right:12px;display:flex;gap:8px;align-items:flex-start;pointer-events:none}
#hud>*{pointer-events:auto}
.card{background:rgba(20,35,30,.94);border:1px solid var(--line);border-radius:8px;backdrop-filter:blur(6px)}
#active{padding:8px 12px;max-width:44%}
#active b{font-size:14px}#active span{display:block;font-size:11px;color:var(--dim);font-style:italic}
.btn{background:var(--panel2);border:1px solid var(--line);border-radius:7px;padding:8px 12px;font-size:12px;font-weight:500}
.btn:hover{border-color:var(--has)}
.btn.on{background:var(--need);border-color:var(--need);color:#2A1B00;font-weight:700}
.btn.ghost{background:rgba(20,35,30,.94)}
#pins{bottom:12px;left:12px;width:364px;max-height:56vh;display:flex;flex-direction:column;overflow:hidden}
#pins h2{margin:0;padding:9px 12px;font-size:12px;letter-spacing:.08em;text-transform:uppercase;color:var(--dim);border-bottom:1px solid var(--line)}
#pinlist{overflow-y:auto}
.pin{padding:9px 12px;border-bottom:1px solid rgba(39,64,54,.6)}
.pin.sel{background:var(--panel2)}
.pin.unplaced{box-shadow:inset 3px 0 0 var(--new)}
.pin .top{display:flex;gap:6px;align-items:center;margin-bottom:6px}
.pin .num{font-family:var(--mono);font-size:11px;color:var(--dim);width:18px}
.pin .top input{flex:1;padding:5px 7px;font-size:12px}
.x{background:none;border:none;color:var(--dim);font-size:15px;line-height:1;padding:2px 5px;border-radius:4px}
.x:hover{color:var(--warn);background:rgba(255,138,122,.12)}
.seg{display:flex;gap:4px}
.seg button{flex:1;background:var(--ink);border:1px solid var(--line);border-radius:6px;padding:5px 4px;font-size:11px;color:var(--dim)}
.seg button[aria-pressed=true].s-has{background:var(--has);border-color:var(--has);color:#052D1D;font-weight:700}
.seg button[aria-pressed=true].s-need{background:var(--need);border-color:var(--need);color:#2A1B00;font-weight:700}
.seg button[aria-pressed=true].s-skip{background:var(--skip);border-color:var(--skip);color:#0E1A16;font-weight:700}
.place{width:100%;margin-top:5px;background:var(--new);border:none;border-radius:6px;padding:6px;font-size:11px;font-weight:700;color:#06222E}
.place.arm{background:var(--need);color:#2A1B00}
.coord{font-family:var(--mono);font-size:10px;color:var(--dim);margin-top:5px;display:flex;justify-content:space-between}
.empty{padding:14px 12px;font-size:12px;color:var(--dim);line-height:1.5}
.mk{width:16px;height:16px;border-radius:50%;border:2px solid rgba(6,16,12,.85);box-shadow:0 0 0 1px rgba(255,255,255,.35)}
.mk.m-has{background:var(--has)}.mk.m-need{background:var(--need)}.mk.m-skip{background:var(--skip)}.mk.m-new{background:var(--new)}
.mk.act{transform:scale(1.45);box-shadow:0 0 0 3px rgba(99,180,232,.5)}
.mk.ghostpin{opacity:.4;width:11px;height:11px}
#legend{bottom:12px;right:12px;padding:9px 12px;font-size:11px;display:flex;flex-direction:column;gap:5px}
#legend div{display:flex;gap:7px;align-items:center;color:var(--dim)}
#toast{position:absolute;bottom:18px;left:50%;transform:translateX(-50%);background:var(--panel2);border:1px solid var(--line);border-radius:7px;padding:8px 14px;font-size:12px;z-index:900;opacity:0;transition:opacity .25s;pointer-events:none}
#toast.show{opacity:1}
#toast .undo{margin-left:12px;background:var(--new);border:none;border-radius:5px;padding:4px 10px;font-size:11px;font-weight:700;color:#06222E}
@media(max-width:900px){#side{width:265px}#pins{width:calc(100% - 24px)}#legend{display:none}}
</style></head><body>
<div id="app">
  <aside id="side">
    <div class="brand">
      <h1>Sign Placement</h1>
      <p id="prog">Loading…</p>
      <div class="bar"><i id="progbar"></i></div>
      <div id="savepill"><b></b><span id="savetxt">Ready</span></div>
    </div>
    <div id="banner"></div>
    <div class="tools">
      <input type="search" id="q" placeholder="Search plant or botanical name" autocomplete="off">
      <div class="chips">
        <button class="chip" data-f="all" aria-pressed="true">All</button>
        <button class="chip" data-f="unmapped" aria-pressed="false">No pins</button>
        <button class="chip" data-f="review" aria-pressed="false">Unreviewed</button>
        <button class="chip" data-f="need" aria-pressed="false">Needs sign</button>
        <button class="chip" data-f="noloc" aria-pressed="false">No coordinates</button>
      </div>
      <div class="chips">
        <button class="chip" id="reload">Reload from file</button>
        <button class="chip" id="exp">Download a copy</button>
        <button class="chip" id="showall" aria-pressed="false">Show all pins</button>
      </div>
    </div>
    <div id="list"></div>
  </aside>
  <div id="mapwrap">
    <div id="map"></div>
    <div class="float" id="hud">
      <div class="card" id="active"><b>Pick a plant</b><span>then drop pins on the map</span></div>
      <button class="btn ghost" id="addbtn">Drop pins&nbsp; A</button>
      <button class="btn ghost" id="zoombtn">Zoom to pins</button>
    </div>
    <div class="float card" id="pins"><h2 id="pinhdr">Locations</h2><div id="pinlist"></div></div>
    <div class="float card" id="legend">
      <div><span class="dot d-has"></span> Sign is installed</div>
      <div><span class="dot d-need"></span> Needs a sign</div>
      <div><span class="dot d-skip"></span> No sign needed</div>
      <div><span class="dot d-new"></span> Not reviewed yet</div>
      <div><span class="dot d-un"></span> No coordinates</div>
    </div>
    <div id="toast"></div>
  </div>
</div>
<datalist id="areas"></datalist>
<script>
const STATES=[{k:'has_sign',label:'Has sign',cls:'has'},{k:'needs_sign',label:'Needs sign',cls:'need'},{k:'not_needed',label:'No sign',cls:'skip'}];
let SPECIES=[],AREAS=[],pins=[],unlinked=[],rev=0,seq=0,CENTER=[27.51365,-82.65985],DRY=false;
let active=null,addMode=false,showAll=false,filter='all',selPin=null,lastDel=null,placing=null;

const esc=s=>String(s??'').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const located=p=>p.lat!=null&&p.lng!=null;
const nameOf=sid=>{const s=SPECIES.find(x=>x.id===sid);return s?s.c:sid;};
const stateCls=s=>s==='has_sign'?'has':s==='needs_sign'?'need':s==='not_needed'?'skip':'new';
const pinsOf=sid=>pins.filter(p=>p.sid===sid);

/* ---------- saving ---------- */
let saveTimer=null,inFlight=false,dirty=false;
function setPill(cls,txt){const p=document.getElementById('savepill');p.className=cls;document.getElementById('savetxt').textContent=txt;}
function save(){ dirty=true; clearTimeout(saveTimer); setPill('busy','Unsaved changes'); saveTimer=setTimeout(flush,700); }
async function flush(){
  if(inFlight){ save(); return; }
  inFlight=true; setPill('busy','Saving…');
  try{
    const r=await fetch('/api/placements/save',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({rev,placements:pins.concat(unlinked)})});
    const d=await r.json();
    if(r.status===409){ setPill('err','Save blocked — file changed'); toast(d.hint||'File changed on disk','Reload',boot); }
    else if(!r.ok){ setPill('err','Save failed'); toast(d.error||'Save failed'); }
    else { rev=d.rev; dirty=false; setPill('', (DRY?'Dry run — not written · ':'Saved ')+new Date().toLocaleTimeString()); }
  }catch(e){ setPill('err','Save failed — is the server running?'); }
  inFlight=false;
}
window.addEventListener('beforeunload',e=>{ if(dirty){ e.preventDefault(); e.returnValue=''; } });

/* ---------- map ---------- */
let map,layer;
function initMap(){
  map=L.map('map',{zoomControl:true,maxZoom:22}).setView(CENTER,18);
  L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}',
    {maxNativeZoom:19,maxZoom:22,attribution:'Imagery &copy; Esri'}).addTo(map);
  layer=L.layerGroup().addTo(map);
  map.on('click',e=>{
    if(placing){
      const t=pins.find(x=>x.pid===placing);
      if(t){ t.lat=+e.latlng.lat.toFixed(7); t.lng=+e.latlng.lng.toFixed(7); t.updated=new Date().toISOString();
             selPin=t.pid; placing=null; save(); render(); toast('Location set for '+(t.area||t.pid)); }
      return;
    }
    if(!active){ toast('Pick a plant from the list first'); return; }
    if(!addMode) return;
    seq++;
    const p={pid:'PLC-'+String(seq).padStart(4,'0'),sid:active,area:lastArea(),
             lat:+e.latlng.lat.toFixed(7),lng:+e.latlng.lng.toFixed(7),
             state:'needs_sign',legacy:'not_started',notes:null,updated:new Date().toISOString()};
    pins.push(p); selPin=p.pid; save(); render(); focusArea(p.pid,true);
  });
}
function lastArea(){ const m=pinsOf(active).filter(p=>p.area); return m.length?m[m.length-1].area:''; }
function icon(p,mine,ghost){
  const c=['mk','m-'+stateCls(p.state)];
  if(mine&&p.pid===selPin) c.push('act');
  if(ghost) c.push('ghostpin');
  return L.divIcon({className:'',html:'<div class="'+c.join(' ')+'"></div>',iconSize:[16,16],iconAnchor:[8,8]});
}
function drawMap(){
  layer.clearLayers();
  pins.forEach(p=>{
    if(!located(p)) return;
    const mine=p.sid===active;
    if(!mine&&!showAll) return;
    const m=L.marker([p.lat,p.lng],{icon:icon(p,mine,!mine),draggable:mine,riseOnHover:true,zIndexOffset:mine?500:0});
    m.on('click',ev=>{ L.DomEvent.stop(ev); if(!mine) selectSpecies(p.sid); selPin=p.pid; render(); });
    m.on('dragend',ev=>{ const ll=ev.target.getLatLng(); p.lat=+ll.lat.toFixed(7); p.lng=+ll.lng.toFixed(7);
                         p.updated=new Date().toISOString(); save(); render(); });
    if(!mine) m.bindTooltip(nameOf(p.sid),{direction:'right',offset:[8,0]});
    m.addTo(layer);
  });
}
function fit(ps){
  ps=ps.filter(located); if(!ps.length) return;
  if(ps.length===1) map.setView([ps[0].lat,ps[0].lng],Math.max(map.getZoom(),19));
  else map.fitBounds(L.latLngBounds(ps.map(p=>[p.lat,p.lng])).pad(.35));
}

/* ---------- species list ---------- */
function matches(s){
  const ps=pinsOf(s.id);
  if(filter==='unmapped') return ps.length===0;
  if(filter==='review')   return ps.some(p=>!p.state);
  if(filter==='need')     return ps.some(p=>p.state==='needs_sign');
  if(filter==='noloc')    return ps.some(p=>!located(p));
  return true;
}
function drawList(){
  const q=document.getElementById('q').value.trim().toLowerCase();
  const el=document.getElementById('list'); el.innerHTML='';
  SPECIES.filter(s=>matches(s)&&(!q||s.c.toLowerCase().includes(q)||(s.b||'').toLowerCase().includes(q)))
    .forEach(s=>{
      const ps=pinsOf(s.id), row=document.createElement('div');
      row.className='row'; row.setAttribute('aria-selected',s.id===active);
      row.innerHTML='<div class="nm"><b>'+esc(s.c)+'</b><i>'+esc(s.b||'')+'</i></div>'+
        (ps.length?'<div class="dots">'+ps.map(p=>'<span class="dot '+(located(p)?'d-'+stateCls(p.state):'d-un')+'"></span>').join('')+'</div>'
                  :'<span class="none">—</span>');
      row.onclick=()=>selectSpecies(s.id);
      el.appendChild(row);
    });
  const done=SPECIES.filter(s=>pinsOf(s.id).some(located)).length;
  document.getElementById('prog').textContent=done+' of '+SPECIES.length+' plants mapped · '+pins.length+' pins';
  document.getElementById('progbar').style.width=(SPECIES.length?done/SPECIES.length*100:0)+'%';
}
function selectSpecies(id){
  active=id; addMode=true; selPin=null; placing=null;
  document.getElementById('addbtn').classList.add('on');
  const s=SPECIES.find(x=>x.id===id);
  document.getElementById('active').innerHTML='<b>'+esc(s.c)+'</b><span>'+esc(s.b||'')+' · '+esc(s.id)+'</span>';
  fit(pinsOf(id)); render();
}

/* ---------- pin panel ---------- */
function setSel(pid){ selPin=pid; document.querySelectorAll('.pin').forEach(n=>n.classList.toggle('sel',n.dataset.pid===pid)); drawMap(); }
function removePin(pid){
  const i=pins.findIndex(x=>x.pid===pid); if(i<0) return;
  lastDel={p:pins[i],i}; pins.splice(i,1); if(selPin===pid) selPin=null;
  save(); render(); toast('Pin deleted','Undo',undoDel);
}
function undoDel(){
  if(!lastDel) return;
  pins.splice(Math.min(lastDel.i,pins.length),0,lastDel.p);
  selPin=lastDel.p.pid; active=lastDel.p.sid; lastDel=null;
  save(); render(); toast('Pin restored');
}
function focusArea(pid,all){ const el=document.querySelector('#pinlist input[data-pid="'+pid+'"]'); if(el){ el.focus(); if(all) el.select(); } }
function drawPins(){
  const box=document.getElementById('pinlist'), prev=document.activeElement;
  const keep=(prev&&prev.dataset&&prev.dataset.pid)?{pid:prev.dataset.pid,at:prev.selectionStart}:null;
  box.innerHTML='';
  if(!active){ document.getElementById('pinhdr').textContent='Locations';
    box.innerHTML='<div class="empty">Choose a plant on the left, then click the map to drop a pin at each spot where it grows. One pin per stand or cluster is plenty.</div>'; return; }
  const ps=pinsOf(active);
  document.getElementById('pinhdr').textContent='Locations · '+ps.length;
  if(!ps.length){ box.innerHTML='<div class="empty">No pins yet. Click anywhere on the map to add one.</div>'; return; }
  ps.forEach((p,i)=>{
    const d=document.createElement('div');
    d.className='pin'+(p.pid===selPin?' sel':'')+(located(p)?'':' unplaced');
    d.dataset.pid=p.pid;
    d.innerHTML='<div class="top"><span class="num">'+(i+1)+'</span>'+
      '<input type="text" list="areas" data-pid="'+esc(p.pid)+'" value="'+esc(p.area||'')+'" placeholder="Name this spot" spellcheck="false">'+
      '<button class="x" title="Delete pin">&times;</button></div>'+
      '<div class="seg">'+STATES.map(s=>'<button class="s-'+s.cls+'" data-s="'+s.k+'" aria-pressed="'+(p.state===s.k)+'">'+s.label+'</button>').join('')+'</div>'+
      (located(p)
        ? '<div class="coord"><span>'+p.lat.toFixed(6)+', '+p.lng.toFixed(6)+'</span><span>'+esc(p.pid)+'</span></div>'
        : '<button class="place'+(placing===p.pid?' arm':'')+'">'+(placing===p.pid?'Now click the map':'Set location on map')+'</button>'+
          '<div class="coord"><span>No coordinates yet</span><span>'+esc(p.pid)+'</span></div>');
    d.onclick=e=>{ if(e.target.closest('input,button')) return; setSel(p.pid); };
    const ai=d.querySelector('input');
    ai.onfocus=()=>setSel(p.pid);
    ai.oninput=e=>{ p.area=e.target.value.trim()||null; p.updated=new Date().toISOString(); save(); };
    d.querySelector('.x').onclick=e=>{ e.stopPropagation(); removePin(p.pid); };
    const pb=d.querySelector('.place');
    if(pb) pb.onclick=e=>{ e.stopPropagation(); placing=placing===p.pid?null:p.pid; selPin=p.pid; drawPins();
                           toast(placing?'Click the map to place this one':'Cancelled'); };
    d.querySelectorAll('.seg button').forEach(b=>b.onclick=e=>{
      e.stopPropagation();
      p.state=p.state===b.dataset.s?null:b.dataset.s;
      p.updated=new Date().toISOString(); selPin=p.pid; save(); drawList(); drawPins(); drawMap();
    });
    box.appendChild(d);
  });
  if(keep){ const el=box.querySelector('input[data-pid="'+keep.pid+'"]'); if(el){ el.focus(); try{ el.setSelectionRange(keep.at,keep.at); }catch(_){} } }
}
function render(){ drawList(); drawPins(); drawMap(); }

/* ---------- unlinked pins ---------- */
function drawBanner(){
  const b=document.getElementById('banner');
  if(!unlinked.length){ b.style.display='none'; return; }
  b.style.display='block';
  b.innerHTML='<b>'+unlinked.length+' pin'+(unlinked.length>1?'s':'')+' reference a species that is no longer in plant_signage.json:</b><br>'+
    unlinked.map(u=>esc(u.name||u.sid)+' <span style="color:var(--dim)">('+esc(u.pid)+')</span>').join('<br>')+
    '<br><button id="dropunlinked">Remove them</button>';
  document.getElementById('dropunlinked').onclick=()=>{ const n=unlinked.length; unlinked=[]; save(); drawBanner(); toast('Removed '+n+' unlinked pin'+(n>1?'s':'')); };
}

/* ---------- controls ---------- */
document.getElementById('q').oninput=drawList;
document.querySelectorAll('.chip[data-f]').forEach(c=>c.onclick=()=>{
  filter=c.dataset.f;
  document.querySelectorAll('.chip[data-f]').forEach(o=>o.setAttribute('aria-pressed',o===c));
  drawList();
});
document.getElementById('addbtn').onclick=()=>{ addMode=!addMode;
  document.getElementById('addbtn').classList.toggle('on',addMode);
  toast(addMode?'Click the map to drop pins':'Drop-pin mode off'); };
document.getElementById('zoombtn').onclick=()=>{ const ps=active?pinsOf(active):pins; ps.filter(located).length?fit(ps):map.setView(CENTER,18); };
document.getElementById('showall').onclick=e=>{ showAll=!showAll; e.target.setAttribute('aria-pressed',showAll); drawMap(); };
document.getElementById('reload').onclick=()=>{ if(dirty&&!confirm('Unsaved changes will be discarded. Reload from placements.json?')) return; boot(); };
document.getElementById('exp').onclick=()=>{
  const a=document.createElement('a');
  a.href=URL.createObjectURL(new Blob([JSON.stringify(pins.concat(unlinked),null,2)],{type:'application/json'}));
  a.download='placements-copy.json'; a.click();
};
document.addEventListener('keydown',e=>{
  if(/input|textarea/i.test(e.target.tagName)) return;
  if(e.key==='a'||e.key==='A') document.getElementById('addbtn').click();
  if(e.key==='Escape'){ addMode=false; placing=null; document.getElementById('addbtn').classList.remove('on'); drawPins(); }
  if(e.key==='Backspace'&&selPin){ e.preventDefault(); removePin(selPin); }
  if((e.metaKey||e.ctrlKey)&&e.key.toLowerCase()==='z'){ e.preventDefault(); undoDel(); }
  if((e.metaKey||e.ctrlKey)&&e.key.toLowerCase()==='s'){ e.preventDefault(); clearTimeout(saveTimer); flush(); }
});
let tt;
function toast(m,label,fn){
  const t=document.getElementById('toast'); t.innerHTML=''; t.appendChild(document.createTextNode(m));
  if(label){ const b=document.createElement('button'); b.className='undo'; b.textContent=label;
             b.onclick=()=>{ t.classList.remove('show'); fn(); }; t.appendChild(b); }
  t.classList.add('show'); t.style.pointerEvents=label?'auto':'none';
  clearTimeout(tt); tt=setTimeout(()=>t.classList.remove('show'),label?6000:2200);
}

/* ---------- boot ---------- */
async function boot(){
  const d=await (await fetch('/api/placements')).json();
  SPECIES=d.species; AREAS=d.areas; pins=d.placements; unlinked=d.unlinked||[];
  rev=d.rev; CENTER=d.center; DRY=d.dry_run; dirty=false;
  seq=pins.concat(unlinked).reduce((m,p)=>Math.max(m,parseInt(String(p.pid||'').replace(/\D/g,''))||0),0);
  document.getElementById('areas').innerHTML=AREAS.map(a=>'<option value="'+esc(a)+'">').join('');
  if(!map) initMap();
  active=null; selPin=null; placing=null;
  document.getElementById('active').innerHTML='<b>Pick a plant</b><span>then drop pins on the map</span>';
  setPill('',DRY?'Dry run — nothing will be written':'Loaded from placements.json');
  drawBanner(); render();
  const n=pins.filter(p=>!located(p)).length;
  if(n) toast(n+' pin'+(n>1?'s':'')+' still need coordinates — see the "No coordinates" filter');
}
boot();
</script></body></html>
"""


# ╔══════════════════════════════════════════════════════════════════════════╗
# ║  SERVER                                                                  ║
# ╚══════════════════════════════════════════════════════════════════════════╝

class Handler(BaseHTTPRequestHandler):

    def do_GET(self):
        path = urlparse(self.path).path.rstrip("/") or "/"
        if path == "/":
            return self._html(200, UI_HTML)
        if path in API_ROUTES:
            try:
                return self._json(200, API_ROUTES[path](None))
            except Exception as e:                             # noqa: BLE001
                return self._json(500, {"error": str(e)})
        self._json(404, {"error": "Not found"})

    def do_POST(self):
        path = urlparse(self.path).path.rstrip("/") or "/"
        if path not in API_ROUTES:
            return self._json(404, {"error": "Not found"})
        try:
            n = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(n)) if n else {}
        except json.JSONDecodeError as e:
            return self._json(400, {"error": f"bad JSON body: {e}"})
        try:
            result = API_ROUTES[path](body)
            status, payload = result if isinstance(result, tuple) else (200, result)
            self._json(status, payload)
        except Exception as e:                                 # noqa: BLE001
            self._json(500, {"error": str(e)})

    def _json(self, status, data):
        body = json.dumps(data, indent=2).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _html(self, status, html):
        body = html.encode()
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        if args and "/api/" not in str(args[0]):
            print(f"  {args[0]}")


def main():
    global PORT, DRY_RUN
    ap = argparse.ArgumentParser(description="PSBP Sign Placement Mapper")
    ap.add_argument("--port", type=int, default=PORT)
    ap.add_argument("--dry-run", action="store_true",
                    help="serve normally but never write placements.json")
    args = ap.parse_args()
    PORT, DRY_RUN = args.port, args.dry_run

    if not PLANT_SIGNAGE.exists():
        print(f"\n  Cannot find {PLANT_SIGNAGE}")
        print("  Put this script beside psbp_common.py, or set PSBP_REPO.\n")
        return 1

    payload = load_payload()
    print(f"\n{'=' * 68}")
    print("  PSBP SIGN PLACEMENT MAPPER" + ("   [DRY RUN]" if DRY_RUN else ""))
    print(f"{'=' * 68}")
    print(f"  repo        {REPO}")
    print(f"  writes      {PLACEMENTS}")
    print(f"  backups     {BACKUP_DIR}  (last {KEEP_BACKUPS})")
    print(f"  shared code {'psbp_common' if COMMON_OK else 'NOT FOUND — using local fallback'}")
    if not COMMON_OK:
        print(f"              ({_COMMON_ERR})")
    print(f"\n  {len(payload['species'])} published plants · "
          f"{len(payload['placements'])} pins · "
          f"{sum(1 for p in payload['placements'] if p['lat'] is None)} awaiting coordinates")
    if payload["unlinked"]:
        print(f"  {len(payload['unlinked'])} pin(s) reference retired species — "
              f"the page will offer to remove them")
    print(f"\n  →  http://localhost:{PORT}\n  Ctrl-C to stop\n{'=' * 68}\n")

    try:
        ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
    except KeyboardInterrupt:
        print("\n  Stopped.\n")
    except OSError as e:
        print(f"\n  Could not start on port {PORT}: {e}")
        print(f"  Another copy may be running. Try --port {PORT + 1}\n")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
