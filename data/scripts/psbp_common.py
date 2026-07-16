#!/usr/bin/env python3
"""
psbp_common.py — Shared constants, helpers, and credit resolution for PSBP tools.
==================================================================================
Every species tool imports from here. This is the SINGLE SOURCE OF TRUTH for:

  - Repo paths and file locations
  - Photographer name resolution (via photographer_names.json)
  - CC_LICENSES (accepted Creative Commons licenses)
  - Atomic JSON read/write
  - Credit resolution (display name, license, credit line)

Drop this in data/scripts/ alongside the other tools. All tools import from it.

RULE: if you add a new photographer's real name, add it to
photographer_names.json (in data/sources/) and run propagate. If you move
the repo, change REPO below. Those are the only two things to touch.
"""

import json
import os
from pathlib import Path

# ===========================================================================
# REPO ROOT — the ONE line to change if you move the repo.
# ===========================================================================
REPO = Path(__file__).resolve().parents[2]

# ===========================================================================
# DERIVED PATHS  (don't edit — these all follow from REPO)
# ===========================================================================
SOURCES               = REPO / "data" / "sources"
PLANT_SIGNAGE_JSON    = SOURCES / "plant_signage.json"
WILDLIFE_SIGNAGE_JSON = SOURCES / "wildlife_signage.json"
PHOTO_CREDITS_JSON    = SOURCES / "photo_credits.json"
PHOTO_WORKBENCH_JSON  = SOURCES / "photo_workbench.json"
PLANTS_JSON           = REPO / "plants.json"
WILDLIFE_JSON         = REPO / "wildlife.json"
PLANTS_DIR            = REPO / "plants"
WILDLIFE_DIR          = REPO / "wildlife"
PHOTOS_DIR            = REPO / "photos"

# ===========================================================================
# PHOTOGRAPHER NAME REGISTRY
# ===========================================================================
# Real names are stored in photographer_names.json (in data/sources/),
# NOT hardcoded here. That file is the single source of truth:
#   - Editable by hand, by Claude, or (eventually) through the dashboard UI
#   - Tracked in git, travels between machines
#   - Read by display_name() on every call
#
# To add a new photographer's real name:
#   1. Add an entry to photographer_names.json
#   2. Run propagate_photographer_name("their_login") to update existing records
#   3. Re-promote affected species to stamp the new name into HTML + search cards
#
PHOTOGRAPHER_NAMES_JSON = SOURCES / "photographer_names.json"


def _load_photographer_names():
    """Load the photographer names registry. Called on every display_name()
    invocation so edits take effect without restarting the tool.
    The file is tiny (~1 KB), so re-reading is negligible."""
    return load_json(PHOTOGRAPHER_NAMES_JSON, {})

# ===========================================================================
# ACCEPTED LICENSES
# ===========================================================================
CC_LICENSES = frozenset({
    "cc-by", "cc-by-nc", "cc-by-sa", "cc-by-nc-sa",
    "cc-by-nd", "cc-by-nc-nd", "cc0",
})


