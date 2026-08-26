#!/usr/bin/env python3
"""plant_publisher.py — Review, generate, and publish PSBP plant pages.

Reads plant_signage.json + photo_credits.json → generates HTML plant pages
and maintains plants.json (the search/card index).

Usage:
    python3 plant_publisher.py                  # Launch dashboard on http://localhost:8701
    python3 plant_publisher.py --generate-all   # Batch-generate HTML for all status=html species
    python3 plant_publisher.py --validate       # Compare existing HTML hero paths against photo_credits
    python3 plant_publisher.py --generate PSBP-00003  # Generate one species
    python3 plant_publisher.py --clean          # Remove non-html entries from plants.json
    python3 plant_publisher.py --demote PSBP-00003  # Pull back html → spotted

Dashboard workflow:
    1. Browse species by status (html / spotted / research)
    2. Review JSON data, hero photo, and generated preview
    3. Click "Publish" → generates HTML file + updates plants.json + sets status=html
"""

import http.server
import json
import os
import re
import sys
import textwrap
import webbrowser
from copy import deepcopy
from datetime import date
from html import escape as h
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from psbp_common import (
    REPO, SOURCES,
    PLANT_SIGNAGE_JSON as SIGNAGE_JSON,
    PHOTO_CREDITS_JSON as CREDITS_JSON,
    PLANTS_JSON, PLANTS_DIR, PHOTOS_DIR,
    load_json, write_json_atomic,
    display_name, build_credit_line,
    resolve_hero_credit, resolve_gallery_credits,
    delete_species_page,
)

PORT = 8701

# ── Data loading (thin wrappers over psbp_common paths) ─────────────────────

def load_signage():
    return load_json(SIGNAGE_JSON, {"species": []})

def load_credits():
    return load_json(CREDITS_JSON, {"meta": {}, "photos": []})

def load_plants_json():
    return load_json(PLANTS_JSON, [])

def build_hero_lookup(credits):
    from psbp_common import build_hero_lookup as _bhl
    return _bhl(credits, type_filter="Plant")

def build_gallery_lookup(credits):
    from psbp_common import build_gallery_lookup as _bgl
    return _bgl(credits, type_filter="Plant")

def build_species_lookup(signage):
    return {s["id"]: s for s in signage["species"]}

# ── Slug helper ─────────────────────────────────────────────────────────────

def slugify(name):
    """Buccaneer Palm → Buccaneer-Palm"""
    return re.sub(r"[^A-Za-z0-9-]", "", name.replace(" ", "-").replace("'", ""))

def page_filename(psbp_id, common_name):
    return f"{psbp_id}-{slugify(common_name)}.html"

# ── plants.json entry builder ───────────────────────────────────────────────

def _safety_word(level):
    """Traffic-light grade -> a word a visitor understands.

    plant_signage stores Green / Yellow / Red. "Green" sitting next to the word
    "toxic" on a card is ambiguous, so the card publishes the meaning instead of
    the colour. Anything unrecognised returns "" and simply drops out of the
    filter rather than being guessed at.
    """
    return {"green": "safe", "yellow": "caution", "red": "toxic"}.get(
        (level or "").strip().lower(), ""
    )


def _drought_bucket(text):
    """Free-text drought tolerance -> low | moderate | high | "".

    growing_conditions.drought_tolerance is prose written by a researcher —
    "high once established", "moderate; prefers consistent moisture",
    "excellent" — 122 distinct strings across 230 plants, so it can't be
    filtered as-is. This collapses it to three buckets and classifies 202 of the
    209 populated values. The 7 it can't place, plus the 21 blanks, return ""
    and drop out of the filter.
    """
    t = (text or "").lower()
    if not t:
        return ""
    if any(k in t for k in ("excellent", "high", "very good")):
        return "high"
    if "low" in t and "moderate" not in t:
        return "low"
    if any(k in t for k in ("moderate", "good", "medium")):
        return "moderate"
    if "low" in t:
        return "low"
    return ""


def build_plants_json_entry(species, hero):
    """Build one plants.json card entry from signage + hero photo."""
    pid = species["id"]
    cat = species.get("category", "").replace(" and ", " & ")
    # Butterfly relevance — read straight from the structured butterfly object
    # (plant_signage schema 1.4+). No more prose-scraping: larval_food / adult_food
    # are researched booleans and larval_species carries the named hosts.
    bf = species.get("butterfly") or {}
    larval_host    = bool(bf.get("larval_food"))
    nectar         = bool(bf.get("adult_food"))
    butterfly      = larval_host or nectar          # rolled-up "butterfly-relevant"
    larval_species = bf.get("larval_species") or []

    # Invasive — the researcher-owned watch_invasive flag (FISC/IFAS listing OR
    # observed local behavior; the old traffic-light invasive{} dict is retired).
    watch_invasive = bool(species.get("watch_invasive"))
    native         = bool(species.get("native"))
    rare_fruit     = bool(species.get("rare_fruit"))

    # Faceted classification for the browse filters.
    form = species.get("form") or ""
    # Editorial tags ONLY. The native / invasive / butterfly / rare-fruit facets
    # now come from the booleans above (single source of truth), so we strip the
    # tag duplicates and keep only tags with no boolean equivalent (e.g.
    # cultural-historical). Prevents stale tag counts from fighting the booleans.
    _DERIVED_TAGS = {"native", "watch-invasive", "butterfly-host", "rare-fruit"}
    tags = [t for t in (species.get("tags") or []) if t not in _DERIVED_TAGS]

    # Hero photo path and credit — resolve real name + license
    hero_credit = resolve_hero_credit(hero)

    if hero:
        photo = f"photos/{pid}/{hero['filename']}"
        focus = hero.get("focus") or "50% 50%"
    else:
        photo = ""
        focus = "50% 50%"

    return {
        "id": pid,
        "common": species["common_name"],
        "sci": species["botanical_name"],
        "family": (species.get("taxonomy") or {}).get("family", ""),
        "aliases": species.get("alternate_names") or [],
        "cat": cat,
        "form": form,                       # facet: Form dropdown
        "origin": "Native" if native else "Non-native",

        # ── Added 2026-08-26 — schema 1.5 curated sign copy on the card. ──
        # `origin` above is a two-value flag; it cannot say WHERE a plant is
        # from. `origin_short` is the place name in a dozen characters, and
        # `teaser` is a self-contained 100-200 char hook. Both were written for
        # the printed signs, where the length limit is real, which is why they
        # read tighter than anything derived from quick_hits at render time.
        # 230/230 populated. Emitted here so the browse drawer stops slicing
        # prose and just uses the curated line.
        "origin_short": (species.get("origin_short") or "").strip(),
        "teaser":       (species.get("teaser") or "").strip(),
        "native": native,
        "butterfly": butterfly,             # larval OR nectar (rollup + back-compat)
        "larval_host": larval_host,         # filter: larval host plant
        "nectar": nectar,                   # filter: adult nectar source
        "larval_species": larval_species,   # named hosts, for the butterfly page
        "watch_invasive": watch_invasive,   # filter: plants to watch / invasive (CANONICAL)
        "rare_fruit": rare_fruit,           # filter: rare-fruit collection
        "tags": tags,                       # facet: editorial tag chips (non-boolean)
        "photo": photo,
        "page": f"plants/{page_filename(pid, species['common_name'])}",
        # `credit` is the LOGIN — the stable key a profile page joins on.
        # `credit_name` is the DISPLAY string. Both, deliberately: logins don't
        # change and don't collide, names do both. This used to be fed
        # credit_name, so every card stored the name twice and the login never.
        "credit": hero_credit["credit_login"],
        "credit_name": hero_credit["credit_name"],
        "credit_license": hero_credit["credit_license"],
        "credit_line": hero_credit["credit_line"],
        "focus": focus,

        # ── Added 2026-08-18 — four fields the card index never carried. ──
        # searchable text: site.js has always scored matches against p.quick,
        # but nothing ever emitted it, so that branch compared against an empty
        # string on every plant. quick_hits is populated on all 230.
        "quick":   " ".join(species.get("quick_hits") or []),
        # facet: safe around a dog on a leash?  safe 143 / caution 50 / toxic 37
        "dogs":    _safety_word((species.get("toxicity") or {}).get("dogs_level")),
        # facet: can you eat it?                safe 114 / caution 81 / toxic 35
        "edible":  _safety_word((species.get("edibility") or {}).get("level")),
        # facet: how thirsty?                   high 100 / moderate 83 / low 19
        "drought": _drought_bucket(
            (species.get("growing_conditions") or {}).get("drought_tolerance")),
    }

