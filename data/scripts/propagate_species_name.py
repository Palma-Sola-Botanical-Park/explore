#!/usr/bin/env python3
"""
propagate_species_name.py — carry a species' new common name through everything that follows.

    python3 data/scripts/propagate_species_name.py                       # preview drift, all species
    python3 data/scripts/propagate_species_name.py PSBP-00064            # preview one (already renamed)
    python3 data/scripts/propagate_species_name.py PSBP-00064 --apply    # propagate it
    python3 data/scripts/propagate_species_name.py PSBP-00064 "Jungle Flame" --apply   # rename AND propagate

Two ways in, like propagate_names.py:
    1. You renamed the record in Species Manager. The photo rows still carry the old
       name, which is how the script finds out what the old name was. Run it with the id.
    2. You give it the id and the new name, and it renames the record too.
Preview is the default; nothing is written without --apply. One species at a time.

WHY IT EXISTS
    A rename used to be "edit the name and republish", which handles the page and
    its /p/ stub and nothing else: photo rows kept the old name (Jungle Flame, 6
    rows, found by the audit), other species' prose kept naming it, and signs
    already printed carry the OLD page address, which a rename turns into a 404.
    This lists every consequence, does the mechanical ones, and leaves the rest
    as a checklist of exactly where the old name still is.

THREE KINDS OF CONSEQUENCE
    A. Handled by --apply
         the species record's common_name (plant / wildlife / research master),
         the photo rows in photo_credits.json,
         for a published species: the page under its new filename (the old page is
         removed), the /p/<id> stub, plants.json / wildlife.json, publish_state.json
         (all through the publisher's --generate, the one write path for pages).
    B. Needs your words (never auto-edited; found by search, shown with snippets)
         other species' prose that names the old name, hand-kept files such as
         tours-brief.html and landmarks.json, the species' own provenance file,
         and private docs. Edit them, then republish the pages listed.
    C. Cannot be fixed from here
         signs already printed. Today's signs are BETA and will be discarded
         (Randy, 2026-10-04), so this only notes how many carry the old page
         address (/plants/PSBP-xxxxx-Old-Name.html, which a rename turns into a
         404). Signs built since the /p/ flip use /p/00719 and are unaffected.

Standard library only. Run from anywhere; it finds the repo from its own path.
"""
import argparse
import datetime
import glob
import importlib.util
import json
import os
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import psbp_common as pc                                           # noqa: E402

REPO = pc.REPO
SOURCES = REPO / "data" / "sources"
SIGNS_ROOT = Path(os.environ.get("PSBP_SIGNS_ROOT") or os.path.expanduser("~/Documents/PSBP/signs_out"))
PARK_LIBRARY_DOCS = REPO.parent / "park-library" / "system docs"

MASTERS = [  # (label, path, kind) — searched in this order for the id
    ("plant_signage.json", SOURCES / "plant_signage.json", "plants"),
    ("wildlife_signage.json", SOURCES / "wildlife_signage.json", "wildlife"),
    ("research.json", SOURCES / "research.json", "research"),
]
# Text a rename can leave stale, searched for the OLD name. Generated output is
# deliberately absent (plants/, wildlife/, p/, plants.json, wildlife.json are
# rebuilt by the publisher), as are files that key on the id only.
SKIP_DIRS = {".git", "photos", "plants", "wildlife", "p", "node_modules", "__pycache__",
             "images", "docs", "fonts", ".claude"}
SKIP_FILES = {"plants.json", "wildlife.json", "publish_state.json", "photo_credits.json",
              "sign_copy_SUPERSEDED.json", "phenology.json", "photo_workbench.json",
              "placements.json", "plant_signage.json", "wildlife_signage.json", "research.json"}
TEXT_EXT = (".json", ".html", ".js", ".md", ".txt", ".csv", ".css")


def now():
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def find_record(pid):
    for label, path, kind in MASTERS:
        data = pc.load_json(path, {"species": []})
        for sp in data.get("species", []):
            if sp.get("id") == pid:
                return label, path, kind, sp
    return None