# ===========================================================================
# ANIMAL GROUPS & THEMES
# ===========================================================================
# Every wildlife species carries an `animal_group` — a short human label
# like "Bird" or "Butterfly" set during research. That value drives two
# things downstream:
#
#   1. The CSS palette on the species HTML page (theme-bird, theme-butterfly,
#      theme-other)
#   2. The filter bucket on nature.html (🐦 Birds, 🦋 Butterflies, 🐾 Other)
#
# THREE THEMES ONLY:
#   bird       — every bird (blue palette)
#   butterfly  — butterflies and moths (pink palette)
#   other      — everything else: reptiles, amphibians, mammals, insects,
#                arachnids, crustaceans (brown palette)
#
# The mapping below is the SINGLE SOURCE OF TRUTH. To add a new animal
# group (e.g. a species where "Fly" or "Bee" doesn't quite fit), add one
# line here mapping the new key to 'other' — no other file needs to change,
# no new palette needed. The value shows on the species page as informational
# ("Group: Ant"); the filter bucket is always Other.
#
# Adding a NEW theme (e.g. splitting insects into their own bucket) is a
# bigger change: also update the CSS palette in wildlife_publisher.py and
# the WILD_THEMES list in site.js. Don't do this casually.
#
# Use theme_for() when you know the value is valid; use check_animal_group()
# at publish time to gate before you attempt to theme a species.
#
ANIMAL_GROUP_TO_THEME = {
    # ── Birds ───────────────────────────────────────────────
    "Bird":        "bird",
    # ── Butterflies & moths ─────────────────────────────────
    "Butterfly":   "butterfly",
    "Moth":        "butterfly",
    # ── Everything else — 'other' bucket ────────────────────
    # Reptiles
    "Lizard":      "other",
    "Turtle":      "other",
    # Mammals
    "Mammal":      "other",
    # Amphibians
    "Frog":        "other",
    "Toad":        "other",
    # Insects (non-lepidoptera)
    "Beetle":      "other",
    "Bee":         "other",
    "Wasp":        "other",
    "Fly":         "other",
    "Dragonfly":   "other",
    "Grasshopper": "other",
    "True Bug":    "other",
    # Arachnids
    "Spider":      "other",
    # Crustaceans
    "Crustacean":  "other",
}

# Derived — never edit; always in sync with the dict above.
VALID_ANIMAL_GROUPS = frozenset(ANIMAL_GROUP_TO_THEME.keys())
VALID_THEMES        = frozenset(ANIMAL_GROUP_TO_THEME.values())


def theme_for(animal_group):
    """Return the CSS/filter theme string for an animal_group value.

    Raises ValueError if the value is missing or not a recognized key.
    Callers that need a graceful skip (e.g. a bulk publisher that should
    log-and-continue) should gate with check_animal_group() first rather
    than catching this exception.
    """
    ag = (animal_group or "").strip()
    if not ag:
        raise ValueError("animal_group is empty")
    if ag not in ANIMAL_GROUP_TO_THEME:
        raise ValueError(
            f"animal_group={ag!r} is not a recognized value "
            f"(valid: {sorted(VALID_ANIMAL_GROUPS)})"
        )
    return ANIMAL_GROUP_TO_THEME[ag]


def check_animal_group(species):
    """Publish-time gate. Return (ok, reason).

    ok=True  → species has a recognized animal_group; theme_for() will work.
    ok=False → reason is a human-readable string explaining what's wrong.

    Use this to fail-closed before writing any files: no wildlife species
    should be published with a missing or unrecognized animal_group value,
    since the theme would default to something wrong and the species would
    land in the wrong filter bucket on nature.html.
    """
    ag = (species.get("animal_group") or "").strip()
    if not ag:
        return False, "animal_group is empty"
    if ag not in ANIMAL_GROUP_TO_THEME:
        return False, (
            f"animal_group={ag!r} is not a recognized value "
            f"(valid: {sorted(VALID_ANIMAL_GROUPS)})"
        )
    return True, ""


# ===========================================================================
# JSON I/O
# ===========================================================================

def load_json(path, default=None):
    """Load a JSON file, returning default if it doesn't exist.

    Usage:
        data = load_json(PHOTO_CREDITS_JSON, {"meta": {}, "photos": []})
    """
    p = Path(path)
    if not p.is_file():
        return default if default is not None else {}
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def write_json_atomic(path, data):
    """Write JSON via temp + os.replace so a crash can never truncate.

    This is the ONLY way any PSBP tool should write a JSON file.
    Direct json.dump() to a production file is a data-loss bug.
    """
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")                    # trailing newline for clean git diffs
    os.replace(str(tmp), str(p))


# ===========================================================================
# CREDIT RESOLUTION
# ===========================================================================

