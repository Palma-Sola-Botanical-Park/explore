#!/usr/bin/env python3
"""
media_intake.py — register park media (anything that is not a species photo)
and put it in the R2 media library.
=============================================================================
Double-click "Media intake.command" (Mac) or "Media intake.bat" (Office PC)
next to this file. A page opens at http://localhost:8703.

WHAT IT DOES
    1. Shows every file waiting in the inbox folder

           Mac:      ~/PSBP-media/inbox/
           Windows:  C:\\PSBP\\data\\media\\inbox\\

    2. You answer the questions for each one (what it is, who made it, may the
       public see it, tags, kids). "Register" mints the next PM-000123 id,
       moves the original to

           <media root>/originals/media/PM-000123/original.<ext>

       and writes the record into the registry, which is where things are found:

           data/sources/media_library.json      {meta, items:[…]}

    3. "Upload" makes a web copy and a thumbnail (photos only), sends the files
       to the bucket at

           media/PM-000123/v1/original.<ext>
           media/PM-000123/v1/web.jpg          photos: ~1,600 px, ~300 KB
           media/PM-000123/v1/thumb.jpg        photos: ~500 px, ~40 KB
           media/PM-000123/v1/record.json      the public record, so the
                                               catalog can be rebuilt from R2

       verifies each checksum, and writes the key and the public URL back into
       the registry. A file already in the bucket with the same checksum is
       skipped, so Upload is always safe to press again.

RULES BUILT IN
    - Photos with kids in them are refused. They are private, and the private
      bucket is not built yet. Keep them out of the inbox for now.
    - "No" and "Not sure" are registered but never uploaded (same reason).
      Only public = yes reaches the bucket.
    - Unknown stays unknown. Nothing is guessed from a filename.
    - Never a name, event or date in a path. The id is the only path part.
    - Every record here is collection = park: the curated set the website,
      screens and signs draw from. Bev's link area (collection = bev, prefix
      bev/) is a separate, simpler page, not built yet.
    - Never deletes anything, here or in the bucket. Never overwrites a file
      in the bucket that already matches.
    - The registry is re-read from disk before every write.

WHAT IT NEEDS
    R2_ACCOUNT_ID, R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY — either set in the
    environment (Windows: Environment Variables) or, easier on the Mac, in a
    file OUTSIDE the repo:

           <media root>/r2.env
           R2_ACCOUNT_ID=…
           R2_ACCESS_KEY_ID=…
           R2_SECRET_ACCESS_KEY=…

    Never in the repo, never in a page. Pillow for resizing photos; on a Mac
    without Pillow the built-in `sips` is used instead.

USAGE
    python3 data/scripts/media_intake.py                     # sandbox bucket
    python3 data/scripts/media_intake.py --bucket psbp-public   # production, typed on purpose
    python3 data/scripts/media_intake.py --no-open           # don't open the browser
"""
import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import webbrowser
from datetime import datetime, timezone
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlparse, parse_qs, quote, unquote

sys.path.insert(0, str(Path(__file__).resolve().parent))
from psbp_common import REPO, SOURCES, load_json, write_json_atomic  # noqa: E402
from upload_r2_media import (  # noqa: E402
    R2, make_rendition, md5_of, WEB_PX, WEB_BUDGET, THUMB_PX, THUMB_BUDGET, DEFAULT_BUCKET)

PORT = 8703
REVISION = "v1"
GAP_S = 1.0
REGISTRY = SOURCES / "media_library.json"
MEDIA_ROOT = (Path(r"C:\PSBP\data\media") if os.name == "nt"
              else Path.home() / "PSBP-media")
OFFICE_LOGS = Path(r"C:\PSBP\logs")

# Public address of each bucket. Only the sandbox has one today; psbp-public
# gets media.palmasolabp.org after the DNS move, and goes in here then.
PUBLIC_BASE = {
    "psbp-sandbox": "https://pub-895c4e39efa04b698caca4bce36ba281.r2.dev",
}

