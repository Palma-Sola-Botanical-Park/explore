#!/usr/bin/env python3
"""check_inat_obscured.py: which published species does iNaturalist hide? DRY RUN unless --apply.

  python3 data/scripts/check_inat_obscured.py                          # ask iNaturalist, print the findings, write nothing
  python3 data/scripts/check_inat_obscured.py --apply --head 1a2b3c4d  # write `inat_obscured` (HEAD guard: the commit you expect)

RUN ONLY ON RANDY'S WORD, by name. It writes ONE field, `inat_obscured` (true / false), on published plants and
wildlife, and nothing else. It never writes `obscure_on_map`: that is Randy's own decision, set by hand, and the
publisher builds no species map for any species where it is true (the publisher only reads it, never writes it).

HOW A SPECIES IS JUDGED (a fact from iNaturalist, not a guess):
  * seen inside the park polygon with an honest pin -> false (an obscured observation can never land there)
  * not seen there, so ask the park's observer roster for the taxon (the same fallback Species Manager uses for
    Find photos); iNaturalist stamps each observation `taxon_geoprivacy`, and "obscured" or "private" means the
    TAXON is hidden (an observer's own choice shows up in `geoprivacy` and is ignored here) -> true
  * not seen and no roster observation says either -> unknown: the field is left alone and the species is listed
Refuses to --apply when the park sweep came back incomplete (it would mislabel species as unseen).
Records are re-read here and the file must round-trip byte-identically; expect one added line per changed species.
Standard library only. Every log line carries the date and the time.
"""
import sys, json, time, subprocess, datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import photo_wanted
from photo_wanted import park_observations, newest_by_taxon, log

REPO = HERE.parents[1]
SRC = REPO / "data/sources"
FILES = ["plant_signage.json", "wildlife_signage.json"]
HIDDEN = {"obscured", "private"}


def arg(name, default=None):
    return sys.argv[sys.argv.index(name) + 1] if name in sys.argv else default


def head():
    return subprocess.run(["git", "-C", str(REPO), "rev-parse", "--short=8", "HEAD"], capture_output=True, text=True).stdout.strip()


def main():
    apply_ = "--apply" in sys.argv
    if apply_ and arg("--head") != head():
        sys.exit(f"ABORT: --apply needs --head {head()} (the commit you expect); got {arg('--head')}")
    docs, recs = {}, []
    for fname in FILES:
        raw = (SRC / fname).read_text(encoding="utf-8")
        doc = json.loads(raw)
        if raw != json.dumps(doc, indent=2, ensure_ascii=False) + "\n":
            sys.exit(f"ABORT: {fname} does not round-trip byte-identically")
        docs[fname] = doc
        recs += [(fname, r) for r in doc["species"] if r.get("status") == "html" and r.get("inat_taxon_id")]
    log(f"{len(recs)} published species with an iNaturalist taxon id")

    rows = park_observations(False)
    if not rows:
        sys.exit("ABORT: no iNaturalist park sweep; nothing to judge from")
    if apply_ and not photo_wanted.COMPLETE:
        sys.exit("ABORT: the park sweep was incomplete (a page failed). Run it again before --apply")
    seen = newest_by_taxon(rows, [r["inat_taxon_id"] for _, r in recs])
    blind = [r for _, r in recs if r["inat_taxon_id"] not in seen]
    log(f"{len(recs) - len(blind)} seen in the park polygon (not obscured); {len(blind)} not seen: asking the observer roster")

    import species_manager as sm
    roster = sm._project_observers()
    verdict = {}
    for r in blind:
        tid = r["inat_taxon_id"]
        data = sm._inat_get("https://api.inaturalist.org/v1/observations"
                            f"?taxon_id={tid}&user_login={','.join(roster)}&per_page=30&verifiable=any&order=desc&order_by=observed_on")
        time.sleep(1.0)
        res = (data or {}).get("results", [])
        if any((o.get("taxon_geoprivacy") in HIDDEN) for o in res):
            verdict[r["id"]] = True
        # else unknown: left alone

    plan, changes, unknown = {}, [], []
    for fname, r in recs:
        tid = r["inat_taxon_id"]
        if tid in seen:
            new = False
        elif r["id"] in verdict:
            new = True
        else:
            unknown.append(r)
            continue
        plan[r["id"]] = new
        if r.get("inat_obscured") != new:
            changes.append((r, r.get("inat_obscured"), new))

    hidden = [r for _, r in recs if plan.get(r["id"]) is True]
    log(f"inat_obscured TRUE for {len(hidden)}: " + ", ".join(sorted(r["common_name"] for r in hidden)))
    log(f"unknown (not seen and no roster observation): {len(unknown)}: " + ", ".join(sorted(r["common_name"] for r in unknown)))
    log(f"{len(changes)} record(s) would change")
    for r, old, new in changes[:6]:
        log(f"  {r['id']} {r['common_name']}: {old} -> {new}")
    if not apply_:
        log("DRY RUN: nothing written (pass --apply --head <commit>)")
        return
    for fname, doc in docs.items():
        new_species = []
        for r in doc["species"]:
            if r["id"] in plan and r.get("inat_obscured") != plan[r["id"]]:
                out = {}
                for k, v in r.items():
                    if k == "inat_obscured":
                        continue
                    out[k] = v
                    if k == "inat_taxon_id":
                        out["inat_obscured"] = plan[r["id"]]
                if "inat_obscured" not in out:
                    out["inat_obscured"] = plan[r["id"]]
                r = out
            new_species.append(r)
        doc["species"] = new_species
        if head() != arg("--head"):
            sys.exit("ABORT: HEAD moved during the run")
        (SRC / fname).write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        log(f"WROTE {fname}")
    log("Next: git diff --stat (one added line per species). obscure_on_map is untouched: only Randy sets it.")


if __name__ == "__main__":
    main()