def display_name(login, raw_name=""):
    """Resolve a photographer's display name for crediting.

    Priority order:
      1. photographer_names.json  (our canonical real-name registry)
      2. iNat real name            (from the API / stored in photographer_name)
      3. iNat login handle         (last resort)

    This is the function that answers: "how do we credit this person?"
    Call it everywhere — never hand-format a credit.
    """
    names = _load_photographer_names()
    key = (login or "").lower()
    entry = names.get(key)
    if entry:
        # Entry can be a dict {"display_name": "..."} or a plain string
        if isinstance(entry, dict):
            resolved = entry.get("display_name", "")
            if resolved:
                return resolved
        elif isinstance(entry, str):
            return entry
    name = (raw_name or "").strip()
    return name if name else (login or "unknown")


def build_credit_line(name, license_code):
    """Build the full credit string for display.

    Returns:
        "© Rob Carr (CC-BY-NC), via iNaturalist"
        "© Franky McArthur, via iNaturalist"     (when license unknown)
    """
    lic = (license_code or "").strip().upper()
    if lic and lic != "NAN":
        return f"\u00a9 {name} ({lic}), via iNaturalist"
    return f"\u00a9 {name}, via iNaturalist"


def resolve_hero_credit(hero_record):
    """Given a hero photo record from photo_credits.json, resolve the
    canonical credit fields for stamping into search cards AND HTML pages.

    Returns a dict with three fields:
        credit_name     "Rob Carr"
        credit_license  "CC-BY-NC"
        credit_line     "© Rob Carr (CC-BY-NC), via iNaturalist"

    If hero_record is None (species has no hero), returns empty strings
    so callers don't need to guard.
    """
    if not hero_record:
        return {"credit_name": "", "credit_license": "", "credit_line": ""}
    login    = hero_record.get("photographer", "")
    raw_name = hero_record.get("photographer_name", "")
    name     = display_name(login, raw_name)
    lic      = (hero_record.get("license") or "").strip().upper()
    return {
        "credit_name":    name,
        "credit_license": lic,
        "credit_line":    build_credit_line(name, lic),
    }


def resolve_gallery_credits(photo_records):
    """Build a deduplicated list of photographer credits for a species gallery.

    Used when stamping the credits block at the bottom of a species HTML page.
    Returns one entry per unique photographer, ordered by first appearance.

    Each entry:
        credit_name     "Rob Carr"
        credit_license  "CC-BY-NC"
        credit_line     "© Rob Carr (CC-BY-NC), via iNaturalist"
        inat_login      "robcarr52"
    """
    seen = set()
    credits = []
    for p in (photo_records or []):
        login    = p.get("photographer", "")
        raw_name = p.get("photographer_name", "")
        name     = display_name(login, raw_name)
        if name in seen:
            continue
        seen.add(name)
        lic = (p.get("license") or "").strip().upper()
        credits.append({
            "credit_name":    name,
            "credit_license": lic,
            "credit_line":    build_credit_line(name, lic),
            "inat_login":     login,
        })
    return credits


def propagate_photographer_name(login):
    """After adding/changing a name in photographer_names.json, update all
    matching entries in photo_credits.json with the new display name and
    rebuilt credit line.

    Call this after editing photographer_names.json. It touches
    photo_credits.json ONLY — to get the new name into HTML pages and
    search index cards, re-promote the affected species afterward.

    Args:
        login: the iNat login handle (case-insensitive)

    Returns:
        (count_updated, new_display_name) — how many records changed
        and what the resolved name is.
    """
    credits = load_json(PHOTO_CREDITS_JSON)
    name = display_name(login, "")     # resolves from photographer_names.json
    key = (login or "").lower()
    count = 0
    affected_species = set()

    for p in credits.get("photos", []):
        if (p.get("photographer") or "").lower() != key:
            continue
        old_name = p.get("photographer_name", "")
        if old_name != name:
            p["photographer_name"] = name
            p["credit_line"] = build_credit_line(name, p.get("license", ""))
            count += 1
            affected_species.add(p.get("psbp_id", ""))

    if count:
        write_json_atomic(PHOTO_CREDITS_JSON, credits)

    return count, name, affected_species


