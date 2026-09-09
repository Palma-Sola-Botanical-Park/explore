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


def card_hits(species):
    """The bullets the INDEX shows: authored `page.at_a_glance` if it exists,
    otherwise the original `quick_hits`.

    Same override the page generator uses, one level up. Randy, 2026-09-08:
    "we keep it and shift to pages.ataglance when it is available. quick hit
    would ONLY go to index if at a glance isn't populated of course."

    Without this the card, the browse drawer and screen.html keep showing the
    older draft while the page shows the authored one — 34 of 92 wildlife
    species had already diverged that way before this was added.
    """
    pol = (species.get("page") or {}).get("at_a_glance")
    if pol:
        out = []
        for b in pol:
            t = b.get("text") if isinstance(b, dict) else b
            if t:
                out.append(str(t))
        if out:
            return out
    return species.get("quick_hits") or []


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
        # The real array alongside the flattened `quick` search string — see the
        # note in wildlife_publisher.build_wildlife_json_entry. Retires
        # screen.html's fetch of plant_signage.json. Added 2026-09-07.
        "quick_hits":   card_hits(species),
        "native": native,
        "butterfly": butterfly,             # larval OR nectar (rollup + back-compat)
        "larval_host": larval_host,         # filter: larval host plant
        "nectar": nectar,                   # filter: adult nectar source
        "larval_species": larval_species,   # named hosts, for the butterfly page
        # `watch_invasive` is NOT emitted. Randy removed the "Watch — invasive" chip
        # from the browse index 2026-09-01 because the flag is tripped by either a
        # formal listing or observed behaviour and a reader cannot tell which —
        # "invasive status is prose". Nothing has consumed it from this feed since,
        # so it stopped being emitted 2026-09-08. It REMAINS on the source record in
        # plant_signage.json: species_manager edits it, and the page mapper uses it
        # to locate the prose in watch_invasive_notes (Air Potato's FISC Category I
        # text lives only there). The flag finds the sentence; the sentence renders.
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
        # `credit_line` is NOT emitted — the pre-joined string. Every consumer
        # builds its own from credit_name + credit_license, so this was a third
        # copy nothing read. Removed 2026-09-08.
        "focus": focus,

        # ── Added 2026-08-28 — the browse index orders on this. ──
        # feature_tier has been curated in the source since the start but was
        # never emitted, so site.js had no way to sort on it and fell back to
        # PSBP-ID order — i.e. the order things happened to be cataloged in,
        # which buried the rarest holdings on the last page. Recurated
        # 2026-08-28 around "what is here and nowhere else". See
        # FEATURE_TIER.md; due for review every six months.
        "tier": species.get("feature_tier") or "Standard",

        # ── Added 2026-08-18 — four fields the card index never carried. ──
        # searchable text: site.js has always scored matches against p.quick,
        # but nothing ever emitted it, so that branch compared against an empty
        # string on every plant. quick_hits is populated on all 230.
        "quick":   " ".join(card_hits(species)),
        # `dogs`, `edible` and `drought` are NOT emitted. They were added
        # 2026-08-18 as browse facets; the toxicity chips they fed were removed
        # from the index 2026-09-01 ("prose only, per Randy") and nothing has
        # read them since — validated 2026-09-08 across all five files that load
        # this feed. Safety detail lives in the plant page's Take care section,
        # which carries species, symptoms and ASPCA sourcing rather than a
        # two-word badge. The source fields are untouched.
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


# ══════════════════════════════════════════════════════════════════════════
# PLANT PAGE v2 — seven sections, with a page.* override
#
# Agreed with Randy 2026-09-08; full reasoning in park-library
# system docs/PLANT_PAGE_PORT.md. The contract:
#
#     page.<section> present  ->  render it verbatim      (authored)
#     page.<section> absent   ->  assemble from old fields (the mapping)
#
# So all 237 pages publish complete on day one and synthesis rolls out one
# section at a time, with no flag day and NO mechanical relocation pass —
# the mistake made on wildlife, where assembled text was copied into page.*
# and had to be rewritten.
#
# A block is {"label": str|None, "text": str}. Position in the list is
# position on the page.
# ══════════════════════════════════════════════════════════════════════════