# ── HTML page generator ────────────────────────────────────────────────────

# PLANT_CSS moved to css/plant-page.css on 2026-08-18 and is now linked, not
# inlined. It was pasted into all 230 generated pages, so a design change meant
# regenerating every file; it is now a one-file edit that ships instantly.
# The extraction was verbatim, verified by computed-style comparison.


def _format_label(key):
    """growth_rate → Growth rate, usda_zones → USDA zones"""
    label = key.replace("_", " ")
    # Special cases
    if label.lower().startswith("usda"):
        return "USDA " + label[5:]
    return label[0].upper() + label[1:]


def _data_grid_item(label, value, full_width=False):
    fw = ' full-width' if full_width else ''
    return f'    <div class="data-item{fw}"><div class="data-label">{h(label)}</div><div class="data-value">{h(value)}</div></div>'


def _should_be_full_width(value):
    return len(str(value)) > 30


def render_badges(species):
    """Chips retired by design decision (2026-07).

    All three former pills are gone: the Edibility & Toxicity section now
    carries safety honestly in the body (a single pill can't hold "edible
    fruit / toxic seed"), native/non-native lives in plants.json purely for
    index sorting, and invasive status is retained as data, not a pill.
    Kept as a no-op so the single call site still resolves; delete the call
    at the detail-header if you want it gone entirely.
    """
    return ""


def _allow_bold(text):
    """Escape all HTML, then restore ONLY <b>/</b> tags.

    Lets signage authors bold a keyword with <b>...</b> in quick hits while
    keeping every other character safely escaped — a stray < or & can't break
    the page or inject markup. Bold is the only tag permitted. Mirrors the
    identical helper in wildlife_publisher.py so both publishers share one
    bold convention (** was never honored anywhere; <b> is the standard).
    """
    safe = h(text or "")
    safe = safe.replace("&lt;b&gt;", "<b>").replace("&lt;/b&gt;", "</b>")
    safe = safe.replace("&lt;B&gt;", "<b>").replace("&lt;/B&gt;", "</b>")
    return safe


def render_quick_hits(species):
    items = species.get("quick_hits") or []
    if not items:
        return ""
    lines = []
    for item in items:
        lines.append(f"    <li>{_allow_bold(item)}</li>")
    return f"""  <div class="plant-section">
    <div class="plant-section-header"><span class="plant-section-icon">⚡</span><span class="plant-section-title">Quick Hits</span></div>
    <ul class="quick-hits-list">
{chr(10).join(lines)}
    </ul>
  </div>"""


MAX_PARA_CHARS = 400   # readability cap — no rendered paragraph may exceed this (tunable)

def _sentence_split(text):
    return [s.strip() for s in re.findall(r".+?(?:[.!?](?=\s|$)|$)", text.strip()) if s.strip()]

def _cap_paragraph(p, cap=MAX_PARA_CHARS):
    """Split an over-long paragraph into readable chunks, never mid-sentence, so the
    'no wall of text' rule is guaranteed at render time regardless of the data.
    Prefers to start a new chunk at a 'Label:' lead (e.g. 'Skin Contact:')."""
    p = (p or "").strip()
    if len(p) <= cap:
        return [p] if p else []
    chunks, cur = [], ""
    for s in _sentence_split(p):
        starts_label = bool(re.match(r"[A-Z][A-Za-z ()/'-]{1,40}:", s))
        if cur and (len(cur) + 1 + len(s) > cap or starts_label):
            chunks.append(cur); cur = s
        else:
            cur = (cur + " " + s).strip()
    if cur:
        chunks.append(cur)
    return chunks


def _paragraphs(value):
    """Normalize a prose field (list of paragraphs, or legacy newline string) into a
    list of clean paragraph strings, each guaranteed under the readability cap. Also
    splits any legacy string on blank lines so pre-migration data still renders right."""
    if value is None:
        return []
    items = []
    if isinstance(value, str):
        items = [p.strip() for p in re.split(r"\n\s*\n", value) if p.strip()]
    else:
        for item in value:
            if isinstance(item, str):
                items.extend(p.strip() for p in re.split(r"\n\s*\n", item) if p.strip())
    out = []
    for p in items:
        out.extend(_cap_paragraph(p))
    return out


def _render_paragraphs(value):
    return "\n      ".join(f"<p>{h(p)}</p>" for p in _paragraphs(value))


def _p_html(text):
    """Render a single (possibly over-long) string as one or more capped <p> blocks."""
    return "".join(f"<p>{h(p)}</p>" for p in _cap_paragraph((text or "").strip()))


def render_origin(species):
    body = _render_paragraphs(species.get("origin"))
    if not body:
        return ""
    return f"""  <div class="plant-section">
    <div class="plant-section-header"><span class="plant-section-icon">🌍</span><span class="plant-section-title">Origin</span></div>
    <div class="plant-section-body">{body}</div>
  </div>"""


def render_more_info(species):
    items = species.get("more_information") or []
    if not items:
        return ""
    li_items = []
    for item in items:
        li_items.append(f"    <li>{h(item)}</li>")
    return f"""  <div class="plant-more-info">
    <div class="plant-section-header"><span class="plant-section-icon">🔍</span><span class="plant-section-title">More Information</span></div>
    <ul class="more-info-list">
{chr(10).join(li_items)}
    </ul>
  </div>"""


def render_wildlife(species):
    items = species.get("wildlife_value") or []
    if not items:
        return ""
    paras = _render_paragraphs(items)
    return f"""  <div class="plant-section">
    <div class="plant-section-header"><span class="plant-section-icon">🦋</span><span class="plant-section-title">Wildlife Value</span></div>
    <div class="plant-section-body">{paras}</div>
  </div>"""


def render_reproduction(species):
    repro = species.get("reproduction")
    if not repro:
        return ""
    blocks = repro.get("blocks") or []
    wtlf = repro.get("what_to_look_for", "")
    parts = []
    for b in blocks:
        parts.append(f'<div class="repro-item"><div class="repro-label">{h(b["label"])}</div>{_p_html(b["text"])}</div>')
    if wtlf:
        parts.append(f'<div class="repro-item"><div class="repro-label">What to Look For</div>{_p_html(wtlf)}</div>')
    return f"""  <div class="plant-section">
    <div class="plant-section-header"><span class="plant-section-icon">🔬</span><span class="plant-section-title">Reproduction &amp; Identification</span></div>
    <div class="repro-list">
{chr(10).join(parts)}
    </div>
  </div>"""


def render_size_and_growing(species):
    size = species.get("size") or {}
    grow = species.get("growing_conditions") or {}
    if not size and not grow:
        return ""

    grid_items = []
    # Size fields in preferred order
    size_order = ["height", "height_length", "spread", "width", "trunk_diameter",
                  "rosette_height", "flowering_stalk", "crown_shape", "habit",
                  "growth_rate", "texture", "lifespan", "water_depth"]
    for key in size_order:
        val = size.get(key)
        if val:
            grid_items.append(_data_grid_item(_format_label(key), str(val), _should_be_full_width(val)))

    # Growing conditions in preferred order
    grow_order = ["light", "soil_tolerances", "drought_tolerance", "salt_tolerance",
                  "wind_tolerance", "wind_resistance", "cold_tolerance", "usda_zones", "note"]
    for key in grow_order:
        val = grow.get(key)
        if val:
            grid_items.append(_data_grid_item(_format_label(key), str(val), _should_be_full_width(val)))

    return f"""  <div class="plant-section">
    <div class="plant-section-header"><span class="plant-section-icon">📐</span><span class="plant-section-title">Size &amp; Growing Conditions</span></div>
    <div class="data-grid">
{chr(10).join(grid_items)}
    </div>
  </div>"""


