#!/usr/bin/env python3
"""PSBP premium plant-page generator.

Reads the content master + photo-credits CSV and emits phone-card HTML pages
matching the reference standard (PSBP-00003-Buccaneer-Palm.html).

Usage:
    python generate_plant_pages.py ID [ID ...]
    python generate_plant_pages.py --tier Feature
"""
import argparse, html, re, sys, unicodedata
import pandas as pd

MASTER  = "/mnt/user-data/uploads/PSBP_Master_Plant_Signage.xlsx"
CREDITS = "/mnt/user-data/uploads/Plants_and_Wildlife_Photo_Credits.csv"
REF     = "/mnt/user-data/uploads/PSBP-00003-Buccaneer-Palm.html"
OUTDIR  = "/home/claude/work/pages"

# --- how to display photographer handles. Real names for park staff; iNat
# handles otherwise. Flip OWN_PHOTO_PLAIN to True to drop the license/iNat tail
# on Randall's own shots entirely.
DISPLAY_NAME   = {"randall_carter": "Randall Carter"}
OWN_PHOTO_PLAIN = False
OWN_HANDLE      = "randall_carter"

LICENSE_DISPLAY = {
    "cc-by-nc": "CC BY-NC", "cc-by": "CC BY", "cc-by-sa": "CC BY-SA",
    "cc-by-nd": "CC BY-ND", "cc-by-nc-sa": "CC BY-NC-SA",
    "cc-by-nc-nd": "CC BY-NC-ND", "cc0": "CC0", "pd": "Public Domain",
}
OK_STATUS = ("OK",)  # status must start with one of these to publish a credit

def esc(t):  # escape + keep our em dashes / curly quotes intact
    return html.escape(str(t), quote=False).strip()

def slug(name):
    """Common Name -> filename stem: drop apostrophes, non-alnum -> hyphen."""
    s = name.replace("\u2019", "").replace("'", "")
    s = re.sub(r"[^A-Za-z0-9]+", "-", s).strip("-")
    return s

CHUNK_CHARS = 300   # a breakless block longer than this gets sentence-chunked
ABBR = ["U.S.", "e.g.", "i.e.", "Mt.", "St.", "ca.", "approx.", "No.",
        "vs.", "Dr.", "Fig.", "cf.", "etc.", "spp.", "var.", "subsp."]

def strip_cite(t):
    return re.sub(r"\s*\[\d+\]", "", str(t))

def norm(text):
    """Strip citations; turn Unicode line/para separators into blank-line breaks."""
    t = strip_cite(text)
    t = re.sub(r"[\u2028\u2029]+", "\n\n", t)
    return t.strip()

def sentences(t):
    prot = t
    for a in ABBR:
        prot = prot.replace(a, a.replace(".", "\u0001"))
    parts = re.split(r"(?<=[.!?])\s+(?=[\"'\u201c]?[A-Z0-9])", prot)
    return [p.replace("\u0001", ".").strip() for p in parts if p.strip()]

def chunk(body, n=2):
    """Group a long breakless body into ~n-sentence pieces for readability."""
    s = sentences(body)
    if len(s) <= n:
        return [body.strip()]
    return [" ".join(s[i:i + n]) for i in range(0, len(s), n)]

def is_subhead(line):
    line = line.strip()
    return 0 < len(line) <= 62 and line[:1].isupper() and not re.search(r"[.!?:;]$", line)

def segments(text):
    """-> list of (subheading|None, body). Honors blank-line/Unicode breaks and
    'Subheading\\nbody' structure; sentence-chunks long breakless prose."""
    blocks = [b for b in re.split(r"\n\s*\n", norm(text)) if b.strip()]
    segs = []
    for b in blocks:
        lines = [l.strip() for l in b.split("\n") if l.strip()]
        if len(lines) >= 2 and is_subhead(lines[0]):
            sub, body = lines[0], " ".join(lines[1:])
            if len(body) > CHUNK_CHARS:
                cks = chunk(body)
                segs.append((sub, cks[0]))
                segs.extend((None, c) for c in cks[1:])
            else:
                segs.append((sub, body))
        else:
            body = " ".join(lines)
            if len(body) > CHUNK_CHARS:
                segs.extend((None, c) for c in chunk(body))
            else:
                segs.append((None, body))
    return segs

def paras(text):
    return [b for b in re.split(r"\n\s*\n", norm(text)) if b.strip()]

