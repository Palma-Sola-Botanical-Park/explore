#!/usr/bin/env python3
"""
psbp_orphan_audit.py — find what demotion left behind.

WHY THIS EXISTS
---------------
species_manager.handle_api_publish_demote_research() removes a species from
signage in six steps: HTML page, search-index card, hero JPG + folder,
research.json append, signage removal, count updates.

It never touches photo_credits.json.

So every research-demotion leaves the demoted species' photo credits in place,
pointing at a species that no longer exists anywhere. This script finds those,
and every other kind of drift between the four things that are supposed to
agree: the signage masters, the credits ledger, the generated pages, and the
photo folders.

DEFAULT IS READ-ONLY. Nothing is written, moved, or deleted unless you pass
--fix, and --fix writes a timestamped backup of every file it changes.

USAGE
-----
    python3 psbp_orphan_audit.py              # report
    python3 psbp_orphan_audit.py --verbose    # report + every affected file
    python3 psbp_orphan_audit.py --json       # machine-readable
    python3 psbp_orphan_audit.py --fix        # repair (asks first)

THE RULE BEING ENFORCED
-----------------------
    No photo may exist in photo_credits.json unless its species carries
    status "html" or "spotted" in plant_signage.json or wildlife_signage.json.

Everything else in here is a corollary of that, or its mirror image: things
that should exist and don't.
"""

import argparse
import json
import os
import shutil
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

# ── Locate the repo ────────────────────────────────────────────────────────
# Prefer psbp_common so there is one source of truth for paths. Fall back to
# walking up from wherever this file sits, so it also runs from a scratch dir.

def find_repo():
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import psbp_common                                    # noqa: F401
        return Path(psbp_common.REPO)
    except Exception:
        here = Path(__file__).resolve()
        for parent in [here.parent, *here.parents]:
            if (parent / "data" / "sources" / "plant_signage.json").exists():
                return parent
        return None


REPO = find_repo()
if REPO is None:
    sys.exit("Could not find the repo. Run this from inside it, or next to psbp_common.py.")

SOURCES        = REPO / "data" / "sources"
PLANT_SIGNAGE  = SOURCES / "plant_signage.json"
WILD_SIGNAGE   = SOURCES / "wildlife_signage.json"
CREDITS        = SOURCES / "photo_credits.json"
WORKBENCH      = SOURCES / "photo_workbench.json"
PUBLISH_STATE  = SOURCES / "publish_state.json"
RESEARCH       = SOURCES / "research.json"
PLANTS_JSON    = REPO / "plants.json"
WILDLIFE_JSON  = REPO / "wildlife.json"
PLANTS_DIR     = REPO / "plants"
WILDLIFE_DIR   = REPO / "wildlife"
PHOTOS_DIR     = REPO / "photos"


def load(path, default=None):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except FileNotFoundError:
        return default
    except json.JSONDecodeError as exc:
        sys.exit(f"{path.name} is not valid JSON: {exc}")


def species_list(blob):
    if isinstance(blob, dict):
        return blob.get("species", [])
    return blob or []


# ═══════════════════════════════════════════════════════════════════════════
#  GATHER
# ═══════════════════════════════════════════════════════════════════════════

