#!/usr/bin/env python3
"""assign_review_due.py: spread the next-review dates over the calendar. DRY RUN unless --apply.

  python3 data/scripts/assign_review_due.py                       # show the proposed schedule, write nothing
  python3 data/scripts/assign_review_due.py --apply --head 1a2b3c4d   # write review_due (HEAD guard: the commit you expect)
  python3 data/scripts/assign_review_due.py --only PSBP-00123,PSBP-90004 --apply --head ...   # slot just these

RUN ONLY ON RANDY'S WORD, by name. This is the one script allowed to write `review_due`, and it writes NOTHING ELSE:
never `reviewed_on` (that is a person's certification, set by hand), never content. No publisher, merge or batch
tool may call it or set either field.

WHAT IT DOES. For every published species (plants and wildlife) with no `review_due`:
  * first run (nothing scheduled yet): spread each tier evenly from the start date over its own interval, thinnest
    pages first (Feature 8 months, Standard 10, Background 12; no tier counts as Standard). The weekly load comes out
    nearly flat (about 8 a week for 333 species) with a Feature/Standard/Background mix every week.
  * later runs (some already scheduled): put each new species in the lightest week inside the last two months of its
    interval, so nothing piles up. It does not need to be a full interval from now.
Records are re-read inside this script and the file must round-trip byte-identically before anything is written;
`git diff --stat` after, and expect one changed line per species.
"""
import sys, json, subprocess, datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
SRC = REPO / "data/sources"
START = datetime.date(2026, 11, 30)   # a Monday; review dates begin here (Randy, 2026-10-11: near the end of November)
INTERVAL = {"Feature": 8, "Standard": 10, "Background": 12}
FILES = [("plant_signage.json", "plant"), ("wildlife_signage.json", "wildlife")]


def arg(name, default=None):
    return sys.argv[sys.argv.index(name) + 1] if name in sys.argv else default


def head():
    return subprocess.run(["git", "-C", str(REPO), "rev-parse", "--short=8", "HEAD"], capture_output=True, text=True).stdout.strip()


def log(msg):
    print(datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), msg, flush=True)


def words(r):
    return sum(len(((b.get("label") or "") + " " + b.get("text", "")).split())
               for bl in (r.get("page") or {}).values() for b in bl if "text" in b)


def week_start(d):
    return d - datetime.timedelta(days=d.weekday())


def main():
    apply_, only = "--apply" in sys.argv, (arg("--only") or "").split(",") if arg("--only") else None
    if apply_ and arg("--head") != head():
        sys.exit(f"ABORT: --apply needs --head {head()} (the commit you expect); got {arg('--head')}")
    docs, recs = {}, []
    for fname, kind in FILES:
        raw = (SRC / fname).read_text(encoding="utf-8")
        doc = json.loads(raw)
        if raw != json.dumps(doc, indent=2, ensure_ascii=False) + "\n":
            sys.exit(f"ABORT: {fname} does not round-trip byte-identically")
        docs[fname] = doc
        recs += [(fname, r) for r in doc["species"] if r.get("status") == "html"]
    tier = lambda r: r.get("feature_tier") or "Standard"
    scheduled = [r for _, r in recs if r.get("review_due")]
    todo = [(f, r) for f, r in recs if not r.get("review_due") and (only is None or r["id"] in only)]
    log(f"{len(recs)} published, {len(scheduled)} already scheduled, {len(todo)} to schedule")
    today = datetime.date.today()
    plan = {}
    if not scheduled:
        for t, months in INTERVAL.items():
            grp = sorted([x for x in todo if tier(x[1]) == t or (t == "Standard" and tier(x[1]) not in INTERVAL)],
                         key=lambda x: (words(x[1]), x[1]["id"]))
            weeks = max(1, round(months * 52 / 12))
            for i, (_, r) in enumerate(grp):
                plan[r["id"]] = START + datetime.timedelta(days=7 * (i * weeks // max(1, len(grp))))
    else:
        load = {}
        for r in scheduled:
            w = week_start(datetime.date.fromisoformat(r["review_due"]))
            load[w] = load.get(w, 0) + 1
        for _, r in sorted(todo, key=lambda x: x[1]["id"]):
            months = INTERVAL.get(tier(r), 10)
            lo, hi = max(today, START), None
            hi = today + datetime.timedelta(days=round(months * 30.4))
            lo = max(lo, hi - datetime.timedelta(days=61))
            weeks = [week_start(lo) + datetime.timedelta(days=7 * k) for k in range(0, (hi - week_start(lo)).days // 7 + 1)]
            best = min(weeks, key=lambda w: (load.get(w, 0), w))
            load[best] = load.get(best, 0) + 1
            plan[r["id"]] = best
    # report
    by_week, by_tier = {}, {}
    for f, r in todo:
        d = plan[r["id"]]
        by_week[week_start(d)] = by_week.get(week_start(d), 0) + 1
        by_tier.setdefault(tier(r), []).append(d)
    for t in INTERVAL:
        if t in by_tier:
            log(f"{t}: {len(by_tier[t])} species, {min(by_tier[t])} to {max(by_tier[t])}")
    if by_week:
        loads = sorted(by_week.values())
        log(f"weekly load: {len(by_week)} weeks, min {loads[0]}, max {loads[-1]}, average {sum(loads) / len(loads):.1f}")
        months = {}
        for w, n in by_week.items():
            months[w.strftime('%Y-%m')] = months.get(w.strftime('%Y-%m'), 0) + n
        log("by month: " + ", ".join(f"{k} {v}" for k, v in sorted(months.items())))
        for f, r in sorted(todo, key=lambda x: (plan[x[1]["id"]], x[1]["id"]))[:12]:
            log(f"  {plan[r['id']]}  {tier(r):10s} {r['id']}  {r['common_name']}")
    if not apply_:
        log("DRY RUN: nothing written (pass --apply --head <commit>)")
        return
    for fname, doc in docs.items():
        new = []
        for r in doc["species"]:
            if r["id"] in plan:
                out = {}
                for k, v in r.items():
                    out[k] = v
                    if k == "feature_tier":
                        out["review_due"] = plan[r["id"]].isoformat()
                if "review_due" not in out:
                    out["review_due"] = plan[r["id"]].isoformat()
                r = out
            new.append(r)
        doc["species"] = new
        if head() != arg("--head"):
            sys.exit("ABORT: HEAD moved during the run")
        (SRC / fname).write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        log(f"WROTE {fname}")
    log("Next: git diff --stat (one added line per species). reviewed_on is untouched: only a person sets it.")


if __name__ == "__main__":
    main()
