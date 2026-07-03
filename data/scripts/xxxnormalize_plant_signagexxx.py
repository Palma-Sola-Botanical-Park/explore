#!/usr/bin/env python3
"""
normalize_plant_signage.py — cleanup + data-standard migration for plant_signage.json

Standard enforced:
  A. Paragraphs are list items; strings never contain newlines.
  B. No paragraph exceeds MAX_PARA_CHARS (readability cap). Over-long content is split
     at natural (blank-line / labelled) boundaries first, then at sentence boundaries.

Transforms:
  1. quick_hits: **phrase** -> <b>phrase</b> (honored by the publisher). Stray ** stripped.
  2. origin / other_notes / internal_notes: string -> LIST of paragraph strings.
  3. edibility.detail / toxicity.people / toxicity.dogs: string -> LIST of paragraph strings
     (these were the wall-of-text offenders).
  4. Every prose paragraph is made newline-free and capped: any item longer than
     MAX_PARA_CHARS is split into multiple items at sentence boundaries.
  5. All other string leaves: ** and bullet glyphs stripped; newlines collapsed to spaces.
  6. PSBP-00573 edibility.detail trimmed to its verdict (subtractive) + pointer to Toxicity.

Safety: dry-run by default; --write does an atomic replace and bumps meta.updated.
Invents no facts.

Usage:
  python3 normalize_plant_signage.py --path data/sources/plant_signage.json
  python3 normalize_plant_signage.py --path data/sources/plant_signage.json --write
"""
import argparse, json, os, re, tempfile
from datetime import datetime

BULLET_CHARS = "•▪‣◦"
AFRICAN_MILKWEED_ID = "PSBP-00573"
MAX_PARA_CHARS = 400          # readability cap — tune here (mobile-first park signage)

LIST_PROSE   = ("quick_hits", "more_information", "wildlife_value",
                "origin", "other_notes", "internal_notes")
CONVERT_STR  = ("origin", "other_notes", "internal_notes")
NESTED_PROSE = (("edibility", "detail"), ("toxicity", "people"), ("toxicity", "dogs"))

# ── string helpers ───────────────────────────────────────────────────────────

def _tidy(s):
    s = re.sub(r"[ \t]*[" + BULLET_CHARS + r"][ \t]*", " ", s)
    s = re.sub(r"\s*\n\s*", " ", s)
    s = re.sub(r"[ \t]{2,}", " ", s)
    s = re.sub(r"[ \t]+([,.;:])", r"\1", s)
    return s.strip()

def clean_item(s, allow_bold):
    if allow_bold:
        s = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", s)
    s = s.replace("**", "")
    return _tidy(s)

def split_paras(s):
    return [p.strip() for p in re.split(r"\n\s*\n", s) if p.strip()]

def sentence_split(text):
    return [s.strip() for s in re.findall(r".+?(?:[.!?](?=\s|$)|$)", text.strip()) if s.strip()]

def cap_paragraph(p, cap=MAX_PARA_CHARS):
    """Split a paragraph that exceeds the cap into readable chunks, never mid-sentence.
    Prefers to start a new chunk at a 'Label:' lead (e.g. 'Skin Contact:')."""
    if len(p) <= cap:
        return [p]
    chunks, cur = [], ""
    for s in sentence_split(p):
        starts_label = bool(re.match(r"[A-Z][A-Za-z ()/'-]{1,40}:", s))
        if cur and (len(cur) + 1 + len(s) > cap or starts_label):
            chunks.append(cur)
            cur = s
        else:
            cur = (cur + " " + s).strip()
    if cur:
        chunks.append(cur)
    return chunks

def to_para_list(value, allow_bold):
    """str or list -> list of clean, newline-free, length-capped paragraph strings."""
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
        return value, 0, False
    cleaned, capped = [], False
    for x in raw:
        if isinstance(x, str):
            c = clean_item(x, allow_bold)
            pieces = cap_paragraph(c)
            if len(pieces) > 1:
                capped = True
            cleaned.extend(pieces)
        else:
            cleaned.append(x)
    return cleaned, len(cleaned), capped

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

def convert_field(sp, field_label, value, changes, sid, name, allow_bold, was_str, is_convert):
    new, n, capped = to_para_list(value, allow_bold)
    if new == value:
        return value
    n_before = 1 if was_str else (len(value) if isinstance(value, list) else 1)
    if was_str and is_convert:
        kind = "to-list-split" if n > 1 else "to-list"
        changes.append(Change(sid, name, field_label, kind, f"string → {n} item(s)"))
    elif capped:
        changes.append(Change(sid, name, field_label, "length-split", f"{n_before} → {n} items (over {MAX_PARA_CHARS} chars)"))
    elif n != n_before:
        changes.append(Change(sid, name, field_label, "item-split", f"{n_before} → {n} items"))
    elif allow_bold and any(isinstance(x, str) and "<b>" in x for x in new):
        changes.append(Change(sid, name, field_label, "**→<b>", f"{sum(x.count('<b>') for x in new if isinstance(x,str))} span(s)"))
    else:
        changes.append(Change(sid, name, field_label, "cleanup", "whitespace/markup"))
    return new

def process_species(sp, changes):
    sid, name = sp.get("id"), sp.get("common_name")

    # African Milk Weed edibility de-dup first (string, before list conversion)
    if sid == AFRICAN_MILKWEED_ID:
        ed = sp.get("edibility") or {}
        if isinstance(ed.get("detail"), str):
            new = african_milkweed_edibility(ed["detail"])
            if new != ed["detail"]:
                changes.append(Change(sid, name, "edibility.detail", "dedup-trim", f"{len(ed['detail'])}→{len(new)} chars"))
                ed["detail"] = new

    # Top-level prose list fields
    for field in LIST_PROSE:
        if field not in sp or sp[field] is None:
            continue
        sp[field] = convert_field(sp, field, sp[field], changes, sid, name,
                                  allow_bold=(field == "quick_hits"),
                                  was_str=isinstance(sp[field], str),
                                  is_convert=(field in CONVERT_STR))

    # Nested prose (edibility.detail, toxicity.people, toxicity.dogs)
    for parent, child in NESTED_PROSE:
        d = sp.get(parent)
        if isinstance(d, dict) and d.get(child) is not None:
            d[child] = convert_field(sp, f"{parent}.{child}", d[child], changes, sid, name,
                                     allow_bold=False, was_str=isinstance(d[child], str), is_convert=True)

    # Every other string leaf: scrub + enforce no-newline
    handled_parents = {p for p, _ in NESTED_PROSE}
    def walk(obj, top=None):
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
        # skip the nested-prose children we already converted to lists
        if field in handled_parents and isinstance(sp[field], dict):
            for k in list(sp[field].keys()):
                if (field, k) in NESTED_PROSE:
                    continue
                sp[field][k], c = walk(sp[field][k])
                if c:
                    changes.append(Change(sid, name, f"{field}.{k}", "scrub", "markup/newline"))
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
    ap.add_argument("--write", action="store_true")
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

    DETAIL = {"to-list-split", "item-split", "length-split", "**→<b>", "dedup-trim"}
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
