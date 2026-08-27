#!/usr/bin/env python3
"""
make_palm_signs.py — build ONE PDF containing only the palm signs.

For Dave. Selects the palms out of plant_signage.json using the SAME rule the
website's "Palm & Cycad" filter uses — form == "Palm & Cycad" — so the PDF and
the site always agree on what counts. Writes the ID list to signs_palms.txt,
hands it to the sign builder, and renames the combined output to
PALM_SIGNS_2up.pdf so the next run of make_signs.py can't overwrite it.

Run it from the same folder as make_signs.py:

    python3 make_palm_signs.py                     # build it
    python3 make_palm_signs.py --list              # show what's in / held back, build nothing
    python3 make_palm_signs.py --true-palms-only   # family Arecaceae only
    python3 make_palm_signs.py --sort common       # id | common | botanical (default)
    python3 make_palm_signs.py --where             # show which files it found, then stop

IT FINDS ITS OWN FILES. It reads the config out of make_signs.py (REPO, OUT_DIR,
and any *SIGN_COPY* / *SIGNAGE* path it defines), then falls back to searching
this folder, ./data/sources, the repo, and the repo's data/sources. If it still
comes up empty it tells you exactly where it looked. Override anything:

    python3 make_palm_signs.py --repo ~/Documents/GitHub/explore
    python3 make_palm_signs.py --signage /path/to/plant_signage.json
    python3 make_palm_signs.py --copy    /path/to/sign_copy.json
    python3 make_palm_signs.py --out     /path/to/output_folder

What counts as a palm
  • Anything with form == "Palm & Cycad" — matching the site filter. That's the
    true palms plus the cycads and the palm-shaped impostors (Screw Pine,
    Traveller's Palm, Madagascar Palm) the park groups with them.
  • --true-palms-only narrows to family Arecaceae.
  • Only status == "html" is included. Species still marked "spotted" have no
    finished page for the QR to point at, so they're listed at the end as
    "not yet" rather than silently dropped.
"""
import sys, os, re, json, glob, time, subprocess, shutil

HERE = os.path.dirname(os.path.abspath(__file__))
CWD = os.getcwd()

SIGN_SCRIPT = os.path.join(HERE, "make_signs.py")
BATCH_FILE  = os.path.join(HERE, "signs_palms.txt")
FINAL_PDF   = "PALM_SIGNS_2up.pdf"
# v2 names its combined output PRINT_SHEETS_2up.pdf; v1 called it
# ALL_SIGNS_combined.pdf. Both are checked, so this works either way.
COMBINED_NAMES = ["PRINT_SHEETS_2up.pdf", "ALL_SIGNS_combined.pdf"]

PALM_FORM   = "Palm & Cycad"    # the website filter — source of truth
PALM_FAMILY = "Arecaceae"       # the botanical family, for --true-palms-only


# ───────────────────── find the files, don't assume them ─────────────────────
def read_config_from_builder():
    """Pull REPO / OUT_DIR / any *SIGN_COPY* or *SIGNAGE* path straight out of
    make_signs.py, so this script always agrees with the builder. Parses the
    assignment lines only — it never imports or runs the builder."""
    cfg = {}
    if not os.path.exists(SIGN_SCRIPT):
        return cfg
    ns = {"os": os, "__file__": SIGN_SCRIPT}
    pat = re.compile(r'^([A-Z][A-Z0-9_]*)\s*=\s*(.+?)\s*(?:#.*)?$')
    for raw in open(SIGN_SCRIPT, encoding="utf-8", errors="replace"):
        m = pat.match(raw.rstrip("\n"))
        if not m:
            continue
        name, expr = m.group(1), m.group(2)
        if not re.fullmatch(r"[\w\s.,'\"()\[\]/~+-]*", expr):
            continue                      # only simple literal/path expressions
        try:
            ns[name] = eval(expr, {"__builtins__": {}}, ns)
        except Exception:
            continue
    for k, v in ns.items():
        if k in ("os", "__file__") or not isinstance(v, str):
            continue
        if k in ("REPO", "OUT_DIR") or "SIGN_COPY" in k or "SIGNAGE" in k or "COPY" in k:
            cfg[k] = v
    return cfg


