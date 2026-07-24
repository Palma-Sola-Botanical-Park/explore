#!/usr/bin/env python3
"""
propagate_names.py — reconcile photo_credits.json against photographer_names.json.

psbp_common.propagate_photographer_name() exists but has no callers, so name
edits have never actually reached the credits file. This is the missing entry
point.

    python3 data/scripts/propagate_names.py              # preview all drift
    python3 data/scripts/propagate_names.py --apply      # fix it
    python3 data/scripts/propagate_names.py --apply mariaaaz zailibeth
    python3 data/scripts/propagate_names.py --unregistered

Preview is the default; nothing is written without --apply. On apply it calls
the real psbp_common function, so whatever that does is what happens.

After applying you MUST re-promote the affected species — this touches
photo_credits.json only. The script prints the exact id list.
"""

import argparse
import json
import os
import sys
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve()
sys.path.insert(0, str(HERE.parent))          # psbp_common lives beside us

try:
    import psbp_common as pc
except ImportError:
    sys.exit(f"could not import psbp_common from {HERE.parent} — "
             f"run this from data/scripts/ or drop it there")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("logins", nargs="*",
                    help="specific handles to propagate (default: all drifted)")
    ap.add_argument("--apply", action="store_true", help="write changes")
    ap.add_argument("--unregistered", action="store_true",
                    help="also list handles with no registry entry")
    args = ap.parse_args()

    credits = pc.load_json(pc.PHOTO_CREDITS_JSON, {"photos": []})
    photos = credits.get("photos", [])
    names = pc.load_json(pc.PHOTOGRAPHER_NAMES_JSON, {})
    if not photos:
        sys.exit(f"no photos found in {pc.PHOTO_CREDITS_JSON}")

    # ── detect drift ───────────────────────────────────────────────────
    drift = defaultdict(list)          # login -> [(psbp_id, photo_id, old, new)]
    unregistered = defaultdict(int)
    for p in photos:
        login = (p.get("photographer") or "")
        key = login.lower()
        resolved = pc.display_name(login, p.get("photographer_name", ""))
        want = pc.build_credit_line(resolved, p.get("license"))
        if p.get("credit_line") != want or p.get("photographer_name") != resolved:
            drift[key].append((p.get("psbp_id"), p.get("photo_id"),
                               p.get("photographer_name"), resolved))
        if key and key not in names:
            unregistered[key] += 1

    targets = [l.lower() for l in args.logins] if args.logins else sorted(drift)
    unknown = [l for l in targets if l not in drift]
    for l in unknown:
        print(f"  note: {l!r} shows no drift — nothing to do")
    targets = [l for l in targets if l in drift]

    if not targets:
        print("\n  photo_credits.json is in sync with photographer_names.json")
    else:
        total = sum(len(drift[l]) for l in targets)
        head = "APPLYING" if args.apply else "PREVIEW (no changes written)"
        print(f"\n{'=' * 70}\n{head} — {len(targets)} handle(s), "
              f"{total} record(s)\n{'=' * 70}")

        affected = set()
        for login in targets:
            rows = drift[login]
            olds = {o for _, _, o, _ in rows}
            new = rows[0][3]
            registered = "registry" if login in names else "iNat/handle fallback"
            print(f"\n  {login}  ({len(rows)} photo(s), via {registered})")
            for o in sorted(olds):
                print(f"      {o!r}  ->  {new!r}")
            ids = sorted({r[0] for r in rows})
            affected |= set(ids)
            print(f"      species: {' '.join(ids)}")

            if args.apply:
                result = pc.propagate_photographer_name(login)
                # docstring claims a 2-tuple; the code returns 3. Tolerate both.
                count = result[0] if isinstance(result, tuple) else result
                print(f"      -> updated {count} record(s)")

        print(f"\n{'=' * 70}")
        if args.apply:
            print(f"  photo_credits.json updated — {len(affected)} species affected")
            print("\n  NOW RE-PROMOTE. This did not touch any HTML page or the")
            print("  search index; the old names are still stamped there.\n")
            pub = set()
            for corpus in ("plants", "wildlife"):
                for s in pc.load_signage(corpus).get("species", []):
                    if s.get("status") == "html":
                        pub.add(s["id"])

            live = sorted(i for i in affected if i in pub)
            plants = [i for i in live if int(i.split("-")[1]) < 90000]
            wildlife = [i for i in live if int(i.split("-")[1]) >= 90000]
            if plants:
                print(f"  plants   ({len(plants)}): {' '.join(plants)}")
            if wildlife:
                print(f"  wildlife ({len(wildlife)}): {' '.join(wildlife)}")

            later = sorted(i for i in affected if i not in pub)
            if later:
                print(f"\n  not published — nothing to re-promote, the corrected name")
                print(f"  will be stamped whenever these are first published:")
                print(f"    {' '.join(later)}")
            print("\n  Then re-run audit_psbp.py --only CREDITS --only HTML to confirm.")
        else:
            print("  re-run with --apply to write these changes")

    # ── unregistered handles ───────────────────────────────────────────
    if args.unregistered and unregistered:
        print(f"\n{'=' * 70}\nHANDLES WITH NO REGISTRY ENTRY "
              f"({len(unregistered)})\n{'=' * 70}")
        print("  These publish as the bare iNat login. Add a display_name to")
        print("  photographer_names.json, then re-run this script.\n")
        for login, n in sorted(unregistered.items(), key=lambda x: -x[1]):
            print(f"    {n:4d}  {login}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
