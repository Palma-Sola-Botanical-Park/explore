#!/usr/bin/env python3
"""
psbp_page_drift.py — find published pages that no longer match their JSON.

READ-ONLY. Writes nothing, touches nothing. Renders every status=html species
in memory (write_html(dry_run=True)) and compares against the file on disk.

    python3 psbp_page_drift.py              # summary
    python3 psbp_page_drift.py --diff       # + unified diff for each stale page
    python3 psbp_page_drift.py --list       # + bare list of stale IDs

Run this BEFORE `--generate-all` to see exactly how big that commit will be
and which pages it will touch. Worth keeping around afterward as a check that
the post-promotion regeneration is holding.

Verdicts:
  OK       page matches what the generator would produce right now
  STALE    page differs — regeneration will change it
  MISSING  status=html but no file on disk
  NOHERO   status=html but no hero in photo_credits.json (can't render)
  ORPHAN   file on disk whose species is not status=html
"""

import sys
import os
import difflib

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import plant_publisher
import wildlife_publisher
from psbp_common import PLANTS_DIR, WILDLIFE_DIR


def census(corpus):
    pub = plant_publisher if corpus == "plants" else wildlife_publisher
    out_dir = PLANTS_DIR if corpus == "plants" else WILDLIFE_DIR

    signage = pub.load_signage()
    credits = pub.load_credits()
    species_lookup = pub.build_species_lookup(signage)
    heroes = pub.build_hero_lookup(credits)
    galleries = pub.build_gallery_lookup(credits)

    rows = []
    expected_files = set()

    for sid, species in sorted(species_lookup.items()):
        if species.get("status") != "html":
            continue

        hero = heroes.get(sid)
        if not hero:
            rows.append((sid, species.get("common_name", ""), "NOHERO", None))
            continue

        path, rendered = pub.write_html(species, hero, galleries.get(sid, []),
                                        dry_run=True)
        expected_files.add(path.name)

        if not path.exists():
            rows.append((sid, species.get("common_name", ""), "MISSING", None))
            continue

        current = path.read_text(encoding="utf-8")
        if current == rendered:
            rows.append((sid, species.get("common_name", ""), "OK", None))
        else:
            diff = list(difflib.unified_diff(
                current.splitlines(), rendered.splitlines(),
                fromfile=f"{path.name} (on disk)",
                tofile=f"{path.name} (regenerated)",
                lineterm="", n=1,
            ))
            rows.append((sid, species.get("common_name", ""), "STALE", diff))

    # Files on disk that nothing claims.
    if out_dir.exists():
        for f in sorted(out_dir.glob("PSBP-*.html")):
            if f.name not in expected_files:
                rows.append((f.name, "", "ORPHAN", None))

    return rows


def main():
    show_diff = "--diff" in sys.argv
    show_list = "--list" in sys.argv

    grand = {}
    for corpus in ("plants", "wildlife"):
        rows = census(corpus)
        counts = {}
        for _, _, verdict, _ in rows:
            counts[verdict] = counts.get(verdict, 0) + 1
        grand[corpus] = counts

        print(f"\n{'=' * 70}")
        print(f"  {corpus.upper()}")
        print(f"{'=' * 70}")
        for verdict in ("OK", "STALE", "MISSING", "NOHERO", "ORPHAN"):
            if counts.get(verdict):
                print(f"  {verdict:<8} {counts[verdict]}")

        problems = [r for r in rows if r[2] != "OK"]
        if problems:
            print()
            for sid, name, verdict, _ in problems:
                if verdict != "OK":
                    print(f"  {verdict:<8} {sid}  {name}")

        if show_diff:
            for sid, name, verdict, diff in rows:
                if verdict == "STALE" and diff:
                    print(f"\n{'-' * 70}")
                    print(f"  {sid}  {name}   ({len(diff)} diff lines)")
                    print(f"{'-' * 70}")
                    for line in diff:
                        print("  " + line)

        if show_list:
            stale = [r[0] for r in rows if r[2] == "STALE"]
            if stale:
                print("\n  stale ids: " + " ".join(stale))

    print(f"\n{'=' * 70}")
    total_stale = sum(c.get("STALE", 0) for c in grand.values())
    total_ok = sum(c.get("OK", 0) for c in grand.values())
    print(f"  TOTAL: {total_ok} in sync, {total_stale} stale")
    if total_stale:
        print(f"  → `--generate-all` will rewrite {total_stale} page(s).")
        print(f"  → Re-run with --diff to see exactly what changes.")
    else:
        print("  → Nothing to regenerate. Pages match their JSON.")
    print(f"{'=' * 70}\n")

    return 1 if total_stale else 0


if __name__ == "__main__":
    sys.exit(main())
