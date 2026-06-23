#!/usr/bin/env python3
"""
species_manager.py — Unified PSBP Species Dashboard

One tool, one port (8700), five tabs matching the species pipeline:
  Overview  → Pipeline funnel, needs-attention flags (both kingdoms)
  Intake    → Import species from iNat, mint PSBP IDs
  Photos    → Triage + review (hero/gallery/roles)
  Edit      → Signage field editor with live HTML preview
  Publish   → Promote/demote, generate HTML, rebuild indexes

Usage:
    python3 species_manager.py                # Start on port 8700
    python3 species_manager.py --port 8705    # Custom port

Architecture doc:  SPECIES_MANAGER.md
Shared module:     psbp_common.py
"""

import json
import os
import sys
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
from urllib.request import urlopen, Request

# ╔══════════════════════════════════════════════════════════════════════════╗
# ║  CONFIGURATION                                                         ║
# ╚══════════════════════════════════════════════════════════════════════════╝

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from psbp_common import (
    REPO, write_json_atomic, display_name, build_credit_line,
    load_json, PHOTO_CREDITS_JSON, resolve_hero_credit,
)

PORT = 8700

# Data source paths (all relative to REPO)
PLANT_SIGNAGE      = os.path.join(REPO, "data", "sources", "plant_signage.json")
WILDLIFE_SIGNAGE   = os.path.join(REPO, "data", "sources", "wildlife_signage.json")
PHOTO_CREDITS      = os.path.join(REPO, "data", "sources", "photo_credits.json")
PHOTOGRAPHER_NAMES = os.path.join(REPO, "data", "sources", "photographer_names.json")
PLANTS_INDEX       = os.path.join(REPO, "plants.json")
WILDLIFE_INDEX     = os.path.join(REPO, "wildlife.json")
PHOTOS_DIR         = os.path.join(REPO, "photos")

# Tab definitions — order matters for the nav bar
TABS = [
    {"id": "overview", "label": "Overview",       "route": "/",        "icon": "📊"},
    {"id": "intake",   "label": "Intake",         "route": "/intake",  "icon": "📥"},
    {"id": "photos",   "label": "Photos",         "route": "/photos",  "icon": "📷"},
    {"id": "edit",     "label": "Edit & Preview",  "route": "/edit",    "icon": "✏️"},
    {"id": "publish",  "label": "Publish",         "route": "/publish", "icon": "🚀"},
]

# Required fields for promotion readiness (per kingdom)
# Plants use "botanical_name"; wildlife uses "scientific_name"
PLANT_REQUIRED = ["common_name", "botanical_name", "more_information"]
WILDLIFE_REQUIRED = ["common_name", "scientific_name", "animal_group"]


# ╔══════════════════════════════════════════════════════════════════════════╗
# ║  DATA ACCESS                                                           ║
# ║                                                                        ║
# ║  JSON structures (as of schema v1.2 / v1.4):                          ║
# ║    plant_signage.json:    {"meta": {...}, "species": [list]}           ║
# ║    wildlife_signage.json: {"meta": {...}, "species": [list]}           ║
# ║    photo_credits.json:    {"meta": {...}, "photos":  [list]}           ║
# ║    photographer_names.json: {"handle": "Real Name", ...}              ║
# ║    plants.json / wildlife.json: [flat list of card objects]            ║
# ║                                                                        ║
# ║  Species fields:                                                       ║
# ║    Plants:   id, common_name, botanical_name, status, native, ...     ║
# ║    Wildlife: id, common_name, scientific_name, status, animal_group   ║
# ║  Photo fields:                                                         ║
# ║    psbp_id, hero, photographer, license, photo_id, ...               ║
# ╚══════════════════════════════════════════════════════════════════════════╝