def render_safety(species):
    """Render the edibility & toxicity section with appropriate severity styling."""
    ed = species.get("edibility") or {}
    tox = species.get("toxicity") or {}
    ed_level = ed.get("level", "Green")
    tox_level = tox.get("level", "Green")

    dog_level = tox.get("dogs_level", "Green")
    levels = (ed_level, tox_level, dog_level)
    worst = "Red" if "Red" in levels else "Yellow" if "Yellow" in levels else "Green"

    # Choose section class and icon
    if worst == "Red":
        section_cls = "plant-toxic-section"
        icon = "⚠️"
    elif worst == "Yellow":
        section_cls = "plant-caution-section"
        icon = "⚠️"
    else:
        section_cls = "plant-safe-section"
        icon = "✅"

    # Prefer the unified safety_note — ONE coherent, priority-led message that
    # leads with whatever matters most. Fall back to the legacy three fields
    # (edibility.detail + toxicity.people + toxicity.dogs) for pages drafted
    # before the merge, so nothing already published breaks.
    paras = []
    note_paras = _paragraphs(species.get("safety_note"))
    if note_paras:
        for p in note_paras:
            paras.append(f"<p>{h(p)}</p>")
    else:
        for src in (ed.get("detail"), tox.get("people"), tox.get("dogs")):
            for p in _paragraphs(src):
                paras.append(f"<p>{h(p)}</p>")

    if not paras:
        return ""

    return f"""  <div class="{section_cls}">
    <div class="plant-section-header"><span class="plant-section-icon">{icon}</span><span class="plant-section-title">Edibility &amp; Toxicity</span></div>
    <div class="plant-section-body">{"".join(paras)}</div>
  </div>"""


def render_notes(species):
    body = _render_paragraphs(species.get("other_notes"))
    if not body:
        return ""
    return f"""  <div class="plant-section">
    <div class="plant-section-header"><span class="plant-section-icon">📝</span><span class="plant-section-title">Notes</span></div>
    <div class="plant-section-body">{body}</div>
  </div>"""


def render_aliases(species):
    aliases = species.get("alternate_names") or []
    if not aliases:
        return ""
    tags = "".join(f'<span class="alias-tag">{h(a)}</span>' for a in aliases)
    return f"""  <div class="plant-section">
    <div class="plant-section-header"><span class="plant-section-icon">🏷️</span><span class="plant-section-title">Also Known As</span></div>
    <div class="alias-list">{tags}</div>
  </div>"""


def _fmt_observed(date_str):
    """Format an ISO date (2025-11-14) as 'Nov 14, 2025' for display.

    Returns "" if the date is missing or unparseable, so callers can skip it
    cleanly. Only the date is shown — this is the observation date that lets
    visitors see *when* each photo was taken (e.g. a tree in bloom).

    Kept identical to wildlife_publisher._fmt_observed so both corpora render
    dates the same way.
    """
    if not date_str:
        return ""
    try:
        from datetime import datetime as _dt
        return _dt.strptime(date_str[:10], "%Y-%m-%d").strftime("%b %-d, %Y")
    except (ValueError, TypeError):
        return ""


def render_gallery(species, gallery_photos, hero):
    """Render photo gallery section with lightbox.

    Hero (index 0): local path ../photos/PSBP-xxxxx/<filename>.jpg
    Gallery photos (index 1+): iNaturalist CDN URLs (not stored locally)
    If only one image (hero), lightbox still works but no prev/next buttons.
    """
    if not gallery_photos and not hero:
        return "", ""

    pid = species["id"]
    common = species["common_name"]

    # Build lightbox data: hero first, then gallery
    lb_data = []
    if hero:
        hc = resolve_hero_credit(hero)
        lb_data.append({
            "src": f"../photos/{pid}/{hero['filename']}",
            "credit": hc["credit_name"],
            "license": hc["credit_license"],
            "observed": _fmt_observed(hero.get("observed_on", "")),
        })

    grid_items = []
    for p in (gallery_photos or []):
        if p.get("hero"):
            continue
        url = p.get("photo_url", "")
        if not url:
            continue
        idx = len(lb_data)
        gal_login = p.get("photographer", "")
        photographer = display_name(gal_login, p.get("photographer_name", ""))
        observed = _fmt_observed(p.get("observed_on", ""))
        lb_data.append({
            "src": url,
            "credit": photographer,          # display string for the lightbox caption
            "credit_login": gal_login,       # stable key, for a future profile link
            "license": (p.get("license") or "").upper(),
            "observed": observed,
        })
        date_html = f'<div class="gal-date">📅 {h(observed)}</div>' if observed else ""
        _gal_attr = f' data-login="{h(gal_login)}"' if gal_login else ""
        grid_items.append(
            f'<div class="gal-item" onclick="openLB({idx})">'
            f'<img src="{h(url)}" loading="lazy" alt="{h(common)} — photo by {h(photographer)}">'
            f'<div class="gal-credit"{_gal_attr}>📷 {h(photographer)}</div>{date_html}</div>'
        )

    # Gallery section (only if there are non-hero photos)
    if grid_items:
        gallery_html = (
            f'  <div class="plant-section"><div class="plant-section-header">'
            f'<span class="plant-section-icon">📸</span>'
            f'<span class="plant-section-title">Photo Gallery</span></div>\n'
            f'    <div class="gal-note">Photos contributed by park visitors and volunteers via iNaturalist</div>\n'
            f'    <div class="gal-grid">{"".join(grid_items)}</div></div>'
        )
    else:
        gallery_html = ""

    if not lb_data:
        return "", ""

    # Prev/next buttons only if more than one photo
    multi = len(lb_data) > 1
    nav_buttons = (
        '        <button class="lb-prev" onclick="stepLB(-1)">&#8249;</button>\n'
        '        <button class="lb-next" onclick="stepLB(1)">&#8250;</button>\n'
    ) if multi else ""
    counter_html = '        <div class="lb-counter" id="lbCounter"></div>' if multi else ""
    counter_js = "document.getElementById('lbCounter').textContent=(i+1)+' / '+lbData.length;" if multi else ""
    step_js = "function stepLB(dir){lbIdx=(lbIdx+dir+lbData.length)%lbData.length;openLB(lbIdx);}" if multi else ""
    arrow_js = "if(e.key==='ArrowRight')stepLB(1);if(e.key==='ArrowLeft')stepLB(-1);" if multi else ""

    lb_json = json.dumps(lb_data, ensure_ascii=False)
    lightbox_html = f"""    <div class="lightbox" id="lb" onclick="closeLB(event)">
      <div class="lb-inner">
        <button class="lb-close" onclick="closeLB()">&times;</button>
{nav_buttons}        <img class="lb-img" id="lbImg">
        <div class="lb-credit" id="lbCredit"></div>
{counter_html}
      </div>
    </div>
    <script>
    var lbData={lb_json};
    var lbIdx=0;
    function openLB(i){{lbIdx=i;var d=lbData[i];document.getElementById('lbImg').src=d.src;var credit='📷 '+d.credit+' · '+d.license+' · via iNaturalist';if(d.observed)credit+=' · 📅 '+d.observed;document.getElementById('lbCredit').innerHTML=credit;{counter_js}document.getElementById('lb').classList.add('active');document.body.style.overflow='hidden';}}
    function closeLB(e){{if(e&&e.target!==document.getElementById('lb')&&!e.target.classList.contains('lb-close'))return;document.getElementById('lb').classList.remove('active');document.body.style.overflow='';}}
    {step_js}
    document.addEventListener('keydown',function(e){{if(!document.getElementById('lb').classList.contains('active'))return;if(e.key==='Escape')closeLB();{arrow_js}}});
    </script>"""

    return gallery_html, lightbox_html


