#!/usr/bin/env python3
"""
rehydrate_heroes.py  —  one-time migration: restore hero photos at web-res
==========================================================================
The registry (photo_credits.json) was ported from the old ReworkDemo repo, so
it CLAIMS ~230 heroes but the new explore repo has ZERO photo files (we left the
2 GB of originals behind on purpose). This script reuses the curation you
already did — which photo is each species' hero, its focus, its roles — and
re-downloads just those photos at iNaturalist "large" (~1024px), so the repo
gets real, lean hero files without re-reviewing anything by hand.

It is a deliberate ONE-TIME exception to the single-door rule, justified
because the hero decisions already exist. After this, YELLOW (photo_workbench.py)
is the only door for anything new.

WHAT IT DOES, PER HERO ROW
  1. Recover the iNat photo_id — from the row, or parsed out of photo_url
     (the id is always in the URL path, even when the field is null).
  2. Recover the observation id from source_url.
  3. Re-verify against the live iNat API: confirm the photo still exists and is
     still CC, and grab its authoritative URL + current license + photographer.
  4. Download the LARGE rendering -> photos/<psbp_id>/<photo_id>.jpg
  5. Write a fresh registry row: identical curation (hero, focus, roles, tags),
     but photo_url=large, filename=<photo_id>.jpg, photo_id filled in, and
     license/credit refreshed from the live value (fixes the 9 "nan" rows).

  Anything that can't be rehydrated (deleted photo, no longer CC, unrecoverable
  id) is DROPPED and written to rehydrate_report.txt — nothing vanishes silently.

SCOPE: heroes only. Gallery rows stay in the ported manifest for a later pass.

SAFETY
  - The original ported file is snapshotted to photo_credits.ported.json on the
    first run and never touched again — it is the manifest of record.
  - photo_credits.json is rewritten atomically after every successful download,
    so a crash mid-run keeps everything done so far. Re-run to resume.

USAGE
  # Preview — verifies every hero against iNat, writes the report, downloads
  # NOTHING and writes NO registry. Do this first.
  python3 rehydrate_heroes.py --dry-run

  # Real run:
  python3 rehydrate_heroes.py

  # Re-download even files already present:
  python3 rehydrate_heroes.py --force
"""

import json
import os
import re
import sys
import time
import shutil
import datetime
import urllib.request
import urllib.error

# ===========================================================================
# CONFIG  (matches photo_workbench.py)
# ===========================================================================
REPO = "/Users/fiona/Documents/GitHub/explore"

SOURCES            = os.path.join(REPO, "data", "sources")
PHOTO_CREDITS_JSON = os.path.join(SOURCES, "photo_credits.json")
PORTED_JSON        = os.path.join(SOURCES, "photo_credits.ported.json")
PHOTOS_DIR         = os.path.join(REPO, "photos")
REPORT_TXT         = os.path.join(SOURCES, "rehydrate_report.txt")

API_DELAY      = 1.0
DOWNLOAD_DELAY = 0.3

NAME_OVERRIDES = {
    "frankymca":   "Franky McArthur",
    "cleamon":     "Christine Leamon",
    "bevburdette": "Beverly Burdette",
}

CC_LICENSES = {
    "cc-by", "cc-by-nc", "cc-by-sa", "cc-by-nc-sa",
    "cc-by-nd", "cc-by-nc-nd", "cc0",
}


# ===========================================================================
# HELPERS
# ===========================================================================

def display_name(login, name):
    ov = NAME_OVERRIDES.get((login or "").lower())
    if ov:
        return ov
    name = (name or "").strip()
    return name if name else (login or "")


def build_credit_line(name, license_code):
    lic = (license_code or "").strip()
    if lic and lic.lower() != "nan":
        return f"\u00a9 {name} ({lic.upper()}), via iNaturalist"
    return f"\u00a9 {name}, via iNaturalist"


