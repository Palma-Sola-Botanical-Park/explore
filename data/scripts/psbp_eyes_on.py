#!/usr/bin/env python3
"""
psbp_eyes_on.py — "Put your eyes on it": which plants most need looking at.

    python3 psbp_eyes_on.py                 # ranked walk, grouped by area
    python3 psbp_eyes_on.py --flat          # one global ranking, no grouping
    python3 psbp_eyes_on.py --limit 40      # cap the output
    python3 psbp_eyes_on.py --json          # for the dashboard

WHY THIS IS NOT AN AUDIT
    Nothing here is wrong. It is a work queue — the plants whose continued
    existence is least evidenced, ordered so that checking them is a sensible
    walk rather than a scavenger hunt.

THE HARD PART: an observation confirms A plant, not WHICH plant
    iNaturalist observations are per SPECIES. placements are per PLANT. When a
    species has five placements and three observations this year, at least two
    plants are unconfirmed — and possibly all five, if all three observations
    were of the same specimen. So an observation can never confirm an
    individual. Only `last_seen`, set standing at the plant, can do that.

    Observations are therefore used as DILUTION, not proof:

        dilution = placements / (observations in the last year, min 1)

    One plant with ten observations scores near zero. Five plants sharing one
    observation score five times worse than one plant with one observation —
    correct, because four of them have nothing behind them.

    The asymmetry that makes this work: POSITIVE evidence does not propagate
    across placements, but NEGATIVE evidence does. "Nothing of this species has
    been seen since 2023" is true of every plant of it, however many there are.

⚠ THE OBSERVATION AGES HERE COME FROM THE REPO, AND THE REPO LAGS iNATURALIST
    `photo_credits.json` holds only IMPORTED photographs. Measured 2026-08-29,
    two of this script's top four findings were false positives for that reason
    alone:

        Buccaneer Palm   repo 2021-05-28   iNat 2026-07-14  (seen 6 weeks ago)
        Jacaranda        repo 2024-04-21   iNat 2026-07-15  (by donyiyt)

    Amazon Lily and Brazilian Dutchman's Pipe agreed with iNat and are real.
    **Verify anything this script surfaces against iNaturalist before walking
    to it**, or the walk is spent confirming plants somebody photographed in
    July.

    The proper fix is to refresh observation dates from the iNat API keyed on
    `inat_taxon_id`, which every catalogue record carries. Two traps there:
    a project-scoped query SILENTLY OMITS OBSCURED TAXA — 16 catalogue species,
    including the Buccaneer Palm above, and precisely the rare ones most worth
    checking — and a user-scoped query misses the other 71 contributors.

`last_checked` IS THE RESET, NOT `last_seen`
    If you look and the plant is gone, setting only `last_seen` would leave it
    on the list for ever and you would re-walk to the same empty spot. Looking
    is what clears an item; finding is what sets a date. That is why they are
    two fields — see LANDMARKS.md §11.1.
"""
import argparse, collections, datetime, json, math, sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SRC  = REPO / "data" / "sources"
TODAY = datetime.date.today()

WEIGHT_TIER = {"Feature": 2.0, "Standard": 1.0, "Background": 0.6}


def _load(name, default):
    try:
        with open(SRC / name, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def _days(iso):
    if not iso:
        return None
    try:
        return (TODAY - datetime.date.fromisoformat(str(iso)[:10])).days
    except ValueError:
        return None


def build():
    photos = (_load("photo_credits.json", {}) or {}).get("photos", [])
    plants = (_load("plant_signage.json", {}) or {}).get("species", [])
    places = (_load("placements.json", {}) or {}).get("placements", [])
    sig = {s["id"]: s for s in plants}

    newest, recent = {}, collections.Counter()
    for p in photos:
        pid, obs = p.get("psbp_id"), p.get("observed_on")
        if not pid or not obs:
            continue
        if obs > newest.get(pid, ""):
            newest[pid] = obs
        d = _days(obs)
        if d is not None and d <= 365:
            recent[pid] += 1

    n_placed = collections.Counter(
        p.get("subject_id") for p in places if (p.get("kind") or "species") == "species")

    rows = []
    for pl in places:
        if (pl.get("kind") or "species") != "species":
            continue
        sid = pl.get("subject_id")
        sp = sig.get(sid)
        if not sp or sp.get("status") != "html":
            continue

        # Looking is what clears an item; finding is what dates it.
        checked = _days(pl.get("last_checked"))
        seen    = _days(pl.get("last_seen"))
        obs_age = _days(newest.get(sid))

        # Best available evidence, in order of strength.
        if seen is not None:
            age, basis = seen, "last_seen"
        elif obs_age is not None:
            age, basis = obs_age, "observation"
        else:
            age, basis = 3650, "nothing"

        count = max(1, n_placed.get(sid, 1))
        dilution = count / max(1, recent.get(sid, 0)) if recent.get(sid) else float(count)
        w = WEIGHT_TIER.get(sp.get("feature_tier"), 1.0)
        if sp.get("has_sign"):
            w *= 1.5                      # a sign pointing at a dead plant is worse than a page
        score = (age / 365.0) * math.log1p(dilution) * w
        if checked is not None and checked < 180:
            score *= 0.25                 # recently walked past, even if not found

        rows.append({
            "placement_id": pl.get("placement_id"), "subject_id": sid,
            "name": sp.get("common_name"), "area": pl.get("area") or "(no area)",
            "tier": sp.get("feature_tier"), "score": round(score, 2),
            "evidence": basis, "age_days": age, "placements": count,
            "obs_last_year": recent.get(sid, 0),
            "last_seen": pl.get("last_seen"), "last_checked": pl.get("last_checked"),
            "newest_observation": newest.get(sid),
        })
    rows.sort(key=lambda r: -r["score"])
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--flat", action="store_true", help="one global ranking")
    ap.add_argument("--limit", type=int, default=30)
    ap.add_argument("--floor", type=float, default=0.5,
                    help="hide anything scoring below this — recently evidenced")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    rows = build()

    if a.json:
        print(json.dumps({"generated": TODAY.isoformat(), "count": len(rows),
                          "rows": rows}, indent=2))
        return 0

    print(f"\nPUT YOUR EYES ON IT — {len(rows)} placed plants ranked by unverified-ness")
    print("score = evidence age x dilution x importance. Higher = look sooner.\n")
    if a.flat:
        groups = [("all areas", rows[:a.limit])]
    else:
        by = collections.defaultdict(list)
        for r in rows:
            by[r["area"]].append(r)
        groups = sorted(by.items(), key=lambda kv: -sum(x["score"] for x in kv[1]))
    shown = 0
    for area, items in groups:
        if shown >= a.limit:
            break
        worth = [i for i in items if i["score"] >= a.floor]
        if not worth:
            continue
        tot = sum(i["score"] for i in items)
        print(f"  {area}  ({len(worth)} worth a look of {len(items)}, total {tot:.0f})")
        for r in worth[: a.limit - shown]:
            note = (f"{r['placements']} plants / {r['obs_last_year']} obs this year"
                    if r["placements"] > 1 else f"{r['obs_last_year']} obs this year")
            print(f"     {r['score']:6.1f}  {r['name'][:28]:30} "
                  f"{r['evidence']:11} {r['age_days']//30:3}mo  {note}")
            shown += 1
        print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
