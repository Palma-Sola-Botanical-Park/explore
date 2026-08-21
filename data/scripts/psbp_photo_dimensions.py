#!/usr/bin/env python3
"""
psbp_photo_dimensions.py — ask iNaturalist how big each photo actually is.

WHY
---
The screen crops photographs hard: full-bleed to 16:9, vertical panels to a
fifth of the frame, polaroids to a square. A portrait shot cropped to 16:9
loses its subject, and no amount of curation prevents that — only knowing the
shape does. Store width and height once and the display can send portraits to
the tall panels and landscapes to the wide ones, automatically, forever.

It also records the true pixel size, so a photo too small to fill a 4K panel
can be kept off the full-bleed slides.

WHAT IT DOES
------------
Adds three fields to each row of photo_credits.json:

    w            original width in pixels
    h            original height in pixels
    orient       "landscape" | "portrait" | "square"

Nothing else is touched. Rows that already have w/h are skipped, so it is safe
to re-run whenever new photos are promoted — it only fetches what is missing.

It does NOT set the `screen` opt-out flag. That is a judgement about whether a
photograph is worth showing eight feet tall, and a script has no business
making it. What it will do is print a CANDIDATES list at the end — photos whose
dimensions suggest they may not survive the screen — for a human to look at.

EFFICIENCY
----------
iNaturalist's API returns photo dimensions on the OBSERVATION, and accepts 200
observation ids per request. 1,069 photos live on 746 observations, so a full
run is about 4 requests, not 1,069.

USAGE
-----
    python3 psbp_photo_dimensions.py                 # dry run, reports only
    python3 psbp_photo_dimensions.py --write         # write the fields
    python3 psbp_photo_dimensions.py --write --all   # re-fetch even existing

Set a contact address below. iNaturalist asks that API clients identify
themselves, and an anonymous script is the first thing they throttle.
"""

import argparse
import json
import shutil
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

# ── please set this ────────────────────────────────────────────────────────
CONTACT = "Palma Sola Botanical Park <randy@palmasolabp.org>"

API = "https://api.inaturalist.org/v1/observations"
BATCH = 200           # ids per request — the API's documented maximum
PAUSE = 1.2           # seconds between requests; iNat asks for ~1/sec


def find_repo():
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import psbp_common                                    # noqa: F401
        return Path(psbp_common.REPO)
    except Exception:
        here = Path(__file__).resolve()
        for parent in [here.parent, *here.parents]:
            if (parent / "data" / "sources" / "photo_credits.json").exists():
                return parent
    return None