def photo_row_names(pid, credits=None):
    credits = credits or pc.load_json(pc.PHOTO_CREDITS_JSON, {"photos": []})
    from collections import Counter
    return Counter((p.get("common_name") or "") for p in credits.get("photos", [])
                   if p.get("psbp_id") == pid)


def scan_all_drift():
    """[(id, record name, {stale name: rows})] for every species whose photo rows
    disagree with its record. Scientific-name drift is ignored by decision."""
    credits = pc.load_json(pc.PHOTO_CREDITS_JSON, {"photos": []})
    out = []
    for label, path, kind in MASTERS:
        for sp in pc.load_json(path, {"species": []}).get("species", []):
            names = photo_row_names(sp["id"], credits)
            stale = {n: c for n, c in names.items() if n and n != sp.get("common_name")}
            if stale:
                out.append((sp["id"], sp.get("common_name", ""), stale, sp.get("status")))
    return sorted(out)


def snippet(text, needle, width=48):
    i = text.find(needle)
    a, b = max(0, i - width), min(len(text), i + len(needle) + width)
    return ("…" if a else "") + re.sub(r"\s+", " ", text[a:b]) + ("…" if b < len(text) else "")


def walk_strings(obj, path=""):
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield from walk_strings(v, f"{path}.{k}")
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            yield from walk_strings(v, f"{path}[{i}]")
    elif isinstance(obj, str):
        yield path, obj


