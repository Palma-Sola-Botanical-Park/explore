#!/usr/bin/env python3
"""rarefruit_refresh.py: rebuild the data on rarefruit.html, Randy's Rare Fruit grove field page.

  python3 data/scripts/rarefruit_refresh.py          # iNat grove sweep, rewrites the DATA line in rarefruit.html
  python3 data/scripts/rarefruit_refresh.py --dry    # print the summary and what moved, write nothing

Run by the "Refresh rare fruit page" button on Species Manager's To do tab (Randy, 2026-11-01). READ-ONLY on every
master: it reads plant_signage.json, research.json, placements.json and photo_credits.json, asks iNaturalist (read
only) for observations pinned in the grove, and writes ONE line of rarefruit.html (`const DATA=...`). Nothing else.

THE GROVE is a box south of lat 27.5140 and east of lng -82.6595, kept to PARK_POLYGON (pin only, never project_id
or place_id, verifiable=any). It takes in the Butterfly Garden and the bridge end of the Big Pond walkway; it is the
box the first snapshot (2026-08-31) drew. Obscured observations are skipped (their public pin is off).

THE LISTS on the page:
  rare       gold dots: a record flagged rare_fruit, or a record that IS a kind on the MRFC list, with a plant
             observation in the grove
  other      green dots: every other signage record (spotted, html, parked) with an observation in the grove;
             research-stage taxa that are neither are left off (mostly weeds; research.json also holds animals)
  missing    rare or MRFC records in plant_signage.json with no grove observation
  research   the same for research.json records (research stage, or recorded as died)
  mrfc_only  MRFC kinds with no record anywhere in the catalog: the hunt list
Pinned means a placements.json row in area "Rare Fruit" or inside the box. A position is the median of the
observations placed within max(10 m, twice the best accuracy); the accuracy shown is their median. A map photo is
the R2 thumb when photo_credits.json has the photo and the bucket has it, else iNat's small copy from the
best-placed grove observation.

THE MRFC LIST is the Manatee Rare Fruit Council's own page, https://www.mrfc.org/p/palma-sola.html, read 2026-11-01:
47 kinds once cultivars are dropped, each mapped by hand to the record that is that plant. Randy: a hunt list, not an
inventory. If their page changes, edit MRFC below (and MRFC_READ). A mapped record that no longer exists is reported
and treated as no record. Standard library only. Every log line carries the date and the time.
"""
import sys, json, re, datetime, statistics as st
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
REPO = HERE.parents[1]
SRC = REPO / "data" / "sources"
PAGE = REPO / "rarefruit.html"

NORTH, WEST = 27.5140, -82.6595
MRFC_READ = "2026-11-01"
MRFC = [("Abiu", None), ("African Breadnut", "PSBP-00791"), ("Atemoya", None), ("Australian Brown Pine", None),
        ("Avocado", "PSBP-00129"), ("Barbados Cherry", "PSBP-00096"), ("Bird Plum", None), ("Black Sapote", "PSBP-00127"),
        ("Canistel", "PSBP-00607"), ("Dragon Fruit", "PSBP-00080"), ("Fig", None), ("Grumichama", "PSBP-00734"),
        ("Guava", "PSBP-00789"), ("Jaboticaba", None), ("Jackfruit", "PSBP-00132"), ("Kumquat", None),
        ("Lemon Grass", "PSBP-00556"), ("Key Lime", None), ("Rangpur Lime", None), ("Longan", "PSBP-00126"),
        ("Loquat", "PSBP-00568"), ("Lychee", "PSBP-00606"), ("Macadamia Nut", "PSBP-00608"), ("Malabar Chestnut", None),
        ("Mango", "PSBP-00090"), ("Monstera", "PSBP-00701"), ("Moringa", "PSBP-00086"), ("Mulberry", "PSBP-00617"),
        ("Noni", "PSBP-00616"), ("Olive", "PSBP-00716"), ("Papaya", "PSBP-00079"), ("Peach", "PSBP-00762"),
        ("Persimmon", "PSBP-00788"), ("Pineapple", "PSBP-00130"), ("Plum", "PSBP-00711"), ("Pomelo", None),
        ("Pond Apple", "PSBP-00128"), ("Sapodilla", "PSBP-00613"), ("Star Apple", "PSBP-00790"),
        ("Star Fruit", "PSBP-00124"), ("Strawberry Tree", None), ("Sugar Apple", "PSBP-00795"),
        ("Surinam Cherry", "PSBP-00723"), ("Tamarind", "PSBP-00682"), ("Tangerine", None),
        ("White Sapote", "PSBP-00712"), ("Ylang Ylang", "PSBP-00135")]
