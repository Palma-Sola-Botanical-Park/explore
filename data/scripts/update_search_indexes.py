#!/usr/bin/env python3
"""
update_search_indexes.py  —  sync hero photo paths in search indexes
=====================================================================
Reads photo_credits.json for the current hero per species, then patches
plants.json and wildlife.json so their `photo`, `credit`, and `focus`
fields match. Atomic write; original is not modified in place.

Safe to re-run any time heroes change (new hero crowned in YELLOW,
rehydrate re-run, etc.).

USAGE
  python3 update_search_indexes.py           # live
  python3 update_search_indexes.py --dry-run # preview only
"""

import json
import os
import sys

# ===========================================================================
# CONFIG
# ===========================================================================
REPO    = "/Users/fiona/Documents/GitHub/explore"
SOURCES = os.path.join(REPO, "data", "sources")
SEARCH  = os.path.join(REPO, "data")

PHOTO_CREDITS = os.path.join(SOURCES, "photo_credits.json")
PLANTS_JSON   = os.path.join(REPO, "plants.json")
WILDLIFE_JSON = os.path.join(REPO, "wildlife.json")


def write_json_atomic(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    os.replace(tmp, path)


def build_hero_lookup(credits):
    """psbp_id -> hero row from photo_credits.json"""
    heroes = {}
    for p in credits.get("photos", []):
        if p.get("hero") and p.get("psbp_id"):
            heroes[p["psbp_id"]] = p
    return heroes


def patch_index(index, heroes, label, dry_run=False):
    """Update photo/credit/focus fields in a search index. Returns count of changes."""
    changed = 0
    for sp in index:
        pid = sp.get("id", "")
        hero = heroes.get(pid)
        if not hero:
            continue

        photo_id = hero.get("photo_id", "")
        if not photo_id:
            continue

        new_photo = f"photos/{pid}/{photo_id}.jpg"
        new_credit = hero.get("photographer", "")
        new_focus = hero.get("focus", "50% 50%")

        old_photo = sp.get("photo", "")
        if old_photo != new_photo:
            if dry_run:
                print(f"  {pid} {sp.get('common', '?')}:")
                print(f"    photo: {old_photo} -> {new_photo}")
            sp["photo"] = new_photo
            changed += 1

        # Ensure credit and focus fields are present and current
        if sp.get("credit") != new_credit:
            sp["credit"] = new_credit
        if sp.get("focus") != new_focus:
            sp["focus"] = new_focus

    return changed


def main():
    dry_run = "--dry-run" in sys.argv

    if not os.path.isfile(PHOTO_CREDITS):
        print(f"ERROR: {PHOTO_CREDITS} not found"); sys.exit(1)

    credits = json.load(open(PHOTO_CREDITS, encoding="utf-8"))
    heroes = build_hero_lookup(credits)
    print(f"Heroes in photo_credits.json: {len(heroes)}")

    for path, label in [(PLANTS_JSON, "plants"), (WILDLIFE_JSON, "wildlife")]:
        if not os.path.isfile(path):
            print(f"  {label}: file not found, skipping")
            continue
        index = json.load(open(path, encoding="utf-8"))
        n = patch_index(index, heroes, label, dry_run)
        if not dry_run and n > 0:
            write_json_atomic(path, index)
        print(f"  {label}: {n} photo paths updated"
              f"{' (dry run)' if dry_run else ''}"
              f" ({len(index)} species total)")

    print("Done.")


if __name__ == "__main__":
    main()
