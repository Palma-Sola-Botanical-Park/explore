#!/usr/bin/env python3
"""photo_wanted.py: the nightly "which plants most need a photo" list.

  python3 data/scripts/photo_wanted.py              # reads iNat (park polygon), writes data/published/photo_wanted.json
  python3 data/scripts/photo_wanted.py --offline    # no network: approved photos only
  python3 data/scripts/photo_wanted.py --dry        # print the summary, write nothing

THE RULE (Randy, 2026-10-11): keep it simple. Two lists, each with Feature plants first and then the next level down:
  stale   "these plants have stale observations": the oldest newest-sighting first (the newest in-park iNaturalist
          observation or approved photo; a species never seen sorts first)
  photos  "these plants need better pictures": fewest approved photos first (the count is a stand-in: nothing here
          can judge quality), ties broken by the oldest sighting
No calendar, no monthly expectation, no bloom or fruit guessing. Published plants only, never wildlife. feature_tier
"Background" (the bottom feeders) is left out and never flagged; "Standard" is the next level down.

WRITES data/published/photo_wanted.json (generated: never hand-edit):
  stale  {feature: [...], standard: [...]}     photos  {feature: [...], standard: [...]}     (10 cards each)
Each card has words ready for a screen, a web page or an email. The screen deck, the website and an email all read
this one file.

RULES KEPT. Park scope is the polygon in species_manager (pin only, never project_id or place_id); the park sweep is
reused from there, not copied. No iNat logins, no student names, and no location except the species page's own
"Where to find it" words. Standard library only. Every log line carries the date and the time.
"""
import sys, json, datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
REPO = HERE.parents[1]
SRC = REPO / "data/sources"
OUT = REPO / "data/published/photo_wanted.json"
LIST_N = 10
MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def log(msg):
    print(datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), msg, flush=True)


def park_observations(offline):
    """In-park observations as light v2 records, or None if iNat could not be reached.
    Reuses species_manager's polygon sweep so the boundary rule lives in one place."""
    if offline:
        return None
    try:
        import species_manager as sm
        rows = sm._park_observations_lite()
    except Exception as e:                       # network down, import trouble: the list still builds
        log(f"iNat sweep failed ({type(e).__name__}: {e}); continuing with approved photos only")
        return None
    return rows or None


def newest_by_taxon(rows, taxon_ids):
    """taxon id -> newest observation date. An observation counts for a species when its taxon is that species or
    sits below it (a variety or cultivar carries the species id in ancestor_ids)."""
    wanted, newest = set(taxon_ids), {}
    for o in rows:
        d = o.get("observed_on")
        if not d:
            continue
        t = o.get("taxon") or {}
        for tid in ({t.get("id")} | set(t.get("ancestor_ids") or [])) & wanted:
            if d > newest.get(tid, ""):
                newest[tid] = d
    return newest


def when(iso):
    return f"{MONTHS[int(iso[5:7]) - 1]} {iso[:4]}"


def main():
    offline, dry = "--offline" in sys.argv, "--dry" in sys.argv
    plants = [r for r in json.load(open(SRC / "plant_signage.json", encoding="utf-8"))["species"] if r.get("status") == "html"]
    photos = json.load(open(SRC / "photo_credits.json", encoding="utf-8"))["photos"]
    approved = {}
    for p in photos:
        if p.get("publish_ok"):
            approved.setdefault(p["psbp_id"], []).append(p)

    eligible = [r for r in plants if r.get("feature_tier") != "Background"]
    log(f"{len(plants)} published plants, {len(eligible)} listed (Background left out)")
    rows = park_observations(offline)
    seen = newest_by_taxon(rows, [r["inat_taxon_id"] for r in eligible if r.get("inat_taxon_id")]) if rows else {}
    log(f"iNat park sweep: {len(rows) if rows else 'not used'} observations")

    stats = []
    for r in eligible:
        ap = approved.get(r["id"], [])
        dates = [p["observed_on"] for p in ap if p.get("observed_on")]
        if r.get("inat_taxon_id") in seen:
            dates.append(seen[r["inat_taxon_id"]])
        stats.append(dict(r=r, photos=len(ap), newest=max(dates) if dates else "",
                          tier=0 if r.get("feature_tier") == "Feature" else 1))

    def card(s, kind):
        r, n = s["r"], s["photos"]
        name = r["common_name"]
        have = "no photo yet" if n == 0 else f"{n} photo{'s' if n != 1 else ''}"
        if kind == "stale":
            task = (f"Find {name} and add a fresh observation. " +
                    (f"The newest we have is from {when(s['newest'])}." if s["newest"] else "We have no observation of it yet."))
        else:
            task = f"Take a better picture of {name}. We have {have}."
        hero = next((p for p in approved.get(r["id"], []) if p.get("hero")), None)
        where = " ".join(b["text"] for b in ((r.get("page") or {}).get("where_to_find_it_here") or []) if b.get("text"))
        out = dict(id=r["id"], name=name, botanical=r["botanical_name"], tier=r.get("feature_tier") or "Standard",
                   photos=n, newest=s["newest"] or None, task=task,
                   email=f"{task} Take a clear photo and add it to iNaturalist, or send it to Randy.")
        if hero:
            out["hero_photo_id"], out["hero_url"] = hero.get("photo_id"), hero.get("photo_url")
        if where:
            out["where"] = where
        return out

    def lists(key, kind):
        res = {}
        for name, tier in (("feature", 0), ("standard", 1)):
            res[name] = [card(s, kind) for s in sorted((x for x in stats if x["tier"] == tier), key=key)[:LIST_N]]
        return res

    stale = lists(lambda s: (s["newest"], s["photos"]), "stale")
    photos_list = lists(lambda s: (s["photos"], s["newest"]), "photos")
    result = dict(
        meta=dict(generated=datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                  plants_published=len(plants), plants_listed=len(eligible),
                  inat="skipped" if offline else ("ok" if rows else "unavailable"),
                  rule="stale = oldest newest-sighting; photos = fewest approved photos; Feature then Standard; Background omitted"),
        stale=stale, photos=photos_list)

    for label, lst in (("stale", stale), ("photos", photos_list)):
        for tier in ("feature", "standard"):
            log(f"{label}/{tier}: " + "; ".join(c["name"] for c in lst[tier][:4]) + " ...")
    if dry:
        log("--dry: nothing written")
        return
    OUT.write_text(json.dumps(result, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    log(f"wrote {OUT.relative_to(REPO)}")


if __name__ == "__main__":
    main()