STATUS = {"html": "published", "spotted": "not yet published", "parked": "parked",
          "research": "research stage", "died": "recorded as died"}


def log(msg):
    print(datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), msg, flush=True)


def load(name, key):
    with open(SRC / name, encoding="utf-8") as f:
        return json.load(f)[key]


def in_grove(lat, lng, sm):
    return lat <= NORTH and lng >= WEST and sm._in_park({"location": f"{lat},{lng}"})


def grove_observations(sm):
    """Every observation pinned in the grove box, as full v1 records; None if any page failed."""
    poly_lat = [p[1] for p in sm.PARK_POLYGON]
    poly_lng = [p[0] for p in sm.PARK_POLYGON]
    box = f"nelat={NORTH}&nelng={max(poly_lng)}&swlat={min(poly_lat)}&swlng={WEST}&verifiable=any"
    out, above = [], 0
    while True:
        data = sm._inat_get(f"https://api.inaturalist.org/v1/observations?{box}&per_page=200"
                            f"&order_by=id&order=asc&id_above={above}")
        if data is None:
            return None
        res = data.get("results") or []
        if not res:
            return out
        out.extend(res)
        above = res[-1]["id"]


def previous_data(text):
    m = re.search(r"^const DATA=(.*);$", text, re.M)
    try:
        return json.loads(m.group(1)) if m else {}
    except ValueError:
        return {}


def what_moved(old, new):
    """Plain lines saying what changed between the page's old DATA and the new one."""
    def names(d, key):
        return {(r.get("common") or r.get("name")) if isinstance(r, dict) else r for r in d.get(key, [])}
    lines = []
    for key, label in (("rare", "gold dots"), ("other", "green dots"), ("mrfc_only", "hunt list")):
        a, b = names(old, key), names(new, key)
        if b - a:
            lines.append(f"New on the {label}: " + ", ".join(sorted(b - a)))
        if a - b:
            lines.append(f"Off the {label}: " + ", ".join(sorted(a - b)))
    if not lines:
        lines.append("Nothing moved between lists.")
    return lines


