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
import time
import datetime
import threading
import subprocess
from http.server import HTTPServer, ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs, quote
from urllib.request import urlopen, Request
import urllib.error

# ╔══════════════════════════════════════════════════════════════════════════╗
# ║  CONFIGURATION                                                         ║
# ╚══════════════════════════════════════════════════════════════════════════╝

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from psbp_common import (
    REPO, write_json_atomic, display_name, build_credit_line,
    load_json, PHOTO_CREDITS_JSON, resolve_hero_credit, CC_LICENSES,
)

# Import the proven publisher modules for HTML generation + index updates.
# These are the SINGLE SOURCE OF TRUTH for page output — the Publish tab calls
# their functions directly rather than re-implementing the templates. Importing
# is safe: all CLI/server behavior is behind their __main__ guards.
try:
    import plant_publisher
    import wildlife_publisher
    PUBLISHERS_OK = True
    PUBLISHER_IMPORT_ERROR = ""
except Exception as _e:  # pragma: no cover
    PUBLISHERS_OK = False
    PUBLISHER_IMPORT_ERROR = str(_e)
    plant_publisher = None
    wildlife_publisher = None

PORT = 8700

# Data source paths (all relative to REPO)
PLANT_SIGNAGE      = os.path.join(REPO, "data", "sources", "plant_signage.json")
WILDLIFE_SIGNAGE   = os.path.join(REPO, "data", "sources", "wildlife_signage.json")
PHOTO_CREDITS      = os.path.join(REPO, "data", "sources", "photo_credits.json")
PHOTO_WORKBENCH    = os.path.join(REPO, "data", "sources", "photo_workbench.json")
PHOTOGRAPHER_NAMES = os.path.join(REPO, "data", "sources", "photographer_names.json")
PLANTS_INDEX       = os.path.join(REPO, "plants.json")
WILDLIFE_INDEX     = os.path.join(REPO, "wildlife.json")
PHOTOS_DIR         = os.path.join(REPO, "photos")
RESEARCH_JSON      = os.path.join(REPO, "data", "sources", "research.json")

# ── iNaturalist triage config ──────────────────────────────────────────────
# Project slug from the URL: inaturalist.org/projects/<THIS-PART>
# iNat accepts the slug directly as the project_id query parameter.
# Override with the INAT_PROJECT_ID env var if needed.
INAT_PROJECT_ID = os.environ.get("INAT_PROJECT_ID", "palma-sola-botanical-park")

# Curated iNat place drawn for the park boundary (inaturalist.org/places/233156).
# RETAINED FOR REFERENCE ONLY — the photo scan and intake check query the
# PROJECT (project_id), not this place, because project membership includes
# obscured observations whose public pin falls outside the boundary (a place
# query silently drops those). Kept here in case a future place-based helper
# wants it. Override with INAT_PLACE_ID if the place ever changes.
INAT_PLACE_ID = os.environ.get("INAT_PLACE_ID", "233156")

# Park centroid — kept for reference / non-scan geographic helpers only.
# NOTE: no longer used for photo scanning (see _inat_observations).
PARK_LAT  = 27.497
PARK_LNG  = -82.619
PARK_RADIUS_KM = 0.5   # ~500m covers the 10-acre park with margin

# Scan cache lives OUTSIDE the repo — throwaway, re-fetchable iNat results.
TRIAGE_WORKSPACE = os.path.expanduser("~/Documents/PSBP_photo_workspace")
TRIAGE_CACHE_DIR = os.path.join(TRIAGE_WORKSPACE, "cache")

API_DELAY      = 1.0   # seconds between iNat API pages
DOWNLOAD_DELAY = 0.3   # seconds between web-res downloads

# ── Hero photo web-res budget ──────────────────────────────────────────────
# Every hero photo is shrunk to a size budget the moment it lands on disk, so
# it clears the repo's pre-commit size guard (<=500 KB hard) with no manual
# step. Uses macOS `sips` (always present on the Mac this dashboard runs on).
# Quality steps down until the file fits; downscales only if huge. Best-effort:
# if sips ever errors, the file is left as-is and the commit guard still backstops.
HERO_TARGET_KB = int(os.environ.get("PSBP_HERO_TARGET_KB", "300"))  # web-res aim
HERO_HARD_KB   = 500    # the guard's ceiling; we flag anything we can't beat
HERO_MAX_DIM   = 1600   # longest side, px; only downscales, never enlarges
HERO_Q_START   = 72     # first JPEG quality tried (highest)
HERO_Q_MIN     = 40     # never drop below this quality
HERO_Q_STEP    = 8      # quality decrement per attempt