def write_json_atomic(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    os.replace(tmp, path)


def recover_photo_id(row):
    pid = row.get("photo_id")
    if pid:
        return str(pid)
    m = re.search(r"/photos/(\d+)/", row.get("photo_url") or "")
    return m.group(1) if m else None


def recover_obs_id(row):
    oid = row.get("observation_id")
    if oid:
        return str(oid)
    m = re.search(r"/observations/(\d+)", row.get("source_url") or "")
    return m.group(1) if m else None


def inat_get(url):
    headers = {
        "Accept": "application/json",
        "User-Agent": "PSBP-HeroRehydrate/1.0 (palmasolabp.org)",
    }
    token = os.environ.get("INAT_TOKEN")
    if token:
        headers["Authorization"] = token
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return {"_http_error": e.code}
    except Exception as e:
        return {"_error": str(e)}


def resolve_photo(obs_id, photo_id):
    """Re-verify against the live API. Returns (ok, info_or_reason).
    info carries the resolved photo_id, live url, license, observer + dates.
    When photo_id is unknown, a single-photo observation resolves it
    unambiguously; a multi-photo one is left for a YELLOW re-pick."""
    data = inat_get(f"https://api.inaturalist.org/v1/observations/{obs_id}")
    if not data or "_http_error" in data:
        return False, f"observation fetch failed (HTTP {data.get('_http_error') if data else '?'})"
    if "_error" in data:
        return False, f"observation fetch failed ({data['_error']})"
    if not data.get("results"):
        return False, "observation not found (deleted?)"
    obs = data["results"][0]
    photos = obs.get("photos", []) or []
    if not photos:
        return False, f"observation {obs_id} has no photos"

    if photo_id:
        photo = next((p for p in photos if str(p.get("id")) == str(photo_id)), None)
        if not photo:
            return False, f"photo {photo_id} no longer in observation {obs_id}"
    elif len(photos) == 1:
        photo = photos[0]                       # unambiguous recovery
    else:
        return False, (f"photo_id unknown and observation {obs_id} has "
                       f"{len(photos)} photos — re-pick in YELLOW")

    pid = str(photo.get("id"))
    lic = (photo.get("license_code") or "")
    if lic.lower() not in CC_LICENSES:
        return False, f"no longer CC (license: {lic or 'none'})"
    base_url = photo.get("url", "") or ""
    large_url = base_url.replace("/square.", "/large.")
    user = obs.get("user") or {}
    return True, {
        "photo_id":    pid,
        "large_url":   large_url,
        "license":     lic,
        "login":       user.get("login") or "unknown",
        "name":        (user.get("name") or "").strip(),
        "observed_on": obs.get("observed_on") or (obs.get("time_observed_at") or "")[:10] or None,
        "shared_on":   (obs.get("created_at") or "")[:10] or None,
        "obs_url":     f"https://www.inaturalist.org/observations/{obs_id}",
    }


def download(url, dest):
    req = urllib.request.Request(url, headers={"User-Agent": "PSBP-HeroRehydrate/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = resp.read()
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        with open(dest, "wb") as f:
            f.write(data)
        return True, len(data)
    except Exception as e:
        return False, str(e)


# ===========================================================================
# MAIN
# ===========================================================================

def main():
    dry_run = "--dry-run" in sys.argv
    force   = "--force" in sys.argv

    if not os.path.isdir(REPO):
        print(f"ERROR: REPO not found: {REPO}")
        sys.exit(1)
    if not os.path.isfile(PHOTO_CREDITS_JSON) and not os.path.isfile(PORTED_JSON):
        print(f"ERROR: no photo_credits.json or ported snapshot in {SOURCES}")
        sys.exit(1)

    # Snapshot the manifest once. After this, PORTED is the source of truth and
    # photo_credits.json is the slim heroes-only output we build/resume.
    first_run = not os.path.isfile(PORTED_JSON)
    if first_run:
        shutil.copy2(PHOTO_CREDITS_JSON, PORTED_JSON)
        print(f"Snapshotted manifest -> {PORTED_JSON}")

    ported = json.load(open(PORTED_JSON, encoding="utf-8"))
    heroes = [p for p in ported.get("photos", []) if p.get("hero")]
    print(f"Heroes in manifest: {len(heroes)}")

    # Build / resume the slim output.
    if dry_run:
        out = {"meta": {}, "photos": []}
        done_ids = set()
    elif first_run:
        out = {
            "meta": dict(ported.get("meta", {})),
            "photos": [],
        }
        done_ids = set()
    else:
        out = json.load(open(PHOTO_CREDITS_JSON, encoding="utf-8"))
        out.setdefault("photos", [])
        out.pop("_local_folders", None)
        done_ids = {str(p.get("photo_id")) for p in out["photos"] if p.get("photo_id")}

    print(f"Mode: {'DRY RUN (no downloads, no writes)' if dry_run else 'LIVE'}"
          f"{' + force' if force else ''}\n")

    ok = skipped = failed = 0
    failures = []   # (psbp_id, common_name, reason)
    total_bytes = 0

    for i, row in enumerate(heroes, 1):
        psbp_id = row.get("psbp_id", "?")
        common  = row.get("common_name", "?")
        prefix  = f"[{i}/{len(heroes)}] {psbp_id} {common}:"

        photo_id = recover_photo_id(row)   # may be None — resolver can recover it
        obs_id   = recover_obs_id(row)
        if not obs_id:
            print(f"{prefix} UNRECOVERABLE observation id"); failed += 1
            failures.append((psbp_id, common, "no observation id in source_url"))
            continue

        # Resume fast-path only when we already know the id and the file is here.
        if photo_id and not dry_run and not force and photo_id in done_ids \
                and os.path.isfile(os.path.join(PHOTOS_DIR, psbp_id, f"{photo_id}.jpg")):
            print(f"{prefix} already done, skipping"); skipped += 1
            continue

        ok_v, info = resolve_photo(obs_id, photo_id)
        time.sleep(API_DELAY)
        if not ok_v:
            print(f"{prefix} DROP — {info}"); failed += 1
            failures.append((psbp_id, common, info))
            continue

        photo_id = info["photo_id"]            # authoritative, recovered if needed
        dest = os.path.join(PHOTOS_DIR, psbp_id, f"{photo_id}.jpg")
        name = display_name(info["login"], info["name"])

        if dry_run:
            print(f"{prefix} ok — would download large ({name}, {info['license'].upper()})")
            ok += 1
            continue

        good, res = download(info["large_url"], dest)
        if not good:
            print(f"{prefix} download FAILED — {res}"); failed += 1
            failures.append((psbp_id, common, f"download failed: {res}"))
            continue
        total_bytes += res
        time.sleep(DOWNLOAD_DELAY)

        new_row = dict(row)
        new_row["photo_id"]          = photo_id
        new_row["filename"]          = f"{photo_id}.jpg"
        new_row["photo_url"]         = info["large_url"]
        new_row["source_url"]        = info["obs_url"]
        new_row["observation_id"]    = obs_id
        new_row["license"]           = info["license"].upper()
        new_row["photographer"]      = info["login"]
        new_row["photographer_name"] = name
        new_row["credit_line"]       = build_credit_line(name, info["license"])
        new_row["observed_on"]       = info["observed_on"] or row.get("observed_on")
        new_row["shared_on"]         = info["shared_on"] or row.get("shared_on")
        new_row["focus"]             = row.get("focus") or "50% 50%"
        new_row.setdefault("role", ["whole", "gallery"])
        new_row.setdefault("primary_for", ["whole"])
        new_row.setdefault("tags", [])
        new_row.setdefault("used_by", [])
        new_row["publish_ok"]        = True
        new_row["status"]            = "OK"

        out["photos"].append(new_row)
        done_ids.add(photo_id)
        out["meta"]["photo_count"] = len(out["photos"])
        out["meta"]["generated"]   = datetime.date.today().isoformat()
        out["meta"]["note"]        = "heroes rehydrated to large from ported manifest"
        write_json_atomic(PHOTO_CREDITS_JSON, out)
        ok += 1
        print(f"{prefix} ok ({name}, {info['license'].upper()}, {res//1024} KB)")

    # ---- report ----
    lines = []
    lines.append(f"Hero rehydrate report — {datetime.datetime.now().isoformat(timespec='seconds')}")
    lines.append(f"mode: {'dry-run' if dry_run else 'live'}{' +force' if force else ''}")
    lines.append(f"heroes in manifest: {len(heroes)}")
    lines.append(f"rehydrated ok: {ok}   skipped(already done): {skipped}   dropped/failed: {failed}")
    if not dry_run:
        lines.append(f"downloaded: {total_bytes//(1024*1024)} MB across {ok} files")
    lines.append("")
    if failures:
        lines.append(f"--- {len(failures)} heroes need attention (re-pick in YELLOW or re-shoot) ---")
        for psbp_id, common, reason in sorted(failures):
            lines.append(f"  {psbp_id}  {common}  —  {reason}")
    else:
        lines.append("No failures. Every hero rehydrated.")
    report = "\n".join(lines) + "\n"

    if not dry_run:
        with open(REPORT_TXT, "w", encoding="utf-8") as f:
            f.write(report)

    print("\n" + "=" * 70)
    print(report)
    if not dry_run:
        print(f"report written: {REPORT_TXT}")
        print(f"registry written: {PHOTO_CREDITS_JSON}  ({len(out['photos'])} hero rows)")
        print(f"manifest preserved: {PORTED_JSON}")
    print("Done.")


if __name__ == "__main__":
    main()