def generate_html(species, hero, gallery_photos=None, published_on=""):
    """Render the page.

    published_on is the STORED publish date (see PUBLISH STATE in
    psbp_common) — never the current time. Embedding "now" would make every
    page differ from itself on every render, and the render-and-compare
    census in psbp_page_drift.py would report all 289 pages permanently
    stale."""
    """Generate the complete HTML page for a species."""
    pid = species["id"]
    common = species["common_name"]
    sci = species["botanical_name"]
    family = (species.get("taxonomy") or {}).get("family", "")
    # Hero label now shows the Form bucket (was: category). Fall back to the old
    # category string only for any record that predates the form field.
    hero_label = species.get("form", "") or species.get("category", "").replace(" and ", " & ")
    cat_html = h(hero_label)

    focus = (hero.get("focus") if hero else None) or "50% 50%"

    # Hero image path (relative from plants/ directory)
    if hero:
        hero_path = f"../photos/{pid}/{hero['filename']}"
    else:
        hero_path = f"../photos/{pid}-{slugify(common)}.jpg"

    # Credit line — resolved through photographer_names.json
    if hero:
        hc = resolve_hero_credit(hero)
        # data-login carries the stable key into the page without changing a
        # single visible character. site.js can turn the name into a profile
        # link once photographers.json says that person has a page — the
        # decision lives there, where the feed is already loaded, rather than
        # being baked into 321 static files that are expensive to change.
        _login_attr = f' data-login="{h(hc["credit_login"])}"' if hc.get("credit_login") else ""
        credit_parts = [f'📷 Photo by <strong{_login_attr}>{h(hc["credit_name"])}</strong>']
        if hc["credit_license"]:
            credit_parts.append(f' · {h(hc["credit_license"])}')
        credit_parts.append(' · via iNaturalist')
        # Observation date — same formatting as the gallery badges, so the
        # hero's date is visible without opening the lightbox.
        _hero_observed = _fmt_observed(hero.get("observed_on", ""))
        if _hero_observed:
            credit_parts.append(f' · 📅 {h(_hero_observed)}')
        credit_html = ''.join(credit_parts)
    else:
        credit_html = "📷 Photo credit pending"

    # Publish stamp — the visitor-facing freshness signal.
    from psbp_common import fmt_published as _fmt_pub
    _pub_disp = _fmt_pub(published_on)
    stamp_html = (f'<div class="page-stamp">Page updated <strong>{h(_pub_disp)}</strong></div>'
                  if _pub_disp else "")

    # Build all sections
    gallery_section, lightbox_section = render_gallery(species, gallery_photos, hero)
    sections = []
    sections.append(render_quick_hits(species))
    sections.append(render_origin(species))
    sections.append(render_more_info(species))
    sections.append(render_wildlife(species))
    sections.append(render_reproduction(species))
    sections.append(render_size_and_growing(species))
    sections.append(render_safety(species))
    sections.append(render_notes(species))
    if gallery_section:
        sections.append(gallery_section)
    sections.append(render_aliases(species))

    # Photo credits block — stamped at build time, no runtime lookups
    all_gallery = list(gallery_photos or [])
    if hero and hero not in all_gallery:
        all_gallery.insert(0, hero)
    gallery_creds = resolve_gallery_credits(all_gallery)
    if gallery_creds:
        cred_items = ''.join(
            f'<li style="font-size:15px;line-height:1.65;color:var(--text-mid,#2e2e1e);'
            f'padding:8px 0;border-bottom:1px solid rgba(90,122,74,0.12)">'
            f'{h(gc["credit_line"])}</li>'
            for gc in gallery_creds
        )
        sections.append(
            f'<div class="plant-section"><div class="plant-section-header">'
            f'<span class="plant-section-icon">📸</span>'
            f'<span class="plant-section-title">Photo Credits</span></div>'
            f'<ul style="list-style:none;padding:10px 16px">{cred_items}</ul></div>'
        )

    content = "\n".join(s for s in sections if s)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{h(common)} · Palma Sola Botanical Park</title>
<link rel="stylesheet" href="../css/site.css">
<link rel="stylesheet" href="../css/plant-page.css">
</head>
<body>
<div id="nav-placeholder"></div>

<div class="plant-wrap">
<div class="plant-hero">
  <img style="cursor:pointer;object-position:{h(focus)}" src="{hero_path}" alt="{h(common)} at Palma Sola Botanical Park" loading="lazy" onclick="openLB(0)">
  <div class="plant-hero-overlay">
    <div class="plant-hero-category">{cat_html}</div>
    <div class="plant-hero-name">{h(common)}</div>
  </div>
</div>
<div class="plant-sci-band">
  <span class="plant-sci-name">{h(sci)}</span>
  <a class="plant-family-tag" href="../nature.html?family={h(family)}">{h(family)}</a>
</div>
<div class="plant-credit">{credit_html}</div>
<div class="plant-content">
  <div class="plant-status-row">
    {render_badges(species)}
  </div>
{content}
{lightbox_section}
  <a class="all-plants-link" href="../nature.html#plants">🌿 Explore More Plants</a>
</div>
</div><!-- /.plant-wrap -->
<a class="plant-float-back" href="../nature.html#plants">🌿 All Plants</a>