def fetch(ids):
    """One request, up to BATCH observation ids."""
    url = f"{API}?id={','.join(str(i) for i in ids)}&per_page={len(ids)}"
    req = urllib.request.Request(url, headers={
        "User-Agent": f"psbp-photo-dimensions/1.0 ({CONTACT})",
        "Accept": "application/json",
    })
    with urllib.request.urlopen(req, timeout=45) as r:
        return json.loads(r.read().decode("utf-8"))


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--write", action="store_true", help="save the fields (default is a dry run)")
    ap.add_argument("--all", action="store_true", help="re-fetch rows that already have w/h")
    ap.add_argument("--limit", type=int, help="stop after N observations, for testing")
    args = ap.parse_args()

    repo = find_repo()
    if repo is None:
        sys.exit("Could not find the repo. Run from inside it, or beside psbp_common.py.")
    path = repo / "data" / "sources" / "photo_credits.json"

    blob = json.loads(path.read_text(encoding="utf-8"))
    photos = blob["photos"]

    todo = [p for p in photos if args.all or not (p.get("w") and p.get("h"))]
    obs_ids = sorted({str(p["observation_id"]) for p in todo if p.get("observation_id")})
    if args.limit:
        obs_ids = obs_ids[:args.limit]

    print(f"\n  photo_credits.json — {len(photos)} photos")
    print(f"  {len(todo)} need dimensions, across {len(obs_ids)} observations")
    print(f"  ≈ {(len(obs_ids)+BATCH-1)//BATCH} request(s) at {BATCH} ids each\n")
    if not obs_ids:
        print("  Nothing to do.\n")
        return 0

    # photo_id -> (w, h)
    sizes, missing_obs = {}, []
    for i in range(0, len(obs_ids), BATCH):
        chunk = obs_ids[i:i+BATCH]
        print(f"  fetching {i+1}–{i+len(chunk)} of {len(obs_ids)} …", end=" ", flush=True)
        try:
            data = fetch(chunk)
        except urllib.error.HTTPError as exc:
            print(f"HTTP {exc.code}")
            if exc.code == 429:
                print("  Rate limited. Wait a few minutes and re-run — finished rows are kept.")
            break
        except Exception as exc:                                  # noqa: BLE001
            print(f"failed: {exc}")
            break

        seen = set()
        for ob in data.get("results", []):
            seen.add(str(ob.get("id")))
            for ph in ob.get("photos", []) or []:
                dim = ph.get("original_dimensions") or {}
                if dim.get("width") and dim.get("height"):
                    sizes[str(ph.get("id"))] = (dim["width"], dim["height"])
        missing_obs.extend(set(chunk) - seen)
        print(f"{len(data.get('results', []))} observations")
        if i + BATCH < len(obs_ids):
            time.sleep(PAUSE)

    # ── apply ──────────────────────────────────────────────────────────────
    applied, unmatched = 0, []
    for p in todo:
        wh = sizes.get(str(p.get("photo_id")))
        if not wh:
            unmatched.append(p.get("photo_id"))
            continue
        w, h = wh
        p["w"], p["h"] = w, h
        ratio = w / h if h else 1
        p["orient"] = ("landscape" if ratio > 1.15
                       else "portrait" if ratio < 0.87
                       else "square")
        applied += 1

    print(f"\n  matched {applied} photo(s)")
    if unmatched:
        print(f"  {len(unmatched)} not found in the API response "
              f"(deleted from iNat, or the observation is private): {unmatched[:6]}")
    if missing_obs:
        print(f"  {len(missing_obs)} observation(s) returned nothing: {missing_obs[:6]}")

    # ── what the shapes look like ──────────────────────────────────────────
    have = [p for p in photos if p.get("w") and p.get("h")]
    if have:
        counts = {}
        for p in have:
            counts[p["orient"]] = counts.get(p["orient"], 0) + 1
        print("\n  orientation: " + " · ".join(f"{k} {v}" for k, v in sorted(counts.items())))

        # What matters is pixels in the direction that gets STRETCHED, not the
        # short edge. A 1152x2048 phone photo is excellent in a vertical panel
        # and soft across a 4K width — same file, two verdicts.
        soft_wide = [p for p in have if p["w"] < 1800]                     # full-bleed
        soft_tall = [p for p in have if p["h"] < 1400]                     # vertical panel
        extreme   = [p for p in have if p["h"] and (p["w"] / p["h"] < 0.45
                                                    or p["w"] / p["h"] > 3.0)]

        print("\n  CANDIDATES for a `screen: no` — a human should look, not a script:")
        print(f"    {len(soft_wide):4d} under 1800px wide — soft on a full-bleed 4K slide")
        print(f"    {len(soft_tall):4d} under 1400px tall — soft in a vertical panel")
        print(f"    {len(extreme):4d} extreme shape — awkward in every layout")
        print("    (the first two are ORIENTATION problems, not quality ones — the")
        print("     display now routes portraits to tall panels and landscapes to wide")
        print("     slides, so most of these need no action at all)")
        for label, rows in (("soft-wide", soft_wide), ("soft-tall", soft_tall),
                            ("extreme", extreme)):
            for p in rows[:4]:
                print(f"      [{label}] {p['psbp_id']} {p.get('common_name','')[:24]:26s} "
                      f"{p['w']}×{p['h']}  {p.get('photographer_name') or p.get('photographer')}")

    if not args.write:
        print("\n  Dry run — nothing written. Re-run with --write.\n")
        return 0

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = path.with_suffix(path.suffix + f".bak-{stamp}")
    shutil.copy2(path, backup)
    blob.setdefault("meta", {})["updated"] = datetime.now().isoformat(timespec="seconds")
    blob["meta"]["note"] = (blob["meta"].get("note", "") +
                            f" | {datetime.now():%Y-%m-%d}: added w/h/orient from the "
                            f"iNaturalist API for {applied} photos.")
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(blob, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)
    print(f"\n  ✓ written  (backup: {backup.name})\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
