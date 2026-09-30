#!/usr/bin/env python3
"""
upload_r2_media.py — put every species photo into the park's R2 media library.
===============================================================================
Runs on the Office PC. Double-click "Upload media to R2.bat" next to this file.
The companion of fetch_inat_originals.py: that one brings the originals down
from iNaturalist, this one makes the web and thumb copies and sends all three
up to Cloudflare R2.

WHAT IT DOES
    For every original in

        C:\\PSBP\\data\\media\\originals\\inat\\<photo_id>.jpg

    it makes two smaller copies (kept on disk, so a rerun never resizes twice)

        C:\\PSBP\\data\\media\\derived\\inat\\<photo_id>\\v1\\web.jpg     ~1,600 px, ~300 KB
        C:\\PSBP\\data\\media\\derived\\inat\\<photo_id>\\v1\\thumb.jpg   ~500 px, ~40 KB

    and uploads the three of them to the bucket at

        inat/<photo_id>/v1/original.jpg
        inat/<photo_id>/v1/web.jpg
        inat/<photo_id>/v1/thumb.jpg

    A file already in the bucket with the same checksum is skipped, so it is
    safe to stop (close the window) and start again. Every upload is verified:
    R2 answers with the checksum of what it stored, and that must match the
    file that was sent. A record of what is up goes in

        C:\\PSBP\\data\\media\\manifests\\r2_uploads.json

    with a log of each run in C:\\PSBP\\logs.

WHAT IT NEEDS
    Three environment variables for the account running it (Windows: Settings
    → System → About → Advanced system settings → Environment Variables):

        R2_ACCOUNT_ID         the 32-character id in the Cloudflare dashboard URL
        R2_ACCESS_KEY_ID      from R2 → Manage API Tokens
        R2_SECRET_ACCESS_KEY  same place, shown once

    Never in the repo, never in a page — the same rule as ANTHROPIC_API_KEY.
    Pillow for the resizing (the .bat installs it if it is missing).

WHAT IT WILL NOT DO
    Never deletes anything, here or in the bucket. Never overwrites a file in
    the bucket that already matches. Never writes to the repo or to
    photo_credits.json. The bucket must be named on purpose: the default is the
    disposable sandbox, and production is a flag you type.

USAGE
    python upload_r2_media.py                       # sandbox, everything
    python upload_r2_media.py --limit 3             # first 3 photos, for a test
    python upload_r2_media.py --only 703963637      # one photo
    python upload_r2_media.py --bucket psbp-public  # production
    python upload_r2_media.py --dry-run             # resize only, upload nothing
    python upload_r2_media.py --gap 5               # slower, 5 s between files
"""
import argparse
import hashlib
import hmac
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_SRC = (Path(r"C:\PSBP\data\media") if os.name == "nt"
               else Path.home() / "PSBP-media")
OFFICE_LOGS = Path(r"C:\PSBP\logs")

DEFAULT_BUCKET = "psbp-sandbox"      # production is --bucket psbp-public, typed on purpose
REVISION = "v1"                      # bump only if a rendition is ever reprocessed; old paths stay
GAP_S = 1.0                          # seconds between uploads; three per photo

# Renditions: longest side in pixels and the size budget. Quality steps down
# until the file fits the budget, so a busy photo costs a little sharpness and
# a plain one keeps it. Originals go up untouched.
WEB_PX, WEB_BUDGET = 1600, 320 * 1024
THUMB_PX, THUMB_BUDGET = 500, 48 * 1024
QUALITIES = (85, 80, 75, 70, 65, 60)

# Short while sizes are still being tuned; a revisioned path can go immutable
# later without any file changing address. See the media plan §3.
CACHE_CONTROL = "public, max-age=3600"


# ── R2 over the S3 API, standard library only ──────────────────────────────
# One PUT and one HEAD is all this needs, so the signing is written out here
# rather than pulling in a client library that would need installing on Office.

