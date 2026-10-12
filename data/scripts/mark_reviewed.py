#!/usr/bin/env python3
"""mark_reviewed.py: record that a PERSON has reviewed a species page, and set its next review date.

  python3 data/scripts/mark_reviewed.py "Queen's Wreath"                   # DRY RUN (default): shows what would be written
  python3 data/scripts/mark_reviewed.py PSBP-00014 PSBP-00092 --apply --head 1a2b3c4d
  python3 data/scripts/mark_reviewed.py "Anhinga" --date 2026-11-02 --apply --head 1a2b3c4d     # review done on another day

WHO MAY RUN IT. Randy, by pressing "Mark reviewed" on the Species Manager To do tab (which calls mark() below), or Claude when
Randy says "mark review complete" for a named species. No publisher, merge script, batch tool or nightly job ever calls it,
and no Claude session presses the button (CLAUDE.md rule 15). This module and assign_review_due.py are the only code that may
write a review date, and only this one writes `reviewed_on`.

WHAT IT WRITES, and nothing else, on the named published plant or wildlife record:
  reviewed_on  the review date (today unless --date): a person looked at the live page, thought about how it reflects the
               park, checked anything questionable, and certified it until the next review
  review_due   the next date: the review date plus the tier's interval (Feature 8 months, Standard 10, Background 12; no
               tier counts as Standard), moved by up to a month either way to the least crowded week (ties go to the date
               closest to the target), counting every other species' due date, plants and wildlife together
An early review is fine: the next date counts from the day of the review, not from the old due date.
Accepts a PSBP id or a common name (exact, any case). Records are re-read on every call; the file must round-trip
byte-identically. The command line adds a git HEAD guard against a commit made mid-session. Standard library only.
Every log line carries the date and the time.
"""
import sys, json, subprocess, datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
SRC = REPO / "data/sources"          # module attribute: a test points it at a throwaway copy
FILES = ["plant_signage.json", "wildlife_signage.json"]
INTERVAL = {"Feature": 8, "Standard": 10, "Background": 12}
LEEWAY_DAYS = 31


def log(m): print(datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), m, flush=True)
def head(): return subprocess.run(["git", "-C", str(REPO), "rev-parse", "--short=8", "HEAD"], capture_output=True, text=True).stdout.strip()
def monday(d): return d - datetime.timedelta(days=d.weekday())


def add_months(d, n):
    y, m = divmod(d.month - 1 + n, 12)
    y += d.year; m += 1
    last = [31, 29 if y % 4 == 0 and (y % 100 or y % 400 == 0) else 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31][m - 1]
    return datetime.date(y, m, min(d.day, last))


def _load():
    docs, recs = {}, []
    for fname in FILES:
        raw = (SRC / fname).read_text(encoding="utf-8")
        doc = json.loads(raw)
        if raw != json.dumps(doc, indent=2, ensure_ascii=False) + "\n":
            raise ValueError(f"{fname} does not round-trip byte-identically")
        docs[fname] = doc
        recs += [r for r in doc["species"] if r.get("status") == "html"]
    return docs, recs


def _find(recs, token):
    t = str(token).strip().lower()
    hits = [r for r in recs if r["id"].lower() == t] or [r for r in recs if r["common_name"].lower() == t]
    if not hits: raise ValueError(f"no published species matches {token!r} (use the PSBP id or the exact common name)")
    if len(hits) > 1: raise ValueError(f"{token!r} matches {len(hits)} species: use the PSBP id")
    return hits[0]


