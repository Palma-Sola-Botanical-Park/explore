#!/usr/bin/env python3
"""
download_plant_photos.py
========================
Plant twin of download_species_photos.py. Downloads all CC-licensed photos for
one or more PLANT species from iNaturalist and registers each in
photo_credits.json.

DIFFERENCES FROM THE WILDLIFE VERSION:
  - reads plant_signage.json (not wildlife_signage.json)
  - scientific name comes from `botanical_name` (plants) not `scientific_name`
  - type is "Plant"
  - captures the THREE fields wildlife skipped, in the SAME API call:
        photographer_name  (real name when iNat has it, else handle)
        observed_on        (date taken)
        shared_on          (date uploaded)
    so new plant records match the backfilled registry exactly.
  - credit_line uses the real name, one consistent format.
  - NAME_OVERRIDES (keep in sync with backfill_photo_dates.py).
  - writes photo_credits.json atomically (temp + replace) so a crash can't
    truncate the file you just promoted.

The observations CSV is the SAME full-project export the wildlife run used —
it contains plant rows too; we just look them up by the plant taxon_ids.

USAGE
-----
  # Preview (touches nothing) — do this first:
  python3 download_plant_photos.py --species PSBP-00017,PSBP-00037,PSBP-00122 --dry-run

  # Real run:
  python3 download_plant_photos.py --species PSBP-00017,PSBP-00037,PSBP-00122

  # Every plant in the signage JSON:
  python3 download_plant_photos.py --all
"""

import csv
import json
import os
import sys
import time
import urllib.request
import urllib.error
from collections import defaultdict

# ---------------------------------------------------------------------------
# CONFIG
# ---------------------------------------------------------------------------
PLANT_JSON = "plant_signage.json"
PHOTO_CREDITS_JSON = "photo_credits.json"
OBSERVATIONS_CSV = "observations-750817.csv"   # PLANT project export (788 obs, all Plantae)
PHOTOS_DIR = "photos"

API_DELAY = 1.0
DOWNLOAD_DELAY = 0.3

# Keep this in sync with backfill_photo_dates.py. Keys = lowercase iNat handle.
NAME_OVERRIDES = {
    "frankymca": "Franky McArthur",
    "cleamon":   "Christine Leamon",
}

CC_LICENSES = {
    "cc-by", "cc-by-nc", "cc-by-sa", "cc-by-nc-sa",
    "cc-by-nd", "cc-by-nc-nd", "cc0",
}


# ---------------------------------------------------------------------------
# NAME / CREDIT HELPERS  (match backfill_photo_dates.py)
# ---------------------------------------------------------------------------

def display_name(login, name):
    ov = NAME_OVERRIDES.get((login or "").lower())
    if ov:
        return ov
    name = (name or "").strip()
    return name if name else (login or "")


def build_credit_line(name, license_code):
    lic = (license_code or "").strip()
    if lic and lic.lower() != "nan":
        return f"© {name} ({lic.upper()}), via iNaturalist"
    return f"© {name}, via iNaturalist"


# ---------------------------------------------------------------------------
# API + DOWNLOAD
# ---------------------------------------------------------------------------

def inat_get(url):
    headers = {
        "Accept": "application/json",
        "User-Agent": "PSBP-PlantPhotoDownloader/1.0 (palmasolabp.org)",
    }
    token = os.environ.get("INAT_TOKEN")
    if token:
        headers["Authorization"] = token
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        print(f"    HTTP {e.code}: {e.read().decode('utf-8', errors='replace')[:160]}")
        return None
    except Exception as e:
        print(f"    request failed: {e}")
        return None


