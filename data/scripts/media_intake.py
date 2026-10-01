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

       "Suggest" (or "Suggest all") first sends a small copy of each photo to
       Claude, which proposes the title, tags, caption and a kids flag, and the
       date comes off the camera data when it is there. Who made it, the credit
       line, the license and whether it is public are never guessed: set them
       once in "Batch defaults" and every form on the page starts with them.
       Suggestions are highlighted until you register; change anything.

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
    - A species page may place an item with a page block {"media": "PM-000123",
      "caption": "..."} once it is public, uploaded, credited and licensed
      (psbp_common.media_page_check). Put the species ids in "Species in it"
      so the item turns up when that page is drafted.
    - Every record here is collection = park: the curated set the website,
      screens and signs draw from. Bev's link area (collection = bev, prefix
      bev/) is a separate, simpler page, not built yet.
    - Never deletes anything, here or in the bucket. Never overwrites a file
      in the bucket that already matches.
    - The registry is re-read from disk before every write.

WHAT IT NEEDS
    R2_ACCOUNT_ID, R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY for the bucket and
    ANTHROPIC_API_KEY for Suggest — either set in the environment (Windows:
    Environment Variables) or, easier on the Mac, in a file OUTSIDE the repo:

           <media root>/r2.env
           R2_ACCOUNT_ID=…
           R2_ACCESS_KEY_ID=…
           R2_SECRET_ACCESS_KEY=…
           ANTHROPIC_API_KEY=…

    Never in the repo, never in a page. Pillow for resizing photos; on a Mac
    without Pillow the built-in `sips` is used instead.

USAGE
    python3 data/scripts/media_intake.py                     # sandbox bucket
    python3 data/scripts/media_intake.py --bucket psbp-public   # production, typed on purpose
    python3 data/scripts/media_intake.py --no-open           # don't open the browser
