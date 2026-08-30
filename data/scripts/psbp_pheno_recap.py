#!/usr/bin/env python3
"""Re-analyze phenology records that were capped at the old 3-photo limit.

WHY THIS EXISTS
    Until 2026-08-29, PHENO_PHOTOS_PER_OBS was 3: the scanner sent the model
    the first three photos of an observation and dropped the rest. One
    observation gets ONE verdict, so every photo of it is evidence toward that
    verdict — a flower may only be visible in the fifth frame. 265 of 664
    stored records hit that cap and may have been scored blind.

    The limit is now 8. This script re-scores the capped records against the
    photos they never saw.

SAFE BY CONSTRUCTION
    phenology.json is a CACHE, not a source. The raw material is the iNat
    observations, which are permanent and re-fetchable. Randy, 2026-08-29:
    "phenology can be re-analyzed any time, I suppose. Raw data still there."
    Nothing here writes to iNaturalist; it only reads photo URLs.

    HUMAN REVIEWS SURVIVE. A record carrying human_reviewed / human_signs keeps
    both — the AI signs underneath are refreshed, the human verdict on top is
    not touched. Those are the one thing in this file that cannot be rebuilt.

USAGE
    export ANTHROPIC_API_KEY=...
    python3 data/scripts/psbp_pheno_recap.py                 # dry run, shows the plan
    python3 data/scripts/psbp_pheno_recap.py --limit 20 --go # do 20
    python3 data/scripts/psbp_pheno_recap.py --go            # do all of them
    python3 data/scripts/psbp_pheno_recap.py --species PSBP-00089 --go
"""
import argparse, json, os, sys, datetime

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import species_manager as sm          # main-guarded: importing starts no server

OLD_CAP = 3


def capped(store, only=None):
    out = []
    for oid, rec in store["observations"].items():
        if (rec.get("photo_count") or 0) < OLD_CAP:
            continue
        if only and rec.get("psbp_id") != only:
            continue
        out.append((oid, rec))
    out.sort(key=lambda t: (t[1].get("psbp_id") or "", t[1].get("observed_on") or ""))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--go", action="store_true", help="actually re-analyze; otherwise dry run")
    ap.add_argument("--limit", type=int, default=0, help="stop after N observations")
    ap.add_argument("--species", default="", help="restrict to one PSBP id")
    a = ap.parse_args()

    store = sm._pheno_load()
    todo = capped(store, a.species or None)
    if a.limit:
        todo = todo[:a.limit]

    by_tx = {}
    for oid, rec in todo:
        by_tx.setdefault(rec.get("taxon_id"), []).append((oid, rec))

    names = {}
    for sp in sm._pheno_plant_index():
        names[sp["id"]] = sp

    print(f"  {len(todo)} capped records across {len(by_tx)} taxa"
          f"{' (limited)' if a.limit else ''}")
    if not a.go:
        seen = {}
        for oid, rec in todo:
            seen[rec.get("psbp_id")] = seen.get(rec.get("psbp_id"), 0) + 1
        for pid, n in sorted(seen.items(), key=lambda t: -t[1])[:20]:
            nm = (names.get(pid) or {}).get("common_name", pid)
            print(f"     {n:3}  {nm}")
        print("\n  Dry run. Add --go to re-analyze. Human reviews are preserved.")
        return

    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("  ANTHROPIC_API_KEY is not set. Export it and try again.")
        return 1

    done = failed = 0
    for tx, recs in by_tx.items():
        pid = recs[0][1].get("psbp_id")
        sp = names.get(pid)
        if not sp:
            print(f"  ! {pid}: no longer in the plant index, skipping {len(recs)}")
            continue
        obs_list = sm._inat_observations(tx)
        if obs_list is None:
            print(f"  ! {sp['common_name']}: could not reach iNaturalist, skipping")
            continue
        by_id = {str(o.get("id")): o for o in obs_list}
        for oid, old in recs:
            obs = by_id.get(str(oid))
            if not obs:
                print(f"  - {sp['common_name']} obs {oid}: no longer in the project")
                continue
            new = sm._pheno_analyze_obs(obs, sp["common_name"], sp["scientific_name"])
            if "error" in new:
                print(f"  ! {sp['common_name']} obs {oid}: {new['error']}")
                failed += 1
                continue
            new["taxon_id"] = tx
            new["psbp_id"] = pid
            new["kingdom"] = "plants"
            new.pop("usage", None)
            # the one thing that cannot be rebuilt
            new["human_reviewed"] = old.get("human_reviewed", False)
            new["human_signs"] = old.get("human_signs")
            new["recap_from"] = {"photo_count": old.get("photo_count"),
                                 "signs": old.get("signs"),
                                 "at": datetime.datetime.now().isoformat(timespec="seconds")}
            store["observations"][oid] = new
            grew = (new.get("photo_count") or 0) - (old.get("photo_count") or 0)
            flag = "" if not grew else f"  +{grew} photos"
            chg = "" if new.get("signs") == old.get("signs") else "  SIGNS CHANGED"
            print(f"  ok {sp['common_name'][:26]:28} obs {oid}{flag}{chg}")
            done += 1
        sm._pheno_save(store)

    sm._pheno_save(store)
    print(f"\n  re-analyzed {done}, failed {failed}")


if __name__ == "__main__":
    sys.exit(main() or 0)
