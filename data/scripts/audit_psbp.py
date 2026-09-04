#!/usr/bin/env python3
"""
audit_psbp.py — read-only integrity audit for the PSBP species pipeline.

Writes nothing. Touches nothing. Run it from anywhere in the repo:

    python3 data/scripts/audit_psbp.py
    python3 data/scripts/audit_psbp.py --only PHOTOS
    python3 data/scripts/audit_psbp.py --quiet      # summary table only

Sections
--------
  PHOTOS     photo_credits.json internal consistency
  CREDITS    photographer name / credit_line drift
  LINK       photo_credits <-> signage cross-references
  DISK       hero files that should exist on disk
  PUBLISH    published pages vs. the data they were built from
  INDEX      plants.json / wildlife.json hero paths
  FK         placements / phenology / workbench foreign keys
  TAXA       duplicate species across signage + research
  META       meta counters that have drifted from reality

Every finding is one of:
  ERROR  something a visitor could see, or data loss waiting to happen
  WARN   drift that will bite on the next regen
  INFO   worth knowing, not necessarily wrong
"""

import argparse
import json
import os
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

# ── locate the repo ─────────────────────────────────────────────────────────
HERE = Path(__file__).resolve()
REPO = None
for cand in [HERE.parents[2]] + list(HERE.parents):
    if (cand / "data" / "sources" / "photo_credits.json").is_file():
        REPO = cand
        break
if REPO is None:
    REPO = Path(os.environ.get("PSBP_REPO", ".")).resolve()

SOURCES        = REPO / "data" / "sources"
PHOTO_CREDITS  = SOURCES / "photo_credits.json"
PLANT_SIGNAGE  = SOURCES / "plant_signage.json"
WILD_SIGNAGE   = SOURCES / "wildlife_signage.json"
PHOTOG_NAMES   = SOURCES / "photographer_names.json"
WORKBENCH      = SOURCES / "photo_workbench.json"
RESEARCH       = SOURCES / "research.json"
PLACEMENTS     = SOURCES / "placements.json"
LANDMARKS      = REPO / "data" / "sources" / "landmarks.json"
PHENOLOGY      = SOURCES / "phenology.json"
PUBLISH_STATE  = SOURCES / "publish_state.json"
PLANTS_JSON    = REPO / "plants.json"
WILDLIFE_JSON  = REPO / "wildlife.json"
PLANTS_DIR     = REPO / "plants"
WILDLIFE_DIR   = REPO / "wildlife"
PHOTOS_DIR     = REPO / "photos"

CC_LICENSES = {"CC-BY", "CC-BY-NC", "CC-BY-SA", "CC-BY-NC-SA",
               "CC-BY-ND", "CC-BY-NC-ND", "CC0"}
# Licenses that forbid derivative works — relevant if heroes are resized/cropped.
ND_LICENSES = {"CC-BY-ND", "CC-BY-NC-ND"}

PHOTO_FIELDS = {
    "psbp_id", "type", "common_name", "scientific_name", "role", "primary_for",
    "hero", "tags", "photographer", "license", "publish_ok", "status",
    "credit_line", "photo_url", "source_url", "photo_id", "filename",
    "used_by", "focus", "photographer_name", "observed_on", "shared_on",
    "observation_id", "virtual",
}

findings = []       # (section, level, message)
counts = Counter()


def add(section, level, msg):
    findings.append((section, level, msg))
    counts[(section, level)] += 1


def load(path, default=None):
    p = Path(path)
    if not p.is_file():
        return default
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:                                    # noqa: BLE001
        add("META", "ERROR", f"{p.name}: unreadable ({e})")
        return default


# ── credit resolution (mirrors psbp_common, kept local so the audit
#    can never be fooled by a bug in the module it is auditing) ─────────────
def display_name(names, login, raw=""):
    entry = names.get((login or "").lower())
    if isinstance(entry, dict) and entry.get("display_name"):
        return entry["display_name"]
    if isinstance(entry, str):
        return entry
    return (raw or "").strip() or (login or "unknown")


def build_credit_line(name, lic):
    lic = (lic or "").strip().upper()
    if lic and lic != "NAN":
        return f"\u00a9 {name} ({lic}), via iNaturalist"
    return f"\u00a9 {name}, via iNaturalist"