LABEL = r"[A-Z][A-Za-z][A-Za-z'’&/ .\-]{0,28}?"
INLINE_LABEL = re.compile(r"(?:^|(?<=[.;]) )(" + LABEL + r"):\s+")

def parse_kv(text, default_label="Detail"):
    """Parse Size/Growing fields in any of three master styles:
      A) one 'Key: value' per line
      B) several 'Key: value.' segments inline on one line
      C) pure prose with no Key: labels
    Returns [(label|None, value), ...]; prose lead/blobs get default_label."""
    t = norm(text)
    if not t:
        return []
    lines = [l.strip() for l in t.split("\n") if l.strip()]
    kv_lines = [l for l in lines if re.match(r"^" + LABEL + r":\s", l)]
    # Style A: most lines are 'Key: value'
    if len(kv_lines) >= 2 and len(kv_lines) >= len(lines) - 1:
        out = []
        for l in lines:
            m = re.match(r"^(" + LABEL + r"):\s*(.+)$", l)
            if m:
                out.append((m.group(1).strip(), m.group(2).strip()))
            elif out:                       # continuation of previous value
                lab, val = out[-1]
                out[-1] = (lab, (val + " " + l).strip())
            else:
                out.append((default_label, l))
        return out
    # Style B / C: single blob; find inline labels
    blob = " ".join(lines)
    matches = list(INLINE_LABEL.finditer(blob))
    if not matches:
        return [(default_label, blob.strip().rstrip("."))]   # pure prose
    out = []
    if matches[0].start() > 0:
        lead = blob[:matches[0].start()].strip(" .")
        if lead:
            out.append((default_label, lead))
    for i, m in enumerate(matches):
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(blob)
        val = blob[start:end].strip().rstrip(".").strip()
        out.append((m.group(1).strip(), val))
    return out

def count_text_blocks(raw):
    """Count non-empty text blocks separated by any style of line break.
    Normalises every known separator first so the count is break-agnostic."""
    if not raw or str(raw).strip().lower() in ("", "nan"):
        return 0
    t = str(raw)
    # Normalise every line-break variant to \n
    for ch in ("\u2028", "\u2029", "\r\n", "\r"):
        t = t.replace(ch, "\n")
    blocks = [b.strip() for b in re.split(r"\n\s*\n", t) if b.strip()]
    return len(blocks)

def parse_repro(text):
    """Split 'Label: body' or 'Label - body' segments (bodies may run
    multiple sentences).  Normalises Unicode line/para separators first
    so U+2028/U+2029 boundaries are treated the same as newlines."""
    t = str(text).strip()
    t = t.replace(" ", "\n").replace(" ", "\n")
    chunks = re.split(
        r"(?:^|\n)\s*([A-Z][A-Za-z’'&/ ]{2,40}?)(?:\s*:\s*|\s+[-–—]\s+)",
        "\n" + t,
    )
    items = []
    # If the text starts with unlabeled prose (no "Label:" pattern),
    # keep it with a default heading so it is not silently dropped.
    lead = re.sub(r"\s+", " ", chunks[0]).strip() if chunks else ""
    if lead:
        items.append(("Reproduction", lead))
    it = iter(chunks[1:])
    for label, body in zip(it, it):
        body = re.sub(r"\s+", " ", body).strip()
        if body:
            items.append((label.strip(), body))
    return items

def title_case_label(s):
    return s

def badge_native(v):
    v = str(v).strip().lower()
    if v.startswith("non"):
        return '<span class="badge badge-neutral">🌍 Non-Native</span>'
    if "native" in v:
        return '<span class="badge badge-native">🌿 Florida Native</span>'
    return ""

def badge_invasive(v):
    v = str(v).strip().lower()
    if v == "red":
        return '<span class="badge badge-danger">🚫 Invasive</span>'
    if v == "yellow":
        return '<span class="badge badge-warn">⚠️ Watch List</span>'
    if v == "green":
        return '<span class="badge badge-green">✅ Not Invasive</span>'
    return ""

SEVERITY = {"green": 0, "yellow": 1, "red": 2, "nan": 0, "": 0}
def safety_level(row):
    """Determine danger level from TOXICITY color codes only.
    Edibility is deliberately excluded — 'not a significant food plant'
    is not a safety concern and should never trigger a caution badge.
    The Edibility text still appears in the section body for context."""
    tox = SEVERITY.get(str(row.get("Toxicity Green/Yellow/Red", "")).strip().lower(), 0)
    dog = SEVERITY.get(str(row.get("Toxic to Dogs Green/Yellow/Red", "")).strip().lower(), 0)
    return max(tox, dog)  # 0 safe, 1 caution, 2 toxic

