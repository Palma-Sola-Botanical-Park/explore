#!/usr/bin/env python3
"""maintenance_todo.py: the park's standing to-do list, built from the repo and iNaturalist. READ-ONLY on every master.

  python3 data/scripts/maintenance_todo.py              # iNat park sweep, writes data/published/maintenance_todo.json
  python3 data/scripts/maintenance_todo.py --offline    # no network
  python3 data/scripts/maintenance_todo.py --dry        # print the summary, write nothing

FOUR LISTS (Randy, 2026-10-11). Feature plants first, then the next level down (Standard). "Background" (the bottom
feeders) is left out of lists 2 to 4 and never flagged.
  1 review      which species pages need a person to review them (plants and wildlife), from `review_due`
  2 stale       which plants have gone longest without an iNaturalist observation (oldest newest-sighting)
  3 photos      which plants need better pictures: fewest approved photos against a target of 10; and which have
                more than 15 (the oldest are discard candidates)
  4 spot_check  which placements need someone to stand there and see whether the plant still exists (placements.json)

THE REVIEW DATES ARE MANUAL, FOREVER. `reviewed_on` means a person looked at the live page, thought about how it
reflects the park, looked for new stories, checked anything questionable, and certified it until the next review.
`review_due` is the next date. This script only READS them. Nothing here, no publisher and no batch tool, ever
writes them; Randy edits them by hand or asks for `assign_review_due.py` by name. A species with no `review_due`
shows as "not scheduled yet".

Review intervals by feature_tier: Feature 8 months, Standard 10, Background 12 (a species with no tier counts as
Standard). Review dates start 2026-11-30; until the dates exist the lists still fill from the data. Standard
library only. Every log line carries the date and the time. The park scope is the polygon in species_manager (pin
only), reused through photo_wanted.
"""
import sys, json, datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from photo_wanted import park_observations, newest_by_taxon, when, log   # reuse: the polygon rule lives in one place
import photo_wanted


def pw_complete():
    return photo_wanted.COMPLETE


REPO = HERE.parents[1]


def roster_newest(taxon_ids):
    """Newest observation date for taxa the park polygon cannot see.

    iNaturalist obscures threatened taxa: the public pin lands kilometres away, so a polygon query returns nothing.
    This is the same fallback Species Manager uses for Find photos (species_manager._inat_observations): ask the
    park's observer roster for the taxon, accept an obscured record when its pin is inside the park's 0.2 degree
    cell, and an honest-pin record only when it is inside the polygon. One query per taxon (a per-observer loop hit
    iNaturalist's rate limit). Dates only: the true position is never used or stored. Returns {taxon_id: date}.
    A date found this way is APPROXIMATE (an observation in the cell, not certified as in the park)."""
    try:
        import species_manager as sm
        roster = sm._project_observers()
    except Exception as e:
        log(f"roster fallback unavailable ({type(e).__name__}: {e})")
        return {}
    if not roster:
        log("roster fallback: no observer roster")
        return {}
    import time
    found = {}
    for tid in taxon_ids:
        url = ("https://api.inaturalist.org/v1/observations"
               f"?taxon_id={tid}&user_login={','.join(roster)}&per_page=60&verifiable=any"
               "&order=desc&order_by=observed_on")
        data = sm._inat_get(url)
        time.sleep(1.0)
        for o in (data or {}).get("results", []):
            ok = sm._in_park_cell(o) if o.get("obscured") else sm._in_park(o)
            if ok and o.get("observed_on"):
                found[tid] = o["observed_on"]       # newest first, so the first accepted record is the newest
                break
    return found
SRC = REPO / "data/sources"
OUT = REPO / "data/published/maintenance_todo.json"
SCHEDULE_START = "2026-11-30"
LIST_N, PHOTO_TARGET, PHOTO_CEILING = 10, 10, 15
INTERVAL_MONTHS = {"Feature": 8, "Standard": 10, "Background": 12}


def tier_of(r):
    return r.get("feature_tier") or "Standard"


