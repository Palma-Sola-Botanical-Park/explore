#!/usr/bin/env python3
"""
crawl_psbp.py — filesystem reality check for the PSBP repo.

audit_psbp.py reads the JSONs and checks the pages it expects to find.
This one walks the disk first and asks the opposite question: what is
actually here, and does anything explain it?

Read-only unless you pass --fix, which only ever DELETES files it has
classified as SAFE_DELETE, and even then only after --yes.

    python3 data/scripts/crawl_psbp.py
    python3 data/scripts/crawl_psbp.py --verbose
    python3 data/scripts/crawl_psbp.py --fix --yes      # actually delete

Every orphan gets a verdict:

  SAFE_DELETE   demoted species — the page is a leftover, nothing links it
  REGENERATE    published species whose page is stale or missing photos
  INVESTIGATE   on disk, explained by nothing — look before you touch
"""

import argparse
import json
import os
import re
import sys
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve()
REPO = None
for cand in list(HERE.parents):
    if (cand / "data" / "sources" / "photo_credits.json").is_file():
        REPO = cand
        break
if REPO is None:
    REPO = Path(os.environ.get("PSBP_REPO", ".")).resolve()

SOURCES       = REPO / "data" / "sources"
PHOTO_CREDITS = SOURCES / "photo_credits.json"
PLANT_SIGNAGE = SOURCES / "plant_signage.json"
WILD_SIGNAGE  = SOURCES / "wildlife_signage.json"
RESEARCH      = SOURCES / "research.json"
WORKBENCH     = SOURCES / "photo_workbench.json"
PLANTS_JSON   = REPO / "plants.json"
WILDLIFE_JSON = REPO / "wildlife.json"
PLANTS_DIR    = REPO / "plants"
WILDLIFE_DIR  = REPO / "wildlife"
PHOTOS_DIR    = REPO / "photos"

HERO_HARD_KB = 500          # matches the pre-commit guard in species_manager
PSBP_RE = re.compile(r"(PSBP-\d{5})")

verdicts = defaultdict(list)     # verdict -> [(path, why)]
notes = []


def load(path, default=None):
    p = Path(path)
    if not p.is_file():
        return default
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:                                   # noqa: BLE001
        notes.append(f"!! {p.name} unreadable: {e}")
        return default


def verdict(kind, path, why):
    try:
        shown = path.relative_to(REPO)
    except ValueError:
        shown = path
    verdicts[kind].append((shown, why))