def _load(path):
    """Read and parse a JSON file. Returns empty dict on missing file."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        print(f"[WARN] File not found: {path}")
        return {}
    except json.JSONDecodeError as e:
        print(f"[WARN] Bad JSON in {path}: {e}")
        return {}


def _get_species_list(raw):
    """Extract the species list from a signage JSON (handles meta wrapper)."""
    if isinstance(raw, dict):
        return raw.get("species", [])
    if isinstance(raw, list):
        return raw
    return []


def _get_photos_list(raw):
    """Extract the photos list from photo_credits.json (handles meta wrapper)."""
    if isinstance(raw, dict):
        return raw.get("photos", [])
    if isinstance(raw, list):
        return raw
    return []


def _build_hero_index(photos_list):
    """
    Build a set of species IDs that have at least one hero photo.
    Also returns a dict of psbp_id → list of photos for other lookups.
    """
    heroes = set()
    by_species = {}
    for photo in photos_list:
        psbp_id = photo.get("psbp_id", "")
        by_species.setdefault(psbp_id, []).append(photo)
        if photo.get("hero"):
            heroes.add(psbp_id)
    return heroes, by_species


def _hero_on_disk(species_id):
    """Check if the hero photo file exists on disk."""
    hero_dir = os.path.join(PHOTOS_DIR, species_id)
    if not os.path.isdir(hero_dir):
        return False
    return any(f.lower().endswith((".jpg", ".jpeg", ".png", ".webp"))
               for f in os.listdir(hero_dir))


def _check_required_fields(species_data, required):
    """Return list of missing required field names."""
    missing = []
    for field in required:
        val = species_data.get(field, "")
        if not val or (isinstance(val, str) and not val.strip()):
            missing.append(field)
    return missing


def get_overview_data():
    """
    Compute the full Overview payload.

    Returns dict with plants, wildlife, photographers, and attention sections.
    This is the single function that powers the Overview tab.
    """
    plant_species = _get_species_list(_load(PLANT_SIGNAGE))
    wildlife_species = _get_species_list(_load(WILDLIFE_SIGNAGE))
    photos_list = _get_photos_list(_load(PHOTO_CREDITS))
    names = _load(PHOTOGRAPHER_NAMES)

    # Build hero index once — used by both kingdoms
    hero_ids, photos_by_species = _build_hero_index(photos_list)

    def analyze_kingdom(species_list, required_fields, sci_field):
        """sci_field: 'botanical_name' for plants, 'scientific_name' for wildlife."""
        by_status = {}
        attention = []

        for sp in species_list:
            sid = sp.get("id", "???")
            status = sp.get("status", "unknown")
            by_status.setdefault(status, []).append(sid)

            # Attention checks for spotted species
            if status == "spotted":
                issues = []

                if sid not in hero_ids:
                    issues.append("No hero photo")
                elif not _hero_on_disk(sid):
                    issues.append("Hero not on disk")

                missing = _check_required_fields(sp, required_fields)
                if missing:
                    issues.append(f"Missing: {', '.join(missing)}")

                if issues:
                    attention.append({
                        "id": sid,
                        "name": sp.get("common_name", sid),
                        "scientific": sp.get(sci_field, ""),
                        "issues": issues,
                    })

        status_counts = {k: len(v) for k, v in by_status.items()}
        return {
            "total": len(species_list),
            "by_status": status_counts,
            "attention": sorted(attention, key=lambda x: x["id"]),
        }

    # Photographer analysis
    all_logins = set()
    for photo in photos_list:
        login = photo.get("photographer", "")
        if login:
            all_logins.add(login)

    resolved = {login for login in all_logins if login in names}
    unresolved = sorted(all_logins - resolved)

    return {
        "plants": analyze_kingdom(plant_species, PLANT_REQUIRED, "botanical_name"),
        "wildlife": analyze_kingdom(wildlife_species, WILDLIFE_REQUIRED, "scientific_name"),
        "photographers": {
            "total_logins": len(all_logins),
            "resolved": len(resolved),
            "unresolved": unresolved,
            "total_photos": len(photos_list),
        },
    }


def get_species_list(kingdom):
    """
    Return a list of species for a kingdom, sorted by ID.

    Used by Intake, Edit, Publish tabs to populate species pickers.
    kingdom: "plants" or "wildlife"
    """
    path = PLANT_SIGNAGE if kingdom == "plants" else WILDLIFE_SIGNAGE
    sci_field = "botanical_name" if kingdom == "plants" else "scientific_name"
    species_list = _get_species_list(_load(path))
    result = []
    for sp in sorted(species_list, key=lambda s: s.get("id", "")):
        result.append({
            "id": sp.get("id", ""),
            "common_name": sp.get("common_name", ""),
            "scientific_name": sp.get(sci_field, ""),
            "status": sp.get("status", "unknown"),
        })
    return result


# ── Photo data helpers ────────────────────────────────────────────────────

# Content tags available per kingdom (gallery is implicit — always present)
PLANT_PHOTO_TAGS  = ["whole", "flower", "fruit", "leaf", "bark", "seed", "habitat"]
WILDLIFE_PHOTO_TAGS = ["profile", "habitat", "feeding", "flight", "juvenile", "group"]

def _photo_thumb_url(photo):
    """Return the best thumbnail URL for a photo record.

    Strategy:
      1. Heroes stored locally → serve via /photos-file/ route
      2. Stored URL fields (medium_url, url, photo_url) → use directly
         - If it's a square URL from iNat, swap to medium
      3. Fallback → construct iNat open-data S3 URL from photo_id
    """
    psbp_id = photo.get("psbp_id", "")

    # 1) Heroes: scan local photos/ dir for the actual file
    if photo.get("hero") and psbp_id:
        hero_dir = os.path.join(PHOTOS_DIR, psbp_id)
        if os.path.isdir(hero_dir):
            for f in sorted(os.listdir(hero_dir)):
                if f.lower().endswith((".jpg", ".jpeg", ".png", ".webp")):
                    return f"/photos-file/{psbp_id}/{f}"

    # 2) Stored URL fields — check several possible names
    for field in ("medium_url", "url", "photo_url", "thumb_url", "image_url"):
        stored = photo.get(field, "")
        if stored:
            # iNat often stores the square or large size; swap to medium
            # for dashboard thumbnails (500px is plenty for ~220px cards)
            if "/square." in stored:
                stored = stored.replace("/square.", "/medium.")
            elif "/large." in stored:
                stored = stored.replace("/large.", "/medium.")
            return stored

    # 3) Fallback: construct from photo_id via iNat open-data S3
    pid = photo.get("photo_id", "")
    if pid:
        return f"https://inaturalist-open-data.s3.amazonaws.com/photos/{pid}/medium.jpg"

    return ""


def get_photos_summary(kingdom):
    """Return species list with photo counts and hero status for the picker.

    Returns list of dicts sorted by ID:
        [{id, common_name, scientific_name, photo_count, has_hero, status}, ...]
    """
    path = PLANT_SIGNAGE if kingdom == "plants" else WILDLIFE_SIGNAGE
    sci_field = "botanical_name" if kingdom == "plants" else "scientific_name"
    type_filter = "Plant" if kingdom == "plants" else "Wildlife"

    species_list = _get_species_list(_load(path))
    photos_list = _get_photos_list(_load(PHOTO_CREDITS))
    hero_ids, photos_by_species = _build_hero_index(photos_list)

    result = []
    for sp in sorted(species_list, key=lambda s: s.get("id", "")):
        sid = sp.get("id", "")
        # Count photos for this species (filter by type if mixed)
        sp_photos = photos_by_species.get(sid, [])
        type_photos = [p for p in sp_photos if p.get("type") == type_filter]
        result.append({
            "id": sid,
            "common_name": sp.get("common_name", ""),
            "scientific_name": sp.get(sci_field, ""),
            "status": sp.get("status", "unknown"),
            "photo_count": len(type_photos),
            "has_hero": sid in hero_ids,
        })
    return result


def get_species_photos(species_id):
    """Return all photos for a species from photo_credits.json, enriched.

    Each photo gets resolved display_name and a thumb_url for the UI.
    Returns list sorted: hero first, then by photo_id.
    """
    photos_list = _get_photos_list(_load(PHOTO_CREDITS))
    result = []
    for p in photos_list:
        if p.get("psbp_id") != species_id:
            continue
        login = p.get("photographer", "")
        raw_name = p.get("photographer_name", "")
        resolved = display_name(login, raw_name)
        result.append({
            **p,
            "resolved_name": resolved,
            "thumb_url": _photo_thumb_url(p),
        })
    result.sort(key=lambda p: (not p.get("hero", False), p.get("photo_id", "")))
    return result


# ── Hero swap pipeline helpers ────────────────────────────────────────────

def _download_hero_file(photo_url, species_id, photo_id):
    """Download a hero photo from iNat to photos/PSBP-xxxxx/{photo_id}.jpg.

    Uses the /large.jpg size for the published site (not medium).
    Returns the local file path on success, None on failure.
    """
    target_dir = os.path.join(PHOTOS_DIR, species_id)
    os.makedirs(target_dir, exist_ok=True)
    # Ensure we get the large size for the published page
    url = photo_url
    if "/medium." in url:
        url = url.replace("/medium.", "/large.")
    elif "/square." in url:
        url = url.replace("/square.", "/large.")
    target_file = os.path.join(target_dir, f"{photo_id}.jpg")
    req = Request(url, headers={"User-Agent": "PSBP-SpeciesManager/1.0"})
    with urlopen(req, timeout=30) as resp:
        data = resp.read()
    with open(target_file, "wb") as f:
        f.write(data)
    return target_file


def _cleanup_old_hero_files(species_id, keep_photo_id=None):
    """Delete old hero image file(s) from photos/PSBP-xxxxx/.

    Optionally keeps one file (the new hero). Returns list of deleted filenames.
    """
    hero_dir = os.path.join(PHOTOS_DIR, species_id)
    if not os.path.isdir(hero_dir):
        return []
    keep = str(keep_photo_id) if keep_photo_id else None
    deleted = []
    for f in os.listdir(hero_dir):
        if keep and f.startswith(keep):
            continue
        filepath = os.path.join(hero_dir, f)
        if os.path.isfile(filepath):
            os.remove(filepath)
            deleted.append(f)
    return deleted


def _update_search_index_hero(species_id, old_photo_id, new_hero_record):
    """Update the search index card (plants.json or wildlife.json) with new hero.

    Finds the card by species ID, updates credit fields + focus point,
    and replaces old photo_id references with the new one in any string field.
    Returns the index filename updated, or None.
    """
    credit = resolve_hero_credit(new_hero_record)
    new_pid = str(new_hero_record.get("photo_id", ""))
    old_pid = str(old_photo_id) if old_photo_id else ""
    new_focus = new_hero_record.get("focus") or "50% 50%"
    # Normalize Python None stored as string
    if new_focus == "None":
        new_focus = "50% 50%"

    for idx_path in (PLANTS_INDEX, WILDLIFE_INDEX):
        idx = _load(idx_path)
        if not isinstance(idx, list):
            continue
        changed = False
        for card in idx:
            if card.get("id") != species_id:
                continue
            # Update credit fields (card has both "credit" and "credit_name")
            if "credit" in card:
                card["credit"] = credit.get("credit_name", "")
            if "credit_name" in card:
                card["credit_name"] = credit.get("credit_name", "")
            if "credit_license" in card:
                card["credit_license"] = credit.get("credit_license", "")
            if "credit_line" in card:
                card["credit_line"] = credit.get("credit_line", "")
            # Update focus point from new hero
            if "focus" in card:
                card["focus"] = new_focus
            # Replace old hero photo_id in any string field (image paths, etc.)
            if old_pid and new_pid:
                for key in card:
                    if isinstance(card[key], str) and old_pid in card[key]:
                        card[key] = card[key].replace(old_pid, new_pid)
            changed = True
            break
        if changed:
            write_json_atomic(idx_path, idx)
            return os.path.basename(idx_path)
    return None


def _patch_html_hero(species_id, old_photo_id, new_photo_id, old_credit_line, new_credit_line):
    """Patch the generated HTML page to reference the new hero image and credit.

    Does a text replacement of old photo_id → new photo_id throughout the file,
    and old credit line → new credit line. Returns the filename patched, or None.
    """
    old_pid = str(old_photo_id) if old_photo_id else ""
    new_pid = str(new_photo_id)

    for d in (os.path.join(REPO, "plants"), os.path.join(REPO, "wildlife")):
        if not os.path.isdir(d):
            continue
        for f in os.listdir(d):
            if f.startswith(species_id) and f.endswith(".html"):
                filepath = os.path.join(d, f)
                with open(filepath, "r", encoding="utf-8") as fh:
                    html = fh.read()
                original = html
                # Replace photo_id references (image paths, lightbox links, etc.)
                if old_pid:
                    html = html.replace(old_pid, new_pid)
                # Replace credit line
                if old_credit_line and new_credit_line and old_credit_line != new_credit_line:
                    html = html.replace(old_credit_line, new_credit_line)
                if html != original:
                    with open(filepath, "w", encoding="utf-8") as fh:
                        fh.write(html)
                    return f
    return None


# ╔══════════════════════════════════════════════════════════════════════════╗
# ║  API HANDLERS                                                          ║
# ║                                                                        ║
# ║  Each handler takes (params) and returns a JSON-serializable dict.     ║
# ║  Add new endpoints here, then register in API_ROUTES below.            ║
# ╚══════════════════════════════════════════════════════════════════════════╝

def handle_api_overview(params):
    """GET /api/overview — full dashboard stats."""
    return get_overview_data()


def handle_api_species_list(params):
    """GET /api/species?kingdom=plants — species list for pickers."""
    kingdom = params.get("kingdom", ["plants"])[0]
    return {"kingdom": kingdom, "species": get_species_list(kingdom)}


# ── Future API stubs ───────────────────────────────────────────────────────
# These return descriptive placeholders. Replace with real logic as each
# tab gets built out. The route is already wired up.

def handle_api_intake_check(params):
    """POST /api/intake/check — duplicate check before minting. STUB."""
    return {"status": "stub", "message": "Intake duplicate check not yet implemented."}

def handle_api_photos_species(params):
    """GET /api/photos/species?id=PSBP-00001 — all photos for a species."""
    species_id = params.get("id", [""])[0]
    if not species_id:
        return {"error": "Missing id parameter"}
    photos = get_species_photos(species_id)
    return {"species_id": species_id, "photos": photos, "count": len(photos)}


def handle_api_photos_summary(params):
    """GET /api/photos/summary?kingdom=plants — species with photo stats for picker."""
    kingdom = params.get("kingdom", ["plants"])[0]
    return {"kingdom": kingdom, "species": get_photos_summary(kingdom)}


def handle_api_photos_set_hero(params):
    """POST /api/photos/hero — full hero swap pipeline.

    Body: {"psbp_id": "PSBP-00042", "photo_id": "12345678"}

    Steps:
      1. Update photo_credits.json (flip hero flags, update filename)
      2. Delete old hero file(s) from disk
      3. Download new hero from iNat CDN (large size)
      4. Update search index card (credits + image path)
      5. Patch HTML page (swap photo_id and credit references)

    Returns a status report of each step.
    """
    body = params.get("_body", {})
    psbp_id = body.get("psbp_id", "")
    photo_id = str(body.get("photo_id", ""))
    if not psbp_id or not photo_id:
        return {"error": "Missing psbp_id or photo_id"}

    report = {"psbp_id": psbp_id, "new_hero": photo_id, "steps": {}}

    # ── Load data and find records ──────────────────────────────
    credits = _load(PHOTO_CREDITS)
    photos = credits.get("photos", [])
    old_hero_rec = None
    new_hero_rec = None
    old_photo_id = None
    old_credit_line = ""

    for p in photos:
        if p.get("psbp_id") != psbp_id:
            continue
        if str(p.get("photo_id", "")) == photo_id:
            new_hero_rec = p
        elif p.get("hero"):
            old_hero_rec = p
            old_photo_id = str(p.get("photo_id", ""))
            old_credit_line = p.get("credit_line", "")

    if not new_hero_rec:
        return {"error": f"Photo {photo_id} not found for {psbp_id}"}

    # If already the hero, nothing to do
    if new_hero_rec.get("hero"):
        return {"ok": True, "psbp_id": psbp_id, "note": "Already the hero",
                "steps": {}}

    # ── Step 1: Update photo_credits.json ───────────────────────
    for p in photos:
        if p.get("psbp_id") != psbp_id:
            continue
        if str(p.get("photo_id", "")) == photo_id:
            p["hero"] = True
            p["filename"] = f"{photo_id}.jpg"
            p["virtual"] = False
        elif p.get("hero"):
            p["hero"] = False
            # Old hero becomes virtual (CDN-only) unless someone
            # manually keeps the file
            p["filename"] = None
            p["virtual"] = True

    write_json_atomic(PHOTO_CREDITS, credits)
    report["steps"]["credits_updated"] = True

    # ── Step 2: Delete old hero file(s) from disk ───────────────
    try:
        deleted = _cleanup_old_hero_files(psbp_id)
        report["steps"]["old_files_deleted"] = deleted
    except Exception as e:
        report["steps"]["old_files_deleted"] = f"Error: {e}"

    # ── Step 3: Download new hero from iNat ─────────────────────
    photo_url = new_hero_rec.get("photo_url", "")
    if photo_url:
        try:
            dl_path = _download_hero_file(photo_url, psbp_id, photo_id)
            report["steps"]["downloaded"] = os.path.basename(dl_path)
        except Exception as e:
            report["steps"]["downloaded"] = f"Error: {e}"
            report["warning"] = "Hero download failed — run again or download manually"
    else:
        report["steps"]["downloaded"] = "No photo_url in record — skipped"

    # ── Step 4: Update search index ─────────────────────────────
    try:
        idx_file = _update_search_index_hero(psbp_id, old_photo_id, new_hero_rec)
        report["steps"]["search_index"] = idx_file or "Card not found in index"
    except Exception as e:
        report["steps"]["search_index"] = f"Error: {e}"

    # ── Step 5: Patch HTML page ─────────────────────────────────
    new_credit_line = new_hero_rec.get("credit_line", "")
    try:
        html_file = _patch_html_hero(
            psbp_id, old_photo_id, photo_id,
            old_credit_line, new_credit_line
        )
        report["steps"]["html_patched"] = html_file or "No HTML file found"
    except Exception as e:
        report["steps"]["html_patched"] = f"Error: {e}"

    report["ok"] = True
    return report


def handle_api_photos_update_roles(params):
    """POST /api/photos/roles — update content tags on a photo.

    Body: {"psbp_id": "PSBP-00042", "photo_id": "12345678", "roles": ["gallery","leaf","flower"]}
    The "gallery" role is always preserved — it cannot be removed here.
    """
    body = params.get("_body", {})
    psbp_id = body.get("psbp_id", "")
    photo_id = str(body.get("photo_id", ""))
    roles = body.get("roles", [])
    if not psbp_id or not photo_id:
        return {"error": "Missing psbp_id or photo_id"}

    credits = _load(PHOTO_CREDITS)
    photos = credits.get("photos", [])
    found = False

    for p in photos:
        if p.get("psbp_id") == psbp_id and str(p.get("photo_id", "")) == photo_id:
            p["role"] = roles
            found = True
            break

    if not found:
        return {"error": f"Photo {photo_id} not found for {psbp_id}"}

    write_json_atomic(PHOTO_CREDITS, credits)
    return {"ok": True, "psbp_id": psbp_id, "photo_id": photo_id, "roles": roles}


def handle_api_photos_trash(params):
    """POST /api/photos/trash — remove a photo from photo_credits.json.

    Body: {"psbp_id": "PSBP-00042", "photo_id": "12345678"}
    Does NOT delete any files on disk — just removes the registry entry.
    """
    body = params.get("_body", {})
    psbp_id = body.get("psbp_id", "")
    photo_id = str(body.get("photo_id", ""))
    if not psbp_id or not photo_id:
        return {"error": "Missing psbp_id or photo_id"}

    credits = _load(PHOTO_CREDITS)
    photos = credits.get("photos", [])
    original_len = len(photos)

    credits["photos"] = [
        p for p in photos
        if not (p.get("psbp_id") == psbp_id and str(p.get("photo_id", "")) == photo_id)
    ]

    if len(credits["photos"]) == original_len:
        return {"error": f"Photo {photo_id} not found for {psbp_id}"}

    write_json_atomic(PHOTO_CREDITS, credits)
    return {"ok": True, "psbp_id": psbp_id, "removed": photo_id,
            "remaining": len(credits["photos"])}

def handle_api_preview(params):
    """GET /api/preview?id=PSBP-00001 — HTML preview. STUB."""
    return {"status": "stub", "message": "Preview API not yet implemented."}

def handle_api_publish_ready(params):
    """GET /api/publish/ready?id=PSBP-00001 — readiness checklist. STUB."""
    return {"status": "stub", "message": "Publish readiness API not yet implemented."}


def handle_api_photos_debug(params):
    """GET /api/photos/debug?id=PSBP-00005 — raw field dump for debugging URLs.

    Returns the raw keys and first ~80 chars of each value for all photos
    of a species, plus the search index card. Temporary endpoint.
    """
    species_id = params.get("id", [""])[0]

    # Photo records
    photos_list = _get_photos_list(_load(PHOTO_CREDITS))
    matches = [p for p in photos_list if p.get("psbp_id") == species_id]
    photo_samples = []
    for p in matches[:3]:
        fields = {}
        for k, v in p.items():
            sv = str(v)
            fields[k] = sv[:80] + ("…" if len(sv) > 80 else "")
        fields["_resolved_thumb_url"] = _photo_thumb_url(p)
        photo_samples.append(fields)

    # Search index card (try both)
    card_sample = None
    for idx_path in (PLANTS_INDEX, WILDLIFE_INDEX):
        idx = _load(idx_path)
        if isinstance(idx, list):
            for card in idx:
                if card.get("id") == species_id:
                    card_sample = {k: str(v)[:80] for k, v in card.items()}
                    card_sample["_source"] = os.path.basename(idx_path)
                    break
        if card_sample:
            break

    # HTML page check
    html_files = []
    for d in (os.path.join(REPO, "plants"), os.path.join(REPO, "wildlife")):
        if os.path.isdir(d):
            for f in os.listdir(d):
                if f.startswith(species_id) and f.endswith(".html"):
                    html_files.append(f)

    return {
        "species_id": species_id,
        "photo_count": len(matches),
        "sample_photos": photo_samples,
        "search_index_card": card_sample,
        "html_files": html_files,
    }


# ╔══════════════════════════════════════════════════════════════════════════╗
# ║  HTML SHELL                                                            ║
# ║                                                                        ║
# ║  page_shell() wraps tab content in the shared nav, CSS, and brand.     ║
# ╚══════════════════════════════════════════════════════════════════════════╝

BRAND_CSS = """
:root {
    --green-deep:  #1a3a1f;
    --green-mid:   #2d6a35;
    --green-light: #4a9e56;
    --gold:        #c5922a;
    --cream:       #faf8f3;
    --gray-100:    #f5f5f5;
    --gray-200:    #e8e8e8;
    --gray-400:    #999;
    --gray-600:    #555;
    --gray-800:    #222;
    --status-research: #78909c;
    --status-spotted:  #c5922a;
    --status-html:     #2d6a35;
    --radius:      6px;
    --shadow:      0 1px 3px rgba(0,0,0,0.08);
}
* { box-sizing: border-box; margin: 0; padding: 0; }
body {
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    background: var(--cream);
    color: var(--gray-800);
    line-height: 1.5;
}