def locate(filename, extra_first=(), repo=None):
    """Return (path, tried) — first existing candidate, plus everywhere we looked."""
    roots = [HERE, CWD,
             os.path.join(HERE, "data", "sources"),
             os.path.join(CWD, "data", "sources")]
    if repo:
        roots += [os.path.join(repo, "data", "sources"), repo]
    tried = []
    for cand in list(extra_first) + [os.path.join(r, filename) for r in roots]:
        if not cand:
            continue
        cand = os.path.expanduser(cand)
        if cand in tried:
            continue
        tried.append(cand)
        if os.path.isfile(cand):
            return cand, tried
    # last resort: shallow search of the repo
    if repo and os.path.isdir(repo):
        hits = glob.glob(os.path.join(repo, "**", filename), recursive=True)
        tried.append(os.path.join(repo, "**", filename))
        if hits:
            return hits[0], tried
    return None, tried


def die_missing(what, tried):
    print(f"\nCan't find {what}. Looked in:")
    for t in tried:
        print("   ", t)
    print(f"\nPoint at it directly:")
    flag = "--signage" if "signage" in what else "--copy"
    print(f"    python3 {os.path.basename(__file__)} {flag} /full/path/to/{what}")
    print(f"    python3 {os.path.basename(__file__)} --repo /full/path/to/your/repo")
    sys.exit(1)


def arg(args, name, default=None):
    return os.path.expanduser(args[args.index(name) + 1]) if name in args else default


def resolve(args):
    cfg = read_config_from_builder()
    repo = arg(args, "--repo") or cfg.get("REPO")

    sig_hint = [cfg.get(k) for k in cfg if "SIGNAGE" in k]
    cpy_hint = [cfg.get(k) for k in cfg if "SIGN_COPY" in k or "COPY" in k]

    sig = arg(args, "--signage")
    if not sig:
        sig, sig_tried = locate("plant_signage.json", sig_hint, repo)
        if not sig:
            die_missing("plant_signage.json", sig_tried)
    cpy = arg(args, "--copy")
    if not cpy:
        cpy, cpy_tried = locate("sign_copy.json", cpy_hint, repo)
        if not cpy:
            die_missing("sign_copy.json", cpy_tried)

    out_candidates = []
    for c in (arg(args, "--out"), cfg.get("OUT_DIR"),
              os.path.join(HERE, "signs_out"), os.path.join(CWD, "signs_out"),
              os.path.expanduser("~/Desktop/psbp_signs")):
        c = os.path.expanduser(c) if c else None
        if c and c not in out_candidates:
            out_candidates.append(c)
    return repo, sig, cpy, out_candidates


# ─────────────────────────── selection ───────────────────────────
def select(sig_path, cpy_path, sort_key="botanical", true_palms_only=False):
    sg = json.load(open(sig_path))
    copy = json.load(open(cpy_path))
    species = sg.get("species", sg)

    def is_palm(s):
        if true_palms_only:
            return s.get("taxonomy", {}).get("family") == PALM_FAMILY
        return s.get("form") == PALM_FORM

    palms = [s for s in species if is_palm(s)]
    ready   = [s for s in palms if s.get("status") == "html" and s["id"] in copy]
    no_copy = [s for s in palms if s.get("status") == "html" and s["id"] not in copy]
    not_pub = [s for s in palms if s.get("status") != "html"]

    keys = {"id":        lambda s: s["id"],
            "common":    lambda s: s["common_name"].lower(),
            "botanical": lambda s: s.get("botanical_name", "").lower()}
    ready.sort(key=keys.get(sort_key, keys["botanical"]))
    return ready, no_copy, not_pub


def line(s):
    fam = s.get("taxonomy", {}).get("family", "")
    tag = "" if fam == PALM_FAMILY else f"  (not a true palm — {fam})"
    return f"  {s['id']}  {s['common_name']:<26} {s.get('botanical_name',''):<32}{tag}"