def sigv4_authorization(method, path, headers, payload_hash, key_id, secret, now,
                        region="auto", service="s3"):
    """AWS Signature Version 4 for one request. `headers` must already hold
    host and x-amz-date (lower-case names). Checked against Amazon's published
    test vector; see the media plan for the rehearsal log."""
    amz_date = now.strftime("%Y%m%dT%H%M%SZ")
    scope_date = now.strftime("%Y%m%d")
    signed = ";".join(sorted(headers))
    canonical = "\n".join([
        method, path, "",
        "".join(f"{k}:{headers[k].strip()}\n" for k in sorted(headers)),
        signed, payload_hash])
    scope = f"{scope_date}/{region}/{service}/aws4_request"
    to_sign = "\n".join(["AWS4-HMAC-SHA256", amz_date, scope,
                         hashlib.sha256(canonical.encode()).hexdigest()])
    k = ("AWS4" + secret).encode()
    for part in (scope_date, region, service, "aws4_request"):
        k = hmac.new(k, part.encode(), hashlib.sha256).digest()
    signature = hmac.new(k, to_sign.encode(), hashlib.sha256).hexdigest()
    return (f"AWS4-HMAC-SHA256 Credential={key_id}/{scope}, "
            f"SignedHeaders={signed}, Signature={signature}")


class R2:
    def __init__(self, account_id, key_id, secret, bucket):
        self.host = f"{account_id}.r2.cloudflarestorage.com"
        self.key_id, self.secret, self.bucket = key_id, secret, bucket

    def _request(self, method, key, body=b"", extra_headers=None):
        now = datetime.now(timezone.utc)
        payload_hash = hashlib.sha256(body).hexdigest()
        path = "/" + urllib.parse.quote(f"{self.bucket}/{key}", safe="/")
        headers = {"host": self.host,
                   "x-amz-content-sha256": payload_hash,
                   "x-amz-date": now.strftime("%Y%m%dT%H%M%SZ")}
        for k, v in (extra_headers or {}).items():
            headers[k.lower()] = v
        headers["authorization"] = sigv4_authorization(
            method, path, headers, payload_hash, self.key_id, self.secret, now)
        del headers["host"]
        req = urllib.request.Request(f"https://{self.host}{path}", data=body or None,
                                     method=method, headers=headers)
        return urllib.request.urlopen(req, timeout=120)

    def head(self, key):
        """(size, md5) of the object, or None if it is not there."""
        try:
            with self._request("HEAD", key) as r:
                etag = (r.headers.get("ETag") or "").strip('"')
                return int(r.headers.get("Content-Length") or 0), etag
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            raise

    def put(self, key, body, content_type):
        """Upload and return the checksum R2 says it stored."""
        with self._request("PUT", key, body, {"content-type": content_type,
                                              "cache-control": CACHE_CONTROL}) as r:
            return (r.headers.get("ETag") or "").strip('"')


# ── renditions ─────────────────────────────────────────────────────────────

def make_rendition(src, dst, longest_px, budget):
    """Resize the original so its longest side is longest_px, saving at the
    highest quality that fits the budget. Honours the camera's orientation
    tag, drops the rest of the metadata, writes progressive JPEG. Skips work
    already on disk."""
    if dst.exists() and dst.stat().st_size > 0:
        return dst.stat().st_size, False
    from PIL import Image, ImageOps
    dst.parent.mkdir(parents=True, exist_ok=True)
    with Image.open(src) as im:
        im = ImageOps.exif_transpose(im)
        if im.mode not in ("RGB", "L"):
            im = im.convert("RGB")
        im.thumbnail((longest_px, longest_px), Image.LANCZOS)
        tmp = dst.with_suffix(".part")
        for q in QUALITIES:
            im.save(tmp, "JPEG", quality=q, optimize=True, progressive=True)
            if tmp.stat().st_size <= budget:
                break
        os.replace(tmp, dst)
    return dst.stat().st_size, True


def md5_of(data):
    return hashlib.md5(data).hexdigest()


def write_json_atomic(path, data):
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


