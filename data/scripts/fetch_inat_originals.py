#!/usr/bin/env python3
"""
fetch_inat_originals.py — keep our own full-size copy of every species photo.
==============================================================================
Runs on the Office PC. Double-click "Fetch iNat originals.bat" next to this file.

WHAT IT DOES
    Reads data/sources/photo_credits.json (fresh, every run) and downloads each
    photo at iNat's full size to

        C:\\PSBP\\data\\media\\originals\\inat\\<photo_id>.jpg

    one file per iNat photo, named by its photo ID alone: a photo can move to
    another species, its file never moves. A manifest records which species
    use each photo, with size, checksum, licence and credit:

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
import ssl
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


def _ssl_context():
    """Windows Python trusts only what its own root store already holds, and a
    fresh machine may not hold Amazon's root yet — the first run on Office
    failed every photo with CERTIFICATE_VERIFY_FAILED (2026-09-30). Prefer the
    certifi bundle when it is installed, then the copy pip ships with, then the
    system store. Returns (context, description) so the log says which."""
    for label, loader in (("certifi", lambda: __import__("certifi")),
                          ("pip's certifi", lambda: __import__("pip._vendor.certifi", fromlist=["where"]))):
        try:
            mod = loader()
            return ssl.create_default_context(cafile=mod.where()), label
        except Exception:
            continue
    return ssl.create_default_context(), "system certificate store"


SSL_CTX, SSL_SOURCE = _ssl_context()


def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=60, context=SSL_CTX) as r:
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
    # One file per iNat photo. Which species use it can change; that lives in
    # the manifest, never in the path.
    by_photo = {}
    for p in photos:
        if p.get("photo_url") and p.get("psbp_id") and p.get("photo_id"):
            by_photo.setdefault(str(p["photo_id"]), []).append(p)
    todo_ids = list(by_photo)
    if args.limit:
        todo_ids = todo_ids[:args.limit]

    manifest = {}
    if manifest_path.exists():
        for m in json.loads(manifest_path.read_text(encoding="utf-8")).get("files", []):
            manifest[m["file"]] = m

    def rel_for(pid):
        return f"originals/inat/{pid}.jpg"

    todo = sum(1 for pid in todo_ids if not (args.dest / rel_for(pid)).exists())
    log(f"{len(todo_ids)} photos listed in photo_credits.json -> {originals / 'inat'}")
    log(f"{todo} still to fetch, one every {args.gap:g} s: about {todo * args.gap / 3600:.1f} hours. "
        "Safe to close this window; it picks up where it left off.")
    log(f"HTTPS certificates from {SSL_SOURCE}")
    got = skipped = failed = 0
    total_bytes = 0
    last_request = 0.0

    for i, pid in enumerate(todo_ids, 1):
        uses = by_photo[pid]
        p = uses[0]
        rel = rel_for(pid)
        target = args.dest / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        names = ", ".join(u.get("common_name") or u["psbp_id"] for u in uses)

        if target.exists() and target.stat().st_size > 0:
            skipped += 1
        else:
            data = None
            for url in original_url(p["photo_url"]):
                wait = args.gap - (time.time() - last_request)
                if wait > 0:
                    time.sleep(wait)
                last_request = time.time()
                try:
                    data = fetch(url)
                    break
                except (urllib.error.URLError, OSError) as e:
                    log(f"  {pid}: {url.rsplit('/', 1)[1]} failed ({e})")
            if not data:
                failed += 1
                log(f"FAILED {pid} {names}")
                continue
            part = target.with_suffix(".part")
            part.write_bytes(data)
            os.replace(part, target)
            got += 1
            total_bytes += len(data)
            log(f"[{i}/{len(todo_ids)}] {pid}  {len(data) // 1024} KB  {names}")

        size = target.stat().st_size
        old = manifest.get(rel) or {}
        manifest[rel] = {
            "file": rel,
            "photo_id": p["photo_id"],
            "observation_id": p.get("observation_id"),
            "species": [{"psbp_id": u["psbp_id"], "common_name": u.get("common_name")} for u in uses],
            "license": p.get("license"),
            "credit_line": p.get("credit_line"),
            "photographer": p.get("photographer"),
            "source_url": p.get("source_url"),
            "photo_url": p.get("photo_url"),
            "bytes": size,
            "sha256": old["sha256"] if old.get("bytes") == size and old.get("sha256") else sha256_of(target),
            "fetched_at": old.get("fetched_at") or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%MZ"),
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
