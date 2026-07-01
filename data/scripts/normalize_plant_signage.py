#!/usr/bin/env python3
"""
normalize_plant_signage.py — cleanup + data-standard migration for plant_signage.json

Applies the "paragraphs = list items, strings never contain newlines" standard.

  1. quick_hits:  **phrase** -> <b>phrase</b> (the bold the plant publisher now honors
                  via _allow_bold). Leftover unbalanced ** is stripped.
  2. origin / other_notes / internal_notes: converted from a single string to a LIST of
                  paragraph strings (split on blank lines). One item per paragraph.
  3. All prose list fields (quick_hits, more_information, wildlife_value, origin,
                  other_notes, internal_notes): every item is made newline-free; any item
                  that smuggled a blank line is split into separate items.
  4. Every other string field (edibility.detail, toxicity.*, reproduction.*, etc.):
                  ** and bullet glyphs stripped, and any newline collapsed to a space so
                  the no-newline invariant holds dataset-wide.
  5. PSBP-00573 (African Milk Weed): edibility.detail trimmed to its verdict (subtractive)
                  and pointed at Toxicity.

Safety: dry-run by default (writes nothing). --write does an atomic replace and bumps
meta.updated. Invents no facts.

Usage:
  python3 normalize_plant_signage.py --path data/sources/plant_signage.json
  python3 normalize_plant_signage.py --path data/sources/plant_signage.json --write
"""
import argparse, json, os, re, tempfile
from datetime import datetime

BULLET_CHARS = "•▪‣◦"
AFRICAN_MILKWEED_ID = "PSBP-00573"

LIST_PROSE   = ("quick_hits", "more_information", "wildlife_value",
                "origin", "other_notes", "internal_notes")
CONVERT_STR  = ("origin", "other_notes", "internal_notes")   # were strings -> now lists

# ── string helpers ───────────────────────────────────────────────────────────

def _tidy(s):
    s = re.sub(r"[ \t]*[" + BULLET_CHARS + r"][ \t]*", " ", s)   # bullet glyphs -> space
    s = re.sub(r"\s*\n\s*", " ", s)                             # newline -> space (no newline survives)
    s = re.sub(r"[ \t]{2,}", " ", s)                           # collapse double spaces
    s = re.sub(r"[ \t]+([,.;:])", r"\1", s)                    # stray space before punctuation
    return s.strip()

def clean_item(s, allow_bold):
    """One newline-free paragraph string. allow_bold => convert **x** to <b>x</b>."""
    if allow_bold:
        s = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", s)
    s = s.replace("**", "")
    return _tidy(s)

def split_paras(s):
    return [p.strip() for p in re.split(r"\n\s*\n", s) if p.strip()]

def to_para_list(value, allow_bold):
    """str or list -> list of clean, newline-free paragraph strings."""
    raw = []
    if isinstance(value, str):
        raw = split_paras(value) or ([value] if value.strip() else [])
    elif isinstance(value, list):
        for it in value:
            if isinstance(it, str):
                raw.extend(split_paras(it) or ([it] if it.strip() else []))
            else:
                raw.append(it)
    else:
        return value, 0
    cleaned = [clean_item(x, allow_bold) if isinstance(x, str) else x for x in raw]
    return cleaned, len(cleaned)

def scrub_leaf(s):
    return _tidy(s.replace("**", ""))

# ── African Milk Weed targeted edibility de-dup (subtractive) ─────────────────

def african_milkweed_edibility(detail):
    cut = len(detail)
    for marker in ["\n\n•", "\n•", "•", "\n\nSkin Contact", "\nSkin Contact"]:
        i = detail.find(marker)
        if i != -1:
            cut = min(cut, i)
    head = scrub_leaf(detail[:cut])
    if not head.endswith("."):
        head += "."
    return head + " See the Toxicity section below for handling precautions."

# ── per-species processing ───────────────────────────────────────────────────

class Change:
    def __init__(self, sid, name, field, kind, note):
        self.sid, self.name, self.field, self.kind, self.note = sid, name, field, kind, note