def audit():
    plants_sig = species_list(load(PLANT_SIGNAGE, {}))
    wild_sig   = species_list(load(WILD_SIGNAGE, {}))
    credits    = load(CREDITS, {})
    photos     = credits.get("photos", []) if isinstance(credits, dict) else (credits or [])
    research   = species_list(load(RESEARCH, {}))
    pub_state  = (load(PUBLISH_STATE, {}) or {}).get("species", {})
    idx_plants = load(PLANTS_JSON, []) or []
    idx_wild   = load(WILDLIFE_JSON, []) or []

    # id -> (status, corpus, common_name)
    sig = {}
    for sp in plants_sig:
        sig[sp["id"]] = (sp.get("status", "?"), "plants", sp.get("common_name", ""))
    for sp in wild_sig:
        sig[sp["id"]] = (sp.get("status", "?"), "wildlife", sp.get("common_name", ""))

    research_ids = {sp.get("id") for sp in research if sp.get("id")}
    index_ids    = {e.get("id") for e in idx_plants} | {e.get("id") for e in idx_wild}

    r = {"repo": str(REPO), "counts": {}, "findings": defaultdict(list)}
    add = lambda k, v: r["findings"][k].append(v)

    r["counts"] = {
        "signage_species": len(sig),
        "signage_html":    sum(1 for s in sig.values() if s[0] == "html"),
        "signage_spotted": sum(1 for s in sig.values() if s[0] == "spotted"),
        "credit_photos":   len(photos),
        "research":        len(research_ids),
    }

    # ── 1. THE BUG: credits for species that left signage ──────────────────
    by_species = defaultdict(list)
    for p in photos:
        by_species[p.get("psbp_id")].append(p)

    for sid, rows in sorted(by_species.items()):
        if sid in sig:
            status = sig[sid][0]
            if status not in ("html", "spotted"):
                add("credits_bad_status", {
                    "id": sid, "status": status, "photos": len(rows),
                    "name": sig[sid][2],
                    "photographers": sorted({x.get("photographer_name") or x.get("photographer") for x in rows}),
                })
            continue
        add("credits_orphaned", {
            "id": sid,
            "photos": len(rows),
            "name": rows[0].get("common_name", ""),
            "type": rows[0].get("type", ""),
            "in_research": sid in research_ids,
            "has_hero_row": any(x.get("hero") for x in rows),
            "photographers": sorted({x.get("photographer_name") or x.get("photographer") for x in rows}),
        })

    # ── 2. Hero photo folders for species that no longer qualify ───────────
    if PHOTOS_DIR.is_dir():
        for d in sorted(PHOTOS_DIR.iterdir()):
            if not d.is_dir():
                continue
            files = [f.name for f in d.iterdir() if f.is_file() and not f.name.startswith(".")]
            sid = d.name
            if sid not in sig:
                add("photo_dir_orphaned", {"id": sid, "files": files,
                                           "in_research": sid in research_ids})
            elif not files:
                add("photo_dir_empty", {"id": sid, "status": sig[sid][0]})

    # ── 3. Generated pages with no live signage record ─────────────────────
    for corpus, folder in (("plants", PLANTS_DIR), ("wildlife", WILDLIFE_DIR)):
        if not folder.is_dir():
            continue
        for f in sorted(folder.glob("PSBP-*.html")):
            sid = f.name.split("-")[0] + "-" + f.name.split("-")[1]
            status = sig.get(sid, (None,))[0]
            if status is None:
                add("page_orphaned", {"id": sid, "file": str(f.relative_to(REPO)),
                                      "in_research": sid in research_ids})
            elif status != "html":
                add("page_should_be_gone", {"id": sid, "status": status,
                                            "file": str(f.relative_to(REPO))})

    # ── 4. Search-index cards with no live html record ─────────────────────
    for sid in sorted(index_ids):
        status = sig.get(sid, (None,))[0]
        if status != "html":
            add("index_stale", {"id": sid, "status": status or "MISSING"})

    # ── 5. publish_state pointing at nothing ───────────────────────────────
    for sid, rec in sorted(pub_state.items()):
        status = sig.get(sid, (None,))[0]
        fname = rec.get("filename", "")
        corpus = rec.get("corpus", "plants")
        target = (PLANTS_DIR if corpus == "plants" else WILDLIFE_DIR) / fname
        if status is None:
            add("publish_state_orphaned", {"id": sid, "file": fname})
        elif status != "html":
            add("publish_state_stale", {"id": sid, "status": status, "file": fname})
        elif fname and not target.exists():
            add("publish_state_missing_file", {"id": sid, "file": fname})

    # ── 6. The mirror image: things that should exist and don't ────────────
    for sid, (status, corpus, name) in sorted(sig.items()):
        folder = PHOTOS_DIR / sid
        has_local = folder.is_dir() and any(
            f.is_file() and not f.name.startswith(".") for f in folder.iterdir()
        ) if folder.exists() else False
        has_credits = sid in by_species
        hero_rows = [p for p in by_species.get(sid, []) if p.get("hero")]

        if status == "html":
            if not has_local:
                add("html_no_hero_file", {"id": sid, "name": name})
            if not hero_rows:
                add("html_no_hero_credit", {"id": sid, "name": name})
            if sid not in index_ids:
                add("html_not_in_index", {"id": sid, "name": name, "corpus": corpus})
            pages = list((PLANTS_DIR if corpus == "plants" else WILDLIFE_DIR).glob(f"{sid}-*.html"))
            if not pages:
                add("html_no_page", {"id": sid, "name": name, "corpus": corpus})
        elif status == "spotted":
            if not has_credits:
                add("spotted_no_photos", {"id": sid, "name": name})

    # ── 7. Licence check — ND images can't be safely cropped to hero ───────
    for p in photos:
        if str(p.get("license", "")).upper().replace("_", "-") == "CC-BY-ND":
            add("license_nd", {
                "id": p.get("psbp_id"), "name": p.get("common_name", ""),
                "hero": bool(p.get("hero")),
                "photographer": p.get("photographer_name") or p.get("photographer"),
                "photo_id": p.get("photo_id"),
            })

    return r, photos, credits


