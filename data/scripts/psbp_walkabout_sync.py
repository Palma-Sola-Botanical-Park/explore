#!/usr/bin/env python3
"""Refresh the three lookup tables baked into walkabout.html.

WHY THIS EXISTS
    walkabout.html is opened as a local file as often as it is served, and a page on
    file:// cannot fetch a sibling JSON — the browser blocks it. So three lookups are
    EMBEDDED in the HTML rather than loaded at runtime:

        NAMES            login -> display name        (photographer_names.json)
        KNOWN_TAXA       every taxon the park knows   (plant_signage + wildlife + research)
        PUBLISHED_TAXA   every taxon with a page      (plant_signage + wildlife, status=html)

    They are snapshots. Editing the JSON does nothing until this runs. Randy asked the
    question that proves the trap is real: "updating photographer_names doesn't do that
    inside this HTML because the snapshot was made to it already, correct?" — correct,
    and a comment in the file was never going to be enough.

    Two bugs have already come from stale or partial versions of these lists: a Bright
    Futures student showing on the office screen as "theblackd0g13", and 91 published
    wildlife records being reported as unresearched because PUBLISHED_TAXA held plants only.

USAGE
    python3 data/scripts/psbp_walkabout_sync.py          # show what would change
    python3 data/scripts/psbp_walkabout_sync.py --write  # apply it

    Run it after editing photographer_names.json, or after publishing species.
"""
import argparse, json, os, re, sys

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SRC  = os.path.join(REPO, "data", "sources")
PAGE = os.path.join(REPO, "walkabout.html")


def _rows(path, key="species"):
    d = json.load(open(os.path.join(SRC, path), encoding="utf-8"))
    if isinstance(d, dict):
        d = d.get(key, d)
    return d if isinstance(d, list) else []


def build():
    plants   = _rows("plant_signage.json")
    wildlife = _rows("wildlife_signage.json")
    research = _rows("research.json")

    names = {k: v["display_name"]
             for k, v in json.load(open(os.path.join(SRC, "photographer_names.json"),
                                        encoding="utf-8")).items()
             if k != "_note" and isinstance(v, dict) and v.get("display_name")}

    known = sorted({r["inat_taxon_id"] for r in plants + wildlife + research
                    if r.get("inat_taxon_id")})
    published = sorted({r["inat_taxon_id"] for r in plants + wildlife
                        if r.get("status") == "html" and r.get("inat_taxon_id")})
    return names, known, published


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true", help="apply the changes")
    a = ap.parse_args()

    names, known, published = build()
    html = open(PAGE, encoding="utf-8").read()

    targets = [
        ("NAMES",           r"const NAMES=\{.*?\};",
         "const NAMES=" + json.dumps(names, ensure_ascii=False) + ";", len(names)),
        ("KNOWN_TAXA",      r"const KNOWN_TAXA=new Set\(\[[^\]]*\]\);",
         "const KNOWN_TAXA=new Set(" + json.dumps(known) + ");", len(known)),
        ("PUBLISHED_TAXA",  r"const PUBLISHED_TAXA=new Set\(\[[^\]]*\]\);",
         "const PUBLISHED_TAXA=new Set(" + json.dumps(published) + ");", len(published)),
    ]

    changed = 0
    for label, pat, repl, n in targets:
        m = re.search(pat, html, re.S)
        if not m:
            print(f"  ! {label}: could not find it in walkabout.html — has the line been edited?")
            return 1
        same = m.group(0) == repl
        print(f"  {'=' if same else '~'} {label:15} {n:4} entries  "
              f"{'unchanged' if same else 'WOULD CHANGE' if not a.write else 'updated'}")
        if not same:
            changed += 1
            html = html[:m.start()] + repl + html[m.end():]

    if not changed:
        print("\n  Nothing to do — walkabout.html already matches the sources.")
        return 0
    if not a.write:
        print(f"\n  {changed} list(s) are stale. Re-run with --write to apply.")
        return 0
    open(PAGE, "w", encoding="utf-8").write(html)
    print(f"\n  walkabout.html updated ({changed} list(s)).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