def report(ready, no_copy, not_pub):
    true_palms = sum(1 for s in ready if s.get("taxonomy", {}).get("family") == PALM_FAMILY)
    print(f"\nIN — {len(ready)} signs ({true_palms} true palms, "
          f"{len(ready)-true_palms} cycads/lookalikes)\n" + "-" * 78)
    for s in ready:
        print(line(s))
    if no_copy:
        print(f"\nHELD BACK — published but no sign_copy.json entry ({len(no_copy)})\n" + "-" * 78)
        for s in no_copy:
            print(line(s))
    if not_pub:
        print(f"\nNOT YET — still 'spotted', no page for the QR to point at ({len(not_pub)})\n" + "-" * 78)
        for s in not_pub:
            print(line(s))
        print("\n  ^ these are the ones you and Dave walked that haven't been processed.")


# ─────────────────────────── main ───────────────────────────
def main():
    args = sys.argv[1:]
    repo, sig, cpy, out_candidates = resolve(args)

    print("Using:")
    print("   plant_signage.json :", sig)
    print("   sign_copy.json     :", cpy)
    print("   sign builder       :", SIGN_SCRIPT if os.path.exists(SIGN_SCRIPT) else "NOT FOUND")
    if "--where" in args:
        print("   output searched in :", ", ".join(out_candidates))
        return

    sort_key = arg(args, "--sort", "botanical")
    ready, no_copy, not_pub = select(sig, cpy, sort_key, "--true-palms-only" in args)
    report(ready, no_copy, not_pub)

    if "--list" in args or "--dry-run" in args:
        return
    if not ready:
        sys.exit("\nNothing to build.")

    # Bare IDs, one per line, nothing else. The builder reads each whole line as
    # an ID — a trailing "# name" comment becomes part of the ID and every
    # lookup fails. Only leading-# lines are safe, so the header is the one
    # comment allowed here. Do not "helpfully" annotate these lines.
    with open(BATCH_FILE, "w") as f:
        f.write("# Palm signs — generated by make_palm_signs.py, do not hand-edit\n")
        for s in ready:
            f.write(f"{s['id']}\n")
    print(f"\nWrote {BATCH_FILE} ({len(ready)} IDs)")

    if not os.path.exists(SIGN_SCRIPT):
        sys.exit(f"Can't find the sign builder at {SIGN_SCRIPT} — "
                 f"put this script next to make_signs.py.")

    # Clear stale combined PDFs so we can't rename an old file by mistake.
    for d in out_candidates:
        for n in COMBINED_NAMES:
            p = os.path.join(d, n)
            if os.path.exists(p):
                os.remove(p)
    started = time.time() - 2

    print(f"Running {os.path.basename(SIGN_SCRIPT)} …\n" + "=" * 78)
    r = subprocess.run([sys.executable, SIGN_SCRIPT, "--file", BATCH_FILE], cwd=HERE)
    print("=" * 78)
    if r.returncode != 0:
        sys.exit(f"Sign builder exited {r.returncode} — PDF not renamed.")

    built = None
    for d in out_candidates:
        for n in COMBINED_NAMES:
            p = os.path.join(d, n)
            if os.path.exists(p) and os.path.getmtime(p) >= started:
                built = p
                break
        if built:
            break
    if not built:
        print("\nThe signs built, but I couldn't find the combined PDF to rename. Looked for")
        print("   " + " / ".join(COMBINED_NAMES) + "   in:")
        for d in out_candidates:
            print("   ", d)
        print("Rename it by hand, or rerun with --out /path/to/that/folder.")
        return

    final = os.path.join(os.path.dirname(built), FINAL_PDF)
    shutil.move(built, final)
    print(f"\n✔  {len(ready)} palm signs → {final}")
    if not_pub:
        print(f"   ({len(not_pub)} more are logged but not yet published — "
              f"tell Dave they're in the queue.)")
    print("   Scan-test one before printing anything permanent.")


if __name__ == "__main__":
    main()
