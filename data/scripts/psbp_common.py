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
import re
import shutil
from datetime import datetime
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
PUBLISH_STATE_JSON    = SOURCES / "publish_state.json"
PLANTS_JSON           = REPO / "plants.json"
WILDLIFE_JSON         = REPO / "wildlife.json"
PLANTS_DIR            = REPO / "plants"
WILDLIFE_DIR          = REPO / "wildlife"
PHOTOS_DIR            = REPO / "photos"
PERMALINK_DIR         = REPO / "p"        # short QR permalinks: p/00719/index.html

# ===========================================================================
# MEDIA LIBRARY (Cloudflare R2) — where species photographs are served from
# ===========================================================================
# Off: pages use today's addresses (a local copy under photos/ when one
# exists, otherwise the iNaturalist URL on the photo row). On: every
# photograph on every regenerated page loads from R2 at
#   <MEDIA_BASE>/inat/<photo_id>/<MEDIA_REV>/<size>.jpg
# with size = original | web | thumb. Nothing is stored per photo; the address
# is built at generation time, so a new host is one line here plus
# --generate-all. Flipping MEDIA_ON back to False and regenerating is the
# rollback. MEDIA_BASE is the sandbox bucket while the library is rehearsed;
# it becomes the production bucket, then https://media.palmasolabp.org.
MEDIA_BASE = "https://pub-895c4e39efa04b698caca4bce36ba281.r2.dev"
MEDIA_REV  = "v1"
MEDIA_ON   = False


# One species at a time, while the library fills (Randy, 10-01): with the
# whole-site switch still off, a species page takes its photographs from R2 as
# soon as EVERY published photo of that species is confirmed in the bucket;
# until then it keeps today's addresses, unchanged. The card photo in
# plants.json / wildlife.json never moves this way (the TV decks read it), only
# with MEDIA_ON. A photo is confirmed by asking the bucket's public address
# once; a yes is remembered outside the repo, because nothing is ever deleted
# from the bucket. A no is asked again after a minute.
MEDIA_PER_SPECIES = True
MEDIA_LOCAL_ROOT = (Path(r"C:\PSBP\data\media") if os.name == "nt" else Path.home() / "PSBP-media")
MEDIA_CONFIRMED_JSON = MEDIA_LOCAL_ROOT / "manifests" / "r2_confirmed.json"
_media_state = {"ids": None, "mtime": None, "no": {}, "offline_until": 0.0,
                "credits_mtime": None, "by_species": {}}


def _media_confirmed_ids():
    st = _media_state
    try:
        mtime = MEDIA_CONFIRMED_JSON.stat().st_mtime
    except OSError:
        mtime = None
    if st["ids"] is None or mtime != st["mtime"]:
        data = load_json(MEDIA_CONFIRMED_JSON, {}) if mtime else {}
        st["ids"] = set(data.get(MEDIA_BASE, []))
        st["mtime"] = mtime
    return st["ids"]


def media_confirm(photo_id):
    """Remember that this photo's three files are in the bucket."""
    ids = _media_confirmed_ids()
    if str(photo_id) in ids:
        return
    ids.add(str(photo_id))
    try:
        MEDIA_CONFIRMED_JSON.parent.mkdir(parents=True, exist_ok=True)
        data = load_json(MEDIA_CONFIRMED_JSON, {}) or {}
        data[MEDIA_BASE] = sorted(set(data.get(MEDIA_BASE, [])) | ids)
        write_json_atomic(MEDIA_CONFIRMED_JSON, data)
        _media_state["mtime"] = MEDIA_CONFIRMED_JSON.stat().st_mtime
    except OSError:
        pass                      # the answer still holds for this run


