#!/usr/bin/env python3
"""
fetch_my_photos.py — pull YOUR OWN iNat observation photos into the PSBP repo.
==============================================================================
Place in data/scripts/ alongside species_manager.py.

WHY THIS EXISTS
    The triage grid in species_manager queries iNat by project_id. Observations
    that never became project members are invisible to it — which is most of
    the threatened-taxa palms, whose public coordinates land far from the park.
    This script queries by user_id instead, so your own photos are reachable
    regardless of project membership or coordinate obscuring.

WHAT IT DOES, PER SPECIES
    1. Look up inat_taxon_id from plant_signage.json
    2. GET /v1/observations?user_id=<you>&taxon_id=<t>&verifiable=any  (paginated)
    3. Order photos: newest observation first, original photo order within each
    4. Drop any photo_id already in photo_credits.json
    5. If the species has NO hero yet, the first photo becomes the hero:
           download /large.jpg -> photos/PSBP-xxxxx/<photo_id>.jpg
           shrink in place to the repo size budget
           verify the file exists and is non-empty
           ONLY THEN write the registry record
       If the download fails, nothing is recorded for that species. Ever.
    6. Remaining photos are recorded as gallery entries with virtual=True and
       filename=None — served from the iNat CDN, exactly like the app's own
       non-hero photos. No local file, no size cost.
    7. photo_credits.json is written atomically after each species, so an
       interrupted run keeps everything it already finished.

WHAT IT WILL NOT DO
    - Never replaces an existing hero. A species that already has one gets its
      new photos as gallery only. Use the Photos page to re-crown.
    - Never records a hero whose file is not on disk.
    - Never adds a photo_id twice.
    - Never touches plant_signage.json or research.json.

USAGE
    python3 fetch_my_photos.py                      # dry run, all spotted w/o hero
    python3 fetch_my_photos.py --apply              # actually do it
    python3 fetch_my_photos.py --ids PSBP-00265,PSBP-00280 --apply
    python3 fetch_my_photos.py --all-spotted --apply
    python3 fetch_my_photos.py --max-gallery 12 --apply

    INAT_TOKEN=<jwt> python3 fetch_my_photos.py --apply
        Optional. Only affects coordinates, which this script does not use.
        Photos are public either way.
"""

import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import species_manager as sm                                       # noqa: E402
from psbp_common import (                                          # noqa: E402
    CC_LICENSES, load_json, write_json_atomic,
    display_name, build_credit_line,
)

DEFAULT_USER = "randall_carter"
DEFAULT_MAX_GALLERY = 8
API_PAGE = 200
MAX_PAGES = 10


# ───────────────────────────────────────────────────────────────────────────
# iNat
# ───────────────────────────────────────────────────────────────────────────

def fetch_my_observations(user_login, taxon_id):
    """Every observation of one taxon by one user, newest first.

    verifiable=any keeps casual-grade records — nearly all cultivated park
    plantings are casual, and excluding them is what makes a species look
    unphotographed when it isn't.
    """
    out, page = [], 1
    while page <= MAX_PAGES:
        url = ("https://api.inaturalist.org/v1/observations"
               f"?user_id={user_login}&taxon_id={taxon_id}"
               f"&per_page={API_PAGE}&page={page}&verifiable=any"
               "&order=desc&order_by=observed_on")
        data = sm._inat_get(url)
        if not data:
            break
        results = data.get("results", [])
        out.extend(results)
        if len(results) < API_PAGE:
            break
        page += 1
        time.sleep(sm.API_DELAY)
    return out


def _photo_url(photo, size="large"):
    """iNat photo objects carry a 'square' thumbnail URL; swap in the size we
    want. Mirrors _download_hero_file's own substitution so hero and gallery
    records always point at the same rendition."""
    url = (photo or {}).get("url") or ""
    for token in ("square", "small", "medium", "thumb"):
        if f"/{token}." in url:
            return url.replace(f"/{token}.", f"/{size}.")
    return url


