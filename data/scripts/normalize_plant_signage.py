#!/usr/bin/env python3
"""
normalize_plant_signage.py — one-time cleanup pass for data/sources/plant_signage.json

What it does (and NOTHING else):
  1. quick_hits:  **phrase**  ->  <b>phrase</b>      (the bold you actually wanted;
                  the plant publisher now honors <b> via _allow_bold). Any leftover
                  unbalanced ** is stripped so no literal asterisks survive.
  2. all OTHER string fields: strip ** to plain text and remove bullet glyphs (•).
                  Body fields are HTML-escaped by the renderer, so <b>/markdown can't
                  render there — plain prose is the only honest option.
  3. PSBP-00573 (African Milk Weed): de-duplicate. edibility.detail is trimmed to its
                  verdict (subtractive — we keep the existing lead sentences and drop the
                  toxicity narrative that was pasted in), then points to Toxicity.
                  toxicity.people is de-bulleted into plain prose (no facts changed).
  4. collapse double spaces and runaway blank lines everywhere.

Safety:
  - Dry-run by default: prints every change, writes nothing.
  - --write performs an atomic replace (temp file + os.replace) and bumps meta.updated.
  - Invents no facts; the African Milk Weed edibility trim is purely subtractive + a pointer.

Usage:
  python3 normalize_plant_signage.py --path data/sources/plant_signage.json           # dry run
  python3 normalize_plant_signage.py --path data/sources/plant_signage.json --write    # apply
"""
import argparse, json, os, re, sys, tempfile
from datetime import datetime

BULLET_CHARS = "•▪‣◦"
AFRICAN_MILKWEED_ID = "PSBP-00573"

# ── string transforms ────────────────────────────────────────────────────────

def collapse_ws(s):
    s = re.sub(r"\n{3,}", "\n\n", s)
    s = re.sub(r"[ \t]{2,}", " ", s)
    s = re.sub(r"\n[ \t]+", "\n", s)          # drop indent left by bullet removal
    # tidy stray space before punctuation left by bullet removal
    s = re.sub(r"[ \t]+([,.;:])", r"\1", s)
    return s.strip()

def quickhit_bold(s):
    """**x** -> <b>x</b> for quick hits; drop any leftover unbalanced **."""
    out = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", s)
    out = out.replace("**", "")          # unbalanced remnants
    return collapse_ws(out)

def strip_markup(s):
    """Body fields: no bold survives the renderer -> strip ** and bullet glyphs."""
    out = s.replace("**", "")
    out = re.sub(r"[ \t]*[" + BULLET_CHARS + r"][ \t]*", " ", out)
    return collapse_ws(out)

def african_milkweed_edibility(detail):
    """Subtractive trim: keep the verdict up to the first bullet / blank-line list,
    drop the pasted-in toxicity narrative, append a pointer to Toxicity."""
    # cut at the first bullet glyph or the first double-newline (start of the list)
    cut = len(detail)
    for marker in ["\n\n•", "\n•", "•"]:
        i = detail.find(marker)
        if i != -1:
            cut = min(cut, i)
    head = strip_markup(detail[:cut])
    if not head.endswith("."):
        head += "."
    return head + " See the Toxicity section below for handling precautions."

# ── walk & record ────────────────────────────────────────────────────────────

class Change:
    def __init__(self, sid, name, field, kind, before, after):
        self.sid, self.name, self.field, self.kind = sid, name, field, kind
        self.before, self.after = before, after

def snip(s, n=90):
    s = s.replace("\n", "⏎")
    return s if len(s) <= n else s[:n] + "…"

def process_species(sp, changes):
    sid, name = sp.get("id"), sp.get("common_name")

    # 1) quick_hits -> <b>
    qh = sp.get("quick_hits")
    if isinstance(qh, list):
        for i, item in enumerate(qh):
            if isinstance(item, str) and ("**" in item or "  " in item):
                new = quickhit_bold(item)
                if new != item:
                    kind = "**→<b>" if "<b>" in new else "cleanup"
                    changes.append(Change(sid, name, f"quick_hits[{i}]", kind, item, new))
                    qh[i] = new

    # 3) African Milk Weed targeted de-dup (before generic body strip)
    if sid == AFRICAN_MILKWEED_ID:
        ed = sp.get("edibility") or {}
        if isinstance(ed.get("detail"), str):
            new = african_milkweed_edibility(ed["detail"])
            if new != ed["detail"]:
                changes.append(Change(sid, name, "edibility.detail", "dedup-trim", ed["detail"], new))
                ed["detail"] = new

    # 2) every other string leaf -> strip markup (skip quick_hits, already handled)
    def walk(obj, path):
        if isinstance(obj, str):
            if "**" in obj or any(b in obj for b in BULLET_CHARS) or "  " in obj:
                new = strip_markup(obj)
                if new != obj:
                    return new, True
            return obj, False
        if isinstance(obj, list):
            changed = False
            for i in range(len(obj)):
                obj[i], c = walk(obj[i], f"{path}[{i}]")
                changed = changed or c
            return obj, changed
        if isinstance(obj, dict):
            changed = False
            for k in list(obj.keys()):
                obj[k], c = walk(obj[k], f"{path}.{k}" if path else k)
                changed = changed or c
            return obj, changed
        return obj, False

    for field in list(sp.keys()):
        if field == "quick_hits":
            continue
        before_json = json.dumps(sp[field], ensure_ascii=False)
        sp[field], changed = walk(sp[field], field)
        if changed:
            after_json = json.dumps(sp[field], ensure_ascii=False)
            if before_json != after_json:
                changes.append(Change(sid, name, field, "strip-markup",
                                      snip(before_json, 120), snip(after_json, 120)))

# ── atomic write ─────────────────────────────────────────────────────────────

def write_atomic(path, data):
    d = os.path.dirname(os.path.abspath(path))
    fd, tmp = tempfile.mkstemp(dir=d, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.write("\n")
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)

# ── main ─────────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--path", required=True)
    ap.add_argument("--write", action="store_true", help="apply changes (default is dry run)")
    args = ap.parse_args()

    with open(args.path, encoding="utf-8") as f:
        data = json.load(f)

    changes = []
    for sp in data.get("species", []):
        process_species(sp, changes)

    if not changes:
        print("No changes needed — file is already clean.")
        return

    # report
    by_kind = {}
    for c in changes:
        by_kind.setdefault(c.kind, 0)
        by_kind[c.kind] += 1
    affected = sorted({c.sid for c in changes})

    print(f"{'DRY RUN — no file written' if not args.write else 'APPLYING CHANGES'}")
    print(f"{len(changes)} change(s) across {len(affected)} species\n")
    print("By kind:", ", ".join(f"{k}={v}" for k, v in sorted(by_kind.items())), "\n")

    cur = None
    for c in changes:
        if c.sid != cur:
            cur = c.sid
            print(f"── {c.sid}  {c.name}")
        print(f"    [{c.kind}] {c.field}")
        print(f"        before: {snip(c.before)}")
        print(f"        after : {snip(c.after)}")

    if args.write:
        data.setdefault("meta", {})["updated"] = datetime.now().isoformat(timespec="seconds")
        write_atomic(args.path, data)
        print(f"\n✓ Wrote {args.path} atomically. meta.updated bumped.")
    else:
        print("\nRe-run with --write to apply.")

if __name__ == "__main__":
    main()
