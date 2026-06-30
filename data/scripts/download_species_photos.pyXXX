#!/usr/bin/env python3
"""
download_species_photos.py
==========================
Downloads all licensed photos for one or more wildlife species from iNaturalist
observations and updates photo_credits.json.

USAGE
-----
  # Download photos for Brown Anole only (test run):
  python3 download_species_photos.py --species PSBP-99999

  # Download photos for a batch (comma-separated):
  python3 download_species_photos.py --species PSBP-99999,PSBP-99998,PSBP-99997

  # Download photos for ALL species in the JSON:
  python3 download_species_photos.py --all

  # Dry run (shows what it would download, touches nothing):
  python3 download_species_photos.py --species PSBP-99999 --dry-run

FILES NEEDED (same directory, or edit paths below):
  - wildlife_signage.json
  - photo_credits.json
  - observations-750448.csv   (to find observation IDs per species)

OUTPUT:
  - Photos saved to: photos/PSBP-XXXXX/<photo_id>.jpg
  - photo_credits.json updated with new entries

WHAT IT DOES PER SPECIES:
  1. Finds all observation IDs for the species from the CSV
  2. Hits the iNat API for each observation to get all photos + licenses
  3. Downloads each CC-licensed photo (large size, ~1024px)
  4. Adds a photo_credits.json entry for each new photo
  5. Preserves existing hero — new photos come in as role:["gallery"], hero:false
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
WILDLIFE_JSON = "wildlife_signage.json"
PHOTO_CREDITS_JSON = "photo_credits.json"
OBSERVATIONS_CSV = "observations-750448.csv"
PHOTOS_DIR = "photos"  # base folder — subfolders created per species

# Rate limiting
API_DELAY = 1.0        # seconds between API calls
DOWNLOAD_DELAY = 0.3   # seconds between photo downloads

# Accepted CC licenses (iNat license codes)
CC_LICENSES = {
    "cc-by", "cc-by-nc", "cc-by-sa", "cc-by-nc-sa",
    "cc-by-nd", "cc-by-nc-nd", "cc0",
    # iNat sometimes capitalizes
    "CC-BY", "CC-BY-NC", "CC-BY-SA", "CC-BY-NC-SA",
    "CC-BY-ND", "CC-BY-NC-ND", "CC0",
    "CC BY", "CC BY-NC",  # space variants
}


# ---------------------------------------------------------------------------
# API + DOWNLOAD HELPERS
# ---------------------------------------------------------------------------

def inat_get(url):
    """GET from iNat API, return parsed JSON."""
    headers = {
        "Accept": "application/json",
        "User-Agent": "PSBP-PhotoDownloader/1.0 (palmasolabp.org)"
    }
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        print(f"    HTTP {e.code}: {e.read().decode('utf-8', errors='replace')[:200]}")
        return None


def get_observation_photos(obs_id):
    """Fetch full observation from iNat API, return list of photo dicts."""
    url = f"https://api.inaturalist.org/v1/observations/{obs_id}"
    data = inat_get(url)
    if not data or not data.get("results"):
        return [], None, None
    obs = data["results"][0]
    observer = obs.get("user", {}).get("login", "unknown")
    obs_url = f"https://www.inaturalist.org/observations/{obs_id}"
    photos = []
    for p in obs.get("photos", []):
        photo_url = p.get("url", "")
        # iNat returns "square" size by default — swap to "large"
        large_url = photo_url.replace("/square.", "/original.")
        license_code = p.get("license_code") or ""
        attribution = p.get("attribution", "")
        photos.append({
            "photo_id": str(p.get("id", "")),
            "large_url": large_url,
            "license_code": license_code,
            "attribution": attribution,
            "observer": observer,
        })
    return photos, observer, obs_url


def download_photo(url, dest_path):
    """Download a photo to disk. Returns True on success."""
    headers = {"User-Agent": "PSBP-PhotoDownloader/1.0"}
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req) as resp:
            with open(dest_path, "wb") as f:
                f.write(resp.read())
        return True
    except Exception as e:
        print(f"    Download failed: {e}")
        return False


# ---------------------------------------------------------------------------
# MAIN LOGIC
# ---------------------------------------------------------------------------

def build_obs_index(csv_path):
    """Build taxon_id → [obs_id, ...] from the CSV, plus subspecies rollups."""
    rows = list(csv.DictReader(open(csv_path, encoding="utf-8")))
    index = defaultdict(set)
    for r in rows:
        tid = r.get("taxon_id", "").strip()
        oid = r.get("id", "").strip()
        if tid and oid:
            index[int(tid)].add(oid)

    # Also index by binomial for subspecies rollup
    sci_index = defaultdict(set)
    for r in rows:
        sci = r.get("scientific_name", "").strip().split()
        oid = r.get("id", "").strip()
        if len(sci) >= 2 and oid:
            sci_index[f"{sci[0]} {sci[1]}"].add(oid)

    return index, sci_index, rows


def process_species(species_entry, obs_ids, photo_credits, dry_run=False):
    """
    Download all licensed photos for one species.
    Returns list of new photo_credits entries.
    """
    psbp_id = species_entry["id"]
    common_name = species_entry["common_name"]
    sci_name = species_entry["scientific_name"]
    animal_group = species_entry.get("animal_group", "")

    # Determine subfolder — architecture says just the ID
    subfolder = os.path.join(PHOTOS_DIR, psbp_id)

    # What photo_ids are already in the registry?
    existing_photo_ids = set()
    has_hero = False
    for pc in photo_credits:
        if pc.get("psbp_id") == psbp_id:
            pid = pc.get("photo_id")
            if pid:
                existing_photo_ids.add(str(pid))
            if pc.get("hero"):
                has_hero = True

    print(f"\n{'='*70}")
    print(f"{psbp_id} — {common_name} ({sci_name})")
    print(f"  Observations to check: {len(obs_ids)}")
    print(f"  Already in registry: {len(existing_photo_ids)} photos")
    print(f"  Has hero: {has_hero}")
    print(f"  Subfolder: {subfolder}/")

    if not dry_run:
        os.makedirs(subfolder, exist_ok=True)

    new_entries = []
    photos_downloaded = 0
    photos_skipped_license = 0
    photos_skipped_existing = 0

    for i, obs_id in enumerate(sorted(obs_ids)):
        print(f"  [{i+1}/{len(obs_ids)}] Observation #{obs_id}...", end=" ")
        photos, observer, obs_url = get_observation_photos(obs_id)

        if not photos:
            print("no photos")
            time.sleep(API_DELAY)
            continue

        print(f"{len(photos)} photo(s)")

        for p in photos:
            photo_id = p["photo_id"]
            license_code = p["license_code"]

            # Already in registry?
            if photo_id in existing_photo_ids:
                photos_skipped_existing += 1
                continue

            # License check
            if not license_code or license_code.lower() not in {lc.lower() for lc in CC_LICENSES}:
                photos_skipped_license += 1
                print(f"    photo {photo_id}: skipped (license: {license_code or 'none'})")
                continue

            # Determine filename and path
            filename = f"{photo_id}.jpg"
            dest_path = os.path.join(subfolder, filename)

            # Determine role
            # First licensed photo becomes hero if none exists yet
            is_first = not has_hero and photos_downloaded == 0 and not new_entries
            if is_first:
                role = ["whole", "gallery"]
                primary_for = ["whole"]
                hero = True
            else:
                role = ["gallery"]
                primary_for = []
                hero = False

            # Download
            if not dry_run:
                print(f"    photo {photo_id}: downloading...", end=" ")
                ok = download_photo(p["large_url"], dest_path)
                if ok:
                    print(f"✓ ({filename})")
                    photos_downloaded += 1
                else:
                    continue
                time.sleep(DOWNLOAD_DELAY)
            else:
                print(f"    photo {photo_id}: would download → {dest_path}  (hero={hero})")
                photos_downloaded += 1

            # Build credit entry
            credit_line = f"© {observer} ({license_code.upper()}), via iNaturalist"
            entry = {
                "psbp_id": psbp_id,
                "type": "Wildlife",
                "common_name": common_name,
                "scientific_name": sci_name,
                "role": role,
                "primary_for": primary_for,
                "hero": hero,
                "focus": "50% 50%" if hero else None,
                "tags": [],
                "photographer": observer,
                "license": license_code.upper() if license_code else "",
                "publish_ok": True,
                "status": "OK",
                "credit_line": credit_line,
                "photo_url": p["large_url"],
                "source_url": obs_url,
                "observation_id": obs_id,
                "photo_id": photo_id,
                "filename": filename,
                "used_by": []
            }
            new_entries.append(entry)
            existing_photo_ids.add(photo_id)  # prevent re-processing

        time.sleep(API_DELAY)

    print(f"\n  Summary for {common_name}:")
    print(f"    New photos downloaded: {photos_downloaded}")
    print(f"    Skipped (already in registry): {photos_skipped_existing}")
    print(f"    Skipped (no CC license): {photos_skipped_license}")
    if new_entries:
        heroes = [e for e in new_entries if e["hero"]]
        if heroes:
            print(f"    New hero assigned: {heroes[0]['filename']} by {heroes[0]['photographer']}")

    return new_entries


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Download species photos from iNaturalist")
    parser.add_argument("--species", help="Comma-separated PSBP IDs (e.g., PSBP-99999,PSBP-99998)")
    parser.add_argument("--all", action="store_true", help="Process all species in the JSON")
    parser.add_argument("--dry-run", action="store_true", help="Show what would happen without downloading")
    args = parser.parse_args()

    if not args.species and not args.all:
        print("Usage: python3 download_species_photos.py --species PSBP-99999")
        print("       python3 download_species_photos.py --all")
        print("       Add --dry-run to preview without downloading")
        sys.exit(1)

    # Load data
    ws = json.load(open(WILDLIFE_JSON, encoding="utf-8"))
    pc_data = json.load(open(PHOTO_CREDITS_JSON, encoding="utf-8"))
    photo_credits = pc_data["photos"]

    # Build observation index from CSV
    obs_index, sci_index, csv_rows = build_obs_index(OBSERVATIONS_CSV)

    # Determine which species to process
    if args.all:
        target_ids = [s["id"] for s in ws["species"]]
    else:
        target_ids = [x.strip() for x in args.species.split(",")]

    species_map = {s["id"]: s for s in ws["species"]}
    targets = []
    for tid in target_ids:
        if tid in species_map:
            targets.append(species_map[tid])
        else:
            print(f"WARNING: {tid} not found in {WILDLIFE_JSON}")

    print("=" * 70)
    print(f"PSBP Photo Downloader — {'DRY RUN' if args.dry_run else 'LIVE'}")
    print(f"Species to process: {len(targets)}")
    print("=" * 70)

    all_new_entries = []

    for sp in targets:
        taxon_id = sp["inat_taxon_id"]

        # Get observation IDs from CSV (direct taxon match)
        obs_ids = set(obs_index.get(taxon_id, set()))

        # Also get subspecies rollups
        sci_parts = sp["scientific_name"].split()
        if len(sci_parts) >= 2:
            binom = f"{sci_parts[0]} {sci_parts[1]}"
            obs_ids |= sci_index.get(binom, set())

        if not obs_ids:
            print(f"\n{sp['id']} — {sp['common_name']}: no observations in CSV, skipping")
            continue

        new_entries = process_species(sp, obs_ids, photo_credits, dry_run=args.dry_run)
        all_new_entries.extend(new_entries)
        # Add to running list so next species sees them
        photo_credits.extend(new_entries)

    # Save updated photo_credits.json
    if all_new_entries and not args.dry_run:
        pc_data["photos"] = photo_credits
        pc_data["meta"]["photo_count"] = len(photo_credits)
        pc_data["meta"]["generated"] = "2026-06-19"

        with open(PHOTO_CREDITS_JSON, "w", encoding="utf-8") as f:
            json.dump(pc_data, f, indent=2, ensure_ascii=False)

        print(f"\n{'='*70}")
        print(f"photo_credits.json updated: {len(all_new_entries)} new entries added")
        print(f"Total photos in registry: {len(photo_credits)}")
    elif all_new_entries and args.dry_run:
        print(f"\n{'='*70}")
        print(f"DRY RUN: would add {len(all_new_entries)} entries to photo_credits.json")
    else:
        print(f"\n{'='*70}")
        print("No new photos to add.")

    print("Done.")


if __name__ == "__main__":
    main()