{stamp_html}
<div id="footer-placeholder"></div>
<script src="../js/site.js"></script>
<script>
injectShared({{ inatBar: false }});
</script>
</body>
</html>"""


# ── File writers ────────────────────────────────────────────────────────────

# ── Publish stamp plumbing ──────────────────────────────────────────────────

def _publish_fingerprints(species, hero, gallery_photos):
    """(input_hash, generator, stored_date) for one species."""
    import sys as _sys
    from psbp_common import (compute_input_hash, generator_fingerprint,
                             get_publish_record)
    rec = get_publish_record(species["id"])
    return (compute_input_hash(species, hero, gallery_photos),
            generator_fingerprint(_sys.modules[__name__]),
            (rec or {}).get("last_published", ""))


def _record_publish(corpus, species_id, input_hash, generator, filename, stamp):
    """Persist the publish record. Never fatal — a page that wrote fine must
    not be reported as failed because a bookkeeping file was unwritable."""
    try:
        from psbp_common import record_publish
        record_publish(corpus, species_id, input_hash, generator, filename, stamp)
    except Exception as e:                                     # noqa: BLE001
        print(f"  ⚠ publish_state not updated for {species_id}: {e}")


def write_html(species, hero, gallery_photos=None, dry_run=False):
    """Write the page. Sole write path for plants pages — every caller
    (dashboard, CLI publish, --generate-all) routes through here, which is why
    the publish stamp is recorded here rather than at the call sites.

    last_published moves only when the RENDERED page changes. See
    psbp_common.page_content_changed for why the input hash isn't used for that.
    """
    from psbp_common import today_iso, page_content_changed
    input_hash, generator, prev = _publish_fingerprints(species, hero, gallery_photos)
    filename = page_filename(species["id"], species["common_name"])
    path = PLANTS_DIR / filename

    # Render first with the date already on file, so the comparison below is
    # about content and nothing else.
    html_content = generate_html(species, hero, gallery_photos, published_on=prev)
    if dry_run:
        return path, html_content

    if page_content_changed(path, html_content):
        stamp = today_iso()
        if stamp != prev:
            html_content = generate_html(species, hero, gallery_photos,
                                         published_on=stamp)
    else:
        stamp = prev or today_iso()
        if stamp != prev:
            html_content = generate_html(species, hero, gallery_photos,
                                         published_on=stamp)

    PLANTS_DIR.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(html_content, encoding="utf-8")
    tmp.rename(path)

    # A common_name edit moves where this writes. Reconcile what is already on
    # disk so the id keeps exactly one page, under the name the index points at.
    # See psbp_common.reconcile_page_siblings — the case step is load-bearing.
    from psbp_common import reconcile_page_siblings
    recased, dropped = reconcile_page_siblings(PLANTS_DIR, species["id"], filename)
    if recased:
        print(f"    ↻ {species['id']}: corrected filename case "
              f"{recased} -> {filename}")
    for gone in dropped:
        print(f"    ✕ {species['id']}: removed stale page {gone} "
              f"(renamed to {filename})")

    _record_publish("plants", species["id"], input_hash, generator, filename, stamp)
    return path, html_content

def update_plants_json(species, hero):
    """Add or update a species entry in plants.json. Preserves sort order by ID."""
    entries = load_plants_json()
    entry = build_plants_json_entry(species, hero)
    found = False
    for i, e in enumerate(entries):
        if e["id"] == entry["id"]:
            entries[i] = entry
            found = True
            break
    if not found:
        entries.append(entry)
    entries.sort(key=lambda e: e["id"])
    write_json_atomic(PLANTS_JSON, entries)
    return entry


def update_signage_status(species_id, new_status):
    """Thin wrapper — delegates to psbp_common with corpus='plants'."""
    from psbp_common import update_signage_status as _uss
    _uss("plants", species_id, new_status)


# ── Validation ──────────────────────────────────────────────────────────────

def validate_existing_html(species_lookup, heroes):
    """Check existing HTML files against JSON data. Returns list of issues."""
    issues = []
    if not PLANTS_DIR.exists():
        issues.append(("MISSING", "plants/ directory does not exist"))
        return issues

    for html_file in sorted(PLANTS_DIR.glob("PSBP-*.html")):
        pid = html_file.name.split("-", 2)[0] + "-" + html_file.name.split("-", 2)[1]
        # Extract PSBP-NNNNN from filename
        m = re.match(r"(PSBP-\d{5})", html_file.name)
        if not m:
            issues.append(("PARSE", f"Cannot extract ID from {html_file.name}"))
            continue
        pid = m.group(1)

        content = html_file.read_text(encoding="utf-8")

        # Check hero image path
        hero = heroes.get(pid)
        if hero:
            expected_path = f"../photos/{pid}/{hero['filename']}"
            if expected_path not in content:
                # Find what path IS used
                img_match = re.search(r'plant-hero-link.*?href="([^"]+)"', content, re.DOTALL)
                actual = img_match.group(1) if img_match else "NOT FOUND"
                issues.append(("HERO_PATH", f"{pid}: expected {expected_path}, found {actual}"))

        # Check species exists in signage
        if pid not in species_lookup:
            issues.append(("NO_SIGNAGE", f"{pid}: HTML exists but no entry in plant_signage.json"))

    # Check for signage entries with status=html but no HTML file
    for sid, spec in species_lookup.items():
        if spec["status"] == "html":
            expected_file = PLANTS_DIR / page_filename(sid, spec["common_name"])
            if not expected_file.exists():
                issues.append(("NO_HTML", f"{sid} {spec['common_name']}: status=html but no HTML file"))

    return issues


# ── Dashboard HTML ──────────────────────────────────────────────────────────

DASHBOARD_HTML = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>PSBP Plant Publisher</title>
<style>
  * { margin:0; padding:0; box-sizing:border-box; }
  body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; background:#1a1a14; color:#e8e3d8; }

  .layout { display:grid; grid-template-columns:320px 1fr; height:100vh; }

  /* Sidebar */
  .sidebar { background:#222218; border-right:1px solid #3a3a2e; overflow-y:auto; display:flex; flex-direction:column; }
  .sidebar-header { padding:16px; background:#2d4a2d; border-bottom:2px solid #b8942a; position:sticky; top:0; z-index:10; }
  .sidebar-header h1 { font-size:15px; font-weight:700; letter-spacing:2px; text-transform:uppercase; color:#d4aa40; }
  .sidebar-header .counts { font-size:12px; color:#a0a090; margin-top:4px; }

  .filter-bar { padding:8px 12px; display:flex; gap:6px; flex-wrap:wrap; position:sticky; top:60px; background:#222218; z-index:9; border-bottom:1px solid #3a3a2e; }
  .filter-btn { font-size:11px; font-weight:700; padding:4px 10px; border-radius:12px; border:1.5px solid; cursor:pointer; background:transparent; transition:all .2s; }
  .filter-btn[data-status="html"] { color:#4a9e56; border-color:#4a9e56; }
  .filter-btn[data-status="html"].active { background:#4a9e56; color:#fff; }
  .filter-btn[data-status="spotted"] { color:#d4aa40; border-color:#d4aa40; }
  .filter-btn[data-status="spotted"].active { background:#d4aa40; color:#1a1a14; }
  .filter-btn[data-status="research"] { color:#888; border-color:#666; }
  .filter-btn[data-status="research"].active { background:#666; color:#fff; }

  .search-box { width:100%; padding:8px 12px; background:#1a1a14; border:1px solid #3a3a2e; border-radius:6px; color:#e8e3d8; font-size:13px; margin:8px 12px; width:calc(100% - 24px); }
  .search-box:focus { outline:none; border-color:#d4aa40; }

  .species-list { flex:1; overflow-y:auto; }
  .species-item { padding:10px 14px; border-bottom:1px solid #2a2a22; cursor:pointer; transition:background .15s; display:flex; align-items:center; gap:10px; }
  .species-item:hover { background:#2a2a22; }
  .species-item.selected { background:#2d4a2d; border-left:3px solid #d4aa40; }
  .species-item .dot { width:8px; height:8px; border-radius:50%; flex-shrink:0; }
  .species-item .dot.html { background:#4a9e56; }
  .species-item .dot.spotted { background:#d4aa40; }
  .species-item .dot.research { background:#666; }
  .species-item .info { flex:1; min-width:0; }
  .species-item .name { font-size:14px; font-weight:600; color:#e8e3d8; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
  .species-item .sci { font-size:12px; color:#a0a090; font-style:italic; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
  .species-item .id-tag { font-size:10px; color:#666; font-family:monospace; }

  /* Main panel */
  .main { overflow-y:auto; background:#1a1a14; }
  .main-empty { display:flex; align-items:center; justify-content:center; height:100%; color:#666; font-size:18px; }

  /* Detail view */
  .detail { padding:24px; max-width:900px; }
  .detail-header { display:flex; align-items:flex-start; gap:20px; margin-bottom:24px; }
  .detail-hero { width:280px; height:200px; border-radius:10px; overflow:hidden; flex-shrink:0; background:#2a2a22; }
  .detail-hero img { width:100%; height:100%; object-fit:cover; }
  .detail-hero .no-hero { display:flex; align-items:center; justify-content:center; height:100%; color:#666; font-size:14px; }
  .detail-meta { flex:1; }
  .detail-meta h2 { font-family: Georgia, serif; font-size:28px; color:#e8e3d8; margin-bottom:4px; }
  .detail-meta .sci { font-size:18px; color:#a0a090; font-style:italic; margin-bottom:8px; }
  .detail-meta .meta-row { font-size:13px; color:#a0a090; margin-bottom:3px; }
  .detail-meta .meta-row strong { color:#d4aa40; }

  .action-bar { display:flex; gap:10px; margin:16px 0 24px; padding:16px; background:#222218; border-radius:10px; border:1px solid #3a3a2e; align-items:center; }
  .action-bar .status-badge { font-size:12px; font-weight:700; padding:4px 12px; border-radius:12px; }
  .action-bar .status-badge.html { background:#4a9e56; color:#fff; }
  .action-bar .status-badge.spotted { background:#d4aa40; color:#1a1a14; }
  .action-bar .status-badge.research { background:#666; color:#fff; }
  .btn { padding:8px 18px; border-radius:8px; border:none; font-size:13px; font-weight:700; cursor:pointer; transition:all .2s; }
  .btn-publish { background:#2d6a35; color:#fff; }
  .btn-publish:hover { background:#4a9e56; }
  .btn-publish:disabled { background:#333; color:#666; cursor:not-allowed; }
  .btn-preview { background:#3a3a2e; color:#e8e3d8; }
  .btn-preview:hover { background:#4a4a3e; }
  .btn-demote { background:#6a3520; color:#fff; }
  .btn-demote:hover { background:#8a4530; }
  .btn-regen { background:#7a5000; color:#fff; }
  .btn-regen:hover { background:#b8942a; }
  .action-msg { font-size:12px; color:#4a9e56; margin-left:auto; }

  /* Data sections */
  .data-section { margin-bottom:16px; background:#222218; border-radius:10px; overflow:hidden; border:1px solid #3a3a2e; }
  .data-section-header { padding:10px 16px; background:#2d4a2d; font-size:11px; font-weight:700; letter-spacing:2px; text-transform:uppercase; color:#d4aa40; cursor:pointer; user-select:none; display:flex; justify-content:space-between; }
  .data-section-header .toggle { color:#a0a090; }
  .data-section-body { padding:14px 16px; }
  .data-section-body.collapsed { display:none; }
  .data-row { display:flex; gap:8px; margin-bottom:6px; font-size:14px; line-height:1.5; }
  .data-row .label { color:#a0a090; min-width:140px; flex-shrink:0; font-size:12px; text-transform:uppercase; letter-spacing:0.5px; padding-top:2px; }
  .data-row .value { color:#e8e3d8; }
  .data-row .value.list-value { display:flex; flex-wrap:wrap; gap:4px; }
  .data-row .value .tag { background:#3a3a2e; padding:2px 8px; border-radius:4px; font-size:13px; }
  .text-block { font-size:14px; line-height:1.65; color:#c8c3b8; margin-bottom:8px; }
  .text-block:last-child { margin-bottom:0; }

  /* Preview iframe */
  .preview-frame { width:100%; height:80vh; border:none; border-radius:10px; background:#e8e3d8; margin-top:16px; }

  /* Toast */
  .toast { position:fixed; bottom:24px; right:24px; background:#2d6a35; color:#fff; padding:12px 20px; border-radius:8px; font-size:14px; font-weight:600; box-shadow:0 4px 16px rgba(0,0,0,0.4); transform:translateY(80px); opacity:0; transition:all .3s; z-index:100; }
  .toast.show { transform:translateY(0); opacity:1; }
</style>
</head>
<body>
<div class="layout">
  <div class="sidebar">
    <div class="sidebar-header">
      <h1>🌿 Plant Publisher</h1>
      <div class="counts" id="counts"></div>
    </div>
    <div class="filter-bar" id="filters"></div>
    <input class="search-box" id="search" placeholder="Search by name, ID, or family…" autocomplete="off">
    <div class="species-list" id="species-list"></div>
  </div>
  <div class="main" id="main">
    <div class="main-empty">Select a species to review</div>
  </div>
</div>
<div class="toast" id="toast"></div>

<script>
let DATA = null;
let selectedId = null;
let activeFilters = new Set(['html', 'spotted', 'research']);

async function init() {
  const resp = await fetch('/api/data');
  DATA = await resp.json();
  renderCounts();
  renderFilters();
  renderList();
}

function renderCounts() {
  const counts = {html:0, spotted:0, research:0};
  DATA.species.forEach(s => counts[s.status] = (counts[s.status]||0)+1);
  document.getElementById('counts').textContent =
    `${counts.html} html · ${counts.spotted} spotted · ${counts.research} research · ${DATA.species.length} total`;
}

function renderFilters() {
  const bar = document.getElementById('filters');
  ['html','spotted','research'].forEach(status => {
    const btn = document.createElement('button');
    btn.className = 'filter-btn active';
    btn.dataset.status = status;
    btn.textContent = status;
    btn.onclick = () => {
      if (activeFilters.has(status)) activeFilters.delete(status);
      else activeFilters.add(status);
      btn.classList.toggle('active');
      renderList();
    };
    bar.appendChild(btn);
  });
}

function renderList() {
  const query = (document.getElementById('search').value || '').toLowerCase();
  const list = document.getElementById('species-list');
  list.innerHTML = '';
  const filtered = DATA.species.filter(s => {
    if (!activeFilters.has(s.status)) return false;
    if (query) {
      const hay = (s.common_name + ' ' + s.botanical_name + ' ' + s.id + ' ' + (s.taxonomy?.family||'')).toLowerCase();
      if (!hay.includes(query)) return false;
    }
    return true;
  });
  filtered.forEach(s => {
    const div = document.createElement('div');
    div.className = 'species-item' + (s.id === selectedId ? ' selected' : '');
    div.innerHTML = `
      <div class="dot ${s.status}"></div>
      <div class="info">
        <div class="name">${esc(s.common_name)}</div>
        <div class="sci">${esc(s.botanical_name)}</div>
      </div>
      <div class="id-tag">${s.id}</div>`;
    div.onclick = () => selectSpecies(s.id);
    list.appendChild(div);
  });
}

function selectSpecies(id) {
  selectedId = id;
  renderList();
  renderDetail(id);
}

function renderDetail(id) {
  const species = DATA.species.find(s => s.id === id);
  const hero = DATA.heroes[id] || null;
  const hasHero = !!hero;
  const main = document.getElementById('main');

  const heroUrl = hasHero ? hero.photo_url : '';
  const heroHtml = hasHero
    ? `<img src="${esc(heroUrl)}" alt="${esc(species.common_name)}" style="object-position:${esc(hero.focus || '50% 50%')}">`
    : '<div class="no-hero">No hero photo</div>';

  const family = species.taxonomy?.family || '';
  const cat = species.category || '';

  // plants.json entry preview
  const pjEntry = DATA.plants_json_lookup[id];
  const inPlantsJson = !!pjEntry;

  main.innerHTML = `
    <div class="detail">
      <div class="detail-header">
        <div class="detail-hero">${heroHtml}</div>
        <div class="detail-meta">
          <h2>${esc(species.common_name)}</h2>
          <div class="sci">${esc(species.botanical_name)}</div>
          <div class="meta-row"><strong>ID:</strong> ${species.id}</div>
          <div class="meta-row"><strong>Family:</strong> ${esc(family)}</div>
          <div class="meta-row"><strong>Category:</strong> ${esc(cat)}</div>
          <div class="meta-row"><strong>Feature tier:</strong> ${species.feature_tier || '—'}</div>
          <div class="meta-row"><strong>Has sign:</strong> ${species.has_sign ? 'Yes' : 'No'}</div>
          <div class="meta-row"><strong>In plants.json:</strong> ${inPlantsJson ? 'Yes' : 'No'}</div>
          ${hasHero ? `<div class="meta-row"><strong>Hero:</strong> ${esc(hero.photographer_name)} · ${esc(hero.filename)}</div>` : '<div class="meta-row" style="color:#c49a20"><strong>⚠ No hero photo in photo_credits.json</strong></div>'}
        </div>
      </div>

      <div class="action-bar">
        <span class="status-badge ${species.status}">${species.status.toUpperCase()}</span>
        <button class="btn btn-publish" onclick="doPublish('${id}')" ${!hasHero ? 'disabled title="Needs hero photo"' : ''}>
          ${species.status === 'html' ? '♻️ Regenerate & Publish' : '🚀 Publish to HTML'}
        </button>
        <button class="btn btn-preview" onclick="doPreview('${id}')">👁 Preview HTML</button>
        ${species.status === 'html' ? `<button class="btn btn-demote" onclick="doDemote('${id}')">⬇ Demote to Spotted</button>` : ''}
        <span class="action-msg" id="action-msg"></span>
      </div>

      ${renderDataSections(species, hero)}
    </div>`;
}

function renderDataSections(s, hero) {
  let html = '';

  // Quick Hits
  if (s.quick_hits?.length) {
    html += dataSection('Quick Hits', s.quick_hits.map((q,i) => `<div class="text-block">${i+1}. ${esc(q)}</div>`).join(''));
  }

  // Origin
  if (s.origin) {
    html += dataSection('Origin', `<div class="text-block">${Array.isArray(s.origin)?s.origin.map(esc).join('<br>'):esc(s.origin)}</div>`);
  }

  // More Information
  if (s.more_information?.length) {
    html += dataSection('More Information', s.more_information.map(p => `<div class="text-block">${esc(p)}</div>`).join(''));
  }

  // Wildlife Value
  if (s.wildlife_value?.length) {
    html += dataSection('Wildlife Value', s.wildlife_value.map(p => `<div class="text-block">${esc(p)}</div>`).join(''));
  }

  // Reproduction
  if (s.reproduction) {
    let rhtml = '';
    (s.reproduction.blocks || []).forEach(b => {
      rhtml += `<div class="data-row"><div class="label">${esc(b.label)}</div><div class="value">${esc(b.text)}</div></div>`;
    });
    if (s.reproduction.what_to_look_for) {
      rhtml += `<div class="data-row"><div class="label">What to look for</div><div class="value">${esc(s.reproduction.what_to_look_for)}</div></div>`;
    }
    html += dataSection('Reproduction', rhtml);
  }

  // Size
  if (s.size) {
    let shtml = '';
    Object.entries(s.size).forEach(([k,v]) => {
      if (v) shtml += `<div class="data-row"><div class="label">${esc(k.replace(/_/g,' '))}</div><div class="value">${esc(String(v))}</div></div>`;
    });
    html += dataSection('Size', shtml);
  }

  // Growing Conditions
  if (s.growing_conditions) {
    let ghtml = '';
    Object.entries(s.growing_conditions).forEach(([k,v]) => {
      if (v) ghtml += `<div class="data-row"><div class="label">${esc(k.replace(/_/g,' '))}</div><div class="value">${esc(String(v))}</div></div>`;
    });
    html += dataSection('Growing Conditions', ghtml);
  }

  // Safety — prefer the unified safety_note (what actually publishes); fall back
  // to the legacy edibility/toxicity prose for records drafted before the merge,
  // mirroring render_safety() so this audit view matches the published page.
  let safetyHtml = '';
  if (Array.isArray(s.safety_note) && s.safety_note.length) {
    safetyHtml += `<div class="data-row"><div class="label">Safety note</div><div class="value">${s.safety_note.map(esc).join('<br>')}</div></div>`;
  } else {
    if (s.edibility) {
      safetyHtml += `<div class="data-row"><div class="label">Edibility (${s.edibility.level})</div><div class="value">${Array.isArray(s.edibility.detail)?s.edibility.detail.map(esc).join('<br>'):esc(s.edibility.detail||'')}</div></div>`;
    }
    if (s.toxicity) {
      safetyHtml += `<div class="data-row"><div class="label">Toxicity (${s.toxicity.level})</div><div class="value">${Array.isArray(s.toxicity.people)?s.toxicity.people.map(esc).join('<br>'):esc(s.toxicity.people||'')}</div></div>`;
      if (s.toxicity.dogs) {
        safetyHtml += `<div class="data-row"><div class="label">Dogs (${s.toxicity.dogs_level})</div><div class="value">${Array.isArray(s.toxicity.dogs)?s.toxicity.dogs.map(esc).join('<br>'):esc(s.toxicity.dogs)}</div></div>`;
      }
    }
  }
  if (safetyHtml) html += dataSection('Edibility & Toxicity', safetyHtml);

  // Invasive
  if (s.invasive) {
    html += dataSection('Invasive Status', `<div class="data-row"><div class="label">Level: ${s.invasive.level}</div><div class="value">${esc(s.invasive.notes || '')}</div></div>`);
  }

  // Aliases
  if (s.alternate_names?.length) {
    const tags = s.alternate_names.map(a => `<span class="tag">${esc(a)}</span>`).join('');
    html += dataSection('Alternate Names', `<div class="data-row"><div class="value list-value">${tags}</div></div>`);
  }

  // Notes
  if (s.other_notes) {
    html += dataSection('Notes', `<div class="text-block">${esc(s.other_notes)}</div>`);
  }

  return html;
}

function dataSection(title, body, collapsed) {
  return `<div class="data-section">
    <div class="data-section-header" onclick="this.nextElementSibling.classList.toggle('collapsed')">
      ${esc(title)} <span class="toggle">▾</span>
    </div>
    <div class="data-section-body${collapsed ? ' collapsed' : ''}">${body}</div>
  </div>`;
}

async function doPublish(id) {
  const msg = document.getElementById('action-msg');
  msg.textContent = 'Publishing…';
  msg.style.color = '#d4aa40';
  try {
    const resp = await fetch('/api/publish', {
      method: 'POST',
      headers: {'Content-Type':'application/json'},
      body: JSON.stringify({id})
    });
    const result = await resp.json();
    if (result.ok) {
      msg.textContent = '✓ Published';
      msg.style.color = '#4a9e56';
      showToast(`Published ${result.filename} — plants.json updated`);
      // Refresh data
      const dresp = await fetch('/api/data');
      DATA = await dresp.json();
      renderCounts();
      renderList();
      renderDetail(id);
    } else {
      msg.textContent = '✗ ' + result.error;
      msg.style.color = '#c44';
    }
  } catch(e) {
    msg.textContent = '✗ Network error';
    msg.style.color = '#c44';
  }
}

async function doPreview(id) {
  window.open('/api/preview?id=' + id, '_blank');
}

async function doDemote(id) {
  if (!confirm('Demote this species to spotted? It will be removed from plants.json.')) return;
  const msg = document.getElementById('action-msg');
  msg.textContent = 'Demoting…';
  msg.style.color = '#d4aa40';
  try {
    const resp = await fetch('/api/demote', {
      method: 'POST',
      headers: {'Content-Type':'application/json'},
      body: JSON.stringify({id})
    });
    const result = await resp.json();
    if (result.ok) {
      msg.textContent = '✓ Demoted to spotted';
      msg.style.color = '#d4aa40';
      showToast(result.message);
      const dresp = await fetch('/api/data');
      DATA = await dresp.json();
      renderCounts();
      renderList();
      renderDetail(id);
    } else {
      msg.textContent = '✗ ' + result.error;
      msg.style.color = '#c44';
    }
  } catch(e) {
    msg.textContent = '✗ Network error';
    msg.style.color = '#c44';
  }
}

function showToast(text) {
  const t = document.getElementById('toast');
  t.textContent = text;
  t.classList.add('show');
  setTimeout(() => t.classList.remove('show'), 3000);
}

function esc(s) {
  if (!s) return '';
  const d = document.createElement('div');
  d.textContent = s;
  return d.innerHTML;
}

document.getElementById('search').addEventListener('input', renderList);
init();
</script>
</body>
</html>
"""