# ═══════════════════════════════════════════════════════════════════════════
# ── publish-state fingerprints ──────────────────────────────────────────────
# MIRRORS psbp_common.compute_input_hash / generator_fingerprint. Kept local so
# this audit stays dependency-free and can run against a half-broken repo.
#
# If either function changes in psbp_common, bump HASH_VERSION there. This
# audit checks the version recorded in publish_state.json and refuses to
# compare when it doesn't recognise it — a drifted copy reports "cannot
# compare" instead of confidently wrong staleness.
HASH_VERSION = 1


def _canonical(obj):
    return json.dumps(obj, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, default=str)


def _input_hash(species, hero, gallery_photos):
    """Fingerprint of the inputs that produce a page. Gallery rows are sorted
    by photo_id so registry reordering isn't mistaken for a content change."""
    import hashlib
    gal = sorted((p for p in (gallery_photos or [])),
                 key=lambda p: str(p.get("photo_id", "")))
    payload = _canonical({"species": species, "hero": hero, "gallery": gal})
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def _generator_fingerprint(corpus):
    """Hash of the publisher module source, read as a plain file (no import)."""
    import hashlib
    name = "plant_publisher.py" if corpus == "plants" else "wildlife_publisher.py"
    try:
        return hashlib.sha256((HERE.parent / name).read_bytes()).hexdigest()[:16]
    except Exception:                                          # noqa: BLE001
        return ""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", action="append", default=None,
                    help="run only these sections (repeatable)")
    ap.add_argument("--quiet", action="store_true", help="summary table only")
    ap.add_argument("--max", type=int, default=15,
                    help="max examples printed per finding group")
    ap.add_argument("--json", action="store_true",
                    help="emit JSON instead of a report (feeds the health page)")
    args = ap.parse_args()
    only = {s.upper() for s in args.only} if args.only else None

    def run(section):
        return only is None or section in only

    credits = load(PHOTO_CREDITS, {"photos": []}) or {"photos": []}
    photos  = credits.get("photos", [])
    names   = load(PHOTOG_NAMES, {}) or {}
    plants  = (load(PLANT_SIGNAGE, {}) or {}).get("species", [])
    wild    = (load(WILD_SIGNAGE, {}) or {}).get("species", [])
    research = (load(RESEARCH, {}) or {}).get("species", [])

    plant_ids = {s["id"] for s in plants}
    wild_ids  = {s["id"] for s in wild}
    sign_ids  = plant_ids | wild_ids
    res_ids   = {s["id"] for s in research}
    sign_by_id = {s["id"]: s for s in plants + wild}
    pub_ids   = {s["id"] for s in plants + wild if s.get("status") == "html"}

    by_species = defaultdict(list)
    for p in photos:
        by_species[p.get("psbp_id")].append(p)
    heroes  = {p["psbp_id"]: p for p in photos if p.get("hero")}
    gallery = defaultdict(list)
    for p in photos:
        if "gallery" in (p.get("role") or []):
            gallery[p["psbp_id"]].append(p)

    # ── CONTENT ───────────────────────────────────────────────────────────
    # The teaser is printed on the SIGN (make_signs.py, "the hook, under the
    # photo") and shown in the nature.html drawer. quick_hits[0] opens the page
    # the QR code leads to. So a visitor reads the teaser, scans, and lands on
    # the first quick hit — and on 38 records in Aug 2026 those were the same
    # sentence. The scan should reward them, not repeat the placard.
    #
    # The rule (Medium #5 part 4): teaser and quick_hits[0] must carry DIFFERENT
    # facts. Measured as Jaccard word overlap; 0.50 flags the tail without
    # firing on the many records that legitimately share a few common words.
    if run("CONTENT"):
        def _words(t):
            return set(re.sub(r"[^a-z0-9 ]", " ", (t or "").lower()).split())

        # A published PLANT with no teaser ships a sign with no hook and an
        # empty drawer on nature.html.
        #
        # PLANTS ONLY — FOR NOW. Randy, 2026-09-01: "teaser originated with
        # signage. Wildlife will have no sign. Then we decided to use it in
        # index." The field was born as sign copy and only later took a second
        # job in the browse index. Wildlife is never signed, so it never grew
        # one: wildlife_publisher.py has no teaser, and wildlife.json has no
        # teaser key.
        #
        # ⚠ THIS IS A "NOT YET", NOT A "NEVER". Randy, same day: "soon wildlife
        #   will get a new published page, and a new index with a quick view and
        #   the quick view needs a good short blurb." That is Low #6 — the
        #   wildlife publisher rework. When it lands, wildlife needs a short
        #   blurb of its own and this check should cover it.
        #
        # Order matters: the publisher and wildlife.json need the field FIRST.
        # Turning this on before then would flag all 91 published animals for
        # lacking something their pipeline cannot yet produce.
        for sp in plants:
            if sp.get("status") != "html":
                continue
            if not (sp.get("teaser") or "").strip():
                add("CONTENT", "WARN",
                    f"{sp['id']} {sp.get('common_name')}: published with no teaser — "
                    f"the sign has no hook and the quick-view drawer opens empty")

        for sp in plants + wild:
            if sp.get("status") != "html":
                continue
            teaser = sp.get("teaser")
            hits = sp.get("quick_hits") or []
            if not isinstance(teaser, str) or not hits or not isinstance(hits[0], str):
                continue
            a, b = _words(teaser), _words(hits[0])
            if not a or not b:
                continue
            j = len(a & b) / len(a | b)
            if j >= 0.50:
                add("CONTENT", "WARN",
                    f"{sp['id']} {sp.get('common_name')}: teaser and quick_hits[0] "
                    f"overlap {j:.0%} — the sign and the page say the same thing")

        # UNITS — imperial, spelled out, rounded. Randy, 2026-09-01: "I prefer
        # rough numbers and imperial. Instead of 18, use almost 20."
        #
        # The corpus already voted: 976 uses of "feet" against 67 of "meters"
        # before the 2026-09-01 pass. What that pass fixed was 102 metric-only
        # figures a Florida visitor could not read ("berries up to 1.7 cm"),
        # 23 metric parentheticals, and converted figures carrying false
        # precision — "49 to 66 feet" is a arithmetic conversion of a round
        # 15-to-20-metre estimate, and implies a survey nobody did.
        #
        # internal_notes is EXEMPT: it cites sources, and sources are metric.
        _METRIC = re.compile(r"\b\d[\d.,]*\s?(?:mm|cm|millimet(?:er|re)s?|centimet(?:er|re)s?|met(?:er|re)s?)\b"
                             r"|\b\d\s?m\b(?!\w)", re.I)
        _ABBREV = re.compile(r"(?<=[\d\s])\bft\b|(?<=\d)\s*\bin\.(?=\s+[a-z])")

        def _visible(sp):
            out = []
            def w(v):
                if isinstance(v, str):
                    out.append(v)
                elif isinstance(v, list):
                    for x in v: w(x)
                elif isinstance(v, dict):
                    for k, x in v.items():
                        if k != "internal_notes": w(x)
            w({k: v for k, v in sp.items() if k != "internal_notes"})
            return " ".join(out)

        for sp in plants + wild:
            t = _visible(sp)
            m = _METRIC.search(t)
            if m:
                add("CONTENT", "WARN",
                    f"{sp['id']} {sp.get('common_name')}: metric in visitor prose "
                    f"(\u201c{m.group(0)}\u201d) — convert to imperial and round it")
            a = _ABBREV.search(t)
            if a:
                add("CONTENT", "WARN",
                    f"{sp['id']} {sp.get('common_name')}: abbreviated unit "
                    f"(\u201c{a.group(0).strip()}\u201d) — spell out feet/inches")

    # ── PHOTOS ────────────────────────────────────────────────────────────
    if run("PHOTOS"):
        for p in photos:
            miss = PHOTO_FIELDS - set(p)
            if miss:
                add("PHOTOS", "ERROR",
                    f"{p.get('psbp_id')} / {p.get('photo_id')}: missing field(s) "
                    f"{sorted(miss)}")
            lic = (p.get("license") or "").strip().upper()
            if lic not in CC_LICENSES:
                add("PHOTOS", "ERROR",
                    f"{p.get('psbp_id')} / {p.get('photo_id')}: license "
                    f"{lic!r} is not an accepted CC license")
            elif lic in ND_LICENSES and p.get("hero"):
                add("PHOTOS", "WARN",
                    f"{p['psbp_id']}: hero is {lic} (no-derivatives) — the hero "
                    f"pipeline resizes the file locally; confirm that is acceptable")
            if not p.get("publish_ok"):
                add("PHOTOS", "ERROR",
                    f"{p.get('psbp_id')} / {p.get('photo_id')}: publish_ok is false "
                    f"but the record is still live")
            if p.get("virtual") == p.get("hero"):
                add("PHOTOS", "WARN",
                    f"{p.get('psbp_id')} / {p.get('photo_id')}: virtual="
                    f"{p.get('virtual')} contradicts hero={p.get('hero')} "
                    f"(invariant: virtual == not hero)")
            if not p.get("hero") and "gallery" not in (p.get("role") or []):
                add("PHOTOS", "WARN",
                    f"{p.get('psbp_id')} / {p.get('photo_id')}: neither hero nor "
                    f"gallery — role={p.get('role')} — nothing renders this record")
            pid = p.get("psbp_id")
            exp = "Plant" if pid in plant_ids else "Wildlife" if pid in wild_ids else None
            if exp and p.get("type") != exp:
                add("PHOTOS", "ERROR",
                    f"{pid} / {p.get('photo_id')}: type={p.get('type')!r} but the id "
                    f"lives in the {exp.lower()} signage master")

        for pid, plist in by_species.items():
            h = [x for x in plist if x.get("hero")]
            if len(h) > 1:
                add("PHOTOS", "ERROR",
                    f"{pid}: {len(h)} heroes "
                    f"({', '.join(str(x.get('photo_id')) for x in h)})")

        for key, n in Counter((p.get("psbp_id"), str(p.get("photo_id")))
                              for p in photos).items():
            if n > 1:
                add("PHOTOS", "ERROR", f"{key[0]}: photo_id {key[1]} appears {n}x")
        for pid, n in Counter(str(p.get("photo_id")) for p in photos).items():
            if n > 1:
                owners = sorted({p["psbp_id"] for p in photos
                                 if str(p.get("photo_id")) == pid})
                add("PHOTOS", "WARN",
                    f"photo_id {pid} is credited to {len(owners)} species: {owners}")

    # ── CREDITS ───────────────────────────────────────────────────────────
    if run("CREDITS"):
        unregistered = Counter()
        for p in photos:
            login = (p.get("photographer") or "").lower()
            resolved = display_name(names, login, p.get("photographer_name", ""))
            want = build_credit_line(resolved, p.get("license"))
            if p.get("credit_line") != want:
                add("CREDITS", "ERROR",
                    f"{p.get('psbp_id')} / {p.get('photo_id')}: stored credit_line "
                    f"{p.get('credit_line')!r} != resolved {want!r} — run "
                    f"propagate_photographer_name({login!r}) then re-promote")
            if p.get("photographer_name") != resolved:
                add("CREDITS", "WARN",
                    f"{p.get('psbp_id')} / {p.get('photo_id')}: photographer_name "
                    f"{p.get('photographer_name')!r} != registry {resolved!r}")
            if login and login not in names:
                unregistered[login] += 1
        for login, n in unregistered.most_common():
            add("CREDITS", "INFO",
                f"{login}: {n} photo(s) credited by bare iNat handle — no entry in "
                f"photographer_names.json")

    # ── LINK ──────────────────────────────────────────────────────────────
    if run("LINK"):
        for pid in sorted(set(by_species) - sign_ids):
            where = "in research.json" if pid in res_ids else "NOT FOUND ANYWHERE"
            lvl = "INFO" if pid in res_ids else "ERROR"
            add("LINK", lvl,
                f"{pid}: {len(by_species[pid])} photo record(s) but no signage entry "
                f"({where})")
        for sid in sorted(pub_ids - set(heroes)):
            add("LINK", "ERROR",
                f"{sid} {sign_by_id[sid].get('common_name')}: status=html but no hero "
                f"photo — the page will render a broken image")
        # Two different problems, so two different tests.
        #
        # 1. ONE PHOTO — INFO, not WARN. Counted on the photo records, NOT on
        #    gallery roles: ticking "gallery" on a lone hero used to silence this
        #    even though the record still had a single photo. (Found by Randy on
        #    PSBP-00424, 2026-09-01.) Nothing is broken — the page renders that
        #    photo once as the hero and lists it in the lightbox array, which is
        #    data, not a second image. It is simply a field-work note: this
        #    species has been photographed once.
        # 2. SEVERAL PHOTOS, NONE TAGGED GALLERY. A tagging gap: the pictures
        #    exist, the page just cannot show them.
        for sid in sorted(pub_ids):
            n = len(by_species.get(sid, []))
            name = sign_by_id[sid].get("common_name")
            if n <= 1:
                add("LINK", "INFO", f"{sid} {name}: only one photo published.")
            elif sid not in gallery:
                add("LINK", "WARN",
                    f"{sid} {name}: {n} photos, but none tagged for the gallery.")
        # (Removed 2026-09-01) There used to be an INFO here for species with
        # photos on file that are not yet published. Randy reviews photos before
        # every publish without exception, so it flagged a state that is normal
        # and expected — noise, not signal.

    # ── DISK ──────────────────────────────────────────────────────────────
    if run("DISK"):
        if not PHOTOS_DIR.is_dir():
            add("DISK", "ERROR", f"{PHOTOS_DIR} does not exist — cannot check heroes")
        else:
            on_disk = {d.name for d in PHOTOS_DIR.iterdir() if d.is_dir()}
            for sid in sorted(pub_ids):
                h = heroes.get(sid)
                if not h:
                    continue
                f = PHOTOS_DIR / sid / h.get("filename", "")
                if not f.is_file():
                    add("DISK", "ERROR",
                        f"{sid}: hero file missing on disk — {f.relative_to(REPO)}")
                extra = [x.name for x in (PHOTOS_DIR / sid).glob("*")
                         if (PHOTOS_DIR / sid).is_dir() and x.name != h.get("filename")]
                if extra:
                    add("DISK", "WARN",
                        f"{sid}: stale file(s) alongside the hero: {extra}")
            for d in sorted(on_disk - set(heroes)):
                add("DISK", "WARN",
                    f"photos/{d}/ exists but no hero record points at it")
        declared = set(credits.get("_local_folders") or [])
        if declared:
            add("DISK", "INFO",
                f"_local_folders lists {len(declared)} folder(s) but no script in the "
                f"repo reads or writes it — it is a stale snapshot; consider deleting "
                f"the key or regenerating it at hero-swap time")

    # ── PUBLISH  (was: HTML — see the note below) ─────────────────────────
    #
    # This section used to parse every rendered page: pull image srcs out with
    # a regex, extract iNat photo IDs from those URLs, re-parse the lbData
    # JSON, and grep for photographer names. It answered four questions —
    #
    #   * does the page render a photo with no credit record?  (the Gallinule)
    #   * is a gallery-role photo missing from the page?
    #   * does the local hero image match the registry?
    #   * is every credited name actually printed?
    #
    # — all of which are the SAME question wearing four hats: does this page
    # still match what the generator would produce from the current JSON?
    #
    # Scraping our own output to ask that was always backwards. It was done
    # because nothing recorded what a page had been built from. publish_state
    # .json now does, so comparing two fingerprints answers all four at once —
    # and unlike the regexes, it cannot drift out of sync with the template.
    #
    # The file-level checks below never needed HTML parsing and are unchanged.
    # For byte-exact verification — catching hand-edited pages, which no
    # fingerprint can detect — run psbp_page_drift.py.
    if run("PUBLISH"):
        state = load(PUBLISH_STATE, {}) or {}
        recs = state.get("species", {}) if isinstance(state, dict) else {}
        ver = (state.get("meta", {}) or {}).get("hash_version")

        can_compare = True
        if not recs:
            add("PUBLISH", "INFO",
                "publish_state.json is empty or absent — run "
                "psbp_seed_publish_state.py, then regenerate. Staleness cannot "
                "be computed until pages record what they were built from.")
            can_compare = False
        elif ver != HASH_VERSION:
            add("PUBLISH", "INFO",
                f"publish_state.json uses hash_version {ver!r}, this audit "
                f"understands {HASH_VERSION}. Declining to compare rather than "
                f"reporting wrong answers.")
            can_compare = False

        for corpus, d, ids in (("plants", PLANTS_DIR, plant_ids),
                               ("wildlife", WILDLIFE_DIR, wild_ids)):
            if not d.is_dir():
                add("PUBLISH", "ERROR", f"{d} does not exist")
                continue

            # ── file-level checks (no HTML is read) ──
            seen_pages = set()
            for f in sorted(d.glob("PSBP-*.html")):
                m = re.match(r"(PSBP-\d{5})", f.name)
                if not m:
                    add("PUBLISH", "ERROR", f"{f.name}: cannot parse a PSBP id")
                    continue
                sid = m.group(1)
                seen_pages.add(sid)
                if sid not in ids:
                    add("PUBLISH", "ERROR",
                        f"{f.name}: page exists but {sid} is not in the "
                        f"{corpus} signage master")
                    continue
                if sign_by_id[sid].get("status") != "html":
                    add("PUBLISH", "ERROR",
                        f"{f.name}: page exists but status="
                        f"{sign_by_id[sid].get('status')!r} — should have been "
                        f"deleted on demotion")

            for sid in sorted(ids):
                if sign_by_id[sid].get("status") == "html" and sid not in seen_pages:
                    add("PUBLISH", "ERROR",
                        f"{sid} {sign_by_id[sid].get('common_name')}: status=html "
                        f"but no page in {corpus}/")

            if not can_compare:
                continue

            # ── fingerprint comparison (replaces all the scraping) ──
            generator = _generator_fingerprint(corpus)
            old_generator = 0
            for sid in sorted(ids):
                sp = sign_by_id[sid]
                if sp.get("status") != "html":
                    if sid in recs:
                        add("PUBLISH", "WARN",
                            f"{sid} {sp.get('common_name')}: status="
                            f"{sp.get('status')!r} but still has a publish record")
                    continue

                rec = recs.get(sid)
                if not rec:
                    add("PUBLISH", "WARN",
                        f"{sid} {sp.get('common_name')}: published page with no "
                        f"publish record — regenerate to stamp it")
                    continue

                hero = heroes.get(sid)
                if not hero:
                    continue          # already reported by DISK / LINK
                want = _input_hash(sp, hero, gallery.get(sid, []))

                if rec.get("input_hash") != want:
                    add("PUBLISH", "ERROR",
                        f"{sid} {sp.get('common_name')}: page was built from "
                        f"different data than the JSON now holds — STALE, "
                        f"regenerate (last published "
                        f"{rec.get('last_published', '?')})")
                elif generator and rec.get("generator") != generator:
                    # Counted, not listed. The publisher file changing does NOT
                    # mean the OUTPUT changed — a comment or a CLI tweak moves
                    # this fingerprint while every page renders identically.
                    # One aggregate line, and psbp_page_drift.py gives the
                    # exact answer.
                    old_generator += 1

            if old_generator:
                add("PUBLISH", "INFO",
                    f"{old_generator} {corpus} page(s) were published by an "
                    f"older {corpus} publisher. That often means nothing — "
                    f"comments and CLI edits move this fingerprint without "
                    f"changing any page. Run psbp_page_drift.py for the exact "
                    f"answer, and regenerate if it reports drift.")

    # ── INDEX ─────────────────────────────────────────────────────────────
    if run("INDEX"):
        for label, path, ids in (("plants.json", PLANTS_JSON, plant_ids),
                                 ("wildlife.json", WILDLIFE_JSON, wild_ids)):
            idx = load(path)
            if idx is None:
                add("INDEX", "ERROR", f"{label} not found")
                continue
            seen = set()
            for e in idx:
                sid = e.get("id")
                seen.add(sid)
                if sid not in ids:
                    add("INDEX", "ERROR", f"{label}: {sid} not in the signage master")
                    continue
                if sign_by_id[sid].get("status") != "html":
                    add("INDEX", "ERROR",
                        f"{label}: {sid} is listed but status="
                        f"{sign_by_id[sid].get('status')!r}")
                h = heroes.get(sid)
                if h:
                    want = f"photos/{sid}/{h['filename']}"
                    if e.get("photo") != want:
                        add("INDEX", "ERROR",
                            f"{label}: {sid} photo={e.get('photo')!r} expected {want!r}")
            for sid in sorted({s for s in ids
                               if sign_by_id[s].get("status") == "html"} - seen):
                add("INDEX", "ERROR", f"{label}: {sid} is published but missing")

    # ── FK ────────────────────────────────────────────────────────────────
    if run("FK"):
        res_by_id = {s["id"]: s for s in research}
        pl = (load(PLACEMENTS, {}) or {}).get("placements", [])
        # ── schema 3.0 ────────────────────────────────────────────────────
        # placements.json now holds landmarks as well as species, keyed by
        # `subject_id` with `kind` saying which. A landmark row would fire the
        # species FK check on every run, so it is routed to its own check
        # against landmarks.json instead. `species_id` is still read as a
        # fallback so a stale file audits rather than crashes.
        lm_raw = load(LANDMARKS, [])
        lm_ids = {r.get("id") for r in
                  (lm_raw if isinstance(lm_raw, list)
                   else (lm_raw or {}).get("landmarks", []))}
        for x in pl:
            sid = x.get("subject_id", x.get("species_id"))
            if (x.get("kind") or "species") != "species":
                if not sid:
                    add("FK", "ERROR",
                        f"placements {x.get('placement_id')}: no subject_id")
                elif lm_ids and sid not in lm_ids:
                    add("FK", "ERROR",
                        f"placements {x.get('placement_id')}: {sid} is not in "
                        f"landmarks.json")
                continue
            if not sid or sid in sign_ids:
                continue
            if sid in res_by_id:
                add("FK", "INFO",
                    f"placements {x.get('placement_id')} {x.get('subject_id')}: "
                    f"species is in research (status="
                    f"{res_by_id[sid].get('status')!r}), not yet in signage")
            else:
                add("FK", "ERROR",
                    f"placements {x.get('placement_id')}: subject_id {sid} is in "
                    f"no signage master and no research file")
        placed = {x.get("subject_id", x.get("species_id")) for x in pl}
        gap = len({s for s in plant_ids
                   if sign_by_id[s].get("status") == "html"} - placed)
        if gap:
            add("FK", "INFO",
                f"{gap} published plant(s) have no placement row — expected if "
                f"signs are still being surveyed")

        ph = (load(PHENOLOGY, {}) or {}).get("observations", {})
        bad = {v.get("psbp_id") for v in ph.values()
               if v.get("psbp_id") and v["psbp_id"] not in sign_ids | res_ids}
        for b in sorted(bad):
            add("FK", "ERROR", f"phenology: psbp_id {b} is unknown")

        wb = (load(WORKBENCH, {}) or {}).get("decisions", {})
        live = {str(p.get("photo_id")) for p in photos}
        for k, v in wb.items():
            if v.get("decision") == "skip" and k in live:
                add("FK", "ERROR",
                    f"workbench: photo {k} is marked skip but is still live in "
                    f"photo_credits.json")
            if v.get("decision") == "promoted" and k not in live:
                add("FK", "WARN",
                    f"workbench: photo {k} is marked promoted but is not in "
                    f"photo_credits.json")

    # ── TAXA ──────────────────────────────────────────────────────────────
    if run("TAXA"):
        sci = defaultdict(list)
        tax = defaultdict(list)
        # Track WHICH file each record came from. A duplicate between two
        # research stubs is housekeeping; one between a published page and a
        # research stub is a different problem, and the message should say so.
        for src, group in (("plant_signage", plants),
                           ("wildlife_signage", wild),
                           ("research", research)):
            for s in group:
                who = (s["id"], s.get("common_name") or "?", s.get("status"), src)
                name = (s.get("botanical_name") or s.get("scientific_name") or "").strip().lower()
                if name:
                    sci[name].append(who)
                if s.get("inat_taxon_id"):
                    tax[s["inat_taxon_id"]].append(who)

        def _fmt(recs):
            return "; ".join(f"{i} {nm} ({st} in {src}.json)" for i, nm, st, src in recs)

        for name, recs in sci.items():
            if len(recs) > 1:
                add("TAXA", "WARN", f"{name!r} appears {len(recs)}x — {_fmt(recs)}")
        for tid, recs in tax.items():
            if len(recs) > 1:
                add("TAXA", "WARN",
                    f"inat_taxon_id {tid} used by {len(recs)} records — {_fmt(recs)}")

    # ── META ──────────────────────────────────────────────────────────────
    if run("META"):
        checks = [
            (PHOTO_CREDITS, "photo_count", len(photos)),
            (PLANT_SIGNAGE, "species_count", len(plants)),
            (PLACEMENTS, "placement_count",
             len((load(PLACEMENTS, {}) or {}).get("placements", []))),
            (RESEARCH, "species_count", len(research)),
        ]
        for path, key, actual in checks:
            doc = load(path, {}) or {}
            declared = (doc.get("meta") or {}).get(key)
            if declared is not None and declared != actual:
                add("META", "WARN",
                    f"{Path(path).name}: meta.{key}={declared} but the file holds "
                    f"{actual}")
        rmeta = ((load(RESEARCH, {}) or {}).get("meta") or {})
        # The meta.status_counts check was removed 2026-09-03 with the field it
        # watched. It duplicated a fact the records already carry, drifted when a
        # write path forgot it, and nothing read it — so this check existed only
        # to report on the problem the field created. Don't reinstate either.
        nums = [int(s["id"].split("-")[1]) for s in plants + wild + research
                if re.match(r"PSBP-\d+$", s.get("id", ""))]
        if nums:
            pmax = max(n for n in nums if n < 90000)
            wmin = min((n for n in nums if n >= 90000), default=None)
            add("META", "INFO",
                f"id allocation in fact: plants used up to PSBP-{pmax:05d}; "
                f"wildlife band occupies "
                f"PSBP-{wmin:05d}..PSBP-{max(n for n in nums if n >= 90000):05d}")
            alloc = rmeta.get("id_allocation") or {}
            if alloc:
                add("META", "INFO",
                    f"research.json meta.id_allocation says {alloc} — compare with "
                    f"the line above and correct if it has drifted")

    # ── JSON out ──────────────────────────────────────────────────────────
    # Added 2026-08-28 for the species data-integrity health page (Medium #1).
    # Shape deliberately mirrors psbp_orphan_audit.py --json so one board can
    # render both without a second parser.
    #
    # NOTE: --json ignores --max. The terminal report truncates long groups
    # because a human is reading it; the health page needs every finding, and
    # a silently truncated feed would show "27 photo warnings" next to a list
    # of 15 and look like a bug in the page.
    if args.json:
        order_j = ["PHOTOS", "CREDITS", "CONTENT", "LINK", "DISK", "PUBLISH",
                   "INDEX", "FK", "TAXA", "META"]
        secs = []
        for sec in order_j:
            rows = [(l, m) for s_, l, m in findings if s_ == sec]
            if not rows and not run(sec):
                continue
            secs.append({
                "section": sec,
                "counts": {lvl: sum(1 for l, _ in rows if l == lvl)
                           for lvl in ("ERROR", "WARN", "INFO")},
                "findings": [{"level": l, "message": m} for l, m in rows],
            })
        tot_j = Counter()
        for sec in secs:
            for lvl, n in sec["counts"].items():
                tot_j[lvl] += n
        print(json.dumps({
            "repo": str(REPO),
            "generated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "sections_run": sorted(only) if only else order_j,
            "totals": {lvl: tot_j[lvl] for lvl in ("ERROR", "WARN", "INFO")},
            "sections": secs,
        }, indent=2, ensure_ascii=False))
        return 1 if tot_j["ERROR"] else 0

    # ── report ────────────────────────────────────────────────────────────
    # ⚠ A section missing from this list is collected and silently discarded —
    #   add() fills `findings`, but only sections named here are printed or
    #   counted. CONTENT was added 2026-08-28 and cost twenty minutes of
    #   debugging a check that was working perfectly.
    order = ["PHOTOS", "CREDITS", "CONTENT", "LINK", "DISK", "PUBLISH", "INDEX",
             "FK", "TAXA", "META"]
    if not args.quiet:
        for sec in order:
            rows = [f for f in findings if f[0] == sec]
            if not rows:
                continue
            print(f"\n{'=' * 74}\n{sec}\n{'=' * 74}")
            for lvl in ("ERROR", "WARN", "INFO"):
                group = [m for s, l, m in rows if l == lvl]
                if not group:
                    continue
                print(f"\n  {lvl} ({len(group)})")
                for m in group[:args.max]:
                    print(f"    - {m}")
                if len(group) > args.max:
                    print(f"    … {len(group) - args.max} more")

    print(f"\n{'=' * 74}\nSUMMARY  (repo: {REPO})\n{'=' * 74}")
    print(f"  {'section':<10}{'ERROR':>8}{'WARN':>8}{'INFO':>8}")
    tot = Counter()
    for sec in order:
        e, w, i = (counts[(sec, "ERROR")], counts[(sec, "WARN")], counts[(sec, "INFO")])
        if e or w or i:
            print(f"  {sec:<10}{e:>8}{w:>8}{i:>8}")
        tot["ERROR"] += e; tot["WARN"] += w; tot["INFO"] += i
    print(f"  {'TOTAL':<10}{tot['ERROR']:>8}{tot['WARN']:>8}{tot['INFO']:>8}")
    return 1 if tot["ERROR"] else 0


if __name__ == "__main__":
    sys.exit(main())