def flatten_photos(observations):
    """Observations -> ordered photo dicts, newest observation first.

    Within an observation, iNat returns photos in the order the observer
    arranged them, so photo[0] is the one you chose as the lead image. That
    ordering is why "just take the first" is a sane hero default.
    """
    rows = []
    for obs in observations:
        user = obs.get("user") or {}
        for idx, photo in enumerate(obs.get("photos") or []):
            pid = str(photo.get("id") or "")
            if not pid:
                continue
            rows.append({
                "photo_id":     pid,
                "obs_id":       str(obs.get("id") or ""),
                "obs_position": idx,
                "large_url":    _photo_url(photo, "large"),
                "thumb_url":    _photo_url(photo, "medium"),
                "source_url":   f"https://www.inaturalist.org/observations/{obs.get('id')}",
                "license":      (photo.get("license_code") or "").lower(),
                "photographer": user.get("login", ""),
                "photographer_name": user.get("name") or "",
                "observed_on":  obs.get("observed_on"),
                "shared_on":    (obs.get("created_at") or "")[:10] or None,
                "quality":      obs.get("quality_grade", ""),
            })
    return rows


# ───────────────────────────────────────────────────────────────────────────
# Registry records — shape must match species_manager's own writer exactly
# ───────────────────────────────────────────────────────────────────────────

def build_record(species, row, is_hero):
    """One photo_credits.json entry.

    Field-for-field identical to the dict species_manager builds when you
    promote a photo through the Photos page. If that shape ever changes,
    this function is the thing to update.
    """
    name = row["photographer_name"] or display_name(row["photographer"], "")
    name = display_name(row["photographer"], name)
    lic = (row["license"] or "").upper()
    return {
        "psbp_id":           species["id"],
        "type":              "Plant",
        "common_name":       species.get("common_name", ""),
        "scientific_name":   species.get("botanical_name", ""),
        "role":              ["whole", "gallery"] if is_hero else ["gallery"],
        "primary_for":       ["whole"] if is_hero else [],
        "hero":              is_hero,
        "focus":             "50% 50%" if is_hero else None,
        "tags":              [],
        "photographer":      row["photographer"],
        "photographer_name": name,
        "license":           lic,
        "publish_ok":        True,
        "status":            "OK",
        "credit_line":       build_credit_line(name, lic),
        "observed_on":       row["observed_on"],
        "shared_on":         row["shared_on"],
        "photo_url":         row["large_url"],
        "source_url":        row["source_url"],
        "observation_id":    row["obs_id"],
        "photo_id":          row["photo_id"],
        "filename":          f"{row['photo_id']}.jpg" if is_hero else None,
        "used_by":           [],
        "virtual":           not is_hero,
    }


def record_workbench(wb, row, psbp_id):
    """Mirror the triage ledger so these photos read as already-decided and
    don't resurface as fresh candidates in the Photos grid."""
    wb["decisions"][row["photo_id"]] = {
        "decision":          "promoted",
        "reviewed_on":       sm._today(),
        "psbp_id":           psbp_id,
        "obs_id":            row["obs_id"],
        "photographer":      row["photographer"],
        "photographer_name": row["photographer_name"],
        "license":           row["license"],
        "observed_on":       row["observed_on"],
        "shared_on":         row["shared_on"],
        "thumb_url":         row["thumb_url"],
        "large_url":         row["large_url"],
        "source_url":        row["source_url"],
        "source":            "fetch_my_photos",
    }


# ───────────────────────────────────────────────────────────────────────────
# Target selection
# ───────────────────────────────────────────────────────────────────────────

def pick_targets(args, signage, credits):
    heroed = {p["psbp_id"] for p in credits.get("photos", []) if p.get("hero")}
    species = signage.get("species", [])
    if args.ids:
        want = {i.strip().upper() for i in args.ids.split(",") if i.strip()}
        chosen = [s for s in species if s["id"] in want]
        missing = want - {s["id"] for s in chosen}
        for m in sorted(missing):
            print(f"  ! {m} not found in plant_signage.json")
        return chosen
    pool = [s for s in species if s.get("status") == "spotted"]
    if not args.all_spotted:
        pool = [s for s in pool if s["id"] not in heroed]
    return pool[:args.limit] if args.limit else pool