/* ── Header ─────────────────────────────────────────────────── */
header {
    background: var(--green-deep);
    color: white;
    padding: 14px 24px;
    display: flex;
    align-items: center;
    gap: 12px;
}
header h1 {
    font-size: 18px;
    font-weight: 600;
    letter-spacing: 0.3px;
}
header .subtitle {
    font-size: 13px;
    opacity: 0.7;
    margin-left: auto;
}

/* ── Tab Nav ────────────────────────────────────────────────── */
nav.tabs {
    background: white;
    border-bottom: 1px solid var(--gray-200);
    display: flex;
    gap: 0;
    padding: 0 16px;
    box-shadow: var(--shadow);
}
nav.tabs a {
    text-decoration: none;
    color: var(--gray-600);
    padding: 12px 20px;
    font-size: 14px;
    font-weight: 500;
    border-bottom: 2px solid transparent;
    transition: all 0.15s;
    display: flex;
    align-items: center;
    gap: 6px;
}
nav.tabs a:hover {
    color: var(--green-mid);
    background: var(--gray-100);
}
nav.tabs a.active {
    color: var(--green-deep);
    border-bottom-color: var(--green-mid);
}
nav.tabs a .tab-icon { font-size: 15px; }

/* ── Main Content ───────────────────────────────────────────── */
main {
    max-width: 1100px;
    margin: 0 auto;
    padding: 24px;
}