V2_GROWS = {"reproduction", "propagation", "rhizomes", "pollination",
            "growth", "life cycle"}

# An entry that talks about THIS PARK belongs in What it does here, not in
# Where it comes from. 96 entries across 68 plants route this way — and they
# are the most distinctive content in the catalogue.
V2_PARK_RE = re.compile(
    r"\b(this park|at the park|the park(?:'s)?\b|our specimen|our own|visitors?\b"
    r"|we suspect|we planted|elsewhere in the (?:park|collection)|this collection"
    r"|side-by-side)\b", re.I)

# Broader meaning to the world — use, commodity, tradition, national symbol.
# Randy, 2026-09-08: "Id suggest a cultural significance sectino honestly ...
# some words that suggest broader meaning to the world". 182 entries, 120 of
# 237 plants (51%) — comparable coverage to Take care.
V2_CULTURAL_RE = re.compile(
    r"\b(used (?:to|for|as|in)|commercial|timber|lumber|rope|fib(?:re|er)|wax|oil"
    r"|medicin\w*|dye|furniture|pillows|upholstery|industry|superfood|export|crop"
    r"|harvest|tradition\w*|ritual|deity|temple|folklore|mytholog\w*|ceremon\w*"
    r"|national (?:flower|tree)|coat of arms|historic\w*|colonial|indigenous|navy"
    r"|ship|boat|carousel|cultur\w*)\b", re.I)

V2_LOC_RE = re.compile(
    r"[^.!?]*\b(office|steps|pavilion|butterfly garden|nursery|pond|gate|entrance"
    r"|boardwalk|welcome island|driveway|shade garden)\b[^.!?]*[.!?]", re.I)


def _v2_polished(species, key):
    """The override. Returns authored blocks, or None to fall through."""
    return (species.get("page") or {}).get(key) or None


def _v2_lst(v):
    if not v:
        return []
    return [str(x) for x in v] if isinstance(v, list) else [str(v)]


def _v2_dict(x):
    """`invasive` is a dict on 130 records, absent on 106 and a bare bool on 1.
    Everything downstream assumes a mapping, so normalise here rather than
    guarding at every call site."""
    return x if isinstance(x, dict) else {}


def _v2_level(x):
    return str(_v2_dict(x).get("level", "") or "").lower()


def _v2_zone(pid):
    """Named placement zone(s) for a plant, e.g. 'Shade Garden'."""
    zones = sorted({z for z in _plant_zones().get(str(pid), []) if z})
    if not zones:
        return None
    return zones[0] if len(zones) == 1 else ", ".join(zones[:-1]) + " and " + zones[-1]


_PLANT_ZONES = None


def _plant_zones():
    global _PLANT_ZONES
    if _PLANT_ZONES is None:
        _PLANT_ZONES = {}
        try:
            raw = load_json(SOURCES / "placements.json", [])
            rows = raw if isinstance(raw, list) else (raw.get("placements") or raw.get("records") or [])
            for r in rows or []:
                if str(r.get("kind", "")).lower() != "species":
                    continue
                z = (r.get("area") or r.get("zone") or "").strip()
                if r.get("subject_id") and z:
                    _PLANT_ZONES.setdefault(str(r["subject_id"]), []).append(z)
        except (OSError, ValueError, KeyError, TypeError) as exc:
            # NOT a bare except. A bare one here silently swallowed a wrong
            # path constant on 2026-09-08 and 'Where to find it here' quietly
            # dropped from 67% of plants to 39% with no error anywhere.
            print(f"  ⚠ placements unreadable, zones unavailable: {exc}")
    return _PLANT_ZONES