def process_species(sp, changes):
    sid, name = sp.get("id"), sp.get("common_name")

    # African Milk Weed edibility de-dup first (stays a string; scrubbed below)
    if sid == AFRICAN_MILKWEED_ID:
        ed = sp.get("edibility") or {}
        if isinstance(ed.get("detail"), str):
            new = african_milkweed_edibility(ed["detail"])
            if new != ed["detail"]:
                changes.append(Change(sid, name, "edibility.detail", "dedup-trim",
                                      f"{len(ed['detail'])}→{len(new)} chars"))
                ed["detail"] = new

    # Prose list fields: convert / flatten / clean
    for field in LIST_PROSE:
        if field not in sp or sp[field] is None:
            continue
        before = sp[field]
        allow_bold = (field == "quick_hits")
        new, n = to_para_list(before, allow_bold)
        if new == before:
            continue
        was_str = isinstance(before, str)
        n_before = 1 if was_str else len(before)
        if was_str and field in CONVERT_STR:
            kind = "to-list-split" if n > 1 else "to-list"
            changes.append(Change(sid, name, field, kind, f"string → {n} item(s)"))
        elif n != n_before:
            changes.append(Change(sid, name, field, "item-split", f"{n_before} → {n} items"))
        elif allow_bold and any("<b>" in x for x in new if isinstance(x, str)):
            changes.append(Change(sid, name, field, "**→<b>", f"{sum(x.count('<b>') for x in new if isinstance(x,str))} span(s)"))
        else:
            changes.append(Change(sid, name, field, "cleanup", "whitespace/markup"))
        sp[field] = new

    # Every other string leaf: scrub + enforce no-newline
    def walk(obj):
        if isinstance(obj, str):
            new = scrub_leaf(obj)
            return new, (new != obj)
        if isinstance(obj, list):
            changed = False
            for i in range(len(obj)):
                obj[i], c = walk(obj[i]); changed = changed or c
            return obj, changed
        if isinstance(obj, dict):
            changed = False
            for k in list(obj.keys()):
                obj[k], c = walk(obj[k]); changed = changed or c
            return obj, changed
        return obj, False

    for field in list(sp.keys()):
        if field in LIST_PROSE:
            continue
        sp[field], changed = walk(sp[field])
        if changed:
            changes.append(Change(sid, name, field, "scrub", "markup/newline"))

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
        print("No changes needed — file already matches the standard.")
        return

    by_kind = {}
    for c in changes:
        by_kind[c.kind] = by_kind.get(c.kind, 0) + 1
    affected = sorted({c.sid for c in changes})

    print("DRY RUN — no file written" if not args.write else "APPLYING CHANGES")
    print(f"{len(changes)} change(s) across {len(affected)} species\n")
    print("By kind:", ", ".join(f"{k}={v}" for k, v in sorted(by_kind.items())), "\n")

    # Detail only the interesting kinds; summarize the bulk (plain wraps/scrubs) as counts.
    DETAIL = {"to-list-split", "item-split", "**→<b>", "dedup-trim"}
    detailed = [c for c in changes if c.kind in DETAIL]
    if detailed:
        print("Notable changes:")
        cur = None
        for c in detailed:
            if c.sid != cur:
                cur = c.sid
                print(f"  ── {c.sid}  {c.name}")
            print(f"       [{c.kind}] {c.field}: {c.note}")
        print()
    bulk = {k: v for k, v in by_kind.items() if k not in DETAIL}
    if bulk:
        print("Bulk (not itemized):", ", ".join(f"{k}={v}" for k, v in sorted(bulk.items())))
        print("  (to-list = single-paragraph field wrapped as a 1-item list; "
              "scrub/cleanup = whitespace or stray-markup tidy)")

    if args.write:
        data.setdefault("meta", {})["updated"] = datetime.now().isoformat(timespec="seconds")
        write_atomic(args.path, data)
        print(f"\n✓ Wrote {args.path} atomically. meta.updated bumped.")
    else:
        print("\nRe-run with --write to apply.")

if __name__ == "__main__":
    main()