def list_photographers():
    """List all photographers in photo_credits.json with their current
    display names and photo counts. Useful for finding handles that
    need a real-name override.

    Returns a list of dicts sorted by photo count (descending):
        [{"login": "robcarr52", "display_name": "Rob Carr",
          "has_override": True, "photo_count": 129}, ...]
    """
    credits = load_json(PHOTO_CREDITS_JSON, {"photos": []})
    names_file = _load_photographer_names()
    photographers = {}

    for p in credits.get("photos", []):
        login = (p.get("photographer") or "").lower()
        if not login:
            continue
        if login not in photographers:
            photographers[login] = {
                "login": login,
                "display_name": display_name(login, p.get("photographer_name", "")),
                "has_override": login in names_file,
                "photo_count": 0,
            }
        photographers[login]["photo_count"] += 1

    return sorted(photographers.values(),
                  key=lambda x: x["photo_count"], reverse=True)


# ===========================================================================
# SIGNAGE HELPERS
# ===========================================================================

def load_signage(corpus):
    """Load the signage JSON for plants or wildlife.

    Args:
        corpus: "plants" or "wildlife"
    Returns:
        The parsed JSON dict (with a "species" key).
    """
    path = PLANT_SIGNAGE_JSON if corpus == "plants" else WILDLIFE_SIGNAGE_JSON
    return load_json(path, {"species": []})


def species_lookup(signage):
    """Build a dict mapping species_id → species record."""
    return {s["id"]: s for s in signage.get("species", [])}


def sci_name_of(species):
    """Get the scientific name from either a plant or wildlife record."""
    return species.get("botanical_name") or species.get("scientific_name") or ""


def credit_type(corpus):
    """Return 'Plant' or 'Wildlife' for tagging photo_credits entries."""
    return "Plant" if corpus == "plants" else "Wildlife"


# ===========================================================================
# HERO + GALLERY LOOKUPS
# ===========================================================================

def build_hero_lookup(credits, type_filter=None):
    """Map psbp_id → hero photo record.

    Args:
        credits: parsed photo_credits.json dict
        type_filter: "Plant" or "Wildlife" to filter, or None for all
    """
    heroes = {}
    for p in credits.get("photos", []):
        if type_filter and p.get("type") != type_filter:
            continue
        if p.get("hero"):
            heroes[p["psbp_id"]] = p
    return heroes


def build_gallery_lookup(credits, type_filter=None):
    """Map psbp_id → list of gallery photos (hero first, then others).

    Args:
        credits: parsed photo_credits.json dict
        type_filter: "Plant" or "Wildlife" to filter, or None for all
    """
    galleries = {}
    for p in credits.get("photos", []):
        if type_filter and p.get("type") != type_filter:
            continue
        if "gallery" not in (p.get("role") or []):
            continue
        pid = p["psbp_id"]
        galleries.setdefault(pid, []).append(p)
    # Hero first in each gallery list.
    for pid in galleries:
        galleries[pid].sort(
            key=lambda p: (not p.get("hero", False), p.get("photo_id", ""))
        )
    return galleries


# ===========================================================================
# STATUS TRANSITIONS
# ===========================================================================

def update_signage_status(corpus, species_id, new_status):
    """Change a species' status in its signage JSON (atomic write).

    This is the only function that should flip status values.
    """
    path = PLANT_SIGNAGE_JSON if corpus == "plants" else WILDLIFE_SIGNAGE_JSON
    signage = load_json(path)
    for s in signage.get("species", []):
        if s["id"] == species_id:
            s["status"] = new_status
            break
    write_json_atomic(path, signage)


def delete_species_page(corpus, species_id, common_name=""):
    """Delete the generated HTML page(s) for a species from disk.

    Called during demotion. Handles the filename pattern
    PSBP-xxxxx-Common-Name.html and catches any variants via glob.
    Returns the list of deleted filenames (for logging).
    """
    import re
    target_dir = PLANTS_DIR if corpus == "plants" else WILDLIFE_DIR
    deleted = []
    for f in target_dir.glob(f"{species_id}-*.html"):
        f.unlink()
        deleted.append(f.name)
    return deleted
