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
  HTML       what the published pages actually reference   <-- the important one
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
PHENOLOGY      = SOURCES / "phenology.json"
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
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", action="append", default=None,
                    help="run only these sections (repeatable)")
    ap.add_argument("--quiet", action="store_true", help="summary table only")
    ap.add_argument("--max", type=int, default=15,
                    help="max examples printed per finding group")
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
        for sid in sorted(pub_ids - set(gallery)):
            h = heroes.get(sid)
            add("LINK", "WARN",
                f"{sid} {sign_by_id[sid].get('common_name')}: published with no "
                f"gallery-role photo (hero role={h.get('role') if h else None}) — "
                f"the Photo Credits block may be the only attribution on the page")
        stray = sorted(set(by_species) & sign_ids - pub_ids)
        for sid in stray:
            add("LINK", "INFO",
                f"{sid}: photos on file but status="
                f"{sign_by_id[sid].get('status')!r} (not published)")

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

    # ── HTML  (the one that answers 'is every photo in the JSON?') ────────
    if run("HTML"):
        img_re   = re.compile(r'<img[^>]+src="([^"]+)"', re.I)
        href_re  = re.compile(r'href="(\.\./photos/[^"]+)"')
        lbdata_re = re.compile(r'var\s+lbData\s*=\s*(\[.*?\]);', re.S)
        inat_re  = re.compile(r'/photos/(\d+)/')

        for corpus, d, ids in (("plants", PLANTS_DIR, plant_ids),
                               ("wildlife", WILDLIFE_DIR, wild_ids)):
            if not d.is_dir():
                add("HTML", "ERROR", f"{d} does not exist")
                continue
            seen_pages = set()
            for f in sorted(d.glob("PSBP-*.html")):
                m = re.match(r"(PSBP-\d{5})", f.name)
                if not m:
                    add("HTML", "ERROR", f"{f.name}: cannot parse a PSBP id")
                    continue
                sid = m.group(1)
                seen_pages.add(sid)
                html = f.read_text(encoding="utf-8", errors="replace")

                if sid not in ids:
                    add("HTML", "ERROR",
                        f"{f.name}: page exists but {sid} is not in the "
                        f"{corpus} signage master")
                    continue
                if sign_by_id[sid].get("status") != "html":
                    add("HTML", "ERROR",
                        f"{f.name}: page exists but status="
                        f"{sign_by_id[sid].get('status')!r} — should have been deleted "
                        f"on demotion")

                registered = {str(p.get("photo_id")) for p in by_species.get(sid, [])}
                local_ok = set()
                h = heroes.get(sid)
                if h:
                    local_ok.add(f"../photos/{sid}/{h['filename']}")

                # every image reference on the page
                refs = set(img_re.findall(html)) | set(href_re.findall(html))
                lb = lbdata_re.search(html)
                if lb:
                    try:
                        refs |= {d_["src"] for d_ in json.loads(lb.group(1))
                                 if isinstance(d_, dict) and d_.get("src")}
                    except Exception:                          # noqa: BLE001
                        add("HTML", "WARN", f"{f.name}: lbData did not parse as JSON")

                used_ids = set()
                for ref in refs:
                    if ref.startswith("data:") or ref.startswith("#"):
                        continue
                    if ref.startswith("../photos/") or ref.startswith("photos/"):
                        if ref not in local_ok:
                            add("HTML", "ERROR",
                                f"{f.name}: local image {ref} does not match the hero "
                                f"record in photo_credits.json")
                        continue
                    im = inat_re.search(ref)
                    if not im:
                        continue                     # logo/icon/etc, not a photo
                    used_ids.add(im.group(1))

                orphan_use = used_ids - registered
                for oid in sorted(orphan_use):
                    add("HTML", "ERROR",
                        f"{f.name}: renders iNat photo {oid} with NO record in "
                        f"photo_credits.json — uncredited use, regenerate the page")

                expected = {str(p.get("photo_id")) for p in gallery.get(sid, [])
                            if not p.get("hero")}
                stale = expected - used_ids
                for sidp in sorted(stale):
                    add("HTML", "WARN",
                        f"{f.name}: photo {sidp} is gallery-role in the JSON but does "
                        f"not appear on the page — page is stale, re-promote")

                # credit names actually printed on the page
                for p in by_species.get(sid, []):
                    if str(p.get("photo_id")) not in used_ids and not p.get("hero"):
                        continue
                    want = display_name(names, p.get("photographer"),
                                        p.get("photographer_name", ""))
                    if want not in html:
                        add("HTML", "WARN",
                            f"{f.name}: photo {p.get('photo_id')} should be credited "
                            f"to {want!r} but that name is not on the page")

            for sid in sorted(ids):
                if sign_by_id[sid].get("status") == "html" and sid not in seen_pages:
                    add("HTML", "ERROR",
                        f"{sid} {sign_by_id[sid].get('common_name')}: status=html but "
                        f"no page in {corpus}/")

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
        for x in pl:
            sid = x.get("species_id")
            if not sid or sid in sign_ids:
                continue
            if sid in res_by_id:
                add("FK", "INFO",
                    f"placements {x.get('placement_id')} {x.get('common_name')}: "
                    f"species is in research (status="
                    f"{res_by_id[sid].get('status')!r}), not yet in signage")
            else:
                add("FK", "ERROR",
                    f"placements {x.get('placement_id')}: species_id {sid} is in "
                    f"no signage master and no research file")
        placed = {x.get("species_id") for x in pl}
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
        for s in plants + wild + research:
            name = (s.get("botanical_name") or s.get("scientific_name") or "").strip().lower()
            if name:
                sci[name].append((s["id"], s.get("status")))
            if s.get("inat_taxon_id"):
                tax[s["inat_taxon_id"]].append(s["id"])
        for name, recs in sci.items():
            if len(recs) > 1:
                add("TAXA", "WARN",
                    f"{name!r} appears {len(recs)}x: "
                    f"{', '.join(f'{i} ({st})' for i, st in recs)}")
        for tid, recs in tax.items():
            if len(recs) > 1:
                add("TAXA", "WARN",
                    f"inat_taxon_id {tid} shared by {recs}")

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
        declared_sc = rmeta.get("status_counts")
        actual_sc = dict(Counter(s.get("status") for s in research))
        if declared_sc and declared_sc != actual_sc:
            add("META", "WARN",
                f"research.json: meta.status_counts={declared_sc} but actual="
                f"{actual_sc}")
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

    # ── report ────────────────────────────────────────────────────────────
    order = ["PHOTOS", "CREDITS", "LINK", "DISK", "HTML", "INDEX", "FK", "TAXA", "META"]
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