# ── HTTP Server ─────────────────────────────────────────────────────────────

class DashboardHandler(http.server.BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        # Suppress default logging noise
        pass

    def _json_response(self, data, status=200):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", len(body))
        self.end_headers()
        self.wfile.write(body)

    def _html_response(self, html_str, status=200):
        body = html_str.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", len(body))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        parsed = urlparse(self.path)

        if parsed.path == "/" or parsed.path == "":
            self._html_response(DASHBOARD_HTML)

        elif parsed.path == "/api/data":
            signage = load_signage()
            credits = load_credits()
            heroes = build_hero_lookup(credits)
            plants_json = load_plants_json()
            pj_lookup = {p["id"]: p for p in plants_json}

            # Serialize heroes for JSON (just the fields the dashboard needs)
            heroes_out = {}
            for pid, hr in heroes.items():
                hc = resolve_hero_credit(hr)
                heroes_out[pid] = {
                    "filename": hr["filename"],
                    "photo_url": hr.get("photo_url", ""),
                    "photographer_name": hc["credit_name"],
                    "photographer": hr.get("photographer", ""),
                    "license": hc["credit_license"],
                    "credit_line": hc["credit_line"],
                    "focus": hr.get("focus") or "50% 50%",
                }

            self._json_response({
                "species": signage["species"],
                "heroes": heroes_out,
                "plants_json_lookup": pj_lookup,
                "meta": signage["meta"],
            })

        elif parsed.path == "/api/preview":
            qs = parse_qs(parsed.query)
            pid = qs.get("id", [None])[0]
            if not pid:
                self._html_response("<h1>Missing id parameter</h1>", 400)
                return
            signage = load_signage()
            credits = load_credits()
            heroes = build_hero_lookup(credits)
            galleries = build_gallery_lookup(credits)
            species_lookup = build_species_lookup(signage)
            species = species_lookup.get(pid)
            if not species:
                self._html_response(f"<h1>Species {pid} not found</h1>", 404)
                return
            hero = heroes.get(pid)
            # Generate preview with absolute image URLs (iNat) for browser viewing
            preview_html = generate_html(species, hero, galleries.get(pid, []))
            # Replace relative photo paths with absolute iNat URLs for preview
            if hero and hero.get("photo_url"):
                rel_path = f"../photos/{pid}/{hero['filename']}"
                preview_html = preview_html.replace(rel_path, hero["photo_url"])
            # Remove site.js dependency for preview
            preview_html = preview_html.replace('<link rel="stylesheet" href="../css/site.css">', '')
            preview_html = preview_html.replace('<div id="nav-placeholder"></div>', '')
            preview_html = preview_html.replace('<div id="footer-placeholder"></div>', '')
            preview_html = preview_html.replace('<script src="../js/site.js"></script>', '')
            preview_html = preview_html.replace('injectShared({ inatBar: false });', '')
            self._html_response(preview_html)

        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        if self.path == "/api/publish":
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length))
            pid = body.get("id")
            if not pid:
                self._json_response({"ok": False, "error": "Missing id"}, 400)
                return

            try:
                signage = load_signage()
                credits = load_credits()
                heroes = build_hero_lookup(credits)
                galleries = build_gallery_lookup(credits)
                species_lookup = build_species_lookup(signage)

                species = species_lookup.get(pid)
                if not species:
                    self._json_response({"ok": False, "error": f"Species {pid} not found"}, 404)
                    return

                hero = heroes.get(pid)
                if not hero:
                    self._json_response({"ok": False, "error": f"No hero photo for {pid}"}, 400)
                    return

                # Generate HTML
                path, _ = write_html(species, hero, galleries.get(pid, []))

                # Update plants.json
                entry = update_plants_json(species, hero)

                # Update status to html if not already
                if species["status"] != "html":
                    update_signage_status(pid, "html")

                self._json_response({
                    "ok": True,
                    "filename": path.name,
                    "plants_json_entry": entry,
                })
                print(f"  ✓ Published {pid} → {path.name}")

            except Exception as e:
                self._json_response({"ok": False, "error": str(e)}, 500)

        elif self.path == "/api/demote":
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length))
            pid = body.get("id")
            if not pid:
                self._json_response({"ok": False, "error": "Missing id"}, 400)
                return
            try:
                signage = load_signage()
                species = build_species_lookup(signage).get(pid)
                if not species:
                    self._json_response({"ok": False, "error": f"{pid} not found"}, 404)
                    return
                if species["status"] != "html":
                    self._json_response({"ok": False, "error": f"{pid} is already {species['status']}"}, 400)
                    return

                update_signage_status(pid, "spotted")

                entries = load_plants_json()
                entries = [e for e in entries if e["id"] != pid]
                entries.sort(key=lambda e: e["id"])
                write_json_atomic(PLANTS_JSON, entries)

                # Clean up orphan HTML file(s)
                deleted = delete_species_page("plants", pid)
                for fname in deleted:
                    print(f"  🗑 Deleted {fname}")

                self._json_response({
                    "ok": True,
                    "message": f"{species['common_name']} demoted to spotted",
                })
                print(f"  ⬇ Demoted {pid} {species['common_name']} → spotted")
            except Exception as e:
                self._json_response({"ok": False, "error": str(e)}, 500)

        else:
            self.send_response(404)
            self.end_headers()