# ── main ───────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", type=Path, default=DEFAULT_SRC)
    ap.add_argument("--bucket", default=DEFAULT_BUCKET)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--only", help="one photo id")
    ap.add_argument("--gap", type=float, default=GAP_S)
    ap.add_argument("--dry-run", action="store_true", help="make renditions, upload nothing")
    args = ap.parse_args()

    originals = args.src / "originals" / "inat"
    derived = args.src / "derived" / "inat"
    manifests = args.src / "manifests"
    logs = OFFICE_LOGS if os.name == "nt" and OFFICE_LOGS.is_dir() else args.src / "logs"
    for d in (derived, manifests, logs):
        d.mkdir(parents=True, exist_ok=True)
    manifest_path = manifests / "r2_uploads.json"
    log_path = logs / f"upload_r2_media-{datetime.now():%Y-%m-%d}.log"
    log_f = open(log_path, "a", encoding="utf-8")

    def log(msg):
        line = f"{datetime.now():%H:%M:%S}  {msg}"
        print(line, flush=True)
        log_f.write(line + "\n")
        log_f.flush()

    try:
        import PIL  # noqa: F401
    except ImportError:
        log("Pillow is not installed. Run:  python -m pip install pillow   (the .bat does this)")
        return 2

    r2 = None
    if not args.dry_run:
        env = {k: os.environ.get(k, "").strip()
               for k in ("R2_ACCOUNT_ID", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY")}
        missing = [k for k, v in env.items() if not v]
        if missing:
            log("Missing environment variable(s): " + ", ".join(missing)
                + ". Set them for this account, open a new window, run again.")
            return 2
        r2 = R2(env["R2_ACCOUNT_ID"], env["R2_ACCESS_KEY_ID"], env["R2_SECRET_ACCESS_KEY"], args.bucket)

    if not originals.is_dir():
        log(f"No originals at {originals}. Run fetch_inat_originals.py first.")
        return 2
    ids = sorted(p.stem for p in originals.glob("*.jpg") if p.stat().st_size > 0)
    if args.only:
        ids = [i for i in ids if i == str(args.only)]
    if args.limit:
        ids = ids[:args.limit]

    manifest = {}
    if manifest_path.exists():
        for m in json.loads(manifest_path.read_text(encoding="utf-8")).get("photos", []):
            manifest[m["photo_id"]] = m

    log(f"{len(ids)} originals in {originals} -> bucket {args.bucket}"
        + (" (DRY RUN: no uploads)" if args.dry_run else ""))
    uploaded = skipped = failed = 0
    total_bytes = 0
    last_request = 0.0

    for i, pid in enumerate(ids, 1):
        src = originals / f"{pid}.jpg"
        out = derived / pid / REVISION
        try:
            web_size, web_new = make_rendition(src, out / "web.jpg", WEB_PX, WEB_BUDGET)
            thumb_size, thumb_new = make_rendition(src, out / "thumb.jpg", THUMB_PX, THUMB_BUDGET)
        except Exception as e:
            failed += 1
            log(f"FAILED {pid}: could not make renditions ({e})")
            continue
        made = " made" if (web_new or thumb_new) else ""
        files = (("original.jpg", src), ("web.jpg", out / "web.jpg"), ("thumb.jpg", out / "thumb.jpg"))
        if args.dry_run:
            log(f"[{i}/{len(ids)}] {pid}  original {src.stat().st_size // 1024} KB, "
                f"web {web_size // 1024} KB, thumb {thumb_size // 1024} KB{made}")
            continue

        record = manifest.get(pid) or {"photo_id": pid, "bucket": args.bucket, "files": {}}
        ok = True
        for name, path in files:
            key = f"inat/{pid}/{REVISION}/{name}"
            data = path.read_bytes()
            local_md5 = md5_of(data)
            wait = args.gap - (time.time() - last_request)
            if wait > 0:
                time.sleep(wait)
            last_request = time.time()
            try:
                have = r2.head(key)
                if have and have[1] == local_md5:
                    status = "already up"
                else:
                    etag = r2.put(key, data, "image/jpeg")
                    if etag != local_md5:
                        raise RuntimeError(f"checksum mismatch after upload ({etag} != {local_md5})")
                    status = "uploaded"
                    total_bytes += len(data)
                record["files"][name] = {"key": key, "bytes": len(data), "md5": local_md5,
                                         "uploaded_at": record["files"].get(name, {}).get("uploaded_at")
                                         if status == "already up" else
                                         datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%MZ")}
                log(f"[{i}/{len(ids)}] {pid} {name:<12} {len(data) // 1024:>5} KB  {status}")
            except Exception as e:
                ok = False
                log(f"FAILED {pid} {name}: {e}")
                break
        if ok:
            manifest[pid] = record
            uploaded += 1
        else:
            failed += 1
        if i % 25 == 0:
            write_json_atomic(manifest_path, {"photos": sorted(manifest.values(), key=lambda m: m["photo_id"])})

    if not args.dry_run:
        photos = sorted(manifest.values(), key=lambda m: m["photo_id"])
        write_json_atomic(manifest_path, {
            "meta": {"updated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%MZ"),
                     "bucket": args.bucket, "revision": REVISION, "count": len(photos)},
            "photos": photos,
        })
        log(f"Done. {uploaded} photos complete in the bucket ({total_bytes / 1e6:.0f} MB sent this run), "
            f"{failed} failed. Manifest lists {len(photos)} photos.")
        if failed:
            log("Run it again to retry the failures; files already up are skipped.")
    else:
        log("Dry run done. Renditions are on disk; nothing was uploaded.")
    log_f.close()
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