"""
import argparse
from collections import Counter
import base64
import hashlib
import json
import os
import re
import shutil
import struct
import subprocess
import sys
import threading
import time
import webbrowser
from datetime import datetime, timezone
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from pathlib import Path
import urllib.error
import urllib.request
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

# Suggest: one Messages API call per photo, standard library only, the same
# shape Species Manager uses. The model only proposes what can be SEEN:
# title, tags, caption, whether anyone looks under 18. Never a name, a date,
# a license or a public decision (CLAUDE.md §7: never guess a photographer, a
# date, a place, or a license).
ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_VERSION = "2023-06-01"
AI_MODEL = "claude-opus-5-5"
SUGGEST_PX, SUGGEST_BUDGET = 1024, 400 * 1024
TAG_VOCAB = ("wedding", "ceremony", "reception", "event", "dinner", "plant sale", "class",
             "volunteers", "students", "nursery", "galleria", "pavilion", "office", "pond",
             "path", "garden", "butterfly garden", "serenity garden", "plumeria", "rare fruit",
             "sign", "map", "flyer", "aerial", "landscape", "bird", "wildlife", "flower", "tree",
             "sunset", "holiday lights", "people", "crowd")
SUGGEST_SYSTEM = (
    "You catalog photographs for Palma Sola Botanical Park, a free, volunteer-run, ten-acre "
    "botanical park in Bradenton, Florida: ponds, palms, a galleria and pavilion used for "
    "weddings and events, an office, a nursery and plant sales, a butterfly garden, a serenity "
    "garden, Bright Futures student volunteers on Wednesdays. Describe only what is visible. "
    "Never guess a person's name, the photographer, the date, or whether the photo may be "
    "published. Title: a plain phrase of at most ten words, what the photo shows, no "
    "marketing language. Tags: a few from this list where they fit, lowercase, plus at most two "
    "of your own: " + ", ".join(TAG_VOCAB) + ". Caption: one factual sentence a visitor "
    "could read under the photo, no names. kids: 'yes' if anyone clearly looks under 18, "
    "'unsure' if someone might, else 'no'. people: 'none', 'few' or 'crowd'.")
SUGGEST_SCHEMA = {"type": "object", "additionalProperties": False,
                  "required": ["title", "tags", "caption", "kids", "people"],
                  "properties": {"title": {"type": "string"},
                                 "tags": {"type": "array", "items": {"type": "string"}},
                                 "caption": {"type": "string"},
                                 "kids": {"type": "string", "enum": ["yes", "unsure", "no"]},
                                 "people": {"type": "string", "enum": ["none", "few", "crowd"]}}}

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
# License choices. A species page may use the item only with one of these
# (psbp_common.MEDIA_PAGE_LICENSES); ordinary park media may leave it blank.
LICENSES = ("permission", "cc-by", "cc-by-nc", "cc-by-sa", "cc-by-nc-sa", "cc-by-nd", "cc-by-nc-nd", "cc0")
LICENSE_CHOICES = (("permission", "Given to the park, with permission to show it"), ("cc-by", "CC BY"),
                   ("cc-by-nc", "CC BY-NC"), ("cc-by-sa", "CC BY-SA"), ("cc-by-nc-sa", "CC BY-NC-SA"),
                   ("cc-by-nd", "CC BY-ND"), ("cc-by-nc-nd", "CC BY-NC-ND"), ("cc0", "CC0 (public domain)"))
PUBLIC_FIELDS = ("media_id", "kind", "collection", "title", "made_by", "credit_line", "license", "public",
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


# ── look-alikes ────────────────────────────────────────────────────────────
# The same picture saved twice under two names, or once full size and once
# shrunk, has different bytes, so the exact check misses it. A difference hash
# does not: the photo is reduced to a 17 x 16 grey grid and each cell is
# compared with its neighbour, giving 256 bits that survive resizing and
# recompression. Resized copies of one photo differ by 0 to 25 bits; different
# photos of the same scene by far more. Measured on the first 72 files, 10-01.
LOOKALIKE_BITS = 30


def _grey_grid(path):
    """16 rows of 17 grey values, by Pillow when present, else macOS sips."""
    try:
        from PIL import Image, ImageOps
        with Image.open(path) as im:
            im = ImageOps.exif_transpose(im).convert("L").resize((17, 16), Image.LANCZOS)
            px = list(im.getdata())
        return [px[y * 17:(y + 1) * 17] for y in range(16)]
    except ImportError:
        pass
    if not shutil.which("sips"):
        return None
    tmp = MEDIA_ROOT / "derived" / f"_dhash-{os.getpid()}-{threading.get_ident()}.bmp"
    tmp.parent.mkdir(parents=True, exist_ok=True)
    try:
        subprocess.run(["sips", "-s", "format", "bmp", "-z", "16", "17", str(path), "--out", str(tmp)],
                       capture_output=True, check=True)
        b = tmp.read_bytes()
        off = struct.unpack("<I", b[10:14])[0]
        w, hgt = struct.unpack("<ii", b[18:26])
        bpp = struct.unpack("<H", b[28:30])[0] // 8
        row = (w * bpp + 3) // 4 * 4
        grid = [[sum(b[off + y * row + x * bpp: off + y * row + x * bpp + 3]) / 3 for x in range(w)]
                for y in range(abs(hgt))]
        return grid[::-1] if hgt > 0 else grid
    finally:
        tmp.unlink(missing_ok=True)


def dhash_of(path):
    """64 hex characters, or None for anything that is not a readable picture."""
    if kind_of(Path(path).suffix.lower().lstrip(".")) not in ("photo", "image"):
        return None
    try:
        grid = _grey_grid(path)
        bits = 0
        for y in range(16):
            for x in range(16):
                bits = (bits << 1) | (grid[y][x] > grid[y][x + 1])
        return f"{bits:064x}"
    except Exception:
        return None


def _bits_apart(a, b):
    return bin(int(a, 16) ^ int(b, 16)).count("1")


_inbox_hash = {}          # (name, size, mtime) -> dhash, so a reload does not re-read every file


def inbox_dhash(path):
    st = path.stat()
    key = (path.name, st.st_size, int(st.st_mtime))
    if key not in _inbox_hash:
        _inbox_hash[key] = dhash_of(path)
    return _inbox_hash[key]


def is_marked_duplicate(rec):
    return (rec.get("title") or "").upper().startswith("DUPLICATE")


def ensure_dhashes(reg):
    """Give every registered picture its hash, once. True if any was added."""
    changed = False
    for r in reg["items"]:
        if "dhash" not in r["source"]:
            src = original_of(r)
            r["source"]["dhash"] = dhash_of(src) if src else None
            changed = True
    return changed


def lookalike_in(reg, dh, skip_id=None):
    """The registered item that looks most like this hash, if close enough."""
    if not dh:
        return None
    best = None
    for r in reg["items"]:
        other = r["source"].get("dhash")
        if not other or r["media_id"] == skip_id or is_marked_duplicate(r):
            continue
        d = _bits_apart(dh, other)
        if d <= LOOKALIKE_BITS and (best is None or d < best[0]):
            best = (d, r)
    return best[1] if best else None


class LookAlike(ValueError):
    pass


# ── credentials ────────────────────────────────────────────────────────────

def _env_file_values():
    """KEY=VALUE lines from <media root>/r2.env, outside the repo."""
    out = {}
    env_file = MEDIA_ROOT / "r2.env"
    if env_file.is_file():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def _secret(name):
    """The environment first, then the env file. (value, where)."""
    v = os.environ.get(name, "").strip()
    if v:
        return v, "environment"
    v = _env_file_values().get(name, "")
    return v, (str(MEDIA_ROOT / "r2.env") if v else "")


def r2_credentials():
    """Three values from the environment, else from <media root>/r2.env.
    Returns (dict, where) or (None, what is missing)."""
    keys = ("R2_ACCOUNT_ID", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY")
    found = {k: _secret(k) for k in keys}
    env = {k: v for k, (v, _w) in found.items()}
    where = next((w for _v, w in found.values() if w), "environment")
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


def _unknown_to_blank(rec):
    """A typed "unknown" is the same as nothing. The first form said "unknown
    is fine" and Randy typed it, so every save cleans it, old records too."""
    for k in ("made_by", "credit_line", "caption", "date"):
        if isinstance(rec.get(k), str) and rec[k].strip().lower() in ("", "unknown", "n/a", "none", "?"):
            rec[k] = None
    return rec