def get_observation(obs_id):
    """Return (photos, meta) for an observation. meta carries observer login +
    real name + the two dates — the fields the wildlife script never grabbed."""
    data = inat_get(f"https://api.inaturalist.org/v1/observations/{obs_id}")
    if not data or not data.get("results"):
        return [], None
    obs = data["results"][0]
    user = obs.get("user") or {}
    meta = {
        "login": user.get("login") or "unknown",
        "name": (user.get("name") or "").strip(),
        "observed_on": obs.get("observed_on") or (obs.get("time_observed_at") or "")[:10] or None,
        "shared_on": (obs.get("created_at") or "")[:10] or None,
        "obs_url": f"https://www.inaturalist.org/observations/{obs_id}",
    }
    photos = []
    for p in obs.get("photos", []):
        photos.append({
            "photo_id": str(p.get("id", "")),
            "large_url": (p.get("url", "") or "").replace("/square.", "/original."),
            "license_code": p.get("license_code") or "",
        })
    return photos, meta


def download_photo(url, dest_path):
    req = urllib.request.Request(url, headers={"User-Agent": "PSBP-PlantPhotoDownloader/1.0"})
    try:
        with urllib.request.urlopen(req) as resp:
            with open(dest_path, "wb") as f:
                f.write(resp.read())
        return True
    except Exception as e:
        print(f"    download failed: {e}")
        return False