# ═══════════════════════════════════════════════════════════════════════════
#  REPORT
# ═══════════════════════════════════════════════════════════════════════════

HEADINGS = [
    ("credits_orphaned",         "Credits for species that no longer exist in signage", True),
    ("credits_bad_status",       "Credits for species that are neither html nor spotted", True),
    ("photo_dir_orphaned",       "Hero photo folders with no signage record", True),
    ("page_orphaned",            "Generated pages with no signage record", True),
    ("page_should_be_gone",      "Pages for species no longer published", True),
    ("index_stale",              "Search-index cards with no html record", True),
    ("publish_state_orphaned",   "publish_state records for vanished species", True),
    ("publish_state_stale",      "publish_state records for unpublished species", True),
    ("publish_state_missing_file", "publish_state records whose file is gone", False),
    ("photo_dir_empty",          "Empty hero photo folders", False),
    ("html_no_hero_file",        "Published species with no local hero JPG", False),
    ("html_no_hero_credit",      "Published species with no hero row in credits", False),
    ("html_no_page",             "Published species with no generated page", False),
    ("html_not_in_index",        "Published species missing from the search index", False),
    ("spotted_no_photos",        "Spotted species with no photos yet", False),
    ("license_nd",               "CC-BY-ND photos (no derivatives — cropping is a derivative)", False),
]


def report(r, verbose):
    f = r["findings"]
    c = r["counts"]
    print(f"\n  PSBP orphan audit — {r['repo']}")
    print(f"  {datetime.now():%Y-%m-%d %H:%M}")
    print("  " + "─" * 68)
    print(f"  {c['signage_species']} species in signage "
          f"({c['signage_html']} html, {c['signage_spotted']} spotted) · "
          f"{c['credit_photos']} credited photos · {c['research']} in research")
    print()

    problems = 0
    for key, title, is_problem in HEADINGS:
        items = f.get(key, [])
        if not items:
            continue
        if is_problem:
            problems += len(items)
        mark = "✗" if is_problem else "·"
        print(f"  {mark} {title}: {len(items)}")
        show = items if verbose else items[:6]
        for it in show:
            bits = [it.get("id", "")]
            if it.get("name"):         bits.append(it["name"])
            if it.get("status"):       bits.append(f"[{it['status']}]")
            if it.get("photos"):       bits.append(f"{it['photos']} photo(s)")
            if it.get("file"):         bits.append(it["file"])
            if it.get("files"):        bits.append(", ".join(it["files"]))
            if it.get("in_research"):  bits.append("(in research.json)")
            if it.get("hero"):         bits.append("HERO")
            if it.get("photographers"):bits.append("— " + ", ".join(it["photographers"]))
            if it.get("photographer"): bits.append("— " + str(it["photographer"]))
            print("      " + "  ".join(str(b) for b in bits if b))
        if not verbose and len(items) > 6:
            print(f"      … and {len(items) - 6} more (--verbose to list)")
        print()

    if problems == 0:
        print("  Nothing orphaned. The four sources agree.\n")
    else:
        print(f"  {problems} item(s) need attention. Re-run with --fix to repair.\n")
    return problems


# ═══════════════════════════════════════════════════════════════════════════
#  FIX
# ═══════════════════════════════════════════════════════════════════════════

def backup(path):
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    dest = path.with_suffix(path.suffix + f".bak-{stamp}")
    shutil.copy2(path, dest)
    return dest