KINDS = {
    "photo":    {"jpg", "jpeg", "png", "heic", "heif", "tif", "tiff", "webp", "gif"},
    "image":    {"svg"},
    "document": {"pdf", "docx", "doc", "txt", "md"},
    "video":    {"mp4", "mov", "m4v"},
    "audio":    {"mp3", "m4a", "wav", "aac"},
}
CONTENT_TYPES = {
    "jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png", "gif": "image/gif",
    "webp": "image/webp", "heic": "image/heic", "heif": "image/heif", "tif": "image/tiff",
    "tiff": "image/tiff", "svg": "image/svg+xml", "pdf": "application/pdf",
    "mp4": "video/mp4", "mov": "video/quicktime", "m4v": "video/x-m4v",
    "mp3": "audio/mpeg", "m4a": "audio/mp4", "wav": "audio/wav", "aac": "audio/aac",
    "txt": "text/plain", "md": "text/markdown", "json": "application/json",
}
PUBLIC_FIELDS = ("media_id", "kind", "collection", "title", "made_by", "credit_line", "public",
                 "kids", "tags", "date", "at_the_park", "species", "caption",
                 "files", "used_on", "updated")

_lock = threading.Lock()
_log_f = None
BUCKET = DEFAULT_BUCKET


def now_z():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%MZ")


def log(msg):
    line = f"{datetime.now():%Y-%m-%d %H:%M:%S}  {msg}"
    print(line, flush=True)
    if _log_f:
        _log_f.write(line + "\n")
        _log_f.flush()


def kind_of(ext):
    for k, exts in KINDS.items():
        if ext in exts:
            return k
    return "file"