# ── the seven mapping rules ───────────────────────────────────────────────

def v2_map_at_a_glance(sp):
    return [{"text": t} for t in _v2_lst(sp.get("quick_hits"))]


def v2_map_how_to_know_it(sp):
    out = []
    for b in (_v2_dict(sp.get("reproduction")).get("blocks") or []):
        label = (b.get("label") or "").strip()
        if label.lower() in V2_GROWS or not b.get("text"):
            continue
        out.append({"label": label or None, "text": b["text"]})
    look = _v2_dict(sp.get("reproduction")).get("what_to_look_for")
    if look:
        out.append({"text": look})
    return out


def v2_map_where_to_find_it_here(sp):
    """Placement zone, else a location the record's own prose names, else
    NOTHING. Randy chose omission over a 'coming soon' placeholder."""
    zone = _v2_zone(sp["id"])
    if zone:
        lead = "In" if zone.lower().startswith("the ") else "In the"
        return [{"text": f"{lead} {zone}."}]
    blob = " ".join(json.dumps(sp.get(k), ensure_ascii=False)
                    for k in ("quick_hits", "more_information", "other_notes", "origin") if sp.get(k))
    m = V2_LOC_RE.search(blob)
    if m:
        return [{"text": m.group(0).strip().strip('"')}]
    return []


def v2_map_what_it_does_here(sp):
    """Value to wildlife AND to this park — widened 2026-09-08."""
    out = [{"text": t} for t in _v2_lst(sp.get("wildlife_value"))]
    notes = _v2_dict(sp.get("butterfly")).get("notes")
    if notes:
        out.append({"label": "Butterflies", "text": notes})
    for m in _v2_lst(sp.get("more_information")):
        if V2_PARK_RE.search(m):
            out.append({"text": m})
    return out


def v2_map_where_it_comes_from(sp):
    """Origin and how it got here. Park material goes to What it does here and
    world-meaning material to Cultural significance, so this stays about
    provenance instead of becoming the dumping ground it was in the first
    mapping (249 words on Wild Lime against 55 authored)."""
    out = [{"text": t} for t in _v2_lst(sp.get("origin"))]
    for m in _v2_lst(sp.get("more_information")):
        if V2_PARK_RE.search(m) or V2_CULTURAL_RE.search(m):
            continue
        out.append({"text": m})
    return out


def v2_map_cultural_significance(sp):
    """What the plant has meant to people — use, commodity, tradition, symbol.
    Live Oak built the USS Constitution; Gumbo Limbo made carousel horses;
    Red Silk Cotton is planted at temples in India. Hidden when there is none."""
    return [{"text": m} for m in _v2_lst(sp.get("more_information"))
            if not V2_PARK_RE.search(m) and V2_CULTURAL_RE.search(m)]


def v2_map_how_it_grows(sp):
    """Conditions, rates, what it flourishes in — Randy's definition, which
    reverses ChatGPT's 'not gardening requirements'. The note is prose and
    leads; the ratings follow as labelled blocks. When this section is
    authored, state a condition only where it explains something."""
    out = []
    gc = sp.get("growing_conditions") or {}
    sz = sp.get("size") or {}
    if gc.get("note"):
        out.append({"text": gc["note"]})
    for label, key in (("Light", "light"), ("Soil", "soil_tolerances"),
                       ("Drought", "drought_tolerance"), ("Salt", "salt_tolerance"),
                       ("Cold", "cold_tolerance")):
        v = gc.get(key)
        if v:
            out.append({"label": label, "text": str(v)[:1].upper() + str(v)[1:]})
    bits = [x for x in (sz.get("height"), sz.get("spread")) if x]
    if bits:
        out.append({"label": "Size", "text": "; ".join(str(b) for b in bits)})
    for label, key in (("Habit", "habit"), ("Growth rate", "growth_rate")):
        if sz.get(key):
            out.append({"label": label, "text": str(sz[key])[:1].upper() + str(sz[key])[1:]})
    inv = _v2_invasive_text(sp)
    if inv:
        out.append({"label": "It spreads", "text": inv})
    return out


