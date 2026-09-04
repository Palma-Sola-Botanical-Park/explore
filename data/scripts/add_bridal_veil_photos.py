#!/usr/bin/env python3
"""
add_bridal_veil_photos.py  —  one-off helper

Adds EVERY photo from one or more iNaturalist observations into
photo_credits.json, and records a matching "promoted" verdict in
photo_workbench.json so your triage scan never resurfaces them.

Why a script instead of hand-editing: it reuses your own psbp_common helpers
(same paths, same credit-line formatting, atomic writes) and derives every
field the way _apply_triage_decision does, so the rows come out identical to
what the Triage UI would have written — just without you touching the JSON.

WHAT IT DOES (per photo, mirroring species_manager's promote path):
  * builds a photo_credits row with the full schema
  * adds every photo as a GALLERY / virtual row (hero = false, served from the
    iNat CDN, no file to download). Nothing is set as the hero — see the note
    at the bottom of this file for why, and how to pick one later.
  * writes a "promoted" ledger entry keyed by photo_id
  * bumps meta.photo_count
  * is idempotent: a photo_id already in photo_credits.json is skipped

SAFE BY DEFAULT: prints a plan and writes NOTHING unless you pass --write.
On --write it first copies each JSON to <file>.bak-<timestamp>.

USAGE (from the repo root):
    python data/scripts/add_bridal_veil_photos.py            # preview only
    python data/scripts/add_bridal_veil_photos.py --write    # actually apply

If any observation is fully obscured and the photos come back empty, set a
curator token first:  export INAT_TOKEN='...'   (same var species_manager uses)
"""

import argparse
import datetime as dt
import json
import os
import shutil
import sys
import urllib.error
import urllib.request

# ── Make psbp_common importable no matter where python is invoked from ────────
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from psbp_common import (            # noqa: E402
    load_json, write_json_atomic, display_name, build_credit_line,
    CC_LICENSES, PHOTO_CREDITS_JSON, PHOTO_WORKBENCH_JSON,
)

# ── EDIT HERE if you reuse this for another species ───────────────────────────
PSBP_ID         = "PSBP-00605"          # Bridal Veil Tree in research.json
COMMON_NAME     = "Bridal Veil Tree"    # research.json has this blank; set it here
SCIENTIFIC_NAME = "Libidibia punctata"
CREDIT_TYPE     = "Plant"
OBSERVATION_IDS = ["365782014", "388487197"]
# ──────────────────────────────────────────────────────────────────────────────


def today():
    return dt.date.today().isoformat()


def inat_get(url):
    """GET JSON from iNat — same headers/token behaviour as _inat_get()."""
    headers = {
        "Accept": "application/json",
        "User-Agent": "PSBP-SpeciesManager/1.0 (palmasolabp.org)",
    }
    token = os.environ.get("INAT_TOKEN")
    if token:
        headers["Authorization"] = token
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        print(f"    HTTP {e.code} fetching {url}: "
              f"{e.read().decode('utf-8', errors='replace')[:160]}")
        return None
    except Exception as e:                                   # noqa: BLE001
        print(f"    request failed for {url}: {e}")
        return None


def sized_url(photo_url, size):
    """Turn any iNat photo URL (square/medium/large/original) into the size we
    want. iNat photo URLs differ only by that path segment, so swap it."""
    for s in ("square", "small", "medium", "large", "original"):
        photo_url = photo_url.replace(f"/{s}.", f"/{size}.")
    return photo_url


def fetch_observation(obs_id):
    data = inat_get(f"https://api.inaturalist.org/v1/observations/{obs_id}")
    if not data or not data.get("results"):
        print(f"  ! obs {obs_id}: no data returned "
              f"(obscured + no token?) — skipping")
        return None
    return data["results"][0]