# ── CLI entry points ────────────────────────────────────────────────────────

def cmd_dashboard():
    print(f"\n  🌿 PSBP Plant Publisher")
    print(f"  Dashboard: http://localhost:{PORT}")
    print(f"  Press Ctrl+C to stop\n")
    server = http.server.HTTPServer(("127.0.0.1", PORT), DashboardHandler)
    webbrowser.open(f"http://localhost:{PORT}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n  Stopped.")
        server.server_close()


def validate_data_invariants(signage):
    """Enforce the data standard, fail-closed:
      1. No string value anywhere contains a newline (paragraph breaks live as
         separate list items, never inside a string).
      2. The list-typed prose fields really are lists.
    Returns a list of (tag, message) issues."""
    LIST_PROSE = ("quick_hits", "more_information", "wildlife_value",
                  "origin", "other_notes", "internal_notes")
    issues = []

    def scan(value, path, sid):
        if isinstance(value, str):
            if "\n" in value:
                issues.append(("NEWLINE", f"{sid}: '{path}' has a newline — split into separate list items"))
            if len(value) > MAX_PARA_CHARS:
                issues.append(("LONG", f"{sid}: '{path}' is {len(value)} chars (> {MAX_PARA_CHARS}) — render will split it; consider breaking it at the source"))
        elif isinstance(value, dict):
            for k, v in value.items():
                scan(v, f"{path}.{k}" if path else k, sid)
        elif isinstance(value, list):
            for i, v in enumerate(value):
                scan(v, f"{path}[{i}]", sid)

    for sp in signage.get("species", []):
        sid = sp.get("id")
        scan(sp, "", sid)
        for f in LIST_PROSE:
            v = sp.get(f)
            if v is not None and not isinstance(v, list):
                issues.append(("SHAPE", f"{sid}: '{f}' should be a list of paragraphs, found {type(v).__name__}"))
    return issues