# ───────────────────────────────────────────────────────────────────────────
# Main
# ───────────────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--user", default=DEFAULT_USER, help="iNat login to pull from")
    ap.add_argument("--ids", help="comma-separated PSBP ids")
    ap.add_argument("--all-spotted", action="store_true",
                    help="every spotted species, even ones that already have a hero")
    ap.add_argument("--limit", type=int, help="cap number of species")
    ap.add_argument("--max-gallery", type=int, default=DEFAULT_MAX_GALLERY,
                    help=f"max gallery photos per species (default {DEFAULT_MAX_GALLERY})")
    ap.add_argument("--allow-noncc", action="store_true",
                    help="accept non-CC photos when they are your own")
    ap.add_argument("--apply", action="store_true",
                    help="actually write; omit for a dry run")
    args = ap.parse_args()

    signage = load_json(sm.PLANT_SIGNAGE, {"species": []})
    credits = load_json(sm.PHOTO_CREDITS, {"meta": {}, "photos": []})
    credits.setdefault("photos", [])
    known = {str(p.get("photo_id")) for p in credits["photos"]}
    heroed = {p["psbp_id"] for p in credits["photos"] if p.get("hero")}

    targets = pick_targets(args, signage, credits)
    mode = "APPLY" if args.apply else "DRY RUN"
    print(f"\n{'='*72}\n  fetch_my_photos — {mode}   user={args.user}   "
          f"species={len(targets)}\n{'='*72}")
    if not targets:
        print("  nothing to do")
        return 0

    wb = sm.load_workbench() if args.apply else {"decisions": {}}
    tot_hero = tot_gal = tot_skip = 0
    failures = []

    for sp in targets:
        pid, taxon = sp["id"], sp.get("inat_taxon_id")
        label = f"{pid} {sp.get('common_name') or sp.get('botanical_name','')}"
        if not taxon:
            print(f"\n{label}\n  ! no inat_taxon_id — skipped")
            continue

        obs = fetch_my_observations(args.user, taxon)
        rows = flatten_photos(obs)
        fresh, dupes = [], 0
        for r in rows:
            if r["photo_id"] in known:
                dupes += 1
                continue
            if r["license"] not in CC_LICENSES:
                if not (args.allow_noncc and r["photographer"] == args.user):
                    tot_skip += 1
                    continue
            fresh.append(r)

        has_hero = pid in heroed
        print(f"\n{label}  (taxon {taxon})")
        print(f"  {len(obs)} observation(s), {len(rows)} photo(s), "
              f"{len(fresh)} new, {dupes} already in registry"
              f"{', HAS HERO' if has_hero else ''}")
        if not fresh:
            continue

        hero_row = None if has_hero else fresh[0]
        gallery = fresh[1:] if hero_row else fresh
        gallery = gallery[:args.max_gallery]

        if hero_row:
            print(f"  HERO    {hero_row['photo_id']}  obs {hero_row['obs_id']}  "
                  f"{hero_row['license'].upper()}")
        for g in gallery:
            print(f"  gallery {g['photo_id']}  obs {g['obs_id']}  (stays on iNat)")

        if not args.apply:
            tot_hero += 1 if hero_row else 0
            tot_gal += len(gallery)
            continue

        new_records = []
        if hero_row:
            try:
                path = sm._download_hero_file(hero_row["large_url"], pid,
                                              hero_row["photo_id"])
            except Exception as e:                                 # noqa: BLE001
                path = None
                print(f"  ! download raised: {e}")
            if not path or not os.path.isfile(path) or os.path.getsize(path) == 0:
                print(f"  ! hero download FAILED — nothing recorded for {pid}")
                failures.append((pid, "hero download failed"))
                continue
            print(f"  + saved {os.path.relpath(path, sm.REPO)} "
                  f"({os.path.getsize(path)//1024}KB)")
            new_records.append(build_record(sp, hero_row, True))
            record_workbench(wb, hero_row, pid)
            heroed.add(pid)
            known.add(hero_row["photo_id"])
            time.sleep(sm.DOWNLOAD_DELAY)

        for g in gallery:
            new_records.append(build_record(sp, g, False))
            record_workbench(wb, g, pid)
            known.add(g["photo_id"])

        credits["photos"].extend(new_records)
        credits.setdefault("meta", {})["photo_count"] = len(credits["photos"])
        write_json_atomic(sm.PHOTO_CREDITS, credits)
        write_json_atomic(sm.PHOTO_WORKBENCH, wb)
        tot_hero += 1 if hero_row else 0
        tot_gal += len(gallery)
        print(f"  ✓ {len(new_records)} record(s) written")

    print(f"\n{'='*72}")
    print(f"  heroes: {tot_hero}   gallery: {tot_gal}   "
          f"non-CC skipped: {tot_skip}   failures: {len(failures)}")
    for f in failures:
        print(f"    ! {f[0]}: {f[1]}")
    if not args.apply:
        print("\n  DRY RUN — nothing written. Re-run with --apply.")
    print(f"{'='*72}\n")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