def human(n):
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f}{unit}" if unit == "B" else f"{n/1:.0f}{unit}"
        n /= 1024
    return f"{n:.0f}GB"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--verbose", action="store_true")
    ap.add_argument("--fix", action="store_true",
                    help="delete SAFE_DELETE files (requires --yes)")
    ap.add_argument("--yes", action="store_true")
    args = ap.parse_args()

    credits  = load(PHOTO_CREDITS, {"photos": []}) or {"photos": []}
    photos   = credits.get("photos", [])
    plants   = (load(PLANT_SIGNAGE, {}) or {}).get("species", [])
    wild     = (load(WILD_SIGNAGE, {}) or {}).get("species", [])
    research = (load(RESEARCH, {}) or {}).get("species", [])
    wb       = (load(WORKBENCH, {}) or {}).get("decisions", {})

    sign_by_id = {s["id"]: s for s in plants + wild}
    res_by_id  = {s["id"]: s for s in research}
    heroes     = {p["psbp_id"]: p for p in photos if p.get("hero")}
    by_species = defaultdict(list)
    for p in photos:
        by_species[p.get("psbp_id")].append(p)

    index_ids = set()
    for path in (PLANTS_JSON, WILDLIFE_JSON):
        for e in (load(path, []) or []):
            if e.get("id"):
                index_ids.add(e["id"])

    # ── 1. every file under plants/ and wildlife/ ────────────────────────
    print("=" * 74)
    print("PAGES")
    print("=" * 74)
    page_count = 0
    for corpus, d in (("plants", PLANTS_DIR), ("wildlife", WILDLIFE_DIR)):
        if not d.is_dir():
            notes.append(f"!! {d} does not exist")
            continue
        for f in sorted(d.rglob("*")):
            if f.is_dir():
                continue
            page_count += 1
            if f.suffix.lower() != ".html":
                verdict("INVESTIGATE", f,
                        f"non-HTML file sitting in {corpus}/")
                continue
            m = PSBP_RE.match(f.name)
            if not m:
                verdict("INVESTIGATE", f,
                        f"HTML in {corpus}/ with no PSBP id in the filename")
                continue
            sid = m.group(1)
            sign = sign_by_id.get(sid)

            if sign is None:
                if sid in res_by_id:
                    verdict("SAFE_DELETE", f,
                            f"{res_by_id[sid].get('common_name')} was demoted to "
                            f"research (status="
                            f"{res_by_id[sid].get('status')}) — page is a leftover")
                else:
                    verdict("INVESTIGATE", f,
                            f"{sid} is in no signage master and no research file")
                continue

            if sign.get("status") != "html":
                verdict("SAFE_DELETE", f,
                        f"{sign.get('common_name')} has status="
                        f"{sign.get('status')!r} — page should have been removed")
                continue

            # published: is the filename the one the publisher would write?
            expected_stub = f"{sid}-"
            if not f.name.startswith(expected_stub):
                verdict("INVESTIGATE", f, "filename does not start with its id")

            if sid not in index_ids:
                verdict("REGENERATE", f,
                        f"{sign.get('common_name')} is published and has a page, but "
                        f"is absent from plants.json/wildlife.json — invisible to "
                        f"site search")

            # duplicate pages for one species (renamed common_name leaves both)
            siblings = [x for x in d.glob(f"{sid}-*.html")]
            if len(siblings) > 1:
                verdict("INVESTIGATE", f,
                        f"{len(siblings)} pages exist for {sid}: "
                        f"{sorted(x.name for x in siblings)} — a rename probably "
                        f"left the old one behind")

    print(f"  walked {page_count} file(s) under plants/ + wildlife/")

    # ── 2. photos/ ──────────────────────────────────────────────────────
    print()
    print("=" * 74)
    print("PHOTOS DIRECTORY")
    print("=" * 74)
    total_bytes = 0
    dir_count = 0
    if not PHOTOS_DIR.is_dir():
        notes.append(f"!! {PHOTOS_DIR} does not exist")
    else:
        for d in sorted(PHOTOS_DIR.iterdir()):
            if d.is_file():
                verdict("INVESTIGATE", d, "loose file at the top of photos/")
                continue
            if not d.is_dir():
                continue
            dir_count += 1
            sid = d.name
            files = [x for x in d.iterdir() if x.is_file()
                     and not x.name.startswith(".")]
            hero = heroes.get(sid)

            if not files:
                verdict("SAFE_DELETE", d, "empty directory")
                continue

            for x in files:
                total_bytes += x.stat().st_size

            if hero is None:
                if sid in res_by_id:
                    verdict("INVESTIGATE", d,
                            f"{res_by_id[sid].get('common_name')} is in research "
                            f"with no hero record, but {len(files)} file(s) remain")
                else:
                    verdict("INVESTIGATE", d,
                            f"{len(files)} file(s) but no hero record for {sid}")
                continue

            want = hero.get("filename", "")
            for x in files:
                if x.name != want:
                    verdict("SAFE_DELETE", x,
                            f"not the current hero ({want}) — leftover from a swap")
                    continue
                kb = x.stat().st_size / 1024
                if kb > HERO_HARD_KB:
                    verdict("INVESTIGATE", x,
                            f"{kb:.0f}KB exceeds the {HERO_HARD_KB}KB commit guard")

            if not (d / want).is_file():
                verdict("REGENERATE", d,
                        f"hero record points at {want}, which is not here")

    print(f"  {dir_count} species folder(s), {total_bytes/1048576:.1f} MB total")

    # ── 3. photos referenced by pages but absent from credits ───────────
    print()
    print("=" * 74)
    print("PAGE <-> REGISTRY DRIFT")
    print("=" * 74)
    inat_re = re.compile(r"/photos/(\d+)/")
    drift = 0
    for corpus, d, in (("plants", PLANTS_DIR), ("wildlife", WILDLIFE_DIR)):
        if not d.is_dir():
            continue
        for f in sorted(d.glob("PSBP-*.html")):
            sid = PSBP_RE.match(f.name).group(1)
            if sid not in sign_by_id:
                continue
            html = f.read_text(encoding="utf-8", errors="replace")
            used = set(inat_re.findall(html))
            known = {str(p.get("photo_id")) for p in by_species.get(sid, [])}
            for missing in sorted(used - known):
                drift += 1
                d_ = wb.get(missing) or {}
                if d_.get("decision") == "skip":
                    why = (f"trashed {d_.get('reviewed_on')} "
                           f"({d_.get('note') or 'no note'}) but the page was never "
                           f"regenerated")
                elif d_.get("decision"):
                    why = (f"workbench says {d_['decision']} for "
                           f"{d_.get('psbp_id')} — not in credits though")
                else:
                    why = "no workbench record at all — removal path unknown"
                verdict("REGENERATE", f, f"renders photo {missing}: {why}")
    print(f"  {drift} photo reference(s) with no registry record")

    # ── 4. workbench strays ─────────────────────────────────────────────
    live = {str(p.get("photo_id")) for p in photos}
    strays = [(k, v) for k, v in wb.items()
              if v.get("decision") == "promoted" and k not in live]
    if strays:
        print()
        print("=" * 74)
        print("WORKBENCH STRAYS")
        print("=" * 74)
        print(f"  {len(strays)} photo(s) marked promoted but missing from credits —")
        print("  these left via a path that writes no skip verdict:")
        for k, v in sorted(strays)[:20]:
            print(f"    {k}  ->  {v.get('psbp_id')}  promoted {v.get('reviewed_on')}")

    # ── report ──────────────────────────────────────────────────────────
    print()
    print("=" * 74)
    print(f"VERDICTS  (repo: {REPO})")
    print("=" * 74)
    for kind in ("SAFE_DELETE", "REGENERATE", "INVESTIGATE"):
        rows = verdicts[kind]
        print(f"\n  {kind}  ({len(rows)})")
        if not rows:
            continue
        limit = len(rows) if args.verbose else 20
        for path, why in rows[:limit]:
            print(f"    {path}")
            print(f"        {why}")
        if len(rows) > limit:
            print(f"    … {len(rows) - limit} more (--verbose to see all)")

    for n in notes:
        print(f"\n  {n}")

    if verdicts["REGENERATE"]:
        ids = sorted({PSBP_RE.search(str(p)).group(1)
                      for p, _ in verdicts["REGENERATE"]
                      if PSBP_RE.search(str(p))})
        print(f"\n  To fix REGENERATE, re-promote: {' '.join(ids)}")

    # ── optional deletion ───────────────────────────────────────────────
    if args.fix:
        targets = [p for p, _ in verdicts["SAFE_DELETE"]]
        if not targets:
            print("\n  nothing to delete")
        elif not args.yes:
            print(f"\n  --fix would delete {len(targets)} path(s); "
                  f"add --yes to actually do it")
        else:
            for rel in targets:
                p = REPO / rel
                try:
                    if p.is_dir():
                        p.rmdir()
                    else:
                        p.unlink()
                    print(f"  deleted {rel}")
                except Exception as e:                        # noqa: BLE001
                    print(f"  FAILED {rel}: {e}")

    return 1 if verdicts["INVESTIGATE"] else 0


if __name__ == "__main__":
    sys.exit(main())