def cmd_validate():
    signage = load_signage()
    credits = load_credits()
    heroes = build_hero_lookup(credits)
    species_lookup = build_species_lookup(signage)

    issues = validate_existing_html(species_lookup, heroes)
    if not issues:
        print("✓ All HTML files validated — hero paths and signage entries match.")
    else:
        print(f"Found {len(issues)} issue(s):\n")
        for tag, msg in issues:
            print(f"  [{tag}] {msg}")

    # Also validate plants.json
    plants = load_plants_json()
    pj_issues = 0
    for p in plants:
        hero = heroes.get(p["id"])
        if hero:
            expected = f"photos/{p['id']}/{hero['filename']}"
            if p.get("photo") != expected:
                print(f"  [PLANTS_JSON] {p['id']} {p['common']}: photo={p.get('photo')} expected={expected}")
                pj_issues += 1
    if pj_issues == 0:
        print("✓ plants.json hero paths all match photo_credits.")

    # Data standard: no newlines in strings; prose fields are lists (fail-closed).
    inv_issues = validate_data_invariants(signage)
    if inv_issues:
        print(f"\n  Data standard: {len(inv_issues)} issue(s):")
        for tag, msg in inv_issues:
            print(f"    [{tag}] {msg}")
    else:
        print("✓ Data standard: no newlines in strings; prose fields are lists.")

    print(f"\n  Summary: {len(issues)} HTML issues, {pj_issues} plants.json issues, {len(inv_issues)} data-standard issues")


def cmd_generate_all():
    signage = load_signage()
    credits = load_credits()
    heroes = build_hero_lookup(credits)
    galleries = build_gallery_lookup(credits)

    # Rebuild plants.json from scratch — only status=html species
    fresh_entries = []
    count = 0
    skipped = 0
    for species in signage["species"]:
        if species["status"] != "html":
            continue
        hero = heroes.get(species["id"])
        if not hero:
            print(f"  ⚠ {species['id']} {species['common_name']}: no hero photo, skipping HTML + plants.json")
            skipped += 1
            continue
        path, _ = write_html(species, hero, galleries.get(species["id"], []))
        fresh_entries.append(build_plants_json_entry(species, hero))
        count += 1

    # Atomic write of the complete, clean plants.json
    fresh_entries.sort(key=lambda e: e["id"])
    write_json_atomic(PLANTS_JSON, fresh_entries)

    print(f"\n  ✓ Generated {count} HTML files, skipped {skipped}")
    print(f"  ✓ plants.json rebuilt with {count} entries (html-only)")


def cmd_clean():
    """Remove non-html entries from plants.json using plant_signage status as source of truth."""
    signage = load_signage()
    species_lookup = build_species_lookup(signage)
    entries = load_plants_json()
    before = len(entries)

    kept = []
    removed = []
    for e in entries:
        spec = species_lookup.get(e["id"])
        if spec and spec["status"] == "html":
            kept.append(e)
        else:
            status = spec["status"] if spec else "NOT IN SIGNAGE"
            removed.append((e["id"], e["common"], status))

    if not removed:
        print("  ✓ plants.json is already clean — all entries are status=html")
        return

    print(f"  Removing {len(removed)} non-html entries from plants.json:\n")
    for pid, name, status in removed:
        print(f"    {pid} {name} (status={status})")

    kept.sort(key=lambda e: e["id"])
    write_json_atomic(PLANTS_JSON, kept)

    print(f"\n  ✓ plants.json: {before} → {len(kept)} entries")


def cmd_generate_one(pid):
    signage = load_signage()
    credits = load_credits()
    heroes = build_hero_lookup(credits)
    galleries = build_gallery_lookup(credits)
    species_lookup = build_species_lookup(signage)

    species = species_lookup.get(pid)
    if not species:
        print(f"  ✗ Species {pid} not found in plant_signage.json")
        sys.exit(1)

    hero = heroes.get(pid)
    if not hero:
        print(f"  ⚠ No hero photo for {pid} — generating with placeholder path")

    path, _ = write_html(species, hero, galleries.get(pid, []))
    entry = update_plants_json(species, hero)
    print(f"  ✓ {path}")
    print(f"  ✓ plants.json updated for {pid}")


def cmd_demote(pid):
    """Demote a species from html → spotted. Removes from plants.json and deletes HTML file."""
    signage = load_signage()
    species_lookup = build_species_lookup(signage)

    species = species_lookup.get(pid)
    if not species:
        print(f"  ✗ {pid} not found in plant_signage.json")
        sys.exit(1)

    if species["status"] != "html":
        print(f"  ✗ {pid} {species['common_name']} is already status={species['status']}")
        sys.exit(1)

    # Demote status in signage
    update_signage_status(pid, "spotted")

    # Remove from plants.json
    entries = load_plants_json()
    before = len(entries)
    entries = [e for e in entries if e["id"] != pid]
    if len(entries) < before:
        entries.sort(key=lambda e: e["id"])
        write_json_atomic(PLANTS_JSON, entries)
        print(f"  ✓ Removed from plants.json ({before} → {len(entries)} entries)")
    else:
        print(f"  ⚠ {pid} was not in plants.json")

    # Clean up orphan HTML file(s)
    deleted = delete_species_page("plants", pid)
    for fname in deleted:
        print(f"  🗑 Deleted {fname}")

    print(f"  ✓ {pid} {species['common_name']} demoted to spotted")


def main():
    if len(sys.argv) < 2:
        cmd_dashboard()
    elif sys.argv[1] == "--validate":
        cmd_validate()
    elif sys.argv[1] == "--generate-all":
        cmd_generate_all()
    elif sys.argv[1] == "--clean":
        cmd_clean()
    elif sys.argv[1] == "--generate" and len(sys.argv) >= 3:
        cmd_generate_one(sys.argv[2])
    elif sys.argv[1] == "--demote" and len(sys.argv) >= 3:
        cmd_demote(sys.argv[2])
    else:
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    main()