def publisher_for(kind):
    name = "plant_publisher" if kind == "plants" else "wildlife_publisher"
    spec = importlib.util.spec_from_file_location(name, HERE / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def printed_signs(pid):
    """Beta sign PDFs: the top level of signs_out and archive/. builds/ is excluded:
    since the 2026-10-04 flip those carry the /p/ address, which survives a rename."""
    found = glob.glob(str(SIGNS_ROOT / f"sign_{pid}_*.pdf"))
    for d in glob.glob(str(SIGNS_ROOT / "archive" / "*")):
        found += glob.glob(str(Path(d) / f"sign_{pid}_*.pdf"))
    return sorted(found)


def collect(pid, old, kind, rec):
    """Everything the rename touches or leaves behind."""
    r = {"warnings": [], "other_records": [], "files": [], "docs": [], "signs": printed_signs(pid)}
    # same-name collision
    for label, path, k in MASTERS:
        for sp in pc.load_json(path, {"species": []}).get("species", []):
            if sp.get("id") != pid and (sp.get("common_name") or "").strip().lower() == r.get("new", "").lower() and r.get("new"):
                r["warnings"].append(f"{sp['id']} in {label} already has this common name")
    aliases = [a for a in (rec.get("also_known_as") or rec.get("aliases") or []) if isinstance(a, str)]
    if any(a.lower() == old.lower() for a in aliases):
        r["warnings"].append("the old name is also listed in this record's aliases")
    # B1: other species' prose
    for label, path, k in MASTERS:
        for sp in pc.load_json(path, {"species": []}).get("species", []):
            if sp.get("id") == pid:
                continue
            for p, text in walk_strings(sp):
                if old in text:
                    r["other_records"].append((sp["id"], sp.get("common_name", ""), label, p, snippet(text, old)))
    # B2: hand-kept files in the repo
    for root, dirs, files in os.walk(REPO):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for f in files:
            if not f.endswith(TEXT_EXT) or f in SKIP_FILES:
                continue
            p = Path(root) / f
            try:
                t = p.read_text(encoding="utf-8")
            except Exception:                                      # noqa: BLE001
                continue
            if old in t:
                r["files"].append((str(p.relative_to(REPO)), t.count(old), snippet(t, old)))
    # B3: this species' own provenance file (keyed by id, may quote the name)
    # is already covered by the walk above (data/sources/provenance/*.json).
    # B4: private docs
    if PARK_LIBRARY_DOCS.is_dir():
        for p in sorted(PARK_LIBRARY_DOCS.glob("*.md")):
            try:
                t = p.read_text(encoding="utf-8")
            except Exception:                                      # noqa: BLE001
                continue
            if old in t:
                r["docs"].append((p.name, t.count(old)))
    return r


def show(pid, old, new, label, kind, rec, found, filenames, rename_record):
    status = rec.get("status")
    print(f"{now()}  rename {pid}: {old!r} -> {new!r}   ({label}, status={status})\n")
    print("A. HANDLED BY --apply")
    print(f"   - {label}: common_name " + ("(set to the new name)" if rename_record else "(already renamed)"))
    n_rows = sum(1 for p in pc.load_json(pc.PHOTO_CREDITS_JSON, {"photos": []}).get("photos", [])
                 if p.get("psbp_id") == pid)
    print(f"   - photo_credits.json: {n_rows} photo row(s)")
    if status == "html":
        print(f"   - page: {filenames[0]}  ->  {filenames[1]}   (old page removed)")
        print(f"   - /p/{pid.split('-')[-1]}/ stub, {kind}.json, publish_state.json  (via the publisher)")
    else:
        print(f"   - no page, no stub (status={status}); identity is free to change here")
    print("\nB. NEEDS YOUR WORDS (not auto-edited)")
    if not (found["other_records"] or found["files"] or found["docs"]):
        print("   nothing else names it")
    seen = set()
    for pid2, name2, lab, path, snip in found["other_records"]:
        print(f"   - {pid2} {name2} [{lab} {path}]: {snip}")
        seen.add(pid2)
    for f, n, snip in found["files"]:
        print(f"   - {f} (x{n}): {snip}")
    for d, n in found["docs"]:
        print(f"   - park-library/system docs/{d} (x{n})")
    if seen:
        print("   After editing those records, republish their pages: "
              + ", ".join(sorted(seen)))
    print("\nC. CANNOT BE FIXED FROM HERE")
    if found["signs"] and status == "html":
        print(f"   - {len(found['signs'])} beta sign(s) on record carry the old page address and will "
              f"404 after this. Beta signs are to be discarded, so nothing to do.")
    else:
        print("   - nothing")
    for w in found["warnings"]:
        print(f"   ! {w}")


def apply(pid, old, new, label, path, kind, rec, filenames, rename_record):
    status = rec.get("status")
    print(f"\n{now()}  APPLYING")
    # A1. the record (only when a new name was given): re-read, change, write
    #     (rule 4: never write a stale copy)
    data = pc.load_json(path, {"species": []})
    rec2 = next(s for s in data["species"] if s["id"] == pid)
    if rename_record:
        if rec2.get("common_name") != old:
            sys.exit(f"stopped: {pid} is now {rec2.get('common_name')!r} on disk, not {old!r}. "
                     f"Someone (Species Manager?) changed it since the preview. Run the preview again.")
        rec2["common_name"] = new
        pc.write_json_atomic(path, data)
        print(f"  ✓ {label}: common_name set")
    elif rec2.get("common_name") != new:
        sys.exit(f"stopped: {pid} is now {rec2.get('common_name')!r} on disk, not {new!r}. Run the preview again.")
    else:
        print(f"  = {label}: already says {new!r}")
    # A2. photo rows
    credits = pc.load_json(pc.PHOTO_CREDITS_JSON, {"photos": []})
    n = 0
    for ph in credits.get("photos", []):
        if ph.get("psbp_id") == pid and ph.get("common_name") != new:
            ph["common_name"] = new
            n += 1
    if n:
        pc.write_json_atomic(pc.PHOTO_CREDITS_JSON, credits)
    print(f"  ✓ photo_credits.json: {n} row(s) updated")
    # A3. page, stub, indexes, publish state — the publisher is the one write path
    if status == "html":
        script = HERE / ("plant_publisher.py" if kind == "plants" else "wildlife_publisher.py")
        proc = subprocess.run([sys.executable, str(script), "--generate", pid],
                              capture_output=True, text=True, cwd=str(REPO))
        out = (proc.stdout + proc.stderr).strip()
        print("  " + out.replace("\n", "\n  "))
        newp = REPO / kind / filenames[1]
        oldp = REPO / kind / filenames[0]
        stub = REPO / "p" / pid.split("-")[-1] / "index.html"
        ok = newp.is_file() and not (oldp.is_file() and oldp.name != newp.name)
        if kind == "plants":
            ok = ok and stub.is_file() and filenames[1] in stub.read_text(encoding="utf-8")
        print(f"  {'✓' if ok else '✗'} new page present, old page gone"
              + (", stub points at it" if kind == "plants" else ""))
        if proc.returncode != 0 or not ok:
            print("  !! the publish step did not finish cleanly; read the lines above before anything else")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("id", nargs="?", help="PSBP id; omit to preview drift across all species")
    ap.add_argument("new_name", nargs="?", help="give this to rename the record too")
    ap.add_argument("--old", help="the previous name, if the photo rows no longer show it")
    ap.add_argument("--apply", action="store_true", help="write the changes (default: preview only)")
    a = ap.parse_args()

    if not a.id:
        drift = scan_all_drift()
        if not drift:
            print(f"{now()}  no drift: every photo row carries its species' current common name.")
            return
        print(f"{now()}  {len(drift)} species where photo rows carry a different common name:\n")
        for pid, cur, stale, status in drift:
            print(f"  {pid} {cur!r} (status={status}): rows say "
                  + ", ".join(f"{n!r} x{c}" for n, c in stale.items()))
        print("\nRun with one id to preview it, then add --apply.")
        return

    hit = find_record(a.id)
    if not hit:
        sys.exit(f"{a.id} is not in plant_signage.json, wildlife_signage.json or research.json")
    label, path, kind, rec = hit
    rename_record = bool(a.new_name)
    if rename_record:
        old, new = rec.get("common_name", ""), a.new_name.strip()
    else:
        new = rec.get("common_name", "")
        stale = {n: c for n, c in photo_row_names(a.id).items() if n and n != new}
        old = a.old or (next(iter(stale)) if len(stale) == 1 else "")
        if not old:
            if len(stale) > 1:
                sys.exit(f"The photo rows carry several old names ({', '.join(map(repr, stale))}); "
                         f"say which with --old.")
            sys.exit(f"{a.id} {new!r}: the photo rows already match the record, so there is nothing "
                     f"to propagate. To search for leftovers of an earlier name, add --old 'Previous Name'.")
    if not new or new == old:
        sys.exit("The new name is empty or the same as the old one.")
    filenames = ("", "")
    if rec.get("status") == "html":
        pub = publisher_for(kind)
        filenames = (pub.page_filename(a.id, old), pub.page_filename(a.id, new))
        if filenames[0] == filenames[1]:
            print("(the page filename does not change: only spacing or case that the filename ignores)\n")

    found = collect(a.id, old, kind, rec)
    found["new"] = new
    found["warnings"] = []
    for l2, p2, k2 in MASTERS:                  # collision check needs `new`
        for sp in pc.load_json(p2, {"species": []}).get("species", []):
            if sp.get("id") != a.id and (sp.get("common_name") or "").strip().lower() == new.lower():
                found["warnings"].append(f"{sp['id']} in {l2} already has the common name {new!r}")
    aliases = [x for x in (rec.get("also_known_as") or rec.get("aliases") or []) if isinstance(x, str)]
    if any(x.lower() == old.lower() for x in aliases):
        found["warnings"].append("the old name is also listed in this record's aliases")

    show(a.id, old, new, label, kind, rec, found, filenames, rename_record)
    if not a.apply:
        print("\nPREVIEW ONLY. Nothing was written. Add --apply to do section A.")
        return
    apply(a.id, old, new, label, path, kind, rec, filenames, rename_record)
    print("\nAudit (errors only):")
    proc = subprocess.run([sys.executable, str(HERE / "audit_psbp.py")], capture_output=True, text=True, cwd=str(REPO))
    lines = proc.stdout.splitlines()
    summary = [l.strip() for l in lines if l.strip().startswith("TOTAL")]
    errors, grab = [], False
    for l in lines:
        if l.startswith("  ERROR ("):
            grab = True
            continue
        if grab and (l.startswith("  WARN") or l.startswith("  INFO") or l.startswith("===")):
            grab = False
        if grab and l.strip().startswith("- "):
            errors.append(l.strip())
    print("  " + (summary[0] if summary else "(no summary)"))
    for e in errors[:12]:
        print("   " + e[:200])
    print("\nStill yours to do: section B above, then `git status` and commit in GitHub Desktop.")


if __name__ == "__main__":
    main()