def build_rows(obs):
    """Return (credit_rows, ledger_entries) for one observation."""
    obs_id  = str(obs.get("id"))
    user    = obs.get("user") or {}
    login   = user.get("login", "")
    name    = display_name(login, user.get("name", ""))
    observed_on = obs.get("observed_on") or ""
    shared_on   = (obs.get("created_at") or "")[:10]
    source_url  = f"https://www.inaturalist.org/observations/{obs_id}"

    credit_rows, ledger = [], []
    photos = obs.get("photos") or []
    if not photos:
        print(f"  ! obs {obs_id}: 0 photos in payload")
        return credit_rows, ledger

    for p in photos:
        pid   = str(p.get("id"))
        base  = p.get("url") or ""                 # usually the square thumb
        lic   = (p.get("license_code") or "")      # e.g. 'cc-by-nc' or '' (ARR)
        large = sized_url(base, "large")
        medium = sized_url(base, "medium")

        if lic and lic.lower() not in CC_LICENSES:
            print(f"    · photo {pid}: license '{lic}' is not Creative Commons "
                  f"— added anyway (it's your own), but note it won't be on the "
                  f"open-data CDN.")

        credit_rows.append({
            "psbp_id":           PSBP_ID,
            "type":              CREDIT_TYPE,
            "common_name":       COMMON_NAME,
            "scientific_name":   SCIENTIFIC_NAME,
            "role":              ["gallery"],
            "primary_for":       [],
            "hero":              False,
            "focus":             None,
            "tags":              [],
            "photographer":      login,
            "photographer_name": name,
            "license":           lic.upper(),
            "publish_ok":        True,
            "status":            "OK",
            "credit_line":       build_credit_line(name, lic),
            "observed_on":       observed_on,
            "shared_on":         shared_on,
            "photo_url":         large,
            "source_url":        source_url,
            "observation_id":    obs_id,
            "photo_id":          pid,
            "filename":          None,          # virtual → served from CDN
            "used_by":           [],
            "virtual":           True,
        })
        ledger.append((pid, {
            "decision":          "promoted",
            "reviewed_on":       today(),
            "psbp_id":           PSBP_ID,
            "obs_id":            obs_id,
            "photographer":      login,
            "photographer_name": name,
            "license":           lic,
            "observed_on":       observed_on,
            "shared_on":         shared_on,
            "thumb_url":         medium,
            "large_url":         large,
            "source_url":        source_url,
            "note":              "hand-added: obscured obs, not caught by scan",
        }))
    return credit_rows, ledger


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true",
                    help="actually modify the JSON files (default: preview only)")
    args = ap.parse_args()

    credits = load_json(PHOTO_CREDITS_JSON, {"meta": {}, "photos": []})
    workbench = load_json(PHOTO_WORKBENCH_JSON, {"meta": {}, "decisions": {}})
    credits.setdefault("photos", [])
    credits.setdefault("meta", {})
    workbench.setdefault("decisions", {})

    existing_ids = {str(p.get("photo_id")) for p in credits["photos"]}

    all_rows, all_ledger, skipped = [], [], []
    for obs_id in OBSERVATION_IDS:
        print(f"\nObservation {obs_id}:")
        obs = fetch_observation(obs_id)
        if not obs:
            continue
        rows, ledger = build_rows(obs)
        for row, (pid, led) in zip(rows, ledger):
            if pid in existing_ids:
                skipped.append(pid)
                print(f"    · photo {pid}: already in photo_credits — skipping")
                continue
            existing_ids.add(pid)
            all_rows.append(row)
            all_ledger.append((pid, led))
            print(f"    + photo {pid}  ({row['license'] or 'no license'})  "
                  f"{row['photo_url']}")

    print(f"\nSummary: {len(all_rows)} new photo(s) to add, "
          f"{len(skipped)} already present.")
    if not all_rows:
        print("Nothing to do.")
        return 0

    if not args.write:
        print("\nPreview only — nothing written. Re-run with --write to apply:")
        print("    python data/scripts/add_bridal_veil_photos.py --write")
        return 0

    # ── apply ────────────────────────────────────────────────────────────────
    # Backups go OUTSIDE the repo — see psbp_common.backup_file(). These used to
    # be written beside the masters, inside a GitHub Pages repo, under a
    # `.bak-<stamp>` name that .gitignore's `*.bak` does not match.
    from psbp_common import backup_file, BACKUP_DIR
    for path in (PHOTO_CREDITS_JSON, PHOTO_WORKBENCH_JSON):
        if os.path.exists(path):
            backup_file(path)
    print(f"\nBacked up both files to {BACKUP_DIR}")

    credits["photos"].extend(all_rows)
    credits["meta"]["photo_count"] = len(credits["photos"])
    for pid, led in all_ledger:
        workbench["decisions"][pid] = led

    write_json_atomic(PHOTO_CREDITS_JSON, credits)
    write_json_atomic(PHOTO_WORKBENCH_JSON, workbench)
    print(f"Wrote {len(all_rows)} photos to photo_credits.json "
          f"(photo_count now {credits['meta']['photo_count']}) "
          f"and {len(all_ledger)} ledger entries to photo_workbench.json.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