def _write_fields(docs, values):
    """values: {id: (reviewed_on or None, review_due or None)}. reviewed_on then review_due sit right after feature_tier
    (else at the end of the record); None removes the field. Every other field is left exactly as it was."""
    for fname, doc in docs.items():
        out, touched = [], 0
        for r in doc["species"]:
            if r["id"] in values:
                rev, due = values[r["id"]]
                ordered, placed = {}, False
                def add():
                    if rev is not None: ordered["reviewed_on"] = rev
                    if due is not None: ordered["review_due"] = due
                for k, v in r.items():
                    if k in ("reviewed_on", "review_due"): continue
                    ordered[k] = v
                    if k == "feature_tier": add(); placed = True
                if not placed: add()
                r = ordered; touched += 1
            out.append(r)
        if touched:
            doc["species"] = out
            (SRC / fname).write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def mark(tokens, when=None, apply=False, expect_head=None):
    """Plan (and with apply=True write) reviews for the named species. Returns one dict per species:
    id, name, tier, reviewed_on, review_due, target, shift_days, others_that_week, prev_reviewed_on, prev_review_due.
    Raises ValueError for an unknown or ambiguous name, a bad date, or a file that does not round-trip."""
    if isinstance(tokens, str): tokens = [tokens]
    try:
        when = when if isinstance(when, datetime.date) else (datetime.date.fromisoformat(when) if when else datetime.date.today())
    except ValueError:
        raise ValueError(f"{when!r} is not a date (use YYYY-MM-DD)")
    docs, recs = _load()
    targets = [_find(recs, t) for t in tokens]
    target_ids = {r["id"] for r in targets}
    load = {}
    for r in recs:
        if r["id"] in target_ids or not r.get("review_due"): continue
        w = monday(datetime.date.fromisoformat(r["review_due"]))
        load[w] = load.get(w, 0) + 1
    results, values = [], {}
    for r in targets:
        tier = r.get("feature_tier") or "Standard"
        target = add_months(when, INTERVAL.get(tier, 10))
        cands, d = [], monday(target - datetime.timedelta(days=LEEWAY_DAYS))
        while d <= target + datetime.timedelta(days=LEEWAY_DAYS):
            cands.append(d); d += datetime.timedelta(days=7)
        best = min(cands, key=lambda w: (load.get(w, 0), abs((w - target).days), w))
        others = load.get(best, 0)
        load[best] = others + 1
        values[r["id"]] = (when.isoformat(), best.isoformat())
        results.append(dict(id=r["id"], name=r["common_name"], tier=tier, reviewed_on=when.isoformat(), review_due=best.isoformat(),
                            target=target.isoformat(), shift_days=(best - target).days, others_that_week=others,
                            interval_months=INTERVAL.get(tier, 10),
                            prev_reviewed_on=r.get("reviewed_on"), prev_review_due=r.get("review_due")))
    if apply:
        if expect_head is not None and head() != expect_head: raise ValueError(f"HEAD is {head()}, expected {expect_head}")
        _write_fields(docs, values)
    return results


def restore(items):
    """Put exact earlier values back (the Undo): items = [{id, reviewed_on, review_due}], None removes a field."""
    docs, recs = _load()
    known = {r["id"] for r in recs}
    values = {}
    for it in items:
        if it["id"] not in known: raise ValueError(f"{it['id']} is not a published species")
        values[it["id"]] = (it.get("reviewed_on"), it.get("review_due"))
    _write_fields(docs, values)
    return values


def main():
    names, i = [], 1
    while i < len(sys.argv):
        a = sys.argv[i]
        if a in ("--date", "--head"): i += 2; continue
        if a.startswith("--"): i += 1; continue
        names.append(a); i += 1
    if not names: sys.exit(__doc__)
    def arg(n): return sys.argv[sys.argv.index(n) + 1] if n in sys.argv else None
    apply_ = "--apply" in sys.argv
    if apply_ and arg("--head") != head(): sys.exit(f"ABORT: --apply needs --head {head()} (the commit you expect); got {arg('--head')}")
    try:
        results = mark(names, arg("--date"), apply=apply_, expect_head=arg("--head") if apply_ else None)
    except ValueError as e:
        sys.exit(f"ABORT: {e}")
    for r in results:
        log(f"{r['id']} {r['name']} ({r['tier']}): reviewed_on {r['reviewed_on']}; review_due {r['review_due']} (target {r['target']}, "
            f"{r['shift_days']:+d} days, {r['others_that_week']} others already that week); was due {r['prev_review_due']}, previously reviewed {r['prev_reviewed_on'] or 'never'}")
    if not apply_: log("DRY RUN: nothing written (pass --apply --head <commit>)")
    else: log("WROTE. Next: git diff --stat (a couple of lines per species). Refresh the To do tab to see the list move.")


if __name__ == "__main__":
    main()