def v2_map_take_care(sp):
    """OMITTED unless something is actually Red or Yellow. A section saying
    'nothing will happen' is worse than no section — the rule that removed 33
    filler sections from the wildlife pages."""
    # HAZARD ONLY. Invasive status is not a hazard to a visitor — Randy,
    # 2026-09-08: "invasive info, if interesting goes at botom 'how it grows'.
    # BORING". So it moved to the end of How it grows and this gate no longer
    # reads `invasive` or `watch_invasive`.
    if not any(_v2_level(sp.get(k)) in ("red", "yellow")
               for k in ("toxicity", "edibility")):
        return []
    out, seen = [], []
    for t in _v2_lst(_v2_dict(sp.get("toxicity")).get("people")):
        out.append({"label": "The risk", "text": t})
        seen.append(t)
    for t in _v2_lst(sp.get("safety_note")):
        # dedupe: safety_note routinely restates toxicity.people verbatim
        if any(_v2_same(t, s) for s in seen):
            continue
        out.append({"text": t})
        seen.append(t)
    return out


def _v2_invasive_text(sp):
    """The spreading story, wherever it is stored.

    Air Potato (PSBP-00561) has `invasive: true` — a bare bool where 130 other
    records hold a dict — so its FISC Category I / Florida Noxious Weed status
    lives only in `watch_invasive_notes`. Read both."""
    # LEVEL-GATED. Without this every plant carrying "Native to Florida. Not
    # invasive." got an "It spreads" block — 143 of them, which is exactly the
    # says-nothing-will-happen filler the whole model exists to delete.
    inv = (_v2_dict(sp.get("invasive")).get("notes")
           if _v2_level(sp.get("invasive")) in ("red", "yellow") else None)
    if not inv and sp.get("watch_invasive"):
        inv = sp.get("watch_invasive_notes")
    return inv


def _v2_same(a, b):
    """Near-duplicate test for the take_care dedupe."""
    norm = lambda s: set(re.findall(r"[a-z]{4,}", str(s).lower()))
    wa, wb = norm(a), norm(b)
    if not wa or not wb:
        return False
    return len(wa & wb) / min(len(wa), len(wb)) > 0.6


V2_SECTIONS = [
    # Order set by Randy 2026-09-08, on interest rather than urgency.
    # He rejected hoisting Take care for hazardous plants: "I do not find it
    # persuasive that we could possibly prevent something from happening with
    # our plant species page, so order should be based on interest, not safety."
    ("glance",  "At a glance",           "at_a_glance",           v2_map_at_a_glance),
    ("culture", "Cultural significance", "cultural_significance", v2_map_cultural_significance),
    ("from",    "Where it comes from",   "where_it_comes_from",   v2_map_where_it_comes_from),
    ("find",    "Where to find it here", "where_to_find_it_here", v2_map_where_to_find_it_here),
    ("does",    "What it does here",     "what_it_does_here",     v2_map_what_it_does_here),
    ("know",    "How to know it",        "how_to_know_it",        v2_map_how_to_know_it),
    ("grows",   "How it grows",          "how_it_grows",          v2_map_how_it_grows),
    ("care",    "Take care",             "take_care",             v2_map_take_care),
]


def v2_section_blocks(species, key, mapper):
    """Authored blocks if present, else mapped. Returns (blocks, authored?)."""
    pol = _v2_polished(species, key)
    if pol:
        return pol, True
    return mapper(species), False


# ── v2 rendering ──────────────────────────────────────────────────────────