def save_registry(reg):
    for r in reg["items"]:
        _unknown_to_blank(r)
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

def _title_tags_species(form):
    title = (form.get("title") or "").strip()
    if not title:
        raise ValueError("A title is needed. What is it?")
    tags = [t.strip() for t in (form.get("tags") or "").split(",") if t.strip()]
    # The form turns a typed name into its id when one is picked from the list.
    # A name typed in full and never picked still works if it matches exactly one.
    index = species_index()
    species, bad = [], []
    for tok in (t.strip() for t in (form.get("species") or "").split(",")):
        if not tok:
            continue
        if re.fullmatch(r"PSBP-\d{5}", tok.upper()):
            hit = [tok.upper()] if any(i == tok.upper() for i, _, _ in index) else []
        else:
            hit = [i for i, name, _ in index if name.lower() == tok.lower()]
        if len(hit) == 1:
            if hit[0] not in species:
                species.append(hit[0])
        else:
            bad.append(tok)
    if bad:
        raise ValueError("Species not recognised: " + ", ".join(bad)
                         + ". Type part of the name and pick it from the list.")
    return title, tags, species


def species_index():
    """[id, common name, scientific name] for every species in the three
    masters, read fresh each time (rule 4). Feeds the name picker on the form."""
    out = []
    for fname in ("plant_signage.json", "wildlife_signage.json", "research.json"):
        for sp in load_json(SOURCES / fname, {}).get("species", []):
            if sp.get("id") and sp.get("common_name"):
                out.append((sp["id"], sp["common_name"],
                            sp.get("botanical_name") or sp.get("scientific_name") or ""))
    return out

def register(form):
    """Mint an id, move the inbox file to originals, write the record."""
    name = os.path.basename(form.get("filename", ""))
    src = MEDIA_ROOT / "inbox" / name
    if not name or not src.is_file():
        raise ValueError(f"not in the inbox: {name}")
    if form.get("kids") in ("yes", "true", "on", True):
        raise ValueError("Kids in the photo: that is private, and the private bucket is not "
                         "built yet. Take it out of the inbox for now; it was not registered.")
    title, tags, species = _title_tags_species(form)
    ext = src.suffix.lower().lstrip(".")

    def opt(k):
        v = (form.get(k) or "").strip()
        return v or None

    with _lock:
        reg = load_registry()
        same = next((r for r in reg["items"] if r["source"].get("sha256") == sha256_of(src)), None)
        if same:
            raise ValueError(f"This is the very same file as {same['media_id']} \"{same['title']}\", "
                             "already registered. Take it out of the inbox.")
        ensure_dhashes(reg)
        dh = inbox_dhash(src)
        twin = lookalike_in(reg, dh)
        if twin and form.get("anyway") != "yes":
            raise LookAlike(f"This looks like the same picture as {twin['media_id']} \"{twin['title']}\", "
                            "already registered (perhaps at another size or under another name).")
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
            "license": form.get("license") if form.get("license") in LICENSES else None,
            "public": form.get("public") if form.get("public") in ("yes", "no", "not_sure") else "not_sure",
            "kids": False,
            "tags": tags,
            "date": opt("date"),
            # Everything registered here was made at the park (Randy, 10-01: "I don't
            # think there is a single file not in the park"). The question is gone
            # from the form; the field stays so an exception can be set by hand.
            "at_the_park": form.get("at_the_park") if form.get("at_the_park") in ("yes", "no", "unknown") else "yes",
            "species": species,
            "caption": opt("caption"),
            "source": {"type": opt("dropped_by") or "randy", "original_filename": name,
                       "sha256": digest, "bytes": dest.stat().st_size, "dhash": dh},
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


# ── edit ───────────────────────────────────────────────────────────────────