def safety_block(row):
    lvl = safety_level(row)
    edib = esc(row.get("Edibility", "")) if str(row.get("Edibility", "")) != "nan" else ""
    people = str(row.get("Toxic to People", "")); people = esc(people) if people != "nan" else ""
    dogs = str(row.get("Toxic to Dogs", "")); dogs = esc(dogs) if dogs != "nan" else ""
    tox_line = " ".join(t for t in [people, dogs] if t and t.lower() not in ("nan",))
    ps = []
    if edib:
        ps.append(f"<p>{edib}</p>")
    if tox_line and tox_line != edib:
        ps.append(f"<p>{tox_line}</p>")
    body = "".join(ps) or "<p>No specific edibility or toxicity data on record.</p>"
    if lvl == 2:
        sect, badge = "plant-toxic-section", '<span class="badge badge-danger">☠️ Toxic</span>'
    elif lvl == 1:
        sect, badge = "plant-caution-section", '<span class="badge badge-warn">⚠️ Mild Caution</span>'
    else:
        sect, badge = "plant-safe-section", '<span class="badge badge-safe">✅ Non-Toxic</span>'
    icon = "⚠️" if lvl else "✅"
    section_html = (
        f'  <div class="{sect}">\n'
        f'    <div class="plant-section-header"><span class="plant-section-icon">{icon}</span>'
        f'<span class="plant-section-title">Edibility &amp; Toxicity</span></div>\n'
        f'    <div class="plant-section-body">{body}</div>\n'
        f'  </div>'
    )
    return badge, section_html

FULLWIDTH_KEYS = {"growth rate", "soil tolerances", "soil tolerance", "habit", "watering", "note", "notes"}
def data_grid(row):
    items = parse_kv(row.get("Size", ""), default_label="Size") \
          + parse_kv(row.get("Growing Conditions", ""), default_label="Growing Conditions")
    cells = []
    for label, value in items:
        if not value:
            continue
        lab = (label or "Detail").strip()
        full = lab.lower() in FULLWIDTH_KEYS or len(value) > 24
        cls = "data-item full-width" if full else "data-item"
        cells.append(
            f'    <div class="{cls}"><div class="data-label">{esc(lab)}</div>'
            f'<div class="data-value">{esc(value)}</div></div>'
        )
    return "\n".join(cells)

def credit_line(crow):
    if crow is None:
        return '📷 Photo Coming Soon'
    pub = str(crow.get("Publish-OK", "")).strip().lower()
    status = str(crow.get("Status", "")).strip()
    if pub != "yes" or not any(status.startswith(s) for s in OK_STATUS):
        return '📷 Photo Coming Soon'
    handle = str(crow.get("Photographer", "")).strip()
    name = DISPLAY_NAME.get(handle, handle)
    lic = LICENSE_DISPLAY.get(str(crow.get("License", "")).strip().lower(),
                              str(crow.get("License", "")).upper())
    if handle == OWN_HANDLE and OWN_PHOTO_PLAIN:
        return f'📷 Photo by <strong>{esc(name)}</strong>'
    return f'📷 Photo by <strong>{esc(name)}</strong> · {esc(lic)} · via iNaturalist'

def md_bold(s):
    # convert **anchor** markers (authored in the master) into <strong> tags.
    # esc() runs first and leaves * untouched, so markers survive escaping.
    return re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", s)

def li_list(text, dark=False, bold=False):
    out = []
    for sub, body in segments(text):
        b = md_bold(esc(body)) if bold else esc(body)
        if sub:
            s = md_bold(esc(sub)) if bold else esc(sub)
            out.append(f"    <li><strong>{s}</strong> {b}</li>")
        else:
            out.append(f"    <li>{b}</li>")
    return "\n".join(out)

def p_list(text):
    out = []
    for sub, body in segments(text):
        if sub:
            out.append(f"<p><strong>{esc(sub)}</strong> {esc(body)}</p>")
        else:
            out.append(f"<p>{esc(body)}</p>")
    return "".join(out)

