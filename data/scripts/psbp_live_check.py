#!/usr/bin/env python3
"""
psbp_live_check.py — does the PUBLISHED SITE actually serve what the repo says?

Every other audit here reads the repo. This one reads the web. That gap is the
whole point: `audit_psbp.py`, `crawl_psbp.py` and `psbp_orphan_audit.py` all
agreed the catalogue was healthy while `plants/PSBP-00561-Air-Potato.html` was
a 404 on the live site for five days — because the repo held the file under a
lowercase name, macOS is case-INSENSITIVE so every local check passed, and
GitHub Pages is case-SENSITIVE so the card linked to nothing.

    python3 psbp_live_check.py                  # full check
    python3 psbp_live_check.py --quick          # indexes + 25 sampled pages
    python3 psbp_live_check.py --hash           # also byte-compare against local
    python3 psbp_live_check.py --base https://palmasolabp.org/
    python3 psbp_live_check.py --json

READ-ONLY. It makes GET/HEAD requests and writes nothing, anywhere.
Exits nonzero if anything is genuinely broken.

WHAT IT CHECKS
--------------
  1. The live search indexes load and parse
  2. Local and live indexes agree — if they differ, Pages has not finished
     rebuilding and everything after this is testing yesterday's deploy
  3. Every page path IN THE LIVE INDEX resolves — the index and the pages are
     checked against each other ON THE SERVER, not against local data
  4. Case: the exact-case filename the index points at is what the server has.
     This is the check that would have caught the Air Potato
  5. The hand-written pages and the published feeds resolve
  6. With --hash, live bytes match local bytes for a sample

READING THE RESULT
------------------
  404  a real missing file — act on it
  503  GitHub Pages rate-limiting, not your bug. Retried automatically; only
       reported if it survives the retries
  A live/local index mismatch on its own usually just means "pushed a minute
  ago, wait for the build."
"""

import argparse
import concurrent.futures
import hashlib
import json
import os
import random
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

DEFAULT_BASE = "https://palma-sola-botanical-park.github.io/explore/"
UA           = "psbp-live-check"
WORKERS      = 4        # 8 reliably trips Pages rate-limiting; 4 does not
RETRIES      = 3
TIMEOUT      = 25

# Hand-written pages that no index points at, so nothing else would notice them
# going missing. screen.html included: it has no analytics, so a 404 there is
# invisible until someone looks at a blank wall.
EXTRA_PAGES = [
    "", "index.html", "nature.html", "visit.html", "events.html", "venue.html",
    "get-involved.html", "contact.html", "news.html", "photographers.html",
    "get-started.html", "viewer.html", "screen.html", "data-health.html",
    "feed.html", "plants.json", "wildlife.json",
]
FEEDS = ["events", "classes", "news", "announcements", "volunteer", "series",
         "newsletters", "right_now", "venues", "organization", "photographers",
         "tours", "tour_stops", "wedding_calendar", "wedding_gallery"]


def find_repo():
    here = Path(__file__).resolve()
    for parent in [here.parents[2]] + list(here.parents):
        if (parent / "data" / "sources" / "plant_signage.json").exists():
            return parent
    return Path(os.environ.get("PSBP_REPO", here.parents[2]))


REPO = find_repo()