/* ── Cards ──────────────────────────────────────────────────── */
.card {
    background: white;
    border-radius: var(--radius);
    box-shadow: var(--shadow);
    padding: 20px;
    margin-bottom: 16px;
}
.card h2 {
    font-size: 15px;
    font-weight: 600;
    color: var(--green-deep);
    margin-bottom: 14px;
    display: flex;
    align-items: center;
    gap: 8px;
}

/* ── Two-column grid ────────────────────────────────────────── */
.grid-2 {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 16px;
}
@media (max-width: 700px) {
    .grid-2 { grid-template-columns: 1fr; }
}

/* ── Funnel bars ────────────────────────────────────────────── */
.funnel-row {
    display: flex;
    align-items: center;
    margin-bottom: 10px;
    gap: 10px;
}
.funnel-label {
    width: 80px;
    font-size: 13px;
    font-weight: 500;
    text-transform: capitalize;
}
.funnel-bar-track {
    flex: 1;
    height: 24px;
    background: var(--gray-100);
    border-radius: 4px;
    overflow: hidden;
    position: relative;
}
.funnel-bar-fill {
    height: 100%;
    border-radius: 4px;
    transition: width 0.4s ease;
    min-width: 2px;
}
.funnel-bar-fill.research { background: var(--status-research); }
.funnel-bar-fill.spotted  { background: var(--status-spotted); }
.funnel-bar-fill.html     { background: var(--status-html); }
.funnel-count {
    width: 40px;
    font-size: 14px;
    font-weight: 600;
    text-align: right;
}
.funnel-total {
    font-size: 22px;
    font-weight: 700;
    color: var(--green-deep);
    margin-bottom: 4px;
}
.funnel-total-label {
    font-size: 12px;
    color: var(--gray-400);
    text-transform: uppercase;
    letter-spacing: 0.5px;
    margin-bottom: 14px;
}

/* ── Attention list ─────────────────────────────────────────── */
.attention-item {
    display: flex;
    align-items: flex-start;
    gap: 10px;
    padding: 10px 0;
    border-bottom: 1px solid var(--gray-100);
    font-size: 13px;
}
.attention-item:last-child { border-bottom: none; }
.attention-id {
    font-family: "SF Mono", Menlo, monospace;
    font-size: 12px;
    color: var(--gray-600);
    background: var(--gray-100);
    padding: 2px 6px;
    border-radius: 3px;
    white-space: nowrap;
}
.attention-name {
    font-weight: 500;
    color: var(--gray-800);
    min-width: 140px;
}
.attention-issues {
    color: var(--gold);
    font-size: 12px;
}
.attention-empty {
    color: var(--gray-400);
    font-style: italic;
    padding: 12px 0;
    font-size: 13px;
}

/* ── Photographer badges ────────────────────────────────────── */
.photog-resolved {
    display: inline-block;
    background: #e8f5e9;
    color: var(--green-mid);
    padding: 2px 8px;
    border-radius: 3px;
    font-size: 12px;
    margin: 2px;
}
.photog-unresolved {
    display: inline-block;
    background: #fff3e0;
    color: #e65100;
    padding: 2px 8px;
    border-radius: 3px;
    font-size: 12px;
    font-family: "SF Mono", Menlo, monospace;
    margin: 2px;
}

/* ── Stub tab content ───────────────────────────────────────── */
.stub-banner {
    background: white;
    border-radius: var(--radius);
    box-shadow: var(--shadow);
    padding: 32px;
    text-align: center;
}
.stub-banner h2 {
    font-size: 18px;
    color: var(--green-deep);
    margin-bottom: 8px;
    justify-content: center;
}
.stub-banner .stub-status {
    display: inline-block;
    background: var(--gray-100);
    color: var(--gray-600);
    padding: 3px 10px;
    border-radius: 12px;
    font-size: 12px;
    font-weight: 500;
    margin-bottom: 16px;
}
.stub-spec {
    text-align: left;
    max-width: 560px;
    margin: 0 auto;
}
.stub-spec h3 {
    font-size: 14px;
    font-weight: 600;
    color: var(--green-deep);
    margin: 16px 0 6px;
}
.stub-spec ul {
    padding-left: 20px;
    font-size: 13px;
    color: var(--gray-600);
}
.stub-spec li { margin-bottom: 4px; }

/* ── Mode toggle (plant/wildlife) ───────────────────────────── */
.mode-toggle {
    display: flex;
    gap: 0;
    background: var(--gray-100);
    border-radius: var(--radius);
    padding: 3px;
    width: fit-content;
    margin-bottom: 20px;
}
.mode-toggle button {
    padding: 6px 18px;
    border: none;
    background: transparent;
    border-radius: 4px;
    font-size: 13px;
    font-weight: 500;
    cursor: pointer;
    color: var(--gray-600);
    transition: all 0.15s;
}
.mode-toggle button.active {
    background: white;
    color: var(--green-deep);
    box-shadow: var(--shadow);
}

/* ── Stat badges ────────────────────────────────────────────── */
.stat-row {
    display: flex;
    gap: 16px;
    flex-wrap: wrap;
    margin-bottom: 16px;
}
.stat-badge {
    background: var(--gray-100);
    border-radius: var(--radius);
    padding: 10px 16px;
    text-align: center;
    min-width: 90px;
}
.stat-badge .stat-num {
    font-size: 20px;
    font-weight: 700;
    color: var(--green-deep);
}
.stat-badge .stat-label {
    font-size: 11px;
    color: var(--gray-400);
    text-transform: uppercase;
    letter-spacing: 0.3px;
}

/* ── Loading state ──────────────────────────────────────────── */
.loading {
    text-align: center;
    padding: 40px;
    color: var(--gray-400);
    font-size: 14px;
}

/* ── Photos tab ────────────────────────────────────────────── */
.photos-layout {
    display: grid;
    grid-template-columns: 280px 1fr;
    gap: 16px;
    align-items: start;
}
@media (max-width: 800px) {
    .photos-layout { grid-template-columns: 1fr; }
}