def _v2_inflow_figure(rec, caption):
    """A photograph placed INSIDE the prose, with the wide credit plate.
    Markup copied from a generated wildlife page — do not reconstruct it."""
    c = resolve_hero_credit(rec) or {}
    src = (f"../photos/{rec['psbp_id']}/{rec['filename']}"
           if rec.get("filename") and (PHOTOS_DIR / rec["psbp_id"] / rec["filename"]).exists()
           else rec.get("photo_url", ""))
    cap = f"<figcaption>{h(str(caption))}</figcaption>" if caption else ""
    return (f'<figure class="sp-figure"><img src="{h(src)}" alt="" loading="lazy">{cap}'
            '<div class="credit-plate"><div class="credit-byline">'
            '<span class="credit-eyebrow">Photograph by</span>'
            f'<span class="credit-name">{h(c.get("display") or c.get("photographer") or "")}</span></div>'
            '<div class="credit-license"><span class="cc-badge"><span class="cc-mark">cc</span>'
            f'<span class="cc-term">{h((c.get("license") or "").replace("CC-", ""))}</span></span>'
            f'<span class="credit-src">{h(c.get("date") or "")} &middot; via iNaturalist</span>'
            '</div></div></figure>')


def _v2_block_html(b, photo_index=None):
    """A block is prose, or a photograph placed in the flow.

    ⚠ Photo blocks matter: contextual placement is the strongest feature of the
    wildlife pages (the Osprey's nest material, the Bright Futures rocks). An
    earlier version of this function ignored them and rendered an empty
    paragraph — silently, which is the worst way to lose a photograph."""
    if not isinstance(b, dict):
        return f'<div class="sp-block"><p>{h(str(b))}</p></div>'
    if b.get("photo"):
        rec = (photo_index or {}).get(str(b["photo"]))
        return _v2_inflow_figure(rec, b.get("caption")) if rec else ""
    label = b.get("label")
    lab = f'<div class="sp-block-label">{h(str(label))}</div>' if label else ""
    return f'<div class="sp-block">{lab}<p>{h(str(b.get("text","")))}</p></div>'


def _v2_fact_html(text):
    """A single one-off fact gets a callout instead of a section of its own.
    78 of the 120 plants carrying cultural material carry exactly one."""
    return ('<div class="sp-fact"><div class="sp-fact-h">Worth knowing</div>'
            f'<p>{h(str(text))}</p></div>')


