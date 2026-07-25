#!/usr/bin/env python3
"""
psbp_card_drift.py — would regenerating change the search index cards?

READ-ONLY. Writes nothing.

Companion to psbp_page_drift.py, which only checks HTML pages. This checks
plants.json and wildlife.json.

Why it matters: regeneration calls update_plants_json() / update_wildlife_json(),
which REPLACE a card wholesale with whatever build_*_json_entry() produces. The
old hero-swap code patched individual fields instead. So if any card carries a
key the builder doesn't emit — hand-added, or left over from an older schema —
regeneration would silently drop it.

This script answers that before you find out the hard way.

    python3 psbp_card_drift.py            # summary
    python3 psbp_card_drift.py --verbose  # show every differing field

Verdicts:
  OK       rebuilt card is identical to the card on file
  LOST     rebuilt card is MISSING keys the current card has  <-- the dangerous one
  GAINED   rebuilt card adds keys not currently present
  CHANGED  same keys, different values (often a legitimately stale card)
  NOCARD   status=html but no card in the index at all
"""

import sys
import os
import json

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import plant_publisher
import wildlife_publisher
from psbp_common import PLANTS_JSON, WILDLIFE_JSON, load_json


def audit(corpus):
    if corpus == "plants":
        pub, idx_path, builder = plant_publisher, PLANTS_JSON, plant_publisher.build_plants_json_entry
    else:
        pub, idx_path, builder = wildlife_publisher, WILDLIFE_JSON, wildlife_publisher.build_wildlife_json_entry

    signage = pub.load_signage()
    credits = pub.load_credits()
    species_lookup = pub.build_species_lookup(signage)
    heroes = pub.build_hero_lookup(credits)

    cards = {c.get("id"): c for c in load_json(idx_path, [])}

    rows = []
    for sid, species in sorted(species_lookup.items()):
        if species.get("status") != "html":
            continue
        hero = heroes.get(sid)
        if not hero:
            continue

        current = cards.get(sid)
        if current is None:
            rows.append((sid, "NOCARD", {}))
            continue

        try:
            rebuilt = builder(species, hero)
        except Exception as e:
            rows.append((sid, "ERROR", {"error": str(e)}))
            continue

        lost = sorted(set(current) - set(rebuilt))
        gained = sorted(set(rebuilt) - set(current))
        changed = sorted(k for k in set(current) & set(rebuilt)
                         if current[k] != rebuilt[k])

        if lost:
            verdict = "LOST"
        elif gained:
            verdict = "GAINED"
        elif changed:
            verdict = "CHANGED"
        else:
            verdict = "OK"

        rows.append((sid, verdict, {
            "lost": {k: current[k] for k in lost},
            "gained": {k: rebuilt[k] for k in gained},
            "changed": {k: (current[k], rebuilt[k]) for k in changed},
        }))

    return rows


def main():
    verbose = "--verbose" in sys.argv
    all_lost_keys = {}
    total = {}

    for corpus in ("plants", "wildlife"):
        rows = audit(corpus)
        counts = {}
        for _, verdict, _ in rows:
            counts[verdict] = counts.get(verdict, 0) + 1
            total[verdict] = total.get(verdict, 0) + 1

        print(f"\n{'=' * 70}")
        print(f"  {corpus.upper()}  ({len(rows)} published species with a hero)")
        print(f"{'=' * 70}")
        for v in ("OK", "LOST", "GAINED", "CHANGED", "NOCARD", "ERROR"):
            if counts.get(v):
                print(f"  {v:<8} {counts[v]}")

        for sid, verdict, detail in rows:
            if verdict in ("OK",):
                continue
            if verdict == "LOST":
                for k in detail.get("lost", {}):
                    all_lost_keys.setdefault(k, []).append(sid)
            if verbose or verdict in ("LOST", "NOCARD", "ERROR"):
                print(f"\n  {verdict}  {sid}")
                for k, v in detail.get("lost", {}).items():
                    print(f"      - would LOSE   {k} = {json.dumps(v)[:90]}")
                for k, v in detail.get("gained", {}).items():
                    print(f"      + would ADD    {k} = {json.dumps(v)[:90]}")
                if verbose:
                    for k, (old, new) in detail.get("changed", {}).items():
                        print(f"      ~ would CHANGE {k}")
                        print(f"          from {json.dumps(old)[:80]}")
                        print(f"          to   {json.dumps(new)[:80]}")

    print(f"\n{'=' * 70}")
    if all_lost_keys:
        print("  ⚠  REGENERATION WOULD DROP THESE KEYS:")
        for k, ids in sorted(all_lost_keys.items()):
            print(f"      {k}  ({len(ids)} card(s), e.g. {ids[0]})")
        print("  → Do NOT proceed. These fields need adding to the entry builder.")
    elif total.get("CHANGED"):
        print(f"  {total.get('OK', 0)} card(s) identical, "
              f"{total['CHANGED']} would change values but lose no fields.")
        print("  → Safe. Run with --verbose to see what differs.")
    else:
        print(f"  All {total.get('OK', 0)} card(s) identical. Nothing would change.")
        print("  → Safe.")
    print(f"{'=' * 70}\n")

    return 1 if all_lost_keys else 0


if __name__ == "__main__":
    sys.exit(main())