def build_review(plants, wild, today):
    """The pages-to-review block, from review_due / reviewed_on on the published records. Fast: no iNaturalist."""
    def rcard(r, kind):
        return dict(id=r["id"], name=r["common_name"], kind=kind, tier=tier_of(r),
                    reviewed_on=r.get("reviewed_on"), review_due=r.get("review_due"),
                    interval_months=INTERVAL_MONTHS.get(tier_of(r), 10))
    every = [rcard(r, "plant") for r in plants] + [rcard(r, "wildlife") for r in wild]
    scheduled = [c for c in every if c["review_due"]]
    soon = (today + datetime.timedelta(days=14)).isoformat()
    due_now = sorted([c for c in scheduled if c["review_due"] <= soon], key=lambda c: (c["review_due"], c["tier"] != "Feature"))
    unscheduled = sorted([c for c in every if not c["review_due"]], key=lambda c: (c["tier"] != "Feature", c["tier"] == "Background", c["name"]))
    by_tier = {}
    for t in ("Feature", "Standard", "Background"):
        mine = [c for c in every if c["tier"] == t]
        mine.sort(key=lambda c: (c["review_due"] or "9999-12-31", c["name"]))
        for c in mine:
            c["overdue"] = bool(c["review_due"] and c["review_due"] < today.isoformat())
            c["due_soon"] = bool(c["review_due"] and c["review_due"] <= soon)
        by_tier[t] = dict(total=len(mine), reviewed=len([c for c in mine if c.get("reviewed_on")]), first=mine[:12])
    return dict(
        counts=dict(species=len(every), scheduled=len(scheduled), not_scheduled=len(unscheduled),
                    overdue=len([c for c in scheduled if c["review_due"] < today.isoformat()]),
                    never_reviewed=len([c for c in every if not c.get("reviewed_on")])),
        due_now=due_now[:30],
        next_up=sorted([c for c in scheduled if c["review_due"] > soon], key=lambda c: c["review_due"])[:15],
        not_scheduled_first=unscheduled[:15],
        by_tier=by_tier)


def _published():
    plants = [r for r in json.load(open(SRC / "plant_signage.json", encoding="utf-8"))["species"] if r.get("status") == "html"]
    wild = [r for r in json.load(open(SRC / "wildlife_signage.json", encoding="utf-8"))["species"] if r.get("status") == "html"]
    return plants, wild