def generate_html_v2(species, hero, gallery_photos=None, published_on=""):
    """The seven-section plant page. See V2_SECTIONS for the order and the
    mapping, and park-library system docs/PLANT_PAGE_PORT.md for the reasoning."""
    pid    = species["id"]
    common = species["common_name"]
    sci    = species.get("botanical_name", "")
    eyebrow = species.get("form") or (species.get("category") or "").replace(" and ", " & ")
    gallery_photos = gallery_photos or []

    focus = (hero.get("focus") if hero else None) or "50% 50%"
    hero_src = f"../photos/{pid}/{hero['filename']}" if hero else ""
    hc = resolve_hero_credit(hero) if hero else {}
    hero_by  = hc.get("display") or hc.get("photographer") or ""
    hero_date = hc.get("date") or ""
    hero_lic  = (hc.get("license") or "").replace("CC-", "")

    # A single cultural fact becomes a callout inside Where it comes from,
    # rather than a section with one paragraph in it.
    photo_index = {}
    for _p in ([hero] if hero else []) + list(gallery_photos):
        if _p and _p.get("photo_id"):
            photo_index[str(_p["photo_id"])] = _p

    cult_blocks, cult_authored = v2_section_blocks(
        species, "cultural_significance", v2_map_cultural_significance)
    single_fact = (len(cult_blocks) == 1 and not cult_authored)

    rail, main = [], []
    for anchor, title, key, mapper in V2_SECTIONS:
        if key == "cultural_significance" and single_fact:
            continue                                   # rendered as a callout
        blocks, _ = v2_section_blocks(species, key, mapper)
        if not blocks:
            continue                                   # conditional — omit, never apologise
        rail.append(f'<a href="#{anchor}"{" class=\"on\"" if not rail else ""}>{h(title)}</a>')
        if key == "at_a_glance":
            inner = ('<div class="sp-quick"><ul>'
                     + "".join(f"<li>{h(str(b.get('text', b)))}</li>" for b in blocks)
                     + "</ul></div>")
        else:
            inner = "".join(_v2_block_html(b, photo_index) for b in blocks)
            if key == "where_it_comes_from" and single_fact:
                inner = _v2_fact_html(cult_blocks[0].get("text", "")) + inner
        main.append(f'<section class="sp-sec" id="{anchor}"><h2>{h(title)}</h2>'
                    f'<div class="sp-sec-rule"></div>{inner}</section>')

    # ── photographs: a gallery of one is not a gallery ────────────────────
    strip = ""
    photos_js = "[]"
    if len(gallery_photos) >= 2:
        creds = resolve_gallery_credits(gallery_photos)
        _lb = []
        for i, g in enumerate(gallery_photos):
            c = creds[i] if i < len(creds) else {}
            local = PHOTOS_DIR / pid / (g.get("filename") or "")
            _lb.append({"src": (f"../photos/{pid}/{g['filename']}"
                                if g.get("filename") and local.exists() else g.get("photo_url", "")),
                        "alt": "",
                        "by": c.get("display") or c.get("photographer") or "",
                        "date": c.get("date") or ""})
        photos_js = json.dumps(_lb, ensure_ascii=False)
        thumbs = "".join(
            f'<button type="button" data-i="{i}" aria-label="Photograph">'
            f'<img src="{h(g.get("photo_url",""))}" alt=""></button>'
            for i, g in enumerate(gallery_photos) if i > 0)
        strip = (f'<div class="sp-strip" id="heroStrip">{thumbs}</div>'
                 '<button class="sp-herogal" id="heroGal" type="button" '
                 'aria-label="Open the photograph gallery">'
                 f'<span>Gallery <span class="n">{len(gallery_photos)}</span></span></button>')
        figs = ""
        for i, g in enumerate(gallery_photos):
            c = creds[i] if i < len(creds) else {}
            local = PHOTOS_DIR / pid / (g.get("filename") or "")
            src = f"../photos/{pid}/{g['filename']}" if g.get("filename") and local.exists() \
                  else g.get("photo_url", "")
            figs += (f'<figure data-i="{i}"><div class="shot">'
                     f'<img src="{h(src)}" alt="" loading="lazy"></div>'
                     '<figcaption><div class="credit-plate">'
                     '<span class="credit-eyebrow">Photograph by</span>'
                     f'<span class="credit-name">{h(c.get("display") or c.get("photographer") or "")}</span>'
                     '<span class="credit-meta"><span class="cc-badge"><span class="cc-mark">cc</span>'
                     f'<span class="cc-term">{h((c.get("license") or "").replace("CC-", ""))}</span></span>'
                     f'<span>{h(c.get("date") or "")}</span><span class="sep">&middot;</span>'
                     '<span>iNaturalist</span></span></div></figcaption></figure>')
        rail.append('<a href="#photos">Photographs</a>')
        main.append('<section class="sp-sec" id="photos"><h2>Photographs</h2>'
                    '<div class="sp-sec-rule"></div>'
                    '<p style="color:var(--ink-soft);font-size:var(--t-sm);margin-bottom:1.2rem">'
                    'Every one taken in this park, by the people who walk it.</p>'
                    f'<div class="sp-gal" id="gal">{figs}</div></section>')

    aka = species.get("alternate_names") or []
    aka_html = ('<div class="sp-aka"><div class="sp-block-label">Also known as</div>'
                f'<p>{" &middot; ".join(h(a) for a in aka)}</p></div>') if aka else ""

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{h(common)} ({h(sci)}) — Palma Sola Botanical Park</title>
<link rel="icon" type="image/png" href="../images/favicon.png">
<link rel="apple-touch-icon" href="../images/favicon.png">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Playfair+Display:ital,wght@0,700;1,600&family=Source+Sans+3:wght@400;600;700&display=swap" rel="stylesheet">
<link rel="stylesheet" href="../css/psbp.css">
<link rel="stylesheet" href="../css/species-v2.css">
</head>
<body data-bands="on">
<div id="nav-placeholder"></div>