def media_in_bucket(photo_id):
    """True when the photo is confirmed in the bucket. thumb.jpg is the last of
    the three files both uploaders send, so its presence means all three."""
    import time
    import urllib.error
    import urllib.request
    pid = str(photo_id)
    st = _media_state
    if pid in _media_confirmed_ids():
        return True
    now = time.time()
    if now < st["offline_until"] or now - st["no"].get(pid, 0) < 60:
        return False
    req = urllib.request.Request(f"{MEDIA_BASE}/inat/{pid}/{MEDIA_REV}/thumb.jpg", method="HEAD",
                                 headers={"User-Agent": "PalmaSolaBotanicalPark-publisher/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=8) as r:
            ok = r.status == 200
    except urllib.error.HTTPError as e:
        ok = False
        if e.code != 404:
            st["offline_until"] = now + 60      # blocked or throttled: stop asking for a minute
    except (urllib.error.URLError, OSError):
        ok = False
        st["offline_until"] = now + 60          # no network: pages keep today's addresses
    if ok:
        media_confirm(pid)
    else:
        st["no"][pid] = now
    return ok


def media_confirmed_offline(photo_id):
    """True when the local record already says this photo is in the bucket.
    Never touches the network, so a list page can ask about hundreds of photos."""
    return str(photo_id) in _media_confirmed_ids()


def media_ready(psbp_id):
    """True when every published photo of this species is in the bucket."""
    if not psbp_id:
        return False
    st = _media_state
    try:
        mtime = PHOTO_CREDITS_JSON.stat().st_mtime
    except OSError:
        return False
    if mtime != st["credits_mtime"]:
        by = {}
        for p in load_json(PHOTO_CREDITS_JSON, {"photos": []}).get("photos", []):
            if p.get("publish_ok") and p.get("photo_id") and p.get("psbp_id"):
                by.setdefault(p["psbp_id"], []).append(str(p["photo_id"]))
        st["by_species"], st["credits_mtime"] = by, mtime
    ids = st["by_species"].get(psbp_id)
    return bool(ids) and all(media_in_bucket(i) for i in ids)


def media_url(rec, size="web", whole_site_only=False):
    """R2 address for a photo row at one size, or None when this photo should
    keep today's address: the library is off and its species is not wholly in
    the bucket yet, or the row has no iNat photo id. `whole_site_only` is for
    the card photo, which moves only with MEDIA_ON."""
    if not rec:
        return None
    photo_id = rec.get("photo_id")
    if not photo_id:
        return None
    if not MEDIA_ON:
        if whole_site_only or not MEDIA_PER_SPECIES or not media_ready(rec.get("psbp_id")):
            return None
    return f"{MEDIA_BASE}/inat/{photo_id}/{MEDIA_REV}/{size}.jpg"

# ===========================================================================
# PARK MEDIA LIBRARY on species pages
# ===========================================================================
# media_library.json (written by media_intake.py) holds everything that is not
# an iNaturalist species photo. A page block {"media": "PM-000123"} places one
# of its items in the flow. The rule for whether it may show lives HERE, once:
# the publishers render through it and audit_psbp.py --only PAGE reports
# through it, so they cannot disagree.
MEDIA_LIBRARY_JSON = SOURCES / "media_library.json"

# A gifted photograph is not Creative Commons; "permission" records that the
# photographer gave the park the right to show it. Anything else is refused.
MEDIA_PAGE_LICENSES = frozenset({"cc-by", "cc-by-nc", "cc-by-sa", "cc-by-nc-sa",
                                 "cc-by-nd", "cc-by-nc-nd", "cc0", "permission"})


def media_page_check(media_id):
    """(record, problem) for one media item named on a species page. The
    record comes back only when every condition holds; otherwise `problem`
    says which one failed, in words the audit can print."""
    lib = load_json(MEDIA_LIBRARY_JSON, {"items": []})
    rec = next((r for r in lib.get("items", []) if r.get("media_id") == media_id), None)
    if not rec:
        return None, f"{media_id} is not in media_library.json"
    if rec.get("public") != "yes":
        return None, f"{media_id} is not public ({rec.get('public')})"
    if rec.get("kids"):
        return None, f"{media_id} has kids in it"
    if rec.get("state") != "done" or not rec.get("url"):
        return None, f"{media_id} is not uploaded yet (state {rec.get('state')})"
    if not (rec.get("credit_line") or rec.get("made_by")):
        return None, f"{media_id} has no credit line and no maker"
    if (rec.get("license") or "").lower() not in MEDIA_PAGE_LICENSES:
        return None, f"{media_id} has no license a page may publish under (license {rec.get('license')!r})"
    return rec, ""


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
    "Snake":       "other",
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
    # Any other insect order — lacewings and antlions (Neuroptera), mantids,
    # earwigs, ants. A real value, not a dumping ground: it exists so an
    # unlisted order can be labelled honestly instead of being forced into the
    # nearest wrong key. PSBP-90005 (Antlions and Owlflies) was published as
    # "Fly" for exactly that reason — antlions are Neuroptera, not Diptera.
    "Insect":      "other",
    # Arachnids
    "Spider":      "other",
    # Crustaceans
    "Crustacean":  "other",
}

# Derived — never edit; always in sync with the dict above.
VALID_ANIMAL_GROUPS = frozenset(ANIMAL_GROUP_TO_THEME.keys())
VALID_THEMES        = frozenset(ANIMAL_GROUP_TO_THEME.values())


# ── deriving animal_group from iNaturalist taxonomy ─────────────────────────
#
# animal_group is a TAXONOMIC FACT, not a curatorial judgement. A raccoon is a
# Mammal whatever the park thinks. It was previously hand-typed, which is how
# one reached `spotted` filed as "Fly" — a valid key, mapping to the same theme
# bucket as Mammal, so it rendered perfectly and would have gone live.
#
# The iNat /v1/taxa response already carries everything needed, and intake
# already fetches it to read family and genus. class + order settle almost
# every case.
#
# THE RULE: derive only when confident, return None otherwise. An empty value
# is caught by check_animal_group(); a plausible wrong one is caught by nobody.
# So snakes (Squamata, but no "Snake" key), salamanders, fish and molluscs
# deliberately return None and wait for a human rather than being approximated.

_BEE_FAMILIES = {"Apidae", "Andrenidae", "Halictidae", "Megachilidae",
                 "Colletidae", "Melittidae", "Stenotritidae"}

# order -> animal_group, for class Insecta.
_INSECT_ORDERS = {
    "Coleoptera":   "Beetle",
    "Diptera":      "Fly",
    "Odonata":      "Dragonfly",     # covers damselflies too — no separate key
    "Orthoptera":   "Grasshopper",
    "Hemiptera":    "True Bug",
}


def derive_animal_group(taxon):
    """Best-effort animal_group from an iNat taxon dict.

    `taxon` needs: rank, iconic (iconic_taxon_name), and an `ancestors` map of
    rank -> name (at least class / order / family / superfamily).

    Returns (animal_group, reason). animal_group is None when the taxonomy does
    not settle it — the caller should leave the field blank and let a human
    choose, NEVER substitute a near-miss.
    """
    anc     = taxon.get("ancestors") or {}
    klass   = anc.get("class") or ""
    order   = anc.get("order") or ""
    family  = anc.get("family") or ""
    superf  = anc.get("superfamily") or ""
    iconic  = (taxon.get("iconic") or "").strip()

    # class is the reliable discriminator; fall back to the iconic taxon, which
    # is the same rank for every animal group the park records.
    klass = klass or iconic

    if klass == "Aves":         return "Bird", "class Aves"
    if klass == "Mammalia":     return "Mammal", "class Mammalia"
    if klass == "Arachnida":
        if order and order != "Araneae":
            return None, f"Arachnida but order {order} is not a spider"
        return "Spider", "class Arachnida"
    if klass == "Malacostraca": return "Crustacean", "class Malacostraca"

    if klass == "Reptilia":
        if order == "Testudines": return "Turtle", "order Testudines"
        if order == "Squamata":
            # Squamata is lizards AND snakes, and iNat splits them at suborder:
            # Sauria for lizards, Serpentes for snakes. Without the suborder
            # the order alone cannot tell them apart, so that case waits for a
            # human rather than guessing at a coin flip.
            sub = anc.get("suborder") or ""
            if sub == "Sauria":    return "Lizard", "suborder Sauria"
            if sub == "Serpentes": return "Snake", "suborder Serpentes"
            return None, "order Squamata, suborder unknown — lizard or snake?"
        return None, f"Reptilia, order {order or 'unknown'}"

    if klass == "Amphibia":
        if order == "Anura":
            if family == "Bufonidae": return "Toad", "family Bufonidae"
            return "Frog", "order Anura"
        return None, f"Amphibia, order {order or 'unknown'}"

    if klass == "Insecta":
        if order == "Lepidoptera":
            # Papilionoidea is butterflies and skippers; everything else is a
            # moth. Superfamily is present on the ancestors list for both.
            if superf == "Papilionoidea": return "Butterfly", "superfamily Papilionoidea"
            if superf:                    return "Moth", f"Lepidoptera, superfamily {superf}"
            return None, "Lepidoptera with no superfamily — butterfly or moth?"
        if order == "Hymenoptera":
            if family in _BEE_FAMILIES: return "Bee", f"family {family}"
            if family == "Formicidae":  return "Insect", "family Formicidae (ants)"
            if family:                  return "Wasp", f"Hymenoptera, family {family}"
            return None, "Hymenoptera with no family — bee or wasp?"
        if order in _INSECT_ORDERS:
            return _INSECT_ORDERS[order], f"order {order}"
        if order:
            # A real insect in an order with no dedicated key — Neuroptera,
            # Mantodea, Dermaptera. This is what "Insect" is for.
            return "Insect", f"order {order}, no dedicated key"
        return None, "Insecta with no order"

    return None, f"class {klass or 'unknown'} has no mapping"


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


# ===========================================================================
# COMMON-NAME CASING
# ===========================================================================

_TITLE_SMALL_WORDS = {
    "a", "an", "and", "as", "at", "but", "by", "de", "del", "for", "in", "la",
    "of", "on", "or", "the", "to", "van", "von", "with", "upon",
}


def proper_common_name(name):
    """Title-case a common name the way the catalogue writes them.

    iNaturalist returns common names in its own casing — 'aloe vera',
    'thorny olive', 'century plants' — and the original spreadsheet was no more
    consistent. Left alone they have to be corrected by hand, one record at a
    time, which is the chore this removes. Applied at intake so it never
    accumulates.

    HYPHENATED NAMES ARE RETURNED UNCHANGED, DELIBERATELY.
    'Black-throated Blue Warbler' and 'black-eyed Susan' are the CORRECT forms:
    ornithological and botanical usage both lowercase the second element of a
    hyphenated compound. Capitalising it would be an error, not a fix, and iNat
    already has these right. 'Four-o-clock' is a further trap — that 'o' is a
    contraction of o'clock. So anything with a hyphen is left exactly as found
    and stays a human decision.

    Also never capitalises the letter after an apostrophe ("Buddha's Belly",
    not "Buddha'S Belly"), keeps existing acronyms, and keeps small words
    lowercase unless they lead or close the name.
    """
    n = (name or "").strip()
    if not n or "-" in n:
        return name

    def cap(tok, first, last):
        if len(tok) > 1 and tok.isupper():
            return tok
        if tok.lower() in _TITLE_SMALL_WORDS and not first and not last:
            return tok.lower()
        out, up = [], True
        for ch in tok:
            out.append(ch.upper() if (up and ch.isalpha()) else ch.lower())
            if up and ch.isalpha():
                up = False
        return "".join(out)

    words = n.split(" ")
    return " ".join(cap(w, i == 0, i == len(words) - 1)
                    for i, w in enumerate(words))


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


# Backups go OUTSIDE the repo. Override with PSBP_BACKUP_DIR.
BACKUP_DIR = Path(os.environ.get("PSBP_BACKUP_DIR")
                  or Path.home() / "Documents" / "PSBP" / "backups")


def backup_file(path):
    """Copy a file to the out-of-repo backup folder. Returns the new path.

    This is the ONLY way a PSBP tool should back up a master before writing.

    WHY NOT ALONGSIDE THE FILE (added 2026-09-03, after it bit):
    a backup written next to a master lands inside a GitHub Pages repo. Four
    scripts did this; three used a `.bak-<timestamp>` suffix, which `.gitignore`
    (`*.bak`) does NOT match — so a 1.1 MB copy of photo_credits.json showed up
    as a new file to stage and was only stopped by the pre-commit size guard.

    It is also redundant. These files are tracked, so git already holds every
    prior version: `git checkout <sha> -- <path>` is the real safety net, and
    GitHub Desktop's Discard Changes does the same for uncommitted work. The
    copy on disk protects against exactly one thing git doesn't — a bad write
    that happens while other changes are already staged — which is worth
    keeping, just not in here.
    """
    src = Path(path)
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    dest = BACKUP_DIR / f"{src.stem}.{stamp}{src.suffix}"
    shutil.copy2(src, dest)
    return dest


# ===========================================================================
# CREDIT RESOLUTION
# ===========================================================================

def display_name(login, raw_name=""):
    """Resolve a photographer's display name for crediting.

    Priority order:
      1. photographer_names.json  "credit_as"     (a stand-in the person is
                                                   credited under instead of
                                                   their name — delete the
                                                   field to credit by name)
      2. photographer_names.json  "display_name"  (our canonical real-name registry)
      3. iNat real name            (from the API / stored in photographer_name)
      4. iNat login handle         (last resort)

    This is the function that answers: "how do we credit this person?"
    Call it everywhere — never hand-format a credit.
    """
    names = _load_photographer_names()
    key = (login or "").lower()
    entry = names.get(key)
    if entry:
        # Entry can be a dict {"display_name": "..."} or a plain string
        if isinstance(entry, dict):
            resolved = entry.get("credit_as", "") or entry.get("display_name", "")
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

    Returns a dict with four fields:
        credit_login    "robcarr52"                                (the KEY)
        credit_name     "Rob Carr"                                 (the DISPLAY)
        credit_license  "CC-BY-NC"
        credit_line     "© Rob Carr (CC-BY-NC), via iNaturalist"

    THE LOGIN AND THE NAME ARE BOTH REQUIRED, FOR DIFFERENT JOBS.
    The login is the stable key: display names change, logins do not, and two
    contributors can share a display name. The name is what a human reads —
    nobody wants `theblackd0g13` on a species page. Carrying only one of them
    is what broke this: the card's `credit` field was fed `credit_name`, so
    every card stored the display name twice and the login nowhere, and
    nothing could link a photograph back to its photographer.

    This function already computed the login and simply didn't return it.

    If hero_record is None (species has no hero), returns empty strings
    so callers don't need to guard.
    """
    if not hero_record:
        return {"credit_login": "", "credit_name": "", "credit_license": "",
                "credit_line": ""}
    login    = hero_record.get("photographer", "")
    raw_name = hero_record.get("photographer_name", "")
    name     = display_name(login, raw_name)
    lic      = (hero_record.get("license") or "").strip().upper()
    return {
        "credit_login":   login,
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
        (count_updated, new_display_name, affected_species) — a THREE-tuple:
        how many photo records changed, the resolved display name, and the
        sorted list of PSBP ids that need re-promoting to get the new name
        onto their pages and index cards.

        (This said two values until 2026-08-24, which would have raised
        ValueError in any caller that unpacked it. Nothing called it until
        07-24, so the error never fired.)
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


def card_hits(species):
    """The bullets shown ANYWHERE OTHER THAN THE PAGE ITSELF: authored
    `page.at_a_glance` if it exists, otherwise the original `quick_hits`.

    Randy, 2026-09-08: "we keep it and shift to pages.ataglance when it is
    available. quick hit would ONLY go to index if at a glance isn't populated
    of course."

    THIS LIVES HERE BECAUSE IT HAS THREE CALLERS, NOT TWO. Both publishers
    stamp it into plants.json / wildlife.json (search cards, the nature.html
    browse drawer, screen.html), and validate_promote.py stamps it onto the
    Right Now cards on the home page. It was originally written twice, once
    per publisher, and the Right Now path was missed entirely — so an authored
    species showed its authored bullet on its own page and its superseded draft
    on the home page. A single copy is what stops that recurring.

    Reads `page.at_a_glance` blocks, which are {"text": ...} dicts, but
    tolerates bare strings so a hand-edited record cannot blank a card.
    """
    pol = (species.get("page") or {}).get("at_a_glance")
    if pol:
        out = []
        for b in pol:
            t = b.get("text") if isinstance(b, dict) else b
            if t:
                out.append(str(t))
        if out:
            return out
    return species.get("quick_hits") or []


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


def permalink_stub_html(corpus_dir, filename, common_name):
    """The ~400-byte redirect behind a printed QR code: /p/00719 -> the species page.

    Two levels down, so the RELATIVE ../../<corpus>/<file> resolves the same under
    github.io/explore/p/00719/ today and palmasolabp.org/p/00719/ after cutover,
    with no regeneration. location.replace() keeps Back from bouncing the visitor
    into the redirect again; the meta refresh is the no-JS fallback.

    ?src=sign is the one thing that can't be added after lamination, so it lives
    HERE rather than in the QR: the code stays short, and GoatCounter (which reads
    `src` as the source) names every /p/ scan as a sign. /p/ is printed only on signs.
    """
    import html as _h
    target = f"../../{corpus_dir}/{filename}"
    dest = f"{target}?src=sign"
    title = _h.escape(f"{common_name} — Palma Sola Botanical Park")
    return (
        '<!doctype html>\n'
        '<meta charset="utf-8">\n'
        f'<title>{title}</title>\n'
        '<meta name="viewport" content="width=device-width,initial-scale=1">\n'
        f'<link rel="canonical" href="{target}">\n'
        f'<meta http-equiv="refresh" content="0; url={dest}">\n'
        f'<script>location.replace("{dest}"+location.hash)</script>\n'
        f'<p><a href="{dest}">{title}</a></p>\n'
    )


def write_permalink_stub(psbp_id, corpus_dir, filename, common_name):
    """Write p/<digits>/index.html for a published species. Idempotent: an
    unchanged stub is not rewritten. Called from the publisher's write_html, so
    the page and its stub are written by the same call and cannot disagree.
    Returns the path written, or None if it was already current."""
    digits = re.sub(r"\D", "", psbp_id)
    path = PERMALINK_DIR / digits / "index.html"
    content = permalink_stub_html(corpus_dir, filename, common_name)
    if path.is_file() and path.read_text(encoding="utf-8") == content:
        return None
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(content, encoding="utf-8")
    tmp.rename(path)
    return path


def remove_permalink_stub(psbp_id):
    """Remove p/<digits>/ on demotion. A printed code for a demoted species then
    404s, which is the invariant the audit expects; stubs must never outlive pages."""
    digits = re.sub(r"\D", "", psbp_id)
    d = PERMALINK_DIR / digits
    gone = False
    if d.is_dir():
        for f in d.iterdir():
            f.unlink()
        d.rmdir()
        gone = True
    return gone


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
    if remove_permalink_stub(species_id):
        deleted.append("p/" + re.sub(r"\D", "", species_id) + "/")
    # Drop the publish record too — a species with no page must not keep
    # claiming a publish date. Same choke-point logic as write_html.
    try:
        forget_publish(species_id)
    except Exception:                                          # noqa: BLE001
        pass
    return deleted


# ===========================================================================
# PUBLISH STATE  —  when was each page last published, and from what inputs
# ===========================================================================
#
# publish_state.json is machine-owned. Nothing here is hand-edited, and
# deleting the file is safe: it rebuilds as pages are republished, and
# psbp_page_drift.py can still answer staleness on its own by rendering.
#
#   {"meta": {"hash_version": 1, "updated": "..."},
#    "species": {"PSBP-00003": {"last_published": "2026-07-24",
#                               "input_hash": "a1b2...",   # data fingerprint
#                               "generator":  "c3d4...",   # template fingerprint
#                               "filename":   "PSBP-00003-Buccaneer-Palm.html",
#                               "corpus":     "plants"}}}
#
# WHY IT EXISTS: staleness used to be answered by parsing rendered HTML and
# pulling photo IDs out of image URLs — scraping our own output to ask a
# question the data should have answered. With these two fingerprints stored,
# "is this page older than its data?" is a string comparison.
#
# input_hash covers the species signage record + its hero + its gallery rows.
# generator covers the publisher module source, because a template edit makes
# every page stale while every input hash stays identical. Both must match for
# a page to be considered current.
#
# HASH_VERSION must be bumped if the canonical form below ever changes.
# Consumers (audit_psbp.py) check it and decline to compare rather than
# silently reporting wrong answers against a format they don't understand.

HASH_VERSION = 1


def _canonical(obj):
    """Stable JSON for hashing: sorted keys, no incidental whitespace."""
    import json as _json
    return _json.dumps(obj, sort_keys=True, separators=(",", ":"),
                       ensure_ascii=False, default=str)


def compute_input_hash(species, hero, gallery_photos):
    """Fingerprint of everything that feeds a generated page.

    Gallery rows are sorted by photo_id so that reordering in the registry
    doesn't read as a content change.
    """
    import hashlib
    gallery = sorted(
        (p for p in (gallery_photos or [])),
        key=lambda p: str(p.get("photo_id", "")),
    )
    payload = _canonical({
        "species": species,
        "hero": hero,
        "gallery": gallery,
    })
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def generator_fingerprint(module):
    """Fingerprint of a publisher module's source.

    Deliberately covers the whole file rather than just the template string:
    over-triggering (an unrelated edit marks pages stale) costs one idempotent
    regeneration, while under-triggering leaves pages silently wrong. Erring
    toward the cheap failure is the point.
    """
    import hashlib
    try:
        src = Path(module.__file__).read_bytes()
    except Exception:
        return ""
    return hashlib.sha256(src).hexdigest()[:16]


def load_publish_state():
    """Read publish_state.json, returning the empty shape if absent."""
    state = load_json(PUBLISH_STATE_JSON, None)
    if not isinstance(state, dict):
        state = {}
    state.setdefault("meta", {})
    state.setdefault("species", {})
    return state


def get_publish_record(species_id, state=None):
    """The stored record for one species, or None."""
    st = state if state is not None else load_publish_state()
    rec = st.get("species", {}).get(species_id)
    return rec if isinstance(rec, dict) else None


def record_publish(corpus, species_id, input_hash, generator, filename,
                   last_published):
    """Write one species' publish record — but ONLY if it actually changed.

    This no-op-on-identical behaviour matters more than it looks. --generate-all
    calls write_html for all 289 species; if every call rewrote this file (and
    bumped a meta timestamp), publish_state.json would appear in every commit
    even when nothing else did. The whole value of "generate-all, then read the
    git diff" is that untouched pages produce no diff. Bookkeeping must not be
    the thing that breaks that.
    """
    import datetime
    state = load_publish_state()
    entry = {
        "last_published": last_published,
        "input_hash": input_hash,
        "generator": generator,
        "filename": filename,
        "corpus": corpus,
    }
    if state.get("species", {}).get(species_id) == entry:
        return entry                      # nothing to write
    state["species"][species_id] = entry
    state["meta"]["hash_version"] = HASH_VERSION
    state["meta"]["updated"] = datetime.datetime.now().isoformat(timespec="seconds")
    state["meta"]["species_count"] = len(state["species"])
    write_json_atomic(PUBLISH_STATE_JSON, state)
    return entry


def forget_publish(species_id):
    """Drop a species' record — called when its page is deleted."""
    state = load_publish_state()
    if species_id in state.get("species", {}):
        del state["species"][species_id]
        state["meta"]["species_count"] = len(state["species"])
        write_json_atomic(PUBLISH_STATE_JSON, state)
        return True
    return False


def today_iso():
    import datetime
    return datetime.date.today().isoformat()


_STAMP_RE = None


def strip_page_stamp(html):
    """Remove the visible 'Page updated' footer from a page's HTML.

    Used to compare two renders while ignoring the stamp itself — otherwise
    the comparison is circular: the date differs, so the page differs, so the
    date must change.
    """
    global _STAMP_RE
    if _STAMP_RE is None:
        import re as _re
        _STAMP_RE = _re.compile(r'<div class="page-stamp">.*?</div>\s*', _re.S)
    return _STAMP_RE.sub("", html or "")


def reconcile_page_siblings(directory, psbp_id, filename, dry_run=False):
    """Leave `directory` holding exactly ONE page for psbp_id, named `filename`.

    Call this immediately after writing a species page. `page_filename()`
    derives the name from `common_name`, so any edit to that field changes
    where the next publish writes — and nothing used to clean up what was
    already there. That has bitten three times:

      · PSBP-00355  "Red Lantan" typo fixed -> two pages, both published
      · PSBP-00056  Parrots Beak -> Heliconia, old page orphaned until deleted
      · PSBP-00561  "air potato" -> "Air Potato", which is the nasty one

    THE CASE STEP HAS TO COME FIRST, AND IT IS NOT OPTIONAL.
    macOS is case-insensitive: writing PSBP-00561-Air-Potato.html when
    PSBP-00561-air-potato.html exists opens THAT file and keeps its old name.
    So the page you just wrote is sitting under the wrong name, the index
    points at the right one, and every local check passes because .exists()
    says yes. GitHub Pages is case-sensitive, so it 404s for real visitors —
    which it did, for five days, invisibly.

    Sweeping siblings before fixing the case would therefore delete the page
    that was just written. Rename first, then sweep what is genuinely left.

    Returns (renamed_from | None, [removed_filenames]).
    """
    d = Path(directory)
    if not d.is_dir():
        return None, []
    # 5-digit ids mean "PSBP-00056-" can never prefix another record's pages.
    prefix = f"{psbp_id}-"
    siblings = [f for f in os.listdir(d)
                if f.startswith(prefix) and f.endswith(".html")]

    # 1. Case. os.listdir() returns the real stored names, so membership here is
    #    genuinely case-sensitive — unlike Path.exists(), which is not.
    renamed_from = None
    if filename not in siblings:
        variant = next((f for f in siblings if f.lower() == filename.lower()), None)
        if variant:
            renamed_from = variant
            if not dry_run:
                # Via a temp name: a direct rename between two spellings of the
                # same name is a no-op on a case-insensitive filesystem.
                tmp = d / f".{psbp_id}.case-rename.tmp"
                (d / variant).rename(tmp)
                tmp.rename(d / filename)
            siblings = [filename if f == variant else f for f in siblings]

    # 2. Genuine leftovers from a real rename. Safe to delete: pages are
    #    derived artifacts, regenerable from the master, and in git.
    removed = []
    for f in siblings:
        if f == filename:
            continue
        removed.append(f)
        if not dry_run:
            try:
                (d / f).unlink()
            except OSError:
                pass
    return renamed_from, removed


def page_content_changed(path, new_html):
    """Did anything a visitor can see actually change?

    This — not the input hash — decides whether last_published moves. The hash
    covers every field of every input record, including plenty that never
    reach the page (used_by, publish_ok, virtual, last_reviewed, taxon ids),
    and the generator fingerprint covers the whole publisher file, comments and
    CLI code included. Dating a page from those would inflate freshness: the
    footer would advance when nothing a reader can see had moved.

    So the stamp answers the honest question — is the rendered output
    different? — by comparing renders with the stamp stripped out.

    The hashes are still stored: they let audit_psbp.py answer "were the
    inputs different" without rendering anything. That check is deliberately
    conservative and will occasionally flag a page whose visible output is
    fine. Regeneration is idempotent, so the cost of that is nil, and
    psbp_page_drift.py remains the exact byte-level answer.
    """
    try:
        old = Path(path).read_text(encoding="utf-8")
    except Exception:                                          # noqa: BLE001
        return True                       # no page yet — treat as changed
    return strip_page_stamp(old) != strip_page_stamp(new_html)


def fmt_published(date_str):
    """'2026-07-24' -> 'July 24, 2026' for the page footer."""
    if not date_str:
        return ""
    try:
        from datetime import datetime as _dt
        return _dt.strptime(date_str[:10], "%Y-%m-%d").strftime("%B %-d, %Y")
    except (ValueError, TypeError):
        return ""
