#!/usr/bin/env python3
"""
psbp_seed_publish_state.py — backfill publish_state.json from git history.

RUN THIS ONCE, after installing the new publishers and BEFORE --generate-all.

Why it exists
-------------
publish_state.json starts empty, so the first regeneration would stamp all 289
pages "updated today" — technically true, but it throws away real history and
tells a visitor nothing. Git already knows when each page last genuinely
changed. This reads that and seeds the file with honest dates.

Because it also stores the CURRENT input and generator fingerprints, the
regeneration that follows sees "nothing changed" for every species and keeps
the seeded dates instead of bumping them. Pages then carry their true last-
changed date, and only diverge from it when something actually changes.

    python3 psbp_seed_publish_state.py            # preview, writes nothing
    python3 psbp_seed_publish_state.py --write    # write publish_state.json
    python3 psbp_seed_publish_state.py --write --force   # overwrite existing

Date source, in order of preference:
  1. Last git commit touching that page file
  2. The file's modification time on disk
  3. Today (only if the page is untracked and unreadable)
"""

import os
import subprocess
import sys
import datetime
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import plant_publisher
import wildlife_publisher
from psbp_common import (REPO, PLANTS_DIR, WILDLIFE_DIR, PUBLISH_STATE_JSON,
                         HASH_VERSION, compute_input_hash, generator_fingerprint,
                         load_publish_state, write_json_atomic)


def git_last_commit_date(path):
    """ISO date of the last commit touching path, or '' if unknown."""
    try:
        out = subprocess.run(
            ["git", "log", "-1", "--format=%ad", "--date=short", "--", str(path)],
            cwd=str(REPO), capture_output=True, text=True, timeout=15,
        )
        if out.returncode == 0:
            return out.stdout.strip()[:10]
    except Exception:                                          # noqa: BLE001
        pass
    return ""


def file_mtime_date(path):
    try:
        return datetime.date.fromtimestamp(path.stat().st_mtime).isoformat()
    except Exception:                                          # noqa: BLE001
        return ""


def seed(corpus):
    pub = plant_publisher if corpus == "plants" else wildlife_publisher
    out_dir = PLANTS_DIR if corpus == "plants" else WILDLIFE_DIR

    signage = pub.load_signage()
    credits = pub.load_credits()
    heroes = pub.build_hero_lookup(credits)
    galleries = pub.build_gallery_lookup(credits)
    generator = generator_fingerprint(pub)

    rows = []
    for sid, species in sorted(pub.build_species_lookup(signage).items()):
        if species.get("status") != "html":
            continue
        hero = heroes.get(sid)
        if not hero:
            rows.append((sid, "", "SKIP no hero", None))
            continue

        filename = pub.page_filename(sid, species["common_name"])
        path = out_dir / filename

        if path.exists():
            date = git_last_commit_date(path)
            source = "git"
            if not date:
                date = file_mtime_date(path)
                source = "mtime"
        else:
            date, source = "", "no page"

        if not date:
            date = datetime.date.today().isoformat()
            source = "today"

        rows.append((sid, date, source, {
            "last_published": date,
            "input_hash": compute_input_hash(species, hero, galleries.get(sid, [])),
            "generator": generator,
            "filename": filename,
            "corpus": corpus,
        }))
    return rows


def main():
    write = "--write" in sys.argv
    force = "--force" in sys.argv

    existing = load_publish_state()
    if existing.get("species") and not force:
        print(f"\n  publish_state.json already has {len(existing['species'])} record(s).")
        print("  Seeding is a one-time operation — pass --force to overwrite.\n")
        return 1

    state = {"meta": {}, "species": {}}
    counts = {}
    for corpus in ("plants", "wildlife"):
        rows = seed(corpus)
        print(f"\n{'=' * 66}\n  {corpus.upper()}\n{'=' * 66}")
        for sid, date, source, rec in rows:
            counts[source] = counts.get(source, 0) + 1
            if rec:
                state["species"][sid] = rec
            flag = "" if source == "git" else f"   <- {source}"
            print(f"  {sid}  {date or '—':<12}{flag}")

    state["meta"] = {
        "hash_version": HASH_VERSION,
        "updated": datetime.datetime.now().isoformat(timespec="seconds"),
        "species_count": len(state["species"]),
        "note": "Seeded from git history by psbp_seed_publish_state.py",
    }

    print(f"\n{'=' * 66}")
    print(f"  {len(state['species'])} record(s) prepared")
    for k, v in sorted(counts.items()):
        print(f"    date from {k}: {v}")

    oldest = min((r["last_published"] for r in state["species"].values()), default="—")
    newest = max((r["last_published"] for r in state["species"].values()), default="—")
    print(f"  date range: {oldest} .. {newest}")

    if write:
        write_json_atomic(PUBLISH_STATE_JSON, state)
        print(f"\n  ✓ Wrote {PUBLISH_STATE_JSON}")
        print("  Next: run --generate-all on both publishers. Dates will NOT bump,")
        print("  because the stored fingerprints already match the current inputs.")
    else:
        print("\n  DRY RUN — nothing written. Re-run with --write to save.")
    print(f"{'=' * 66}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
