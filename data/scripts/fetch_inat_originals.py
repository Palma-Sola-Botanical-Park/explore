#!/usr/bin/env python3
"""
fetch_inat_originals.py — keep our own full-size copy of every species photo.
==============================================================================
Runs on the Office PC. Double-click "Fetch iNat originals.bat" next to this file.

WHAT IT DOES
    Reads data/sources/photo_credits.json (fresh, every run) and downloads each
    photo at iNat's full size to

        C:\\PSBP\\data\\media\\originals\\PSBP-xxxxx\\<photo_id>.jpg

    and writes a manifest with size, checksum, licence and credit for each file:

        C:\\PSBP\\data\\media\\manifests\\inat_originals.json

    with a log of each run in C:\\PSBP\\logs, beside the controller's logs

    A photo already on disk is skipped, so it is safe to stop (close the window)
    and start again at any time. It deliberately goes slowly: one photo every
    50 seconds, so the whole set takes about a day and iNat never notices us.

WHAT IT WILL NOT DO
    Never writes to the repo. Never touches photo_credits.json. Never deletes.

USAGE
    python fetch_inat_originals.py               # everything
    python fetch_inat_originals.py --limit 20    # first 20, for a test
    python fetch_inat_originals.py --gap 2       # faster, 2 seconds apart
    python fetch_inat_originals.py --dest D:\\somewhere
"""
import argparse
import hashlib
import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
CREDITS = REPO / "data" / "sources" / "photo_credits.json"
DEFAULT_DEST = (Path(r"C:\PSBP\data\media") if os.name == "nt"
                else Path.home() / "PSBP-media")
OFFICE_LOGS = Path(r"C:\PSBP\logs")

USER_AGENT = "PalmaSolaBotanicalPark-media-library/1.0 (palma-sola-botanical-park.github.io)"
GAP_S = 50   # seconds between downloads; ~1,640 photos takes about a day


def original_url(photo_url):
    base, name = photo_url.rsplit("/", 1)
    ext = name.rsplit(".", 1)[1] if "." in name else "jpg"
    return f"{base}/original.{ext}", photo_url


def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read()


def sha256_of(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def write_json_atomic(path, data):
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dest", type=Path, default=DEFAULT_DEST)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--gap", type=float, default=GAP_S)
    args = ap.parse_args()

    originals = args.dest / "originals"
    manifests = args.dest / "manifests"
    logs = OFFICE_LOGS if os.name == "nt" and OFFICE_LOGS.is_dir() else args.dest / "logs"
    for d in (originals, manifests, logs):
        d.mkdir(parents=True, exist_ok=True)
    manifest_path = manifests / "inat_originals.json"
    log_path = logs / f"fetch_inat_originals-{datetime.now():%Y-%m-%d}.log"
    log_f = open(log_path, "a", encoding="utf-8")

    def log(msg):
        line = f"{datetime.now():%H:%M:%S}  {msg}"
        print(line, flush=True)
        log_f.write(line + "\n")
        log_f.flush()

    photos = json.loads(CREDITS.read_text(encoding="utf-8"))["photos"]
    rows = [p for p in photos if p.get("photo_url") and p.get("psbp_id") and p.get("photo_id")]
    if args.limit:
        rows = rows[:args.limit]

    manifest = {}
    if manifest_path.exists():
        for m in json.loads(manifest_path.read_text(encoding="utf-8")).get("files", []):
            manifest[m["file"]] = m

    todo = sum(1 for p in rows if not (args.dest / f"originals/{p['psbp_id']}/{p['photo_id']}.jpg").exists())
    log(f"{len(rows)} photos listed in photo_credits.json -> {originals}")
    log(f"{todo} still to fetch, one every {args.gap:g} s: about {todo * args.gap / 3600:.1f} hours. "
        "Safe to close this window; it picks up where it left off.")
    got = skipped = failed = 0
    total_bytes = 0
    by_photo_id = {}   # a photo used by two species is fetched once, copied once
    last_request = 0.0

    for i, p in enumerate(rows, 1):
        pid = str(p["photo_id"])
        rel = f"originals/{p['psbp_id']}/{pid}.jpg"
        target = args.dest / rel
        target.parent.mkdir(parents=True, exist_ok=True)

        if target.exists() and target.stat().st_size > 0:
            skipped += 1
            by_photo_id.setdefault(pid, target)
            if rel not in manifest:
                manifest[rel] = None   # filled in below
        else:
            data = None
            source = None
            if pid in by_photo_id:
                data = by_photo_id[pid].read_bytes()
                source = "copy"
            else:
                for url in original_url(p["photo_url"]):
                    wait = args.gap - (time.time() - last_request)
                    if wait > 0:
                        time.sleep(wait)
                    last_request = time.time()
                    try:
                        data = fetch(url)
                        source = url
                        break
                    except (urllib.error.URLError, OSError) as e:
                        log(f"  {p['psbp_id']} {pid}: {url.rsplit('/', 1)[1]} failed ({e})")
            if not data:
                failed += 1
                log(f"FAILED {p['psbp_id']} {pid} {p.get('common_name', '')}")
                continue
            part = target.with_suffix(".part")
            part.write_bytes(data)
            os.replace(part, target)
            by_photo_id.setdefault(pid, target)
            got += 1
            total_bytes += len(data)
            manifest[rel] = None
            if source != "copy":
                log(f"[{i}/{len(rows)}] {p['psbp_id']} {pid}  {len(data) // 1024} KB  "
                    f"{p.get('common_name', '')}")

        if manifest[rel] is None:
            manifest[rel] = {
                "file": rel,
                "psbp_id": p["psbp_id"],
                "common_name": p.get("common_name"),
                "photo_id": p["photo_id"],
                "observation_id": p.get("observation_id"),
                "license": p.get("license"),
                "credit_line": p.get("credit_line"),
                "photographer": p.get("photographer"),
                "source_url": p.get("source_url"),
                "photo_url": p.get("photo_url"),
                "bytes": target.stat().st_size,
                "sha256": sha256_of(target),
                "fetched_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%MZ"),
            }
        if i % 50 == 0:
            write_json_atomic(manifest_path, {"files": sorted(manifest.values(), key=lambda m: m["file"])})

    files = sorted(manifest.values(), key=lambda m: m["file"])
    write_json_atomic(manifest_path, {
        "meta": {"updated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%MZ"),
                 "count": len(files),
                 "bytes": sum(m["bytes"] for m in files)},
        "files": files,
    })
    log(f"Done. {got} downloaded ({total_bytes / 1e9:.2f} GB), {skipped} already here, "
        f"{failed} failed. Library holds {len(files)} files, "
        f"{sum(m['bytes'] for m in files) / 1e9:.2f} GB.")
    if failed:
        log("Run it again to retry the failures; finished files are skipped.")
    log_f.close()
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