def build(row, crow, head):
    common = str(row["Common Name"]).strip()
    sci = str(row["Botanical Name"]).strip()
    family = str(row["Family"]).strip()
    pid = str(row["PSBP Species ID"]).strip()
    category = str(row["Category"]).strip().replace(" and ", " & ")
    stem = f"{pid}-{slug(common)}"

    native_badge = badge_native(row.get("Native or Non-native", ""))
    inv_badge = badge_invasive(row.get("Invasive Green/Yellow/Red", ""))
    safe_badge, safety_html = safety_block(row)
    badges = "\n    ".join(b for b in [native_badge, inv_badge, safe_badge] if b)

    # repro
    repro_items = parse_repro(row.get("Reproduction and Identification", ""))
    repro_html = "\n".join(
        f'<div class="repro-item"><div class="repro-label">{esc(l)}</div><p>{esc(b)}</p></div>'
        for l, b in repro_items
    )
    # Block-count sanity check: did we lose any sections?
    repro_raw = row.get("Reproduction and Identification", "")
    expected_blocks = count_text_blocks(repro_raw)
    actual_sections = len(repro_items)
    if expected_blocks and actual_sections < expected_blocks:
        print(f"  ⚠️  {pid} Repro: {expected_blocks} text blocks in spreadsheet "
              f"but only {actual_sections} sections parsed — check for dropped content")


    # aliases
    raw = str(row.get("Alternate Names", ""))
    parts = re.split(r"\n| · | — | - |·", raw) if raw and raw != "nan" else []
    aliases = [a.strip() for a in parts
               if a.strip() and a.strip() != "nan" and a.strip().lower() != common.lower()]
    alias_html = "".join(f'<span class="alias-tag">{esc(a)}</span>' for a in aliases)

    # notes
    notes = str(row.get("Other Notes", "")).strip()
    notes_section = ""
    if notes and notes.lower() != "nan":
        notes = re.sub(r"^[A-Z][A-Z ]{2,}:\s*", "", notes)  # strip ALLCAPS label
        notes_section = (
            '  <div class="plant-section">\n'
            '    <div class="plant-section-header"><span class="plant-section-icon">📝</span>'
            '<span class="plant-section-title">Notes</span></div>\n'
            f'    <div class="plant-section-body">{p_list(notes)}</div>\n'
            '  </div>\n'
        )

    alias_section = ""
    if alias_html:
        alias_section = (
            '  <div class="plant-section">\n'
            '    <div class="plant-section-header"><span class="plant-section-icon">🏷️</span>'
            '<span class="plant-section-title">Also Known As</span></div>\n'
            f'    <div class="alias-list">{alias_html}</div>\n'
            '  </div>\n'
        )

    head = head.replace("Buccaneer Palm · Palma Sola Botanical Park",
                        f"{common} · Palma Sola Botanical Park")

    # Placeholder-aware hero/credit: a "Photo Coming Soon" plant gets no image
    # link (clicking a placeholder is pointless) and no credit line (the hero
    # graphic already says Coming Soon).
    cl = credit_line(crow)
    is_placeholder = "Coming Soon" in cl
    if is_placeholder:
        hero_media = f'  <img src="../photos/{stem}.jpg" alt="{esc(common)} at Palma Sola Botanical Park" loading="lazy">'
        credit_block = ""
    else:
        hero_media = (f'  <a class="plant-hero-link" href="../photos/{stem}.jpg" target="_blank" rel="noopener">\n'
                      f'    <img src="../photos/{stem}.jpg" alt="{esc(common)} at Palma Sola Botanical Park" loading="lazy">\n'
                      f'  </a>')
        credit_block = f'<div class="plant-credit">{cl}</div>\n'

    body = f'''</head>
<body>
<div id="nav-placeholder"></div>

<div class="plant-wrap">
<div class="plant-hero">
{hero_media}
  <div class="plant-hero-overlay">
    <div class="plant-hero-category">{esc(category)}</div>
    <div class="plant-hero-name">{esc(common)}</div>
  </div>
</div>
<div class="plant-sci-band">
  <span class="plant-sci-name">{esc(sci)}</span>
  <a class="plant-family-tag" href="../nature.html?family={esc(family)}">{esc(family)}</a>
</div>
{credit_block}<div class="plant-content">
  <div class="plant-status-row">
    {badges}
  </div>
  <div class="plant-section">
    <div class="plant-section-header"><span class="plant-section-icon">⚡</span><span class="plant-section-title">Quick Hits</span></div>
    <ul class="quick-hits-list">
{li_list(row.get("Quick Hits", ""), bold=True)}
    </ul>
  </div>
  <div class="plant-section">
    <div class="plant-section-header"><span class="plant-section-icon">🌍</span><span class="plant-section-title">Origin</span></div>
    <div class="plant-section-body">{p_list(row.get("Origin", ""))}</div>
  </div>
  <div class="plant-more-info">
    <div class="plant-section-header"><span class="plant-section-icon">🔍</span><span class="plant-section-title">More Information</span></div>
    <ul class="more-info-list">
{li_list(row.get("More Information", ""))}
    </ul>
  </div>
  <div class="plant-section">
    <div class="plant-section-header"><span class="plant-section-icon">🦋</span><span class="plant-section-title">Wildlife Value</span></div>
    <div class="plant-section-body">{p_list(row.get("Wildlife Value", ""))}</div>
  </div>
  <div class="plant-section">
    <div class="plant-section-header"><span class="plant-section-icon">🔬</span><span class="plant-section-title">Reproduction &amp; Identification</span></div>
    <div class="repro-list">
{repro_html}
    </div>
  </div>
  <div class="plant-section">
    <div class="plant-section-header"><span class="plant-section-icon">📐</span><span class="plant-section-title">Size &amp; Growing Conditions</span></div>
    <div class="data-grid">
{data_grid(row)}
    </div>
  </div>
{safety_html}
{notes_section}{alias_section}
  <a class="all-plants-link" href="../nature.html#plants">🌿 Explore More Plants</a>
</div>
</div><!-- /.plant-wrap -->
<a class="plant-float-back" href="../nature.html#plants">🌿 All Plants</a>

<div id="footer-placeholder"></div>
<script src="../js/site.js"></script>
<script>
injectShared({{ inatBar: false }});
</script>
</body>
</html>'''
    return stem, head + body