# Tab definitions — order matters for the nav bar
TABS = [
    {"id": "overview", "label": "Overview",       "route": "/",        "icon": "📊"},
    {"id": "intake",   "label": "Intake",         "route": "/intake",  "icon": "📥"},
    {"id": "photos",   "label": "Photos",         "route": "/photos",  "icon": "📷"},
    {"id": "cultivated", "label": "Cultivated",   "route": "/cultivated", "icon": "🏷️"},
    {"id": "phenology", "label": "Phenology",     "route": "/phenology", "icon": "🌸"},
    {"id": "publish",  "label": "Preview & Publish", "route": "/publish", "icon": "🚀"},
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
    """Read and parse a JSON file. Returns empty dict on any read/parse trouble.

    This is deliberately forgiving: a file may be read at the exact moment it's
    being rewritten (by a publish, the sync pipeline, or another tool), which can
    raise JSONDecodeError, UnicodeDecodeError, or a transient OSError. Rather than
    let a half-written file crash a whole dashboard page, we log and return {} —
    the caller renders with what it has, and the next refresh picks up the
    finished file. Tolerate one retry for the mid-write race.
    """
    import time as _t
    for attempt in range(2):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except FileNotFoundError:
            print(f"[WARN] File not found: {path}")
            return {}
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            # Possibly mid-write — wait a beat and try once more.
            if attempt == 0:
                _t.sleep(0.15)
                continue
            print(f"[WARN] Unparseable JSON in {path}: {e}")
            return {}
        except OSError as e:
            if attempt == 0:
                _t.sleep(0.15)
                continue
            print(f"[WARN] Could not read {path}: {e}")
            return {}
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
                        "status": status,
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

    def research_breakdown(kingdom):
        """Counts of active research.json candidates for a kingdom, by source."""
        items = [s for s in get_research_list(kingdom)
                 if s.get("status") == "research"]
        by_source = {}
        for s in items:
            src = s.get("source") or "unknown"
            by_source[src] = by_source.get(src, 0) + 1
        return {"total": len(items), "by_source": by_source}

    plants_kd = analyze_kingdom(plant_species, PLANT_REQUIRED, "botanical_name")
    plants_kd["research"] = research_breakdown("plants")
    wildlife_kd = analyze_kingdom(wildlife_species, WILDLIFE_REQUIRED, "scientific_name")
    wildlife_kd["research"] = research_breakdown("wildlife")

    return {
        "plants": plants_kd,
        "wildlife": wildlife_kd,
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


# ── Intake / Research helpers ────────────────────────────────────────────

# Fields that live only in research.json and don't transfer to signage.
# Everything else carries over, with status flipped to "spotted".
_RESEARCH_ONLY_FIELDS = {
    "import_source",      # how it got into research.json
    "research_source",    # which source (inat_observed, inventory, etc.)
    "csv_data",           # bulk import artifact
    "inat_obs_count",     # snapshot count, not maintained
    "observation_stats",  # snapshot stats
    "sources",            # research citation list
    "last_reviewed",      # research review date
    "type",               # implicit from which signage file it lands in
}

# Key content fields per kingdom — used for completeness indicators.
_PLANT_CONTENT_KEYS = [
    "quick_hits", "more_information", "origin", "wildlife_value",
    "reproduction", "growing_conditions", "edibility", "toxicity",
    "alternate_names", "butterfly_host",
]
_WILDLIFE_CONTENT_KEYS = [
    "quick_hits", "more_information", "range_and_origin", "diet",
    "behavior", "habitat", "sounds", "identification",
    "also_known_as", "seasonality", "size",
]


def _is_filled(val):
    """Check if a field value counts as populated."""
    if val is None:
        return False
    if isinstance(val, str):
        return bool(val.strip())
    if isinstance(val, (list, dict)):
        return bool(val)
    return True


def get_research_list(kingdom):
    """Return research.json species filtered by kingdom, sorted by ID.

    Each item includes a content_filled / content_total count for the picker
    badge.  Died/stolen species are included (picker shows them) but the
    promote button disables for non-research status.
    """
    raw = _load(RESEARCH_JSON)
    species = _get_species_list(raw)
    type_val = "plant" if kingdom == "plants" else "wildlife"
    content_keys = _PLANT_CONTENT_KEYS if kingdom == "plants" else _WILDLIFE_CONTENT_KEYS

    result = []
    for sp in sorted(species, key=lambda s: s.get("id", "")):
        if sp.get("type", "plant") != type_val:
            continue
        sci = sp.get("botanical_name", "") if kingdom == "plants" else sp.get("scientific_name", "")
        filled = sum(1 for f in content_keys if _is_filled(sp.get(f)))
        result.append({
            "id":             sp.get("id", ""),
            "common_name":    sp.get("common_name", ""),
            "scientific_name": sci,
            "status":         sp.get("status", "research"),
            "category":       sp.get("category", ""),
            "feature_tier":   sp.get("feature_tier", ""),
            "native":         sp.get("native"),
            "has_taxon":      bool(sp.get("inat_taxon_id")),
            "content_filled": filled,
            "content_total":  len(content_keys),
            "has_sign":       sp.get("has_sign", False),
            "source":         sp.get("research_source", ""),
            "inat_taxon_id":  sp.get("inat_taxon_id"),
            "inat_obs_count": sp.get("inat_obs_count"),
        })
    return result


def get_research_detail(species_id):
    """Return the full record for one research.json species."""
    raw = _load(RESEARCH_JSON)
    species = _get_species_list(raw)
    sp = next((s for s in species if s.get("id") == species_id), None)
    return sp


def _check_intake_duplicates(kingdom, species):
    """Check if this species already exists in the target signage JSON.

    Returns a list of matching entries with the reasons for the match.
    """
    path = PLANT_SIGNAGE if kingdom == "plants" else WILDLIFE_SIGNAGE
    existing = _get_species_list(_load(path))

    sid = species.get("id", "")
    taxon_id = species.get("inat_taxon_id")
    common = (species.get("common_name") or "").lower().strip()
    sci = (species.get("botanical_name") or species.get("scientific_name") or "").lower().strip()

    dupes = []
    for ex in existing:
        reasons = []
        if ex.get("id") == sid:
            reasons.append("Same PSBP ID")
        if taxon_id and ex.get("inat_taxon_id") == taxon_id:
            reasons.append("Same iNat taxon ID")
        ex_common = (ex.get("common_name") or "").lower().strip()
        ex_sci = (ex.get("botanical_name") or ex.get("scientific_name") or "").lower().strip()
        if common and ex_common and ex_common == common:
            reasons.append("Same common name")
        if sci and ex_sci and ex_sci == sci:
            reasons.append("Same scientific name")
        if reasons:
            dupes.append({
                "id":              ex.get("id", ""),
                "common_name":     ex.get("common_name", ""),
                "scientific_name": ex_sci,
                "status":          ex.get("status", ""),
                "reasons":         reasons,
            })
    return dupes


def _auto_import_hero(species_id, kingdom, common_name, scientific_name, taxon_id):
    """Best-effort: grab the best CC photo from iNat as a provisional hero.

    Returns a small report dict and never raises into the promote path. Queries
    only the park project's observations (same source as triage), and only
    CC-licensed photos are eligible — the park publishes CC only. If the species
    already has a hero, has no taxon id, or iNat offers no CC photo, nothing is
    written and the reason is reported so the UI can warn at promote time. The
    imported photo is a placeholder hero — swap/crop it later in the Photos tab.
    """
    if not taxon_id:
        return {"imported": False, "reason": "no iNat taxon id on the record"}

    # Never clobber a hero that already exists.
    credits = _load(PHOTO_CREDITS)
    for p in credits.get("photos", []):
        if p.get("psbp_id") == species_id and p.get("hero"):
            return {"imported": False, "reason": "already has a hero"}

    obs = _inat_observations(taxon_id)
    if not obs:
        return {"imported": False, "reason": "no park observations returned from iNat"}

    cc, non_cc = _cc_photos_from_observations(obs)
    if not cc:
        extra = f" ({non_cc} non-CC found)" if non_cc else ""
        return {"imported": False, "reason": f"no CC photos on iNat{extra} — add your own"}

    best = cc[0]  # newest observation's first CC photo; a placeholder to swap later
    ctype = "Plant" if kingdom == "plants" else "Wildlife"
    payload = {
        "decision":          "promoted",
        "photo_id":          best.get("photo_id", ""),
        "psbp_id":           species_id,
        "kingdom":           kingdom,
        "type":              ctype,
        "common_name":       common_name,
        "scientific_name":   scientific_name,
        "photographer":      best.get("photographer", ""),
        "photographer_name": best.get("photographer_name", ""),
        "license":           best.get("license", ""),
        "large_url":         best.get("large_url", ""),
        "thumb_url":         best.get("thumb_url", ""),
        "source_url":        best.get("source_url", ""),
        "obs_id":            best.get("obs_id", ""),
        "observed_on":       best.get("observed_on"),
        "shared_on":         best.get("shared_on"),
    }
    res = _apply_triage_decision(payload)
    if res.get("ok") and res.get("is_hero"):
        return {"imported": True, "reason": "imported",
                "photo_id":     best.get("photo_id", ""),
                "photographer": best.get("photographer_name") or best.get("photographer", ""),
                "license":      best.get("license", ""),
                "cc_available": len(cc)}
    return {"imported": False,
            "reason": res.get("error", "photo recorded but not as hero"),
            "cc_available": len(cc)}


def promote_to_spotted(species_id, kingdom):
    """Move a species from research.json → signage JSON as 'spotted'.

    Steps:
      1. Load the species from research.json
      2. Block if an exact ID duplicate exists in signage
      3. Build the signage record (strip research-only fields, set status)
      4. Append to signage JSON (sorted by ID, meta updated)
      5. Remove from research.json (meta counts updated)

    Returns {ok, id, common_name, kingdom, duplicates_warned}.
    """
    # Load research
    research = _load(RESEARCH_JSON)
    research_species = _get_species_list(research)
    sp = next((s for s in research_species if s.get("id") == species_id), None)
    if not sp:
        return {"ok": False, "error": f"{species_id} not found in research.json"}

    # Hard-block on exact ID collision
    dupes = _check_intake_duplicates(kingdom, sp)
    id_collisions = [d for d in dupes if "Same PSBP ID" in d["reasons"]]
    if id_collisions:
        return {"ok": False,
                "error": f"{species_id} already exists in {kingdom} signage",
                "duplicates": dupes}

    # Build the signage record — carry over everything except research-only
    record = {}
    for k, v in sp.items():
        if k not in _RESEARCH_ONLY_FIELDS:
            record[k] = v
    record["status"] = "spotted"

    # Write to signage
    path = PLANT_SIGNAGE if kingdom == "plants" else WILDLIFE_SIGNAGE
    signage = _load(path)
    signage.setdefault("species", []).append(record)
    signage["species"].sort(key=lambda s: s.get("id", ""))
    signage.setdefault("meta", {})
    signage["meta"]["species_count"] = len(signage["species"])
    write_json_atomic(path, signage)

    # Remove from research.json
    research["species"] = [s for s in research_species if s.get("id") != species_id]
    research.setdefault("meta", {})
    research["meta"]["species_count"] = len(research["species"])
    plants_left = sum(1 for s in research["species"] if s.get("type") == "plant")
    wildlife_left = sum(1 for s in research["species"] if s.get("type") == "wildlife")
    research["meta"]["plant_count"] = plants_left
    research["meta"]["wildlife_count"] = wildlife_left
    write_json_atomic(RESEARCH_JSON, research)

    # ── Best-effort: pull a CC hero from iNat so the species lands in spotted
    #    already pictured (a placeholder to swap later). The data move above has
    #    already committed; a slow or photo-less iNat must NEVER undo it, so this
    #    is fully wrapped and only ever produces a report.
    hero_report = {"imported": False, "reason": "skipped"}
    try:
        hero_report = _auto_import_hero(
            species_id, kingdom,
            sp.get("common_name", ""),
            sp.get("botanical_name") or sp.get("scientific_name") or "",
            sp.get("inat_taxon_id"),
        )
    except Exception as e:
        hero_report = {"imported": False, "reason": f"import error: {e}"}

    # Non-blocking duplicates to warn about (same name / taxon but different ID)
    warned = [d for d in dupes if "Same PSBP ID" not in d["reasons"]]
    return {
        "ok":                True,
        "id":                species_id,
        "common_name":       sp.get("common_name", ""),
        "kingdom":           kingdom,
        "duplicates_warned": warned,
        "hero_report":       hero_report,
    }

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
    workbench = load_workbench()

    result = []
    for sp in sorted(species_list, key=lambda s: s.get("id", "")):
        sid = sp.get("id", "")
        # Count photos for this species (filter by type if mixed)
        sp_photos = photos_by_species.get(sid, [])
        type_photos = [p for p in sp_photos if p.get("type") == type_filter]
        counts = _triage_counts(kingdom, sid, photos_list, workbench)
        result.append({
            "id": sid,
            "common_name": sp.get("common_name", ""),
            "scientific_name": sp.get(sci_field, ""),
            "status": sp.get("status", "unknown"),
            "photo_count": len(type_photos),
            "has_hero": sid in hero_ids,
            "has_taxon": bool(sp.get("inat_taxon_id")),
            "green": counts["green"],
            "red": counts["red"],
            "yellow": counts["yellow"],
            "scanned": counts["scanned"],
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

def _shrink_hero_file(filepath):
    """Shrink a freshly downloaded hero JPG to the web-res budget, in place.

    Steps JPEG quality down (via macOS `sips`) until the file is at/under
    HERO_TARGET_KB, so it clears the repo's pre-commit size guard. Downscales
    to HERO_MAX_DIM on the longest side only if larger. Atomic replace; never
    inflates. Best-effort: if sips is missing or errors, the original file is
    left untouched and the pre-commit guard remains the backstop.

    Returns a short status string for the console log.
    """
    try:
        target = HERO_TARGET_KB * 1024
        hard = HERO_HARD_KB * 1024
        before = os.path.getsize(filepath)
        if before <= target:
            return f"{before // 1024}KB (already under budget)"

        # Current pixel dimensions — only to decide whether to downscale.
        w = h = 0
        try:
            out = subprocess.run(
                ["sips", "-g", "pixelWidth", "-g", "pixelHeight", filepath],
                capture_output=True, text=True, timeout=20,
            ).stdout
            for line in out.splitlines():
                s = line.strip()
                if s.startswith("pixelWidth:"):
                    w = int(s.split(":")[1])
                elif s.startswith("pixelHeight:"):
                    h = int(s.split(":")[1])
        except Exception:
            pass

        dim_args = []
        if w > HERO_MAX_DIM or h > HERO_MAX_DIM:
            dim_args = ["-Z", str(HERO_MAX_DIM)]

        d = os.path.dirname(filepath)
        base = os.path.basename(filepath)
        result_tmp = None
        result_bytes = before
        q = HERO_Q_START
        # Each attempt re-encodes from the ORIGINAL file, not a prior attempt.
        while True:
            tmp = os.path.join(d, f".{base}.shrink.{os.getpid()}.{q}.jpg")
            proc = subprocess.run(
                ["sips", *dim_args, "-s", "format", "jpeg",
                 "-s", "formatOptions", str(q), filepath, "--out", tmp],
                capture_output=True, text=True, timeout=60,
            )
            if proc.returncode != 0 or not os.path.exists(tmp):
                if os.path.exists(tmp):
                    os.remove(tmp)
                if result_tmp and os.path.exists(result_tmp):
                    os.remove(result_tmp)
                return f"shrink skipped (sips error); left at {before // 1024}KB"
            size = os.path.getsize(tmp)
            if result_tmp and os.path.exists(result_tmp):
                os.remove(result_tmp)   # drop the previous (larger) attempt
            result_tmp, result_bytes = tmp, size
            if size <= target or q <= HERO_Q_MIN:
                break
            q -= HERO_Q_STEP
            if q < HERO_Q_MIN:
                q = HERO_Q_MIN

        if result_bytes >= before:
            if result_tmp and os.path.exists(result_tmp):
                os.remove(result_tmp)   # no gain — keep the original untouched
            return f"kept original ({before // 1024}KB, no gain)"

        os.replace(result_tmp, filepath)   # atomic, same directory
        flag = ""
        if result_bytes > hard:
            flag = f"  !! STILL OVER {HERO_HARD_KB}KB at q{HERO_Q_MIN} — downscale manually"
        return f"{before // 1024}KB -> {result_bytes // 1024}KB (q{q}){flag}"
    except Exception as e:
        return f"shrink error ({e}); left as-is"


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
    status = _shrink_hero_file(target_file)
    print(f"  [hero shrink] {species_id}/{photo_id}.jpg: {status}")
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
# ║  TRIAGE — iNaturalist photo scanning (ported from photo_workbench.py)  ║
# ║                                                                        ║
# ║  Reads only (no auth needed). Decisions written to a ledger:           ║
# ║    photo_workbench.json  {meta:{cursors}, decisions:{photo_id:{...}}}  ║
# ║  Verdicts: promoted / skip / block. Scan results cache OUTSIDE repo.   ║
# ╚══════════════════════════════════════════════════════════════════════════╝

def _today():
    return datetime.date.today().isoformat()


def load_workbench():
    """Load the triage decision ledger (the 'seen it' memory)."""
    wb = load_json(PHOTO_WORKBENCH, None)
    if wb is None:
        wb = {"meta": {"cursors": {}}, "decisions": {}}
    wb.setdefault("meta", {}).setdefault("cursors", {})
    wb.setdefault("decisions", {})
    return wb


def _inat_get(url):
    """GET a JSON resource from iNat. No auth needed for public CC photos."""
    headers = {
        "Accept": "application/json",
        "User-Agent": "PSBP-SpeciesManager/1.0 (palmasolabp.org)",
    }
    token = os.environ.get("INAT_TOKEN")
    if token:
        headers["Authorization"] = token
    req = Request(url, headers=headers)
    try:
        with urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        print(f"    HTTP {e.code}: {e.read().decode('utf-8', errors='replace')[:160]}")
        return None
    except Exception as e:
        print(f"    request failed: {e}")
        return None


def _inat_get_auth(url, token):
    """GET as a specific user (session token), for cheap token validation."""
    headers = {
        "Accept": "application/json",
        "Authorization": token,
        "User-Agent": "PSBP-SpeciesManager/1.0 (palmasolabp.org)",
    }
    req = Request(url, headers=headers)
    try:
        with urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception:
        return None


def _inat_post(url, token, params=None):
    """POST to iNat with a user token. Returns (ok, status, body_snippet).

    Used for data-quality votes (marking observations not-wild / cultivated).
    Mirrors mark_not_wild.py exactly: query-string params, token in the
    Authorization header. The token is passed per call and never stored.
    """
    if params:
        from urllib.parse import urlencode
        url = url + ("&" if "?" in url else "?") + urlencode(params)
    headers = {
        "Authorization": token,
        "Accept": "application/json",
        "User-Agent": "PSBP-SpeciesManager/1.0 (palmasolabp.org)",
    }
    req = Request(url, headers=headers, method="POST", data=b"")
    try:
        with urlopen(req, timeout=30) as resp:
            code = getattr(resp, "status", resp.getcode())
            return (code in (200, 201, 204), code, "")
    except urllib.error.HTTPError as e:
        return (False, e.code, e.read().decode("utf-8", errors="replace")[:200])
    except Exception as e:
        return (False, 0, str(e)[:200])


def _inat_observations(taxon_id):
    """All park observations for one taxon, paginated.

    Queries the iNat PROJECT (project_id), not a place polygon or a lat/lng
    radius. The project is the curated MEMBERSHIP list: an observation belongs
    because it was collected in the park, regardless of where its PUBLIC pin
    lands. That matters because obscured observations (rare species, or any
    casual obs with geoprivacy on) have a public coordinate scattered far from
    the park — so a place_id / radius query silently drops them, while the
    project still returns them as members. Confirmed in practice: the obscured
    casual Foxtail Palm (public place "Florida, US") is returned by project_id
    but NOT by place_id.

    For a collection project keyed on the park, project membership is a superset
    of a raw place query, so project_id is the most complete single key.

    verifiable=any keeps cultivated/casual-grade observations in — most of a
    botanical garden's plantings are casual grade.

    To pull fully-OBSCURED rare species' true coordinates/photos, set the
    INAT_TOKEN env var to a token for an account trusted with the project's
    hidden coordinates (a project curator). _inat_get already forwards it.
    """
    out, page = [], 1
    while True:
        url = ("https://api.inaturalist.org/v1/observations"
               f"?taxon_id={taxon_id}&project_id={INAT_PROJECT_ID}"
               f"&per_page=200&page={page}&verifiable=any"
               "&order=desc&order_by=created_at")
        data = _inat_get(url)
        if not data:
            break
        results = data.get("results", [])
        out.extend(results)
        if len(results) < 200:
            break
        page += 1
        if page > 10:
            break
        time.sleep(API_DELAY)
    return out


def _cc_photos_from_observations(obs_list):
    """Flatten observations → CC photo records ready for the triage grid.
    Non-CC photos are counted but not shown (the park only uses CC)."""
    cc, non_cc = [], 0
    for obs in obs_list:
        user = obs.get("user") or {}
        login = user.get("login") or "unknown"
        name = display_name(login, user.get("name"))
        observed_on = obs.get("observed_on") or (obs.get("time_observed_at") or "")[:10] or None
        shared_on = (obs.get("created_at") or "")[:10] or None
        obs_id = str(obs.get("id", ""))
        src = f"https://www.inaturalist.org/observations/{obs_id}"
        for p in obs.get("photos", []):
            lic = (p.get("license_code") or "")
            if lic.lower() not in CC_LICENSES:
                non_cc += 1
                continue
            base_url = p.get("url", "") or ""
            cc.append({
                "photo_id":          str(p.get("id", "")),
                "obs_id":            obs_id,
                "thumb_url":         base_url.replace("/square.", "/medium."),
                "large_url":         base_url.replace("/square.", "/large."),
                "license":           lic,
                "photographer":      login,
                "photographer_name": name,
                "observed_on":       observed_on,
                "shared_on":         shared_on,
                "source_url":        src,
            })
    return cc, non_cc


# ── Scan cache (workspace, outside the repo) ───────────────────────────────

def _cache_path(kingdom, psbp_id):
    return os.path.join(TRIAGE_CACHE_DIR, kingdom, f"{psbp_id}.json")


def _read_cache(kingdom, psbp_id):
    return load_json(_cache_path(kingdom, psbp_id), None)


def _write_cache(kingdom, psbp_id, cc, non_cc):
    payload = {
        "psbp_id": psbp_id,
        "scanned_at": datetime.datetime.utcnow().isoformat() + "Z",
        "cc": cc,
        "cc_count": len(cc),
        "non_cc_count": non_cc,
    }
    write_json_atomic(_cache_path(kingdom, psbp_id), payload)
    return payload


def _count_new_candidates(species_id, cc_list, decided=None, registry_ids=None):
    """How many scanned CC photos are NEW — not yet decided and not already
    in the registry. This is the actionable number ('relevant to our species'),
    as opposed to total CC photos seen (which includes already-adjudicated ones).

    Pass decided/registry_ids to avoid re-reading the JSON on every species
    during a scan-all; omit them for a one-off single-species scan.
    """
    if decided is None:
        decided = {str(k) for k in load_workbench()["decisions"].keys()}
    if registry_ids is None:
        registry_ids = {str(p.get("photo_id"))
                        for p in _get_photos_list(_load(PHOTO_CREDITS))
                        if p.get("photo_id")}
    n = 0
    for p in cc_list:
        pid = str(p.get("photo_id", ""))
        if pid and pid not in decided and pid not in registry_ids:
            n += 1
    return n


def _scan_species(kingdom, species, decided=None, registry_ids=None):
    """Hit iNat for one species, refresh its cache. Returns cache payload or error.

    decided/registry_ids are optional pre-loaded sets (used by scan-all so the
    new-candidate count doesn't re-read JSON for every species).
    """
    if not INAT_PROJECT_ID:
        return {"error": "INAT_PROJECT_ID is not set."}
    taxon_id = species.get("inat_taxon_id")
    if not taxon_id:
        return {"error": f"{species.get('id')} has no inat_taxon_id in the signage JSON."}
    obs = _inat_observations(taxon_id)
    cc, non_cc = _cc_photos_from_observations(obs)
    payload = _write_cache(kingdom, species["id"], cc, non_cc)
    payload["new_count"] = _count_new_candidates(species["id"], cc, decided, registry_ids)
    return payload


def _decided_photo_ids(workbench, photos_list):
    """A photo is 'decided' if it has a ledger verdict OR is already in the registry."""
    ids = set(str(k) for k in workbench["decisions"].keys())
    for p in photos_list:
        if p.get("photo_id"):
            ids.add(str(p["photo_id"]))
    return ids


def _triage_counts(kingdom, species_id, photos_list, workbench):
    """Compute GREEN / RED / YELLOW counts for a species.

    GREEN  = photos in the registry (promoted: hero + gallery)
    RED    = skipped + blocked in the ledger
    YELLOW = scanned CC photos with no verdict yet (undecided)
    """
    # GREEN — registry rows for this species
    green = sum(1 for p in photos_list if p.get("psbp_id") == species_id)

    # RED — skip/block decisions in the ledger for this species
    red = 0
    for pid, dec in workbench["decisions"].items():
        if dec.get("psbp_id") == species_id and dec.get("decision") in ("skip", "block"):
            red += 1

    # YELLOW — scanned CC photos not yet decided
    yellow = 0
    scanned = False
    cache = _read_cache(kingdom, species_id)
    if cache:
        scanned = True
        decided = _decided_photo_ids(workbench, photos_list)
        yellow = sum(1 for p in cache["cc"] if p["photo_id"] not in decided)

    return {"green": green, "red": red, "yellow": yellow, "scanned": scanned}


def _build_triage_view(kingdom, species_id, mode="new"):
    """Return the candidate photos for the triage workspace.

    mode:
      'new'     — undecided CC photos (YELLOW) only
      'skipped' — undecided + previously skipped (revisit), NOT blocked

    Skipped photos are surfaced from two places:
      1. The scan cache (if they were in the last scan)
      2. The ledger directly (covers photos demoted from Review mode, which
         may not be in the current scan cache)
    Each photo carries a 'state' for shading: 'new' or 'skipped'.
    """
    photos_list = _get_photos_list(_load(PHOTO_CREDITS))
    workbench = load_workbench()
    cache = _read_cache(kingdom, species_id)
    decisions = workbench["decisions"]
    registry_ids = {str(p.get("photo_id")) for p in photos_list if p.get("photo_id")}

    out = []
    seen = set()

    # ── 1. Walk the scan cache (the YELLOW source + cached skips) ──
    if cache:
        for p in cache["cc"]:
            pid = p["photo_id"]
            if pid in registry_ids:
                continue  # already promoted — shows in Review, not here
            verdict = decisions.get(pid, {}).get("decision")
            if verdict is None:
                state = "new"
            elif verdict == "skip":
                if mode != "skipped":
                    continue
                state = "skipped"
            else:  # block — never resurface
                continue
            item = dict(p)
            item["state"] = state
            out.append(item)
            seen.add(pid)

    # ── 2. In skipped mode, add ledger skips not covered by the cache ──
    #    (e.g. photos demoted from Review mode — they have a skip verdict
    #     and stored display fields, but may not be in the scan cache.)
    if mode == "skipped":
        for pid, dec in decisions.items():
            if dec.get("psbp_id") != species_id:
                continue
            if dec.get("decision") != "skip":
                continue
            if pid in seen or pid in registry_ids:
                continue
            # Reconstruct a card from the stored ledger fields. Old skips
            # (recorded before URLs were stored in the ledger) have no
            # thumb_url/large_url — derive them from photo_id so the card
            # renders instead of showing a broken image on a cold cache.
            _thumb = dec.get("thumb_url", "") or (
                f"https://inaturalist-open-data.s3.amazonaws.com/photos/{pid}/medium.jpg")
            _large = dec.get("large_url", "") or (
                f"https://inaturalist-open-data.s3.amazonaws.com/photos/{pid}/large.jpg")
            out.append({
                "photo_id":          pid,
                "obs_id":            dec.get("obs_id", ""),
                "thumb_url":         _thumb,
                "large_url":         _large,
                "license":           dec.get("license", ""),
                "photographer":      dec.get("photographer", ""),
                "photographer_name": dec.get("photographer_name", "")
                                     or display_name(dec.get("photographer", ""), ""),
                "observed_on":       dec.get("observed_on"),
                "shared_on":         dec.get("shared_on"),
                "source_url":        dec.get("source_url", ""),
                "state":             "skipped",
            })
            seen.add(pid)

    scanned = cache is not None
    return {"photos": out, "scanned": scanned or mode == "skipped",
            "scanned_at": cache.get("scanned_at") if cache else None,
            "non_cc": cache.get("non_cc_count", 0) if cache else 0}


def _apply_triage_decision(payload):
    """Apply a promote/skip/block decision. Ported from photo_workbench.py.

    Promote: heroes download a file + write registry row; gallery = virtual row.
    All decisions are recorded in the ledger so future scans hide them.
    """
    decision = payload.get("decision")
    pid      = str(payload.get("photo_id", ""))
    psbp_id  = payload.get("psbp_id", "")
    kingdom  = payload.get("kingdom", "plants")
    promoted_as_hero = False

    if decision not in ("promoted", "skip", "block") or not pid or not psbp_id:
        return {"ok": False, "error": "bad decision payload"}

    if decision == "promoted":
        credits = _load(PHOTO_CREDITS)
        credits.setdefault("photos", [])
        has_hero = any(p.get("psbp_id") == psbp_id and p.get("hero")
                       for p in credits["photos"])
        if any(str(p.get("photo_id")) == pid for p in credits["photos"]):
            return {"ok": False, "error": "already in registry"}

        is_hero = not has_hero
        promoted_as_hero = is_hero
        name = payload.get("photographer_name") or display_name(
            payload.get("photographer"), "")
        lic = payload.get("license", "")

        if is_hero:
            filename = f"{pid}.jpg"
            dest = os.path.join(PHOTOS_DIR, psbp_id, filename)
            if not _download_hero_file(payload.get("large_url", ""), psbp_id, pid):
                return {"ok": False, "error": "web-res download failed — nothing recorded"}
            time.sleep(DOWNLOAD_DELAY)
        else:
            filename = None  # virtual — served from iNat CDN

        ctype = "Plant" if kingdom == "plants" else "Wildlife"
        entry = {
            "psbp_id":           psbp_id,
            "type":              payload.get("type", ctype),
            "common_name":       payload.get("common_name", ""),
            "scientific_name":   payload.get("scientific_name", ""),
            "role":              ["whole", "gallery"] if is_hero else ["gallery"],
            "primary_for":       ["whole"] if is_hero else [],
            "hero":              is_hero,
            "focus":             "50% 50%" if is_hero else None,
            "tags":              [],
            "photographer":      payload.get("photographer", ""),
            "photographer_name": name,
            "license":           lic.upper() if lic else "",
            "publish_ok":        True,
            "status":            "OK",
            "credit_line":       build_credit_line(name, lic),
            "observed_on":       payload.get("observed_on"),
            "shared_on":         payload.get("shared_on"),
            "photo_url":         payload.get("large_url", ""),
            "source_url":        payload.get("source_url", ""),
            "observation_id":    payload.get("obs_id", ""),
            "photo_id":          pid,
            "filename":          filename,
            "used_by":           [],
            "virtual":           not is_hero,
        }
        credits["photos"].append(entry)
        credits.setdefault("meta", {})["photo_count"] = len(credits["photos"])
        write_json_atomic(PHOTO_CREDITS, credits)

    # Record every decision in the ledger. We store enough display fields that
    # a skipped photo can be reconstructed for "revisit skipped" even if it's
    # no longer in the scan cache (e.g. it was demoted from Review mode).
    wb = load_workbench()
    wb["decisions"][pid] = {
        "decision":          decision,
        "reviewed_on":       _today(),
        "psbp_id":           psbp_id,
        "obs_id":            payload.get("obs_id", ""),
        "photographer":      payload.get("photographer", ""),
        "photographer_name": payload.get("photographer_name", ""),
        "license":           payload.get("license", ""),
        "observed_on":       payload.get("observed_on"),
        "shared_on":         payload.get("shared_on"),
        "thumb_url":         payload.get("thumb_url", ""),
        "large_url":         payload.get("large_url", ""),
        "source_url":        payload.get("source_url", ""),
        "note":              "",
    }
    write_json_atomic(PHOTO_WORKBENCH, wb)

    return {"ok": True, "decision": decision, "psbp_id": psbp_id,
            "is_hero": promoted_as_hero}


# ╔══════════════════════════════════════════════════════════════════════════╗
# ║  API HANDLERS                                                          ║
# ║                                                                        ║
# ║  Each handler takes (params) and returns a JSON-serializable dict.     ║
# ║  Add new endpoints here, then register in API_ROUTES below.            ║
# ╚══════════════════════════════════════════════════════════════════════════╝

def handle_api_overview(params):
    """GET /api/overview — full dashboard stats.

    Wrapped so a transient read hiccup (a JSON mid-write on cold start) returns
    a safe, empty-shaped payload the front-end can render, instead of a 500 that
    blanks the page. The next refresh shows real numbers.
    """
    try:
        return get_overview_data()
    except Exception as e:
        import traceback
        print(f"[WARN] overview computation failed, returning empty: {e}")
        traceback.print_exc()
        empty_kingdom = {"total": 0, "by_status": {}, "attention": []}
        return {
            "plants": dict(empty_kingdom),
            "wildlife": dict(empty_kingdom),
            "photographers": {
                "total_logins": 0, "resolved": 0,
                "unresolved": [], "total_photos": 0,
            },
            "_transient_error": True,
        }


def handle_api_species_list(params):
    """GET /api/species?kingdom=plants — species list for pickers."""
    kingdom = params.get("kingdom", ["plants"])[0]
    return {"kingdom": kingdom, "species": get_species_list(kingdom)}


# ── Future API stubs ───────────────────────────────────────────────────────
# These return descriptive placeholders. Replace with real logic as each
# tab gets built out. The route is already wired up.

# ╔══════════════════════════════════════════════════════════════════════════╗
# ║  iNAT DISCOVERY — scan the project, diff against everything we track,      ║
# ║  surface brand-new taxa, and seed them into research.json.                 ║
# ║                                                                            ║
# ║  Scan uses the species_counts endpoint (one cheap paginated call returns   ║
# ║  every distinct taxon in the project with its observation count). The      ║
# ║  join key is inat_taxon_id, falling back to scientific name.               ║
# ╚══════════════════════════════════════════════════════════════════════════╝

def _inat_species_counts():
    """All distinct taxa observed in the PSBP project, with counts.

    Returns a list of dicts: taxon_id, scientific_name, common_name, rank,
    iconic (Plantae/Aves/...), obs_count, default_photo. Paginated; per_page
    maxes at 500 on this endpoint. verifiable=any keeps casual/cultivated in,
    matching _inat_observations' philosophy (a botanical garden is mostly
    casual-grade plantings).
    """
    out, page = [], 1
    while True:
        url = ("https://api.inaturalist.org/v1/observations/species_counts"
               f"?project_id={INAT_PROJECT_ID}&verifiable=any"
               f"&per_page=500&page={page}")
        data = _inat_get(url)
        if not data:
            break
        results = data.get("results", [])
        for r in results:
            t = r.get("taxon") or {}
            out.append({
                "taxon_id":      t.get("id"),
                "scientific_name": t.get("name", "") or "",
                "common_name":   t.get("preferred_common_name", "") or "",
                "rank":          t.get("rank", "") or "",
                "iconic":        t.get("iconic_taxon_name", "") or "",
                "obs_count":     r.get("count", 0),
                "default_photo": ((t.get("default_photo") or {}).get("square_url")) or "",
            })
        total = data.get("total_results", len(out))
        if len(results) < 500 or len(out) >= total:
            break
        page += 1
        if page > 20:   # 10k taxa ceiling — far beyond any park
            break
        time.sleep(API_DELAY)
    return out


def _known_taxa_index():
    """Build lookup maps of everything we already track.

    Returns (by_taxon_id, by_sci_name). Each maps to a record describing where
    the species lives and its status. Signage ingested AFTER research so a
    promoted species reflects its further-along signage status, not the stale
    research row.
    """
    by_taxon, by_sci = {}, {}

    def ingest(lst, kingdom, where):
        for e in lst:
            tid = e.get("inat_taxon_id")
            sci = (e.get("botanical_name") or e.get("scientific_name") or "").strip().lower()
            rec = {
                "id":              e.get("id", ""),
                "status":          e.get("status", ""),
                "kingdom":         kingdom,
                "where":           where,
                "common_name":     e.get("common_name", ""),
                "scientific_name": e.get("botanical_name") or e.get("scientific_name") or "",
            }
            if tid:
                try:
                    by_taxon[int(tid)] = rec
                except (TypeError, ValueError):
                    pass
            if sci:
                by_sci[sci] = rec

    for e in _get_species_list(_load(RESEARCH_JSON)):
        k = "plants" if e.get("type", "plant") == "plant" else "wildlife"
        ingest([e], k, "research")
    ingest(_get_species_list(_load(PLANT_SIGNAGE)), "plants", "signage")
    ingest(_get_species_list(_load(WILDLIFE_SIGNAGE)), "wildlife", "signage")
    return by_taxon, by_sci


def _all_used_psbp_nums():
    """Every numeric PSBP id in use across research, signage, and the indexes."""
    import re as _re
    used = set()
    sources = [
        _get_species_list(_load(RESEARCH_JSON)),
        _get_species_list(_load(PLANT_SIGNAGE)),
        _get_species_list(_load(WILDLIFE_SIGNAGE)),
    ]
    for path in (PLANTS_INDEX, WILDLIFE_INDEX):
        idx = _load(path)
        if isinstance(idx, list):
            sources.append(idx)
    for lst in sources:
        for e in lst:
            m = _re.match(r"PSBP-(\d+)$", str(e.get("id", "")))
            if m:
                used.add(int(m.group(1)))
    return used


# Plant ids live below this; wildlife ids live at/above it (the 99xxx band).
_WILDLIFE_BAND_FLOOR = 90000
_WILDLIFE_BAND_CEIL  = 99999


def _next_psbp_id(kingdom):
    """Mint the next free PSBP id in the correct kingdom band.

    Plants count up from the low end; wildlife live in 90000–99999. Never
    overflows the 5-digit format. Returns (id_str, warning_or_None) — or
    (None, error) if a band is exhausted.
    """
    used = _all_used_psbp_nums()
    if kingdom == "plants":
        band = [n for n in used if n < _WILDLIFE_BAND_FLOOR]
        nxt = (max(band) + 1) if band else 1
        while nxt in used:
            nxt += 1
        if nxt >= _WILDLIFE_BAND_FLOOR:
            return None, "plant ID band collided with the wildlife band — assign manually"
        return f"PSBP-{nxt:05d}", None

    # wildlife
    band = [n for n in used if n >= _WILDLIFE_BAND_FLOOR]
    nxt = (max(band) + 1) if band else (_WILDLIFE_BAND_FLOOR + 1)
    warn = None
    if nxt > _WILDLIFE_BAND_CEIL:
        # Top of the band is taken — fall back to the lowest free slot.
        nxt = next((c for c in range(_WILDLIFE_BAND_FLOOR + 1, _WILDLIFE_BAND_CEIL + 1)
                    if c not in used), None)
        if nxt is None:
            return None, "wildlife ID band (90000–99999) exhausted — assign manually"
        warn = "wildlife band nearly full — assigned a gap-fill ID; verify it reads sensibly"
    while nxt in used:
        nxt += 1
    if nxt > _WILDLIFE_BAND_CEIL:
        return None, "wildlife ID band (90000–99999) exhausted — assign manually"
    return f"PSBP-{nxt:05d}", warn


def discover_reconcile():
    """Scan the project and bucket every observed taxon as NEW or tracked."""
    counts = _inat_species_counts()
    if not counts:
        return {"ok": False,
                "error": "No taxa returned from iNaturalist — offline, or the "
                         "project slug is wrong. Check INAT_PROJECT_ID."}
    by_taxon, by_sci = _known_taxa_index()

    new_items, ready_items, pipeline_items = [], [], []
    for c in counts:
        tid = c.get("taxon_id")
        sci = (c.get("scientific_name") or "").strip().lower()
        match = None
        if tid is not None:
            try:
                match = by_taxon.get(int(tid))
            except (TypeError, ValueError):
                match = None
        if not match and sci:
            match = by_sci.get(sci)

        if not match:
            new_items.append({**c, "tracked": False})
            continue

        item = {**c, "tracked": True, "psbp_id": match["id"],
                "status": match["status"], "where": match["where"],
                "kingdom": match["kingdom"]}
        if match["where"] == "research":
            # In the research pile AND freshly observed → ready to advance.
            # A died/stolen species you just observed alive is a revive flag.
            item["revivable"] = match["status"] in ("died", "stolen")
            ready_items.append(item)
        else:
            pipeline_items.append(item)

    new_items.sort(key=lambda x: -(x.get("obs_count") or 0))
    # Ready: surface dead-but-observed first, then best-observed.
    ready_items.sort(key=lambda x: (not x.get("revivable"), -(x.get("obs_count") or 0)))

    pipe_summary = {}
    for t in pipeline_items:
        key = t.get("status") or "unknown"
        pipe_summary[key] = pipe_summary.get(key, 0) + 1

    return {"ok": True,
            "scanned": len(counts),
            "new_count": len(new_items),
            "ready_count": len(ready_items),
            "pipeline_count": len(pipeline_items),
            "pipeline_summary": pipe_summary,
            "new": new_items,
            "ready": ready_items,
            "pipeline": pipeline_items}


def add_discovered_to_research(payload):
    """Seed one discovered taxon into research.json as a minimal stub.

    Mints a kingdom-correct PSBP id, carries the iNat identity + a best-effort
    family/genus/species, and leaves all content fields blank for later. iNat
    kingdom comes from the iconic taxon: Plantae → plant, everything else →
    wildlife.
    """
    taxon_id = payload.get("taxon_id")
    sci = (payload.get("scientific_name") or "").strip()
    common = (payload.get("common_name") or "").strip()
    iconic = (payload.get("iconic") or "").strip().lower()
    obs_count = payload.get("obs_count")

    if not sci:
        return {"ok": False, "error": "Missing scientific_name"}

    kingdom = "plants" if iconic == "plantae" else "wildlife"

    # Guard against a double-add or a race with a concurrent promotion.
    by_taxon, by_sci = _known_taxa_index()
    existing = None
    if taxon_id is not None:
        try:
            existing = by_taxon.get(int(taxon_id))
        except (TypeError, ValueError):
            existing = None
    if not existing:
        existing = by_sci.get(sci.lower())
    if existing:
        return {"ok": False,
                "error": f"Already tracked as {existing['id']} "
                         f"({existing['status']}, {existing['where']})",
                "existing": existing}

    new_id, warn = _next_psbp_id(kingdom)
    if not new_id:
        return {"ok": False, "error": warn or "Could not mint a PSBP id"}

    # Best-effort taxonomy. Genus/species from the binomial; family from a
    # single taxon lookup (only one extra request, and only on add).
    parts = sci.split()
    genus = parts[0] if parts else ""
    species = parts[1] if len(parts) >= 2 else ""
    family = ""
    if taxon_id is not None:
        tdata = _inat_get(f"https://api.inaturalist.org/v1/taxa/{taxon_id}")
        if tdata and tdata.get("results"):
            for a in (tdata["results"][0].get("ancestors") or []):
                if a.get("rank") == "family":
                    family = a.get("name", "") or family
                elif a.get("rank") == "genus":
                    genus = a.get("name", "") or genus

    sci_field = "botanical_name" if kingdom == "plants" else "scientific_name"
    entry = {
        "id":              new_id,
        "common_name":     common or sci,
        sci_field:         sci,
        "inat_taxon_id":   int(taxon_id) if taxon_id is not None else None,
        "taxonomy":        {"family": family, "genus": genus, "species": species},
        "type":            "plant" if kingdom == "plants" else "wildlife",
        "status":          "research",
        "has_sign":        False,
        "research_source": "inat_observed",
        "inat_obs_count":  obs_count,
    }

    research = _load(RESEARCH_JSON)
    research.setdefault("species", []).append(entry)
    research["species"].sort(key=lambda s: s.get("id", ""))
    meta = research.setdefault("meta", {})
    meta["species_count"]  = len(research["species"])
    meta["plant_count"]    = sum(1 for s in research["species"] if s.get("type") == "plant")
    meta["wildlife_count"] = sum(1 for s in research["species"] if s.get("type") == "wildlife")
    write_json_atomic(RESEARCH_JSON, research)

    return {"ok": True, "id": new_id, "kingdom": kingdom,
            "common_name": entry["common_name"], "scientific_name": sci,
            "family": family, "warning": warn}


def handle_api_intake_discover(params):
    """GET /api/intake/discover — scan iNat project, diff against tracked."""
    return discover_reconcile()


def handle_api_intake_add_research(params):
    """POST /api/intake/add-research — seed a discovered taxon into research.json.

    Body: {taxon_id, scientific_name, common_name, iconic, obs_count}
    """
    body = params.get("_body", {})
    return add_discovered_to_research(body)


def handle_api_intake_list(params):
    """GET /api/intake/list?kingdom=plants — research.json species for picker."""
    kingdom = params.get("kingdom", ["plants"])[0]
    return {"kingdom": kingdom, "species": get_research_list(kingdom)}


def handle_api_intake_detail(params):
    """GET /api/intake/detail?id=PSBP-00123 — full research.json record."""
    species_id = params.get("id", [""])[0]
    if not species_id:
        return {"error": "Missing id"}
    sp = get_research_detail(species_id)
    if not sp:
        return {"error": f"{species_id} not found in research.json"}
    return {"species": sp}


def handle_api_intake_check(params):
    """POST /api/intake/check — duplicate check before promoting."""
    body = params.get("_body", {})
    kingdom = body.get("kingdom", "plants")
    species_id = body.get("id", "")
    if not species_id:
        return {"error": "Missing id"}
    sp = get_research_detail(species_id)
    if not sp:
        return {"error": f"{species_id} not found in research.json"}
    dupes = _check_intake_duplicates(kingdom, sp)
    return {"id": species_id, "duplicates": dupes, "has_duplicates": len(dupes) > 0}


def handle_api_intake_promote(params):
    """POST /api/intake/promote — move from research.json to signage as spotted.

    Body: {"kingdom": "plants", "id": "PSBP-00123"}
    """
    body = params.get("_body", {})
    kingdom = body.get("kingdom", "plants")
    species_id = body.get("id", "")
    if not species_id:
        return {"ok": False, "error": "Missing id"}
    return promote_to_spotted(species_id, kingdom)


def handle_api_intake_set_status(params):
    """POST /api/intake/set-status — change status within research.json.

    Body: {"id": "PSBP-00123", "status": "died"}
    Valid targets: research, died, stolen.  This does NOT move the record
    to signage — it only relabels it inside research.json.
    """
    body = params.get("_body", {})
    species_id = body.get("id", "")
    new_status = body.get("status", "")
    if not species_id or not new_status:
        return {"ok": False, "error": "Missing id or status"}
    if new_status not in ("research", "died", "stolen"):
        return {"ok": False, "error": f"Invalid status: {new_status}"}

    research = _load(RESEARCH_JSON)
    species_list = _get_species_list(research)
    sp = next((s for s in species_list if s.get("id") == species_id), None)
    if not sp:
        return {"ok": False, "error": f"{species_id} not found in research.json"}

    old_status = sp.get("status", "research")
    if old_status == new_status:
        return {"ok": True, "id": species_id, "note": "No change"}

    sp["status"] = new_status
    # Update meta status_counts
    research.setdefault("meta", {})
    counts = {}
    for s in species_list:
        st = s.get("status", "research")
        counts[st] = counts.get(st, 0) + 1
    research["meta"]["status_counts"] = counts
    write_json_atomic(RESEARCH_JSON, research)

    return {
        "ok": True,
        "id": species_id,
        "common_name": sp.get("common_name", ""),
        "old_status": old_status,
        "new_status": new_status,
    }


def handle_api_intake_inat_check(params):
    """GET /api/intake/inat-check?taxon_id=12345 — observation quality from iNat.

    Hits the iNat API for PSBP-project observations of this taxon and returns
    a summary: total obs, quality grades, unique observers, latest date.
    Helps Randy judge whether an iNat-only sighting is a fluke or solid.
    """
    taxon_id = params.get("taxon_id", [""])[0]
    if not taxon_id:
        return {"error": "Missing taxon_id"}

    # Query the PROJECT (membership), not a place/radius — matches the photo
    # scan and includes obscured + casual-grade observations that a place query
    # would drop (their public pin lands outside the park boundary).
    url = ("https://api.inaturalist.org/v1/observations"
           f"?taxon_id={taxon_id}&project_id={INAT_PROJECT_ID}&per_page=200"
           "&verifiable=any&order=desc&order_by=created_at")
    data = _inat_get(url)
    if not data:
        return {"error": "iNat API request failed"}

    results = data.get("results", [])
    total = data.get("total_results", len(results))

    quality = {}
    observers = set()
    latest_date = None
    for obs in results:
        qg = obs.get("quality_grade", "unknown")
        quality[qg] = quality.get(qg, 0) + 1
        user = obs.get("user") or {}
        if user.get("login"):
            observers.add(user["login"])
        obs_date = obs.get("observed_on") or ""
        if obs_date and (not latest_date or obs_date > latest_date):
            latest_date = obs_date

    return {
        "taxon_id":           taxon_id,
        "total_observations": total,
        "quality_grades":     quality,
        "unique_observers":   len(observers),
        "observer_logins":    sorted(observers),
        "latest_observation": latest_date,
        "project_url":        (f"https://www.inaturalist.org/observations"
                               f"?project_id={INAT_PROJECT_ID}&taxon_id={taxon_id}"
                               f"&verifiable=any"),
    }

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


def handle_api_triage_scan(params):
    """POST /api/triage/scan — fetch fresh iNat results for one species.

    Body: {"kingdom": "plants", "id": "PSBP-00005"}
    Hits iNat (read-only), caches CC photos. Returns the CC count.
    """
    body = params.get("_body", {})
    kingdom = body.get("kingdom", "plants")
    species_id = body.get("id", "")
    if not species_id:
        return {"ok": False, "error": "Missing species id"}

    path = PLANT_SIGNAGE if kingdom == "plants" else WILDLIFE_SIGNAGE
    species_list = _get_species_list(_load(path))
    sp = next((s for s in species_list if s.get("id") == species_id), None)
    if not sp:
        return {"ok": False, "error": f"{species_id} not found in {kingdom} signage"}

    res = _scan_species(kingdom, sp)
    if "error" in res:
        return {"ok": False, "error": res["error"]}
    return {"ok": True, "cc_count": res["cc_count"], "new_count": res.get("new_count", 0),
            "non_cc_count": res["non_cc_count"], "scanned_at": res["scanned_at"]}


# ── Scan-all background job ────────────────────────────────────────────────
# A single scan-all runs at a time. Progress lives in this module-level dict
# so the poll endpoint can report it. The scan runs in a daemon thread, so it
# survives the browser navigating away — the data still gets written, and the
# progress can be re-read whenever the page comes back.

# The most recent finished scan-all summary is also persisted here so the
# results survive a page reload OR a dashboard restart (not just an in-memory
# toast that flashes once and vanishes). Kept per-kingdom so a wildlife scan
# doesn't clobber the plants result (Randy works in both).
def _last_scan_path(kingdom):
    safe = "wildlife" if kingdom == "wildlife" else "plants"
    return os.path.join(TRIAGE_WORKSPACE, f"_last_scan_{safe}.json")

_SCAN_JOB = {
    "running":   False,
    "kingdom":   None,
    "done":      0,
    "total":     0,
    "current":   "",        # common name of species being scanned
    "scanned":   0,
    "failed":    [],
    "skipped_no_taxon": [],
    "total_cc_found":   0,   # every CC photo seen (incl. already-adjudicated)
    "total_new_found":  0,   # NEW candidates only — undecided + not in registry
    "species_with_new": 0,   # how many species gained at least one new candidate
    "started_at": None,
    "finished_at": None,
}
_SCAN_LOCK = threading.Lock()


def _write_last_scan_summary(job):
    """Persist a compact summary of a finished scan-all run to disk."""
    summary = {
        "kingdom":          job.get("kingdom"),
        "total":            job.get("total", 0),
        "scanned":          job.get("scanned", 0),
        "total_cc_found":   job.get("total_cc_found", 0),
        "total_new_found":  job.get("total_new_found", 0),
        "species_with_new": job.get("species_with_new", 0),
        "failed":           list(job.get("failed", [])),
        "skipped_no_taxon": list(job.get("skipped_no_taxon", [])),
        "started_at":       job.get("started_at"),
        "finished_at":      job.get("finished_at"),
    }
    try:
        write_json_atomic(_last_scan_path(job.get("kingdom")), summary)
    except Exception as e:
        print(f"    could not persist last-scan summary: {e}")
    return summary


def _scan_all_worker(kingdom, targets):
    """Background worker: scan each target species, updating _SCAN_JOB."""
    global _SCAN_JOB
    # Load the decided-set and registry once up front so the new-candidate count
    # doesn't re-read JSON for all ~190 species. These don't change during an
    # automated scan (no human is adjudicating mid-run).
    decided = {str(k) for k in load_workbench()["decisions"].keys()}
    registry_ids = {str(p.get("photo_id"))
                    for p in _get_photos_list(_load(PHOTO_CREDITS))
                    if p.get("photo_id")}
    for i, sp in enumerate(targets):
        with _SCAN_LOCK:
            _SCAN_JOB["current"] = sp.get("common_name", sp.get("id", ""))
        res = _scan_species(kingdom, sp, decided=decided, registry_ids=registry_ids)
        with _SCAN_LOCK:
            if "error" in res:
                _SCAN_JOB["failed"].append({"id": sp.get("id"), "error": res["error"]})
            else:
                _SCAN_JOB["scanned"] += 1
                _SCAN_JOB["total_cc_found"] += res.get("cc_count", 0)
                nc = res.get("new_count", 0)
                _SCAN_JOB["total_new_found"] += nc
                if nc > 0:
                    _SCAN_JOB["species_with_new"] += 1
            _SCAN_JOB["done"] = i + 1
        # Be polite to iNat between species (skip the wait after the last one)
        if i < len(targets) - 1:
            time.sleep(API_DELAY)
    with _SCAN_LOCK:
        _SCAN_JOB["running"] = False
        _SCAN_JOB["current"] = ""
        _SCAN_JOB["finished_at"] = datetime.datetime.utcnow().isoformat() + "Z"
        _write_last_scan_summary(_SCAN_JOB)


def handle_api_triage_scan_all(params):
    """POST /api/triage/scan-all — start a background scan of all html+spotted species.

    Body: {"kingdom": "plants"}
    Returns immediately with the total count; the scan runs in a thread.
    Poll /api/triage/scan-progress for live status.
    """
    global _SCAN_JOB
    body = params.get("_body", {})
    kingdom = body.get("kingdom", "plants")

    with _SCAN_LOCK:
        if _SCAN_JOB["running"]:
            return {"ok": False, "error": "A scan is already running.",
                    "running": True, "done": _SCAN_JOB["done"],
                    "total": _SCAN_JOB["total"]}

    path = PLANT_SIGNAGE if kingdom == "plants" else WILDLIFE_SIGNAGE
    species_list = _get_species_list(_load(path))
    targets = [s for s in species_list
               if s.get("status") in ("html", "spotted") and s.get("inat_taxon_id")]
    skipped_no_taxon = [s.get("id") for s in species_list
                        if s.get("status") in ("html", "spotted") and not s.get("inat_taxon_id")]

    if not targets:
        return {"ok": False, "error": "No scannable species (need html/spotted status + taxon ID).",
                "skipped_no_taxon": skipped_no_taxon}

    # Reset job state and launch the worker.
    with _SCAN_LOCK:
        _SCAN_JOB.update({
            "running": True, "kingdom": kingdom,
            "done": 0, "total": len(targets), "current": "",
            "scanned": 0, "failed": [], "skipped_no_taxon": skipped_no_taxon,
            "total_cc_found": 0, "total_new_found": 0, "species_with_new": 0,
            "started_at": datetime.datetime.utcnow().isoformat() + "Z",
            "finished_at": None,
        })

    t = threading.Thread(target=_scan_all_worker, args=(kingdom, targets), daemon=True)
    t.start()

    return {"ok": True, "started": True, "kingdom": kingdom, "total": len(targets),
            "skipped_no_taxon": skipped_no_taxon}


def handle_api_triage_scan_progress(params):
    """GET /api/triage/scan-progress — poll the running scan-all job.

    Returns the live job state. When running flips to False, the caller should
    fetch fresh picker data once to show the refreshed counts.
    """
    with _SCAN_LOCK:
        job = dict(_SCAN_JOB)  # snapshot
    # When finished, attach refreshed picker data so the client updates once.
    if not job["running"] and job["finished_at"] and job["kingdom"]:
        job["species"] = get_photos_summary(job["kingdom"])
    return job


def handle_api_triage_last_scan(params):
    """GET /api/triage/last-scan?kingdom=plants — the persisted summary of the
    most recent finished scan-all for that kingdom. Survives page reloads and
    dashboard restarts so the result banner can be shown again."""
    kingdom = params.get("kingdom", ["plants"])[0]
    return load_json(_last_scan_path(kingdom), {}) or {}


def handle_api_triage_view(params):
    """GET /api/triage/view?kingdom=plants&id=PSBP-00005&mode=new — candidates."""
    kingdom = params.get("kingdom", ["plants"])[0]
    species_id = params.get("id", [""])[0]
    mode = params.get("mode", ["new"])[0]
    if not species_id:
        return {"error": "Missing species id"}
    view = _build_triage_view(kingdom, species_id, mode)
    return {"kingdom": kingdom, "species_id": species_id, "mode": mode, **view}


def handle_api_triage_decide(params):
    """POST /api/triage/decide — apply promote/skip/block.

    Body carries everything needed so we never re-hit the API here:
      {kingdom, decision, photo_id, psbp_id, obs_id, large_url, source_url,
       photographer, photographer_name, license, observed_on, shared_on,
       common_name, scientific_name, type}
    """
    body = params.get("_body", {})
    return _apply_triage_decision(body)


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
            # Promoted photos (often from the gallery) carry focus=null.
            # Give the new hero a sensible default so the registry and the
            # generated page never inherit a null object-position. Randy can
            # fine-tune the crop later via the focus control.
            if not p.get("focus"):
                p["focus"] = "50% 50%"
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
    """POST /api/photos/trash — demote a photo from the registry.

    Body: {"psbp_id": "PSBP-00042", "photo_id": "12345678"}

    Removes the row from photo_credits.json AND writes a 'skip' verdict to
    the workbench ledger (with display fields) so the photo can be brought
    back later via Triage → "revisit skipped". Does NOT delete files on disk.
    """
    body = params.get("_body", {})
    psbp_id = body.get("psbp_id", "")
    photo_id = str(body.get("photo_id", ""))
    if not psbp_id or not photo_id:
        return {"error": "Missing psbp_id or photo_id"}

    credits = _load(PHOTO_CREDITS)
    photos = credits.get("photos", [])

    # Grab the row's display fields before removing it (for the ledger).
    removed_row = None
    for p in photos:
        if p.get("psbp_id") == psbp_id and str(p.get("photo_id", "")) == photo_id:
            removed_row = p
            break

    if removed_row is None:
        return {"error": f"Photo {photo_id} not found for {psbp_id}"}

    credits["photos"] = [
        p for p in photos
        if not (p.get("psbp_id") == psbp_id and str(p.get("photo_id", "")) == photo_id)
    ]
    credits.setdefault("meta", {})["photo_count"] = len(credits["photos"])
    write_json_atomic(PHOTO_CREDITS, credits)

    # Write a 'skip' verdict so this photo returns via "revisit skipped".
    photo_url = removed_row.get("photo_url", "")
    thumb_url = photo_url.replace("/large.", "/medium.") if photo_url else ""
    wb = load_workbench()
    wb["decisions"][photo_id] = {
        "decision":          "skip",
        "reviewed_on":       _today(),
        "psbp_id":           psbp_id,
        "obs_id":            removed_row.get("observation_id", ""),
        "photographer":      removed_row.get("photographer", ""),
        "photographer_name": removed_row.get("photographer_name", ""),
        "license":           removed_row.get("license", ""),
        "observed_on":       removed_row.get("observed_on"),
        "shared_on":         removed_row.get("shared_on"),
        "thumb_url":         thumb_url,
        "large_url":         photo_url,
        "source_url":        removed_row.get("source_url", ""),
        "note":              "demoted from Review",
    }
    write_json_atomic(PHOTO_WORKBENCH, wb)

    return {"ok": True, "psbp_id": psbp_id, "removed": photo_id,
            "remaining": len(credits["photos"]), "demoted_to": "skipped"}

# ── Gap audit (preview-only; never touches data or the publisher) ──────────
#
# Defines which signage fields matter for a complete page, and what counts as
# "thin." Used ONLY to render the Gaps overlay in preview. Nothing here is ever
# written to JSON or seen by a visitor.
#
# Each entry: (label, accessor, kind)
#   kind "text"  → flag if empty/whitespace; thin if very short
#   kind "list"  → flag if empty; thin if below min_len
#   kind "blocks"→ list of {label,text}; flag if empty
#   accessor is a dotted path into the species dict (supports one level of nesting)

PLANT_GAP_SPEC = [
    ("Common name",       "common_name",        "text",   None),
    ("Scientific name",   "botanical_name",     "text",   None),
    ("Family",            "taxonomy.family",    "text",   None),
    ("Category",          "category",           "text",   None),
    ("Quick hits",        "quick_hits",         "list",   3),
    ("Origin",            "origin",             "text",   "prose"),
    ("More information",  "more_information",   "list",   2),
    ("Wildlife value",    "wildlife_value",     "list",   1),
    ("Reproduction blocks", "reproduction.blocks", "blocks", None),
    ("What to look for",  "reproduction.what_to_look_for", "text", "prose"),
    ("Size",              "size.height_length", "text",   None),
    ("Growing light",     "growing_conditions.light", "text", None),
    ("Edibility detail",  "edibility.detail",   "text",   None),
    ("Toxicity (people)", "toxicity.people",    "text",   None),
]

WILDLIFE_GAP_SPEC = [
    ("Common name",       "common_name",        "text",   None),
    ("Scientific name",   "scientific_name",    "text",   None),
    ("Animal group",      "animal_group",       "text",   None),
    ("Category",          "category",           "text",   None),
    ("Quick hits",        "quick_hits",         "list",   3),
    ("Range & origin",    "range_and_origin",   "text",   "prose"),
    ("More information",  "more_information",   "list",   2),
    ("Identification blocks", "identification.blocks", "blocks", None),
    ("What to look for",  "identification.what_to_look_for", "text", "prose"),
    ("Diet",              "diet",               "text",   "prose"),
    ("Behavior",          "behavior",           "text",   "prose"),
    ("Voice / sounds",    "sounds",             "text",   "prose"),
    ("Where to look",     "where_to_look",      "text",   "prose"),
    ("When to see",       "when_to_see",        "text",   "prose"),
    ("Habitat",           "habitat",            "text",   "prose"),
]

# Placeholder strings that should count as EMPTY even though the field has text.
_PLACEHOLDER_MARKERS = (
    "to be expanded", "to be documented", "to be drafted", "first-pass stub",
    "see full writeup", "see quick hit", "future review", "content to be",
)

def _dig(species, path):
    """Follow a dotted path one or two levels deep; return the value or None."""
    cur = species
    for part in path.split("."):
        if not isinstance(cur, dict):
            return None
        cur = cur.get(part)
    return cur


def _looks_placeholder(text):
    t = (text or "").strip().lower()
    if not t or t == "—" or t == "-":
        return True
    return any(m in t for m in _PLACEHOLDER_MARKERS)


def audit_gaps(kingdom, species):
    """Return a list of gap findings for a species. Preview-only.

    Each finding: {"label","level"} where level is "missing" or "thin".
    """
    spec = PLANT_GAP_SPEC if kingdom == "plants" else WILDLIFE_GAP_SPEC
    findings = []

    for label, path, kind, minimum in spec:
        val = _dig(species, path)

        if kind == "text":
            if not val or _looks_placeholder(str(val)):
                findings.append({"label": label, "level": "missing"})
            elif minimum == "prose" and len(str(val).strip()) < 25:
                findings.append({"label": label, "level": "thin"})

        elif kind == "list":
            items = [x for x in (val or []) if not _looks_placeholder(str(x))]
            if not items:
                findings.append({"label": label, "level": "missing"})
            elif minimum and len(items) < minimum:
                findings.append({"label": label, "level": "thin"})

        elif kind == "blocks":
            blocks = val or []
            good = [b for b in blocks
                    if isinstance(b, dict) and not _looks_placeholder(b.get("text", ""))]
            if not good:
                findings.append({"label": label, "level": "missing"})

    return findings


def _gaps_overlay_html(findings):
    """Build the floating Gaps panel injected into preview when gaps=1."""
    if not findings:
        return (
            '<div id="gaps-panel" style="position:fixed;right:16px;top:50px;width:230px;'
            'z-index:99998;background:#1a3a1f;color:#fff;border-radius:10px;'
            'padding:14px 16px;font:13px system-ui;box-shadow:0 4px 20px rgba(0,0,0,.3);">'
            '<strong style="display:block;margin-bottom:4px;">✓ No gaps</strong>'
            'Every tracked field has real content. Looks publish-ready.</div>'
        )
    missing = [f for f in findings if f["level"] == "missing"]
    thin = [f for f in findings if f["level"] == "thin"]
    rows = ""
    for f in missing:
        rows += (f'<div style="display:flex;gap:6px;align-items:center;margin:3px 0;">'
                 f'<span style="color:#ff6b6b;">●</span>'
                 f'<span>{f["label"]}</span>'
                 f'<span style="margin-left:auto;font-size:10px;opacity:.7;">empty</span></div>')
    for f in thin:
        rows += (f'<div style="display:flex;gap:6px;align-items:center;margin:3px 0;">'
                 f'<span style="color:#ffd24a;">●</span>'
                 f'<span>{f["label"]}</span>'
                 f'<span style="margin-left:auto;font-size:10px;opacity:.7;">thin</span></div>')
    return (
        '<div id="gaps-panel" style="position:fixed;right:16px;top:50px;width:250px;'
        'z-index:99998;background:#1a3a1f;color:#fff;border-radius:10px;'
        'padding:14px 16px;font:13px system-ui;box-shadow:0 4px 20px rgba(0,0,0,.3);'
        'max-height:80vh;overflow:auto;">'
        f'<strong style="display:block;margin-bottom:8px;">Gaps to fill '
        f'({len(missing)} empty, {len(thin)} thin)</strong>'
        f'{rows}'
        '<div style="margin-top:10px;font-size:11px;opacity:.7;line-height:1.4;">'
        'Red = empty/placeholder · Yellow = present but short. '
        'These flags are preview-only and never publish.</div></div>'
    )


def render_preview_html(kingdom, species_id, gaps_mode=False):
    """Render a species page in memory using the publisher's generator.

    Returns (html_string, status_code). Never writes files or changes status —
    a true dry run, so spotted species can be previewed before publishing.
    """
    pub = _publisher_for(kingdom)
    if pub is None:
        return (f"<h1>Preview unavailable</h1><p>{PUBLISHER_IMPORT_ERROR}</p>", 500)

    try:
        signage = pub.load_signage()
        credits = pub.load_credits()
        heroes = pub.build_hero_lookup(credits)
        galleries = pub.build_gallery_lookup(credits)
        species = pub.build_species_lookup(signage).get(species_id)
        if not species:
            return (f"<h1>Not found</h1><p>{species_id} is not in {kingdom} signage.</p>", 404)

        hero = heroes.get(species_id)
        if not hero:
            return (f"<h1>No hero photo</h1>"
                    f"<p>{species_id} has no hero photo yet, so there's nothing to preview. "
                    f"Add one in the Photos tab first.</p>", 400)

        html = pub.generate_html(species, hero, galleries.get(species_id, []))

        # Banner + a toggle between Visitor view and Gaps view. The gaps overlay
        # is rendered from a preview-only audit — nothing here touches the data
        # or the published page.
        other_mode = "0" if gaps_mode else "1"
        other_label = "Visitor view" if gaps_mode else "Gaps view"
        toggle = (
            f'<a href="/preview?kingdom={kingdom}&id={species_id}&gaps={other_mode}" '
            'style="margin-left:14px;color:#1a3a1f;background:rgba(255,255,255,.55);'
            'padding:2px 10px;border-radius:10px;text-decoration:none;font-weight:700;">'
            f'⇄ {other_label}</a>'
        )
        banner = (
            '<div style="position:fixed;top:0;left:0;right:0;z-index:99999;'
            'background:#c5922a;color:#1a3a1f;font:600 13px system-ui;'
            'padding:7px 14px;text-align:center;box-shadow:0 2px 8px rgba(0,0,0,.2);">'
            f'PREVIEW · {species_id} · status: {species.get("status","?")} · '
            + ('GAPS VIEW' if gaps_mode else 'not yet published — dry run')
            + toggle +
            '</div><div style="height:34px;"></div>'
        )
        # Photos are referenced two ways in the published markup:
        #   gallery / relative :  "photos/PSBP-xxxxx/file.jpg"
        #   hero & lightbox    :  "../photos/PSBP-xxxxx/file.jpg"  (published pages
        #                          live in /plants/ or /wildlife/, hence the ../)
        # The preview server is flat, so the ../ form resolves to /photos/... and
        # 404s — that's the broken-image "?" on the hero. Rewrite BOTH forms onto
        # the dashboard's /photos-file/ route, which streams the real file straight
        # from the local repo. This touches only the in-memory preview; the path the
        # publisher actually writes is never altered (it'll be regenerated correctly
        # on publish).
        for q in ('"', "'", '('):
            html = html.replace(f'{q}../photos/', f'{q}/photos-file/')
            html = html.replace(f'{q}photos/',    f'{q}/photos-file/')

        # Gaps overlay panel (only in gaps mode)
        overlay = _gaps_overlay_html(audit_gaps(kingdom, species)) if gaps_mode else ""

        # Inject banner + overlay right after <body>
        if "<body" in html:
            idx = html.index("<body")
            close = html.index(">", idx) + 1
            html = html[:close] + banner + overlay + html[close:]
        else:
            html = banner + overlay + html
        return (html, 200)
    except Exception as e:
        import traceback
        return (f"<h1>Preview error</h1><pre>{traceback.format_exc()[-800:]}</pre>", 500)


def handle_api_preview(params):
    """Deprecated JSON stub — preview is served as raw HTML via /preview route."""
    return {"status": "moved", "message": "Use /preview?kingdom=&id= for rendered HTML."}

def handle_api_photos_focus(params):
    """POST /api/photos/focus — set the focus point on a photo.

    Body: {"psbp_id": "PSBP-00042", "photo_id": "12345678", "focus": "35% 60%"}

    Writes to photo_credits.json. If this photo is the hero, also propagates
    the focus point to the search index card so the published page crops right.
    """
    body = params.get("_body", {})
    psbp_id = body.get("psbp_id", "")
    photo_id = str(body.get("photo_id", ""))
    focus = body.get("focus", "")
    if not psbp_id or not photo_id:
        return {"error": "Missing psbp_id or photo_id"}
    if not focus:
        return {"error": "Missing focus value"}

    credits = _load(PHOTO_CREDITS)
    photos = credits.get("photos", [])
    target = None
    for p in photos:
        if p.get("psbp_id") == psbp_id and str(p.get("photo_id", "")) == photo_id:
            p["focus"] = focus
            target = p
            break

    if not target:
        return {"error": f"Photo {photo_id} not found for {psbp_id}"}

    write_json_atomic(PHOTO_CREDITS, credits)

    result = {"ok": True, "psbp_id": psbp_id, "photo_id": photo_id, "focus": focus}

    # If this is the hero, propagate focus to the search index card
    if target.get("hero"):
        for idx_path in (PLANTS_INDEX, WILDLIFE_INDEX):
            idx = _load(idx_path)
            if not isinstance(idx, list):
                continue
            changed = False
            for card in idx:
                if card.get("id") == psbp_id and "focus" in card:
                    card["focus"] = focus
                    changed = True
                    break
            if changed:
                write_json_atomic(idx_path, idx)
                result["search_index_updated"] = os.path.basename(idx_path)
                break

    return result


def _publisher_for(kingdom):
    """Return the right publisher module for a kingdom, or None."""
    if not PUBLISHERS_OK:
        return None
    return plant_publisher if kingdom == "plants" else wildlife_publisher


def _publish_readiness(kingdom, species, hero):
    """Compute the readiness checklist for one species.

    Returns (checks_list, ready_bool). Each check: {label, ok}.
    """
    sci_field = "botanical_name" if kingdom == "plants" else "scientific_name"
    checks = []

    has_hero = hero is not None
    checks.append({"label": "Hero photo", "ok": has_hero})

    hero_on_disk = bool(has_hero and _hero_on_disk(species.get("id", "")))
    checks.append({"label": "Hero file on disk", "ok": hero_on_disk})

    has_common = bool(species.get("common_name"))
    checks.append({"label": "Common name", "ok": has_common})

    has_sci = bool(species.get(sci_field))
    checks.append({"label": "Scientific name", "ok": has_sci})

    # Credit resolves to a real name (not just a handle) when we have a hero
    credit_ok = False
    if has_hero:
        cred = resolve_hero_credit(hero)
        credit_ok = bool(cred.get("credit_name"))
    checks.append({"label": "Photo credit resolved", "ok": credit_ok})

    ready = all(c["ok"] for c in checks)
    return checks, ready


def get_publish_list(kingdom):
    """Species list for the Publish tab: status, readiness, hero presence."""
    path = PLANT_SIGNAGE if kingdom == "plants" else WILDLIFE_SIGNAGE
    sci_field = "botanical_name" if kingdom == "plants" else "scientific_name"
    type_filter = "Plant" if kingdom == "plants" else "Wildlife"

    species_list = _get_species_list(_load(path))
    photos_list = _get_photos_list(_load(PHOTO_CREDITS))
    hero_ids, _ = _build_hero_index(photos_list)
    heroes = {p["psbp_id"]: p for p in photos_list
              if p.get("hero") and p.get("type") == type_filter}

    result = []
    for sp in sorted(species_list, key=lambda s: s.get("id", "")):
        sid = sp.get("id", "")
        hero = heroes.get(sid)
        checks, ready = _publish_readiness(kingdom, sp, hero)
        result.append({
            "id": sid,
            "common_name": sp.get("common_name", ""),
            "scientific_name": sp.get(sci_field, ""),
            "status": sp.get("status", "unknown"),
            "has_hero": sid in hero_ids,
            "ready": ready,
            "checks": checks,
            "tags": sp.get("tags") or [],
            "aliases": sp.get("also_known_as") or sp.get("alternate_names") or [],
        })
    return result


def handle_api_publish_list(params):
    """GET /api/publish/list?kingdom=plants — species with readiness for Publish tab."""
    kingdom = params.get("kingdom", ["plants"])[0]
    if not PUBLISHERS_OK:
        return {"kingdom": kingdom, "species": [],
                "error": f"Publisher modules failed to import: {PUBLISHER_IMPORT_ERROR}"}
    return {"kingdom": kingdom, "species": get_publish_list(kingdom)}


def handle_api_publish_ready(params):
    """GET /api/publish/ready?kingdom=plants&id=PSBP-00001 — readiness checklist."""
    kingdom = params.get("kingdom", ["plants"])[0]
    species_id = params.get("id", [""])[0]
    if not species_id:
        return {"error": "Missing id"}
    path = PLANT_SIGNAGE if kingdom == "plants" else WILDLIFE_SIGNAGE
    species = next((s for s in _get_species_list(_load(path)) if s.get("id") == species_id), None)
    if not species:
        return {"error": f"{species_id} not found"}
    type_filter = "Plant" if kingdom == "plants" else "Wildlife"
    photos_list = _get_photos_list(_load(PHOTO_CREDITS))
    hero = next((p for p in photos_list
                 if p.get("psbp_id") == species_id and p.get("hero")
                 and p.get("type") == type_filter), None)
    checks, ready = _publish_readiness(kingdom, species, hero)
    return {"id": species_id, "kingdom": kingdom, "checks": checks, "ready": ready}


def handle_api_publish_promote(params):
    """POST /api/publish/promote — generate HTML, update index, set status=html.

    Body: {"kingdom": "plants", "id": "PSBP-00005"}
    Delegates to the publisher module's proven functions.
    """
    body = params.get("_body", {})
    kingdom = body.get("kingdom", "plants")
    pid = body.get("id", "")
    if not pid:
        return {"ok": False, "error": "Missing id"}

    pub = _publisher_for(kingdom)
    if pub is None:
        return {"ok": False, "error": f"Publisher unavailable: {PUBLISHER_IMPORT_ERROR}"}

    try:
        signage = pub.load_signage()
        credits = pub.load_credits()
        heroes = pub.build_hero_lookup(credits)
        galleries = pub.build_gallery_lookup(credits)
        species_lookup = pub.build_species_lookup(signage)

        species = species_lookup.get(pid)
        if not species:
            return {"ok": False, "error": f"{pid} not found in {kingdom} signage"}

        hero = heroes.get(pid)
        if not hero:
            return {"ok": False, "error": f"No hero photo for {pid} — can't publish"}

        # Generate the HTML page (publisher's proven template)
        path, _ = pub.write_html(species, hero, galleries.get(pid, []))

        # Update the search index card
        if kingdom == "plants":
            entry = pub.update_plants_json(species, hero)
        else:
            entry = pub.update_wildlife_json(species, hero)

        # Flip status to html
        was_status = species.get("status")
        if was_status != "html":
            pub.update_signage_status(pid, "html")

        return {
            "ok": True,
            "id": pid,
            "filename": getattr(path, "name", str(path)),
            "was_status": was_status,
            "new_status": "html",
            "regenerated": was_status == "html",
        }
    except Exception as e:
        import traceback
        return {"ok": False, "error": str(e), "trace": traceback.format_exc()[-500:]}


def handle_api_publish_demote(params):
    """POST /api/publish/demote — delete HTML, clean index, set status=spotted.

    Body: {"kingdom": "plants", "id": "PSBP-00005"}
    """
    body = params.get("_body", {})
    kingdom = body.get("kingdom", "plants")
    pid = body.get("id", "")
    if not pid:
        return {"ok": False, "error": "Missing id"}

    pub = _publisher_for(kingdom)
    if pub is None:
        return {"ok": False, "error": f"Publisher unavailable: {PUBLISHER_IMPORT_ERROR}"}

    try:
        signage = pub.load_signage()
        species = pub.build_species_lookup(signage).get(pid)
        if not species:
            return {"ok": False, "error": f"{pid} not found"}
        if species.get("status") != "html":
            return {"ok": False, "error": f"{pid} is {species.get('status')}, not html"}

        # Set status back to spotted
        pub.update_signage_status(pid, "spotted")

        # Remove the card from the search index
        if kingdom == "plants":
            from psbp_common import PLANTS_JSON as IDX
        else:
            from psbp_common import WILDLIFE_JSON as IDX
        entries = load_json(IDX, [])
        entries = [e for e in entries if e.get("id") != pid]
        entries.sort(key=lambda e: e.get("id", ""))
        write_json_atomic(IDX, entries)

        # Delete the generated HTML file(s)
        from psbp_common import delete_species_page
        deleted = delete_species_page(kingdom, pid)

        return {"ok": True, "id": pid, "new_status": "spotted",
                "deleted_files": deleted}
    except Exception as e:
        import traceback
        return {"ok": False, "error": str(e), "trace": traceback.format_exc()[-500:]}


def handle_api_publish_demote_research(params):
    """POST /api/publish/demote-research — full removal from signage back to research.json.

    Body: {"kingdom": "plants", "id": "PSBP-00005", "reason": "research"|"died"}

    This is a deeper demote than the spotted demote: the species record moves
    entirely out of the signage JSON back into research.json, hero files are
    cleaned up, and if the species was published its HTML page and search index
    card are also removed.

    reason='research' → status 'research' (just not ready, revisit later)
    reason='died'     → status 'died' (suspected dead/gone from park)
    """
    body = params.get("_body", {})
    kingdom = body.get("kingdom", "plants")
    pid = body.get("id", "")
    reason = body.get("reason", "research")
    if not pid:
        return {"ok": False, "error": "Missing id"}
    if reason not in ("research", "died"):
        return {"ok": False, "error": f"Invalid reason: {reason}"}

    path = PLANT_SIGNAGE if kingdom == "plants" else WILDLIFE_SIGNAGE
    type_val = "plant" if kingdom == "plants" else "wildlife"

    try:
        # ── 1. Load and find the species in signage ────────────────
        signage = _load(path)
        species_list = _get_species_list(signage)
        sp = next((s for s in species_list if s.get("id") == pid), None)
        if not sp:
            return {"ok": False, "error": f"{pid} not found in {kingdom} signage"}
        was_status = sp.get("status", "unknown")

        # ── 2. If published, clean up HTML page + search index ─────
        deleted_files = []
        if was_status == "html":
            # Remove from search index
            if kingdom == "plants":
                from psbp_common import PLANTS_JSON as IDX
            else:
                from psbp_common import WILDLIFE_JSON as IDX
            entries = load_json(IDX, [])
            entries = [e for e in entries if e.get("id") != pid]
            entries.sort(key=lambda e: e.get("id", ""))
            write_json_atomic(IDX, entries)

            # Delete HTML page
            from psbp_common import delete_species_page
            deleted_files = delete_species_page(kingdom, pid)

        # ── 3. Delete hero JPG from photos/PSBP-xxxxx/ ────────────
        hero_dir = os.path.join(PHOTOS_DIR, pid)
        hero_deleted = []
        if os.path.isdir(hero_dir):
            for f in os.listdir(hero_dir):
                fp = os.path.join(hero_dir, f)
                if os.path.isfile(fp):
                    os.remove(fp)
                    hero_deleted.append(f)
            try:
                os.rmdir(hero_dir)
            except OSError:
                pass

        # ── 4. Build research.json record ──────────────────────────
        record = dict(sp)
        record["status"] = reason     # "research" or "died"
        record["type"] = type_val
        record["research_source"] = "prior_research"

        # ── 5. Append to research.json ─────────────────────────────
        research = _load(RESEARCH_JSON)
        research.setdefault("species", []).append(record)
        research["species"].sort(key=lambda s: s.get("id", ""))
        research.setdefault("meta", {})
        research["meta"]["species_count"] = len(research["species"])
        plants_r = sum(1 for s in research["species"] if s.get("type") == "plant")
        wildlife_r = sum(1 for s in research["species"] if s.get("type") == "wildlife")
        research["meta"]["plant_count"] = plants_r
        research["meta"]["wildlife_count"] = wildlife_r
        # Update status_counts
        status_counts = {}
        for s in research["species"]:
            st = s.get("status", "research")
            status_counts[st] = status_counts.get(st, 0) + 1
        research["meta"]["status_counts"] = status_counts
        write_json_atomic(RESEARCH_JSON, research)

        # ── 6. Remove from signage JSON ────────────────────────────
        signage["species"] = [s for s in species_list if s.get("id") != pid]
        signage.setdefault("meta", {})
        signage["meta"]["species_count"] = len(signage["species"])
        write_json_atomic(path, signage)

        label = "marked dead" if reason == "died" else "returned to research"
        return {
            "ok": True,
            "id": pid,
            "common_name": sp.get("common_name", ""),
            "was_status": was_status,
            "new_status": reason,
            "label": label,
            "deleted_files": deleted_files,
            "hero_deleted": hero_deleted,
        }
    except Exception as e:
        import traceback
        return {"ok": False, "error": str(e), "trace": traceback.format_exc()[-500:]}


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
.rb-title { margin: 14px 0 6px; font-size: 11px; text-transform: uppercase;
            letter-spacing: .04em; color: #8a8f96; }
.rb-bar { display: flex; height: 12px; border-radius: 6px; overflow: hidden;
          background: #eceff1; }
.rb-seg { height: 100%; }
.rb-legend { display: flex; flex-wrap: wrap; gap: 4px 14px; margin-top: 8px; }
.rb-li { display: inline-flex; align-items: center; font-size: 12px; color: #555; }
.rb-li b { margin-left: 4px; color: #222; }
.rb-dot { width: 9px; height: 9px; border-radius: 2px; margin-right: 5px;
          display: inline-block; }
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
.attention-intro {
    font-size: 12px;
    color: var(--gray-400);
    margin: -6px 0 12px;
    line-height: 1.4;
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
.photo-date {
    font-size: 11px;
    color: var(--gray-400);
    margin-bottom: 6px;
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

/* ── Triage mode ───────────────────────────────────────────── */
.photos-controls {
    display: flex;
    gap: 28px;
    align-items: flex-end;
    flex-wrap: wrap;
    margin-bottom: 20px;
}
.control-group {
    display: flex;
    flex-direction: column;
    gap: 4px;
}
.control-group-label {
    font-size: 11px;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.4px;
    color: var(--gray-400);
}
.photos-controls .mode-toggle { margin-bottom: 0; }

/* Picker legend */
.picker-legend {
    display: flex;
    gap: 12px;
    margin-top: 8px;
    font-size: 11px;
    color: var(--gray-400);
}
.picker-legend span { display: flex; align-items: center; gap: 4px; }
.picker-legend .dot {
    width: 8px; height: 8px; border-radius: 50%;
}
.picker-legend .dot.green  { background: var(--green-light); }
.picker-legend .dot.yellow { background: var(--gold); }
.picker-legend .dot.red    { background: #c62828; }

/* New-only toggle (Find Photos picker) */
.new-only-btn {
    width: 100%;
    margin-top: 10px;
    padding: 7px 12px;
    border: 1px solid var(--gold, #c8a02a);
    background: white;
    color: #8a6d00;
    border-radius: var(--radius);
    font-size: 13px;
    font-weight: 600;
    cursor: pointer;
    display: flex;
    align-items: center;
    justify-content: center;
    gap: 8px;
    transition: background 0.15s, color 0.15s;
}
.new-only-btn:hover { background: #fffaf0; }
.new-only-btn.active {
    background: var(--gold, #c8a02a);
    color: #3a2e00;
    border-color: var(--gold, #c8a02a);
}
.new-only-btn .no-count {
    min-width: 20px;
    padding: 1px 7px;
    border-radius: 10px;
    background: #fff3d6;
    color: #8a6d00;
    font-variant-numeric: tabular-nums;
    font-size: 12px;
}
.new-only-btn.active .no-count { background: rgba(255,255,255,0.55); color: #3a2e00; }

/* Scan-all button */
.scan-all-btn {
    width: 100%;
    margin-top: 10px;
    padding: 8px 12px;
    border-radius: var(--radius);
    border: 1px solid var(--green-mid);
    background: white;
    color: var(--green-mid);
    font-size: 13px;
    font-weight: 500;
    cursor: pointer;
    transition: all 0.12s;
}
.scan-all-btn:hover { background: var(--green-mid); color: white; }
.scan-all-btn:disabled {
    opacity: 0.6;
    cursor: default;
    background: var(--gray-100);
    color: var(--gray-400);
    border-color: var(--gray-200);
}

/* Status pills — shared across picker and overview */
.status-pill {
    display: inline-block;
    font-size: 10px;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.4px;
    padding: 1px 7px;
    border-radius: 8px;
    margin-top: 3px;
}
.status-pill.html {
    background: var(--green-deep);
    color: white;
}
.status-pill.spotted {
    background: var(--gold);
    color: var(--green-deep);
}
.status-pill.research {
    background: #e4e9ec;
    color: var(--status-research);
}

/* Scan-all progress banner */
.scan-progress {
    position: fixed;
    top: 16px;
    left: 50%;
    transform: translateX(-50%) translateY(-20px);
    width: min(440px, calc(100vw - 40px));
    background: white;
    border-radius: var(--radius);
    box-shadow: 0 4px 20px rgba(0,0,0,0.18);
    border: 1px solid var(--green-mid);
    padding: 14px 18px;
    z-index: 1500;
    opacity: 0;
    pointer-events: none;
    transition: opacity 0.25s, transform 0.25s;
}
.scan-progress.show {
    opacity: 1;
    transform: translateX(-50%) translateY(0);
}
.scan-progress-head {
    display: flex;
    justify-content: space-between;
    align-items: baseline;
    margin-bottom: 8px;
}
.scan-progress-head #scan-progress-label {
    font-size: 13px;
    font-weight: 600;
    color: var(--green-deep);
}
.scan-progress-head #scan-progress-count {
    font-size: 13px;
    font-weight: 600;
    color: var(--green-mid);
    font-variant-numeric: tabular-nums;
}
.scan-progress-track {
    height: 8px;
    background: var(--gray-100);
    border-radius: 4px;
    overflow: hidden;
}
.scan-progress-fill {
    height: 100%;
    width: 0%;
    background: var(--green-mid);
    border-radius: 4px;
    transition: width 0.4s ease;
}
.scan-progress-current {
    font-size: 12px;
    color: var(--gray-400);
    margin-top: 8px;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
    min-height: 16px;
}

/* Persistent scan-all result banner — stays until dismissed, unlike a toast. */
.scan-summary {
    display: none;
    margin: 0 0 16px 0;
    background: var(--green-pale, #eef4ec);
    border: 1px solid var(--green-mid);
    border-left: 4px solid var(--green-mid);
    border-radius: var(--radius);
    padding: 14px 16px;
}
.scan-summary.show { display: block; }
.scan-summary-head {
    display: flex;
    justify-content: space-between;
    align-items: flex-start;
    gap: 12px;
}
.scan-summary-headline {
    font-size: 15px;
    font-weight: 700;
    color: var(--green-deep);
}
.scan-summary-headline .big {
    font-size: 18px;
    font-variant-numeric: tabular-nums;
}
.scan-summary-sub {
    font-size: 12.5px;
    color: var(--gray-500, #5b6b5b);
    margin-top: 4px;
    line-height: 1.5;
}
.scan-summary-sub .muted { color: var(--gray-400); }
.scan-summary-warn { color: #9a6a00; }
.scan-summary-close {
    background: none;
    border: none;
    font-size: 18px;
    line-height: 1;
    color: var(--gray-400);
    cursor: pointer;
    padding: 2px 4px;
    flex-shrink: 0;
}
.scan-summary-close:hover { color: var(--gray-600, #444); }
.scan-summary-time { font-size: 11px; color: var(--gray-400); margin-top: 6px; }

/* ── Publish tab ───────────────────────────────────────────── */
.pub-layout { max-width: 920px; }
.pub-intro { margin-bottom: 16px; }
.pub-intro p {
    font-size: 13px;
    color: var(--gray-600);
    line-height: 1.6;
    margin: 0;
}
.pub-intro .status-pill { vertical-align: middle; }
.pub-group { margin-bottom: 16px; }
.pub-group h2 {
    font-size: 15px;
    display: flex;
    align-items: center;
    gap: 10px;
    margin-bottom: 12px;
}
.pub-group-count {
    font-size: 12px;
    font-weight: 500;
    color: var(--gray-400);
}
.pub-empty {
    font-size: 13px;
    color: var(--gray-400);
    font-style: italic;
    padding: 6px 0;
}
.pub-row {
    border: 1px solid var(--gray-200);
    border-radius: var(--radius);
    padding: 12px 14px;
    margin-bottom: 8px;
}
.pub-row:last-child { margin-bottom: 0; }
.pub-row-main {
    display: flex;
    align-items: center;
    gap: 10px;
    margin-bottom: 8px;
}
.pub-id {
    font-family: "SF Mono", Menlo, monospace;
    font-size: 11px;
    color: var(--gray-600);
    background: var(--gray-100);
    padding: 2px 7px;
    border-radius: 3px;
}
.pub-names { flex: 1; min-width: 0; }
.pub-common { font-weight: 600; font-size: 14px; }
.pub-sci {
    font-size: 12px;
    color: var(--gray-400);
    font-style: italic;
    margin-left: 6px;
}
.pub-checks {
    display: flex;
    flex-wrap: wrap;
    gap: 6px;
    margin-bottom: 10px;
}
.pub-check {
    font-size: 11px;
    padding: 2px 8px;
    border-radius: 10px;
}
.pub-check.ok { background: #e8f5e9; color: var(--green-mid); }
.pub-check.no { background: #ffebee; color: #c62828; }
.pub-actions {
    display: flex;
    gap: 8px;
    align-items: center;
}
.pub-btn {
    padding: 6px 14px;
    border-radius: var(--radius);
    font-size: 13px;
    font-weight: 500;
    cursor: pointer;
    border: 1px solid;
}
.pub-btn.aidraft {
    background: #1a3a5c;
    color: #fff;
    border-color: #1a3a5c;
}
.pub-btn.aidraft:hover { background: #234d77; }
.pub-btn.airevise {
    background: #5c3a6b;
    color: #fff;
    border-color: #5c3a6b;
}
.pub-btn.airevise:hover { background: #6f4a80; }
.rev-box { display: flex; flex-direction: column; gap: 8px; }
.rev-label { font-size: 12px; color: #4a5a6a; }
.rev-text { width: 100%; box-sizing: border-box; font: inherit; font-size: 13px;
    padding: 8px 10px; border: 1px solid #cdd6df; border-radius: 7px; resize: vertical; }
.rev-row { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.rev-check { font-size: 12px; color: #5a6675; display: flex; align-items: center; gap: 6px; }
.ai-result {
    display: none;
    margin-top: 10px;
    padding: 12px 14px;
    border-radius: 9px;
    font-size: 13px;
    line-height: 1.5;
    background: #f4f7fb;
    border: 1px solid #d9e2ee;
    color: #2a3a4a;
}
.ai-result.working { color: #43566b; }
.ai-result.error { background: #fdecec; border-color: #f3c4c4; color: #8a2a2a; }
.ai-spin { display: inline-block; animation: aipulse 1.1s ease-in-out infinite; }
@keyframes aipulse { 0%,100%{opacity:.4;} 50%{opacity:1;} }
.ai-summary { font-style: italic; margin-bottom: 8px; color: #3a4a5a; }
.ai-line { margin: 6px 0; }
.ai-line.ai-none { color: #6b7a8a; }
.ai-line.ai-muted { color: #94a3b4; font-size: 12px; }
.ai-chip {
    display: inline-block; margin: 2px 4px 2px 0; padding: 1px 8px;
    border-radius: 10px; font-size: 12px;
}
.ai-chip.ok { background: #e3f2e6; color: #2d6a35; }
.ai-chip.skip { background: #eceff1; color: #607080; }
.ai-chip.low { background: #fff4d9; color: #8a6300; }
.ai-sources { margin: 6px 0; }
.ai-sources summary { cursor: pointer; color: #1a3a5c; font-size: 12px; }
.ai-sources ul { margin: 6px 0 0 18px; padding: 0; }
.ai-sources a { color: #1a5276; }
.ai-raw { background: #fff; border: 1px solid #eee; padding: 6px; font-size: 11px; overflow:auto; max-height: 120px; }
.pub-btn.promote {
    background: var(--green-mid);
    color: white;
    border-color: var(--green-mid);
}
.pub-btn.promote:hover { background: var(--green-deep); }
.pub-btn.promote:disabled {
    background: var(--gray-100);
    color: var(--gray-400);
    border-color: var(--gray-200);
    cursor: default;
}
.pub-btn.regen {
    background: white;
    color: var(--green-mid);
    border-color: var(--green-mid);
}
.pub-btn.regen:hover { background: #e8f5e9; }
.pub-btn.demote {
    background: white;
    color: var(--gray-600);
    border-color: var(--gray-200);
}
.pub-btn.demote:hover { background: var(--gray-100); color: #c62828; border-color: #c62828; }
.pub-working {
    font-size: 13px;
    color: var(--gray-400);
    font-style: italic;
}
.pub-na { color: var(--gray-200); }
.pub-secondary {
    display: flex;
    gap: 12px;
    margin-top: 6px;
    padding-top: 6px;
    border-top: 1px dashed var(--gray-100);
}
.pub-link-btn {
    border: none;
    background: none;
    font-size: 11px;
    color: var(--gray-400);
    cursor: pointer;
    padding: 2px 0;
    transition: color 0.12s;
}
.pub-link-btn:hover { color: var(--gray-600); }
.pub-link-btn.dead-link:hover { color: #c62828; }
.pub-tags {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 5px;
    margin-bottom: 10px;
}
.pub-tags-label {
    font-size: 10px;
    text-transform: uppercase;
    letter-spacing: 0.5px;
    color: var(--gray-400);
    margin-right: 2px;
}
.pub-tag {
    font-size: 11px;
    padding: 1px 7px;
    border-radius: 3px;
    background: #eef1f4;
    color: #5a6b7a;
    border: 1px dashed #c5cfd8;
}
.pub-search {
    padding: 7px 10px;
    border: 1px solid var(--gray-200);
    border-radius: 6px;
    font-size: 13px;
    outline: none;
    width: 100%;
}
.pub-search:focus { border-color: var(--green-mid); }
.pub-btn.preview {
    background: white;
    color: var(--gray-600);
    border-color: var(--gray-200);
}
.pub-btn.preview:hover {
    background: var(--cream);
    color: var(--green-deep);
    border-color: var(--gold);
}
.pub-btn.gaps {
    background: white;
    color: #b8860b;
    border-color: #e0c66a;
}
.pub-btn.gaps:hover { background: #fff8e1; border-color: var(--gold); }

/* Picker triage counts */
.pi-counts {
    display: flex;
    gap: 3px;
    flex-shrink: 0;
}
.pc {
    font-size: 11px;
    font-weight: 600;
    min-width: 20px;
    text-align: center;
    padding: 1px 5px;
    border-radius: 8px;
}
.pc.green  { background: #e8f5e9; color: var(--green-mid); }
.pc.yellow { background: #fff8e1; color: #b8860b; }
.pc.red    { background: #ffebee; color: #c62828; }
.pi-warn { color: var(--gold); font-size: 12px; }

/* Triage header actions */
.triage-header-actions { margin-left: auto; }
.fetch-more-btn {
    padding: 7px 14px;
    border-radius: var(--radius);
    border: 1px solid var(--green-mid);
    background: var(--green-mid);
    color: white;
    font-size: 13px;
    font-weight: 500;
    cursor: pointer;
}
.fetch-more-btn:hover { background: var(--green-deep); }
.triage-status {
    font-size: 12px;
    color: var(--gray-400);
    margin-bottom: 14px;
}

/* Triage cards */
.triage-card.triage-skipped { box-shadow: 0 0 0 2px #c62828, var(--shadow); }
.triage-card.triage-new { box-shadow: 0 0 0 2px var(--gold), var(--shadow); }
.triage-card.triage-decided { opacity: 0.5; }
.triage-badge {
    position: absolute;
    top: 8px;
    left: 8px;
    font-size: 11px;
    font-weight: 600;
    padding: 2px 8px;
    border-radius: 3px;
    color: white;
    letter-spacing: 0.3px;
}
.triage-badge.skipped { background: #c62828; }

.triage-actions {
    display: flex;
    gap: 0;
    border-top: 1px solid var(--gray-100);
}
.t-btn {
    flex: 1;
    padding: 8px 4px;
    border: none;
    background: transparent;
    font-size: 12px;
    font-weight: 500;
    cursor: pointer;
    transition: all 0.1s;
}
.t-btn + .t-btn { border-left: 1px solid var(--gray-100); }
.t-btn.promote { color: var(--green-mid); flex: 1.6; }
.t-btn.promote:hover { background: #e8f5e9; }
.t-btn.skip { color: var(--gray-600); }
.t-btn.skip:hover { background: var(--gray-100); }
.t-btn.block { color: #c62828; }
.t-btn.block:hover { background: #ffebee; }
.t-saving, .t-verdict {
    flex: 1;
    text-align: center;
    padding: 8px 4px;
    font-size: 12px;
    color: var(--gray-400);
    font-style: italic;
}
.t-verdict { color: var(--green-mid); font-style: normal; font-weight: 500; }

/* Fetch-more modal */
.fetch-modal-inner {
    background: white;
    border-radius: var(--radius);
    max-width: 460px;
    width: 100%;
    box-shadow: 0 8px 32px rgba(0,0,0,0.3);
    overflow: hidden;
}
.fetch-body { padding: 18px; }
.fetch-species {
    font-size: 13px;
    color: var(--gray-600);
    font-style: italic;
    margin-bottom: 14px;
}
.fetch-choice {
    display: block;
    width: 100%;
    text-align: left;
    padding: 14px 16px;
    margin-bottom: 10px;
    border: 1px solid var(--gray-200);
    border-radius: var(--radius);
    background: white;
    cursor: pointer;
    transition: all 0.12s;
}
.fetch-choice:hover {
    border-color: var(--green-mid);
    background: #f5faf6;
}
.fetch-choice .fc-title {
    display: block;
    font-size: 14px;
    font-weight: 600;
    color: var(--green-deep);
    margin-bottom: 3px;
}
.fetch-choice .fc-sub {
    display: block;
    font-size: 12px;
    color: var(--gray-400);
}
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

/* Thumbnail wrapper (clickable for focus editor) */
.photo-thumb-wrap {
    position: relative;
    cursor: pointer;
    overflow: hidden;
}
.photo-thumb-wrap .focus-hint {
    position: absolute;
    bottom: 6px;
    right: 6px;
    background: rgba(0,0,0,0.55);
    color: white;
    font-size: 10px;
    padding: 2px 7px;
    border-radius: 3px;
    opacity: 0;
    transition: opacity 0.15s;
    pointer-events: none;
}
.photo-thumb-wrap:hover .focus-hint { opacity: 1; }

/* Focus editor modal */
.focus-modal {
    position: fixed;
    inset: 0;
    background: rgba(0,0,0,0.6);
    display: none;
    align-items: center;
    justify-content: center;
    z-index: 2000;
    padding: 24px;
}
.focus-modal.open { display: flex; }
.focus-modal-inner {
    background: white;
    border-radius: var(--radius);
    max-width: 720px;
    width: 100%;
    max-height: 90vh;
    display: flex;
    flex-direction: column;
    overflow: hidden;
    box-shadow: 0 8px 32px rgba(0,0,0,0.3);
}
.focus-modal-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 14px 18px;
    border-bottom: 1px solid var(--gray-200);
    font-size: 14px;
    font-weight: 500;
    color: var(--green-deep);
}
.focus-close {
    border: none;
    background: transparent;
    font-size: 18px;
    cursor: pointer;
    color: var(--gray-400);
    line-height: 1;
}
.focus-close:hover { color: var(--gray-800); }
.focus-stage {
    position: relative;
    flex: 1;
    overflow: auto;
    background: var(--gray-100);
    display: flex;
    align-items: center;
    justify-content: center;
    min-height: 200px;
    cursor: crosshair;
}
.focus-stage img {
    max-width: 100%;
    max-height: 60vh;
    display: block;
    user-select: none;
    -webkit-user-drag: none;
}
.focus-crosshair {
    position: absolute;
    width: 28px;
    height: 28px;
    margin-left: -14px;
    margin-top: -14px;
    border: 2px solid white;
    border-radius: 50%;
    box-shadow: 0 0 0 2px rgba(0,0,0,0.5), inset 0 0 0 1px rgba(0,0,0,0.5);
    pointer-events: none;
    transition: left 0.05s, top 0.05s;
}
.focus-crosshair::before, .focus-crosshair::after {
    content: '';
    position: absolute;
    background: white;
    box-shadow: 0 0 1px rgba(0,0,0,0.8);
}
.focus-crosshair::before {
    left: 50%; top: 50%;
    width: 2px; height: 10px;
    margin-left: -1px; margin-top: -5px;
}
.focus-crosshair::after {
    left: 50%; top: 50%;
    width: 10px; height: 2px;
    margin-left: -5px; margin-top: -1px;
}
.focus-modal-footer {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 12px 18px;
    border-top: 1px solid var(--gray-200);
}
.focus-coords {
    font-family: "SF Mono", Menlo, monospace;
    font-size: 13px;
    color: var(--gray-600);
    background: var(--gray-100);
    padding: 4px 10px;
    border-radius: 4px;
}
.focus-actions { display: flex; gap: 8px; }
.focus-btn-cancel, .focus-btn-save {
    padding: 7px 16px;
    border-radius: var(--radius);
    font-size: 13px;
    font-weight: 500;
    cursor: pointer;
    border: 1px solid var(--gray-200);
}
.focus-btn-cancel { background: white; color: var(--gray-600); }
.focus-btn-cancel:hover { background: var(--gray-100); }
.focus-btn-save {
    background: var(--green-mid);
    color: white;
    border-color: var(--green-mid);
}
.focus-btn-save:hover { background: var(--green-deep); }
.focus-btn-save:disabled { opacity: 0.6; cursor: default; }
.focus-preview-note {
    padding: 0 18px 14px;
    font-size: 12px;
    color: var(--gray-400);
}

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

/* ── Intake tab ───────────────────────────────────────────── */
.intake-detail-card h2 {
    font-size: 18px;
    margin: 0;
    display: inline;
}
.intake-header {
    display: flex;
    align-items: flex-start;
    gap: 12px;
    margin-bottom: 12px;
}
.intake-header > div { flex: 1; min-width: 0; }
.intake-sci {
    font-size: 13px;
    color: var(--gray-400);
    font-style: italic;
    margin-top: 2px;
}
.intake-taxonomy {
    font-size: 12px;
    color: var(--gray-600);
    margin-bottom: 12px;
    padding: 6px 10px;
    background: var(--gray-100);
    border-radius: 4px;
}
.intake-chips {
    display: flex;
    flex-wrap: wrap;
    gap: 6px;
    margin-bottom: 16px;
}
.intake-chip {
    font-size: 11px;
    padding: 3px 9px;
    border-radius: 10px;
    background: var(--gray-100);
    color: var(--gray-600);
    font-weight: 500;
}
.intake-chip.native { background: #e8f5e9; color: var(--green-mid); }
.intake-chip.non-native { background: #fff3e0; color: #e65100; }
.intake-chip.sign { background: #e3f2fd; color: #1565c0; }
.intake-chip.taxon {
    background: #f3e5f5;
    color: #7b1fa2;
    font-family: "SF Mono", Menlo, monospace;
}
.intake-section { margin-bottom: 16px; }
.intake-section h3 {
    font-size: 13px;
    font-weight: 600;
    color: var(--green-deep);
    margin-bottom: 8px;
    display: flex;
    align-items: center;
    gap: 8px;
}
.intake-fill-count {
    font-size: 12px;
    font-weight: 500;
    color: var(--gray-400);
}
.intake-fields {
    border: 1px solid var(--gray-200);
    border-radius: var(--radius);
    overflow: hidden;
}
.intake-field {
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 6px 12px;
    font-size: 13px;
    border-bottom: 1px solid var(--gray-100);
}
.intake-field:last-child { border-bottom: none; }
.intake-field.filled .if-check { color: var(--green-mid); }
.intake-field.empty .if-check { color: var(--gray-200); }
.intake-field.empty .if-label { color: var(--gray-400); }
.if-check {
    font-size: 12px;
    width: 16px;
    text-align: center;
    flex-shrink: 0;
}
.if-label { font-weight: 500; min-width: 130px; }
.if-detail {
    flex: 1;
    font-size: 12px;
    color: var(--gray-400);
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
}
.intake-qh {
    font-size: 13px;
    color: var(--gray-600);
    line-height: 1.5;
    margin-bottom: 6px;
    padding-left: 4px;
}
.intake-qh-more {
    font-size: 12px;
    color: var(--gray-400);
    font-style: italic;
    padding-left: 4px;
}
.intake-notes {
    font-size: 12px;
    color: var(--gray-600);
    background: var(--gray-100);
    padding: 8px 12px;
    border-radius: 4px;
    margin-bottom: 16px;
    line-height: 1.5;
}
.intake-actions {
    display: flex;
    gap: 8px;
    padding-top: 12px;
    border-top: 1px solid var(--gray-200);
}
.intake-actions .promote { flex: 1; }
.pi-badge.has-content { background: #e8f5e9; color: var(--green-mid); }
.pi-badge.no-content { background: var(--gray-100); color: var(--gray-400); }
.intake-status-marker {
    font-size: 10px;
    font-weight: 600;
    text-transform: uppercase;
    color: var(--status-research);
    margin-left: 4px;
}

/* Source tier: left-border accents on picker items */
.picker-item.src-both      { border-left: 3px solid var(--green-mid); background: #f5faf6; }
.picker-item.src-prior      { border-left: 3px solid #5c6bc0; }
.picker-item.src-inat       { border-left: 3px solid var(--gold); }
.picker-item.src-inventory  { border-left: 3px solid var(--gray-200); }

/* Dead/stolen species in picker */
.picker-item.is-dead { background: #fef2f2; }
.picker-item.is-dead .pi-common { text-decoration: line-through; color: var(--gray-400); }
.picker-item.is-dead .pi-sci { color: var(--gray-200); }

/* Discover-style picker cards: name, sci, meta, inline actions */
.picker-item.card { display: block; padding: 10px 12px; }
.picker-item.card .pi-top { display: flex; align-items: flex-start; justify-content: space-between; gap: 8px; }
.picker-item.card .pi-name { display: flex; flex-direction: column; min-width: 0; }
.picker-item.card .pi-common { font-weight: 600; color: var(--green-deep); }
.picker-item.card .pi-sci { font-style: italic; color: var(--gray-600); font-size: 12px; }
.pi-content { font-size: 10px; font-weight: 700; padding: 1px 6px; border-radius: 8px; white-space: nowrap; flex: 0 0 auto; }
.pi-content.has { background: #e3f0e5; color: #2d6a35; }
.pi-content.none { background: #f0eee8; color: #b09a5a; }
.picker-item.card .pi-meta { font-size: 11px; color: #999; margin-top: 3px; }
.picker-item.card .pi-actions { display: flex; gap: 6px; margin-top: 8px; }
.pi-btn { border: none; border-radius: 6px; padding: 5px 10px; font-size: 11px; font-weight: 700; cursor: pointer; }
.pi-work { background: var(--green-mid); color: #fff; }
.pi-dead { background: #f0eee8; color: #8a6d2f; }
.pi-revive { background: #c62828; color: #fff; }

/* Source banner in detail card */
.intake-source-banner {
    display: flex;
    align-items: center;
    gap: 10px;
    padding: 10px 14px;
    border-radius: var(--radius);
    font-size: 13px;
    font-weight: 500;
    margin-bottom: 14px;
}
.intake-source-banner .isb-icon { font-size: 16px; }
.intake-source-banner .isb-label { flex: 1; }
.intake-source-banner .isb-sub {
    font-size: 11px;
    font-weight: 400;
    color: inherit;
    opacity: 0.7;
    display: block;
    margin-top: 1px;
}
.intake-source-banner.src-both {
    background: #e8f5e9;
    color: var(--green-deep);
    border-left: 4px solid var(--green-mid);
}
.intake-source-banner.src-prior {
    background: #e8eaf6;
    color: #283593;
    border-left: 4px solid #5c6bc0;
}
.intake-source-banner.src-inat {
    background: #fff8e1;
    color: #5d4200;
    border-left: 4px solid var(--gold);
}
.intake-source-banner.src-inventory {
    background: var(--gray-100);
    color: var(--gray-600);
    border-left: 4px solid var(--gray-400);
}

/* Dead detail card */
.intake-detail-card.is-dead {
    border: 2px solid #ef9a9a;
    background: #fef8f8;
}
.intake-dead-banner {
    background: #ffebee;
    color: #b71c1c;
    padding: 10px 14px;
    border-radius: var(--radius);
    font-size: 13px;
    font-weight: 600;
    margin-bottom: 14px;
    display: flex;
    align-items: center;
    gap: 8px;
}

/* iNat buttons */
.intake-inat-row {
    display: flex;
    gap: 8px;
    margin-bottom: 14px;
    flex-wrap: wrap;
}
.inat-link-btn {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    padding: 6px 14px;
    border-radius: var(--radius);
    font-size: 12px;
    font-weight: 500;
    text-decoration: none;
    border: 1px solid var(--gold);
    background: #fff8e1;
    color: #5d4200;
    cursor: pointer;
    transition: all 0.12s;
}
.inat-link-btn:hover { background: var(--gold); color: white; }
.inat-check-btn {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    padding: 6px 14px;
    border-radius: var(--radius);
    font-size: 12px;
    font-weight: 500;
    border: 1px solid var(--gray-200);
    background: white;
    color: var(--gray-600);
    cursor: pointer;
    transition: all 0.12s;
}
.inat-check-btn:hover { border-color: var(--green-mid); color: var(--green-mid); }
.inat-check-btn:disabled { opacity: 0.5; cursor: default; }
.inat-summary {
    font-size: 12px;
    color: var(--gray-600);
    padding: 8px 12px;
    background: var(--gray-100);
    border-radius: 4px;
    margin-bottom: 14px;
    line-height: 1.6;
}
.inat-summary .is-strong { color: var(--green-mid); font-weight: 600; }
.inat-summary .is-weak { color: #c62828; font-weight: 600; }

/* Dead/revive button */
.pub-btn.dead-btn {
    background: white;
    color: #c62828;
    border-color: #ef9a9a;
}
.pub-btn.dead-btn:hover { background: #ffebee; border-color: #c62828; }
.pub-btn.revive-btn {
    background: white;
    color: var(--green-mid);
    border-color: var(--green-mid);
}
.pub-btn.revive-btn:hover { background: #e8f5e9; }
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
                <h2>🌱 Plants in progress (spotted)</h2>
                <p class="attention-intro">Spotted species still being prepped for publish. Missing pieces here are expected — this is the to-do list before they go live.</p>
                <div id="plants-attention"></div>
            </div>
            <div class="card">
                <h2>🦎 Wildlife in progress (spotted)</h2>
                <p class="attention-intro">Spotted species still being prepped for publish. Missing pieces here are expected — this is the to-do list before they go live.</p>
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
    async function loadOverview(attempt) {
        attempt = attempt || 0;
        try {
            const resp = await fetch('/api/overview');
            const data = await resp.json();
            // If the server hit a transient read race, retry briefly before showing data.
            if (data._transient_error && attempt < 3) {
                document.getElementById('overview-loading').textContent =
                    'Loading data… (warming up)';
                setTimeout(() => loadOverview(attempt + 1), 500);
                return;
            }
            renderOverview(data);
        } catch (err) {
            // Network/parse hiccup on cold start — retry a few times before giving up.
            if (attempt < 3) {
                document.getElementById('overview-loading').textContent =
                    'Loading data… (retrying)';
                setTimeout(() => loadOverview(attempt + 1), 500);
                return;
            }
            document.getElementById('overview-loading').textContent =
                'Error loading data: ' + err.message + ' — try refreshing the page.';
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
        const research  = (kingdomData.research && kingdomData.research.total) || 0;
        const spotted   = kingdomData.by_status.spotted || 0;
        const published = kingdomData.by_status.html || 0;
        const pipeline  = (research + spotted + published) || 1;

        const rows = [
            ['research', 'Research',  research],
            ['spotted',  'Spotted',   spotted],
            ['html',     'Published', published],
        ];

        let html = `<div class="funnel-total">${research + spotted + published}</div>`;
        html += `<div class="funnel-total-label">in pipeline (research → published)</div>`;

        for (const [status, label, count] of rows) {
            const pct = Math.round((count / pipeline) * 100);
            html += `
                <div class="funnel-row">
                    <span class="funnel-label">${label}</span>
                    <div class="funnel-bar-track">
                        <div class="funnel-bar-fill ${status}"
                             style="width: ${Math.max(pct, 1)}%"></div>
                    </div>
                    <span class="funnel-count">${count}</span>
                </div>
            `;
        }

        // Research pile broken down by source — the candidates feeding the funnel.
        const src = (kingdomData.research && kingdomData.research.by_source) || {};
        const SRC = [
            ['inat_observed',       'iNat sighting',  '#c5922a'],
            ['park_inventory+inat', 'Confirmed',      '#2d6a35'],
            ['park_inventory',      'Inventory only', '#9aa0a6'],
            ['prior_research',      'Prior research', '#5c6bc0'],
        ];
        const known = SRC.reduce((a, s) => a + (src[s[0]] || 0), 0);
        const other = research - known;

        if (research > 0) {
            html += '<div class="rb-title">Research pile by source</div>';
            html += '<div class="rb-bar">';
            for (const [key, , color] of SRC) {
                const n = src[key] || 0;
                if (n > 0) html += `<div class="rb-seg" title="${n}" `
                    + `style="width:${(n / research * 100).toFixed(1)}%;background:${color};"></div>`;
            }
            if (other > 0) html += `<div class="rb-seg" `
                + `style="width:${(other / research * 100).toFixed(1)}%;background:#ccc;"></div>`;
            html += '</div>';

            html += '<div class="rb-legend">';
            for (const [key, label, color] of SRC) {
                const n = src[key] || 0;
                if (n > 0) html += `<span class="rb-li">`
                    + `<i class="rb-dot" style="background:${color};"></i>${label} <b>${n}</b></span>`;
            }
            if (other > 0) html += `<span class="rb-li">`
                + `<i class="rb-dot" style="background:#ccc;"></i>Other <b>${other}</b></span>`;
            html += '</div>';
        }

        el.innerHTML = html;
    }

    function renderAttention(containerId, items) {
        const el = document.getElementById(containerId);
        if (!items || items.length === 0) {
            el.innerHTML = '<div class="attention-empty">All spotted species are publish-ready — nothing outstanding.</div>';
            return;
        }
        let html = '';
        for (const item of items) {
            const st = (item.status || 'spotted').toLowerCase();
            const stLabel = st === 'html' ? 'Published' : st.charAt(0).toUpperCase() + st.slice(1);
            html += `
                <div class="attention-item">
                    <span class="attention-id">${item.id}</span>
                    <span class="attention-name">${item.name}</span>
                    <span class="status-pill ${st}">${stLabel}</span>
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
    """Intake tab — browse research.json, preview, promote to spotted."""
    return f"""
    <!-- ░░ iNat Discovery panel ░░ -->
    <style>
    .discover-panel{{background:#fff;border:1px solid #e5e0d5;border-left:4px solid var(--gold);border-radius:10px;padding:14px 16px;margin-bottom:16px;}}
    .discover-head{{display:flex;align-items:center;justify-content:space-between;gap:12px;flex-wrap:wrap;}}
    .discover-title{{font-weight:700;font-size:15px;color:var(--green-deep);}}
    .discover-sub{{display:block;font-size:12px;color:var(--gray-600);margin-top:2px;}}
    .discover-scan-btn{{background:var(--green-mid);color:#fff;border:none;border-radius:7px;padding:8px 14px;font-size:13px;font-weight:600;cursor:pointer;white-space:nowrap;}}
    .discover-scan-btn:disabled{{opacity:.6;cursor:default;}}
    .discover-status{{font-size:13px;color:var(--gray-600);margin-top:8px;}}
    .discover-summary{{font-size:13px;color:var(--green-deep);font-weight:600;margin:10px 0 6px;}}
    .discover-new-grid{{display:flex;flex-direction:column;gap:8px;}}
    .discover-card{{display:flex;align-items:center;gap:12px;padding:8px 10px;border:1px solid #eee;border-radius:8px;background:var(--cream);}}
    .discover-card img{{width:46px;height:46px;border-radius:6px;object-fit:cover;flex:0 0 auto;background:#ddd;}}
    .discover-card .dc-body{{flex:1 1 auto;min-width:0;}}
    .discover-card .dc-name{{font-weight:600;color:var(--green-deep);font-size:14px;}}
    .discover-card .dc-sci{{font-style:italic;color:var(--gray-600);font-size:12px;}}
    .discover-card .dc-meta{{font-size:11px;color:#999;margin-top:1px;}}
    .discover-add-btn{{background:var(--gold);color:#3a2c08;border:none;border-radius:6px;padding:7px 12px;font-size:12px;font-weight:700;cursor:pointer;white-space:nowrap;}}
    .discover-add-btn:disabled{{opacity:.55;cursor:default;}}
    .discover-tracked-toggle{{margin-top:12px;font-size:12px;color:var(--green-mid);cursor:pointer;background:none;border:none;padding:0;text-decoration:underline;}}
    .discover-tracked-list{{margin-top:8px;max-height:220px;overflow:auto;border-top:1px dashed #ddd;padding-top:8px;}}
    .discover-tracked-row{{display:flex;justify-content:space-between;gap:8px;font-size:12px;padding:3px 0;color:var(--gray-600);}}
    .discover-pill{{font-size:10px;font-weight:700;padding:1px 7px;border-radius:9px;text-transform:uppercase;letter-spacing:.3px;}}
    .dp-research{{background:#eceff1;color:#546e7a;}}
    .dp-spotted{{background:#fdf0d5;color:#9a6b12;}}
    .dp-html{{background:#e3f0e5;color:#2d6a35;}}
    .discover-section-label{{font-size:12px;font-weight:700;letter-spacing:.3px;color:var(--green-deep);margin:14px 0 6px;}}
    .discover-card.ready{{border-left:3px solid var(--green-mid);background:#f1f7f1;}}
    .discover-open-btn{{background:var(--green-mid);color:#fff;border:none;border-radius:6px;padding:7px 12px;font-size:12px;font-weight:700;cursor:pointer;white-space:nowrap;}}
    .discover-revive-btn{{background:#c62828;color:#fff;border:none;border-radius:6px;padding:7px 12px;font-size:12px;font-weight:700;cursor:pointer;white-space:nowrap;}}
    .discover-empty{{font-size:13px;color:var(--gray-600);padding:6px 0;}}
    </style>
    <div class="discover-panel">
        <div class="discover-head">
            <div>
                <span class="discover-title">🔭 Discover new species</span>
                <span class="discover-sub">Scan the iNaturalist project and surface taxa not yet in research.json</span>
            </div>
            <button class="discover-scan-btn" id="discover-scan-btn" onclick="discoverScan()">🌐 Scan project</button>
        </div>
        <div class="discover-status" id="discover-status"></div>
        <div class="discover-results" id="discover-results"></div>
    </div>

    <!-- Top controls: kingdom toggle + status filter + count -->
    <div class="photos-controls">
        <div class="control-group">
            <span class="control-group-label">Kingdom</span>
            <div class="mode-toggle" id="intake-kingdom-toggle">
                <button class="active" onclick="intakeSwitchKingdom('plants')">🌱 Plants</button>
                <button onclick="intakeSwitchKingdom('wildlife')">🦎 Wildlife</button>
            </div>
        </div>
        <div class="control-group">
            <span class="control-group-label">Show</span>
            <div class="mode-toggle" id="intake-status-toggle">
                <button class="active" onclick="intakeSetStatus('research')">Research</button>
                <button onclick="intakeSetStatus('dead')">☠ Dead</button>
                <button onclick="intakeSetStatus('all')">All</button>
            </div>
        </div>
        <div class="control-group">
            <span class="control-group-label">Research Pool</span>
            <span id="intake-count" style="font-size:13px; color:var(--gray-600); font-weight:500;">—</span>
        </div>
    </div>

    <!-- Two-column layout: picker + detail -->
    <div class="photos-layout">
        <!-- Left: species picker from research.json -->
        <div class="species-picker">
            <div class="picker-header">
                <h3>📥 Research Pool</h3>
                <input type="text" class="picker-search" id="intake-search"
                       placeholder="Name, ID, or category…"
                       oninput="intakeRenderPicker()">
                <div class="picker-legend" style="margin-top:8px;">
                    <span><span class="dot" style="background:var(--green-mid);"></span> Both</span>
                    <span><span class="dot" style="background:#5c6bc0;"></span> Prior</span>
                    <span><span class="dot" style="background:var(--gold);"></span> iNat</span>
                    <span><span class="dot" style="background:var(--gray-300,#ccc);"></span> Sheet</span>
                </div>
            </div>
            <div class="picker-list" id="intake-picker-list">
                <div class="loading">Loading…</div>
            </div>
        </div>

        <!-- Right: detail card -->
        <div class="photos-main" id="intake-detail">
            <div class="photos-select-prompt">
                <div class="psp-icon">📥</div>
                <p>Select a species to preview</p>
                <p style="font-size:12px; color:var(--gray-400); margin-top:4px;">
                    Green-bordered = confirmed in inventory + iNat — strongest candidates</p>
            </div>
        </div>
    </div>

    <div class="toast" id="toast"></div>

    <script>
    let intakeKingdom = 'plants';
    let intakeStatusFilter = 'research';
    let intakeSpecies = [];
    let intakeSelected = null;

    const SOURCE_RANK = {{
        'park_inventory+inat': 0,
        'prior_research': 1,
        'inat_observed': 2,
        'park_inventory': 3,
    }};
    const SOURCE_META = {{
        'park_inventory+inat': {{
            cls: 'src-both', icon: '✦', label: 'Confirmed — Inventory + iNat',
            sub: 'In the park inventory spreadsheet AND observed on iNaturalist. Strongest candidate for promotion.',
        }},
        'prior_research': {{
            cls: 'src-prior', icon: '📋', label: 'Previously Researched',
            sub: 'Had content written in a prior signage session. Ready for review and promotion.',
        }},
        'inat_observed': {{
            cls: 'src-inat', icon: '🔍', label: 'iNat Observation Only',
            sub: 'Seen on iNaturalist but not in the park inventory. Verify before promoting.',
        }},
        'park_inventory': {{
            cls: 'src-inventory', icon: '📄', label: 'Inventory Only',
            sub: 'Listed in the park inventory spreadsheet. Not yet observed on iNaturalist.',
        }},
    }};

    function intakeToast(msg, isError) {{
        const el = document.getElementById('toast');
        el.textContent = msg;
        el.className = 'toast show' + (isError ? ' error' : '');
        clearTimeout(el._tid);
        el._tid = setTimeout(() => el.className = 'toast', Math.max(2400, msg.length * 45));
    }}

    function intakeSwitchKingdom(k) {{
        intakeKingdom = k;
        const btns = document.querySelectorAll('#intake-kingdom-toggle button');
        btns.forEach(b => b.classList.remove('active'));
        btns[k === 'plants' ? 0 : 1].classList.add('active');
        intakeSelected = null;
        intakeLoad();
    }}

    function intakeSetStatus(f) {{
        intakeStatusFilter = f;
        const btns = document.querySelectorAll('#intake-status-toggle button');
        btns.forEach(b => b.classList.remove('active'));
        const idx = {{research: 0, dead: 1, all: 2}}[f];
        btns[idx].classList.add('active');
        intakeRenderPicker();
        // Clear detail if the selected species is now hidden
        if (intakeSelected) {{
            const visible = intakeFilteredSpecies();
            if (!visible.find(s => s.id === intakeSelected)) {{
                intakeSelected = null;
                document.getElementById('intake-detail').innerHTML =
                    '<div class="photos-select-prompt"><div class="psp-icon">📥</div>'
                    + '<p>Select a species to preview</p></div>';
            }}
        }}
    }}

    async function intakeLoad() {{
        const picker = document.getElementById('intake-picker-list');
        picker.innerHTML = '<div class="loading">Loading…</div>';
        document.getElementById('intake-detail').innerHTML =
            '<div class="photos-select-prompt"><div class="psp-icon">📥</div>'
            + '<p>Select a species to preview</p></div>';
        try {{
            const resp = await fetch(`/api/intake/list?kingdom=${{intakeKingdom}}`);
            const data = await resp.json();
            intakeSpecies = data.species || [];
            intakeRenderPicker();
        }} catch (err) {{
            picker.innerHTML = '<div class="picker-empty">Error loading research data.</div>';
        }}
    }}

    function intakeFilteredSpecies() {{
        const q = (document.getElementById('intake-search')?.value || '').toLowerCase();
        let filtered = intakeSpecies;

        // Status filter
        if (intakeStatusFilter === 'research') {{
            filtered = filtered.filter(s => s.status === 'research');
        }} else if (intakeStatusFilter === 'dead') {{
            filtered = filtered.filter(s => s.status === 'died' || s.status === 'stolen');
        }}

        // Search
        if (q) {{
            filtered = filtered.filter(s =>
                (s.common_name || '').toLowerCase().includes(q) ||
                (s.scientific_name || '').toLowerCase().includes(q) ||
                (s.id || '').toLowerCase().includes(q) ||
                (s.category || '').toLowerCase().includes(q)
            );
        }}

        // Sort by source priority, then ID
        filtered.sort((a, b) => {{
            const ra = SOURCE_RANK[a.source] ?? 9;
            const rb = SOURCE_RANK[b.source] ?? 9;
            if (ra !== rb) return ra - rb;
            return a.id.localeCompare(b.id);
        }});

        return filtered;
    }}

    function intakeRenderPicker() {{
        const list = document.getElementById('intake-picker-list');
        const filtered = intakeFilteredSpecies();

        // Update count
        const total = intakeSpecies.length;
        const researchCount = intakeSpecies.filter(s => s.status === 'research').length;
        const deadCount = intakeSpecies.filter(s => s.status === 'died' || s.status === 'stolen').length;
        let countText = `${{filtered.length}} shown`;
        if (intakeStatusFilter === 'research') countText += ` of ${{researchCount}} research`;
        else if (intakeStatusFilter === 'dead') countText += ` of ${{deadCount}} dead/stolen`;
        else countText += ` of ${{total}} total`;
        document.getElementById('intake-count').textContent = countText;

        if (!filtered.length) {{
            list.innerHTML = '<div class="picker-empty">'
                + (intakeStatusFilter === 'dead' ? 'No dead/stolen species.' : 'No species match.')
                + '</div>';
            return;
        }}

        list.innerHTML = filtered.map(s => {{
            const active = s.id === intakeSelected ? 'active' : '';
            const name = s.common_name || s.scientific_name || s.id;
            const sci = s.scientific_name || '';
            const isDead = s.status === 'died' || s.status === 'stolen';
            const deadCls = isDead ? 'is-dead' : '';
            const sm = SOURCE_META[s.source] || {{}};
            const srcCls = sm.cls || '';
            const srcLabel = ({{
                'park_inventory+inat': 'Inventory + iNat',
                'prior_research': 'Prior research',
                'inat_observed': 'iNat sighting',
                'park_inventory': 'Inventory'
            }})[s.source] || '';
            const obs = (typeof s.inat_obs_count === 'number' && s.inat_obs_count > 0)
                ? `${{s.inat_obs_count}} obs` : '';
            const meta = [s.id, obs, srcLabel].filter(Boolean).join(' · ');
            const content = `<span class="pi-content ${{s.content_filled > 0 ? 'has' : 'none'}}" title="${{s.content_filled}} of ${{s.content_total}} content fields filled">${{s.content_filled}}/${{s.content_total}}</span>`;
            const actions = isDead
                ? `<button class="pi-btn pi-revive" onclick="event.stopPropagation(); intakeSetSpeciesStatus('${{s.id}}','research')">↩ Revive</button>`
                : `<button class="pi-btn pi-work" onclick="event.stopPropagation(); intakeWorkIt('${{s.id}}')">Work it</button>`
                  + `<button class="pi-btn pi-dead" onclick="event.stopPropagation(); intakeSetSpeciesStatus('${{s.id}}','died')">Mark dead</button>`;
            return `<div class="picker-item card ${{active}} ${{srcCls}} ${{deadCls}}" onclick="intakeSelect('${{s.id}}')">
                <div class="pi-top">
                    <div class="pi-name">
                        <span class="pi-common">${{esc(name)}}</span>
                        <span class="pi-sci">${{esc(sci)}}</span>
                    </div>
                    ${{content}}
                </div>
                <div class="pi-meta">${{esc(meta)}}</div>
                <div class="pi-actions">${{actions}}</div>
            </div>`;
        }}).join('');
    }}

    function intakeWorkIt(id) {{
        intakeSelect(id);
        const el = document.getElementById('intake-detail');
        if (el) el.scrollIntoView({{behavior: 'smooth', block: 'start'}});
    }}

    async function intakeSelect(id) {{
        intakeSelected = id;
        intakeRenderPicker();
        const detail = document.getElementById('intake-detail');
        detail.innerHTML = '<div class="loading">Loading…</div>';
        try {{
            const resp = await fetch(`/api/intake/detail?id=${{id}}`);
            const data = await resp.json();
            if (data.error) {{
                detail.innerHTML = `<div class="card"><p style="color:#c62828">${{esc(data.error)}}</p></div>`;
                return;
            }}
            intakeRenderDetail(data.species);
        }} catch (err) {{
            detail.innerHTML = '<div class="card"><p>Error loading details.</p></div>';
        }}
    }}

    function isFilled(val) {{
        if (val === null || val === undefined) return false;
        if (typeof val === 'string') return val.trim().length > 0;
        if (Array.isArray(val)) return val.length > 0;
        if (typeof val === 'object') return Object.keys(val).length > 0;
        return true;
    }}

    function fieldSummary(val) {{
        if (!isFilled(val)) return '';
        if (Array.isArray(val)) return val.length + ' item' + (val.length !== 1 ? 's' : '');
        if (typeof val === 'object' && val !== null) {{
            const blocks = val.blocks;
            if (Array.isArray(blocks)) return blocks.length + ' section' + (blocks.length !== 1 ? 's' : '');
            return 'present';
        }}
        if (typeof val === 'string') {{
            return val.length > 60 ? esc(val.substring(0, 57)) + '…' : esc(val);
        }}
        return String(val);
    }}

    function intakeRenderDetail(sp) {{
        const detail = document.getElementById('intake-detail');
        const isPlant = intakeKingdom === 'plants';
        const sciName = isPlant ? (sp.botanical_name || '') : (sp.scientific_name || '');
        const commonName = sp.common_name || sciName || sp.id;
        const isDead = sp.status === 'died' || sp.status === 'stolen';

        // Source banner
        const src = sp.research_source || '';
        const sm = SOURCE_META[src] || {{ cls: '', icon: '❓', label: 'Unknown source', sub: '' }};
        const sourceBanner = `<div class="intake-source-banner ${{sm.cls}}">
            <span class="isb-icon">${{sm.icon}}</span>
            <span class="isb-label">${{sm.label}}<span class="isb-sub">${{sm.sub}}</span></span>
        </div>`;

        // Dead banner
        const deadBanner = isDead
            ? `<div class="intake-dead-banner">☠ This species is marked <strong>${{sp.status}}</strong>
                   — it will not appear in the research pool.
                   <button class="pub-btn revive-btn" style="margin-left:auto;"
                           onclick="intakeSetSpeciesStatus('${{sp.id}}', 'research')">↩ Revive</button>
               </div>`
            : '';

        // Metadata chips
        const chips = [];
        if (sp.category) chips.push(`<span class="intake-chip">${{esc(sp.category)}}</span>`);
        if (sp.feature_tier) chips.push(`<span class="intake-chip">${{esc(sp.feature_tier)}}</span>`);
        if (sp.native === true) chips.push('<span class="intake-chip native">Native</span>');
        else if (sp.native === false) chips.push('<span class="intake-chip non-native">Non-native</span>');
        if (sp.has_sign) chips.push('<span class="intake-chip sign">Has Sign</span>');
        if (sp.sign_level && sp.sign_level !== 'Species')
            chips.push(`<span class="intake-chip">${{esc(sp.sign_level)}}</span>`);
        if (!isPlant && sp.animal_group)
            chips.push(`<span class="intake-chip">${{esc(sp.animal_group)}}</span>`);

        // iNat row (link + check button)
        let inatHtml = '';
        if (sp.inat_taxon_id) {{
            const obsUrl = `https://www.inaturalist.org/observations?project_id=palma-sola-botanical-park&taxon_id=${{sp.inat_taxon_id}}&verifiable=any`;
            const taxUrl = `https://www.inaturalist.org/taxa/${{sp.inat_taxon_id}}`;
            inatHtml = `
                <div class="intake-inat-row">
                    <a href="${{obsUrl}}" target="_blank" class="inat-link-btn">
                        🔍 View PSBP Observations</a>
                    <a href="${{taxUrl}}" target="_blank" class="inat-link-btn"
                       style="border-color:var(--gray-200); background:white; color:var(--gray-600);">
                        📖 iNat Taxon Page</a>
                    <button class="inat-check-btn" id="inat-check-btn"
                            onclick="intakeCheckInat(${{sp.inat_taxon_id}})">
                        🌐 Check Quality</button>
                </div>
                <div id="inat-summary"></div>`;
        }} else {{
            inatHtml = `<div class="intake-inat-row">
                <span style="font-size:12px; color:var(--gray-400);">No iNat taxon ID — cannot link to observations</span>
            </div>`;
        }}

        // Content field audit
        const contentFields = isPlant ? [
            ['quick_hits',        'Quick Hits'],
            ['more_information',  'Description'],
            ['origin',            'Origin'],
            ['wildlife_value',    'Wildlife Value'],
            ['reproduction',      'Growth & Form'],
            ['growing_conditions','Growing Conditions'],
            ['edibility',         'Edibility'],
            ['toxicity',          'Toxicity'],
            ['alternate_names',   'Alternate Names'],
            ['butterfly_host',    'Butterfly Host'],
        ] : [
            ['quick_hits',        'Quick Hits'],
            ['more_information',  'Description'],
            ['range_and_origin',  'Range & Origin'],
            ['diet',              'Diet'],
            ['behavior',          'Behavior'],
            ['habitat',           'Habitat'],
            ['sounds',            'Sounds'],
            ['identification',    'ID Tips'],
            ['also_known_as',     'Also Known As'],
            ['seasonality',       'Seasonality'],
            ['size',              'Size'],
        ];

        let filled = 0;
        const fieldRows = contentFields.map(([key, label]) => {{
            const val = sp[key];
            const ok = isFilled(val);
            if (ok) filled++;
            const det = ok ? fieldSummary(val) : '';
            return `<div class="intake-field ${{ok ? 'filled' : 'empty'}}">
                <span class="if-check">${{ok ? '✓' : '✗'}}</span>
                <span class="if-label">${{label}}</span>
                ${{det ? `<span class="if-detail">${{det}}</span>` : ''}}
            </div>`;
        }}).join('');

        // Quick hits preview
        let quickHitsHtml = '';
        if (sp.quick_hits && sp.quick_hits.length) {{
            const preview = sp.quick_hits.slice(0, 2);
            quickHitsHtml = `
                <div class="intake-section">
                    <h3>Quick Hits Preview</h3>
                    ${{preview.map(h => `<p class="intake-qh">• ${{esc(h)}}</p>`).join('')}}
                    ${{sp.quick_hits.length > 2
                        ? `<p class="intake-qh-more">+ ${{sp.quick_hits.length - 2}} more</p>` : ''}}
                </div>`;
        }}

        // Taxonomy
        let taxHtml = '';
        if (sp.taxonomy) {{
            const parts = [];
            if (sp.taxonomy.family) parts.push(`Family: ${{esc(sp.taxonomy.family)}}`);
            if (sp.taxonomy.genus) parts.push(`Genus: <em>${{esc(sp.taxonomy.genus)}}</em>`);
            if (parts.length)
                taxHtml = `<div class="intake-taxonomy">${{parts.join(' · ')}}</div>`;
        }}

        // Internal notes
        let notesHtml = '';
        if (sp.internal_notes)
            notesHtml = `<div class="intake-notes"><strong>Notes:</strong> ${{esc(sp.internal_notes)}}</div>`;

        // Tags
        let tagsHtml = '';
        if (sp.tags && sp.tags.length) {{
            tagsHtml = `<div class="pub-tags" style="margin-bottom:12px;">
                <span class="pub-tags-label">tags</span>
                ${{sp.tags.map(t => `<span class="pub-tag">${{esc(t)}}</span>`).join('')}}
            </div>`;
        }}

        // Action buttons
        const canPromote = sp.status === 'research';
        let actionsHtml = '';
        if (isDead) {{
            actionsHtml = `<div class="intake-actions">
                <button class="pub-btn revive-btn" onclick="intakeSetSpeciesStatus('${{sp.id}}', 'research')">
                    ↩ Revive to Research</button>
            </div>`;
        }} else {{
            actionsHtml = `<div class="intake-actions">
                <button class="pub-btn promote" ${{canPromote ? '' : 'disabled'}}
                        title="${{canPromote ? 'Move to signage JSON as spotted' : 'Cannot promote — status is ' + sp.status}}"
                        onclick="intakePromote('${{sp.id}}')">
                    ⬆ Promote to Spotted</button>
                <button class="pub-btn dead-btn"
                        onclick="intakeSetSpeciesStatus('${{sp.id}}', 'died')"
                        title="Park this species as dead / no longer in park">
                    ☠ Mark Dead</button>
            </div>`;
        }}

        detail.innerHTML = `
            <div class="card intake-detail-card ${{isDead ? 'is-dead' : ''}}">
                <div class="intake-header">
                    <span class="pub-id">${{sp.id}}</span>
                    <div>
                        <h2>${{esc(commonName)}}</h2>
                        <div class="intake-sci">${{esc(sciName)}}</div>
                    </div>
                    <span class="status-pill ${{sp.status === 'research' ? 'research' : 'spotted'}}">${{esc(sp.status)}}</span>
                </div>

                ${{deadBanner}}
                ${{sourceBanner}}
                ${{taxHtml}}
                <div class="intake-chips">${{chips.join('')}}</div>
                ${{tagsHtml}}
                ${{inatHtml}}

                <div class="intake-section">
                    <h3>Content Fields
                        <span class="intake-fill-count">${{filled}} of ${{contentFields.length}}</span>
                    </h3>
                    <div class="intake-fields">${{fieldRows}}</div>
                </div>

                ${{quickHitsHtml}}
                ${{notesHtml}}
                ${{actionsHtml}}
            </div>
        `;
    }}

    async function intakeCheckInat(taxonId) {{
        const btn = document.getElementById('inat-check-btn');
        const sumDiv = document.getElementById('inat-summary');
        if (btn) {{ btn.disabled = true; btn.textContent = '⏳ Checking…'; }}
        try {{
            const resp = await fetch(`/api/intake/inat-check?taxon_id=${{taxonId}}`);
            const data = await resp.json();
            if (data.error) {{
                sumDiv.innerHTML = `<div class="inat-summary" style="color:#c62828;">
                    Failed: ${{esc(data.error)}}</div>`;
            }} else {{
                const rg = data.quality_grades.research || 0;
                const ni = data.quality_grades.needs_id || 0;
                const cas = data.quality_grades.casual || 0;
                const total = data.total_observations;
                const obs = data.unique_observers;
                const latest = data.latest_observation || '—';

                const strength = (rg > 0 && obs > 1) ? 'is-strong' : (total <= 1 || obs <= 1) ? 'is-weak' : '';
                const verdict = rg > 0 && obs > 1
                    ? '✓ Solid — multiple observers, research-grade IDs'
                    : total === 0
                    ? '⚠ No observations found in the PSBP project'
                    : obs <= 1
                    ? '⚠ Single observer — needs independent confirmation'
                    : '⚠ No research-grade IDs yet';

                sumDiv.innerHTML = `<div class="inat-summary">
                    <strong>${{total}}</strong> observation${{total !== 1 ? 's' : ''}} ·
                    <strong>${{obs}}</strong> observer${{obs !== 1 ? 's' : ''}} ·
                    <strong>${{rg}}</strong> research grade ·
                    <strong>${{ni}}</strong> needs ID
                    ${{cas ? ` · <strong>${{cas}}</strong> casual` : ''}}
                    · latest: ${{latest}}<br>
                    <span class="${{strength}}">${{verdict}}</span>
                </div>`;
            }}
        }} catch (err) {{
            sumDiv.innerHTML = `<div class="inat-summary" style="color:#c62828;">
                Network error — is the dashboard online?</div>`;
        }}
        if (btn) {{ btn.disabled = false; btn.textContent = '🌐 Check Quality'; }}
    }}

    async function intakeSetSpeciesStatus(id, newStatus) {{
        const label = newStatus === 'died' ? 'dead' : 'research';
        const sp = intakeSpecies.find(s => s.id === id);
        const name = sp ? (sp.common_name || sp.id) : id;

        if (newStatus === 'died' && !confirm(`Mark ${{name}} as dead?\\nIt will move to the Dead view.`)) return;

        try {{
            const resp = await fetch('/api/intake/set-status', {{
                method: 'POST',
                headers: {{'Content-Type': 'application/json'}},
                body: JSON.stringify({{id: id, status: newStatus}})
            }});
            const data = await resp.json();
            if (data.ok) {{
                const verb = newStatus === 'died' ? 'Marked dead' : 'Revived';
                intakeToast(`${{verb}}: ${{data.common_name || id}}`);
                intakeSelected = null;
                intakeLoad();
            }} else {{
                intakeToast(data.error || 'Status change failed', true);
            }}
        }} catch (err) {{
            intakeToast('Error: ' + err.message, true);
        }}
    }}

    async function intakePromote(id) {{
        // Duplicate check first
        try {{
            const checkResp = await fetch('/api/intake/check', {{
                method: 'POST',
                headers: {{'Content-Type': 'application/json'}},
                body: JSON.stringify({{kingdom: intakeKingdom, id: id}})
            }});
            const checkData = await checkResp.json();
            if (checkData.has_duplicates) {{
                const dupeList = checkData.duplicates.map(d =>
                    `  ${{d.id}}  ${{d.common_name}}  (${{d.reasons.join(', ')}})`
                ).join('\\n');
                if (!confirm('Potential duplicates found in '
                    + intakeKingdom + ' signage:\\n\\n' + dupeList
                    + '\\n\\nPromote anyway?')) {{
                    return;
                }}
            }}
        }} catch (err) {{
            if (!confirm('Could not check for duplicates. Promote anyway?')) return;
        }}

        const btn = document.querySelector('.intake-actions .promote');
        if (btn) {{ btn.disabled = true; btn.textContent = 'Promoting…'; }}

        try {{
            const resp = await fetch('/api/intake/promote', {{
                method: 'POST',
                headers: {{'Content-Type': 'application/json'}},
                body: JSON.stringify({{kingdom: intakeKingdom, id: id}})
            }});
            const data = await resp.json();
            if (data.ok) {{
                let msg = `Promoted ${{data.common_name || data.id}} → spotted`;
                if (data.duplicates_warned && data.duplicates_warned.length) {{
                    msg += ` (note: ${{data.duplicates_warned.length}} similar species in signage)`;
                }}
                if (data.hero_report) {{
                    if (data.hero_report.imported) {{
                        msg += ` · ✓ hero imported (${{esc(data.hero_report.photographer || 'CC')}})`;
                    }} else {{
                        msg += ` · ⚠ no hero: ${{esc(data.hero_report.reason || 'unavailable')}}`;
                    }}
                }}
                intakeToast(msg);
                intakeSelected = null;
                intakeLoad();
            }} else {{
                intakeToast(data.error || 'Promote failed', true);
                if (btn) {{ btn.disabled = false; btn.textContent = '⬆ Promote to Spotted'; }}
            }}
        }} catch (err) {{
            intakeToast('Error: ' + err.message, true);
            if (btn) {{ btn.disabled = false; btn.textContent = '⬆ Promote to Spotted'; }}
        }}
    }}

    function esc(s) {{
        if (!s) return '';
        const d = document.createElement('div'); d.textContent = s; return d.innerHTML;
    }}

    // ░░ iNat Discovery ░░
    let discoverNew = [];
    function discoverStatusPill(s) {{
        const k = (s === 'html') ? 'dp-html' : (s === 'spotted') ? 'dp-spotted' : 'dp-research';
        return `<span class="discover-pill ${{k}}">${{esc(s || '?')}}</span>`;
    }}
    async function discoverScan() {{
        const btn = document.getElementById('discover-scan-btn');
        const status = document.getElementById('discover-status');
        const results = document.getElementById('discover-results');
        btn.disabled = true; btn.textContent = '⏳ Scanning…';
        status.textContent = 'Querying the iNaturalist project — this can take a few seconds…';
        results.innerHTML = '';
        try {{
            const resp = await fetch('/api/intake/discover');
            const data = await resp.json();
            if (!data.ok) {{ status.textContent = data.error || 'Scan failed.'; }}
            else {{ discoverRender(data); status.textContent = ''; }}
        }} catch (err) {{
            status.innerHTML = '<span style="color:#c62828;">Network error — is the dashboard online?</span>';
        }}
        btn.disabled = false; btn.textContent = '🌐 Re-scan project';
    }}
    function discoverRender(d) {{
        discoverNew = d.new || [];
        const ready = d.ready || [];
        const results = document.getElementById('discover-results');
        const sm = d.pipeline_summary || {{}};
        const smBits = Object.keys(sm).sort().map(k => `${{sm[k]}} ${{k}}`).join(' · ');
        let html = `<div class="discover-summary">Scanned ${{d.scanned}} taxa · `
            + `<span style="color:var(--gold);">${{d.new_count}} new</span> · `
            + `<span style="color:var(--green-mid);">${{d.ready_count}} ready to advance</span> · `
            + `${{d.pipeline_count}} in pipeline${{smBits ? ' (' + smBits + ')' : ''}}</div>`;

        // Ready to advance — in research.json AND now observed on iNat
        if (ready.length) {{
            html += '<div class="discover-section-label">⭐ In your research pile and now observed — ready to advance</div>';
            html += '<div class="discover-new-grid">';
            ready.forEach(it => {{
                const photo = it.default_photo ? `<img src="${{esc(it.default_photo)}}" alt="">` : '<img alt="">';
                const cn = it.common_name || it.scientific_name;
                const dead = it.revivable;
                const meta = `${{esc(it.psbp_id)}} · ${{it.obs_count}} obs`
                    + (dead ? ` · <span style="color:#c62828;font-weight:600;">marked ${{esc(it.status)}} — you observed a live one</span>` : '');
                const actions = dead
                    ? `<button class="discover-revive-btn" onclick="discoverRevive('${{it.psbp_id}}', this)">↩ Revive</button>`
                    : `<button class="discover-open-btn" onclick="discoverOpenInPicker('${{it.psbp_id}}','${{it.kingdom}}','${{it.status}}')">→ Work it</button>`;
                html += `<div class="discover-card ready">
                    ${{photo}}
                    <div class="dc-body">
                        <div class="dc-name">${{esc(cn)}} ${{discoverStatusPill(it.status)}}</div>
                        <div class="dc-sci">${{esc(it.scientific_name)}}</div>
                        <div class="dc-meta">${{meta}}</div>
                    </div>
                    ${{actions}}
                </div>`;
            }});
            html += '</div>';
        }}

        // Brand new — not tracked anywhere
        if (discoverNew.length) {{
            html += '<div class="discover-section-label">🆕 Brand new — not in the system yet</div>';
            html += '<div class="discover-new-grid">';
            discoverNew.forEach((it, i) => {{
                const photo = it.default_photo ? `<img src="${{esc(it.default_photo)}}" alt="">` : '<img alt="">';
                const cn = it.common_name || it.scientific_name;
                const meta = `${{it.obs_count}} obs · ${{esc(it.iconic || it.rank || '')}}`;
                html += `<div class="discover-card" id="dcard-${{i}}">
                    ${{photo}}
                    <div class="dc-body">
                        <div class="dc-name">${{esc(cn)}}</div>
                        <div class="dc-sci">${{esc(it.scientific_name)}}</div>
                        <div class="dc-meta">${{meta}}</div>
                    </div>
                    <button class="discover-add-btn" onclick="discoverAdd(${{i}}, this)">+ Add to research</button>
                </div>`;
            }});
            html += '</div>';
        }}

        if (!ready.length && !discoverNew.length) {{
            html += '<div class="discover-empty">Nothing new or newly-observed — everything you\\'ve photographed is already moving through the pipeline. 🎉</div>';
        }}

        // Already in the pipeline (spotted / published) — collapsed
        const pipe = d.pipeline || [];
        if (pipe.length) {{
            html += `<button class="discover-tracked-toggle" onclick="discoverToggleTracked()">▸ Show ${{pipe.length}} already in the pipeline</button>`;
            html += '<div class="discover-tracked-list" id="discover-tracked-list" style="display:none;">';
            pipe.slice().sort((a, b) => (a.scientific_name || '').localeCompare(b.scientific_name || '')).forEach(t => {{
                html += `<div class="discover-tracked-row">
                    <span>${{esc(t.common_name || t.scientific_name)}} <span style="color:#aaa;">${{esc(t.psbp_id || '')}}</span></span>
                    ${{discoverStatusPill(t.status)}}
                </div>`;
            }});
            html += '</div>';
        }}
        results.innerHTML = html;
    }}
    function discoverOpenInPicker(id, kingdom, status) {{
        const go = () => {{
            intakeSelect(id);
            const el = document.getElementById('intake-detail');
            if (el) el.scrollIntoView({{behavior: 'smooth', block: 'start'}});
        }};
        let delay = 0;
        if (kingdom && kingdom !== intakeKingdom) {{ intakeSwitchKingdom(kingdom); delay = 450; }}
        if (status && status !== 'research') {{ intakeSetStatus('all'); }}
        setTimeout(go, delay);
    }}
    async function discoverRevive(id, btn) {{
        btn.disabled = true; btn.textContent = 'Reviving…';
        try {{
            const resp = await fetch('/api/intake/set-status', {{
                method: 'POST', headers: {{'Content-Type': 'application/json'}},
                body: JSON.stringify({{id: id, status: 'research'}})
            }});
            const data = await resp.json();
            if (data.ok) {{
                intakeToast('Revived ' + (data.common_name || id) + ' → research');
                btn.textContent = '✓ Revived'; intakeLoad();
            }} else {{
                intakeToast(data.error || 'Revive failed', true);
                btn.disabled = false; btn.textContent = '↩ Revive';
            }}
        }} catch (err) {{
            intakeToast('Error: ' + err.message, true);
            btn.disabled = false; btn.textContent = '↩ Revive';
        }}
    }}
    function discoverToggleTracked() {{
        const el = document.getElementById('discover-tracked-list');
        const btn = document.querySelector('.discover-tracked-toggle');
        if (!el) return;
        const open = el.style.display !== 'none';
        el.style.display = open ? 'none' : 'block';
        if (btn) btn.textContent = (open ? '▸ Show ' : '▾ Hide ') + el.children.length + ' already in the pipeline';
    }}
    async function discoverAdd(i, btn) {{
        const it = discoverNew[i];
        if (!it) return;
        btn.disabled = true; btn.textContent = 'Adding…';
        try {{
            const resp = await fetch('/api/intake/add-research', {{
                method: 'POST', headers: {{'Content-Type': 'application/json'}},
                body: JSON.stringify({{
                    taxon_id: it.taxon_id, scientific_name: it.scientific_name,
                    common_name: it.common_name, iconic: it.iconic, obs_count: it.obs_count
                }})
            }});
            const data = await resp.json();
            if (data.ok) {{
                let msg = `Added ${{data.common_name || data.scientific_name}} → ${{data.id}} (${{data.kingdom}})`;
                if (data.warning) msg += ` ⚠ ${{data.warning}}`;
                intakeToast(msg);
                const card = document.getElementById('dcard-' + i);
                if (card) card.style.opacity = .4;
                btn.textContent = '✓ ' + data.id;
                intakeLoad();
            }} else {{
                intakeToast(data.error || 'Add failed', true);
                btn.disabled = false; btn.textContent = '+ Add to research';
            }}
        }} catch (err) {{
            intakeToast('Error: ' + err.message, true);
            btn.disabled = false; btn.textContent = '+ Add to research';
        }}
    }}

    intakeLoad();
    </script>
    """


def render_photos():
    """Photos tab — review mode: browse, crown heroes, tag roles, trash."""
    plant_tags_js = json.dumps(PLANT_PHOTO_TAGS)
    wildlife_tags_js = json.dumps(WILDLIFE_PHOTO_TAGS)
    return f"""
    <!-- Top controls: kingdom + mode -->
    <div class="photos-controls">
        <div class="control-group">
            <span class="control-group-label">Kingdom</span>
            <div class="mode-toggle" id="photos-mode-toggle">
                <button class="active" onclick="switchKingdom('plants')">🌱 Plants</button>
                <button onclick="switchKingdom('wildlife')">🦎 Wildlife</button>
            </div>
        </div>
        <div class="control-group">
            <span class="control-group-label">Mode</span>
            <div class="mode-toggle" id="photos-workmode-toggle">
                <button class="active" onclick="switchWorkMode('review')">✓ Review</button>
                <button onclick="switchWorkMode('triage')">🔍 Find Photos</button>
            </div>
        </div>
    </div>

    <div class="photos-layout">
        <!-- Species picker sidebar -->
        <div class="species-picker">
            <div class="picker-header">
                <h3>Select Species</h3>
                <input type="text" class="picker-search" id="picker-search"
                       placeholder="Filter by name or ID…"
                       oninput="filterPicker()">
                <div class="picker-legend" id="picker-legend" style="display:none;">
                    <span><span class="dot green"></span>in system</span>
                    <span><span class="dot yellow"></span>to look at</span>
                    <span><span class="dot red"></span>set aside / blocked</span>
                </div>
                <button class="new-only-btn" id="new-only-btn" style="display:none;"
                        onclick="toggleNewOnly()">
                    🟡 New only <span class="no-count" id="new-only-count">0</span>
                </button>
                <button class="scan-all-btn" id="scan-all-btn" style="display:none;"
                        onclick="scanAll()">⟳ Scan all for new photos</button>
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

    <!-- Persistent scan-all result summary (stays until dismissed) -->
    <div class="scan-summary" id="scan-summary">
        <div class="scan-summary-head">
            <div>
                <div class="scan-summary-headline" id="scan-summary-headline"></div>
                <div class="scan-summary-sub" id="scan-summary-sub"></div>
                <div class="scan-summary-time" id="scan-summary-time"></div>
            </div>
            <button class="scan-summary-close" onclick="dismissScanSummary()" title="Dismiss">&times;</button>
        </div>
    </div>

    <!-- Scan-all progress banner -->
    <div class="scan-progress" id="scan-progress">
        <div class="scan-progress-inner">
            <div class="scan-progress-head">
                <span id="scan-progress-label">Scanning…</span>
                <span id="scan-progress-count">0 / 0</span>
            </div>
            <div class="scan-progress-track">
                <div class="scan-progress-fill" id="scan-progress-fill"></div>
            </div>
            <div class="scan-progress-current" id="scan-progress-current"></div>
        </div>
    </div>

    <!-- Toast -->
    <div class="toast" id="toast"></div>

    <!-- Fetch-more modal -->
    <div class="focus-modal" id="fetch-modal" onclick="if(event.target.id==='fetch-modal')closeFetchModal()">
        <div class="fetch-modal-inner">
            <div class="focus-modal-header">
                <span>Fetch photos from iNaturalist</span>
                <button class="focus-close" onclick="closeFetchModal()">✕</button>
            </div>
            <div class="fetch-body">
                <p class="fetch-species" id="fetch-species-name"></p>
                <button class="fetch-choice" onclick="doFetch('new')">
                    <span class="fc-title">🔍 Look for new photos</span>
                    <span class="fc-sub">Scan iNaturalist for CC-licensed photos not yet decided</span>
                </button>
                <button class="fetch-choice" onclick="doFetch('skipped')">
                    <span class="fc-title">↩ Revisit set-aside photos</span>
                    <span class="fc-sub">Bring back photos you set aside earlier (blocked stay hidden)</span>
                </button>
            </div>
        </div>
    </div>

    <!-- Focus editor modal -->
    <div class="focus-modal" id="focus-modal" onclick="if(event.target.id==='focus-modal')closeFocusEditor()">
        <div class="focus-modal-inner">
            <div class="focus-modal-header">
                <span>Set focus point — click the image where the subject is</span>
                <button class="focus-close" onclick="closeFocusEditor()">✕</button>
            </div>
            <div class="focus-stage" id="focus-stage" onclick="placeFocus(event)">
                <img id="focus-img" src="" alt="">
                <div class="focus-crosshair" id="focus-crosshair"></div>
            </div>
            <div class="focus-modal-footer">
                <span class="focus-coords" id="focus-coords">50% 50%</span>
                <div class="focus-actions">
                    <button class="focus-btn-cancel" onclick="closeFocusEditor()">Cancel</button>
                    <button class="focus-btn-save" id="focus-save" onclick="saveFocus()">Save focus</button>
                </div>
            </div>
            <div class="focus-preview-note">
                The crop preview on the card updates to match. For the hero,
                this also sets how the published page crops the image.
            </div>
        </div>
    </div>

    <script>
    // ── State ──────────────────────────────────────────────────
    let currentKingdom = 'plants';
    let currentSpeciesId = null;
    let allSpecies = [];
    let workMode = 'review';  // 'review' or 'triage'
    let newOnly = false;       // Find Photos: show only species with new photos to review
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

    // ── Work mode toggle (Review / Triage) ────────────────────
    function switchWorkMode(m) {{
        workMode = m;
        const btns = document.querySelectorAll('#photos-workmode-toggle button');
        btns.forEach(b => b.classList.remove('active'));
        btns[m === 'review' ? 0 : 1].classList.add('active');
        // Show triage legend + scan-all + new-only only in find-photos mode
        document.getElementById('picker-legend').style.display =
            m === 'triage' ? 'flex' : 'none';
        document.getElementById('scan-all-btn').style.display =
            m === 'triage' ? 'block' : 'none';
        const nob = document.getElementById('new-only-btn');
        nob.style.display = m === 'triage' ? 'flex' : 'none';
        if (m !== 'triage') {{ newOnly = false; nob.classList.remove('active'); }}
        filterPicker();
        // Reset the main panel
        const prompt = m === 'triage'
            ? `<div class="photos-select-prompt">
                   <div class="psp-icon">🔍</div>
                   <p>Select a species to find more iNaturalist photos</p>
                   <p style="font-size:12px;">Promote to hero/gallery, set aside, or block</p>
               </div>`
            : `<div class="photos-select-prompt">
                   <div class="psp-icon">📷</div>
                   <p>Select a species to manage its photos</p>
               </div>`;
        document.getElementById('photos-main').innerHTML = prompt;
        if (currentSpeciesId) selectSpecies(currentSpeciesId);
    }}

    // ── Kingdom toggle ────────────────────────────────────────
    function switchKingdom(k) {{
        currentKingdom = k;
        currentSpeciesId = null;
        const btns = document.querySelectorAll('#photos-mode-toggle button');
        btns.forEach(b => b.classList.remove('active'));
        btns[k === 'plants' ? 0 : 1].classList.add('active');
        loadPickerList();
        loadLastScanSummary();
        const icon = workMode === 'triage' ? '🔍' : '📷';
        document.getElementById('photos-main').innerHTML = `
            <div class="photos-select-prompt">
                <div class="psp-icon">${{icon}}</div>
                <p>Select a species to ${{workMode === 'triage' ? 'find more photos for' : 'manage'}} it</p>
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
            filterPicker();
        }} catch (err) {{
            list.innerHTML = '<div class="picker-empty">Error loading species</div>';
        }}
    }}

    function statusPill(status) {{
        const s = (status || '').toLowerCase();
        const labels = {{ html: 'Published', spotted: 'Spotted', research: 'Research' }};
        const label = labels[s] || status || 'unknown';
        return `<span class="status-pill ${{s}}">${{label}}</span>`;
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
            if (workMode === 'triage') {{
                // Triage: show GREEN / YELLOW / RED counts
                const g = sp.green || 0, y = sp.yellow || 0, r = sp.red || 0;
                const noTaxon = !sp.has_taxon
                    ? '<span class="pi-warn" title="No iNat taxon ID — cannot scan">⚠</span>'
                    : '';
                html += `
                    <div class="picker-item${{active}}" onclick="selectSpecies('${{sp.id}}')" data-id="${{sp.id}}">
                        <div class="pi-name">
                            <span class="pi-common">${{esc(sp.common_name || sp.id)}} ${{noTaxon}}</span>
                            <span class="pi-sci">${{esc(sp.scientific_name)}}</span>
                            ${{statusPill(sp.status)}}
                        </div>
                        <span class="pi-counts">
                            <span class="pc green" title="${{g}} in system">${{g}}</span>
                            <span class="pc yellow" title="${{y}} to look at">${{y}}</span>
                            <span class="pc red" title="${{r}} set aside or blocked">${{r}}</span>
                        </span>
                    </div>`;
            }} else {{
                // Review: photo count + hero dot
                const badgeCls = sp.photo_count > 0 ? 'has-photos' : 'no-photos';
                const heroCls = sp.has_hero ? '' : ' none';
                html += `
                    <div class="picker-item${{active}}" onclick="selectSpecies('${{sp.id}}')" data-id="${{sp.id}}">
                        <div class="pi-hero-dot${{heroCls}}" title="${{sp.has_hero ? 'Has hero' : 'No hero'}}"></div>
                        <div class="pi-name">
                            <span class="pi-common">${{esc(sp.common_name || sp.id)}}</span>
                            <span class="pi-sci">${{esc(sp.scientific_name)}}</span>
                            ${{statusPill(sp.status)}}
                        </div>
                        <span class="pi-badge ${{badgeCls}}">${{sp.photo_count}}</span>
                    </div>`;
            }}
        }}
        list.innerHTML = html;
    }}

    // Combined picker filter: search text + (in triage) the New-only toggle.
    // When New-only is on, also sort most-new-first so the biggest queues lead.
    function pickerFiltered() {{
        let list = allSpecies.slice();
        if (newOnly && workMode === 'triage') {{
            list = list.filter(sp => (sp.yellow || 0) > 0);
            list.sort((a, b) => (b.yellow || 0) - (a.yellow || 0));
        }}
        const q = document.getElementById('picker-search').value.toLowerCase().trim();
        if (q) {{
            list = list.filter(sp =>
                (sp.common_name || '').toLowerCase().includes(q) ||
                (sp.scientific_name || '').toLowerCase().includes(q) ||
                (sp.id || '').toLowerCase().includes(q));
        }}
        return list;
    }}

    function filterPicker() {{
        const list = pickerFiltered();
        if (newOnly && workMode === 'triage' && !list.length) {{
            document.getElementById('picker-list').innerHTML =
                '<div class="picker-empty">Nothing new to review 🎉</div>';
        }} else {{
            renderPicker(list);
        }}
        updateNewOnlyCount();
    }}

    function toggleNewOnly() {{
        newOnly = !newOnly;
        document.getElementById('new-only-btn').classList.toggle('active', newOnly);
        filterPicker();
    }}

    // Count of species (in the current kingdom) that have photos waiting.
    function updateNewOnlyCount() {{
        const n = allSpecies.filter(sp => (sp.yellow || 0) > 0).length;
        const lbl = document.getElementById('new-only-count');
        if (lbl) lbl.textContent = n;
    }}

    // ── Select & load photos for a species ────────────────────
    async function selectSpecies(id) {{
        currentSpeciesId = id;
        document.querySelectorAll('.picker-item').forEach(el => {{
            el.classList.toggle('active', el.dataset.id === id);
        }});
        if (workMode === 'triage') {{
            await loadTriage(id);
        }} else {{
            await loadPhotos(id);
        }}
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
            // Full-size URL for the focus editor (prefer large, fall back to thumb)
            const fullUrl = (photo.photo_url || photo.thumb_url || '').replace('/medium.', '/large.');
            if (photo.thumb_url) {{
                html += `<div class="photo-thumb-wrap"
                              onclick="openFocusEditor('${{speciesId}}', '${{pid}}', '${{esc(fullUrl)}}', '${{focus}}')"
                              title="Click to set focus point">
                    <img class="photo-thumb" src="${{esc(photo.thumb_url)}}"
                         alt="${{esc(photo.resolved_name)}}"
                         loading="lazy"
                         style="object-position: ${{focus}}"
                         onerror="imgFail(this)">
                    <div class="focus-hint">⊕ focus</div>
                </div>`;
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

            // Date taken
            const dateTaken = fmtDate(photo.observed_on);
            if (dateTaken) {{
                html += `<div class="photo-date" title="Date observed on iNaturalist">📅 ${{dateTaken}}</div>`;
            }}

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
                        title="Set aside — moves to Find Photos">
                    ✕ Set aside
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
            filterPicker();
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
        if (!confirm('Set this photo aside? It leaves the gallery/hero and moves to Find Photos, where you can swap it back in another day. (File on disk is not deleted.)')) return;
        try {{
            const resp = await fetch('/api/photos/trash', {{
                method: 'POST',
                headers: {{'Content-Type': 'application/json'}},
                body: JSON.stringify({{psbp_id: speciesId, photo_id: photoId}})
            }});
            const data = await resp.json();
            if (data.error) {{ toast(data.error, true); return; }}
            toast('Set aside — find it under Find Photos → revisit');
            await loadPhotos(speciesId);
            loadPickerList();
        }} catch (err) {{ toast('Error: ' + err.message, true); }}
    }}

    // ── Focus editor ──────────────────────────────────────────
    let focusState = {{ speciesId: null, photoId: null, x: 50, y: 50 }};

    function openFocusEditor(speciesId, photoId, fullUrl, currentFocus) {{
        focusState.speciesId = speciesId;
        focusState.photoId = photoId;
        // Parse "35% 60%" → x=35, y=60
        const m = String(currentFocus).match(/([\\d.]+)%\\s+([\\d.]+)%/);
        focusState.x = m ? parseFloat(m[1]) : 50;
        focusState.y = m ? parseFloat(m[2]) : 50;

        const img = document.getElementById('focus-img');
        img.src = fullUrl;
        document.getElementById('focus-modal').classList.add('open');
        updateCrosshair();
    }}

    function closeFocusEditor() {{
        document.getElementById('focus-modal').classList.remove('open');
    }}

    function updateCrosshair() {{
        const ch = document.getElementById('focus-crosshair');
        ch.style.left = focusState.x + '%';
        ch.style.top = focusState.y + '%';
        document.getElementById('focus-coords').textContent =
            `${{Math.round(focusState.x)}}% ${{Math.round(focusState.y)}}%`;
    }}

    function placeFocus(e) {{
        const img = document.getElementById('focus-img');
        const rect = img.getBoundingClientRect();
        const x = ((e.clientX - rect.left) / rect.width) * 100;
        const y = ((e.clientY - rect.top) / rect.height) * 100;
        focusState.x = Math.max(0, Math.min(100, x));
        focusState.y = Math.max(0, Math.min(100, y));
        updateCrosshair();
    }}

    async function saveFocus() {{
        const focus = `${{Math.round(focusState.x)}}% ${{Math.round(focusState.y)}}%`;
        const btn = document.getElementById('focus-save');
        btn.disabled = true;
        btn.textContent = 'Saving…';
        try {{
            const resp = await fetch('/api/photos/focus', {{
                method: 'POST',
                headers: {{'Content-Type': 'application/json'}},
                body: JSON.stringify({{
                    psbp_id: focusState.speciesId,
                    photo_id: focusState.photoId,
                    focus: focus
                }})
            }});
            const data = await resp.json();
            if (data.error) {{ toast(data.error, true); return; }}
            let msg = 'Focus saved';
            if (data.search_index_updated) msg += ' ✓ index updated';
            toast(msg);
            closeFocusEditor();
            await loadPhotos(focusState.speciesId);
        }} catch (err) {{
            toast('Error: ' + err.message, true);
        }} finally {{
            btn.disabled = false;
            btn.textContent = 'Save focus';
        }}
    }}

    // ── Triage workspace ──────────────────────────────────────
    let triageMode = 'new';  // 'new' or 'skipped'

    async function loadTriage(speciesId) {{
        const main = document.getElementById('photos-main');
        const sp = allSpecies.find(s => s.id === speciesId) || {{}};

        // Header with fetch-more button
        let header = `
            <div class="species-header">
                <span class="sh-id">${{speciesId}}</span>
                <div>
                    <h2>${{esc(sp.common_name || speciesId)}}</h2>
                    <span class="sh-sci">${{esc(sp.scientific_name || '')}}</span>
                </div>
                <div class="triage-header-actions">
                    <button class="fetch-more-btn" onclick="openFetchModal()">⟳ Fetch more</button>
                </div>
            </div>`;

        if (!sp.has_taxon) {{
            main.innerHTML = header + `<div class="photos-empty">
                This species has no <code>inat_taxon_id</code> in its signage record.<br>
                <span style="font-size:12px;color:var(--gray-400);">
                    Add the iNaturalist taxon ID to enable scanning.
                </span>
            </div>`;
            return;
        }}

        main.innerHTML = header + '<div class="loading">Loading candidates…</div>';

        try {{
            const resp = await fetch(`/api/triage/view?kingdom=${{currentKingdom}}&id=${{speciesId}}&mode=${{triageMode}}`);
            const data = await resp.json();
            renderTriageGrid(speciesId, data, header);
        }} catch (err) {{
            main.innerHTML = header + '<div class="photos-empty">Error loading candidates</div>';
        }}
    }}

    function renderTriageGrid(speciesId, data, header) {{
        const main = document.getElementById('photos-main');
        const sp = allSpecies.find(s => s.id === speciesId) || {{}};

        if (!data.scanned) {{
            main.innerHTML = header + `<div class="photos-empty">
                Not scanned yet.<br>
                <span style="font-size:12px;color:var(--gray-400);">
                    Click <strong>⟳ Fetch more</strong> to scan iNaturalist for photos.
                </span>
            </div>`;
            return;
        }}

        const photos = data.photos || [];
        const modeLabel = triageMode === 'skipped'
            ? 'showing new + set-aside' : 'showing new candidates';

        let html = header;
        html += `<div class="triage-status">
            ${{photos.length}} candidate${{photos.length !== 1 ? 's' : ''}} ·
            ${{modeLabel}}${{data.non_cc ? ` · ${{data.non_cc}} non-CC hidden` : ''}}
        </div>`;

        if (photos.length === 0) {{
            html += `<div class="photos-empty">
                ${{triageMode === 'skipped'
                    ? 'No new or set-aside photos to show. Everything here is decided.'
                    : 'No new photos. Try “Revisit set-aside” from Fetch more, or scan again later.'}}
            </div>`;
            main.innerHTML = html;
            return;
        }}

        html += '<div class="photos-grid">';
        for (const p of photos) {{
            html += triageCard(speciesId, sp, p);
        }}
        html += '</div>';
        main.innerHTML = html;
    }}

    function triageCard(speciesId, sp, p) {{
        const hasHero = (sp.green || 0) > 0 && sp.has_hero;
        const promoteLabel = hasHero ? 'Promote (gallery)' : 'Promote as hero ★';
        const date = fmtDate(p.observed_on);
        const license = (p.license || '').toUpperCase();
        const stateCls = p.state === 'skipped' ? ' triage-skipped' : ' triage-new';

        return `<div class="photo-card triage-card${{stateCls}}" id="tcard-${{p.photo_id}}">
            <div class="photo-thumb-wrap" onclick="window.open('${{esc(p.source_url)}}','_blank')"
                 title="Open observation on iNaturalist">
                <img class="photo-thumb" src="${{esc(p.thumb_url)}}" loading="lazy" onerror="imgFail(this)">
                ${{p.state === 'skipped' ? '<div class="triage-badge skipped">SET ASIDE</div>' : ''}}
            </div>
            <div class="photo-info">
                <div class="photo-credit">
                    <span class="credit-name">${{esc(p.photographer_name)}}</span>
                    ${{license ? `<span class="photo-license">${{esc(license)}}</span>` : ''}}
                </div>
                ${{date ? `<div class="photo-date">📅 ${{date}}</div>` : ''}}
            </div>
            <div class="triage-actions" id="tact-${{p.photo_id}}">
                <button class="t-btn promote" onclick='triageDecide("${{speciesId}}", ${{tjs(p)}}, "promoted", this)'>
                    ${{promoteLabel}}
                </button>
                <button class="t-btn skip" onclick='triageDecide("${{speciesId}}", ${{tjs(p)}}, "skip", this)'>Set aside</button>
                <button class="t-btn block" onclick='triageDecide("${{speciesId}}", ${{tjs(p)}}, "block", this)'>Block</button>
            </div>
        </div>`;
    }}

    // Safe JSON for inline onclick (escape single quotes)
    function tjs(o) {{ return JSON.stringify(o).replace(/'/g, "&#39;"); }}

    async function triageDecide(speciesId, p, decision, btn) {{
        // Block requires confirmation — it's the one verdict with no easy UI undo
        if (decision === 'block') {{
            if (!confirm('Block this photo? It will stay hidden from future scans (only recoverable by editing the JSON).')) return;
        }}

        const actEl = document.getElementById('tact-' + p.photo_id);
        if (actEl) actEl.innerHTML = '<div class="t-saving">saving…</div>';

        const sp = allSpecies.find(s => s.id === speciesId) || {{}};
        const payload = {{
            kingdom: currentKingdom,
            decision: decision,
            photo_id: p.photo_id,
            psbp_id: speciesId,
            obs_id: p.obs_id,
            large_url: p.large_url,
            thumb_url: p.thumb_url,
            source_url: p.source_url,
            photographer: p.photographer,
            photographer_name: p.photographer_name,
            license: p.license,
            observed_on: p.observed_on,
            shared_on: p.shared_on,
            common_name: sp.common_name,
            scientific_name: sp.scientific_name,
            type: currentKingdom === 'plants' ? 'Plant' : 'Wildlife',
        }};

        try {{
            const resp = await fetch('/api/triage/decide', {{
                method: 'POST',
                headers: {{'Content-Type': 'application/json'}},
                body: JSON.stringify(payload)
            }});
            const res = await resp.json();
            if (!res.ok) {{
                toast(res.error || 'Decision failed', true);
                if (actEl) actEl.innerHTML = '<div class="t-saving">error — reload</div>';
                return;
            }}

            // Update local counts
            const idx = allSpecies.findIndex(s => s.id === speciesId);
            if (idx >= 0) {{
                if (decision === 'promoted') {{
                    allSpecies[idx].green = (allSpecies[idx].green || 0) + 1;
                    allSpecies[idx].photo_count = (allSpecies[idx].photo_count || 0) + 1;
                    if (res.is_hero) allSpecies[idx].has_hero = true;
                }} else {{
                    allSpecies[idx].red = (allSpecies[idx].red || 0) + 1;
                }}
                allSpecies[idx].yellow = Math.max(0, (allSpecies[idx].yellow || 0) - 1);
            }}

            // Fade the card out
            const card = document.getElementById('tcard-' + p.photo_id);
            if (card) {{
                let verdict = decision;
                if (decision === 'promoted') verdict = res.is_hero ? 'promoted as hero ★' : 'added to gallery';
                card.classList.add('triage-decided');
                if (actEl) actEl.innerHTML = `<div class="t-verdict">${{verdict}}</div>`;
            }}
            toast(decision === 'promoted'
                ? (res.is_hero ? 'Promoted as hero' : 'Added to gallery')
                : (decision === 'block' ? 'Blocked' : 'Set aside'));
            filterPicker();
            document.querySelector(`.picker-item[data-id="${{speciesId}}"]`)?.classList.add('active');
        }} catch (err) {{
            toast('Error: ' + err.message, true);
        }}
    }}

    // ── Scan all species ──────────────────────────────────────
    let scanPollTimer = null;

    async function scanAll() {{
        const btn = document.getElementById('scan-all-btn');
        const kingdomLabel = currentKingdom === 'plants' ? 'plants' : 'wildlife';
        if (!confirm(`Scan all published + spotted ${{kingdomLabel}} for new iNaturalist photos?\\n\\nThis checks each species one at a time and can take a couple of minutes. A progress bar will show how far along it is — you can keep working while it runs.`)) return;

        btn.disabled = true;
        btn.textContent = '⟳ Scanning…';

        try {{
            const resp = await fetch('/api/triage/scan-all', {{
                method: 'POST',
                headers: {{'Content-Type': 'application/json'}},
                body: JSON.stringify({{kingdom: currentKingdom}})
            }});
            const data = await resp.json();
            if (!data.ok) {{
                if (data.running) {{
                    toast(`A scan is already running (${{data.done}}/${{data.total}})`, true);
                    showScanProgress();
                    startScanPoll();
                }} else {{
                    toast(data.error || 'Scan-all failed', true);
                    btn.disabled = false;
                    btn.textContent = '⟳ Scan all for new photos';
                }}
                return;
            }}
            // Job started — show the bar and begin polling.
            showScanProgress();
            updateScanProgress({{done: 0, total: data.total, current: ''}});
            startScanPoll();
        }} catch (err) {{
            toast('Error: ' + err.message, true);
            btn.disabled = false;
            btn.textContent = '⟳ Scan all for new photos';
        }}
    }}

    function showScanProgress() {{
        const label = currentKingdom === 'plants' ? 'Scanning plants…' : 'Scanning wildlife…';
        document.getElementById('scan-progress-label').textContent = label;
        document.getElementById('scan-progress').classList.add('show');
    }}
    function hideScanProgress() {{
        document.getElementById('scan-progress').classList.remove('show');
    }}

    function updateScanProgress(job) {{
        const done = job.done || 0, total = job.total || 1;
        const pct = Math.round((done / total) * 100);
        document.getElementById('scan-progress-count').textContent = `${{done}} / ${{total}}`;
        document.getElementById('scan-progress-fill').style.width = pct + '%';
        document.getElementById('scan-progress-current').textContent =
            job.current ? `Scanning: ${{job.current}}` : '';
    }}

    function startScanPoll() {{
        if (scanPollTimer) clearInterval(scanPollTimer);
        scanPollTimer = setInterval(pollScanProgress, 1000);
    }}

    async function pollScanProgress() {{
        try {{
            const resp = await fetch('/api/triage/scan-progress');
            const job = await resp.json();
            updateScanProgress(job);

            if (!job.running) {{
                // Done — stop polling, refresh counts, summarize.
                clearInterval(scanPollTimer);
                scanPollTimer = null;

                if (job.species) {{
                    allSpecies = job.species;
                    filterPicker();
                    if (currentSpeciesId) {{
                        document.querySelector(`.picker-item[data-id="${{currentSpeciesId}}"]`)?.classList.add('active');
                    }}
                }}

                // Show the persistent summary banner (replaces the old toast
                // that flashed once and disappeared).
                showScanSummary(job);
                console.log('Scan-all report:', job);

                // Leave the bar at 100% briefly, then hide.
                updateScanProgress({{done: job.total, total: job.total, current: ''}});
                setTimeout(hideScanProgress, 1500);

                const btn = document.getElementById('scan-all-btn');
                btn.disabled = false;
                btn.textContent = '⟳ Scan all for new photos';
            }}
        }} catch (err) {{
            // Network hiccup — keep polling; don't kill the job view.
            console.warn('progress poll failed', err);
        }}
    }}

    // ── Persistent scan-all summary banner ────────────────────
    // Renders the result of a scan-all and keeps it on screen until dismissed.
    // The headline number is NEW candidates relevant to our documented species —
    // not the raw CC total, which counts photos already adjudicated.
    function renderScanSummary(s) {{
        const banner   = document.getElementById('scan-summary');
        const headline = document.getElementById('scan-summary-headline');
        const sub      = document.getElementById('scan-summary-sub');
        const timeEl   = document.getElementById('scan-summary-time');
        if (!s || !s.finished_at) {{ banner.classList.remove('show'); return; }}

        const newN     = s.total_new_found || 0;
        const withNew  = s.species_with_new || 0;
        const kLabel   = s.kingdom === 'wildlife' ? 'wildlife' : 'plants';

        headline.innerHTML = newN > 0
            ? `<span class="big">${{newN}}</span> new photo${{newN !== 1 ? 's' : ''}} to review` +
              ` · across ${{withNew}} species`
            : `No new photos — you're all caught up on ${{kLabel}} 🎉`;

        const bits = [];
        bits.push(`Scanned <strong>${{s.scanned}}</strong> of ${{s.total}} ${{kLabel}}`);
        bits.push(`<span class="muted">${{s.total_cc_found || 0}} CC photos seen total</span>`);
        if (s.failed && s.failed.length)
            bits.push(`<span class="scan-summary-warn">${{s.failed.length}} failed</span>`);
        if (s.skipped_no_taxon && s.skipped_no_taxon.length)
            bits.push(`<span class="muted">${{s.skipped_no_taxon.length}} skipped (no taxon ID)</span>`);
        sub.innerHTML = bits.join(' · ');

        timeEl.textContent = s.finished_at ? `Last scan: ${{fmtScanTime(s.finished_at)}}` : '';
        banner.classList.add('show');
    }}

    function fmtScanTime(iso) {{
        try {{
            const d = new Date(iso);
            return d.toLocaleString([], {{month:'short', day:'numeric',
                hour:'numeric', minute:'2-digit'}});
        }} catch (e) {{ return iso; }}
    }}

    // After a fresh scan-all finishes, the job object IS the summary.
    function showScanSummary(job) {{ renderScanSummary(job); }}

    function dismissScanSummary() {{
        document.getElementById('scan-summary').classList.remove('show');
    }}

    // On Photos-tab load, re-show the last scan's result (survives reloads
    // and dashboard restarts because it's persisted to disk).
    async function loadLastScanSummary() {{
        try {{
            const resp = await fetch(`/api/triage/last-scan?kingdom=${{currentKingdom}}`);
            const s = await resp.json();
            if (s && s.finished_at && s.kingdom === currentKingdom) renderScanSummary(s);
        }} catch (err) {{ /* ignore */ }}
    }}

    // If a scan is already running when the Photos tab loads, resume the bar.
    async function resumeScanIfRunning() {{
        try {{
            const resp = await fetch('/api/triage/scan-progress');
            const job = await resp.json();
            if (job.running) {{
                document.getElementById('scan-all-btn').disabled = true;
                document.getElementById('scan-all-btn').textContent = '⟳ Scanning…';
                showScanProgress();
                updateScanProgress(job);
                startScanPoll();
            }}
        }} catch (err) {{ /* ignore */ }}
    }}

    // ── Fetch-more modal ──────────────────────────────────────
    function openFetchModal() {{
        const sp = allSpecies.find(s => s.id === currentSpeciesId) || {{}};
        document.getElementById('fetch-species-name').textContent =
            (sp.common_name || currentSpeciesId) + ' — ' + (sp.scientific_name || '');
        document.getElementById('fetch-modal').classList.add('open');
    }}
    function closeFetchModal() {{
        document.getElementById('fetch-modal').classList.remove('open');
    }}

    async function doFetch(which) {{
        closeFetchModal();
        if (which === 'skipped') {{
            // No network — just switch the view mode to include skipped
            triageMode = 'skipped';
            toast('Showing set-aside photos');
            await loadTriage(currentSpeciesId);
            return;
        }}
        // 'new' → scan iNat
        triageMode = 'new';
        const main = document.getElementById('photos-main');
        toast('Scanning iNaturalist…');
        const sp = allSpecies.find(s => s.id === currentSpeciesId) || {{}};
        // Keep header visible, show scanning state below it
        try {{
            const resp = await fetch('/api/triage/scan', {{
                method: 'POST',
                headers: {{'Content-Type': 'application/json'}},
                body: JSON.stringify({{kingdom: currentKingdom, id: currentSpeciesId}})
            }});
            const res = await resp.json();
            if (!res.ok) {{
                toast(res.error || 'Scan failed', true);
                return;
            }}
            const nn = res.new_count || 0;
            toast(nn > 0
                ? `${{nn}} new photo${{nn !== 1 ? 's' : ''}} to review`
                  + ` (${{res.cc_count}} CC seen)`
                : `No new photos — ${{res.cc_count}} CC already reviewed`);
            // Refresh picker counts then reload the grid
            await loadPickerList();
            await loadTriage(currentSpeciesId);
        }} catch (err) {{
            toast('Error: ' + err.message, true);
        }}
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
    function fmtDate(iso) {{
        if (!iso || iso === 'None') return '';
        // Parse YYYY-MM-DD without timezone shift
        const m = String(iso).match(/^(\\d{{4}})-(\\d{{2}})-(\\d{{2}})/);
        if (!m) return esc(iso);
        const months = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
        const mon = months[parseInt(m[2], 10) - 1] || m[2];
        return `${{mon}} ${{parseInt(m[3], 10)}}, ${{m[1]}}`;
    }}

    // ── Init ──────────────────────────────────────────────────
    loadPickerList();
    resumeScanIfRunning();
    loadLastScanSummary();
    </script>
    """


def render_publish():
    return f"""
    <!-- Kingdom toggle + status filter + search -->
    <div class="photos-controls">
        <div class="control-group">
            <span class="control-group-label">Kingdom</span>
            <div class="mode-toggle" id="pub-kingdom-toggle">
                <button class="active" onclick="pubSwitchKingdom('plants')">🌱 Plants</button>
                <button onclick="pubSwitchKingdom('wildlife')">🦎 Wildlife</button>
            </div>
        </div>
        <div class="control-group">
            <span class="control-group-label">Show</span>
            <div class="mode-toggle" id="pub-status-toggle">
                <button class="active" onclick="pubSetFilter('all')">All</button>
                <button onclick="pubSetFilter('html')">Published</button>
                <button onclick="pubSetFilter('spotted')">Spotted</button>
            </div>
        </div>
        <div class="control-group" style="flex:1; min-width:180px;">
            <span class="control-group-label">Search</span>
            <input type="text" class="pub-search" id="pub-search"
                   placeholder="Filter by name or ID…" oninput="pubRender()">
        </div>
    </div>

    <div class="pub-layout">
        <div class="pub-intro card">
            <p>Promote moves a <span class="status-pill spotted">Spotted</span> species to
            <span class="status-pill html">Published</span> — it generates the HTML page, stamps
            photo credits, and adds it to the search index. Demote reverses all three.</p>
        </div>

        <div id="pub-groups">
            <div class="loading">Loading…</div>
        </div>
    </div>

    <div class="toast" id="toast"></div>

    <script>
    let pubKingdom = 'plants';
    let pubFilter = 'all';
    let pubAllSpecies = [];

    function pubToast(msg, isError) {{
        const el = document.getElementById('toast');
        el.textContent = msg;
        el.className = 'toast show' + (isError ? ' error' : '');
        clearTimeout(el._tid);
        el._tid = setTimeout(() => el.className = 'toast', Math.max(2400, msg.length * 45));
    }}

    function pubSwitchKingdom(k) {{
        pubKingdom = k;
        const btns = document.querySelectorAll('#pub-kingdom-toggle button');
        btns.forEach(b => b.classList.remove('active'));
        btns[k === 'plants' ? 0 : 1].classList.add('active');
        loadPublishList();
    }}

    function pubSetFilter(f) {{
        pubFilter = f;
        const btns = document.querySelectorAll('#pub-status-toggle button');
        btns.forEach(b => b.classList.remove('active'));
        const idx = {{all: 0, html: 1, spotted: 2}}[f];
        btns[idx].classList.add('active');
        pubRender();
    }}

    async function loadPublishList() {{
        const wrap = document.getElementById('pub-groups');
        wrap.innerHTML = '<div class="loading">Loading…</div>';
        try {{
            const resp = await fetch(`/api/publish/list?kingdom=${{pubKingdom}}`);
            const data = await resp.json();
            if (data.error) {{
                wrap.innerHTML = `<div class="card"><p style="color:#c62828;">${{esc(data.error)}}</p></div>`;
                return;
            }}
            pubAllSpecies = data.species || [];
            pubRender();
        }} catch (err) {{
            wrap.innerHTML = '<div class="card"><p>Error loading species.</p></div>';
        }}
    }}

    function pubRender() {{
        const q = (document.getElementById('pub-search')?.value || '').toLowerCase().trim();
        let species = pubAllSpecies;
        if (q) {{
            species = species.filter(s =>
                (s.common_name || '').toLowerCase().includes(q) ||
                (s.scientific_name || '').toLowerCase().includes(q) ||
                (s.id || '').toLowerCase().includes(q));
        }}
        renderPublishGroups(species);
    }}

    function renderPublishGroups(species) {{
        const wrap = document.getElementById('pub-groups');
        const spotted = species.filter(s => s.status === 'spotted');
        const html = species.filter(s => s.status === 'html');
        const other = species.filter(s => s.status !== 'spotted' && s.status !== 'html');
        const readySpotted = spotted.filter(s => s.ready).length;

        let out = '';
        const showSpotted = pubFilter === 'all' || pubFilter === 'spotted';
        const showHtml = pubFilter === 'all' || pubFilter === 'html';

        if (showSpotted) {{
            out += `<div class="card pub-group">
                <h2>🌟 Spotted — ready to publish
                    <span class="pub-group-count">${{readySpotted}} of ${{spotted.length}} ready</span>
                </h2>`;
            out += spotted.length ? spotted.map(pubRow).join('')
                                  : '<p class="pub-empty">No spotted species.</p>';
            out += '</div>';
        }}

        if (showHtml) {{
            out += `<div class="card pub-group">
                <h2>✅ Published <span class="pub-group-count">${{html.length}}</span></h2>`;
            out += html.length ? html.map(pubRow).join('')
                               : '<p class="pub-empty">Nothing published yet.</p>';
            out += '</div>';
        }}

        if (pubFilter === 'all' && other.length) {{
            out += `<div class="card pub-group">
                <h2>🔬 Other status <span class="pub-group-count">${{other.length}}</span></h2>`;
            out += other.map(pubRow).join('');
            out += '</div>';
        }}

        if (!out) out = '<div class="card"><p class="pub-empty">No species match.</p></div>';
        wrap.innerHTML = out;
    }}

    function pubRow(sp) {{
        const checksHtml = sp.checks.map(c =>
            `<span class="pub-check ${{c.ok ? 'ok' : 'no'}}" title="${{esc(c.label)}}">
                ${{c.ok ? '✓' : '✗'}} ${{esc(c.label)}}
            </span>`
        ).join('');

        let actions = '';
        const previewBtn = sp.has_hero
            ? `<button class="pub-btn preview" onclick="pubPreview('${{sp.id}}', 0)"
                       title="Open the generated page in a new browser tab">👁 Preview</button>
               <button class="pub-btn gaps" onclick="pubPreview('${{sp.id}}', 1)"
                       title="Preview with missing/thin fields flagged">⚠ Gaps</button>`
            : '';
        if (sp.status === 'spotted') {{
            const disabled = sp.ready ? '' : 'disabled';
            const title = sp.ready ? 'Generate page and publish' : 'Complete the checklist first';
            actions = previewBtn +
                `<button class="pub-btn aidraft" onclick="pubAiDraft('${{sp.id}}')"
                          title="Have Claude research authoritative sources and draft the empty fields">🤖 Draft with Claude</button>` +
                `<button class="pub-btn airevise" onclick="pubReviseOpen('${{sp.id}}')"
                          title="Paste feedback (from Gemini, a person, your own notes) and have Claude revise">🔁 Revise with Claude</button>` +
                `<button class="pub-btn promote" ${{disabled}} title="${{title}}"
                          onclick="pubPromote('${{sp.id}}')">🚀 Publish</button>`;
        }} else if (sp.status === 'html') {{
            actions = previewBtn + `
                <button class="pub-btn regen" onclick="pubPromote('${{sp.id}}')"
                        title="Regenerate the page from current data">♻️ Regenerate</button>
                <button class="pub-btn demote" onclick="pubDemote('${{sp.id}}')"
                        title="Pull back to spotted">⬇ Demote</button>`;
        }} else {{
            actions = previewBtn || `<span class="pub-na">—</span>`;
        }}

        const tagsHtml = (sp.tags && sp.tags.length)
            ? `<div class="pub-tags" title="Internal tags — used in the dashboard, never shown on the public page">
                 <span class="pub-tags-label">tags</span>
                 ${{sp.tags.map(t => `<span class="pub-tag">${{esc(t)}}</span>`).join('')}}
               </div>`
            : '';

        return `<div class="pub-row" id="pubrow-${{sp.id}}">
            <div class="pub-row-main">
                <span class="pub-id">${{sp.id}}</span>
                <div class="pub-names">
                    <span class="pub-common">${{esc(sp.common_name || sp.id)}}</span>
                    <span class="pub-sci">${{esc(sp.scientific_name || '')}}</span>
                </div>
                <span class="status-pill ${{sp.status}}">${{sp.status === 'html' ? 'Published' : sp.status.charAt(0).toUpperCase()+sp.status.slice(1)}}</span>
            </div>
            <div class="pub-checks">${{checksHtml}}</div>
            ${{tagsHtml}}
            <div class="pub-actions">${{actions}}</div>
            <div class="pub-secondary">
                <button class="pub-link-btn" onclick="pubDemoteResearch('${{sp.id}}', 'research')"
                        title="Move back to research.json — not ready yet">↩ Return to Research</button>
                <button class="pub-link-btn dead-link" onclick="pubDemoteResearch('${{sp.id}}', 'died')"
                        title="Suspected dead — move to research.json as died">☠ Mark Dead</button>
            </div>
            <div class="ai-result" id="ai-result-${{sp.id}}"></div>
        </div>`;
    }}

    function pubReviseOpen(id) {{
        const panel = document.getElementById('ai-result-' + id);
        panel.style.display = 'block';
        panel.className = 'ai-result';
        panel.innerHTML = `
            <div class="rev-box">
                <div class="rev-label">Paste feedback for Claude — from Gemini, a person, or your own notes.
                    Tone, facts, formatting, anything. Claude changes only what you mention.</div>
                <textarea id="rev-text-${{id}}" class="rev-text" rows="4"
                    placeholder="e.g. Dave says it does clump here — fix the suckering claim. Also tighten More Information, it's a touch long."></textarea>
                <div class="rev-row">
                    <label class="rev-check"><input type="checkbox" id="rev-search-${{id}}" checked>
                        let Claude web-search to verify facts (uncheck for tone/formatting-only — faster, cheaper)</label>
                </div>
                <div class="rev-row">
                    <button class="pub-btn airevise" onclick="pubReviseSubmit('${{id}}')">🔁 Send to Claude</button>
                    <button class="pub-link-btn" onclick="document.getElementById('ai-result-${{id}}').style.display='none'">cancel</button>
                </div>
            </div>`;
        const ta = document.getElementById('rev-text-' + id);
        if (ta) ta.focus();
    }}

    async function pubReviseSubmit(id) {{
        const text = (document.getElementById('rev-text-' + id) || {{}}).value || '';
        const allowSearch = (document.getElementById('rev-search-' + id) || {{}}).checked;
        if (!text.trim()) {{ pubToast('Add some feedback first', true); return; }}
        const panel = document.getElementById('ai-result-' + id);
        panel.className = 'ai-result working';
        panel.innerHTML = '<span class="ai-spin">🔁</span> Claude is applying your feedback' +
            (allowSearch ? ' and verifying facts' : '') + '…';
        try {{
            const resp = await fetch('/api/ai/revise', {{
                method: 'POST', headers: {{'Content-Type': 'application/json'}},
                body: JSON.stringify({{kingdom: pubKingdom, id: id, feedback: text, allow_search: allowSearch}})
            }});
            const res = await resp.json();
            if (!res.ok) {{
                panel.className = 'ai-result error';
                panel.innerHTML = '⚠️ ' + esc(res.error || 'Revision failed') +
                    (res.raw_tail ? `<pre class="ai-raw">${{esc(res.raw_tail)}}</pre>` : '');
                return;
            }}
            let html = '';
            if (res.summary) html += `<div class="ai-summary">${{esc(res.summary)}}</div>`;
            if (res.changed && res.changed.length) {{
                html += `<div class="ai-line"><b>✓ Revised (${{res.changed.length}}):</b></div>`;
                html += res.changed.map(f =>
                    `<div class="ai-line ai-muted"><span class="ai-chip ok">${{esc(f)}}</span> ${{esc((res.reasons&&res.reasons[f])||'')}}</div>`).join('');
            }} else {{
                html += `<div class="ai-line ai-none">No fields changed — Claude didn't find anything in the feedback to act on, or couldn't verify a factual change.</div>`;
            }}
            if (res.sources && res.sources.length) {{
                const items = res.sources.slice(0, 12).map(s =>
                    `<li><a href="${{esc(s.url)}}" target="_blank" rel="noopener">${{esc(s.title || s.url)}}</a></li>`).join('');
                html += `<details class="ai-sources"><summary>📚 ${{res.sources.length}} source(s) · ${{res.searches || 0}} search(es)</summary><ul>${{items}}</ul></details>`;
            }}
            const inT = res.usage && res.usage.input_tokens, outT = res.usage && res.usage.output_tokens;
            if (inT || outT) html += `<div class="ai-line ai-muted">${{res.model}} · ${{inT||0}} in / ${{outT||0}} out tokens</div>`;
            html += `<div class="ai-line">
                <button class="pub-btn preview" onclick="pubPreview('${{id}}', 0)">👁 Open Preview to review</button>
                <button class="pub-btn airevise" onclick="pubReviseOpen('${{id}}')">🔁 Another round</button></div>`;
            panel.className = 'ai-result done';
            panel.innerHTML = html;
        }} catch (err) {{
            panel.className = 'ai-result error';
            panel.innerHTML = '⚠️ ' + esc(err.message);
        }}
    }}

    async function pubAiDraft(id) {{
        const panel = document.getElementById('ai-result-' + id);
        panel.style.display = 'block';
        panel.className = 'ai-result working';
        panel.innerHTML = '<span class="ai-spin">🤖</span> Claude is researching authoritative sources and drafting the empty fields… this can take up to a minute.';
        try {{
            const resp = await fetch('/api/ai/draft', {{
                method: 'POST', headers: {{'Content-Type': 'application/json'}},
                body: JSON.stringify({{kingdom: pubKingdom, id: id, overwrite: false}})
            }});
            const res = await resp.json();
            if (!res.ok) {{
                panel.className = 'ai-result error';
                panel.innerHTML = '⚠️ ' + esc(res.error || 'Draft failed') +
                    (res.raw_tail ? `<pre class="ai-raw">${{esc(res.raw_tail)}}</pre>` : '');
                return;
            }}
            const chips = (arr, cls) => (arr && arr.length)
                ? arr.map(f => `<span class="ai-chip ${{cls}}">${{esc(f)}}</span>`).join('') : '';
            let html = '';
            if (res.summary) html += `<div class="ai-summary">${{esc(res.summary)}}</div>`;
            if (res.filled && res.filled.length)
                html += `<div class="ai-line"><b>✓ Drafted (${{res.filled.length}}):</b> ${{chips(res.filled,'ok')}}</div>`;
            else
                html += `<div class="ai-line ai-none">No empty fields were filled — everything was already populated, or Claude couldn't source anything new.</div>`;
            if (res.skipped_existing && res.skipped_existing.length)
                html += `<div class="ai-line"><b>↩ Left as-is (already had content):</b> ${{chips(res.skipped_existing,'skip')}}</div>`;
            if (res.low_confidence && res.low_confidence.length)
                html += `<div class="ai-line"><b>⚠ Double-check these:</b> ${{chips(res.low_confidence,'low')}}</div>`;
            if (res.rejected_keys && res.rejected_keys.length)
                html += `<div class="ai-line ai-muted">Ignored unknown fields: ${{chips(res.rejected_keys,'skip')}}</div>`;
            if (res.sources && res.sources.length) {{
                const items = res.sources.slice(0, 12).map(s =>
                    `<li><a href="${{esc(s.url)}}" target="_blank" rel="noopener">${{esc(s.title || s.url)}}</a></li>`).join('');
                html += `<details class="ai-sources"><summary>📚 ${{res.sources.length}} source(s) · ${{res.searches || 0}} search(es)</summary><ul>${{items}}</ul></details>`;
            }}
            const inT = res.usage && res.usage.input_tokens, outT = res.usage && res.usage.output_tokens;
            if (inT || outT)
                html += `<div class="ai-line ai-muted">${{res.model}} · ${{inT||0}} in / ${{outT||0}} out tokens</div>`;
            html += `<div class="ai-line"><button class="pub-btn preview" onclick="pubPreview('${{id}}', 0)">👁 Open Preview to review</button></div>`;
            panel.className = 'ai-result done';
            panel.innerHTML = html;
        }} catch (err) {{
            panel.className = 'ai-result error';
            panel.innerHTML = '⚠️ ' + esc(err.message);
        }}
    }}

    function pubPreview(id, gaps) {{
        const g = gaps ? '&gaps=1' : '';
        window.open(`/preview?kingdom=${{pubKingdom}}&id=${{id}}${{g}}`, '_blank');
    }}

    async function pubPromote(id) {{
        const row = document.getElementById('pubrow-' + id);
        const actions = row.querySelector('.pub-actions');
        const prev = actions.innerHTML;
        actions.innerHTML = '<span class="pub-working">Publishing…</span>';
        try {{
            const resp = await fetch('/api/publish/promote', {{
                method: 'POST', headers: {{'Content-Type': 'application/json'}},
                body: JSON.stringify({{kingdom: pubKingdom, id: id}})
            }});
            const res = await resp.json();
            if (!res.ok) {{
                pubToast(res.error || 'Publish failed', true);
                if (res.trace) console.error(res.trace);
                actions.innerHTML = prev;
                return;
            }}
            pubToast(res.regenerated ? `Regenerated ${{res.filename}}` : `Published ${{res.filename}}`);
            loadPublishList();
        }} catch (err) {{
            pubToast('Error: ' + err.message, true);
            actions.innerHTML = prev;
        }}
    }}

    async function pubDemote(id) {{
        if (!confirm('Demote to spotted? This deletes the published HTML page and removes it from the search index. (Photos and signage data are kept.)')) return;
        const row = document.getElementById('pubrow-' + id);
        const actions = row.querySelector('.pub-actions');
        const prev = actions.innerHTML;
        actions.innerHTML = '<span class="pub-working">Demoting…</span>';
        try {{
            const resp = await fetch('/api/publish/demote', {{
                method: 'POST', headers: {{'Content-Type': 'application/json'}},
                body: JSON.stringify({{kingdom: pubKingdom, id: id}})
            }});
            const res = await resp.json();
            if (!res.ok) {{
                pubToast(res.error || 'Demote failed', true);
                if (res.trace) console.error(res.trace);
                actions.innerHTML = prev;
                return;
            }}
            const n = (res.deleted_files || []).length;
            pubToast(`Demoted to spotted${{n ? ` · ${{n}} file${{n!==1?'s':''}} deleted` : ''}}`);
            loadPublishList();
        }} catch (err) {{
            pubToast('Error: ' + err.message, true);
            actions.innerHTML = prev;
        }}
    }}

    async function pubDemoteResearch(id, reason) {{
        const isDead = reason === 'died';
        const label = isDead ? 'Mark dead and move to research?' : 'Return to research pool?';
        const detail = isDead
            ? 'The species will be marked as dead in research.json. Hero photo files will be deleted.'
            : 'The species will move back to research.json for later. Hero photo files will be deleted.';
        if (!confirm(label + '\\n\\n' + detail)) return;

        const row = document.getElementById('pubrow-' + id);
        const actions = row.querySelector('.pub-actions');
        const secondary = row.querySelector('.pub-secondary');
        const prevA = actions.innerHTML;
        const prevS = secondary ? secondary.innerHTML : '';
        actions.innerHTML = '<span class="pub-working">'
            + (isDead ? 'Marking dead…' : 'Moving…') + '</span>';
        if (secondary) secondary.innerHTML = '';

        try {{
            const resp = await fetch('/api/publish/demote-research', {{
                method: 'POST', headers: {{'Content-Type': 'application/json'}},
                body: JSON.stringify({{kingdom: pubKingdom, id: id, reason: reason}})
            }});
            const res = await resp.json();
            if (!res.ok) {{
                pubToast(res.error || 'Failed', true);
                if (res.trace) console.error(res.trace);
                actions.innerHTML = prevA;
                if (secondary) secondary.innerHTML = prevS;
                return;
            }}
            const n = (res.deleted_files || []).length;
            const h = (res.hero_deleted || []).length;
            let msg = res.label + ': ' + (res.common_name || id);
            if (n || h) msg += ` · ${{n + h}} file${{(n+h)!==1?'s':''}} cleaned`;
            pubToast(msg);
            loadPublishList();
        }} catch (err) {{
            pubToast('Error: ' + err.message, true);
            actions.innerHTML = prevA;
            if (secondary) secondary.innerHTML = prevS;
        }}
    }}

    function esc(s) {{
        if (!s) return '';
        const d = document.createElement('div'); d.textContent = s; return d.innerHTML;
    }}

    loadPublishList();
    </script>
    """


# ╔══════════════════════════════════════════════════════════════════════════╗
# ║  HTTP SERVER                                                           ║
# ║                                                                        ║
# ║  Routing: PAGE_ROUTES for HTML pages, API_ROUTES for JSON endpoints.   ║
# ║  Add new routes to the appropriate dict.                               ║
# ╚══════════════════════════════════════════════════════════════════════════╝

# Page routes: path → (tab_id, render_function)
# ╔══════════════════════════════════════════════════════════════════════════╗
# ║  CULTIVATED — mark garden plantings "not wild" on iNaturalist.            ║
# ║                                                                          ║
# ║  Ported from wild_audit.py (read) + mark_not_wild.py (write). Plants      ║
# ║  only; animals are correctly wild and never touched. The write votes the  ║
# ║  "Organism is wild" DQA metric to FALSE — exactly mark_not_wild.py.       ║
# ╚══════════════════════════════════════════════════════════════════════════╝

def _inat_species_counts_captive(captive_value):
    """species_counts for the project filtered by captive flag.
    Returns {taxon_id: {"count": n, "taxon": {...}}}."""
    out = {}
    page = 1
    while True:
        url = ("https://api.inaturalist.org/v1/observations/species_counts"
               f"?project_id={INAT_PROJECT_ID}&verifiable=any"
               f"&captive={captive_value}&per_page=500&page={page}")
        data = _inat_get(url)
        if not data:
            break
        results = data.get("results", [])
        for row in results:
            tx = row.get("taxon") or {}
            tid = tx.get("id")
            if tid is not None:
                out[tid] = {"count": row.get("count", 0), "taxon": tx}
        total = data.get("total_results", 0)
        if len(results) < 500 or (page * 500) >= total:
            break
        page += 1
        time.sleep(API_DELAY)
    return out


WILD_KEEP_JSON = os.path.join(REPO, "data", "sources", "cultivated_keep_wild.json")


def _load_keep_wild():
    """taxon_id -> record for plants the user has declared genuinely wild."""
    data = _load(WILD_KEEP_JSON)
    taxa = data.get("taxa", []) if isinstance(data, dict) else (
        data if isinstance(data, list) else [])
    out = {}
    for t in taxa:
        tid = t.get("taxon_id")
        if tid is not None:
            try:
                out[int(tid)] = t
            except (TypeError, ValueError):
                pass
    return out


def _save_keep_wild(keep_map):
    payload = {
        "meta": {"updated": datetime.date.today().isoformat(),
                 "count": len(keep_map)},
        "taxa": sorted(keep_map.values(),
                       key=lambda t: t.get("scientific_name", "")),
    }
    write_json_atomic(WILD_KEEP_JSON, payload)


def cultivated_keep_wild_add(taxon_id, sci, common):
    keep = _load_keep_wild()
    keep[int(taxon_id)] = {
        "taxon_id":        int(taxon_id),
        "scientific_name": sci or "",
        "common_name":     common or "",
        "added":           datetime.date.today().isoformat(),
    }
    _save_keep_wild(keep)
    return {"ok": True, "kept": len(keep)}


def cultivated_keep_wild_remove(taxon_id):
    keep = _load_keep_wild()
    keep.pop(int(taxon_id), None)
    _save_keep_wild(keep)
    return {"ok": True, "kept": len(keep)}


RECENT_MARK_JSON = os.path.join(REPO, "data", "sources", "cultivated_recent.json")
# How long to trust our own "just marked" memory over iNat's lagging index.
RECENT_MARK_WINDOW_S = 45 * 60


def _load_recent_marked():
    """taxon_id -> {at_epoch, count, scientific_name, ...} for just-marked taxa."""
    data = _load(RECENT_MARK_JSON)
    items = data.get("marked", []) if isinstance(data, dict) else (
        data if isinstance(data, list) else [])
    out = {}
    for r in items:
        tid = r.get("taxon_id")
        if tid is not None:
            try:
                out[int(tid)] = r
            except (TypeError, ValueError):
                pass
    return out


def _save_recent_marked(recent_map):
    write_json_atomic(RECENT_MARK_JSON, {
        "meta": {"updated": datetime.datetime.now().isoformat(timespec="seconds")},
        "marked": list(recent_map.values()),
    })


def _record_recent_mark(taxon_id, name, count):
    recent = _load_recent_marked()
    recent[int(taxon_id)] = {
        "taxon_id":        int(taxon_id),
        "scientific_name": name or "",
        "count":           count,
        "at_epoch":        time.time(),
        "at":              datetime.datetime.now().isoformat(timespec="seconds"),
    }
    _save_recent_marked(recent)


def cultivated_audit(limit=10):
    """Read-only worklist: plant taxa with the most still-WILD observations.

    Animals are excluded (birds/insects in the park ARE wild). Returns the top
    `limit` plant offenders by wild-observation count.
    """
    wild = _inat_species_counts_captive("false")
    cult = _inat_species_counts_captive("true")
    if not wild and not cult:
        return {"ok": False,
                "error": "No data from iNaturalist — offline, or the project "
                         "slug is wrong. Check INAT_PROJECT_ID."}

    keep = _load_keep_wild()
    recent = _load_recent_marked()
    now = time.time()
    # Drop stale "just marked" memories beyond the trust window.
    recent = {tid: r for tid, r in recent.items()
              if (now - r.get("at_epoch", 0)) < RECENT_MARK_WINDOW_S}

    rows = []
    still_recent = {}
    for tid in (set(wild) | set(cult)):
        tx = (wild.get(tid) or cult.get(tid))["taxon"]
        if (tx.get("iconic_taxon_name") or "") != "Plantae":
            continue
        if tid in keep:
            continue
        w = wild.get(tid, {}).get("count", 0)
        c = cult.get(tid, {}).get("count", 0)
        if tid in recent:
            # We marked this recently. If iNat's index still shows it wild,
            # that's reindex lag — keep hiding it. Once the index agrees it's
            # clean (no wild left), forget it so it can resurface if it ever
            # genuinely regresses.
            if w > 0:
                still_recent[tid] = recent[tid]
                continue
            # caught up — drop the memory, and it won't list (w == 0 anyway)
            continue
        if w <= 0:
            continue
        rows.append({
            "taxon_id":       tid,
            "scientific_name": tx.get("name", ""),
            "common_name":    tx.get("preferred_common_name", "") or "",
            "wild_obs":       w,
            "cultivated_obs": c,
            "total_obs":      w + c,
        })

    _save_recent_marked(still_recent)
    rows.sort(key=lambda r: -r["wild_obs"])
    return {"ok": True,
            "offenders":        rows[:limit],
            "offender_total":   len(rows),
            "plant_wild_total": sum(r["wild_obs"] for r in rows),
            "keep_wild":        sorted(keep.values(),
                                       key=lambda t: t.get("scientific_name", "")),
            "recently_marked":  sorted(still_recent.values(),
                                       key=lambda t: t.get("scientific_name", ""))}


def cultivated_preview(taxon_id):
    """Read-only: which observations of one taxon would be marked cultivated."""
    obs = _inat_observations(taxon_id)
    to_change = [o for o in obs if o.get("captive") is not True]
    already   = [o for o in obs if o.get("captive") is True]
    name = ""
    if obs:
        name = (obs[0].get("taxon") or {}).get("name", "")
    wild_list = [{
        "obs_id":        o.get("id"),
        "observer":      (o.get("user") or {}).get("login", "?"),
        "quality_grade": o.get("quality_grade", "?"),
    } for o in to_change]
    return {"ok": True, "taxon_id": taxon_id, "scientific_name": name,
            "wild_count": len(to_change), "cultivated_count": len(already),
            "total": len(obs), "wild": wild_list}


def cultivated_mark(taxon_id, token):
    """WRITE: vote 'not wild' on every still-wild observation of this taxon.

    Re-derives the wild set server-side (never trusts a client list), validates
    the token before any write, rate-limits at one vote/second (matching
    mark_not_wild.py), and returns a per-observation report.
    """
    if not token:
        return {"ok": False, "error": "No iNaturalist token provided"}

    me = _inat_get_auth("https://api.inaturalist.org/v1/users/me", token)
    if not me or not me.get("results"):
        return {"ok": False, "error": "Token invalid or expired — paste a fresh "
                "one from inaturalist.org/users/api_token"}

    obs = _inat_observations(taxon_id)
    to_change = [o for o in obs if o.get("captive") is not True]
    if not to_change:
        return {"ok": True, "marked": 0, "failed": 0, "results": [],
                "note": "Every observation is already cultivated."}

    results, marked, failed = [], 0, 0
    for o in to_change:
        oid = o.get("id")
        observer = (o.get("user") or {}).get("login", "?")
        ok, status, body = _inat_post(
            f"https://api.inaturalist.org/v1/observations/{oid}/quality/wild",
            token, params={"agree": "false"})
        if ok:
            marked += 1
        else:
            failed += 1
        results.append({"obs_id": oid, "observer": observer, "ok": ok,
                        "status": status, "error": body if not ok else ""})
        time.sleep(API_DELAY)
    if marked > 0:
        name = (obs[0].get("taxon") or {}).get("name", "") if obs else ""
        _record_recent_mark(taxon_id, name, marked)
    return {"ok": True, "taxon_id": taxon_id, "marked": marked,
            "failed": failed, "results": results}


def handle_api_cultivated_audit(params):
    """GET /api/cultivated/audit — read-only plant worklist."""
    return cultivated_audit()


def handle_api_cultivated_preview(params):
    """GET /api/cultivated/preview?taxon_id= — wild obs for one taxon."""
    raw = params.get("taxon_id", [""])
    tid = raw[0] if isinstance(raw, list) else raw
    try:
        tid = int(tid)
    except (TypeError, ValueError):
        return {"ok": False, "error": "Bad taxon_id"}
    return cultivated_preview(tid)


def handle_api_cultivated_mark(params):
    """POST /api/cultivated/mark — body {taxon_id, token}."""
    body = params.get("_body", {})
    token = (body.get("token") or "").strip()
    try:
        tid = int(body.get("taxon_id"))
    except (TypeError, ValueError):
        return {"ok": False, "error": "Bad taxon_id"}
    return cultivated_mark(tid, token)


def handle_api_cultivated_keep(params):
    """POST /api/cultivated/keep — body {taxon_id, scientific_name, common_name}
    adds to the 'leave wild' list; {taxon_id, remove:true} removes it."""
    body = params.get("_body", {})
    try:
        tid = int(body.get("taxon_id"))
    except (TypeError, ValueError):
        return {"ok": False, "error": "Bad taxon_id"}
    if body.get("remove"):
        return cultivated_keep_wild_remove(tid)
    return cultivated_keep_wild_add(tid, body.get("scientific_name", ""),
                                    body.get("common_name", ""))


def render_cultivated():
    """Cultivated tab — audit plant observations and vote them 'not wild'."""
    return f"""
    <style>
    .cult-intro{{background:#fff;border:1px solid #e5e0d5;border-left:4px solid var(--green-mid);border-radius:10px;padding:14px 16px;margin-bottom:16px;}}
    .cult-intro h2{{font-size:16px;color:var(--green-deep);margin:0 0 4px;}}
    .cult-intro p{{font-size:13px;color:var(--gray-600);margin:0;}}
    .cult-scan-btn{{background:var(--green-mid);color:#fff;border:none;border-radius:7px;padding:9px 16px;font-size:13px;font-weight:600;cursor:pointer;margin-top:10px;}}
    .cult-scan-btn:disabled{{opacity:.6;cursor:default;}}
    .cult-status{{font-size:13px;color:var(--gray-600);margin-top:8px;}}
    .cult-layout{{display:grid;grid-template-columns:minmax(260px,360px) 1fr;gap:16px;align-items:start;}}
    .cult-summary{{font-size:13px;color:var(--green-deep);font-weight:600;margin-bottom:8px;}}
    .cult-card{{display:flex;align-items:center;justify-content:space-between;gap:8px;padding:9px 11px;border:1px solid #eee;border-radius:8px;background:#fff;cursor:pointer;margin-bottom:6px;}}
    .cult-card:hover{{background:var(--cream);}}
    .cult-card.active{{border-left:3px solid var(--green-mid);background:#eef6ef;}}
    .cult-name{{display:flex;flex-direction:column;min-width:0;}}
    .cult-common{{font-weight:600;color:var(--green-deep);font-size:14px;}}
    .cult-sci{{font-style:italic;color:var(--gray-600);font-size:12px;}}
    .cult-counts{{display:flex;gap:5px;flex:0 0 auto;}}
    .cult-pill{{font-size:11px;font-weight:700;padding:2px 8px;border-radius:9px;white-space:nowrap;}}
    .cult-pill.wild{{background:#fdf0d5;color:#9a6b12;}}
    .cult-pill.cult{{background:#e3f0e5;color:#2d6a35;}}
    .cult-detail{{background:#fff;border:1px solid #eee;border-radius:10px;padding:16px;min-height:120px;}}
    .cult-detail-head h3{{margin:0;font-style:italic;color:var(--green-deep);}}
    .cult-detail-counts{{font-size:13px;color:var(--gray-600);margin-top:6px;}}
    .cult-obs-list{{margin:12px 0;max-height:200px;overflow:auto;border:1px solid #f0eee8;border-radius:6px;}}
    .cult-obs-row{{display:flex;justify-content:space-between;gap:8px;font-size:12px;padding:4px 10px;border-bottom:1px solid #f5f3ee;}}
    .cult-obs-more{{font-size:12px;color:#999;padding:6px 10px;}}
    .cult-token-box{{background:var(--cream);border:1px solid #e5e0d5;border-radius:8px;padding:12px;margin:12px 0;}}
    .cult-token-box label{{display:block;font-size:12px;font-weight:600;color:var(--green-deep);margin-bottom:6px;}}
    .cult-token-box input{{width:100%;box-sizing:border-box;padding:8px 10px;border:1px solid #ccc;border-radius:6px;font-size:13px;font-family:monospace;}}
    .cult-token-note{{font-size:11px;color:#999;margin-top:5px;}}
    .cult-mark-btn{{background:var(--gold);color:#3a2c08;border:none;border-radius:7px;padding:10px 16px;font-size:14px;font-weight:700;cursor:pointer;width:100%;}}
    .cult-mark-btn:disabled{{opacity:.55;cursor:default;}}
    .cult-empty{{font-size:13px;color:var(--gray-600);padding:8px 0;}}
    .cult-result-ok{{font-size:14px;font-weight:600;color:var(--green-mid);margin-top:12px;}}
    .cult-leave{{background:none;border:1px solid #ddd;border-radius:6px;color:#999;font-size:10px;padding:2px 7px;cursor:pointer;white-space:nowrap;}}
    .cult-leave:hover{{border-color:#9a6b12;color:#9a6b12;}}
    .cult-kept-toggle{{margin-top:12px;font-size:12px;color:var(--green-mid);background:none;border:none;padding:0;cursor:pointer;text-decoration:underline;}}
    .cult-recent{{margin-top:10px;font-size:12px;color:#6b7a55;background:#f3f6ed;border:1px solid #e2e8d4;border-radius:7px;padding:8px 10px;}}
    .cult-kept-list{{margin-top:8px;border-top:1px dashed #ddd;padding-top:8px;}}
    .cult-kept-row{{display:flex;justify-content:space-between;align-items:center;gap:8px;font-size:12px;padding:3px 0;color:var(--gray-600);}}
    .cult-restore{{background:none;border:none;color:var(--green-mid);font-size:11px;cursor:pointer;text-decoration:underline;padding:0;white-space:nowrap;}}
    .cult-prompt{{color:var(--gray-400);font-size:13px;text-align:center;padding:24px;}}
    </style>

    <div class="cult-intro">
        <h2>🏷️ Mark plants cultivated</h2>
        <p>iNaturalist treats observations as wild by default. Garden plantings should be flagged
        cultivated so the park's row of Foxtails doesn't read as a wild population. This scans
        <strong>plants only</strong> (animals in the park really are wild) and votes the worst
        offenders "not wild" for you — across the whole project, so your team doesn't have to.</p>
        <button class="cult-scan-btn" id="cult-scan-btn" onclick="cultScan()">🔍 Scan for offenders</button>
        <div class="cult-status" id="cult-status"></div>
    </div>

    <div class="cult-layout">
        <div id="cult-list"></div>
        <div class="cult-detail" id="cult-detail">
            <div class="cult-prompt">Scan, then pick a species to review and mark.</div>
        </div>
    </div>

    <div class="toast" id="cult-toast"></div>

    <script>
    let cultAuditData = null;
    let cultOffenders = [];
    let cultSelected = null;
    let cultDetailData = null;
    let cultToken = '';

    function esc(s) {{
        if (!s) return '';
        const d = document.createElement('div'); d.textContent = s; return d.innerHTML;
    }}
    function cultToast(msg, isErr) {{
        const el = document.getElementById('cult-toast');
        if (!el) return;
        el.textContent = msg; el.className = 'toast show' + (isErr ? ' error' : '');
        clearTimeout(el._t); el._t = setTimeout(() => el.className = 'toast', 3200);
    }}

    async function cultScan() {{
        const btn = document.getElementById('cult-scan-btn');
        const status = document.getElementById('cult-status');
        btn.disabled = true; btn.textContent = '⏳ Scanning…';
        status.textContent = 'Querying iNaturalist for wild vs cultivated plant counts…';
        try {{
            const resp = await fetch('/api/cultivated/audit');
            const data = await resp.json();
            if (!data.ok) {{ status.textContent = data.error || 'Scan failed.'; }}
            else {{ cultAuditData = data; cultRenderList(); status.textContent = ''; }}
        }} catch (e) {{
            status.innerHTML = '<span style="color:#c62828;">Network error — is the dashboard online?</span>';
        }}
        btn.disabled = false; btn.textContent = '🔄 Re-scan';
    }}

    function cultRenderList() {{
        const data = cultAuditData || {{}};
        cultOffenders = data.offenders || [];
        const kept = data.keep_wild || [];
        const list = document.getElementById('cult-list');
        let html = '';
        if (!cultOffenders.length) {{
            html += '<div class="cult-empty">No wild-marked plants left to review. 🎉</div>';
        }} else {{
            html += `<div class="cult-summary">${{data.offender_total}} plant taxa still carry wild observations `
                + `(${{data.plant_wild_total}} total). Worst ${{cultOffenders.length}}:</div>`;
            html += cultOffenders.map(o => `
                <div class="cult-card ${{cultSelected === o.taxon_id ? 'active' : ''}}" onclick="cultSelect(${{o.taxon_id}})">
                    <div class="cult-name">
                        <span class="cult-common">${{esc(o.common_name || o.scientific_name)}}</span>
                        <span class="cult-sci">${{esc(o.scientific_name)}}</span>
                    </div>
                    <div class="cult-counts">
                        <span class="cult-pill wild">${{o.wild_obs}} wild</span>
                        <span class="cult-pill cult">${{o.cultivated_obs}} cult</span>
                        <button class="cult-leave" title="Grows wild here — keep it wild and hide from this list" onclick="event.stopPropagation(); cultKeepWild(${{o.taxon_id}})">leave wild</button>
                    </div>
                </div>`).join('');
        }}
        const recent = data.recently_marked || [];
        if (recent.length) {{
            const names = recent.map(r => esc(r.scientific_name)).join(', ');
            html += `<div class="cult-recent">🕓 ${{recent.length}} just marked (${{names}}) — hidden while iNaturalist re-indexes; this can take a few minutes.</div>`;
        }}
        if (kept.length) {{
            html += `<button class="cult-kept-toggle" onclick="cultToggleKept()">🌿 ${{kept.length}} left wild — manage</button>`;
            html += '<div class="cult-kept-list" id="cult-kept-list" style="display:none;">';
            html += kept.slice().sort((a, b) => (a.scientific_name || '').localeCompare(b.scientific_name || '')).map(k => `
                <div class="cult-kept-row">
                    <span>${{esc(k.common_name || k.scientific_name)}} <span style="color:#aaa;font-style:italic;">${{esc(k.scientific_name)}}</span></span>
                    <button class="cult-restore" onclick="cultRestoreWild(${{k.taxon_id}})">↩ put back</button>
                </div>`).join('');
            html += '</div>';
        }}
        list.innerHTML = html;
    }}

    async function cultKeepWild(tid) {{
        const o = cultOffenders.find(x => x.taxon_id === tid);
        try {{
            await fetch('/api/cultivated/keep', {{
                method: 'POST', headers: {{'Content-Type': 'application/json'}},
                body: JSON.stringify({{taxon_id: tid,
                    scientific_name: o ? o.scientific_name : '',
                    common_name: o ? o.common_name : ''}})
            }});
            cultToast((o ? (o.common_name || o.scientific_name) : 'Taxon') + ' left wild');
            if (cultSelected === tid) {{
                cultSelected = null;
                document.getElementById('cult-detail').innerHTML =
                    '<div class="cult-prompt">Pick a species to review and mark.</div>';
            }}
            cultScan();
        }} catch (e) {{ cultToast('Error: ' + e.message, true); }}
    }}

    async function cultRestoreWild(tid) {{
        try {{
            await fetch('/api/cultivated/keep', {{
                method: 'POST', headers: {{'Content-Type': 'application/json'}},
                body: JSON.stringify({{taxon_id: tid, remove: true}})
            }});
            cultToast('Put back on the worklist');
            cultScan();
        }} catch (e) {{ cultToast('Error: ' + e.message, true); }}
    }}

    function cultToggleKept() {{
        const el = document.getElementById('cult-kept-list');
        if (el) el.style.display = el.style.display === 'none' ? 'block' : 'none';
    }}

    async function cultSelect(tid) {{
        cultSelected = tid; cultRenderList();
        const detail = document.getElementById('cult-detail');
        detail.innerHTML = '<div class="loading">Loading observations…</div>';
        try {{
            const resp = await fetch('/api/cultivated/preview?taxon_id=' + tid);
            const data = await resp.json();
            if (!data.ok) {{ detail.innerHTML = `<div class="cult-empty" style="color:#c62828;">${{esc(data.error || 'error')}}</div>`; return; }}
            cultRenderDetail(data);
        }} catch (e) {{
            detail.innerHTML = '<div class="cult-empty">Error loading observations.</div>';
        }}
    }}

    function cultRenderDetail(d) {{
        cultDetailData = d;
        const detail = document.getElementById('cult-detail');
        if (d.wild_count === 0) {{
            detail.innerHTML = `<div class="cult-detail-head"><h3>${{esc(d.scientific_name)}}</h3></div>`
                + `<div class="cult-empty">All ${{d.total}} observations are already cultivated. Nothing to do. ✓</div>`;
            return;
        }}
        const sample = d.wild.slice(0, 15).map(o =>
            `<div class="cult-obs-row"><span>obs ${{o.obs_id}}</span>`
            + `<span style="color:#999;">${{esc(o.observer)}}</span>`
            + `<span style="color:#aaa;">${{esc(o.quality_grade)}}</span></div>`).join('');
        const more = d.wild.length > 15 ? `<div class="cult-obs-more">…and ${{d.wild.length - 15}} more</div>` : '';
        detail.innerHTML = `
            <div class="cult-detail-head">
                <h3>${{esc(d.scientific_name)}}</h3>
                <div class="cult-detail-counts">
                    <strong style="color:#9a6b12;">${{d.wild_count}}</strong> wild → will be marked cultivated ·
                    <strong>${{d.cultivated_count}}</strong> already cultivated · ${{d.total}} total
                </div>
            </div>
            <div class="cult-obs-list">${{sample}}${{more}}</div>
            <div class="cult-token-box">
                <label>iNaturalist API token <a href="https://www.inaturalist.org/users/api_token" target="_blank" style="font-weight:400;">(get it here)</a></label>
                <input type="password" id="cult-token" placeholder="Paste token…" value="${{esc(cultToken)}}" oninput="cultToken = this.value">
                <div class="cult-token-note">Session only — kept in this browser tab, never written to disk.</div>
            </div>
            <button class="cult-mark-btn" onclick="cultConfirm()">
                Mark ${{d.wild_count}} observation${{d.wild_count !== 1 ? 's' : ''}} cultivated
            </button>
            <div id="cult-result"></div>`;
    }}

    async function cultConfirm() {{
        const d = cultDetailData;
        if (!d) return;
        const tid = d.taxon_id, n = d.wild_count, name = d.scientific_name;
        if (!cultToken.trim()) {{ cultToast('Paste your iNat token first', true); return; }}
        if (!confirm(`Vote NOT WILD (cultivated) on ${{n}} observation(s) of ${{name}}?\\n\\n`
            + `This writes to iNaturalist and changes your whole team's observations. It can be undone per-observation on iNat, but do it deliberately.`)) return;
        const btn = document.querySelector('.cult-mark-btn');
        const result = document.getElementById('cult-result');
        btn.disabled = true; btn.textContent = `Marking ${{n}}… (≈${{n}}s)`;
        result.innerHTML = '<div class="loading">Voting on iNaturalist — one per second, please wait…</div>';
        try {{
            const resp = await fetch('/api/cultivated/mark', {{
                method: 'POST', headers: {{'Content-Type': 'application/json'}},
                body: JSON.stringify({{taxon_id: tid, token: cultToken.trim()}})
            }});
            const data = await resp.json();
            if (!data.ok) {{
                result.innerHTML = `<div class="cult-empty" style="color:#c62828;">${{esc(data.error || 'Failed')}}</div>`;
                btn.disabled = false; btn.textContent = `Mark ${{n}} observation${{n !== 1 ? 's' : ''}} cultivated`;
                return;
            }}
            const failNote = data.failed ? `, <span style="color:#c62828;">${{data.failed}} failed</span>` : '';
            result.innerHTML = `<div class="cult-result-ok">✓ ${{data.marked}} marked cultivated${{failNote}}.</div>`;
            cultToast(`${{data.marked}} marked cultivated`);
            btn.textContent = '✓ Done';
            cultScan();  // refresh worklist so this taxon updates/drops
        }} catch (e) {{
            result.innerHTML = `<div class="cult-empty" style="color:#c62828;">Error: ${{esc(e.message)}}</div>`;
            btn.disabled = false; btn.textContent = `Mark ${{n}} observation${{n !== 1 ? 's' : ''}} cultivated`;
        }}
    }}
    </script>
    """


def render_phenology():
    """Phenology tab — AI-inferred plant phenology from iNat photos, stored
    locally. READ-ONLY with respect to iNaturalist."""
    return """
<style>
  .ph-wrap { display: grid; grid-template-columns: 300px 1fr; gap: 18px; }
  .ph-banner { background:#fff7ef; border:1px solid #f0d9bf; color:#7a5a2e;
    border-radius:9px; padding:10px 14px; font-size:13px; margin-bottom:14px; }
  .ph-banner b { color:#5a3e1a; }
  .ph-pick { max-height:72vh; overflow:auto; }
  .ph-sp { padding:9px 11px; border:1px solid #e6e9ec; border-radius:8px;
    margin-bottom:6px; cursor:pointer; background:#fff; }
  .ph-sp:hover { border-color:#cdd6df; }
  .ph-sp.active { border-color:#2d6a35; background:#f1f8f2; }
  .ph-sp-name { font-weight:600; font-size:14px; }
  .ph-sp-sci { font-style:italic; color:#7a8590; font-size:12px; }
  .ph-sp-meta { font-size:11px; color:#9aa3ad; margin-top:2px; }
  .ph-sp-cov { color:#2d6a35; font-weight:600; }
  .ph-main h2 { margin:0 0 4px; }
  .ph-scanbar { display:flex; gap:10px; align-items:center; margin:10px 0 16px; }
  .ph-btn { background:#1a3a5c; color:#fff; border:none; border-radius:7px;
    padding:8px 14px; font-size:13px; cursor:pointer; }
  .ph-btn:disabled { background:#9aa3ad; cursor:default; }
  .ph-note { font-size:12px; color:#7a8590; }
  .ph-grid { border-collapse:collapse; margin:6px 0 20px; font-size:12px; }
  .ph-grid th, .ph-grid td { border:1px solid #eef1f3; text-align:center;
    padding:4px 6px; min-width:30px; }
  .ph-grid th { background:#f7f9fa; color:#566; font-weight:600; }
  .ph-grid td.sign { text-align:left; font-weight:600; color:#34404a;
    white-space:nowrap; background:#fafbfc; }
  .ph-obs { display:flex; gap:12px; padding:10px; border:1px solid #eef1f3;
    border-radius:9px; margin-bottom:10px; align-items:flex-start; }
  .ph-obs img { width:90px; height:90px; object-fit:cover; border-radius:7px;
    flex-shrink:0; background:#eee; }
  .ph-obs-body { flex:1; }
  .ph-obs-top { font-size:12px; color:#7a8590; margin-bottom:6px; }
  .ph-obs-top a { color:#1a5276; }
  .ph-chips { display:flex; flex-wrap:wrap; gap:6px; }
  .ph-chip { font-size:11px; padding:3px 9px; border-radius:11px; cursor:pointer;
    border:1px solid transparent; user-select:none; }
  .ph-chip.yes { background:#e3f2e6; color:#2d6a35; }
  .ph-chip.no  { background:#eceff1; color:#8a929b; }
  .ph-chip.unsure { background:#fff4d9; color:#8a6300; }
  .ph-obs-note { font-size:12px; color:#67727c; margin-top:6px; font-style:italic; }
  .ph-save { font-size:11px; margin-top:8px; }
  .ph-save button { background:#2d6a35; color:#fff; border:none; border-radius:6px;
    padding:4px 10px; cursor:pointer; font-size:11px; }
  .ph-rev { color:#2d6a35; font-size:11px; font-weight:600; }
  .ph-empty { color:#8a929b; padding:20px; }
  .ph-sugg { background:#f1f8f2; border:1px solid #cfe6d3; border-radius:8px;
    padding:10px 12px; font-size:12px; color:#2d5a33; margin-bottom:14px; }
</style>

<div class="ph-banner">
  🌸 Phenology readings are <b>AI-inferred from iNaturalist photos</b> and stored only in
  your local <code>phenology.json</code>. <b>Nothing is ever written back to iNaturalist</b> —
  you click chips to record a human correction locally. Months shown are suggestions for the
  <code>seasonality</code> fields; copy what you trust.
</div>

<div class="ph-wrap">
  <div>
    <div class="ph-pick" id="ph-pick"><div class="ph-note">Loading plants…</div></div>
  </div>
  <div class="ph-main" id="ph-main">
    <div class="ph-empty">Select a plant to view and build its phenology.</div>
  </div>
</div>

<script>
  const PH_MONTHS = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
  let phSpecies = [];
  let phSelected = null;

  function phEsc(s){ const d=document.createElement('div'); d.textContent=s==null?'':s; return d.innerHTML; }

  async function phLoadSpecies() {
    try {
      const r = await fetch('/api/phenology/species');
      const d = await r.json();
      phSpecies = d.species || [];
      phRenderPicker();
    } catch(e) {
      document.getElementById('ph-pick').innerHTML = '<div class="ph-note">Error loading: '+phEsc(e.message)+'</div>';
    }
  }

  function phRenderPicker() {
    const el = document.getElementById('ph-pick');
    if (!phSpecies.length) { el.innerHTML = '<div class="ph-note">No plants with an iNat taxon found.</div>'; return; }
    el.innerHTML = phSpecies.map(s => `
      <div class="ph-sp ${phSelected===s.id?'active':''}" onclick="phSelect('${s.id}')">
        <div class="ph-sp-name">${phEsc(s.common_name||s.id)}</div>
        <div class="ph-sp-sci">${phEsc(s.scientific_name||'')}</div>
        <div class="ph-sp-meta">${s.id} · ${phEsc(s.status||'')} ·
          ${s.analyzed_count ? `<span class="ph-sp-cov">${s.analyzed_count} analyzed</span>` : 'none yet'}</div>
      </div>`).join('');
  }

  async function phSelect(id) {
    phSelected = id;
    phRenderPicker();
    const main = document.getElementById('ph-main');
    main.innerHTML = '<div class="ph-note">Loading…</div>';
    await phLoadSummary(id);
  }

  async function phLoadSummary(id) {
    try {
      const r = await fetch('/api/phenology/summary?id='+encodeURIComponent(id));
      const d = await r.json();
      phRenderSummary(d);
    } catch(e) {
      document.getElementById('ph-main').innerHTML = '<div class="ph-note">Error: '+phEsc(e.message)+'</div>';
    }
  }

  function phRenderSummary(d) {
    const sp = phSpecies.find(s => s.id === d.id) || {};
    const main = document.getElementById('ph-main');
    let h = `<h2>${phEsc(sp.common_name||d.id)} <span class="ph-sp-sci">${phEsc(sp.scientific_name||'')}</span></h2>`;
    h += `<div class="ph-scanbar">
      <button class="ph-btn" id="ph-scan-btn" onclick="phScan('${d.id}')">🔬 Scan up to 8 new observations</button>
      <span class="ph-note">${d.n_observations} analyzed${d.n_reviewed?` · ${d.n_reviewed} human-reviewed`:''}</span>
    </div>`;
    h += `<div id="ph-scan-status"></div>`;

    if (!d.n_observations) {
      h += '<div class="ph-empty">No observations analyzed yet. Hit “Scan” to have Claude read the iNat photos.</div>';
      main.innerHTML = h; return;
    }

    // Suggested seasonality months
    const fp = (d.months_present.flowers||[]).map(m=>PH_MONTHS[m-1]);
    const frp = (d.months_present.fruit||[]).map(m=>PH_MONTHS[m-1]);
    if (fp.length || frp.length) {
      h += `<div class="ph-sugg">Suggested for <code>seasonality</code>:
        ${fp.length?`<b>flowering</b> ${fp.join(', ')}`:''}${fp.length&&frp.length?' · ':''}${frp.length?`<b>fruiting</b> ${frp.join(', ')}`:''}
        <span class="ph-note">(copy into the species' seasonality fields if you agree)</span></div>`;
    }

    // Monthly grid
    h += '<table class="ph-grid"><tr><th>sign</th>' + PH_MONTHS.map(m=>`<th>${m}</th>`).join('') + '</tr>';
    d.signs.forEach(sg => {
      const row = d.by_sign[sg];
      h += `<tr><td class="sign">${sg.replace('_',' ')}</td>`;
      for (let m=1;m<=12;m++){
        const c = row.months[String(m)]||0;
        const bg = c>0 ? `background:rgba(45,106,53,${Math.min(0.15+c*0.18,0.85)});color:${c>2?'#fff':'#234'}` : '';
        h += `<td style="${bg}">${c||''}</td>`;
      }
      h += '</tr>';
    });
    h += '</table>';

    // Per-observation list
    h += d.observations.map(o => phObsCard(o, d.signs)).join('');
    main.innerHTML = h;
  }

  function phObsCard(o, signs) {
    const eff = (o.human_reviewed && o.human_signs) ? o.human_signs : (o.signs||{});
    const chips = signs.map(sg => {
      const v = eff[sg] || 'unsure';
      return `<span class="ph-chip ${v}" data-obs="${o.obs_id}" data-sign="${sg}" data-val="${v}"
                onclick="phCycle(this)">${sg.replace('_',' ')}: ${v}</span>`;
    }).join('');
    return `<div class="ph-obs" id="ph-obs-${o.obs_id}">
      <img src="${phEsc(o.photo_url)}" alt="obs ${o.obs_id}" loading="lazy">
      <div class="ph-obs-body">
        <div class="ph-obs-top">${phEsc(o.observed_on||'date?')} ·
          <a href="${phEsc(o.obs_url)}" target="_blank" rel="noopener">iNat #${o.obs_id}</a>
          ${o.human_reviewed?'· <span class="ph-rev">✓ human-reviewed</span>':'· AI'}</div>
        <div class="ph-chips">${chips}</div>
        ${o.note?`<div class="ph-obs-note">“${phEsc(o.note)}”</div>`:''}
        <div class="ph-save"><button onclick="phSaveReview('${o.obs_id}')">Save correction</button></div>
      </div>
    </div>`;
  }

  function phCycle(el) {
    const order = ['yes','no','unsure'];
    const cur = el.getAttribute('data-val');
    const next = order[(order.indexOf(cur)+1)%3];
    el.setAttribute('data-val', next);
    el.className = 'ph-chip ' + next;
    el.textContent = el.getAttribute('data-sign').replace('_',' ') + ': ' + next;
  }

  async function phSaveReview(obsId) {
    const chips = document.querySelectorAll(`#ph-obs-${obsId} .ph-chip`);
    const signs = {};
    chips.forEach(c => signs[c.getAttribute('data-sign')] = c.getAttribute('data-val'));
    try {
      const r = await fetch('/api/phenology/review', {
        method:'POST', headers:{'Content-Type':'application/json'},
        body: JSON.stringify({obs_id: obsId, signs: signs})
      });
      const d = await r.json();
      if (d.ok) { await phLoadSummary(phSelected); }
      else alert(d.error||'save failed');
    } catch(e){ alert(e.message); }
  }

  async function phScan(id) {
    const btn = document.getElementById('ph-scan-btn');
    const status = document.getElementById('ph-scan-status');
    if (btn) { btn.disabled = true; btn.textContent = '🔬 Claude is reading photos…'; }
    status.innerHTML = '<div class="ph-note">Fetching observations from iNaturalist and analyzing photos — this can take a bit.</div>';
    try {
      const r = await fetch('/api/phenology/scan', {
        method:'POST', headers:{'Content-Type':'application/json'},
        body: JSON.stringify({id: id, limit: 8})
      });
      const d = await r.json();
      if (!d.ok) { status.innerHTML = '<div class="ph-note" style="color:#a33">⚠️ '+phEsc(d.error)+'</div>'; if(btn){btn.disabled=false;btn.textContent='🔬 Scan up to 8 new observations';} return; }
      const u = d.usage||{};
      status.innerHTML = `<div class="ph-note">✓ Analyzed ${d.analyzed_count}` +
        `${d.remaining?`, ${d.remaining} still unanalyzed (scan again)`:''}` +
        `${d.no_photos?`, ${d.no_photos} had no photo`:''}` +
        `${(d.errors&&d.errors.length)?`, ${d.errors.length} error(s)`:''} · ${u.input_tokens||0} in / ${u.output_tokens||0} out tokens</div>`;
      // refresh species coverage + summary
      await phLoadSpecies();
      await phLoadSummary(id);
    } catch(e) {
      status.innerHTML = '<div class="ph-note" style="color:#a33">⚠️ '+phEsc(e.message)+'</div>';
      if(btn){btn.disabled=false;btn.textContent='🔬 Scan up to 8 new observations';}
    }
  }

  phLoadSpecies();
</script>
"""


PAGE_ROUTES = {

    "/":        ("overview", render_overview),
    "/intake":  ("intake",   render_intake),
    "/photos":  ("photos",   render_photos),
    "/cultivated": ("cultivated", render_cultivated),
    "/phenology": ("phenology", render_phenology),
    "/publish": ("publish",  render_publish),
}

# API routes: path → handler_function
# ════════════════════════════════════════════════════════════════════════════
# AI DRAFT — "Draft with Claude"
#
# Calls the Anthropic Messages API (with the web_search tool) to produce a
# first-cut draft of a species' research/content fields, matching the park's
# established voice and schema. Fills ONLY empty content fields by default —
# never overwrites human/Gemini-written content, and never touches structural,
# ID, taxonomy, photo, or URL-load-bearing fields. The API key is read from the
# ANTHROPIC_API_KEY environment variable and is never stored. A provenance trail
# is written to data/sources/ai_draft_log.json (a sidecar — the signage schema
# is left clean).
# ════════════════════════════════════════════════════════════════════════════

ANTHROPIC_URL      = "https://api.anthropic.com/v1/messages"
ANTHROPIC_VERSION  = "2023-06-01"
AI_MODEL           = "claude-sonnet-4-6"   # bump to "claude-opus-4-8" for max quality
AI_MAX_TOKENS      = 8000
AI_WEB_SEARCH_USES = 8
AI_DRAFT_LOG       = os.path.join(REPO, "data", "sources", "ai_draft_log.json")

# Field → (json-shape, instruction). This is the ONLY surface the AI may write.
# Anything not listed here (id, names, taxonomy, status, photos, observation
# stats, ID-linked similar_species/plant_links, internal_notes …) is protected.
_DRAFT_SPEC_PLANTS = {
    "native":             ("bool", "true if native to Florida / SE United States; false if introduced or cultivated"),
    "alternate_names":    ("list", "common alternate names — list of short strings"),
    "butterfly_host":     ("bool", "true ONLY if a documented larval host plant for butterflies/moths; omit if unknown"),
    "quick_hits":         ("list", "2-4 punchy, surprising one-to-two-sentence facts (list of strings)"),
    "origin":             ("str",  "one short paragraph: native range and how it came to Florida cultivation"),
    "more_information":   ("list", "1-3 engaging natural-history paragraphs (list of strings)"),
    "wildlife_value":     ("list", "1-2 short paragraphs on pollinators / wildlife it supports (list of strings)"),
    "reproduction":       ("dict", 'object {"blocks":[{"label":str,"text":str}], "what_to_look_for":str}'),
    "seasonality":        ("dict", 'object {"bloom_months":str|null,"bloom_description":str|null,"leaf_behavior":str|null,"fruiting_months":str|null,"notes":str|null}'),
    "size":               ("dict", 'object {"height":str,"spread":str,"habit":str,"growth_rate":str,"texture":str}'),
    "growing_conditions": ("dict", 'object {"light":str,"soil_tolerances":str,"drought_tolerance":str,"spacing":str}'),
    "edibility":          ("dict", 'object {"level":"Red|Yellow|Green","detail":str}'),
    "toxicity":           ("dict", 'object {"level":"Red|Yellow|Green","people":str,"dogs_level":"Red|Yellow|Green","dogs":str}'),
    "invasive":           ("dict", 'object {"level":"Red|Yellow|Green","notes":str} — Florida status (UF/IFAS, FLEPPC)'),
    "other_notes":        ("str",  "any extra noteworthy info, or omit"),
}

_DRAFT_SPEC_WILDLIFE = {
    "native":           ("bool", "true if native to Florida; false if introduced"),
    "also_known_as":    ("list", "alternate common names — list of strings"),
    "quick_hits":       ("list", "2-4 punchy one-to-two-sentence facts (list of strings)"),
    "range_and_origin": ("str",  "short paragraph: native range and status in Florida"),
    "more_information": ("list", "1-3 engaging natural-history paragraphs (list of strings)"),
    "identification":   ("dict", 'object {"blocks":[{"label":str,"text":str}], "what_to_look_for":str}'),
    "diet":             ("str",  "what it eats"),
    "behavior":         ("str",  "key behaviors"),
    "sounds":           ("str",  "sounds / vocalizations, or note if essentially silent"),
    "ecological_role":  ("str",  "role in the local food web / ecosystem"),
    "habitat":          ("str",  "preferred habitat"),
    "where_to_look":    ("str",  "where in a Florida park a visitor would spot it"),
    "when_to_see":      ("str",  "time of day / year it is active and visible"),
    "size":             ("dict", 'object {"length":str,"lifespan":str}'),
    "danger":           ("dict", 'object {"people_level":"Red|Yellow|Green","people":str,"pets_level":"Red|Yellow|Green","pets":str}'),
    "interaction":      ("dict", 'object {"level":"Red|Yellow|Green","guidance":str}'),
    "invasive":         ("dict", 'object {"level":"Red|Yellow|Green","notes":str}'),
    "conservation":     ("dict", 'object {"level":"Red|Yellow|Green","status":str}'),
    "seasonality":      ("dict", 'object {"presence":str,"reliability":str,"months":[ints 1-12],"peak":str|null,"note":str}'),
    "sources":          ("list", "the authoritative source URLs you actually used (list of strings)"),
}

_SHAPE_CHECK = {
    "bool": lambda v: isinstance(v, bool),
    "list": lambda v: isinstance(v, list),
    "dict": lambda v: isinstance(v, dict),
    "str":  lambda v: isinstance(v, str),
}


def _draft_spec(kingdom):
    return _DRAFT_SPEC_PLANTS if kingdom == "plants" else _DRAFT_SPEC_WILDLIFE


def _ai_exemplars(kingdom, n=2):
    """Pick the most richly-filled published entries, trimmed to draftable keys,
    as voice/structure references for the model."""
    path = PLANT_SIGNAGE if kingdom == "plants" else WILDLIFE_SIGNAGE
    spec = _draft_spec(kingdom)
    species = [s for s in _get_species_list(_load(path)) if s.get("status") == "html"]

    def richness(e):
        return sum(1 for k in spec if _is_filled(e.get(k)))

    out = []
    for e in sorted(species, key=richness, reverse=True)[:n]:
        out.append({k: e[k] for k in spec if _is_filled(e.get(k))})
    return out


def _ai_build_messages(species, kingdom):
    """Return (system, user_text) for the drafting call."""
    spec = _draft_spec(kingdom)
    noun = "plant" if kingdom == "plants" else "animal"
    sci_field = "botanical_name" if kingdom == "plants" else "scientific_name"
    tax = species.get("taxonomy") or {}

    system = (
        "You are an interpretation writer for Palma Sola Botanical Park, a public "
        "botanical garden in Bradenton, Manatee County, Florida. You write accurate, "
        "engaging signage content in the park's established voice: warm, specific, "
        "factual, lightly surprising, never flowery or padded. This is public-facing "
        "educational signage, so accuracy is paramount. Use the web_search tool to "
        "verify facts against authoritative sources — university extension services and "
        ".edu sites (especially UF/IFAS), USDA, the Florida Native Plant Society, ADW, "
        "IUCN, and botanical-garden references. Never invent facts, numbers, or sources. "
        "If you cannot substantiate a field, omit it. Favor Florida / Gulf-coast-relevant "
        "information."
    )

    schema_lines = "\n".join(
        f'  - "{field}" ({shape}): {instr}' for field, (shape, instr) in spec.items()
    )
    exemplars = _ai_exemplars(kingdom, 2)
    exemplar_json = json.dumps(exemplars, indent=2, ensure_ascii=False)

    target = {
        "common_name": species.get("common_name", ""),
        sci_field: species.get(sci_field, ""),
        "family": tax.get("family", ""),
        "category": species.get("category", ""),
    }

    user = (
        f"Draft first-cut signage content for this {noun}:\n"
        f"{json.dumps(target, indent=2, ensure_ascii=False)}\n\n"
        "FIELD SCHEMA — produce ONLY these fields, each in exactly the shape shown. "
        "Omit any field you cannot responsibly fill from solid sources:\n"
        f"{schema_lines}\n\n"
        "Use \"Red\" / \"Yellow\" / \"Green\" for any *_level field "
        "(Green = safe/fine, Yellow = caution, Red = toxic/dangerous/invasive).\n\n"
        f"Here are {len(exemplars)} existing published entries from this park, for TONE, "
        "depth, and structure only — match this quality; do NOT reuse their facts:\n"
        f"{exemplar_json}\n\n"
        "Do a few targeted web searches, then write a concise first cut — solid and "
        "accurate, not exhaustive (I'll deepen it later). Keep the park's voice.\n\n"
        "OUTPUT CONTRACT: return a single JSON object whose keys are a subset of the "
        "schema fields above. You may also include \"_summary\" (1-2 sentences on what you "
        "drafted and your overall confidence) and \"_low_confidence\" (array of field names "
        "you are least sure about). Wrap the JSON object exactly between a line containing "
        "<<<JSON>>> and a line containing <<<END>>>, and output nothing after <<<END>>>."
    )
    return system, user


def _anthropic_messages(system, user_text, model=AI_MODEL,
                        max_tokens=AI_MAX_TOKENS, web_search=True,
                        max_uses=AI_WEB_SEARCH_USES, timeout=240):
    """POST to the Anthropic Messages API using only the stdlib. Returns the
    parsed response dict, or raises RuntimeError with a friendly message."""
    import urllib.request
    import urllib.error

    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        raise RuntimeError(
            "ANTHROPIC_API_KEY is not set. Export it in the shell that launches the "
            "dashboard (export ANTHROPIC_API_KEY=sk-ant-...), then restart."
        )

    payload = {
        "model": model,
        "max_tokens": max_tokens,
        "system": system,
        "messages": [{"role": "user", "content": user_text}],
    }
    if web_search:
        payload["tools"] = [{"type": "web_search_20250305",
                             "name": "web_search", "max_uses": max_uses}]

    req = urllib.request.Request(
        ANTHROPIC_URL,
        data=json.dumps(payload).encode("utf-8"),
        method="POST",
        headers={
            "x-api-key": key,
            "anthropic-version": ANTHROPIC_VERSION,
            "content-type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:600]
        raise RuntimeError(f"Anthropic API error {e.code}: {detail}")
    except urllib.error.URLError as e:
        raise RuntimeError(f"Could not reach the Anthropic API: {e.reason}")


def _ai_response_text(api_resp):
    """Concatenate all assistant text blocks."""
    return "\n".join(b.get("text", "")
                     for b in api_resp.get("content", [])
                     if b.get("type") == "text")


def _ai_response_searches(api_resp):
    """Count web searches Claude ran (server_tool_use blocks)."""
    return sum(1 for b in api_resp.get("content", [])
               if b.get("type") == "server_tool_use")


def _ai_response_sources(api_resp):
    """Collect cited source URLs from web_search_tool_result blocks."""
    seen, out = set(), []
    for b in api_resp.get("content", []):
        if b.get("type") != "web_search_tool_result":
            continue
        for r in (b.get("content") or []):
            url = r.get("url") if isinstance(r, dict) else None
            if url and url not in seen:
                seen.add(url)
                out.append({"title": r.get("title", ""), "url": url})
    return out


def _ai_parse_json(text):
    """Pull the JSON object out of the model's reply (sentinel-first, then a
    balanced-brace fallback)."""
    import re
    if "<<<JSON>>>" in text and "<<<END>>>" in text:
        chunk = text.split("<<<JSON>>>", 1)[1].split("<<<END>>>", 1)[0]
    else:
        chunk = text
    chunk = re.sub(r"```(?:json)?", "", chunk).strip()
    i, j = chunk.find("{"), chunk.rfind("}")
    if i == -1 or j == -1 or j < i:
        raise ValueError("No JSON object found in the model's reply.")
    return json.loads(chunk[i:j + 1])


def _ai_sanitize(draft, kingdom):
    """Keep only schema fields with the right top-level shape. Returns
    (clean_dict, rejected_keys)."""
    spec = _draft_spec(kingdom)
    clean, rejected = {}, []
    for k, v in draft.items():
        if k.startswith("_"):
            continue
        if k not in spec or v is None:
            if k not in spec and not k.startswith("_"):
                rejected.append(k)
            continue
        if not _SHAPE_CHECK[spec[k][0]](v):
            rejected.append(k)
            continue
        clean[k] = v
    return clean, rejected


def _ai_log_draft(entry):
    """Append a provenance record to the sidecar log (best-effort)."""
    try:
        log = _load(AI_DRAFT_LOG)
        if not isinstance(log, dict):
            log = {}
        log.setdefault("drafts", []).append(entry)
        write_json_atomic(AI_DRAFT_LOG, log)
    except Exception:
        pass


def ai_draft_species(kingdom, species_id, overwrite=False):
    """Orchestrate one AI draft: build prompt → call API → parse → sanitize →
    fill-empty merge → atomic write → log. Returns a report dict."""
    path = PLANT_SIGNAGE if kingdom == "plants" else WILDLIFE_SIGNAGE
    entry = next((s for s in _get_species_list(_load(path))
                  if s.get("id") == species_id), None)
    if not entry:
        return {"ok": False, "error": f"{species_id} not found in {kingdom} signage."}

    system, user = _ai_build_messages(entry, kingdom)
    try:
        api_resp = _anthropic_messages(system, user)
    except RuntimeError as e:
        return {"ok": False, "error": str(e)}

    if api_resp.get("stop_reason") == "max_tokens":
        # Still try to parse — but warn.
        pass

    try:
        draft = _ai_parse_json(_ai_response_text(api_resp))
    except (ValueError, json.JSONDecodeError) as e:
        return {"ok": False,
                "error": f"Could not parse the model's draft: {e}",
                "raw_tail": _ai_response_text(api_resp)[-400:]}

    summary = draft.get("_summary", "")
    low_conf = draft.get("_low_confidence", []) or []
    clean, rejected = _ai_sanitize(draft, kingdom)

    # Re-load immediately before writing so we never clobber a concurrent edit.
    data = _load(path)
    species = _get_species_list(data)
    target = next((s for s in species if s.get("id") == species_id), None)
    if not target:
        return {"ok": False, "error": f"{species_id} disappeared from signage before write."}

    filled, skipped_existing, empty_from_ai = [], [], []
    for k, v in clean.items():
        if not _is_filled(v):
            empty_from_ai.append(k)
            continue
        if not overwrite and _is_filled(target.get(k)):
            skipped_existing.append(k)
            continue
        target[k] = v
        filled.append(k)

    if filled:
        data.setdefault("meta", {})["updated"] = datetime.datetime.now().isoformat(timespec="seconds")
        write_json_atomic(path, data)

    sources = _ai_response_sources(api_resp)
    usage = api_resp.get("usage", {}) or {}
    searches = _ai_response_searches(api_resp)

    _ai_log_draft({
        "ts": datetime.datetime.now().isoformat(timespec="seconds"),
        "id": species_id, "kingdom": kingdom, "model": api_resp.get("model", AI_MODEL),
        "filled": filled, "skipped_existing": skipped_existing,
        "summary": summary, "sources": sources, "usage": usage, "searches": searches,
    })

    return {
        "ok": True,
        "id": species_id,
        "kingdom": kingdom,
        "model": api_resp.get("model", AI_MODEL),
        "filled": filled,
        "skipped_existing": skipped_existing,
        "empty_from_ai": empty_from_ai,
        "rejected_keys": rejected,
        "summary": summary,
        "low_confidence": [k for k in low_conf if isinstance(k, str)],
        "sources": sources,
        "searches": searches,
        "usage": usage,
        "wrote": bool(filled),
    }


def handle_api_ai_draft(params):
    """POST /api/ai/draft  body: {kingdom, id, overwrite?}"""
    body = params.get("_body", {}) or {}
    kingdom = body.get("kingdom", "plants")
    species_id = body.get("id", "")
    overwrite = bool(body.get("overwrite", False))
    if kingdom not in ("plants", "wildlife"):
        return {"ok": False, "error": f"bad kingdom: {kingdom}"}
    if not species_id:
        return {"ok": False, "error": "missing species id"}
    return ai_draft_species(kingdom, species_id, overwrite=overwrite)


# ── Revise: feed ANY feedback (Gemini, a human, your own notes) back to Claude ──
# Same machinery as draft, with one inversion: instead of filling empty fields,
# this OVERWRITES only the fields the feedback implicates and leaves everything
# else byte-for-byte. Iterative by nature — each round sees the latest content.

def _ai_build_revise_messages(species, kingdom, feedback):
    spec = _draft_spec(kingdom)
    sci_field = "botanical_name" if kingdom == "plants" else "scientific_name"
    noun = "plant" if kingdom == "plants" else "animal"
    current = {k: species[k] for k in spec if _is_filled(species.get(k))}

    system = (
        "You are an editor refining existing signage content for Palma Sola Botanical "
        "Park, a public garden in Bradenton, Florida. Apply the reviewer's feedback "
        "precisely. Change ONLY the fields the feedback actually implicates; leave "
        "everything else exactly as it is. Preserve the park's established voice — warm, "
        "specific, factual, lightly surprising, never padded. If the feedback is factual "
        "(a correction, a disputed claim, a request to verify), use the web_search tool "
        "to confirm against authoritative sources (UF/IFAS and other .edu, USDA, FNPS, "
        "ADW, IUCN) before changing it. If the feedback is purely tone, length, or "
        "formatting, no search is needed. Never invent facts."
    )

    schema_lines = "\n".join(
        f'  - "{f}" ({shape}): {instr}' for f, (shape, instr) in spec.items())

    target = {"common_name": species.get("common_name", ""),
              sci_field: species.get(sci_field, "")}

    user = (
        f"Revise the signage content for this {noun}: "
        f"{json.dumps(target, ensure_ascii=False)}.\n\n"
        "CURRENT CONTENT (JSON):\n"
        f"{json.dumps(current, indent=2, ensure_ascii=False)}\n\n"
        "FIELD SCHEMA (shapes you must keep):\n"
        f"{schema_lines}\n\n"
        "REVIEWER FEEDBACK — apply this:\n"
        f"\"\"\"\n{feedback.strip()}\n\"\"\"\n\n"
        "OUTPUT CONTRACT: return a single JSON object containing ONLY the fields you are "
        "changing, each with its full new value in the schema shape. Do NOT include fields "
        "you are leaving unchanged. Also include \"_changes\" (an object mapping each "
        "changed field to a short reason) and \"_summary\" (1-2 sentences). Wrap the JSON "
        "exactly between a line <<<JSON>>> and a line <<<END>>>, nothing after <<<END>>>."
    )
    return system, user


def ai_revise_species(kingdom, species_id, feedback, allow_search=True):
    """Apply reviewer feedback to an existing entry. Overwrites only the fields
    Claude returns (the ones it changed); everything else is untouched."""
    if not (feedback or "").strip():
        return {"ok": False, "error": "No feedback provided."}

    path = PLANT_SIGNAGE if kingdom == "plants" else WILDLIFE_SIGNAGE
    entry = next((s for s in _get_species_list(_load(path))
                  if s.get("id") == species_id), None)
    if not entry:
        return {"ok": False, "error": f"{species_id} not found in {kingdom} signage."}

    system, user = _ai_build_revise_messages(entry, kingdom, feedback)
    try:
        api_resp = _anthropic_messages(system, user, web_search=bool(allow_search),
                                       max_uses=5)
    except RuntimeError as e:
        return {"ok": False, "error": str(e)}

    try:
        draft = _ai_parse_json(_ai_response_text(api_resp))
    except (ValueError, json.JSONDecodeError) as e:
        return {"ok": False, "error": f"Could not parse the revision: {e}",
                "raw_tail": _ai_response_text(api_resp)[-400:]}

    summary = draft.get("_summary", "")
    reasons = draft.get("_changes", {}) or {}
    clean, rejected = _ai_sanitize(draft, kingdom)

    # Re-load right before writing to avoid clobbering a concurrent edit.
    data = _load(path)
    target = next((s for s in _get_species_list(data) if s.get("id") == species_id), None)
    if not target:
        return {"ok": False, "error": f"{species_id} disappeared before write."}

    changed, empty_returned = [], []
    for k, v in clean.items():
        if not _is_filled(v):
            empty_returned.append(k)   # don't blank a field on an empty value
            continue
        target[k] = v
        changed.append(k)

    if changed:
        data.setdefault("meta", {})["updated"] = datetime.datetime.now().isoformat(timespec="seconds")
        write_json_atomic(path, data)

    sources = _ai_response_sources(api_resp)
    usage = api_resp.get("usage", {}) or {}
    searches = _ai_response_searches(api_resp)

    _ai_log_draft({
        "ts": datetime.datetime.now().isoformat(timespec="seconds"),
        "id": species_id, "kingdom": kingdom, "kind": "revise",
        "model": api_resp.get("model", AI_MODEL), "feedback": feedback.strip()[:1000],
        "changed": changed, "summary": summary, "sources": sources,
        "usage": usage, "searches": searches,
    })

    return {
        "ok": True, "id": species_id, "kingdom": kingdom,
        "model": api_resp.get("model", AI_MODEL),
        "changed": changed,
        "reasons": {k: reasons.get(k, "") for k in changed},
        "empty_returned": empty_returned,
        "rejected_keys": rejected,
        "summary": summary,
        "sources": sources, "searches": searches, "usage": usage,
        "wrote": bool(changed),
    }


def handle_api_ai_revise(params):
    """POST /api/ai/revise  body: {kingdom, id, feedback, allow_search?}"""
    body = params.get("_body", {}) or {}
    kingdom = body.get("kingdom", "plants")
    species_id = body.get("id", "")
    feedback = body.get("feedback", "")
    allow_search = bool(body.get("allow_search", True))
    if kingdom not in ("plants", "wildlife"):
        return {"ok": False, "error": f"bad kingdom: {kingdom}"}
    if not species_id:
        return {"ok": False, "error": "missing species id"}
    return ai_revise_species(kingdom, species_id, feedback, allow_search=allow_search)


# ════════════════════════════════════════════════════════════════════════════
# PHENOLOGY — AI-inferred plant phenology from iNat photos
#
# Sends iNat observation photos to Claude (vision) and records which of six
# signs are visibly evident — flowers, flower buds, leaves, leaf buds, fruit,
# seeds — per observation, keyed by the iNat observation ID. Everything is
# stored ONLY in local JSON for website use.
#
#   *** READ-ONLY WITH RESPECT TO iNATURALIST ***
#   This feature NEVER writes to iNat. It only GETs observations and reads
#   public photo URLs. Phenology annotations on iNat are for humans to set.
#   (The only place in this dashboard that POSTs to iNat is the Cultivated tab;
#    nothing here calls _inat_post.)
#
# Records carry human_reviewed / human_signs so a human can override the AI
# reading locally; the monthly summary prefers human truth when present.
# ════════════════════════════════════════════════════════════════════════════

PHENO_MODEL          = "claude-haiku-4-5-20251001"  # cheap + vision; bump to sonnet/opus for tougher IDs
PHENOLOGY_JSON       = os.path.join(REPO, "data", "sources", "phenology.json")
PHENO_SIGNS          = ["flowers", "flower_buds", "leaves", "leaf_buds", "fruit", "seeds"]
PHENO_PHOTOS_PER_OBS = 3       # photos sent to the model per observation
PHENO_MAX_TOKENS     = 700
PHENO_SCAN_DEFAULT   = 8       # observations analyzed per "scan" click


def _pheno_load():
    data = _load(PHENOLOGY_JSON)
    if not isinstance(data, dict):
        data = {}
    data.setdefault("meta", {})
    data.setdefault("observations", {})
    return data


def _pheno_save(data):
    data["meta"]["updated"] = datetime.datetime.now().isoformat(timespec="seconds")
    write_json_atomic(PHENOLOGY_JSON, data)


def _pheno_photo_url(photo):
    """iNat photo objects default to the 'square' thumbnail URL; swap to the
    ~500px 'medium' size — plenty for phenology, cheap to send."""
    url = (photo or {}).get("url") or ""
    return url.replace("square", "medium") if "square" in url else url


def _pheno_plant_index():
    """All plants we could analyze (signage + research) that have an iNat taxon.
    Returns list of {id, common_name, scientific_name, taxon_id, status}."""
    seen, out = set(), []
    sources = [
        (_get_species_list(_load(PLANT_SIGNAGE)), "scientific_name_fallback"),
    ]
    # signage plants
    for sp in _get_species_list(_load(PLANT_SIGNAGE)):
        tid = sp.get("inat_taxon_id")
        if not tid or sp.get("id") in seen:
            continue
        seen.add(sp.get("id"))
        out.append({"id": sp.get("id"), "common_name": sp.get("common_name", ""),
                    "scientific_name": sp.get("botanical_name", ""),
                    "taxon_id": tid, "status": sp.get("status", "")})
    # research plants
    for sp in get_research_list("plants"):
        tid = sp.get("inat_taxon_id")
        if not tid or sp.get("id") in seen:
            continue
        seen.add(sp.get("id"))
        out.append({"id": sp.get("id"), "common_name": sp.get("common_name", ""),
                    "scientific_name": sp.get("scientific_name", ""),
                    "taxon_id": tid, "status": sp.get("status", "")})
    # Order: published (html) first, then spotted, then research, then strays.
    # Within each status band, sort alphabetically by common name.
    _rank = {"html": 0, "spotted": 1, "research": 2}
    out.sort(key=lambda s: (_rank.get(s.get("status"), 3),
                            s.get("common_name") or s.get("id")))
    return out


def _pheno_resolve(psbp_id):
    for sp in _pheno_plant_index():
        if sp["id"] == psbp_id:
            return sp
    return None


def _pheno_prompt(common, sci, observed_on):
    system = (
        "You are a botanist examining photographs to record plant phenology for a "
        "botanical garden's database. Report ONLY what is visibly evident in the "
        "photograph(s) provided — never inferred from prior knowledge of the species. "
        "For each sign answer exactly \"yes\" (clearly visible), \"no\" (clearly absent "
        "or simply not visible in frame), or \"unsure\" (ambiguous / can't tell). Be "
        "conservative: if you cannot clearly see it, use \"unsure\" or \"no\"."
    )
    when = f", observed on {observed_on}" if observed_on else ""
    text = (
        f"These photo(s) are of {common} ({sci}){when}. Looking ONLY at what is visible "
        "in the photo(s), report which of these phenological signs are evident:\n"
        "  - flowers: open blooms\n"
        "  - flower_buds: unopened flower buds\n"
        "  - leaves: foliage present\n"
        "  - leaf_buds: new / emerging leaf buds or fresh growth tips\n"
        "  - fruit: fruit present\n"
        "  - seeds: seeds or seed pods evident\n\n"
        "Output a single JSON object with exactly these keys: flowers, flower_buds, "
        "leaves, leaf_buds, fruit, seeds (each \"yes\"/\"no\"/\"unsure\"), plus \"note\" "
        "(one short sentence). Wrap the JSON exactly between a line <<<JSON>>> and a "
        "line <<<END>>>, with nothing after <<<END>>>."
    )
    return system, text


def _pheno_coerce_signs(raw):
    out = {}
    for s in PHENO_SIGNS:
        v = str(raw.get(s, "unsure")).strip().lower()
        out[s] = v if v in ("yes", "no", "unsure") else "unsure"
    return out


def _pheno_analyze_obs(obs, common, sci):
    """Analyze one observation's photos. Returns a record dict or {'error':...}.
    READ-ONLY: only reads photo URLs; never writes to iNat."""
    photos = obs.get("photos") or []
    urls = [_pheno_photo_url(p) for p in photos if _pheno_photo_url(p)]
    urls = urls[:PHENO_PHOTOS_PER_OBS]
    if not urls:
        return {"error": "no_photos"}

    observed_on = obs.get("observed_on") or (obs.get("observed_on_details") or {}).get("date")
    system, text = _pheno_prompt(common, sci, observed_on)
    content = [{"type": "image", "source": {"type": "url", "url": u}} for u in urls]
    content.append({"type": "text", "text": text})

    try:
        resp = _anthropic_messages(system, content, model=PHENO_MODEL,
                                   max_tokens=PHENO_MAX_TOKENS, web_search=False)
    except RuntimeError as e:
        return {"error": str(e)}

    try:
        raw = _ai_parse_json(_ai_response_text(resp))
    except (ValueError, json.JSONDecodeError) as e:
        return {"error": f"parse: {e}"}

    month = None
    if observed_on and len(observed_on) >= 7 and observed_on[4] == "-":
        try:
            month = int(observed_on[5:7])
        except ValueError:
            month = None

    return {
        "obs_id": obs.get("id"),
        "obs_url": f"https://www.inaturalist.org/observations/{obs.get('id')}",
        "observed_on": observed_on,
        "month": month,
        "photo_url": urls[0],
        "photo_count": len(urls),
        "signs": _pheno_coerce_signs(raw),
        "note": str(raw.get("note", ""))[:300],
        "source": "ai",
        "model": resp.get("model", PHENO_MODEL),
        "analyzed_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "human_reviewed": False,
        "human_signs": None,
        "usage": resp.get("usage", {}) or {},
    }


def phenology_scan(psbp_id, limit=PHENO_SCAN_DEFAULT, force=False):
    """Fetch a species' iNat observations (READ-ONLY) and analyze up to `limit`
    that aren't already recorded. Stores incrementally, keyed by obs id."""
    sp = _pheno_resolve(psbp_id)
    if not sp:
        return {"ok": False, "error": f"{psbp_id} not found as a plant with an iNat taxon."}

    observations = _inat_observations(sp["taxon_id"])   # GET only
    if observations is None:
        return {"ok": False, "error": "Could not reach iNaturalist (check network / INAT_TOKEN)."}

    store = _pheno_load()
    recs = store["observations"]

    analyzed, no_photos, errors = [], 0, []
    in_u = out_u = 0
    remaining = 0

    for obs in observations:
        oid = str(obs.get("id"))
        if not oid or oid == "None":
            continue
        if oid in recs and not force:
            continue
        if not (obs.get("photos")):
            no_photos += 1
            continue
        if len(analyzed) >= limit:
            remaining += 1
            continue

        rec = _pheno_analyze_obs(obs, sp["common_name"], sp["scientific_name"])
        if "error" in rec:
            if rec["error"] == "no_photos":
                no_photos += 1
            else:
                errors.append({"obs_id": oid, "error": rec["error"]})
            # A hard API/credential error: stop early rather than burn the loop.
            if rec["error"].startswith("ANTHROPIC_API_KEY") or "401" in rec["error"]:
                return {"ok": False, "error": rec["error"]}
            continue

        rec["taxon_id"] = sp["taxon_id"]
        rec["psbp_id"] = psbp_id
        rec["kingdom"] = "plants"
        u = rec.pop("usage", {})
        in_u += u.get("input_tokens", 0)
        out_u += u.get("output_tokens", 0)
        recs[oid] = rec
        analyzed.append(oid)
        time.sleep(API_DELAY)

    if analyzed:
        _pheno_save(store)

    # count any still-unanalyzed (with photos) beyond what we did
    total_with_photos = sum(1 for o in observations if o.get("photos"))
    done = sum(1 for o in observations
               if str(o.get("id")) in recs and o.get("photos"))
    remaining = max(total_with_photos - done, 0)

    return {
        "ok": True,
        "id": psbp_id,
        "common_name": sp["common_name"],
        "analyzed": analyzed,
        "analyzed_count": len(analyzed),
        "no_photos": no_photos,
        "errors": errors,
        "remaining": remaining,
        "total_observations": len(observations),
        "model": PHENO_MODEL,
        "usage": {"input_tokens": in_u, "output_tokens": out_u},
    }


def _pheno_effective_signs(rec):
    """Human override wins over the AI reading."""
    if rec.get("human_reviewed") and isinstance(rec.get("human_signs"), dict):
        return _pheno_coerce_signs(rec["human_signs"])
    return rec.get("signs", {})


def phenology_summary(psbp_id):
    """Return this species' stored observations + a per-sign monthly summary.
    'yes' counts toward the month; 'unsure' tallied separately."""
    store = _pheno_load()
    recs = [r for r in store["observations"].values() if r.get("psbp_id") == psbp_id]

    by_sign = {s: {"months": {str(m): 0 for m in range(1, 13)},
                   "yes_total": 0, "unsure_total": 0} for s in PHENO_SIGNS}
    for r in recs:
        eff = _pheno_effective_signs(r)
        m = r.get("month")
        for s in PHENO_SIGNS:
            v = eff.get(s, "unsure")
            if v == "yes":
                by_sign[s]["yes_total"] += 1
                if m:
                    by_sign[s]["months"][str(m)] += 1
            elif v == "unsure":
                by_sign[s]["unsure_total"] += 1

    months_present = {s: [int(m) for m, c in by_sign[s]["months"].items() if c > 0]
                      for s in PHENO_SIGNS}
    for s in months_present:
        months_present[s].sort()

    recs_sorted = sorted(recs, key=lambda r: (r.get("observed_on") or ""), reverse=True)
    reviewed = sum(1 for r in recs if r.get("human_reviewed"))
    return {
        "ok": True,
        "id": psbp_id,
        "n_observations": len(recs),
        "n_reviewed": reviewed,
        "by_sign": by_sign,
        "months_present": months_present,
        "observations": recs_sorted,
        "signs": PHENO_SIGNS,
    }


def phenology_review(obs_id, signs):
    """Apply a HUMAN override to one observation's signs (local only)."""
    store = _pheno_load()
    rec = store["observations"].get(str(obs_id))
    if not rec:
        return {"ok": False, "error": f"obs {obs_id} not recorded."}
    rec["human_signs"] = _pheno_coerce_signs(signs or {})
    rec["human_reviewed"] = True
    rec["reviewed_at"] = datetime.datetime.now().isoformat(timespec="seconds")
    _pheno_save(store)
    return {"ok": True, "obs_id": obs_id, "human_signs": rec["human_signs"]}


def handle_api_phenology_species(params):
    """GET /api/phenology/species — plant list with analyzed coverage."""
    store = _pheno_load()
    counts = {}
    for r in store["observations"].values():
        pid = r.get("psbp_id")
        if pid:
            counts[pid] = counts.get(pid, 0) + 1
    out = _pheno_plant_index()
    for s in out:
        s["analyzed_count"] = counts.get(s["id"], 0)
    return {"ok": True, "species": out}


def handle_api_phenology_summary(params):
    """GET /api/phenology/summary?id=PSBP-xxxxx"""
    pid = params.get("id", [""])[0] if isinstance(params.get("id"), list) else params.get("id", "")
    if not pid:
        return {"ok": False, "error": "missing id"}
    return phenology_summary(pid)


def handle_api_phenology_scan(params):
    """POST /api/phenology/scan  body: {id, limit?, force?}"""
    body = params.get("_body", {}) or {}
    pid = body.get("id", "")
    if not pid:
        return {"ok": False, "error": "missing id"}
    limit = int(body.get("limit", PHENO_SCAN_DEFAULT))
    force = bool(body.get("force", False))
    return phenology_scan(pid, limit=limit, force=force)


def handle_api_phenology_review(params):
    """POST /api/phenology/review  body: {obs_id, signs:{...}}"""
    body = params.get("_body", {}) or {}
    obs_id = body.get("obs_id", "")
    signs = body.get("signs", {})
    if not obs_id:
        return {"ok": False, "error": "missing obs_id"}
    return phenology_review(obs_id, signs)


API_ROUTES = {
    "/api/overview":         handle_api_overview,
    "/api/species":          handle_api_species_list,
    "/api/intake/list":      handle_api_intake_list,
    "/api/intake/detail":    handle_api_intake_detail,
    "/api/intake/check":     handle_api_intake_check,
    "/api/intake/promote":   handle_api_intake_promote,
    "/api/intake/set-status": handle_api_intake_set_status,
    "/api/intake/inat-check": handle_api_intake_inat_check,
    "/api/intake/discover":   handle_api_intake_discover,
    "/api/intake/add-research": handle_api_intake_add_research,
    "/api/photos/species":   handle_api_photos_species,
    "/api/photos/summary":   handle_api_photos_summary,
    "/api/photos/hero":      handle_api_photos_set_hero,
    "/api/photos/roles":     handle_api_photos_update_roles,
    "/api/photos/trash":     handle_api_photos_trash,
    "/api/photos/focus":     handle_api_photos_focus,
    "/api/photos/debug":     handle_api_photos_debug,
    "/api/triage/scan":      handle_api_triage_scan,
    "/api/triage/scan-all":  handle_api_triage_scan_all,
    "/api/triage/scan-progress": handle_api_triage_scan_progress,
    "/api/triage/last-scan": handle_api_triage_last_scan,
    "/api/triage/view":      handle_api_triage_view,
    "/api/triage/decide":    handle_api_triage_decide,
    "/api/preview":          handle_api_preview,
    "/api/publish/list":     handle_api_publish_list,
    "/api/publish/ready":    handle_api_publish_ready,
    "/api/publish/promote":  handle_api_publish_promote,
    "/api/publish/demote":   handle_api_publish_demote,
    "/api/publish/demote-research": handle_api_publish_demote_research,
    "/api/cultivated/audit":   handle_api_cultivated_audit,
    "/api/cultivated/preview": handle_api_cultivated_preview,
    "/api/ai/draft":          handle_api_ai_draft,
    "/api/ai/revise":         handle_api_ai_revise,
    "/api/phenology/species": handle_api_phenology_species,
    "/api/phenology/summary": handle_api_phenology_summary,
    "/api/phenology/scan":    handle_api_phenology_scan,
    "/api/phenology/review":  handle_api_phenology_review,
    "/api/cultivated/mark":    handle_api_cultivated_mark,
    "/api/cultivated/keep":    handle_api_cultivated_keep,
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

        # Live preview: render a species page in memory (no files written)
        if path == "/preview":
            kingdom = params.get("kingdom", ["plants"])[0]
            species_id = params.get("id", [""])[0]
            gaps_mode = params.get("gaps", ["0"])[0] == "1"
            html, code = render_preview_html(kingdom, species_id, gaps_mode)
            self._html_response(code, html)
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

        # Retired routes → redirect somewhere useful (old bookmarks/launchers)
        if path in ("/edit", "/edit-preview"):
            self.send_response(302)
            self.send_header("Location", "/publish")
            self.end_headers()
            return

        # 404 — with a way back
        self._html_response(404, page_shell("overview",
            '<div class="stub-banner"><h2>404 — Page not found</h2>'
            '<p style="margin-top:8px;"><a href="/" style="color:var(--green-mid);">'
            'Back to Overview</a></p></div>'))

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

    server = ThreadingHTTPServer(("", port), DashboardHandler)
    print(f"╔══════════════════════════════════════════════════╗")
    print(f"║  PSBP Species Manager                           ║")
    print(f"║  http://localhost:{port:<5}                       ║")
    print(f"╠══════════════════════════════════════════════════╣")
    print(f"║  Overview .... http://localhost:{port}/           ║")
    print(f"║  Intake ...... http://localhost:{port}/intake     ║")
    print(f"║  Photos ...... http://localhost:{port}/photos     ║")
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