def build(sm, obs):
    import psbp_common as pc
    recs = {}
    for x in load("research.json", "species"):
        recs[x["id"]] = dict(x, _src="research")
    for x in load("plant_signage.json", "species"):
        recs[x["id"]] = dict(x, _src="signage")
    placements = load("placements.json", "placements")
    credited = {str(p["photo_id"]) for p in load("photo_credits.json", "photos") if p.get("photo_id") and p.get("license")}
    with open(REPO / "plants.json", encoding="utf-8") as f:
        pages = {x["id"]: x["page"] for x in json.load(f)}

    problems = []
    mrfc_by_id = {}
    for kind, pid in MRFC:
        if pid and pid not in recs:
            problems.append(f"MRFC {kind}: record {pid} is gone; counted as no record")
        elif pid:
            mrfc_by_id[pid] = kind
    mrfc_only = [k for k, pid in MRFC if not pid or pid not in recs]

    def bot(x):
        return (x.get("botanical_name") or "").strip()

    def label(x):                                 # a spotted record may have no common name yet
        return (x.get("common_name") or "").strip() or bot(x) or x["id"]
    tid2 = {}
    for x in recs.values():
        t = x.get("inat_taxon_id")
        if not t or re.search(r"(aceae|oideae)$", bot(x)):
            continue
        if t in tid2 and tid2[t]["_src"] == "signage":
            continue
        tid2[t] = x

    def match(t):
        if t.get("id") in tid2:
            return tid2[t["id"]]
        for a in reversed(t.get("ancestor_ids") or []):
            if a in tid2:
                return tid2[a]

    pins = {}
    for p in placements:
        if p.get("kind") == "species":
            pins.setdefault(p["subject_id"], []).append(p)

    def grove_pin(p):
        return p.get("area") == "Rare Fruit" or (p.get("latitude") and p.get("longitude")
                                                 and in_grove(p["latitude"], p["longitude"], sm))

    groups, obscured = {}, 0
    for o in obs:
        if not o.get("location"):
            continue
        lat, lng = (float(v) for v in o["location"].split(","))
        if not in_grove(lat, lng, sm):
            continue
        if o.get("obscured"):
            obscured += 1
            continue
        t = o.get("taxon") or {}
        if t.get("iconic_taxon_name") != "Plantae":
            continue
        m = match(t)
        if m and m["_src"] == "research" and not (m.get("rare_fruit") or m["id"] in mrfc_by_id):
            m = None
        if m:
            groups.setdefault(m["id"], []).append(dict(lat=lat, lng=lng, acc=o.get("positional_accuracy"),
                                                       on=o.get("observed_on") or "", photos=o.get("photos") or []))

    def by_acc(o):
        return (o["acc"] is None, o["acc"] or 0)
    rare, other = [], []
    for pid, L in groups.items():
        x = recs[pid]
        known = [o for o in L if o["acc"] is not None]
        if known:
            best_acc = min(o["acc"] for o in known)
            best = [o for o in known if o["acc"] <= max(10, 2 * best_acc)]
            acc = round(st.median(o["acc"] for o in best))
        else:
            best, acc = L, None
        photo = None
        for o in sorted(L, key=by_acc):
            for ph in o["photos"]:
                if str(ph["id"]) in credited and pc.media_in_bucket(ph["id"]):
                    photo = f"{pc.MEDIA_BASE}/inat/{ph['id']}/{pc.MEDIA_REV}/thumb.jpg"
                    break
            if photo:
                break
        if not photo:
            for o in sorted(best, key=by_acc):
                if o["photos"] and o["photos"][0].get("url"):
                    photo = o["photos"][0]["url"].replace("/square.", "/small.")
                    break
        here = [p for p in pins.get(pid, []) if grove_pin(p)]
        away = [p for p in pins.get(pid, []) if not grove_pin(p)]
        r = dict(name=label(x), common=label(x), sci=bot(x), tid=x.get("inat_taxon_id"), n=len(L),
                 lat=round(st.median(o["lat"] for o in best), 7), lng=round(st.median(o["lng"] for o in best), 7),
                 acc=acc, psbp=pid, rare=bool(x.get("rare_fruit")) or pid in mrfc_by_id, status=x.get("status"),
                 photo=photo, last=max(o["on"] for o in L), pinned=bool(here),
                 area=Counter(p.get("area") for p in here).most_common(1)[0][0] if here else None,
                 pins_here=len(here), elsewhere=sorted({p.get("area") or "no area" for p in away}) or None,
                 page=pages.get(pid) if x.get("status") == "html" else None, mrfc=mrfc_by_id.get(pid))
        (rare if r["rare"] else other).append(r)
    rare.sort(key=lambda r: (-r["n"], r["common"]))
    other.sort(key=lambda r: (-r["n"], r["common"]))

    missing, research = [], []
    wanted = [pid for pid, x in recs.items() if x.get("rare_fruit")] + list(mrfc_by_id)
    for pid in dict.fromkeys(wanted):
        if pid in groups:
            continue
        x = recs[pid]
        note = STATUS.get(x.get("status"), x.get("status") or "")
        kind = mrfc_by_id.get(pid)
        if kind and kind.lower() != label(x).lower():
            note += " · MRFC: " + kind
        here = [p for p in pins.get(pid, []) if grove_pin(p)]
        if here:
            note += " · pinned here"
        (research if x["_src"] == "research" else missing).append(
            dict(id=pid, name=label(x), note=note, pinned=bool(here)))
    missing.sort(key=lambda e: e["name"])
    research.sort(key=lambda e: e["name"])
    data = dict(snapshot=datetime.date.today().isoformat(), rare=rare, other=other, missing=missing,
                mrfc_only=mrfc_only, research=research)
    return data, problems, obscured


def main():
    dry = "--dry" in sys.argv
    import species_manager as sm
    log("asking iNaturalist for observations pinned in the grove")
    obs = grove_observations(sm)
    if obs is None:
        log("ERROR: iNaturalist did not answer every page; rarefruit.html left as it was")
        return 1
    data, problems, obscured = build(sm, obs)
    for p in problems:
        log("WARNING: " + p)
    log(f"{len(obs)} observations in the box ({obscured} obscured, skipped); "
        f"{len(data['rare'])} gold, {len(data['other'])} green, {sum(r['pinned'] for r in data['rare'])} gold pinned, "
        f"hunt list {len(data['mrfc_only'])}; MRFC list as read {MRFC_READ}")
    text = PAGE.read_text(encoding="utf-8")          # re-read right before writing
    for line in what_moved(previous_data(text), data):
        log(line)
    if dry:
        log("dry run: nothing written")
        return 0
    new_line = "const DATA=" + json.dumps(data, ensure_ascii=False) + ";"
    text, n = re.subn(r"^const DATA=.*;$", lambda _: new_line, text, flags=re.M)
    if n != 1:
        log(f"ERROR: expected one DATA line in rarefruit.html, found {n}; nothing written")
        return 1
    PAGE.write_text(text, encoding="utf-8")
    log(f"wrote rarefruit.html (data taken {data['snapshot']})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