def edit(form):
    """Change the words on a registered item. The id, the file and its address
    in the bucket never change. An item already in the bucket goes back to
    "waiting" so the next Upload refreshes the record kept beside its files
    (the files themselves are skipped as already up)."""
    media_id = form.get("media_id", "")
    title, tags, species = _title_tags_species(form)

    def opt(k):
        v = (form.get(k) or "").strip()
        return v or None

    public = form.get("public") if form.get("public") in ("yes", "no", "not_sure") else "yes"
    with _lock:
        reg = load_registry()
        rec = next((r for r in reg["items"] if r["media_id"] == media_id), None)
        if not rec:
            raise KeyError(media_id)
        in_bucket = bool(rec.get("url"))
        if in_bucket and public != "yes":
            raise ValueError("This one is already in the public bucket. Taking a file back out "
                             "is not built yet, so it stays public; nothing was changed.")
        rec.update({"title": title, "made_by": opt("made_by"), "credit_line": opt("credit_line"),
                    "license": form.get("license") if form.get("license") in LICENSES else None,
                    "public": public, "tags": tags, "date": opt("date"), "species": species,
                    "caption": opt("caption"), "updated": now_z()})
        rec["state"] = "waiting" if public == "yes" else "private"
        rec.pop("problem", None)
        save_registry(reg)
    log(f"edited {media_id}  \"{title}\"  public={public}  state={rec['state']}")
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


# ── suggest ────────────────────────────────────────────────────────────────