def fix(r, credits_blob, assume_yes=False):
    """Repair only what is unambiguous. Anything requiring a judgement call
    is left alone and reported instead."""
    f = r["findings"]
    plan = []
    orphan_ids = {i["id"] for i in f.get("credits_orphaned", [])}
    if orphan_ids:
        n = sum(i["photos"] for i in f.get("credits_orphaned", []))
        plan.append(f"remove {n} photo row(s) for {len(orphan_ids)} species from photo_credits.json")
    dirs = [i["id"] for i in f.get("photo_dir_orphaned", [])]
    if dirs:
        plan.append(f"delete {len(dirs)} orphaned hero folder(s) under photos/")
    pages = [i["file"] for i in f.get("page_orphaned", [])] + \
            [i["file"] for i in f.get("page_should_be_gone", [])]
    if pages:
        plan.append(f"delete {len(pages)} orphaned page(s)")

    if not plan:
        print("  Nothing to fix.\n")
        return

    print("\n  This will:")
    for p in plan:
        print("    · " + p)
    print("\n  Backups (.bak-TIMESTAMP) are written for every JSON changed.")
    print("  Deleted files are NOT backed up — they are regenerable, but")
    print("  make sure your working tree is committed first.\n")
    if not assume_yes:
        if input("  Proceed? [y/N] ").strip().lower() not in ("y", "yes"):
            print("  Aborted.\n")
            return

    if orphan_ids:
        b = backup(CREDITS)
        before = len(credits_blob["photos"])
        credits_blob["photos"] = [p for p in credits_blob["photos"]
                                  if p.get("psbp_id") not in orphan_ids]
        credits_blob.setdefault("meta", {})["photo_count"] = len(credits_blob["photos"])
        credits_blob["meta"]["updated"] = datetime.now().isoformat(timespec="seconds")
        tmp = CREDITS.with_suffix(".tmp")
        tmp.write_text(json.dumps(credits_blob, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(CREDITS)
        print(f"  ✓ photo_credits.json {before} → {len(credits_blob['photos'])}  (backup: {b.name})")

        # THE WORKBENCH GOES WITH THE CREDITS. Removing a credit row while
        # leaving its decision behind is the asymmetry that produced the
        # PSBP-99922 lockout, and this repair used to do exactly that — it
        # purged credits and never wrote to the workbench at all.
        #
        # Same retention rule as the demote handler: `promoted` and `skip` are
        # working state and leave with the species; `block` means "never
        # resurface" and is kept, deliberately orphaned.
        wb_blob = load(WORKBENCH, {"decisions": {}})
        decisions = wb_blob.get("decisions", {})
        drop = [k for k, d in decisions.items()
                if d.get("psbp_id") in orphan_ids
                and d.get("decision") in ("promoted", "skip")]
        kept = sum(1 for d in decisions.values()
                   if d.get("psbp_id") in orphan_ids and d.get("decision") == "block")
        if drop:
            wb_b = backup(WORKBENCH)
            wb_before = len(decisions)
            for k in drop:
                decisions.pop(k, None)
            wb_blob.setdefault("meta", {})["updated"] = datetime.now().isoformat(timespec="seconds")
            tmp = WORKBENCH.with_suffix(".tmp")
            tmp.write_text(json.dumps(wb_blob, indent=2, ensure_ascii=False), encoding="utf-8")
            tmp.replace(WORKBENCH)
            print(f"  ✓ photo_workbench.json {wb_before} → {len(decisions)}"
                  f"  ({kept} block verdict(s) kept)  (backup: {wb_b.name})")
        elif kept:
            print(f"  · photo_workbench.json unchanged — {kept} block verdict(s) kept by design")

    for sid in dirs:
        d = PHOTOS_DIR / sid
        if d.is_dir():
            shutil.rmtree(d)
            print(f"  ✓ removed photos/{sid}/")

    for rel in pages:
        p = REPO / rel
        if p.exists():
            p.unlink()
            print(f"  ✓ removed {rel}")

    print("\n  Left alone (needs a human): stale index cards, publish_state drift,")
    print("  and every 'should exist but doesn't' finding. Republish fixes most.\n")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--fix", action="store_true", help="repair what is unambiguous")
    ap.add_argument("--yes", action="store_true", help="skip the confirmation prompt")
    ap.add_argument("--verbose", action="store_true", help="list every affected item")
    ap.add_argument("--json", action="store_true", help="emit JSON instead of a report")
    args = ap.parse_args()

    r, photos, credits_blob = audit()

    if args.json:
        print(json.dumps({"repo": r["repo"], "counts": r["counts"],
                          "findings": dict(r["findings"])}, indent=2))
        return 0

    problems = report(r, args.verbose)
    if args.fix:
        fix(r, credits_blob, assume_yes=args.yes)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