def write_json_atomic(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    os.replace(tmp, path)


# ---------------------------------------------------------------------------
# CSV INDEX
# ---------------------------------------------------------------------------

def build_obs_index(csv_path):
    rows = list(csv.DictReader(open(csv_path, encoding="utf-8")))
    index = defaultdict(set)
    sci_index = defaultdict(set)
    for r in rows:
        oid = r.get("id", "").strip()
        tid = r.get("taxon_id", "").strip()
        if tid and oid:
            index[int(tid)].add(oid)
        sci = r.get("scientific_name", "").strip().split()
        if len(sci) >= 2 and oid:
            sci_index[f"{sci[0]} {sci[1]}"].add(oid)
    return index, sci_index


# ---------------------------------------------------------------------------
# PER-SPECIES
# ---------------------------------------------------------------------------

def process_species(sp, obs_ids, photo_credits, dry_run=False):
    psbp_id = sp["id"]
    common_name = sp["common_name"]
    sci_name = sp.get("botanical_name") or sp.get("scientific_name") or ""

    subfolder = os.path.join(PHOTOS_DIR, psbp_id)

    existing_photo_ids = set()
    has_hero = False
    for pc in photo_credits:
        if pc.get("psbp_id") == psbp_id:
            if pc.get("photo_id"):
                existing_photo_ids.add(str(pc["photo_id"]))
            if pc.get("hero"):
                has_hero = True

    print(f"\n{'='*70}")
    print(f"{psbp_id} — {common_name} ({sci_name})")
    print(f"  Observations to check: {len(obs_ids)}")
    print(f"  Already registered:    {len(existing_photo_ids)} | has hero: {has_hero}")

    if not dry_run:
        os.makedirs(subfolder, exist_ok=True)

    new_entries = []
    downloaded = skipped_license = skipped_existing = 0

    for i, obs_id in enumerate(sorted(obs_ids)):
        print(f"  [{i+1}/{len(obs_ids)}] obs #{obs_id}...", end=" ")
        photos, meta = get_observation(obs_id)
        if not photos or not meta:
            print("no photos")
            time.sleep(API_DELAY)
            continue
        name = display_name(meta["login"], meta["name"])
        print(f"{len(photos)} photo(s) — {name}")

        for p in photos:
            pid = p["photo_id"]
            lic = p["license_code"]
            if pid in existing_photo_ids:
                skipped_existing += 1
                continue
            if not lic or lic.lower() not in CC_LICENSES:
                skipped_license += 1
                print(f"    photo {pid}: skipped (license: {lic or 'none'})")
                continue

            filename = f"{pid}.jpg"
            dest = os.path.join(subfolder, filename)
            is_hero = (not has_hero) and downloaded == 0 and not new_entries
            role = ["whole", "gallery"] if is_hero else ["gallery"]

            if not dry_run:
                print(f"    photo {pid}: downloading...", end=" ")
                if not download_photo(p["large_url"], dest):
                    continue
                print(f"✓ {filename}")
                time.sleep(DOWNLOAD_DELAY)
            else:
                print(f"    photo {pid}: would download → {dest}  (hero={is_hero})")
            downloaded += 1

            entry = {
                "psbp_id": psbp_id,
                "type": "Plant",
                "common_name": common_name,
                "scientific_name": sci_name,
                "role": role,
                "primary_for": ["whole"] if is_hero else [],
                "hero": is_hero,
                "focus": "50% 50%" if is_hero else None,
                "tags": [],
                "photographer": meta["login"],
                "photographer_name": name,
                "license": lic.upper(),
                "publish_ok": True,
                "status": "OK",
                "credit_line": build_credit_line(name, lic),
                "observed_on": meta["observed_on"],
                "shared_on": meta["shared_on"],
                "photo_url": p["large_url"],
                "source_url": meta["obs_url"],
                "observation_id": obs_id,
                "photo_id": pid,
                "filename": filename,
                "used_by": [],
            }
            new_entries.append(entry)
            existing_photo_ids.add(pid)

        time.sleep(API_DELAY)

    print(f"  -> downloaded {downloaded}, skipped {skipped_existing} existing, "
          f"{skipped_license} non-CC")
    heroes = [e for e in new_entries if e["hero"]]
    if heroes:
        print(f"     hero: {heroes[0]['filename']} by {heroes[0]['photographer_name']}")
    return new_entries


# ---------------------------------------------------------------------------
# MAIN
# ---------------------------------------------------------------------------

def main():
    import argparse
    ap = argparse.ArgumentParser(description="Download plant photos from iNaturalist")
    ap.add_argument("--species", help="Comma-separated PSBP IDs")
    ap.add_argument("--all", action="store_true", help="Process all plants in the JSON")
    ap.add_argument("--dry-run", action="store_true", help="Preview only")
    args = ap.parse_args()

    if not args.species and not args.all:
        print("Usage: python3 download_plant_photos.py --species PSBP-00017,PSBP-00037,PSBP-00122")
        print("       add --dry-run to preview")
        sys.exit(1)

    ps = json.load(open(PLANT_JSON, encoding="utf-8"))
    pc_data = json.load(open(PHOTO_CREDITS_JSON, encoding="utf-8"))
    photo_credits = pc_data["photos"]
    obs_index, sci_index = build_obs_index(OBSERVATIONS_CSV)

    species_map = {s["id"]: s for s in ps["species"]}
    if args.all:
        target_ids = [s["id"] for s in ps["species"]]
    else:
        target_ids = [x.strip() for x in args.species.split(",")]

    targets = []
    for tid in target_ids:
        if tid in species_map:
            targets.append(species_map[tid])
        else:
            print(f"WARNING: {tid} not found in {PLANT_JSON}")

    print("=" * 70)
    print(f"PSBP Plant Photo Downloader — {'DRY RUN' if args.dry_run else 'LIVE'}")
    print(f"Species to process: {len(targets)}")
    print("=" * 70)

    all_new = []
    for sp in targets:
        taxon_id = sp.get("inat_taxon_id")
        obs_ids = set(obs_index.get(taxon_id, set())) if taxon_id else set()
        parts = (sp.get("botanical_name") or "").split()
        if len(parts) >= 2:
            obs_ids |= sci_index.get(f"{parts[0]} {parts[1]}", set())
        if not obs_ids:
            print(f"\n{sp['id']} — {sp['common_name']}: no observations in CSV, skipping")
            continue
        new = process_species(sp, obs_ids, photo_credits, dry_run=args.dry_run)
        all_new.extend(new)
        photo_credits.extend(new)

    if all_new and not args.dry_run:
        pc_data["photos"] = photo_credits
        pc_data.setdefault("meta", {})["photo_count"] = len(photo_credits)
        write_json_atomic(PHOTO_CREDITS_JSON, pc_data)
        print(f"\n{'='*70}")
        print(f"photo_credits.json updated: +{len(all_new)} entries "
              f"(total {len(photo_credits)})")
    elif all_new:
        print(f"\n{'='*70}")
        print(f"DRY RUN: would add {len(all_new)} entries")
    else:
        print(f"\n{'='*70}\nNo new photos to add.")
    print("Done.")


if __name__ == "__main__":
    main()
