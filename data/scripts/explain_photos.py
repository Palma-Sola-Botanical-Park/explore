#!/usr/bin/env python3
"""
explain_photos.py — answer "where did my photos go?" for one or more species.
=============================================================================
Place in data/scripts/ next to fetch_my_photos.py.

WHY
    fetch_my_photos skips any iNat photo whose photo_id is already in
    photo_credits.json. That check is GLOBAL — it matches the app's own
    "already in registry" guard. So a photo filed under the WRONG species
    still reads as a duplicate and gets silently skipped, which looks like
    "0 new" on a species that appears to have no photos at all.

    This script resolves that: for every photo you have on iNat for a
    species, it says exactly which psbp_id currently owns it.

USAGE
    python3 explain_photos.py --ids PSBP-00476
    python3 explain_photos.py --ids PSBP-00476,PSBP-00265
    python3 explain_photos.py --all-spotted
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import species_manager as sm                                       # noqa: E402
from psbp_common import load_json                                  # noqa: E402
from fetch_my_photos import fetch_my_observations, flatten_photos  # noqa: E402

DEFAULT_USER = "randall_carter"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--user", default=DEFAULT_USER)
    ap.add_argument("--ids", help="comma-separated PSBP ids")
    ap.add_argument("--all-spotted", action="store_true")
    args = ap.parse_args()

    signage = load_json(sm.PLANT_SIGNAGE, {"species": []})
    credits = load_json(sm.PHOTO_CREDITS, {"meta": {}, "photos": []})
    photos = credits.get("photos", [])

    owner = {}
    for p in photos:
        owner.setdefault(str(p.get("photo_id")), []).append(p)

    species = signage.get("species", [])
    if args.ids:
        want = {i.strip().upper() for i in args.ids.split(",") if i.strip()}
        targets = [s for s in species if s["id"] in want]
    elif args.all_spotted:
        targets = [s for s in species if s.get("status") == "spotted"]
    else:
        ap.error("give --ids or --all-spotted")

    for sp in targets:
        pid = sp["id"]
        print(f"\n{'='*72}")
        print(f"  {pid}  {sp.get('common_name','')}  ({sp.get('botanical_name','')})")
        print(f"{'='*72}")

        # 1. What the registry currently holds FOR this species.
        mine = [p for p in photos if p.get("psbp_id") == pid]
        print(f"\n  registry records filed under {pid}: {len(mine)}")
        for p in mine:
            flag = "HERO" if p.get("hero") else "    "
            print(f"    {flag} photo {p.get('photo_id')}  role={p.get('role')}  "
                  f"virtual={p.get('virtual')}  filename={p.get('filename')}  "
                  f"status={p.get('status')}  publish_ok={p.get('publish_ok')}")

        # 2. Is the hero's file actually on disk?
        for p in mine:
            if p.get("hero") and p.get("filename"):
                f = os.path.join(sm.PHOTOS_DIR, pid, p["filename"])
                state = "present" if os.path.isfile(f) else ">>> MISSING <<<"
                size = f" ({os.path.getsize(f)//1024}KB)" if os.path.isfile(f) else ""
                print(f"    hero file {f}: {state}{size}")

        # 3. What iNat has, and who owns each photo in the registry.
        taxon = sp.get("inat_taxon_id")
        if not taxon:
            print("\n  no inat_taxon_id — cannot query iNat")
            continue
        rows = flatten_photos(fetch_my_observations(args.user, taxon))
        print(f"\n  photos on iNat for taxon {taxon}: {len(rows)}")
        for r in rows:
            recs = owner.get(r["photo_id"], [])
            if not recs:
                where = "NOT in registry (fetch_my_photos would import it)"
            else:
                where = ", ".join(
                    f"owned by {x.get('psbp_id')}"
                    f"{' [HERO]' if x.get('hero') else ''}"
                    + ("  <<< MISFILED" if x.get("psbp_id") != pid else "")
                    for x in recs)
            print(f"    photo {r['photo_id']}  obs {r['obs_id']}  {where}")

    print()


if __name__ == "__main__":
    sys.exit(main())