def _anthropic_messages(key, system, content, schema, timeout=120):
    """POST to the Messages API with the standard library. Structured output
    through output_config.format; low effort, this is a look-and-describe job;
    server-side fallbacks on so a safety decline re-runs on another model."""
    payload = {"model": AI_MODEL, "max_tokens": 1024, "system": system,
               "messages": [{"role": "user", "content": content}],
               "output_config": {"effort": "low",
                                 "format": {"type": "json_schema", "schema": schema}},
               "fallbacks": "default"}
    req = urllib.request.Request(
        ANTHROPIC_URL, data=json.dumps(payload).encode("utf-8"), method="POST",
        headers={"x-api-key": key, "anthropic-version": ANTHROPIC_VERSION,
                 "anthropic-beta": "server-side-fallback-2026-07-01",
                 "content-type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"Anthropic API error {e.code}: {e.read().decode('utf-8', 'replace')[:300]}")
    except urllib.error.URLError as e:
        raise RuntimeError(f"Could not reach the Anthropic API: {e.reason}")


def _exif_date(path):
    """YYYY-MM-DD from the camera data, or None. A fact, not a guess."""
    try:
        from PIL import Image
        with Image.open(path) as im:
            raw = im.getexif().get(36867) or im.getexif().get(306)   # DateTimeOriginal, DateTime
        if raw and len(str(raw)) >= 10:
            y, m, d = str(raw)[:10].replace("-", ":").split(":")
            return f"{y}-{m}-{d}"
    except Exception:
        pass
    return None


def suggest(filename):
    """Propose title, tags, caption and the kids flag for one inbox photo."""
    name = os.path.basename(filename or "")
    src = MEDIA_ROOT / "inbox" / name
    if not name or not src.is_file():
        raise ValueError(f"not in the inbox: {name}")
    ext = src.suffix.lower().lstrip(".")
    if kind_of(ext) != "photo":
        raise ValueError("Suggest looks at photographs only; fill this one in by hand.")
    key, _where = _secret("ANTHROPIC_API_KEY")
    if not key:
        raise RuntimeError(f"ANTHROPIC_API_KEY is not set: put it in the environment or in {MEDIA_ROOT / 'r2.env'}")
    small = MEDIA_ROOT / "derived" / "_suggest" / (name + ".jpg")
    rendition(src, small, SUGGEST_PX, SUGGEST_BUDGET)
    data = base64.standard_b64encode(small.read_bytes()).decode("ascii")
    content = [{"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": data}},
               {"type": "text", "text": "Catalog this photograph."}]
    resp = _anthropic_messages(key, SUGGEST_SYSTEM, content, SUGGEST_SCHEMA)
    if resp.get("stop_reason") == "refusal":
        raise RuntimeError("Claude declined to describe this photograph; fill it in by hand.")
    text = "".join(b.get("text", "") for b in resp.get("content", []) if b.get("type") == "text")
    out = json.loads(text)
    out["tags"] = [t.strip().lower() for t in out.get("tags", []) if t.strip()][:8]
    out["date"] = _exif_date(src)
    out["model"] = resp.get("model", AI_MODEL)
    log(f"suggested for {name}: \"{out.get('title')}\" kids={out.get('kids')} people={out.get('people')}")
    return out


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
.sug{background:#fff6dd}
.defaults{background:#fff;border:1px solid var(--gold);border-radius:8px;padding:10px 14px;margin:10px 0}
.defaults .fields{grid-template-columns:repeat(3,1fr)}
.setup{background:#fff;border:1px solid #ddd;border-radius:8px;padding:10px 14px}
.setup div{margin:2px 0}
.sp-pick{display:flex;flex-wrap:wrap;gap:6px}
.sp-pick button{background:#fff;color:var(--green-deep);border:1px solid var(--green-mid);font-weight:400;padding:4px 10px;text-align:left}
.sp-pick button:hover{background:#dff3e0}
.sp-names{color:var(--green-deep)}
.dupe{background:#ffe9c7;border:1px solid var(--gold);border-radius:6px;padding:6px 10px;margin-top:8px}
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
  let res = await post('/register', data);
  if (res.lookalike && confirm(res.error + '\\n\\nRegister it anyway?')){
    data.anyway = 'yes'; res = await post('/register', data);
  }
  if(res.error){
    msg.textContent = res.error; msg.style.display='block';
    btn.disabled = false; btn.textContent = 'Register'; return;
  }
  dropDraft(form);
  form.remove();
  // A reload in the middle of "Suggest all" would stop it, so wait for it to finish.
  if (suggesting) reloadWhenDone = true; else location.reload();
}
// Drafts: what is typed or suggested on a card is kept in this browser until the
// card is registered, so a reload (Register, Upload, refresh) never blanks it.
let suggesting = false, reloadWhenDone = false;
function readDrafts(){ try { return JSON.parse(localStorage.getItem('intakeDrafts')||'{}'); } catch(e){ return {}; } }
function writeDrafts(d){ try { localStorage.setItem('intakeDrafts', JSON.stringify(d)); } catch(e){} }
function fileOf(form){ return form.querySelector('[name=filename]').value; }
function saveDraft(form){
  const d = readDrafts(), vals = {}, sug = [];
  form.querySelectorAll('.fields [name]').forEach(el => {
    vals[el.name] = el.type === 'checkbox' ? el.checked : el.value;
    if (el.classList.contains('sug')) sug.push(el.name);
  });
  d[fileOf(form)] = {vals: vals, sug: sug};
  writeDrafts(d);
}
function dropDraft(form){ const d = readDrafts(); delete d[fileOf(form)]; writeDrafts(d); }
function loadDrafts(){
  const d = readDrafts(), kept = {};
  document.querySelectorAll('form.card').forEach(form => {
    const draft = d[fileOf(form)];
    if (draft){
      kept[fileOf(form)] = draft;
      for (const [k, v] of Object.entries(draft.vals || {})){
        const el = form.querySelector('.fields [name='+k+']'); if (!el) continue;
        if (el.type === 'checkbox') el.checked = !!v; else el.value = v;
        if ((draft.sug || []).includes(k)) el.classList.add('sug');
      }
    }
    form.addEventListener('input', () => saveDraft(form));
    form.addEventListener('change', () => saveDraft(form));
  });
  writeDrafts(kept);                  // forget files that have left the inbox
}
const DEFAULT_FIELDS = ['made_by','credit_line','license','public','dropped_by'];
// One time only: the form used to start at "Not sure" and spell the field "licence".
// Public now starts at Yes, so what this browser remembered is brought along.
(function(){
  try {
    if (localStorage.getItem('intakeV2')) return;
    const fix = v => { if (v.licence !== undefined){ if (v.license === undefined) v.license = v.licence; delete v.licence; }
                       if (v.public === 'not_sure') v.public = 'yes'; };
    const d = JSON.parse(localStorage.getItem('intakeDefaults')||'{}'); fix(d);
    localStorage.setItem('intakeDefaults', JSON.stringify(d));
    const dr = JSON.parse(localStorage.getItem('intakeDrafts')||'{}');
    for (const k in dr) fix(dr[k].vals || {});
    localStorage.setItem('intakeDrafts', JSON.stringify(dr));
    localStorage.setItem('intakeV2', '1');
  } catch(e){}
})();
function loadDefaults(){
  let d = {}; try { d = JSON.parse(localStorage.getItem('intakeDefaults')||'{}'); } catch(e){}
  for (const k of DEFAULT_FIELDS){
    const box = document.querySelector('#defaults [name='+k+']');
    if (box && d[k] !== undefined) box.value = d[k];
    document.querySelectorAll('form.card [name='+k+']').forEach(el => { if (d[k] !== undefined && d[k] !== '') el.value = d[k]; });
  }
}
function saveDefaults(){
  const d = {};
  for (const k of DEFAULT_FIELDS){ const box = document.querySelector('#defaults [name='+k+']'); if (box) d[k] = box.value; }
  try { localStorage.setItem('intakeDefaults', JSON.stringify(d)); } catch(e){}
  loadDefaults();
  const drafts = readDrafts();
  document.querySelectorAll('form.card').forEach(form => { if (drafts[fileOf(form)]) saveDraft(form); });
}
async function suggest(btn){
  const form = btn.closest('form');
  const msg = form.querySelector('.msg');
  btn.disabled = true; btn.textContent = 'Looking…';
  const res = await post('/suggest', {filename: form.querySelector('[name=filename]').value});
  btn.disabled = false; btn.textContent = 'Suggest';
  if (res.error){ msg.textContent = res.error; msg.style.display='block'; return; }
  const set = (k, v) => { const el = form.querySelector('[name='+k+']'); if (el && v){ el.value = v; el.classList.add('sug'); } };
  set('title', res.title); set('tags', (res.tags||[]).join(', ')); set('caption', res.caption); set('date', res.date);
  const kids = form.querySelector('[name=kids]');
  if (res.kids === 'yes') kids.checked = true;
  msg.textContent = 'Suggested: check it. People: ' + res.people + ', kids: ' + res.kids + (res.date ? ', date from the camera.' : ', no camera date.');
  msg.style.display = 'block';
  if (form.isConnected) saveDraft(form);
}
async function suggestAll(btn){
  btn.disabled = true; suggesting = true;
  const buttons = Array.from(document.querySelectorAll('form.card button.suggest'));
  for (let i = 0; i < buttons.length; i++){
    const form = buttons[i].closest('form');
    // Skip a card registered meanwhile, and one that already has a title (typed or suggested).
    if (!buttons[i].isConnected || form.querySelector('[name=title]').value.trim()) continue;
    btn.textContent = 'Suggesting ' + (i+1) + ' of ' + buttons.length + '…';
    await suggest(buttons[i]);
  }
  suggesting = false; btn.textContent = 'Suggest all'; btn.disabled = false;
  if (reloadWhenDone) location.reload();
}
document.addEventListener('DOMContentLoaded', () => { loadDefaults(); loadDrafts(); });
// Species picker: type part of a common or scientific name in "Species in it",
// click a match, and its PSBP id replaces what was typed. The names of the ids
// already in the box are spelled out under it.
const SP_ID = /^PSBP-\\d{5}$/i;
function spNames(input){
  const box = input.parentElement.querySelector('.sp-names'); if (!box) return;
  box.textContent = input.value.split(',').map(t => t.trim().toUpperCase()).filter(t => SP_ID.test(t))
    .map(id => { const s = SPECIES.find(x => x[0] === id); return s ? s[1] : id + ' (not a species we have)'; }).join(' · ');
}
function spSuggest(input){
  const pick = input.parentElement.querySelector('.sp-pick'); if (!pick) return;
  pick.innerHTML = ''; spNames(input);
  const parts = input.value.split(','), q = parts[parts.length - 1].trim().toLowerCase();
  if (q.length < 2 || SP_ID.test(q)) return;
  const hits = SPECIES.filter(x => x[1].toLowerCase().includes(q) || x[2].toLowerCase().includes(q))
    .sort((a, b) => (b[1].toLowerCase().startsWith(q) - a[1].toLowerCase().startsWith(q)) || a[1].localeCompare(b[1])).slice(0, 10);
  if (!hits.length){ pick.textContent = 'No species with "' + q + '" in its name.'; return; }
  for (const [id, name, sci] of hits){
    const b = document.createElement('button'); b.type = 'button';
    b.textContent = name + (sci ? ' (' + sci + ')' : '') + ' ' + id;
    b.onclick = () => {
      parts[parts.length - 1] = ' ' + id;
      input.value = parts.map(t => t.trim()).filter(Boolean).join(', ') + ', ';
      pick.innerHTML = ''; spNames(input); input.focus();
      input.dispatchEvent(new Event('change', {bubbles: true}));   // keeps the draft
    };
    pick.appendChild(b);
  }
}
document.addEventListener('input', e => { if (e.target.name === 'species') spSuggest(e.target); });
document.addEventListener('DOMContentLoaded', () => setTimeout(() => document.querySelectorAll('[name=species]').forEach(spNames), 0));
function toggleEdit(id){ const r = document.getElementById('edit-' + id); r.hidden = !r.hidden; }
async function saveEdit(form){
  const btn = form.querySelector('button[type=submit]'), msg = form.querySelector('.msg');
  btn.disabled = true;
  const res = await post('/edit', Object.fromEntries(new FormData(form).entries()));
  if (res.error){ msg.textContent = res.error; msg.style.display = 'block'; btn.disabled = false; return; }
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
    with _lock:
        reg = load_registry()
        if ensure_dhashes(reg):
            save_registry(reg)
    creds, where = r2_credentials()
    ai_key, ai_where = _secret("ANTHROPIC_API_KEY")
    base = PUBLIC_BASE.get(BUCKET)

    out = [f"<!doctype html><html><head><meta charset='utf-8'><title>PSBP Media intake</title>"
           f"<style>{CSS}</style></head><body><header><h1>Media intake</h1>"
           f"<p>Register park media, then send it to the R2 library. Bucket: <b>{h(BUCKET)}</b>"
           f"{'' if BUCKET == DEFAULT_BUCKET else ' (PRODUCTION)'}</p></header><main>"]
    out.append("<div class='setup'>"
               f"<div>Inbox folder: <code>{h(inbox)}</code> — drop files here, then reload.</div>"
               f"<div>Registry: <code>{h(REGISTRY)}</code> ({len(reg['items'])} items)</div>"
               f"<div>R2 keys: {'found in ' + h(where) if creds else '<b>' + h(where) + '</b>'}</div>"
               f"<div>Suggest (Claude): {'key found in ' + h(ai_where) if ai_key else '<b>ANTHROPIC_API_KEY missing — add it to ' + h(MEDIA_ROOT / 'r2.env') + '</b>'}</div>"
               f"<div>Public address: {h(base) if base else 'none for this bucket yet'}</div></div>")

    # Who took it: every earlier answer is offered, and each card starts with the
    # commonest answer among the last five registered (blank counts as an answer;
    # a tie goes to the more recent). Randy asked for this, 10-01.
    by_id = sorted(reg["items"], key=lambda r: r["media_id"])
    seen = Counter(r["made_by"] for r in by_id if r.get("made_by"))
    makers = "".join(f"<option value=\"{h(n)}\">" for n, _ in seen.most_common())
    last5 = [r.get("made_by") or "" for r in by_id[-5:]]
    maker_default = max(last5, key=lambda n: (last5.count(n), max(i for i, x in enumerate(last5) if x == n))) if last5 else ""
    out.append(f"<datalist id='makers'>{makers}</datalist>")

    out.append(f"<h2>In the inbox ({len(waiting)})</h2>")
    if waiting:
        lic_opts = "".join(f"<option value='{k}'>{v}</option>" for k, v in LICENSE_CHOICES)
        out.append(f"""
<div class='defaults' id='defaults'><b>Batch defaults</b> <small>— set once, every form below starts with these; they are never guessed</small>
  <div class='fields' style='margin-top:6px'>
    <label>Who took or made it?<input name='made_by' list='makers' onchange='saveDefaults()'></label>
    <label>Credit as <small>usually blank</small><input name='credit_line' onchange='saveDefaults()'></label>
    <label>License<select name='license' onchange='saveDefaults()'><option value=''>Unknown / not set</option>{lic_opts}</select></label>
    <label>May the public see it?<select name='public' onchange='saveDefaults()'>
      <option value='yes'>Yes</option><option value='not_sure'>Not sure</option><option value='no'>No</option></select></label>
    <label>Dropped by<select name='dropped_by' onchange='saveDefaults()'><option value='randy'>Randy</option><option value='bev'>Bev</option><option value='other'>Other</option></select></label>
  </div>
  <div class='row'><button class='gold' onclick='suggestAll(this)'>Suggest all</button>
  <span>Claude proposes title, tags, caption and a kids flag for every photo below. You check, then Register.</span></div>
</div>""")
    if not waiting:
        out.append("<p>Nothing waiting. Drop files in the inbox folder and reload.</p>")
    seen_in_inbox = []            # (dhash, name) of the cards above this one
    for p in waiting:
        ext = p.suffix.lower().lstrip(".")
        kind = kind_of(ext)
        dh = inbox_dhash(p)
        twin = lookalike_in(reg, dh)
        twin_file = next((n for d, n in seen_in_inbox if dh and _bits_apart(dh, d) <= LOOKALIKE_BITS), None)
        if dh:
            seen_in_inbox.append((dh, p.name))
        dupe_note = (f"<div class='dupe'>Looks like the same picture as {h(twin['media_id'])} "
                     f"&ldquo;{h(twin['title'])}&rdquo;, already registered. Take it out of the inbox "
                     "unless it really is different.</div>" if twin else
                     f"<div class='dupe'>Looks like the same picture as {h(twin_file)}, higher up in "
                     "this inbox. Keep the larger one.</div>" if twin_file else "")
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
    <label>Who took or made it? <small>leave blank for unknown</small><input name='made_by' list='makers' value="{h(maker_default)}"></label>
    <label>Credit as <small>leave blank; only if the credit should read differently from the name</small><input name='credit_line'></label>
    <label>License <small>needed before a species page may use it</small><select name='license'>
      <option value=''>Unknown / not set</option>{"".join(f"<option value='{k}'>{v}</option>" for k, v in LICENSE_CHOICES)}</select></label>
    <label>May the public see it?<select name='public'>
      <option value='yes' selected>Yes</option><option value='not_sure'>Not sure</option><option value='no'>No</option></select></label>
    <label>Tags <small>comma separated: event, wedding, sign, map, nursery…</small><input name='tags'></label>
    <label>Date <small>if known, YYYY-MM-DD</small><input name='date'></label>
    <label>Species in it <small>type part of a name, pick it from the list</small><input name='species' autocomplete='off'><span class='sp-names'></span><span class='sp-pick'></span></label>
    <label>Dropped by<select name='dropped_by'><option value='randy'>Randy</option><option value='bev'>Bev</option><option value='other'>Other</option></select></label>
    <label class='wide'>Caption <small>optional</small><input name='caption'></label>
    <label class='wide' style='flex-direction:row;gap:8px;align-items:center'>
      <input type='checkbox' name='kids' style='width:auto'> Are there kids in it? <small>(then it is private and stays out for now)</small></label>
  </div>
  {dupe_note}
  <div class='row'><button type='submit'>Register</button>{"<button type='button' class='gold suggest' onclick='suggest(this)'>Suggest</button>" if kind == "photo" else ""}<div class='msg'></div></div>
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
            if not is_marked_duplicate(r):
                twin = lookalike_in(reg, r["source"].get("dhash"), skip_id=r["media_id"])
                if twin:
                    prob += f"<div class='dupe'>Looks like the same picture as {h(twin['media_id'])}</div>"
            btn = (f"<button onclick=\"upload('{r['media_id']}')\">{'Retry' if r['state'] == 'problem' else 'Upload'}</button>"
                   if r["public"] == "yes" and r["state"] in ("waiting", "problem") else "")
            btn += f"<button type='button' onclick=\"toggleEdit('{r['media_id']}')\">Edit</button>"
            out.append(f"<tr><td>{pic}</td><td><code>{r['media_id']}</code></td>"
                       f"<td><b>{h(r['title'])}</b><br><small>{h(r['source']['original_filename'])}"
                       f"{' · ' + h(', '.join(r['tags'])) if r['tags'] else ''}</small></td>"
                       f"<td>{h(r['public'])}</td><td><span class='state {h(r['state'])}'>{h(r['state'])}</span>{prob}</td>"
                       f"<td>{where}</td><td><div class='row'>{btn}</div></td></tr>")
            sel = lambda v, cur: " selected" if v == cur else ""
            out.append(f"""<tr id='edit-{r['media_id']}' hidden><td colspan='7'>
<form onsubmit='event.preventDefault(); saveEdit(this)'>
  <input type='hidden' name='media_id' value='{r['media_id']}'>
  <div class='fields'>
    <label class='wide'>What is it? <small>a plain title</small><input name='title' required value="{h(r['title'])}"></label>
    <label>Who took or made it? <small>leave blank for unknown</small><input name='made_by' list='makers' value="{h(r.get('made_by'))}"></label>
    <label>Credit as <small>leave blank; only if the credit should read differently from the name</small><input name='credit_line' value="{h(r.get('credit_line'))}"></label>
    <label>License <small>needed before a species page may use it</small><select name='license'>
      <option value=''>Unknown / not set</option>{"".join(f"<option value='{k}'{sel(k, r.get('license'))}>{v}</option>" for k, v in LICENSE_CHOICES)}</select></label>
    <label>May the public see it?<select name='public'>
      <option value='yes'{sel('yes', r['public'])}>Yes</option><option value='not_sure'{sel('not_sure', r['public'])}>Not sure</option><option value='no'{sel('no', r['public'])}>No</option></select></label>
    <label>Tags <small>comma separated</small><input name='tags' value="{h(', '.join(r.get('tags') or []))}"></label>
    <label>Date <small>if known, YYYY-MM-DD</small><input name='date' value="{h(r.get('date'))}"></label>
    <label>Species in it <small>type part of a name, pick it from the list</small><input name='species' autocomplete='off' value="{h(', '.join(r.get('species') or []))}"><span class='sp-names'></span><span class='sp-pick'></span></label>
    <label class='wide'>Caption <small>optional</small><input name='caption' value="{h(r.get('caption'))}"></label>
  </div>
  <div class='row'><button type='submit'>Save</button><button type='button' onclick="toggleEdit('{r['media_id']}')" style='background:#777'>Cancel</button>
  {"<span>Already in the bucket: after saving, press Upload once to refresh its record there. The photo itself is not sent again.</span>" if r.get('url') else ""}<div class='msg'></div></div>
</form></td></tr>""")
        out.append("</table>")
    sp_json = json.dumps(species_index(), ensure_ascii=False).replace("</", "<\\/")
    out.append(f"</main><script>const SPECIES = {sp_json};{JS}</script></body></html>")
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
                try:
                    rec = register(body)
                    res = {"ok": True, "media_id": rec["media_id"]}
                except LookAlike as e:
                    res = {"error": str(e), "lookalike": True}
            elif path == "/edit":
                rec = edit(body)
                res = {"ok": True, "media_id": rec["media_id"]}
            elif path == "/upload":
                res = {"lines": upload(body.get("id") or None)}
            elif path == "/suggest":
                res = suggest(body.get("filename"))
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