def sha256_of(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# ── credentials ────────────────────────────────────────────────────────────

def r2_credentials():
    """Three values from the environment, else from <media root>/r2.env.
    Returns (dict, where) or (None, what is missing)."""
    keys = ("R2_ACCOUNT_ID", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY")
    env = {k: os.environ.get(k, "").strip() for k in keys}
    where = "environment"
    if not all(env.values()):
        env_file = MEDIA_ROOT / "r2.env"
        if env_file.is_file():
            where = str(env_file)
            for line in env_file.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if "=" in line and not line.startswith("#"):
                    k, v = line.split("=", 1)
                    if k.strip() in keys and not env[k.strip()]:
                        env[k.strip()] = v.strip().strip('"').strip("'")
    missing = [k for k in keys if not env[k]]
    if missing:
        return None, "missing " + ", ".join(missing) + f" (set them, or put them in {MEDIA_ROOT / 'r2.env'})"
    return env, where


# ── registry ───────────────────────────────────────────────────────────────

def load_registry():
    """Always from disk: Randy may have edited it meanwhile (rule 4)."""
    reg = load_json(REGISTRY, None)
    if not reg:
        reg = {"meta": {}, "items": []}
    reg.setdefault("meta", {})
    reg.setdefault("items", [])
    return reg


def save_registry(reg):
    reg["meta"].update({
        "_note": "The park media library: everything that is not a species photo (those stay in "
                 "photo_credits.json). One record per item, written by media_intake.py. "
                 "media_id is permanent and minted by the intake page, never typed. "
                 "collection is park (the website, screens and signs; Randy's) or bev "
                 "(her link area, never referenced by a page). The key prefix follows it. "
                 "files[].key is the permanent address in the bucket; url is rebuilt from it "
                 "on every upload. Kids and anything not public never reach the public bucket.",
        "updated": now_z(),
        "count": len(reg["items"]),
    })
    reg["items"].sort(key=lambda r: r["media_id"])
    write_json_atomic(REGISTRY, reg)


def mint_id(reg):
    nums = [int(m.group(1)) for r in reg["items"]
            if (m := re.fullmatch(r"PM-(\d{6})", r.get("media_id", "")))]
    return f"PM-{(max(nums) + 1) if nums else 1:06d}"


def update_item(media_id, changes):
    """Re-read, change one record, write. Returns the record."""
    with _lock:
        reg = load_registry()
        for r in reg["items"]:
            if r["media_id"] == media_id:
                r.update(changes)
                r["updated"] = now_z()
                save_registry(reg)
                return r
    raise KeyError(media_id)


def original_of(rec):
    d = MEDIA_ROOT / "originals" / "media" / rec["media_id"]
    hits = sorted(d.glob("original.*")) if d.is_dir() else []
    return hits[0] if hits else None


# ── register ───────────────────────────────────────────────────────────────

def register(form):
    """Mint an id, move the inbox file to originals, write the record."""
    name = os.path.basename(form.get("filename", ""))
    src = MEDIA_ROOT / "inbox" / name
    if not name or not src.is_file():
        raise ValueError(f"not in the inbox: {name}")
    if form.get("kids") in ("yes", "true", "on", True):
        raise ValueError("Kids in the photo: that is private, and the private bucket is not "
                         "built yet. Take it out of the inbox for now; it was not registered.")
    title = (form.get("title") or "").strip()
    if not title:
        raise ValueError("A title is needed. What is it?")
    ext = src.suffix.lower().lstrip(".")
    tags = [t.strip() for t in (form.get("tags") or "").split(",") if t.strip()]
    species = [s.strip().upper() for s in (form.get("species") or "").split(",") if s.strip()]
    bad = [s for s in species if not re.fullmatch(r"PSBP-\d{5}", s)]
    if bad:
        raise ValueError("Species must be PSBP ids (PSBP-00004), not names: " + ", ".join(bad))

    def opt(k):
        v = (form.get(k) or "").strip()
        return v or None

    with _lock:
        reg = load_registry()
        media_id = mint_id(reg)
        dest_dir = MEDIA_ROOT / "originals" / "media" / media_id
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / f"original.{ext}"
        digest = sha256_of(src)
        shutil.move(str(src), str(dest))
        rec = {
            "media_id": media_id,
            "kind": kind_of(ext),
            "collection": "park",
            "title": title,
            "made_by": opt("made_by"),
            "credit_line": opt("credit_line"),
            "public": form.get("public") if form.get("public") in ("yes", "no", "not_sure") else "not_sure",
            "kids": False,
            "tags": tags,
            "date": opt("date"),
            "at_the_park": form.get("at_the_park") if form.get("at_the_park") in ("yes", "no") else "unknown",
            "species": species,
            "caption": opt("caption"),
            "source": {"type": opt("dropped_by") or "randy", "original_filename": name,
                       "sha256": digest, "bytes": dest.stat().st_size},
            "files": [],
            "bucket": None,
            "url": None,
            "state": "waiting",
            "used_on": [],
            "registered": now_z(),
            "updated": now_z(),
        }
        if rec["public"] != "yes":
            rec["state"] = "private"
        reg["items"].append(rec)
        save_registry(reg)
    log(f"registered {media_id}  {name}  \"{title}\"  public={rec['public']}")
    return rec


# ── renditions ─────────────────────────────────────────────────────────────

def _sips_rendition(src, dst, longest_px, budget):
    """Mac fallback when Pillow is not installed: the same stepping-down
    quality loop shrink.sh uses."""
    if dst.exists() and dst.stat().st_size > 0:
        return dst.stat().st_size, False
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_suffix(".part.jpg")
    for q in (85, 80, 75, 70, 65, 60):
        subprocess.run(["sips", "-Z", str(longest_px), "-s", "format", "jpeg",
                        "-s", "formatOptions", str(q), str(src), "--out", str(tmp)],
                       check=True, capture_output=True)
        if tmp.stat().st_size <= budget:
            break
    os.replace(tmp, dst)
    return dst.stat().st_size, True


def rendition(src, dst, longest_px, budget):
    try:
        import PIL  # noqa: F401
        return make_rendition(src, dst, longest_px, budget)
    except ImportError:
        if shutil.which("sips"):
            return _sips_rendition(src, dst, longest_px, budget)
        raise RuntimeError("no Pillow and no sips: cannot resize photos on this machine")


# ── upload ─────────────────────────────────────────────────────────────────

def upload_item(rec, r2, lines):
    """Send one registered item's files to the bucket and record where they
    went. Returns the changes written to the record."""
    mid = rec["media_id"]
    src = original_of(rec)
    if not src:
        raise RuntimeError(f"original file missing under {MEDIA_ROOT / 'originals' / 'media' / mid}")
    ext = src.suffix.lower().lstrip(".")
    files = [("original", src, CONTENT_TYPES.get(ext, "application/octet-stream"))]
    if rec["kind"] == "photo":
        out = MEDIA_ROOT / "derived" / "media" / mid / REVISION
        try:
            rendition(src, out / "web.jpg", WEB_PX, WEB_BUDGET)
            rendition(src, out / "thumb.jpg", THUMB_PX, THUMB_BUDGET)
            files += [("web", out / "web.jpg", "image/jpeg"), ("thumb", out / "thumb.jpg", "image/jpeg")]
        except Exception as e:
            lines.append(f"  {mid}: could not make web/thumb copies ({e}); original only")

    base = PUBLIC_BASE.get(r2.bucket)
    done = {f["role"]: f for f in rec.get("files", [])}
    last = 0.0
    for role, path, ctype in files:
        key = f"media/{mid}/{REVISION}/{role}.{ext if role == 'original' else 'jpg'}"
        data = path.read_bytes()
        local_md5 = md5_of(data)
        wait = GAP_S - (time.time() - last)
        if wait > 0:
            time.sleep(wait)
        last = time.time()
        have = r2.head(key)
        if have and have[1] == local_md5:
            status = "already up"
        elif have:
            raise RuntimeError(f"{key} is already in the bucket with different content; "
                               "not overwriting (write a new revision instead)")
        else:
            etag = r2.put(key, data, ctype)
            if etag != local_md5:
                raise RuntimeError(f"checksum mismatch after upload of {key}")
            status = "uploaded"
        prev = done.get(role, {})
        done[role] = {"role": role, "key": key, "bytes": len(data),
                      "sha256": hashlib.sha256(data).hexdigest(), "md5": local_md5,
                      "url": f"{base}/{key}" if base else None,
                      "uploaded_at": prev.get("uploaded_at") if status == "already up" and prev
                      else now_z()}
        lines.append(f"  {mid} {role:<9} {len(data) // 1024:>6} KB  {status}")

    ordered = [done[r] for r in ("original", "web", "thumb") if r in done]
    changes = {"files": ordered, "bucket": r2.bucket, "state": "done",
               "url": (done.get("web") or done["original"])["url"]}
    # The public record, next to the files, so the catalog can be rebuilt from R2.
    public = {k: rec.get(k) for k in PUBLIC_FIELDS}
    public["files"] = ordered
    public["updated"] = now_z()
    body = json.dumps(public, indent=1, ensure_ascii=False).encode("utf-8")
    time.sleep(max(0.0, GAP_S - (time.time() - last)))
    r2.put(f"media/{mid}/{REVISION}/record.json", body, "application/json")
    lines.append(f"  {mid} record.json  written")
    return changes


def upload(only_id=None):
    lines = []
    creds, where = r2_credentials()
    if not creds:
        lines.append("Cannot upload: " + where)
        return lines
    r2 = R2(creds["R2_ACCOUNT_ID"], creds["R2_ACCESS_KEY_ID"], creds["R2_SECRET_ACCESS_KEY"], BUCKET)
    todo = [r for r in load_registry()["items"]
            if (only_id is None or r["media_id"] == only_id)]
    if only_id is None:
        todo = [r for r in todo if r["state"] in ("waiting", "problem")]
    lines.append(f"{len(todo)} item(s) to send to bucket {BUCKET} (keys from {where})")
    sent = failed = 0
    for rec in todo:
        if rec["public"] != "yes":
            lines.append(f"  {rec['media_id']}: public = {rec['public']}, stays off the public bucket")
            continue
        try:
            changes = upload_item(rec, r2, lines)
            update_item(rec["media_id"], changes)
            sent += 1
        except Exception as e:
            failed += 1
            update_item(rec["media_id"], {"state": "problem", "problem": str(e)})
            lines.append(f"  {rec['media_id']} FAILED: {e}")
    lines.append(f"Done. {sent} item(s) in the bucket, {failed} problem(s)."
                 + (" Press Upload again to retry; files already up are skipped." if failed else ""))
    for ln in lines:
        log(ln.strip())
    return lines


# ── the page ───────────────────────────────────────────────────────────────

def h(s):
    return (str(s) if s is not None else "").replace("&", "&amp;").replace("<", "&lt;") \
        .replace(">", "&gt;").replace('"', "&quot;")


CSS = """
:root{--green-deep:#1a3a1f;--green-mid:#2d6a35;--gold:#c5922a;--cream:#faf8f3;--t-base:17px}
*{box-sizing:border-box}body{margin:0;background:var(--cream);color:#222;
font:var(--t-base)/1.45 "Source Sans 3","Source Sans Pro",system-ui,sans-serif}
header{background:var(--green-deep);color:#fff;padding:14px 20px}
header h1{margin:0;font:600 26px "Playfair Display",Georgia,serif}
header p{margin:4px 0 0;opacity:.85}
main{max-width:1200px;margin:0 auto;padding:16px 20px 60px}
h2{font:600 22px "Playfair Display",Georgia,serif;color:var(--green-deep);margin:28px 0 10px}
.card{background:#fff;border:1px solid #ddd;border-radius:8px;padding:14px;margin:0 0 14px;
display:grid;grid-template-columns:260px 1fr;gap:16px}
.card .pic{width:260px;height:200px;background:#eee;border-radius:6px;display:flex;
align-items:center;justify-content:center;overflow:hidden;color:#666}
.card .pic img{max-width:100%;max-height:100%}
.fields{display:grid;grid-template-columns:1fr 1fr;gap:8px 14px}
.fields label{display:flex;flex-direction:column;gap:2px}
.fields label.wide{grid-column:1/-1}
.fields small{color:#555}
input,select,textarea{font:inherit;padding:6px 8px;border:1px solid #bbb;border-radius:5px;width:100%}
button{font:inherit;font-weight:600;padding:8px 16px;border:0;border-radius:6px;cursor:pointer;
background:var(--green-mid);color:#fff}
button.gold{background:var(--gold);color:#1a1a1a}
.row{display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin-top:8px}
.msg{padding:8px 10px;border-radius:6px;background:#fff6dd;border:1px solid var(--gold);display:none}
.state{display:inline-block;padding:2px 8px;border-radius:12px;background:#eee;font-weight:600}
.state.done{background:#dff3e0;color:var(--green-deep)}.state.problem{background:#ffe0e0;color:#7a1a1a}
.state.private{background:#eee}.state.waiting{background:#fff2cc}
table{width:100%;border-collapse:collapse;background:#fff}
td,th{padding:8px;border-bottom:1px solid #e5e5e5;vertical-align:top;text-align:left}
td img{width:90px;height:70px;object-fit:cover;border-radius:4px;background:#eee}
pre{background:#fff;border:1px solid #ddd;border-radius:6px;padding:12px;white-space:pre-wrap;
font-size:var(--t-base)}
code{font-size:var(--t-base)}
.setup{background:#fff;border:1px solid #ddd;border-radius:8px;padding:10px 14px}
.setup div{margin:2px 0}
"""

JS = """
async function post(url, body){
  const r = await fetch(url,{method:'POST',headers:{'Content-Type':'application/json'},
                             body:JSON.stringify(body||{})});
  return r.json();
}
async function register(form){
  const btn = form.querySelector('button[type=submit]');
  if(btn.disabled) return;            // already sending
  btn.disabled = true; btn.textContent = 'Registering…';
  const data = Object.fromEntries(new FormData(form).entries());
  data.kids = form.querySelector('[name=kids]').checked ? 'yes' : 'no';
  const msg = form.querySelector('.msg');
  const res = await post('/register', data);
  if(res.error){
    msg.textContent = res.error; msg.style.display='block';
    btn.disabled = false; btn.textContent = 'Register'; return;
  }
  location.reload();
}
async function upload(id){
  const out = document.getElementById('log');
  out.textContent = 'Working… (about a second per file, do not close this page)';
  const res = await post('/upload', id ? {id:id} : {});
  out.textContent = (res.lines||[res.error]).join('\\n');
  setTimeout(()=>location.reload(), 2500);
}
"""


def render_page():
    inbox = MEDIA_ROOT / "inbox"
    inbox.mkdir(parents=True, exist_ok=True)
    waiting = sorted(p for p in inbox.iterdir()
                     if p.is_file() and not p.name.startswith(".") and p.suffix)
    reg = load_registry()
    creds, where = r2_credentials()
    base = PUBLIC_BASE.get(BUCKET)

    out = [f"<!doctype html><html><head><meta charset='utf-8'><title>PSBP Media intake</title>"
           f"<style>{CSS}</style></head><body><header><h1>Media intake</h1>"
           f"<p>Register park media, then send it to the R2 library. Bucket: <b>{h(BUCKET)}</b>"
           f"{'' if BUCKET == DEFAULT_BUCKET else ' (PRODUCTION)'}</p></header><main>"]
    out.append("<div class='setup'>"
               f"<div>Inbox folder: <code>{h(inbox)}</code> — drop files here, then reload.</div>"
               f"<div>Registry: <code>{h(REGISTRY)}</code> ({len(reg['items'])} items)</div>"
               f"<div>R2 keys: {'found in ' + h(where) if creds else '<b>' + h(where) + '</b>'}</div>"
               f"<div>Public address: {h(base) if base else 'none for this bucket yet'}</div></div>")

    out.append(f"<h2>In the inbox ({len(waiting)})</h2>")
    if not waiting:
        out.append("<p>Nothing waiting. Drop files in the inbox folder and reload.</p>")
    for p in waiting:
        ext = p.suffix.lower().lstrip(".")
        kind = kind_of(ext)
        pic = (f"<img src='/inbox-file/{quote(p.name)}' alt=''>" if kind in ("photo", "image") and ext != "heic"
               else f"<span>{h(kind)} · .{h(ext)}</span>")
        out.append(f"""
<form class='card' onsubmit='event.preventDefault(); register(this)'>
  <div><div class='pic'>{pic}</div><div style='margin-top:6px'><b>{h(p.name)}</b><br>
  {p.stat().st_size // 1024} KB · {h(kind)}</div></div>
  <div>
  <input type='hidden' name='filename' value='{h(p.name)}'>
  <div class='fields'>
    <label class='wide'>What is it? <small>a plain title</small><input name='title' required></label>
    <label>Who took or made it?<input name='made_by' placeholder='unknown is fine'></label>
    <label>Credit line <small>as it should appear</small><input name='credit_line'></label>
    <label>May the public see it?<select name='public'>
      <option value='yes'>Yes</option><option value='not_sure' selected>Not sure</option><option value='no'>No</option></select></label>
    <label>Taken at the park?<select name='at_the_park'>
      <option value='unknown' selected>Unknown</option><option value='yes'>Yes</option><option value='no'>No</option></select></label>
    <label>Tags <small>comma separated: event, wedding, sign, map, nursery…</small><input name='tags'></label>
    <label>Date <small>if known, YYYY-MM-DD</small><input name='date'></label>
    <label>Species in it <small>PSBP ids, comma separated</small><input name='species'></label>
    <label>Dropped by<select name='dropped_by'><option value='randy'>Randy</option><option value='bev'>Bev</option><option value='other'>Other</option></select></label>
    <label class='wide'>Caption <small>optional</small><input name='caption'></label>
    <label class='wide' style='flex-direction:row;gap:8px;align-items:center'>
      <input type='checkbox' name='kids' style='width:auto'> Are there kids in it? <small>(then it is private and stays out for now)</small></label>
  </div>
  <div class='row'><button type='submit'>Register</button><div class='msg'></div></div>
  </div>
</form>""")

    items = reg["items"]
    n_wait = sum(1 for r in items if r["state"] in ("waiting", "problem") and r["public"] == "yes")
    out.append(f"<h2>Registered ({len(items)})</h2>")
    out.append(f"<div class='row'><button class='gold' onclick='upload()'>Upload everything waiting ({n_wait})</button>"
               "<span>Safe to press again: files already in the bucket are skipped.</span></div>")
    out.append("<pre id='log' style='margin-top:10px'>Upload log appears here.</pre>")
    if items:
        out.append("<table><tr><th></th><th>Id</th><th>Title</th><th>Public</th><th>State</th><th>Where</th><th></th></tr>")
        for r in sorted(items, key=lambda x: x["media_id"], reverse=True):
            src = original_of(r)
            pic = (f"<img src='/orig/{r['media_id']}' alt=''>"
                   if src and r["kind"] in ("photo", "image") and src.suffix.lower() != ".heic" else h(r["kind"]))
            where = (f"<a href='{h(r['url'])}' target='_blank'>{h(r['files'][0]['key'].rsplit('/', 1)[0])}/</a>"
                     if r.get("url") else (h(r["files"][0]["key"].rsplit('/', 1)[0]) if r.get("files") else "—"))
            prob = f"<br><small>{h(r.get('problem'))}</small>" if r["state"] == "problem" else ""
            btn = (f"<button onclick=\"upload('{r['media_id']}')\">Upload</button>"
                   if r["public"] == "yes" else "")
            out.append(f"<tr><td>{pic}</td><td><code>{r['media_id']}</code></td>"
                       f"<td><b>{h(r['title'])}</b><br><small>{h(r['source']['original_filename'])}"
                       f"{' · ' + h(', '.join(r['tags'])) if r['tags'] else ''}</small></td>"
                       f"<td>{h(r['public'])}</td><td><span class='state {h(r['state'])}'>{h(r['state'])}</span>{prob}</td>"
                       f"<td>{where}</td><td>{btn}</td></tr>")
        out.append("</table>")
    out.append(f"</main><script>{JS}</script></body></html>")
    return "".join(out)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass

    def _send(self, code, body, ctype="text/html; charset=utf-8"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _file(self, path):
        if not path or not path.is_file():
            self._send(404, b"not found", "text/plain")
            return
        ctype = CONTENT_TYPES.get(path.suffix.lower().lstrip("."), "application/octet-stream")
        self._send(200, path.read_bytes(), ctype)

    def do_GET(self):
        path = urlparse(self.path).path
        if path.startswith("/inbox-file/"):
            name = os.path.basename(unquote(path[len("/inbox-file/"):]))
            self._file(MEDIA_ROOT / "inbox" / name)
        elif path.startswith("/orig/"):
            mid = os.path.basename(path[len("/orig/"):])
            self._file(original_of({"media_id": mid}) if re.fullmatch(r"PM-\d{6}", mid) else None)
        elif path in ("/", "/index.html"):
            self._send(200, render_page().encode("utf-8"))
        else:
            self._send(404, b"not found", "text/plain")

    def do_POST(self):
        path = urlparse(self.path).path
        n = int(self.headers.get("Content-Length", 0))
        try:
            body = json.loads(self.rfile.read(n) or b"{}")
        except json.JSONDecodeError:
            body = {}
        try:
            if path == "/register":
                rec = register(body)
                res = {"ok": True, "media_id": rec["media_id"]}
            elif path == "/upload":
                res = {"lines": upload(body.get("id") or None)}
            else:
                res = {"error": "unknown route"}
        except Exception as e:
            res = {"error": str(e)}
        self._send(200, json.dumps(res).encode("utf-8"), "application/json")


def main():
    global BUCKET, _log_f
    ap = argparse.ArgumentParser()
    ap.add_argument("--bucket", default=DEFAULT_BUCKET)
    ap.add_argument("--port", type=int, default=PORT)
    ap.add_argument("--no-open", action="store_true")
    args = ap.parse_args()
    BUCKET = args.bucket

    for d in ("inbox", "originals/media", "derived/media"):
        (MEDIA_ROOT / d).mkdir(parents=True, exist_ok=True)
    logs = OFFICE_LOGS if os.name == "nt" and OFFICE_LOGS.is_dir() else MEDIA_ROOT / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    _log_f = open(logs / f"media_intake-{datetime.now():%Y-%m-%d}.log", "a", encoding="utf-8")
    if not REGISTRY.is_file():
        with _lock:
            save_registry({"meta": {}, "items": []})

    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    url = f"http://localhost:{args.port}/"
    print("╔══════════════════════════════════════════════════╗")
    print("║  PSBP Media intake                               ║")
    print(f"║  {url:<48}║")
    print("╚══════════════════════════════════════════════════╝")
    print(f"  Inbox:    {MEDIA_ROOT / 'inbox'}")
    print(f"  Registry: {REGISTRY}")
    print(f"  Bucket:   {BUCKET}{'' if BUCKET == DEFAULT_BUCKET else '   (PRODUCTION)'}")
    print("  Close this window to stop.\n")
    log(f"started on {url} bucket={BUCKET} root={MEDIA_ROOT}")
    if not args.no_open:
        threading.Timer(0.8, webbrowser.open, [url]).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.server_close()
    _log_f.close()


if __name__ == "__main__":
    main()