# ── Cell-level sanitization ──────────────────────────────────────────
# Applied once to every cell in the master spreadsheet immediately after
# loading.  Fixes invisible Unicode gremlins introduced by different
# Claude sessions writing to the same Google Sheet.
_SANITIZE_MAP = str.maketrans({
    "\u2018": "'",   # left single curly  -> straight
    "\u2019": "'",   # right single curly -> straight (apostrophe)
    "\u201C": '"',   # left double curly  -> straight
    "\u201D": '"',   # right double curly -> straight
    "\u00A0": " ",   # non-breaking space -> normal space
    "\u200B": "",    # zero-width space   -> remove
    "\u200C": "",    # zero-width non-joiner -> remove
    "\u200D": "",    # zero-width joiner  -> remove
    "\uFEFF": "",    # byte-order mark    -> remove
    "\u00AD": "",    # soft hyphen        -> remove
})

def sanitize_cell(val):
    """Normalise a single cell value from the master spreadsheet."""
    if pd.isna(val):
        return val
    s = str(val)
    s = s.translate(_SANITIZE_MAP)
    # U+2028/2029 line/para separators -> real newlines
    s = s.replace("\u2028", "\n").replace("\u2029", "\n")
    # Collapse runs of 3+ blank lines into 2
    s = re.sub(r"\n{3,}", "\n\n", s)
    return s

def sanitize_df(df):
    """Clean every string cell in the dataframe in place."""
    for col in df.columns:
        df[col] = df[col].map(sanitize_cell)
    return df

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("ids", nargs="*")
    ap.add_argument("--tier")
    args = ap.parse_args()

    df = pd.read_excel(MASTER, sheet_name="PSBP_Plants", dtype=str)
    sanitize_df(df)
    cr = pd.read_csv(CREDITS, dtype=str)
    cr = cr[cr["Primary"] == "Yes"].set_index("PSBP ID")
    head = open(REF).read()
    head = head[:head.index("</head>")]

    import os
    os.makedirs(OUTDIR, exist_ok=True)

    ids = list(args.ids)
    if args.tier:
        ids += list(df[df["Feature Tier"] == args.tier]["PSBP Species ID"])
    seen, ordered = set(), []
    for i in ids:
        if i not in seen:
            seen.add(i); ordered.append(i)

    built = []
    for pid in ordered:
        sub = df[df["PSBP Species ID"] == pid]
        if sub.empty:
            print(f"!! {pid} not in master"); continue
        row = sub.iloc[0]
        crow = cr.loc[pid] if pid in cr.index else None
        stem, htmltext = build(row, crow, head)
        path = f"{OUTDIR}/{stem}.html"
        open(path, "w").write(htmltext)
        cl = credit_line(crow)
        flag = "" if "Coming Soon" not in cl else "  <-- PHOTO COMING SOON"
        built.append(path)
        print(f"OK {stem}.html | {cl}{flag}")
    print(f"\n{len(built)} pages -> {OUTDIR}")

if __name__ == "__main__":
    main()