def refresh_review_only():
    """Replace only the review block of the existing maintenance_todo.json (instant; used after a review is marked).
    Returns the updated document, or None if the file has never been built."""
    try:
        doc = json.load(open(OUT, encoding="utf-8"))
    except (OSError, ValueError):
        return None
    plants, wild = _published()
    doc["review"] = build_review(plants, wild, datetime.date.today())
    OUT.write_text(json.dumps(doc, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    return doc


def main():
    if "--review-only" in sys.argv:
        doc = refresh_review_only()
        log("review block refreshed" if doc else "no maintenance_todo.json yet: run it without --review-only first")
        return
    offline, dry = "--offline" in sys.argv, "--dry" in sys.argv
    today = datetime.date.today()
    plants = [r for r in json.load(open(SRC / "plant_signage.json", encoding="utf-8"))["species"] if r.get("status") == "html"]
    wild = [r for r in json.load(open(SRC / "wildlife_signage.json", encoding="utf-8"))["species"] if r.get("status") == "html"]
    photos = json.load(open(SRC / "photo_credits.json", encoding="utf-8"))["photos"]
    placements = json.load(open(SRC / "placements.json", encoding="utf-8")).get("placements", [])
    approved = {}
    for p in photos:
        if p.get("publish_ok"):
            approved.setdefault(p["psbp_id"], []).append(p)
    names = {r["id"]: r for r in plants + wild}
    listed_plants = [r for r in plants if tier_of(r) != "Background"]
    log(f"{len(plants)} plants, {len(wild)} wildlife published; {len(listed_plants)} plants listed (Background left out)")

    # ---- 1 review
    review = build_review(plants, wild, today)

    # ---- 2 stale and 3 photos (plants only)
    rows = park_observations(offline)
    seen = newest_by_taxon(rows, [r["inat_taxon_id"] for r in listed_plants if r.get("inat_taxon_id")]) if rows else {}
    log(f"iNat park sweep: {len(rows) if rows else 'not used'} observations")
    # taxa the polygon cannot see (obscured): ask the observer roster, mark the date approximate
    approx = {}
    if rows:
        blind = [r["inat_taxon_id"] for r in listed_plants if r.get("inat_taxon_id") and r["inat_taxon_id"] not in seen]
        approx = roster_newest(blind)
        log(f"{len(blind)} taxa had no in-park observation; roster fallback found a date for {len(approx)}")
    stats = []
    for r in listed_plants:
        ap = approved.get(r["id"], [])
        dates = [p["observed_on"] for p in ap if p.get("observed_on")]
        is_approx = False
        if r.get("inat_taxon_id") in seen:
            dates.append(seen[r["inat_taxon_id"]])
        elif r.get("inat_taxon_id") in approx:
            dates.append(approx[r["inat_taxon_id"]])
            is_approx = approx[r["inat_taxon_id"]] >= max([d for d in dates[:-1]] or [""])   # approximate only if it is the newest
        stats.append(dict(r=r, ap=ap, n=len(ap), newest=max(dates) if dates else "", approx=is_approx,
                          t=0 if tier_of(r) == "Feature" else 1))

    def card(s, task):
        r = s["r"]
        out = dict(id=r["id"], name=r["common_name"], botanical=r["botanical_name"], tier=tier_of(r),
                   photos=s["n"], newest=s["newest"] or None, newest_approximate=bool(s["approx"]), task=task,
                   email=f"{task} Take a clear photo and add it to iNaturalist, or send it to Randy.")
        hero = next((p for p in s["ap"] if p.get("hero")), None)
        if hero:
            out["hero_photo_id"], out["hero_url"] = hero.get("photo_id"), hero.get("photo_url")
        where = " ".join(b["text"] for b in ((r.get("page") or {}).get("where_to_find_it_here") or []) if b.get("text"))
        if where:
            out["where"] = where
        return out

    def two_tiers(key, make, keep=lambda s: True):
        return {name: [make(s) for s in sorted((x for x in stats if x["t"] == t and keep(x)), key=key)[:LIST_N]]
                for name, t in (("feature", 0), ("standard", 1))}

    def stale_task(s):
        return card(s, f"Find {s['r']['common_name']} and add a fresh observation. " +
                    (f"The newest we have is from {when(s['newest'])}" + (" (approximate: an observation whose position iNaturalist hides)." if s["approx"] else ".")
                     if s["newest"] else "We have no observation of it yet."))

    def photo_task(s):
        c = card(s, f"Take a better picture of {s['r']['common_name']}. We have {s['n']} of {PHOTO_TARGET} photos.")
        c["of_target"] = PHOTO_TARGET
        return c

    def discard(s):
        old = sorted((p for p in s["ap"] if p.get("observed_on")), key=lambda p: p["observed_on"])[:1]
        c = card(s, f"{s['r']['common_name']} has {s['n']} photos, {s['n'] - PHOTO_CEILING} over {PHOTO_CEILING}. Review the oldest for discarding.")
        if old:
            c["oldest_photo"] = dict(photo_id=old[0].get("photo_id"), observed_on=old[0]["observed_on"])
        return c

    stale = two_tiers(lambda s: (s["newest"], s["n"]), stale_task)
    photo_lists = two_tiers(lambda s: (s["n"], s["newest"]), photo_task, keep=lambda s: s["n"] < PHOTO_TARGET)
    over = two_tiers(lambda s: (-s["n"], s["newest"]), discard, keep=lambda s: s["n"] > PHOTO_CEILING)

    # ---- 4 spot check (placements.json, read only): last_seen = confirmed present, last_checked = someone looked;
    # a last_checked newer than last_seen (or a check with no sighting) means looked for and not found
    pub_plants = {r["id"]: r for r in listed_plants}
    sc = []
    for pl in placements:
        r = pub_plants.get(pl.get("subject_id"))
        if pl.get("kind") != "species" or not r:
            continue
        seen_on, checked = pl.get("last_seen"), pl.get("last_checked")
        if checked and (not seen_on or checked > seen_on):
            group, why = 0, f"Looked for and not found on {checked}. Is it still there?"
        elif not seen_on and not checked:
            group, why = 1, "No one has confirmed this one yet."
        else:
            group, why = 2, f"Last confirmed {seen_on}."
        sc.append(dict(g=group, t=0 if tier_of(r) == "Feature" else 1, seen=seen_on or "",
                       card=dict(placement_id=pl["placement_id"], id=r["id"], name=r["common_name"], tier=tier_of(r),
                                 area=pl.get("area"), reason=why, last_seen=seen_on, last_checked=checked,
                                 task=f"Check that {r['common_name']} is still there ({pl.get('area') or 'area not recorded'}).")))
    spot = {name: [x["card"] for x in sorted((y for y in sc if y["t"] == t), key=lambda y: (y["g"], y["seen"]))[:LIST_N]]
            for name, t in (("feature", 0), ("standard", 1))}

    result = dict(
        meta=dict(generated=datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                  schedule_start=SCHEDULE_START, inat="skipped" if offline else (("ok" if pw_complete() else "partial") if rows else "unavailable"),
                  placements_read=len(placements),
                  note="review dates are manual (a person certified the page); everything else is computed. Background plants are never listed."),
        review=review, stale=stale, photos=photo_lists, too_many_photos=over, spot_check=spot)

    c = review["counts"]
    log(f"review: {c['scheduled']} scheduled, {c['not_scheduled']} not scheduled, {c['overdue']} overdue, {c['never_reviewed']} never reviewed")
    for label, lst in (("stale", stale), ("photos", photo_lists), ("too_many", over), ("spot_check", spot)):
        log(f"{label}: feature {len(lst['feature'])}, standard {len(lst['standard'])}")
    if dry:
        log("--dry: nothing written")
        return
    OUT.write_text(json.dumps(result, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    log(f"wrote {OUT.relative_to(REPO)}")


if __name__ == "__main__":
    main()