/* Species picker sidebar */
.species-picker {
    background: white;
    border-radius: var(--radius);
    box-shadow: var(--shadow);
    overflow: hidden;
    position: sticky;
    top: 16px;
    max-height: calc(100vh - 120px);
    display: flex;
    flex-direction: column;
}
.picker-header {
    padding: 14px 16px 10px;
    border-bottom: 1px solid var(--gray-200);
}
.picker-header h3 {
    font-size: 13px;
    font-weight: 600;
    color: var(--green-deep);
    margin-bottom: 8px;
}
.picker-search {
    width: 100%;
    padding: 7px 10px;
    border: 1px solid var(--gray-200);
    border-radius: 4px;
    font-size: 13px;
    outline: none;
}
.picker-search:focus { border-color: var(--green-mid); }
.picker-list {
    overflow-y: auto;
    flex: 1;
}
.picker-item {
    padding: 8px 16px;
    cursor: pointer;
    border-bottom: 1px solid var(--gray-100);
    font-size: 13px;
    transition: background 0.1s;
    display: flex;
    align-items: center;
    gap: 8px;
}
.picker-item:hover { background: var(--gray-100); }
.picker-item.active { background: #e8f5e9; border-left: 3px solid var(--green-mid); }
.picker-item .pi-name {
    flex: 1;
    min-width: 0;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
}
.picker-item .pi-name .pi-common { font-weight: 500; }
.picker-item .pi-name .pi-sci {
    font-size: 11px;
    color: var(--gray-400);
    font-style: italic;
    display: block;
}
.picker-item .pi-badge {
    font-size: 11px;
    padding: 1px 6px;
    border-radius: 8px;
    white-space: nowrap;
    font-weight: 600;
}
.pi-badge.has-photos { background: #e8f5e9; color: var(--green-mid); }
.pi-badge.no-photos { background: var(--gray-100); color: var(--gray-400); }
.pi-hero-dot {
    width: 8px; height: 8px;
    border-radius: 50%;
    background: var(--gold);
    flex-shrink: 0;
}
.pi-hero-dot.none { background: transparent; border: 1px dashed var(--gray-200); }
.picker-empty {
    padding: 20px 16px;
    text-align: center;
    color: var(--gray-400);
    font-size: 13px;
}

/* Photo grid */
.photos-main {
    min-height: 300px;
}
.photos-grid {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(220px, 1fr));
    gap: 14px;
}
.photo-card {
    background: white;
    border-radius: var(--radius);
    box-shadow: var(--shadow);
    overflow: hidden;
    position: relative;
    transition: box-shadow 0.15s;
}
.photo-card:hover { box-shadow: 0 2px 8px rgba(0,0,0,0.12); }
.photo-card.is-hero { box-shadow: 0 0 0 2px var(--gold), var(--shadow); }
.photo-thumb {
    width: 100%;
    aspect-ratio: 4/3;
    object-fit: cover;
    display: block;
    background: var(--gray-100);
}
.photo-thumb-placeholder {
    width: 100%;
    aspect-ratio: 4/3;
    display: flex;
    align-items: center;
    justify-content: center;
    background: var(--gray-100);
    color: var(--gray-400);
    font-size: 28px;
}
.photo-hero-badge {
    position: absolute;
    top: 8px;
    left: 8px;
    background: var(--gold);
    color: white;
    font-size: 11px;
    font-weight: 600;
    padding: 2px 8px;
    border-radius: 3px;
    letter-spacing: 0.3px;
}
.photo-info {
    padding: 10px 12px;
}
.photo-credit {
    font-size: 12px;
    color: var(--gray-600);
    margin-bottom: 4px;
    display: flex;
    align-items: center;
    gap: 4px;
}
.photo-credit .credit-name { font-weight: 500; }
.photo-license {
    font-size: 10px;
    background: var(--gray-100);
    color: var(--gray-600);
    padding: 1px 5px;
    border-radius: 3px;
    font-family: "SF Mono", Menlo, monospace;
    text-transform: uppercase;
}

/* Role tags */
.photo-roles {
    display: flex;
    flex-wrap: wrap;
    gap: 4px;
    margin-top: 6px;
}
.role-tag {
    font-size: 11px;
    padding: 2px 7px;
    border-radius: 3px;
    cursor: pointer;
    border: 1px solid var(--gray-200);
    background: white;
    color: var(--gray-600);
    transition: all 0.1s;
    user-select: none;
}
.role-tag:hover { border-color: var(--green-mid); color: var(--green-mid); }
.role-tag.active {
    background: #e8f5e9;
    color: var(--green-mid);
    border-color: var(--green-mid);
}

/* Photo card actions */
.photo-actions {
    display: flex;
    gap: 0;
    border-top: 1px solid var(--gray-100);
}

/* Gallery toggle */
.gallery-toggle-row {
    margin-bottom: 6px;
}
.gallery-toggle {
    font-size: 11px;
    padding: 2px 8px;
    border-radius: 3px;
    cursor: pointer;
    border: 1px solid;
    background: transparent;
    transition: all 0.1s;
    font-weight: 500;
}
.gallery-toggle.in {
    border-color: var(--green-mid);
    color: var(--green-mid);
    background: #e8f5e9;
}
.gallery-toggle.out {
    border-color: var(--gray-200);
    color: var(--gray-400);
    background: var(--gray-100);
}
.gallery-toggle:hover { opacity: 0.8; }
.photo-action-btn {
    flex: 1;
    padding: 7px 0;
    border: none;
    background: transparent;
    font-size: 12px;
    cursor: pointer;
    color: var(--gray-400);
    transition: all 0.1s;
    display: flex;
    align-items: center;
    justify-content: center;
    gap: 4px;
}
.photo-action-btn:hover { background: var(--gray-100); color: var(--gray-800); }
.photo-action-btn.hero-btn:hover { color: var(--gold); }
.photo-action-btn.hero-btn.is-hero { color: var(--gold); font-weight: 600; }
.photo-action-btn.trash-btn:hover { color: #c62828; }
.photo-action-btn + .photo-action-btn { border-left: 1px solid var(--gray-100); }

/* Species header above grid */
.species-header {
    background: white;
    border-radius: var(--radius);
    box-shadow: var(--shadow);
    padding: 16px 20px;
    margin-bottom: 14px;
    display: flex;
    align-items: center;
    gap: 12px;
}
.species-header h2 {
    font-size: 16px;
    margin: 0;
}
.species-header .sh-sci {
    font-size: 13px;
    color: var(--gray-400);
    font-style: italic;
}
.species-header .sh-id {
    font-family: "SF Mono", Menlo, monospace;
    font-size: 12px;
    color: var(--gray-600);
    background: var(--gray-100);
    padding: 2px 8px;
    border-radius: 3px;
}
.species-header .sh-count {
    margin-left: auto;
    font-size: 13px;
    color: var(--gray-400);
}
.photos-empty {
    text-align: center;
    padding: 48px 20px;
    color: var(--gray-400);
    font-size: 14px;
}
.photos-select-prompt {
    background: white;
    border-radius: var(--radius);
    box-shadow: var(--shadow);
    text-align: center;
    padding: 60px 24px;
    color: var(--gray-400);
}
.photos-select-prompt .psp-icon { font-size: 32px; margin-bottom: 8px; }
.photos-select-prompt p { font-size: 14px; margin: 4px 0; }

/* Toast notification */
.toast {
    position: fixed;
    bottom: 24px;
    right: 24px;
    background: var(--green-deep);
    color: white;
    padding: 10px 18px;
    border-radius: var(--radius);
    font-size: 13px;
    box-shadow: 0 4px 12px rgba(0,0,0,0.2);
    z-index: 1000;
    opacity: 0;
    transform: translateY(10px);
    transition: all 0.2s;
}
.toast.show { opacity: 1; transform: translateY(0); }
.toast.error { background: #c62828; }
"""


def page_shell(active_tab_id, body_html):
    """Wrap tab content in the shared page shell (header, nav, CSS)."""
    nav_items = ""
    for tab in TABS:
        cls = "active" if tab["id"] == active_tab_id else ""
        nav_items += (
            f'<a href="{tab["route"]}" class="{cls}">'
            f'<span class="tab-icon">{tab["icon"]}</span>'
            f'{tab["label"]}</a>\n'
        )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>PSBP Species Manager</title>
    <style>{BRAND_CSS}</style>
</head>
<body>
    <header>
        <h1>🌿 PSBP Species Manager</h1>
        <span class="subtitle">port {PORT}</span>
    </header>
    <nav class="tabs">
        {nav_items}
    </nav>
    <main>
        {body_html}
    </main>
</body>
</html>"""


# ╔══════════════════════════════════════════════════════════════════════════╗
# ║  TAB RENDERERS                                                         ║
# ║                                                                        ║
# ║  Each render_*() returns an HTML string for the <main> content.        ║
# ║  Add new tabs here and register in PAGE_ROUTES below.                  ║
# ╚══════════════════════════════════════════════════════════════════════════╝

def render_overview():
    """Overview tab — pipeline funnel and attention flags. Loads via fetch()."""
    return """
    <div id="overview-loading" class="loading">Loading dashboard…</div>
    <div id="overview-content" style="display:none;">

        <!-- Quick stats row -->
        <div class="stat-row" id="stat-row"></div>

        <!-- Pipeline funnels — two columns -->
        <div class="grid-2">
            <div class="card" id="plants-funnel">
                <h2>🌱 Plants</h2>
                <div id="plants-funnel-body"></div>
            </div>
            <div class="card" id="wildlife-funnel">
                <h2>🦎 Wildlife</h2>
                <div id="wildlife-funnel-body"></div>
            </div>
        </div>

        <!-- Attention items -->
        <div class="grid-2">
            <div class="card">
                <h2>⚠️ Plants Needing Attention</h2>
                <div id="plants-attention"></div>
            </div>
            <div class="card">
                <h2>⚠️ Wildlife Needing Attention</h2>
                <div id="wildlife-attention"></div>
            </div>
        </div>

        <!-- Photographers -->
        <div class="card">
            <h2>📸 Photographer Registry</h2>
            <div id="photog-status"></div>
        </div>
    </div>

    <script>
    async function loadOverview() {
        try {
            const resp = await fetch('/api/overview');
            const data = await resp.json();
            renderOverview(data);
        } catch (err) {
            document.getElementById('overview-loading').textContent =
                'Error loading data: ' + err.message;
        }
    }

    function renderOverview(data) {
        document.getElementById('overview-loading').style.display = 'none';
        document.getElementById('overview-content').style.display = 'block';

        // Quick stats
        const totalSpecies = data.plants.total + data.wildlife.total;
        const totalPublished = (data.plants.by_status.html || 0) +
                               (data.wildlife.by_status.html || 0);
        const totalSpotted = (data.plants.by_status.spotted || 0) +
                             (data.wildlife.by_status.spotted || 0);
        document.getElementById('stat-row').innerHTML = `
            <div class="stat-badge">
                <div class="stat-num">${totalSpecies}</div>
                <div class="stat-label">Total Species</div>
            </div>
            <div class="stat-badge">
                <div class="stat-num">${totalPublished}</div>
                <div class="stat-label">Published</div>
            </div>
            <div class="stat-badge">
                <div class="stat-num">${totalSpotted}</div>
                <div class="stat-label">Spotted</div>
            </div>
            <div class="stat-badge">
                <div class="stat-num">${data.photographers.total_photos}</div>
                <div class="stat-label">Photos</div>
            </div>
            <div class="stat-badge">
                <div class="stat-num">${data.photographers.total_logins}</div>
                <div class="stat-label">Photographers</div>
            </div>
        `;

        // Funnels
        renderFunnel('plants-funnel-body', data.plants);
        renderFunnel('wildlife-funnel-body', data.wildlife);

        // Attention
        renderAttention('plants-attention', data.plants.attention);
        renderAttention('wildlife-attention', data.wildlife.attention);

        // Photographers
        renderPhotographers(data.photographers);
    }

    function renderFunnel(containerId, kingdomData) {
        const el = document.getElementById(containerId);
        const total = kingdomData.total || 1;
        const statuses = ['research', 'spotted', 'html'];
        let html = `<div class="funnel-total">${kingdomData.total}</div>`;
        html += `<div class="funnel-total-label">total species</div>`;

        for (const status of statuses) {
            const count = kingdomData.by_status[status] || 0;
            const pct = Math.round((count / total) * 100);
            html += `
                <div class="funnel-row">
                    <span class="funnel-label">${status}</span>
                    <div class="funnel-bar-track">
                        <div class="funnel-bar-fill ${status}"
                             style="width: ${Math.max(pct, 1)}%"></div>
                    </div>
                    <span class="funnel-count">${count}</span>
                </div>
            `;
        }

        // Show any other statuses that aren't in the standard three
        for (const [status, count] of Object.entries(kingdomData.by_status)) {
            if (!statuses.includes(status)) {
                const pct = Math.round((count / total) * 100);
                html += `
                    <div class="funnel-row">
                        <span class="funnel-label">${status}</span>
                        <div class="funnel-bar-track">
                            <div class="funnel-bar-fill"
                                 style="width: ${Math.max(pct, 1)}%; background: #aaa"></div>
                        </div>
                        <span class="funnel-count">${count}</span>
                    </div>
                `;
            }
        }
        el.innerHTML = html;
    }

    function renderAttention(containerId, items) {
        const el = document.getElementById(containerId);
        if (!items || items.length === 0) {
            el.innerHTML = '<div class="attention-empty">All clear — nothing needs attention.</div>';
            return;
        }
        let html = '';
        for (const item of items) {
            html += `
                <div class="attention-item">
                    <span class="attention-id">${item.id}</span>
                    <span class="attention-name">${item.name}</span>
                    <span class="attention-issues">${item.issues.join(' · ')}</span>
                </div>
            `;
        }
        el.innerHTML = html;
    }

    function renderPhotographers(photog) {
        const el = document.getElementById('photog-status');
        let html = `<div style="margin-bottom: 10px; font-size: 13px;">
            <strong>${photog.resolved}</strong> of <strong>${photog.total_logins}</strong>
            photographer handles resolved to real names
        </div>`;

        if (photog.unresolved.length > 0) {
            html += '<div style="margin-top: 8px;">';
            html += '<span style="font-size: 12px; color: var(--gray-600); margin-right: 6px;">Unresolved:</span>';
            for (const handle of photog.unresolved) {
                html += `<span class="photog-unresolved">${handle}</span> `;
            }
            html += '</div>';
            html += `<div style="margin-top: 8px; font-size: 12px; color: var(--gray-400);">
                Add real names in <code>data/sources/photographer_names.json</code>, then propagate.
            </div>`;
        } else {
            html += '<div class="photog-resolved">All handles resolved ✓</div>';
        }
        el.innerHTML = html;
    }

    loadOverview();
    </script>
    """


def render_stub(tab_id, title, features, pipeline_step):
    """
    Render a stub tab with its planned feature spec.

    This gives Randy a preview of what each tab will do, and gives
    future Claudes a clear build spec right in the UI.
    """
    li_items = "\n".join(f"<li>{f}</li>" for f in features)
    return f"""
    <div class="stub-banner">
        <h2>{title}</h2>
        <div class="stub-status">Coming next · {pipeline_step}</div>
        <div class="stub-spec">
            <h3>Planned features</h3>
            <ul>{li_items}</ul>
            <h3>How to build this tab</h3>
            <ul>
                <li>Add API handlers in the <strong>API HANDLERS</strong> section</li>
                <li>Replace this function's content in the <strong>TAB RENDERERS</strong> section</li>
                <li>See <code>SPECIES_MANAGER.md</code> for the full spec and data flow</li>
            </ul>
        </div>
    </div>
    """


def render_intake():
    return render_stub("intake", "📥 Intake — Import Species from iNat", [
        "Paste an iNat observation URL → auto-extract taxon, names, photo",
        "Fuzzy duplicate check against existing signage entries",
        "Mint next available PSBP-xxxxx ID",
        "Plant / Wildlife mode toggle (determines target JSON)",
        "Write new entry to signage JSON with status <code>spotted</code>",
    ], "Pipeline step 1 of 4")


def render_photos():
    """Photos tab — review mode: browse, crown heroes, tag roles, trash."""
    plant_tags_js = json.dumps(PLANT_PHOTO_TAGS)
    wildlife_tags_js = json.dumps(WILDLIFE_PHOTO_TAGS)
    return f"""
    <!-- Kingdom toggle -->
    <div class="mode-toggle" id="photos-mode-toggle">
        <button class="active" onclick="switchKingdom('plants')">🌱 Plants</button>
        <button onclick="switchKingdom('wildlife')">🦎 Wildlife</button>
    </div>

    <div class="photos-layout">
        <!-- Species picker sidebar -->
        <div class="species-picker">
            <div class="picker-header">
                <h3>Select Species</h3>
                <input type="text" class="picker-search" id="picker-search"
                       placeholder="Filter by name or ID…"
                       oninput="filterPicker()">
            </div>
            <div class="picker-list" id="picker-list">
                <div class="loading">Loading…</div>
            </div>
        </div>

        <!-- Photo grid area -->
        <div class="photos-main" id="photos-main">
            <div class="photos-select-prompt">
                <div class="psp-icon">📷</div>
                <p>Select a species to manage its photos</p>
                <p style="font-size:12px;">Crown heroes, tag roles, review gallery</p>
            </div>
        </div>
    </div>

    <!-- Toast -->
    <div class="toast" id="toast"></div>

    <script>
    // ── State ──────────────────────────────────────────────────
    let currentKingdom = 'plants';
    let currentSpeciesId = null;
    let allSpecies = [];
    const PLANT_TAGS = {plant_tags_js};
    const WILDLIFE_TAGS = {wildlife_tags_js};

    // ── Toast helper ──────────────────────────────────────────
    function toast(msg, isError) {{
        const el = document.getElementById('toast');
        el.textContent = msg;
        el.className = 'toast show' + (isError ? ' error' : '');
        clearTimeout(el._tid);
        // Longer display for longer messages
        const dur = Math.max(2200, msg.length * 50);
        el._tid = setTimeout(() => el.className = 'toast', dur);
    }}

    // ── Kingdom toggle ────────────────────────────────────────
    function switchKingdom(k) {{
        currentKingdom = k;
        currentSpeciesId = null;
        const btns = document.querySelectorAll('#photos-mode-toggle button');
        btns.forEach(b => b.classList.remove('active'));
        btns[k === 'plants' ? 0 : 1].classList.add('active');
        loadPickerList();
        document.getElementById('photos-main').innerHTML = `
            <div class="photos-select-prompt">
                <div class="psp-icon">📷</div>
                <p>Select a species to manage its photos</p>
            </div>`;
    }}

    // ── Species picker ────────────────────────────────────────
    async function loadPickerList() {{
        const list = document.getElementById('picker-list');
        list.innerHTML = '<div class="loading">Loading…</div>';
        try {{
            const resp = await fetch(`/api/photos/summary?kingdom=${{currentKingdom}}`);
            const data = await resp.json();
            allSpecies = data.species || [];
            renderPicker(allSpecies);
        }} catch (err) {{
            list.innerHTML = '<div class="picker-empty">Error loading species</div>';
        }}
    }}

    function renderPicker(species) {{
        const list = document.getElementById('picker-list');
        if (!species.length) {{
            list.innerHTML = '<div class="picker-empty">No species found</div>';
            return;
        }}
        let html = '';
        for (const sp of species) {{
            const active = sp.id === currentSpeciesId ? ' active' : '';
            const badgeCls = sp.photo_count > 0 ? 'has-photos' : 'no-photos';
            const heroCls = sp.has_hero ? '' : ' none';
            html += `
                <div class="picker-item${{active}}" onclick="selectSpecies('${{sp.id}}')" data-id="${{sp.id}}">
                    <div class="pi-hero-dot${{heroCls}}" title="${{sp.has_hero ? 'Has hero' : 'No hero'}}"></div>
                    <div class="pi-name">
                        <span class="pi-common">${{esc(sp.common_name || sp.id)}}</span>
                        <span class="pi-sci">${{esc(sp.scientific_name)}}</span>
                    </div>
                    <span class="pi-badge ${{badgeCls}}">${{sp.photo_count}}</span>
                </div>`;
        }}
        list.innerHTML = html;
    }}

    function filterPicker() {{
        const q = document.getElementById('picker-search').value.toLowerCase().trim();
        if (!q) {{
            renderPicker(allSpecies);
            return;
        }}
        const filtered = allSpecies.filter(sp =>
            sp.common_name.toLowerCase().includes(q) ||
            sp.scientific_name.toLowerCase().includes(q) ||
            sp.id.toLowerCase().includes(q)
        );
        renderPicker(filtered);
    }}

    // ── Select & load photos for a species ────────────────────
    async function selectSpecies(id) {{
        currentSpeciesId = id;
        // Update picker active state
        document.querySelectorAll('.picker-item').forEach(el => {{
            el.classList.toggle('active', el.dataset.id === id);
        }});
        await loadPhotos(id);
    }}

    async function loadPhotos(id) {{
        const main = document.getElementById('photos-main');
        main.innerHTML = '<div class="loading">Loading photos…</div>';
        try {{
            const resp = await fetch(`/api/photos/species?id=${{id}}`);
            const data = await resp.json();
            if (data.error) {{
                main.innerHTML = `<div class="photos-empty">${{data.error}}</div>`;
                return;
            }}
            renderPhotosGrid(id, data.photos);
        }} catch (err) {{
            main.innerHTML = '<div class="photos-empty">Error loading photos</div>';
        }}
    }}

    // ── Render the photo grid ─────────────────────────────────
    function renderPhotosGrid(speciesId, photos) {{
        const main = document.getElementById('photos-main');
        // Find species info from picker data
        const sp = allSpecies.find(s => s.id === speciesId) || {{}};
        const tags = currentKingdom === 'plants' ? PLANT_TAGS : WILDLIFE_TAGS;

        let html = `
            <div class="species-header">
                <span class="sh-id">${{speciesId}}</span>
                <div>
                    <h2>${{esc(sp.common_name || speciesId)}}</h2>
                    <span class="sh-sci">${{esc(sp.scientific_name || '')}}</span>
                </div>
                <span class="sh-count">${{photos.length}} photo${{photos.length !== 1 ? 's' : ''}}</span>
            </div>`;

        if (photos.length === 0) {{
            html += `<div class="photos-empty">
                No photos in the registry for this species.<br>
                <span style="font-size:12px;color:var(--gray-400);">
                    Use the Triage workflow to import photos from iNaturalist.
                </span>
            </div>`;
            main.innerHTML = html;
            return;
        }}

        html += '<div class="photos-grid">';
        for (const photo of photos) {{
            const isHero = photo.hero === true;
            const pid = photo.photo_id || '';
            const roles = photo.role || [];
            const contentTags = roles.filter(r => r !== 'gallery');
            const license = (photo.license || '').toUpperCase();

            html += `<div class="photo-card${{isHero ? ' is-hero' : ''}}" data-roles='${{JSON.stringify(roles)}}'>`;

            // Thumbnail — use focus point for object-position if available
            const focus = photo.focus && photo.focus !== 'None' ? photo.focus : '50% 50%';
            if (photo.thumb_url) {{
                html += `<img class="photo-thumb" src="${{esc(photo.thumb_url)}}"
                              alt="${{esc(photo.resolved_name)}}"
                              loading="lazy"
                              style="object-position: ${{focus}}"
                              onerror="imgFail(this)">`;
            }} else {{
                html += '<div class="photo-thumb-placeholder">🖼</div>';
            }}

            // Hero badge
            if (isHero) {{
                html += '<div class="photo-hero-badge">HERO</div>';
            }}

            // Info section
            html += '<div class="photo-info">';

            // Credit line
            html += `<div class="photo-credit">
                <span class="credit-name">${{esc(photo.resolved_name)}}</span>
                ${{license ? `<span class="photo-license">${{esc(license)}}</span>` : ''}}
            </div>`;

            // Gallery toggle — structural, separate from content tags
            const inGallery = roles.includes('gallery');
            html += `<div class="gallery-toggle-row">
                <button class="gallery-toggle ${{inGallery ? 'in' : 'out'}}"
                        onclick="toggleGallery(this, '${{speciesId}}', '${{pid}}')"
                        title="${{inGallery ? 'Photo appears in page gallery' : 'Photo is NOT in the page gallery'}}">
                    ${{inGallery ? '✓ In gallery' : '✗ Not in gallery'}}
                </button>
            </div>`;

            // Content tags (what the photo shows)
            html += `<div class="photo-roles" data-pid="${{pid}}" data-sid="${{speciesId}}">`;
            for (const tag of tags) {{
                const isActive = contentTags.includes(tag);
                html += `<span class="role-tag${{isActive ? ' active' : ''}}"
                               onclick="toggleRole(this, '${{speciesId}}', '${{pid}}', '${{tag}}')"
                               data-tag="${{tag}}">${{tag}}</span>`;
            }}
            html += '</div>';

            html += '</div>'; // .photo-info

            // Action buttons
            html += `<div class="photo-actions">
                <button class="photo-action-btn hero-btn${{isHero ? ' is-hero' : ''}}"
                        onclick="crownHero('${{speciesId}}', '${{pid}}')"
                        title="${{isHero ? 'Current hero' : 'Crown as hero'}}">
                    ${{isHero ? '★ Hero' : '☆ Crown'}}
                </button>
                <button class="photo-action-btn"
                        onclick="window.open('https://www.inaturalist.org/photos/${{pid}}', '_blank')"
                        title="View on iNaturalist">
                    ↗ iNat
                </button>
                <button class="photo-action-btn trash-btn"
                        onclick="trashPhoto('${{speciesId}}', '${{pid}}')"
                        title="Remove from registry">
                    ✕ Remove
                </button>
            </div>`;

            html += '</div>'; // .photo-card
        }}
        html += '</div>'; // .photos-grid
        main.innerHTML = html;
    }}

    // ── Actions ───────────────────────────────────────────────
    async function crownHero(speciesId, photoId) {{
        if (!confirm('Swap hero? This will download the new photo, delete the old, and update the HTML page and search index.')) return;
        // Show working state
        const btns = document.querySelectorAll('.hero-btn');
        btns.forEach(b => {{ b.disabled = true; b.textContent = '⏳ Working…'; }});
        toast('Swapping hero — downloading photo…');

        try {{
            const resp = await fetch('/api/photos/hero', {{
                method: 'POST',
                headers: {{'Content-Type': 'application/json'}},
                body: JSON.stringify({{psbp_id: speciesId, photo_id: photoId}})
            }});
            const data = await resp.json();
            if (data.error) {{
                toast(data.error, true);
                btns.forEach(b => {{ b.disabled = false; }});
                return;
            }}

            // Build result message from pipeline steps
            const s = data.steps || {{}};
            let msg = 'Hero swapped';
            if (s.downloaded && !String(s.downloaded).startsWith('Error'))
                msg += ' ✓ downloaded';
            if (s.search_index && !String(s.search_index).startsWith('Error'))
                msg += ' ✓ index';
            if (s.html_patched && !String(s.html_patched).startsWith('Error'))
                msg += ' ✓ HTML';
            if (data.warning) msg += ' ⚠ ' + data.warning;
            toast(msg);

            // Log full report to console for debugging
            console.log('Hero swap report:', data);

            await loadPhotos(speciesId);
            // Refresh picker to update hero dot
            const spIdx = allSpecies.findIndex(sp => sp.id === speciesId);
            if (spIdx >= 0) allSpecies[spIdx].has_hero = true;
            renderPicker(allSpecies);
            document.querySelector(`.picker-item[data-id="${{speciesId}}"]`)?.classList.add('active');
        }} catch (err) {{
            toast('Error: ' + err.message, true);
        }} finally {{
            const btns2 = document.querySelectorAll('.hero-btn');
            btns2.forEach(b => {{ b.disabled = false; }});
        }}
    }}

    async function toggleRole(el, speciesId, photoId, tag) {{
        // Read current active tags from sibling elements
        const container = el.parentElement;
        const tagEls = container.querySelectorAll('.role-tag');
        // Preserve roles that aren't content tags (e.g. 'gallery')
        const allContentTags = currentKingdom === 'plants' ? PLANT_TAGS : WILDLIFE_TAGS;
        // Get the photo's current roles from the API data
        // Build new roles: keep non-content roles, add active content tags
        let roles = [];
        // We need the original role array — find it from the last API fetch
        const card = container.closest('.photo-card');
        const origRoles = JSON.parse(card.dataset.roles || '[]');
        // Keep any role that's not a content tag (e.g. 'gallery')
        for (const r of origRoles) {{
            if (!allContentTags.includes(r)) roles.push(r);
        }}
        // Add content tags based on toggle state
        tagEls.forEach(t => {{
            if (t.dataset.tag === tag) {{
                // Toggle this one
                if (!t.classList.contains('active')) roles.push(tag);
            }} else if (t.classList.contains('active')) {{
                roles.push(t.dataset.tag);
            }}
        }});

        try {{
            const resp = await fetch('/api/photos/roles', {{
                method: 'POST',
                headers: {{'Content-Type': 'application/json'}},
                body: JSON.stringify({{psbp_id: speciesId, photo_id: photoId, roles: roles}})
            }});
            const data = await resp.json();
            if (data.error) {{ toast(data.error, true); return; }}
            // Update stored roles on the card
            card.dataset.roles = JSON.stringify(data.roles || roles);
            // Toggle the tag visually without full reload
            el.classList.toggle('active');
        }} catch (err) {{ toast('Error: ' + err.message, true); }}
    }}

    async function toggleGallery(el, speciesId, photoId) {{
        const card = el.closest('.photo-card');
        const origRoles = JSON.parse(card.dataset.roles || '[]');
        let roles;
        const wasIn = origRoles.includes('gallery');
        if (wasIn) {{
            roles = origRoles.filter(r => r !== 'gallery');
        }} else {{
            roles = ['gallery', ...origRoles];
        }}

        try {{
            const resp = await fetch('/api/photos/roles', {{
                method: 'POST',
                headers: {{'Content-Type': 'application/json'}},
                body: JSON.stringify({{psbp_id: speciesId, photo_id: photoId, roles: roles}})
            }});
            const data = await resp.json();
            if (data.error) {{ toast(data.error, true); return; }}
            card.dataset.roles = JSON.stringify(data.roles || roles);
            const nowIn = !wasIn;
            el.className = 'gallery-toggle ' + (nowIn ? 'in' : 'out');
            el.textContent = nowIn ? '✓ In gallery' : '✗ Not in gallery';
            el.title = nowIn ? 'Photo appears in page gallery' : 'Photo is NOT in the page gallery';
            toast(nowIn ? 'Added to gallery' : 'Removed from gallery');
        }} catch (err) {{ toast('Error: ' + err.message, true); }}
    }}

    async function trashPhoto(speciesId, photoId) {{
        if (!confirm('Remove this photo from the registry? (File on disk is not deleted.)')) return;
        try {{
            const resp = await fetch('/api/photos/trash', {{
                method: 'POST',
                headers: {{'Content-Type': 'application/json'}},
                body: JSON.stringify({{psbp_id: speciesId, photo_id: photoId}})
            }});
            const data = await resp.json();
            if (data.error) {{ toast(data.error, true); return; }}
            toast('Photo removed');
            await loadPhotos(speciesId);
            loadPickerList();
        }} catch (err) {{ toast('Error: ' + err.message, true); }}
    }}

    // ── Utility ───────────────────────────────────────────────
    function esc(s) {{
        if (!s) return '';
        const d = document.createElement('div');
        d.textContent = s;
        return d.innerHTML;
    }}
    function imgFail(el) {{
        const ph = document.createElement('div');
        ph.className = 'photo-thumb-placeholder';
        ph.textContent = '🖼';
        el.replaceWith(ph);
    }}

    // ── Init ──────────────────────────────────────────────────
    loadPickerList();
    </script>
    """


def render_edit():
    return render_stub("edit", "✏️ Edit & Preview — Signage Content", [
        "Species picker filtered by status (spotted + html)",
        "Editable form for all signage fields (names, description, quick hits, native status…)",
        "Live HTML preview panel — see the page before publishing",
        "Field-level validation with required fields highlighted",
        "Save edits back to signage JSON",
    ], "Pipeline step 3 of 4")


def render_publish():
    return render_stub("publish", "🚀 Publish — Promote & Demote", [
        "Species list with readiness indicators (hero ✓ credits ✓ fields ✓)",
        "One-click promote: spotted → html (generate HTML, stamp credits, update search index)",
        "One-click demote: html → spotted (delete HTML, clean search index)",
        "Re-generate all button with confirmation gate",
        "Replaces <code>plant_publisher.py</code> (port 8701) and <code>wildlife_publisher.py</code> (port 8702)",
    ], "Pipeline step 4 of 4")


# ╔══════════════════════════════════════════════════════════════════════════╗
# ║  HTTP SERVER                                                           ║
# ║                                                                        ║
# ║  Routing: PAGE_ROUTES for HTML pages, API_ROUTES for JSON endpoints.   ║
# ║  Add new routes to the appropriate dict.                               ║
# ╚══════════════════════════════════════════════════════════════════════════╝

# Page routes: path → (tab_id, render_function)
PAGE_ROUTES = {
    "/":        ("overview", render_overview),
    "/intake":  ("intake",   render_intake),
    "/photos":  ("photos",   render_photos),
    "/edit":    ("edit",     render_edit),
    "/publish": ("publish",  render_publish),
}

# API routes: path → handler_function
API_ROUTES = {
    "/api/overview":         handle_api_overview,
    "/api/species":          handle_api_species_list,
    "/api/intake/check":     handle_api_intake_check,
    "/api/photos/species":   handle_api_photos_species,
    "/api/photos/summary":   handle_api_photos_summary,
    "/api/photos/hero":      handle_api_photos_set_hero,
    "/api/photos/roles":     handle_api_photos_update_roles,
    "/api/photos/trash":     handle_api_photos_trash,
    "/api/photos/debug":     handle_api_photos_debug,
    "/api/preview":          handle_api_preview,
    "/api/publish/ready":    handle_api_publish_ready,
}


class DashboardHandler(BaseHTTPRequestHandler):
    """HTTP request handler for the species manager dashboard."""

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        params = parse_qs(parsed.query)

        # Static photo file serving: /photos-file/PSBP-xxxxx/filename.jpg
        if path.startswith("/photos-file/"):
            self._serve_photo_file(path[len("/photos-file/"):])
            return

        # API routes
        if path in API_ROUTES:
            try:
                result = API_ROUTES[path](params)
                self._json_response(200, result)
            except Exception as e:
                self._json_response(500, {"error": str(e)})
            return

        # Page routes
        if path in PAGE_ROUTES:
            tab_id, render_fn = PAGE_ROUTES[path]
            body = render_fn()
            html = page_shell(tab_id, body)
            self._html_response(200, html)
            return

        # 404
        self._html_response(404, page_shell("overview",
            '<div class="stub-banner"><h2>404 — Page not found</h2></div>'))

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        params = parse_qs(parsed.query)

        # Read POST body
        content_len = int(self.headers.get("Content-Length", 0))
        if content_len > 0:
            body = self.rfile.read(content_len)
            try:
                params["_body"] = json.loads(body)
            except json.JSONDecodeError:
                pass

        if path in API_ROUTES:
            try:
                result = API_ROUTES[path](params)
                self._json_response(200, result)
            except Exception as e:
                self._json_response(500, {"error": str(e)})
            return

        self._json_response(404, {"error": "Not found"})

    def _json_response(self, status, data):
        body = json.dumps(data, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", len(body))
        self.end_headers()
        self.wfile.write(body)

    def _html_response(self, status, html):
        body = html.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", len(body))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        """Quieter logging — just method and path."""
        print(f"  {args[0]}" if args else "")

    def _serve_photo_file(self, rel_path):
        """Serve a photo file from the local photos directory."""
        import mimetypes
        # Sanitize: no directory traversal
        clean = os.path.normpath(rel_path)
        if clean.startswith("..") or clean.startswith("/"):
            self._html_response(403, "Forbidden")
            return
        full = os.path.join(PHOTOS_DIR, clean)
        if not os.path.isfile(full):
            self._html_response(404, "Not found")
            return
        mime = mimetypes.guess_type(full)[0] or "application/octet-stream"
        try:
            with open(full, "rb") as f:
                data = f.read()
            self.send_response(200)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", len(data))
            self.send_header("Cache-Control", "max-age=3600")
            self.end_headers()
            self.wfile.write(data)
        except IOError:
            self._html_response(500, "Read error")


def main():
    port = PORT
    if "--port" in sys.argv:
        idx = sys.argv.index("--port")
        if idx + 1 < len(sys.argv):
            port = int(sys.argv[idx + 1])

    server = HTTPServer(("", port), DashboardHandler)
    print(f"╔══════════════════════════════════════════════════╗")
    print(f"║  PSBP Species Manager                           ║")
    print(f"║  http://localhost:{port:<5}                       ║")
    print(f"╠══════════════════════════════════════════════════╣")
    print(f"║  Overview .... http://localhost:{port}/           ║")
    print(f"║  Intake ...... http://localhost:{port}/intake     ║")
    print(f"║  Photos ...... http://localhost:{port}/photos     ║")
    print(f"║  Edit ........ http://localhost:{port}/edit       ║")
    print(f"║  Publish ..... http://localhost:{port}/publish    ║")
    print(f"╚══════════════════════════════════════════════════╝")
    print(f"  Data: {REPO}")
    print(f"  Ctrl+C to stop\n")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down.")
        server.server_close()


if __name__ == "__main__":
    main()
