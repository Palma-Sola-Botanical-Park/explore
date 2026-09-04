#!/usr/bin/env python3
"""
psbp_backfill_taxonomy.py — one-shot repair for taxonomy fields on existing records.

Intake now captures class/order/rank from iNaturalist and derives animal_group
(see add_discovered_to_research in species_manager.py). Records created before
that need backfilling. This does it, from the same iNat endpoint, keyed on the
inat_taxon_id each record already carries.

What it fixes
-------------
  1. EMPTY family        — 29 wildlife records, all published, 26 of them birds
  2. MISPLACED genus     — a family name in the genus slot, which is what a
                           family-rank taxon produced under the old
                           "split the binomial" logic (PSBP-90005 Antlions)
  3. MISSING class/order — never captured before; needed by derive_animal_group
  4. WRONG animal_group  — only where the value is absent, OR where taxonomy
                           contradicts it AND the record has no human override

DRY RUN BY DEFAULT. Nothing is written without --apply.

    python3 psbp_backfill_taxonomy.py                 # report only
    python3 psbp_backfill_taxonomy.py --apply         # write
    python3 psbp_backfill_taxonomy.py --wildlife-only

A backup of each file it changes is written alongside it before the write.
"""

import argparse
import datetime
import json
import os
import shutil
import sys
import time
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from psbp_common import REPO, write_json_atomic, derive_animal_group  # noqa: E402

SOURCES  = os.path.join(REPO, "data", "sources")
FILES = [
    ("wildlife", os.path.join(SOURCES, "wildlife_signage.json")),
    ("plants",   os.path.join(SOURCES, "plant_signage.json")),
    ("research", os.path.join(SOURCES, "research.json")),
]
API = "https://api.inaturalist.org/v1/taxa/{}"
PAUSE = 0.6          # be polite; iNat asks for <=60 requests/minute


def fetch(taxon_id):
    req = urllib.request.Request(API.format(taxon_id),
                                 headers={"User-Agent": "psbp-backfill-taxonomy"})
    with urllib.request.urlopen(req, timeout=25) as r:
        res = json.load(r).get("results") or []
    if not res:
        return None
    t = res[0]
    return {
        "rank":      (t.get("rank") or "").strip(),
        "iconic":    t.get("iconic_taxon_name") or "",
        "ancestors": {a.get("rank"): a.get("name", "")
                      for a in (t.get("ancestors") or []) if a.get("rank")},
    }


def plan_for(sp, kind, tax):
    """Return a list of (field, old, new, why) for one record."""
    out = []
    anc  = tax["ancestors"]
    rank = tax["rank"]
    t    = sp.get("taxonomy") or {}
    name = (sp.get("scientific_name") or sp.get("botanical_name") or "").strip()
    parts = name.split()

    fam_now = t.get("family") or ""
    fam_new = anc.get("family", "") or (parts[0] if rank == "family" and parts else "")
    if fam_new and fam_new != fam_now:
        out.append(("taxonomy.family", fam_now, fam_new, "from iNat ancestors"))

    gen_now = t.get("genus") or ""
    gen_new = anc.get("genus", "")
    if not gen_new and rank == "genus" and parts:
        gen_new = parts[0]
    if gen_new != gen_now:
        # Only *clear* a genus when the taxon genuinely has none (family rank).
        if gen_new or rank in ("family", "order", "class", "subfamily", "tribe", "superfamily"):
            why = "iNat genus" if gen_new else f"taxon is rank {rank} — has no genus"
            out.append(("taxonomy.genus", gen_now, gen_new, why))

    for r in ("class", "order"):
        if anc.get(r) and not t.get(r):
            out.append((f"taxonomy.{r}", t.get(r) or "", anc[r], "newly captured"))

    if not sp.get("inat_rank") and rank:
        out.append(("inat_rank", sp.get("inat_rank") or "", rank, "newly captured"))

    if kind != "plants" and (sp.get("type") or "wildlife") != "plant":
        ag_now = sp.get("animal_group") or ""
        ag_new, reason = derive_animal_group(tax)
        src = sp.get("animal_group_source") or ""
        if ag_new and ag_new != ag_now:
            if src == "human":
                out.append(("animal_group", ag_now, ag_now,
                            f"SKIPPED — human-set, taxonomy suggests {ag_new} ({reason})"))
            else:
                out.append(("animal_group", ag_now, ag_new, reason))
        elif not ag_new and not ag_now:
            out.append(("animal_group", "", "",
                        f"STILL UNSET — {reason}; needs a human"))
    return out


def apply_change(sp, field, new):
    if field.startswith("taxonomy."):
        sp.setdefault("taxonomy", {})[field.split(".", 1)[1]] = new
    else:
        sp[field] = new


def main():
    ap = argparse.ArgumentParser(description="Backfill taxonomy fields from iNaturalist")
    ap.add_argument("--apply", action="store_true", help="write changes (default: dry run)")
    ap.add_argument("--wildlife-only", action="store_true")
    ap.add_argument("--limit", type=int, default=0, help="stop after N records (testing)")
    args = ap.parse_args()

    targets = [f for f in FILES if not args.wildlife_only or f[0] == "wildlife"]
    grand_changed = grand_seen = grand_skipped = 0

    for kind, path in targets:
        if not os.path.exists(path):
            print(f"  {path} not found — skipped")
            continue
        blob = json.load(open(path, encoding="utf-8"))
        rows = blob.get("species", [])
        print(f"\n{'='*74}\n  {kind}  —  {len(rows)} records\n{'='*74}")

        changed_records = 0
        for sp in rows:
            if args.limit and grand_seen >= args.limit:
                break
            tid = sp.get("inat_taxon_id")
            if not tid:
                grand_skipped += 1
                continue
            grand_seen += 1
            try:
                tax = fetch(tid)
            except Exception as e:                                  # noqa: BLE001
                print(f"  {sp.get('id')} {sp.get('common_name','')[:30]:30} FETCH FAILED: {e}")
                continue
            time.sleep(PAUSE)
            if not tax:
                print(f"  {sp.get('id')} {sp.get('common_name','')[:30]:30} taxon {tid} not found")
                continue

            plan = plan_for(sp, kind, tax)
            real = [p for p in plan if p[1] != p[2]]
            notes = [p for p in plan if p[1] == p[2]]
            if real or notes:
                print(f"\n  {sp.get('id')}  {sp.get('common_name','')[:40]}")
                for field, old, new, why in real:
                    print(f"      {field:20} {old!r:24} -> {new!r:24}  ({why})")
                    if args.apply:
                        apply_change(sp, field, new)
                for field, old, new, why in notes:
                    print(f"      {field:20} {why}")
            if real:
                changed_records += 1
                grand_changed += 1

        if args.apply and changed_records:
            # Backup goes OUTSIDE the repo — see psbp_common.backup_file().
            from psbp_common import backup_file
            bak = backup_file(path)
            blob.setdefault("meta", {})["updated"] = \
                datetime.datetime.now().isoformat(timespec="seconds")
            write_json_atomic(path, blob)
            print(f"\n  WROTE {path}\n  backup {bak}")

    print(f"\n{'='*74}")
    print(f"  {grand_seen} records checked · {grand_changed} would change"
          f" · {grand_skipped} skipped (no inat_taxon_id)")
    if not args.apply:
        print("  DRY RUN — nothing written. Re-run with --apply.")
    print(f"{'='*74}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