<div class="sp-hero">
  <img class="sp-hero-fg" src="{h(hero_src)}" alt="{h(common)} at Palma Sola Botanical Park" style="object-position:{h(focus)}">
  <div class="sp-hero-scrim"></div>
  <div class="photo-attr photo-attr--muted sp-heroattr"><span class="attr-line"><span class="attr-credit"><span class="attr-by">{h(hero_by)}</span><span class="attr-date"><span class="attr-dot">&middot;</span> {h(hero_date)}</span></span><span class="cc-badge"><span class="cc-mark">cc</span><span class="cc-term">{h(hero_lic)}</span></span><span class="attr-src">via iNaturalist</span></span></div>
  {strip}
  <div class="sp-hero-inner">
    <div class="sp-eyebrow">{h(eyebrow)}</div>
    <h1 class="sp-name">{h(common)}</h1>
    <div class="sp-sci">{h(sci)}</div>
  </div>
</div>

<div class="sp-herocap"><span class="cap"></span><span class="seqt" id="seq-top"></span></div>

<div class="sp-wrap">
  <div class="sp-cols">
    <aside class="sp-rail"><div class="sp-rail-title">On this page</div>{''.join(rail)}</aside>
    <main>{''.join(main)}{aka_html}</main>
  </div>
</div>

<a class="all-plants-link" href="../nature.html#plants">All plants</a>

<div class="lb" id="lb" aria-hidden="true">
  <button class="lb-close" id="lbClose" aria-label="Close">&times;</button>
  <div class="lb-stage" id="lbStage"><img id="lbImg" src="" alt=""></div>
  <div class="lb-foot"><div class="lb-cred"><div class="lb-eyebrow">Photograph by</div>
  <div class="lb-name" id="lbName"></div><div class="lb-meta" id="lbMeta"></div></div>
  <div class="lb-ctrls"><button class="lb-btn" id="lbPrev" aria-label="Previous photograph">&#8249;</button>
  <span class="lb-count" id="lbCount"></span>
  <button class="lb-btn" id="lbNext" aria-label="Next photograph">&#8250;</button></div>
  <div class="lb-hint">Swipe to move between photographs</div></div>
</div>

<div id="footer-placeholder"></div>

<script>window.PHOTOS={photos_js};</script>
<script src="../js/species-v2.js"></script>
<script src="../js/site.js"></script>
<script>if (typeof injectShared === 'function') {{ injectShared({{ inatBar: false }}); }}</script>
</body>
</html>"""


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
<link rel="icon" type="image/png" href="../images/favicon.png">
<link rel="apple-touch-icon" href="../images/favicon.png">
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
    html_content = generate_html_v2(species, hero, gallery_photos, published_on=prev)
    if dry_run:
        return path, html_content

    if page_content_changed(path, html_content):
        stamp = today_iso()
        if stamp != prev:
            html_content = generate_html_v2(species, hero, gallery_photos,
                                         published_on=stamp)
    else:
        stamp = prev or today_iso()
        if stamp != prev:
            html_content = generate_html_v2(species, hero, gallery_photos,
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

  // Invasive Status block removed 2026-09-01. It rendered the retired
  // invasive{level,notes} traffic-light, which survives on ~131 records and
  // disagrees with the live watch_invasive flag on five of them. Showing it
  // here is how PSBP-00119 Sweet Viburnum came to be published as a "FISC
  // Category I invasive" it is not: the dict said Red, the flag said false,
  // and the prose was written from the screen. Invasive status lives in prose.

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
            preview_html = generate_html_v2(species, hero, galleries.get(pid, []))
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