def get(url, method="GET"):
    """Return (status, body_bytes_or_None). Retries 429/503 — Pages rate-limits."""
    for attempt in range(RETRIES):
        try:
            req = urllib.request.Request(url, method=method, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
                return r.status, (r.read() if method == "GET" else b"")
        except urllib.error.HTTPError as e:
            if e.code in (429, 503) and attempt < RETRIES - 1:
                time.sleep(1.5 * (attempt + 1) + random.random())
                continue
            return e.code, None
        except Exception as e:                                  # noqa: BLE001
            if attempt < RETRIES - 1:
                time.sleep(1.0 * (attempt + 1))
                continue
            return str(e)[:60], None
    return "unreachable", None


def sha(b):
    return hashlib.sha256(b).hexdigest()[:16]


def case_variant(rel_path):
    """If the repo holds `rel_path` under a DIFFERENT case, return that name.

    NEVER use Path.exists() to answer this. macOS is case-insensitive, so
    `plants/PSBP-00561-Air-Potato.html` .exists() returns True while the file
    on disk — and in git, and on GitHub Pages — is `...-air-potato.html`. That
    is the entire bug this script was written for, and the first draft of this
    very function fell for it. os.listdir() returns the real stored names, so
    membership against it is genuinely case-sensitive on every platform.
    """
    p = Path(rel_path)
    parent = REPO / p.parent
    if not parent.is_dir():
        return None
    names = os.listdir(parent)
    if p.name in names:               # case-sensitive membership — the real test
        return None
    return next((f for f in names if f.lower() == p.name.lower()), None)


def main():
    ap = argparse.ArgumentParser(description="Check the live site against the repo")
    ap.add_argument("--base", default=DEFAULT_BASE,
                    help="site root (trailing slash optional). Point this at the "
                         "new domain after the migration")
    ap.add_argument("--quick", action="store_true", help="indexes + 25 sampled pages")
    ap.add_argument("--hash", action="store_true", help="byte-compare live vs local")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    args = ap.parse_args()

    base = args.base if args.base.endswith("/") else args.base + "/"
    errors, warnings, notes = [], [], []
    out = {"base": base}

    def say(*a):
        if not args.json:
            print(*a)

    say(f"\n{'=' * 74}\n  PSBP LIVE CHECK — {base}\n{'=' * 74}")

    # ── 1/2. indexes ────────────────────────────────────────────────────────
    pages = []
    for name in ("plants.json", "wildlife.json"):
        status, body = get(base + name)
        if status != 200 or not body:
            errors.append(f"{name}: index did not load (HTTP {status})")
            continue
        try:
            rows = json.loads(body)
        except Exception as e:                                  # noqa: BLE001
            errors.append(f"{name}: live index is not valid JSON — {e}")
            continue
        pages += [r["page"] for r in rows if r.get("page")]

        local_path = REPO / name
        if local_path.exists():
            live_h, local_h = sha(body), sha(local_path.read_bytes())
            if live_h != local_h:
                warnings.append(
                    f"{name}: live copy differs from local ({live_h} vs {local_h}) — "
                    f"Pages may still be rebuilding, or there are uncommitted changes")
            else:
                notes.append(f"{name}: live matches local ({len(rows)} entries)")
    out["pages_in_live_index"] = len(pages)
    say(f"\n  {len(pages)} page(s) listed in the live indexes")

    # ── 2b. CASE, checked locally — catches it BEFORE the push ──────────────
    # Needs no network and is the cheapest check here, so run it over the local
    # index every time. On a case-insensitive Mac this is the only way to see
    # the problem at all: the file opens fine, the page renders fine, and the
    # site 404s for everyone else.
    local_pages = []
    for name in ("plants.json", "wildlife.json"):
        lp = REPO / name
        if lp.exists():
            try:
                local_pages += [r["page"] for r in json.loads(lp.read_text()) if r.get("page")]
            except Exception:                                    # noqa: BLE001
                pass
    case_bad = [(p, case_variant(p)) for p in local_pages]
    case_bad = [(p, h) for p, h in case_bad if h]
    for p, hit in case_bad:
        errors.append(f"{p}: the index points here but the repo holds '{hit}' — "
                      f"CASE MISMATCH, will 404 on GitHub Pages even though it "
                      f"opens fine locally. Fix: git mv via a temp name")
    if local_pages and not case_bad:
        notes.append(f"case: all {len(local_pages)} local page filenames match "
                     f"the index exactly")

    if args.quick and len(pages) > 25:
        pages = random.sample(pages, 25)
        say(f"  --quick: sampling {len(pages)}")

    # ── 3/4. every page the live index points at ────────────────────────────
    targets = pages + EXTRA_PAGES + [f"data/published/{f}.json" for f in FEEDS]

    def check(p):
        status, _ = get(base + p, method="HEAD")
        return p, status

    bad = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=WORKERS) as ex:
        for p, status in ex.map(check, targets):
            if status != 200:
                bad.append((p, status))
    for p, status in bad:
        if status == 404:
            # A page listed in the index that the server does not have is the
            # Air Potato failure mode exactly: usually a filename whose CASE
            # does not match what the index points at.
            hit = case_variant(p)
            if hit:
                errors.append(f"{p}: 404 live, but the repo has '{hit}' — "
                              f"CASE MISMATCH. Rename with two `git mv` steps")
            else:
                errors.append(f"{p}: 404 — the server does not have this file")
        else:
            warnings.append(f"{p}: HTTP {status} after {RETRIES} attempts")
    say(f"  {len(targets) - len(bad)}/{len(targets)} URL(s) returned 200")

    # ── 5. optional byte comparison ─────────────────────────────────────────
    if args.hash:
        sample = random.sample(pages, min(12, len(pages)))
        mism = 0
        for p in sample:
            lp = REPO / p
            if not lp.exists():
                continue
            status, body = get(base + p)
            if status == 200 and body and sha(body) != sha(lp.read_bytes()):
                mism += 1
                warnings.append(f"{p}: live bytes differ from local — stale deploy or "
                                f"uncommitted local edit")
        say(f"  byte-compared {len(sample)} page(s), {mism} differing")

    # ── report ──────────────────────────────────────────────────────────────
    out.update(errors=errors, warnings=warnings, notes=notes)
    if args.json:
        print(json.dumps(out, indent=2))
        return 1 if errors else 0

    for label, items, mark in (("ERROR", errors, "✗"), ("WARN", warnings, "!"),
                               ("OK", notes, "✓")):
        if items:
            print(f"\n  {label} ({len(items)})")
            for i in items:
                print(f"    {mark} {i}")

    print(f"\n{'=' * 74}")
    if errors:
        print(f"  {len(errors)} error(s) — the live site does not match the repo.")
    else:
        print("  Live site serves everything the indexes point at.")
    print(f"{'=' * 74}\n")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
