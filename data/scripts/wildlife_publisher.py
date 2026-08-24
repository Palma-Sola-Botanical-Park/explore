#!/usr/bin/env python3
"""wildlife_publisher.py — Review, generate, and publish PSBP wildlife pages.

Reads wildlife_signage.json + photo_credits.json → generates HTML wildlife pages
with photo galleries and maintains wildlife.json (the search/card index).

Usage:
    python3 wildlife_publisher.py                      # Launch dashboard on http://localhost:8702
    python3 wildlife_publisher.py --generate-all       # Batch-generate HTML for all status=html species
    python3 wildlife_publisher.py --validate           # Compare existing HTML against JSON sources
    python3 wildlife_publisher.py --generate PSBP-99981  # Generate one species
    python3 wildlife_publisher.py --clean              # Remove non-html entries from wildlife.json
    python3 wildlife_publisher.py --demote PSBP-99981  # Pull back html → spotted

Gallery photos are served from iNaturalist CDN (not stored locally).
Hero photos use local paths (photos/PSBP-xxxxx/<filename>.jpg).
"""

import http.server
import json
import os
import re
import sys
from copy import deepcopy
from datetime import date
from html import escape as h
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from psbp_common import (
    REPO, SOURCES,
    WILDLIFE_SIGNAGE_JSON as SIGNAGE_JSON,
    PHOTO_CREDITS_JSON as CREDITS_JSON,
    WILDLIFE_JSON, WILDLIFE_DIR, PHOTOS_DIR,
    load_json, write_json_atomic,
    display_name, build_credit_line,
    resolve_hero_credit, resolve_gallery_credits,
    delete_species_page,
    # Theme mapping lives in psbp_common — single source of truth for
    # animal_group → theme decisions. See ANIMAL GROUPS & THEMES there.
    theme_for, check_animal_group,
)

PORT = 8702

# ── Data loading (thin wrappers over psbp_common paths) ─────────────────────

def load_signage():
    return load_json(SIGNAGE_JSON, {"species": []})

def load_credits():
    return load_json(CREDITS_JSON, {"meta": {}, "photos": []})

def load_wildlife_json():
    return load_json(WILDLIFE_JSON, [])

def build_hero_lookup(credits):
    from psbp_common import build_hero_lookup as _bhl
    return _bhl(credits, type_filter="Wildlife")

def build_gallery_lookup(credits):
    from psbp_common import build_gallery_lookup as _bgl
    return _bgl(credits, type_filter="Wildlife")

def build_species_lookup(signage):
    return {s["id"]: s for s in signage["species"]}

# ── Slug helper ─────────────────────────────────────────────────────────────

def slugify(name):
    return re.sub(r"[^A-Za-z0-9-]", "", name.replace(" ", "-").replace("'", ""))

def page_filename(psbp_id, common_name):
    return f"{psbp_id}-{slugify(common_name)}.html"

# ── wildlife.json entry builder ─────────────────────────────────────────────

def _safety_word(level):
    """Traffic-light grade -> a word a visitor understands. Mirrors the helper
    of the same name in plant_publisher.py so the two card indexes speak the
    same language: safe / caution / toxic."""
    return {"green": "safe", "yellow": "caution", "red": "toxic"}.get(
        (level or "").strip().lower(), ""
    )


def build_wildlife_json_entry(species, hero):
    pid = species["id"]
    theme = theme_for(species.get("animal_group", ""))

    # Credit resolution — resolve real name + license from hero record
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
        "sci": species["scientific_name"],
        "family": (species.get("taxonomy") or {}).get("family", ""),
        "theme": theme,
        "category": species.get("category", ""),
        "native": bool(species.get("native")),
        "aliases": species.get("also_known_as") or [],
        "tags": species.get("tags") or [],
        "credit": hero_credit["credit_name"],
        "credit_name": hero_credit["credit_name"],
        "credit_license": hero_credit["credit_license"],
        "credit_line": hero_credit["credit_line"],
        "photo": photo,
        "focus": focus or "50% 50%",
        "page": f"wildlife/{page_filename(pid, species['common_name'])}",

        # ── Added 2026-08-18 — three fields the card index never carried. ──
        # searchable text: filterWildlife() in site.js has always scored against
        # w.quick, but nothing ever emitted it, so that branch compared against
        # an empty string. quick_hits is populated on all 90.
        "quick":  " ".join(species.get("quick_hits") or []),
        # facet: safe around a dog?  mirrors "dogs" on the plant cards
        "pets":   _safety_word((species.get("danger") or {}).get("pets_level")),
        # facet: which months is it here? already a real int array, 90/90
        "months": (species.get("seasonality") or {}).get("months") or [],
    }

# ── HTML generation ─────────────────────────────────────────────────────────

# WILD_CSS moved to css/wildlife-page.css on 2026-08-18 and is now linked, not
# inlined — same reasoning as plant-page.css. Extraction was verbatim.


# ── Section renderers ───────────────────────────────────────────────────────

def render_badges(species):
    # RETAINED BUT NOT RENDERED. The chip/status row was removed from the page
    # template (pages now go header → credit → Quick Hits). This function and the
    # .badge CSS are kept so the conditional-chip idea ("show only notable
    # conditions: Threatened, Dangerous, Invasive") can be revisited later without
    # rebuilding from scratch. Wire it back into the wild-content template to use.
    badges = []
    # Origin badge. `native_status` (optional) gives a third option for migrants
    # and winter visitors that are native to North America but do NOT live in
    # Florida year-round — labeling those "Native to Florida" misleads visitors.
    #   native_status == "migrant"  → "Native Migrant"
    #   else falls back to the native True/False boolean.
    native_status = (species.get("native_status") or "").strip().lower()
    if native_status == "migrant":
        badges.append('<span class="badge badge-native">🌿 Native Migrant</span>')
    elif native_status == "non-native" or (not native_status and not species.get("native")):
        badges.append('<span class="badge badge-neutral">🌍 Non-Native</span>')
    else:
        badges.append('<span class="badge badge-native">🌿 Native to Florida</span>')

    cons = (species.get("conservation") or {}).get("level", "Green")
    cons_status = (species.get("conservation") or {}).get("status", "")
    # Extract short label from status (e.g. "Least Concern. ..." → "Least Concern")
    cons_label = cons_status.split(".")[0].strip() if cons_status else "Unknown"
    if cons == "Green":
        badges.append(f'<span class="badge badge-green">✅ {h(cons_label)}</span>')
    elif cons == "Yellow":
        badges.append(f'<span class="badge badge-warn">⚠️ {h(cons_label)}</span>')
    else:
        badges.append(f'<span class="badge badge-danger">⚠️ {h(cons_label)}</span>')

    danger = (species.get("danger") or {}).get("people_level", "Green")
    danger_text = (species.get("danger") or {}).get("people", "")
    danger_label = danger_text.rstrip(".").strip() if danger_text else "Unknown"
    if danger == "Green":
        badges.append(f'<span class="badge badge-safe">✅ {h(danger_label)}</span>')
    elif danger == "Yellow":
        badges.append(f'<span class="badge badge-warn">⚠️ {h(danger_label)}</span>')
    else:
        badges.append(f'<span class="badge badge-danger">⚠️ {h(danger_label)}</span>')

    return "".join(badges)


def _allow_bold(text):
    """Escape all HTML, then restore ONLY <b>/</b> tags.

    Lets signage authors bold a keyword with <b>...</b> in quick hits while
    keeping every other character safely escaped — a stray < or & can't break
    the page or inject markup. Bold is the only tag permitted.
    """
    safe = h(text or "")
    safe = safe.replace("&lt;b&gt;", "<b>").replace("&lt;/b&gt;", "</b>")
    safe = safe.replace("&lt;B&gt;", "<b>").replace("&lt;/B&gt;", "</b>")
    return safe


def render_quick_hits(species):
    items = species.get("quick_hits") or []
    if not items:
        return ""
    li = "".join(f"<li>{_allow_bold(q)}</li>" for q in items)
    return f'<div class="wild-section"><div class="wild-section-header"><span class="wild-section-icon">⚡</span><span class="wild-section-title">Quick Hits</span></div><ul class="quick-hits-list">{li}</ul></div>'


def render_identification(species):
    ident = species.get("identification")
    if not ident:
        return ""
    blocks = ident.get("blocks") or []
    wtlf = ident.get("what_to_look_for", "")
    sounds = species.get("sounds", "")
    items = []
    for b in blocks:
        items.append(f'<div class="spot-item"><div class="spot-label">{h(b["label"])}</div><p>{h(b["text"])}</p></div>')
    if sounds:
        items.append(f'<div class="spot-item"><div class="spot-label">Voice</div><p>{h(sounds)}</p></div>')
    if wtlf:
        items.append(f'<div class="spot-item look"><div class="spot-label">What to Look For</div><p>{h(wtlf)}</p></div>')
    return f'<div class="wild-section"><div class="wild-section-header"><span class="wild-section-icon">🔎</span><span class="wild-section-title">How to Spot It</span></div><div class="spot-list">{"".join(items)}</div></div>'


def render_diet(species):
    diet = species.get("diet")
    if not diet:
        return ""
    return f'<div class="wild-section"><div class="wild-section-header"><span class="wild-section-icon">🍽️</span><span class="wild-section-title">What It Eats</span></div><div class="wild-section-body"><p>{h(diet)}</p></div></div>'


def render_where_when(species):
    where = species.get("where_to_look", "")
    when = species.get("when_to_see", "")
    if not where and not when:
        return ""
    items = []
    if where:
        items.append(f'<div class="spot-item"><div class="spot-label">Where in the park</div><p>{h(where)}</p></div>')
    if when:
        items.append(f'<div class="spot-item"><div class="spot-label">When to see it</div><p>{h(when)}</p></div>')
    return f'<div class="wild-section"><div class="wild-section-header"><span class="wild-section-icon">📍</span><span class="wild-section-title">Where &amp; When</span></div><div class="spot-list">{"".join(items)}</div></div>'


def render_interaction(species):
    """Render the 'Watching It Respectfully' section with appropriate severity."""
    interaction = species.get("interaction") or {}
    level = interaction.get("level", "Green")
    guidance = interaction.get("guidance", "")
    if not guidance:
        guidance = "Enjoy from a distance and please do not feed or approach any park wildlife."

    if level == "Red":
        cls = "wild-danger-section"
    elif level == "Yellow":
        cls = "wild-caution-section"
    else:
        cls = "wild-safe-section"

    return f'<div class="{cls}"><div class="wild-section-header"><span class="wild-section-icon">🤝</span><span class="wild-section-title">Watching It Respectfully</span></div><div class="wild-section-body"><p>{h(guidance)}</p></div></div>'


def _fmt_observed(date_str):
    """Format an ISO date (2025-11-14) as 'Nov 14, 2025' for display.

    Returns "" if the date is missing or unparseable, so callers can skip it
    cleanly. Only the date is shown — this is the observation date that lets
    visitors see *when* each photo was taken (e.g. proving a winter visitor).
    """
    if not date_str:
        return ""
    try:
        from datetime import datetime as _dt
        return _dt.strptime(date_str[:10], "%Y-%m-%d").strftime("%b %-d, %Y")
    except (ValueError, TypeError):
        return ""


def render_gallery(species, gallery_photos, hero):
    """Render the photo gallery section with lightbox.

    Hero photo (index 0): local path ../photos/PSBP-xxxxx/<filename>.jpg
    Gallery photos (index 1+): iNaturalist CDN URLs (not stored locally)
    """
    if not gallery_photos and not hero:
        return "", ""

    pid = species["id"]
    common = species["common_name"]

    # Build lightbox data array: hero first, then gallery photos
    lb_data = []
    if hero:
        hc = resolve_hero_credit(hero)
        lb_data.append({
            "src": f"../photos/{pid}/{hero['filename']}",
            "credit": hc["credit_name"],
            "license": hc["credit_license"],
            "observed": _fmt_observed(hero.get("observed_on", "")),
        })

    # Gallery items (non-hero photos shown in the grid)
    grid_items = []
    for p in (gallery_photos or []):
        if p.get("hero"):
            continue  # hero is already index 0
        url = p.get("photo_url", "")
        if not url:
            continue
        idx = len(lb_data)
        photographer = display_name(p.get("photographer", ""), p.get("photographer_name", ""))
        observed = _fmt_observed(p.get("observed_on", ""))
        lb_data.append({
            "src": url,
            "credit": photographer,
            "license": (p.get("license") or "").upper(),
            "observed": observed,
        })
        date_html = f'<div class="gal-date">📅 {h(observed)}</div>' if observed else ""
        grid_items.append(
            f'<div class="gal-item" onclick="openLB({idx})">'
            f'<img src="{h(url)}" loading="lazy" alt="{h(common)} — photo by {h(photographer)}">'
            f'<div class="gal-credit">📷 {h(photographer)}</div>{date_html}</div>'
        )

    if not grid_items:
        # No gallery photos beyond the hero — still include lightbox for hero click
        gallery_html = ""
    else:
        gallery_html = (
            f'<div class="wild-section"><div class="wild-section-header">'
            f'<span class="wild-section-icon">📸</span>'
            f'<span class="wild-section-title">Photo Gallery</span></div>\n'
            f'    <div class="gal-note">Photos contributed by park visitors and volunteers via iNaturalist</div>\n'
            f'    <div class="gal-grid">{"".join(grid_items)}</div></div>'
        )

    # Lightbox HTML + JS (always present if we have any photos)
    if not lb_data:
        return "", ""

    lb_json = json.dumps(lb_data, ensure_ascii=False)
    lightbox_html = f"""    <div class="lightbox" id="lb" onclick="closeLB(event)">
      <div class="lb-inner">
        <button class="lb-close" onclick="closeLB()">&times;</button>
        <button class="lb-prev" onclick="stepLB(-1)">&#8249;</button>
        <img class="lb-img" id="lbImg">
        <button class="lb-next" onclick="stepLB(1)">&#8250;</button>
        <div class="lb-credit" id="lbCredit"></div>
        <div class="lb-counter" id="lbCounter"></div>
      </div>
    </div>
    <script>
    var lbData={lb_json};
    var lbIdx=0;
    function openLB(i){{lbIdx=i;var d=lbData[i];document.getElementById('lbImg').src=d.src;var credit='📷 '+d.credit+' · '+d.license+' · via iNaturalist';if(d.observed)credit+=' · 📅 '+d.observed;document.getElementById('lbCredit').innerHTML=credit;document.getElementById('lbCounter').textContent=(i+1)+' / '+lbData.length;document.getElementById('lb').classList.add('active');document.body.style.overflow='hidden';}}
    function closeLB(e){{if(e&&e.target!==document.getElementById('lb')&&!e.target.classList.contains('lb-close'))return;document.getElementById('lb').classList.remove('active');document.body.style.overflow='';}}
    function stepLB(dir){{lbIdx=(lbIdx+dir+lbData.length)%lbData.length;openLB(lbIdx);}}
    document.addEventListener('keydown',function(e){{if(!document.getElementById('lb').classList.contains('active'))return;if(e.key==='Escape')closeLB();if(e.key==='ArrowRight')stepLB(1);if(e.key==='ArrowLeft')stepLB(-1);}});
    </script>"""

    return gallery_html, lightbox_html


def render_tags(species):
    """Render the public 'Also Known As' section — REAL alternate names only.

    `tags` (Bird, Native, Loud, Cavity nester, etc.) are INTERNAL dashboard
    metadata used for filtering/organizing species. They are never published.
    Only genuine alternate common names from `also_known_as` appear here, and
    the whole section is dropped when there are none.
    """
    aliases = species.get("also_known_as") or []
    if not aliases:
        return ""
    inner = ('<div class="alias-list">'
             + "".join(f'<span class="alias-tag">{h(a)}</span>' for a in aliases)
             + '</div>')
    return f'<div class="wild-section"><div class="wild-section-header"><span class="wild-section-icon">🏷️</span><span class="wild-section-title">Also Known As</span></div>{inner}</div>'


def generate_html(species, hero, gallery_photos, published_on=""):
    """Render the page.

    published_on is the STORED publish date (see PUBLISH STATE in
    psbp_common) — never the current time. Embedding "now" would make every
    page differ from itself on every render, and the render-and-compare
    census in psbp_page_drift.py would report all 289 pages permanently
    stale."""
    pid = species["id"]
    common = species["common_name"]
    sci = species["scientific_name"]
    family = (species.get("taxonomy") or {}).get("family", "")
    category = species.get("category", "")
    theme = theme_for(species.get("animal_group", ""))
    focus = (hero.get("focus") if hero else None) or "50% 50%"

    # Hero image — credit resolved through photographer_names.json
    if hero:
        hero_path = f"../photos/{pid}/{hero['filename']}"
        hc = resolve_hero_credit(hero)
        credit_parts = [f'📷 Photo by <strong>{h(hc["credit_name"])}</strong>']
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
        hero_path = f"../photos/{pid}-{slugify(common)}.jpg"
        credit_html = "📷 Photo credit pending"

    # Publish stamp — the visitor-facing freshness signal.
    from psbp_common import fmt_published as _fmt_pub
    _pub_disp = _fmt_pub(published_on)
    stamp_html = (f'<div class="page-stamp">Page updated <strong>{h(_pub_disp)}</strong></div>'
                  if _pub_disp else "")

    # Sections
    gallery_section, lightbox_section = render_gallery(species, gallery_photos, hero)

    sections = []
    sections.append(render_quick_hits(species))
    sections.append(render_identification(species))
    sections.append(render_diet(species))
    sections.append(render_where_when(species))
    sections.append(render_interaction(species))
    if gallery_section:
        sections.append(gallery_section)
    sections.append(render_tags(species))

    # Photo credits block — stamped at build time, no runtime lookups
    all_gallery = list(gallery_photos or [])
    if hero and hero not in all_gallery:
        all_gallery.insert(0, hero)
    gallery_creds = resolve_gallery_credits(all_gallery)
    if gallery_creds:
        cred_items = ''.join(
            f'<li style="font-size:15px;line-height:1.65;color:var(--text-mid);'
            f'padding:8px 0;border-bottom:1px solid rgba(60,90,110,0.10)">'
            f'{h(gc["credit_line"])}</li>'
            for gc in gallery_creds
        )
        sections.append(
            f'<div class="wild-section"><div class="wild-section-header">'
            f'<span class="wild-section-icon">📸</span>'
            f'<span class="wild-section-title">Photo Credits</span></div>'
            f'<ul style="list-style:none;padding:10px 16px">{cred_items}</ul></div>'
        )

    content = "\n    ".join(s for s in sections if s)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{h(common)} · Palma Sola Botanical Park</title>
<link rel="stylesheet" href="../css/site.css">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Playfair+Display:ital,wght@0,700;1,600&display=swap" rel="stylesheet">
<link rel="stylesheet" href="../css/wildlife-page.css">
</head>
<body>
<div id="nav-placeholder"></div>

<div class="wild-wrap theme-{theme}">
  <div class="wild-hero">
    <img style="cursor:pointer;object-position:{h(focus)}" src="{hero_path}" alt="{h(common)} at Palma Sola Botanical Park" onclick="openLB(0)">
    <div class="wild-hero-overlay">
      <div class="wild-hero-category">{h(category)}</div>
      <div class="wild-hero-name">{h(common)}</div>
    </div>
  </div>
  <div class="wild-sci-band">
    <span class="wild-sci-name">{h(sci)}</span>
    <a class="wild-family-tag" href="../nature.html?wfamily={h(family)}">{h(family)}</a>
  </div>
  <div class="wild-credit">{credit_html}</div>
  <div class="wild-content">
    {content}
{lightbox_section}
    <a class="all-wild-link" href="../nature.html#wildlife">🦜 Explore More Wildlife</a>
  </div>
</div>

<a class="wild-float-back theme-{theme}" href="../nature.html#wildlife">🦜 All Wildlife</a>

{stamp_html}
<div id="footer-placeholder"></div>
<script src="../js/site.js"></script>
<script>if (typeof injectShared === 'function') {{ injectShared({{ inatBar: false }}); }}</script>
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
    """Write the page. Sole write path for wildlife pages — every caller
    (dashboard, CLI publish, --generate-all) routes through here, which is why
    the publish stamp is recorded here rather than at the call sites.

    last_published moves only when the RENDERED page changes. See
    psbp_common.page_content_changed for why the input hash isn't used for that.
    """
    from psbp_common import today_iso, page_content_changed
    input_hash, generator, prev = _publish_fingerprints(species, hero, gallery_photos)
    filename = page_filename(species["id"], species["common_name"])
    path = WILDLIFE_DIR / filename

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

    WILDLIFE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(html_content, encoding="utf-8")
    tmp.rename(path)

    # A common_name edit moves where this writes. Reconcile what is already on
    # disk so the id keeps exactly one page, under the name the index points at.
    # See psbp_common.reconcile_page_siblings — the case step is load-bearing.
    from psbp_common import reconcile_page_siblings
    recased, dropped = reconcile_page_siblings(WILDLIFE_DIR, species["id"], filename)
    if recased:
        print(f"    ↻ {species['id']}: corrected filename case "
              f"{recased} -> {filename}")
    for gone in dropped:
        print(f"    ✕ {species['id']}: removed stale page {gone} "
              f"(renamed to {filename})")

    _record_publish("wildlife", species["id"], input_hash, generator, filename, stamp)
    return path, html_content

def update_wildlife_json(species, hero):
    entries = load_wildlife_json()
    entry = build_wildlife_json_entry(species, hero)
    found = False
    for i, e in enumerate(entries):
        if e["id"] == entry["id"]:
            entries[i] = entry
            found = True
            break
    if not found:
        entries.append(entry)
    entries.sort(key=lambda e: e["id"])
    write_json_atomic(WILDLIFE_JSON, entries)
    return entry


def update_signage_status(species_id, new_status):
    """Thin wrapper — delegates to psbp_common with corpus='wildlife'."""
    from psbp_common import update_signage_status as _uss
    _uss("wildlife", species_id, new_status)


# ── Dashboard HTML ──────────────────────────────────────────────────────────

DASHBOARD_HTML = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>PSBP Wildlife Publisher</title>
<style>
  * { margin:0; padding:0; box-sizing:border-box; }
  body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; background:#101820; color:#e8e3d8; }
  .layout { display:grid; grid-template-columns:320px 1fr; height:100vh; }
  .sidebar { background:#181e24; border-right:1px solid #2a3038; overflow-y:auto; display:flex; flex-direction:column; }
  .sidebar-header { padding:16px; background:#235e86; border-bottom:2px solid #b8942a; position:sticky; top:0; z-index:10; }
  .sidebar-header h1 { font-size:15px; font-weight:700; letter-spacing:2px; text-transform:uppercase; color:#d4aa40; }
  .sidebar-header .counts { font-size:12px; color:#8ba4b8; margin-top:4px; }
  .filter-bar { padding:8px 12px; display:flex; gap:6px; flex-wrap:wrap; position:sticky; top:60px; background:#181e24; z-index:9; border-bottom:1px solid #2a3038; }
  .filter-btn { font-size:11px; font-weight:700; padding:4px 10px; border-radius:12px; border:1.5px solid; cursor:pointer; background:transparent; transition:all .2s; }
  .filter-btn[data-status="html"] { color:#4a9e56; border-color:#4a9e56; }
  .filter-btn[data-status="html"].active { background:#4a9e56; color:#fff; }
  .filter-btn[data-status="spotted"] { color:#d4aa40; border-color:#d4aa40; }
  .filter-btn[data-status="spotted"].active { background:#d4aa40; color:#1a1a14; }
  .filter-btn[data-status="research"] { color:#888; border-color:#666; }
  .filter-btn[data-status="research"].active { background:#666; color:#fff; }
  .search-box { width:calc(100% - 24px); padding:8px 12px; background:#101820; border:1px solid #2a3038; border-radius:6px; color:#e8e3d8; font-size:13px; margin:8px 12px; }
  .search-box:focus { outline:none; border-color:#d4aa40; }
  .species-list { flex:1; overflow-y:auto; }
  .species-item { padding:10px 14px; border-bottom:1px solid #1e2630; cursor:pointer; transition:background .15s; display:flex; align-items:center; gap:10px; }
  .species-item:hover { background:#1e2630; }
  .species-item.selected { background:#1a3050; border-left:3px solid #d4aa40; }
  .species-item .dot { width:8px; height:8px; border-radius:50%; flex-shrink:0; }
  .species-item .dot.html { background:#4a9e56; }
  .species-item .dot.spotted { background:#d4aa40; }
  .species-item .dot.research { background:#666; }
  .species-item .info { flex:1; min-width:0; }
  .species-item .name { font-size:14px; font-weight:600; color:#e8e3d8; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
  .species-item .sci { font-size:12px; color:#8ba4b8; font-style:italic; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
  .species-item .id-tag { font-size:10px; color:#556; font-family:monospace; }
  .main { overflow-y:auto; background:#101820; }
  .main-empty { display:flex; align-items:center; justify-content:center; height:100%; color:#556; font-size:18px; }
  .detail { padding:24px; max-width:900px; }
  .detail-header { display:flex; align-items:flex-start; gap:20px; margin-bottom:24px; }
  .detail-hero { width:280px; height:200px; border-radius:10px; overflow:hidden; flex-shrink:0; background:#1e2630; }
  .detail-hero img { width:100%; height:100%; object-fit:cover; }
  .detail-hero .no-hero { display:flex; align-items:center; justify-content:center; height:100%; color:#556; font-size:14px; }
  .detail-meta { flex:1; }
  .detail-meta h2 { font-family: Georgia, serif; font-size:28px; color:#e8e3d8; margin-bottom:4px; }
  .detail-meta .sci { font-size:18px; color:#8ba4b8; font-style:italic; margin-bottom:8px; }
  .detail-meta .meta-row { font-size:13px; color:#8ba4b8; margin-bottom:3px; }
  .detail-meta .meta-row strong { color:#d4aa40; }
  .action-bar { display:flex; gap:10px; margin:16px 0 24px; padding:16px; background:#181e24; border-radius:10px; border:1px solid #2a3038; align-items:center; }
  .action-bar .status-badge { font-size:12px; font-weight:700; padding:4px 12px; border-radius:12px; }
  .action-bar .status-badge.html { background:#4a9e56; color:#fff; }
  .action-bar .status-badge.spotted { background:#d4aa40; color:#1a1a14; }
  .action-bar .status-badge.research { background:#666; color:#fff; }
  .btn { padding:8px 18px; border-radius:8px; border:none; font-size:13px; font-weight:700; cursor:pointer; transition:all .2s; }
  .btn-publish { background:#2d6a35; color:#fff; }
  .btn-publish:hover { background:#4a9e56; }
  .btn-publish:disabled { background:#333; color:#666; cursor:not-allowed; }
  .btn-preview { background:#2a3038; color:#e8e3d8; }
  .btn-preview:hover { background:#3a4048; }
  .btn-demote { background:#6a3520; color:#fff; }
  .btn-demote:hover { background:#8a4530; }
  .action-msg { font-size:12px; color:#4a9e56; margin-left:auto; }
  .data-section { margin-bottom:16px; background:#181e24; border-radius:10px; overflow:hidden; border:1px solid #2a3038; }
  .data-section-header { padding:10px 16px; background:#235e86; font-size:11px; font-weight:700; letter-spacing:2px; text-transform:uppercase; color:#d4aa40; cursor:pointer; user-select:none; display:flex; justify-content:space-between; }
  .data-section-header .toggle { color:#8ba4b8; }
  .data-section-body { padding:14px 16px; }
  .data-section-body.collapsed { display:none; }
  .data-row { display:flex; gap:8px; margin-bottom:6px; font-size:14px; line-height:1.5; }
  .data-row .label { color:#8ba4b8; min-width:140px; flex-shrink:0; font-size:12px; text-transform:uppercase; letter-spacing:0.5px; padding-top:2px; }
  .data-row .value { color:#e8e3d8; }
  .text-block { font-size:14px; line-height:1.65; color:#b8c8d0; margin-bottom:8px; }
  .tag { background:#2a3038; padding:2px 8px; border-radius:4px; font-size:13px; display:inline-block; margin:2px; }
  .gal-preview { display:flex; gap:6px; flex-wrap:wrap; padding:4px 0; }
  .gal-preview img { width:80px; height:80px; object-fit:cover; border-radius:6px; }
  .toast { position:fixed; bottom:24px; right:24px; background:#235e86; color:#fff; padding:12px 20px; border-radius:8px; font-size:14px; font-weight:600; box-shadow:0 4px 16px rgba(0,0,0,0.4); transform:translateY(80px); opacity:0; transition:all .3s; z-index:100; }
  .toast.show { transform:translateY(0); opacity:1; }
</style>
</head>
<body>
<div class="layout">
  <div class="sidebar">
    <div class="sidebar-header">
      <h1>🦜 Wildlife Publisher</h1>
      <div class="counts" id="counts"></div>
    </div>
    <div class="filter-bar" id="filters"></div>
    <input class="search-box" id="search" placeholder="Search by name, ID, group, or tag…" autocomplete="off">
    <div class="species-list" id="species-list"></div>
  </div>
  <div class="main" id="main">
    <div class="main-empty">Select a species to review</div>
  </div>
</div>
<div class="toast" id="toast"></div>
<script>
let DATA=null, selectedId=null, activeFilters=new Set(['html','spotted','research']);
async function init(){try{const r=await fetch('/api/data');if(!r.ok){document.getElementById('counts').textContent='Error loading data: '+r.status;return;}DATA=await r.json();if(DATA.error){document.getElementById('counts').textContent='Server error: '+DATA.error;return;}renderCounts();renderFilters();renderList();}catch(e){document.getElementById('counts').textContent='Failed to connect: '+e.message;}}
function renderCounts(){const c={html:0,spotted:0,research:0};DATA.species.forEach(s=>c[s.status]=(c[s.status]||0)+1);document.getElementById('counts').textContent=c.html+' html · '+c.spotted+' spotted · '+c.research+' research · '+DATA.species.length+' total';}
function renderFilters(){const bar=document.getElementById('filters');['html','spotted','research'].forEach(st=>{const b=document.createElement('button');b.className='filter-btn active';b.dataset.status=st;b.textContent=st;b.onclick=()=>{activeFilters.has(st)?activeFilters.delete(st):activeFilters.add(st);b.classList.toggle('active');renderList();};bar.appendChild(b);});}
function renderList(){const q=(document.getElementById('search').value||'').toLowerCase();const list=document.getElementById('species-list');list.innerHTML='';DATA.species.filter(s=>{if(!activeFilters.has(s.status))return false;if(q){const hay=(s.common_name+' '+s.scientific_name+' '+s.id+' '+(s.animal_group||'')+' '+(s.tags||[]).join(' ')).toLowerCase();if(!hay.includes(q))return false;}return true;}).forEach(s=>{const d=document.createElement('div');d.className='species-item'+(s.id===selectedId?' selected':'');d.innerHTML='<div class="dot '+s.status+'"></div><div class="info"><div class="name">'+esc(s.common_name)+'</div><div class="sci">'+esc(s.scientific_name)+'</div></div><div class="id-tag">'+s.id+'</div>';d.onclick=()=>{selectedId=s.id;renderList();renderDetail(s.id);};list.appendChild(d);});}
function renderDetail(id){const s=DATA.species.find(x=>x.id===id),hero=DATA.heroes[id]||null,gallery=DATA.galleries[id]||[],hasHero=!!hero;const main=document.getElementById('main');const heroUrl=hasHero?hero.photo_url:'';const heroHtml=hasHero?'<img src="'+esc(heroUrl)+'" alt="'+esc(s.common_name)+'">':'<div class="no-hero">No hero photo</div>';const pj=DATA.wj_lookup[id];
main.innerHTML='<div class="detail"><div class="detail-header"><div class="detail-hero">'+heroHtml+'</div><div class="detail-meta"><h2>'+esc(s.common_name)+'</h2><div class="sci">'+esc(s.scientific_name)+'</div><div class="meta-row"><strong>ID:</strong> '+s.id+'</div><div class="meta-row"><strong>Group:</strong> '+esc(s.animal_group||'')+'</div><div class="meta-row"><strong>Category:</strong> '+esc(s.category||'')+'</div><div class="meta-row"><strong>Gallery photos:</strong> '+gallery.length+'</div><div class="meta-row"><strong>In wildlife.json:</strong> '+(pj?'Yes':'No')+'</div>'+(hasHero?'<div class="meta-row"><strong>Hero:</strong> '+esc(hero.photographer_name||hero.photographer)+' · '+esc(hero.filename||'')+'</div>':'<div class="meta-row" style="color:#c49a20"><strong>⚠ No hero photo</strong></div>')+'</div></div><div class="action-bar"><span class="status-badge '+s.status+'">'+s.status.toUpperCase()+'</span><button class="btn btn-publish" onclick="doPublish(&#39;'+id+'&#39;)" '+(hasHero?'':'disabled title="Needs hero photo"')+'>'+(s.status==='html'?'♻️ Regenerate':'🚀 Publish')+'</button><button class="btn btn-preview" onclick="window.open(&#39;/api/preview?id='+id+'&#39;,&#39;_blank&#39;)">👁 Preview</button>'+(s.status==='html'?'<button class="btn btn-demote" onclick="doDemote(&#39;'+id+'&#39;)">⬇ Demote</button>':'')+'<span class="action-msg" id="action-msg"></span></div>'+buildSections(s,gallery)+'</div>';}
function buildSections(s,gallery){let h='';if(s.quick_hits?.length)h+=ds('Quick Hits',s.quick_hits.map((q,i)=>'<div class="text-block">'+(i+1)+'. '+esc(q)+'</div>').join(''));if(s.identification){let r='';(s.identification.blocks||[]).forEach(b=>{r+='<div class="data-row"><div class="label">'+esc(b.label)+'</div><div class="value">'+esc(b.text)+'</div></div>';});if(s.identification.what_to_look_for)r+='<div class="data-row"><div class="label">Look for</div><div class="value">'+esc(s.identification.what_to_look_for)+'</div></div>';h+=ds('Identification',r);}if(s.diet)h+=ds('Diet','<div class="text-block">'+esc(s.diet)+'</div>');if(s.where_to_look||s.when_to_see)h+=ds('Where & When','<div class="data-row"><div class="label">Where</div><div class="value">'+esc(s.where_to_look||'')+'</div></div><div class="data-row"><div class="label">When</div><div class="value">'+esc(s.when_to_see||'')+'</div></div>');if(s.more_information?.length)h+=ds('More Information',s.more_information.map(p=>'<div class="text-block">'+esc(p)+'</div>').join(''));if(s.interaction)h+=ds('Interaction','<div class="data-row"><div class="label">Level: '+esc(s.interaction.level||'')+'</div><div class="value">'+esc(s.interaction.guidance||'')+'</div></div>');if(gallery.length>1){let g='<div class="gal-preview">';gallery.forEach(p=>{if(p.photo_url)g+='<img src="'+esc(p.photo_url)+'">';});g+='</div>';h+=ds('Gallery ('+gallery.length+' photos)',g);}if(s.tags?.length)h+=ds('Tags','<div>'+s.tags.map(t=>'<span class="tag">'+esc(t)+'</span>').join('')+'</div>');return h;}
function ds(title,body){return '<div class="data-section"><div class="data-section-header" onclick="this.nextElementSibling.classList.toggle(&#39;collapsed&#39;)">'+esc(title)+' <span class="toggle">▾</span></div><div class="data-section-body">'+body+'</div></div>';}
async function doPublish(id){const msg=document.getElementById('action-msg');msg.textContent='Publishing…';msg.style.color='#d4aa40';try{const r=await(await fetch('/api/publish',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({id})})).json();if(r.ok){msg.textContent='✓ Published';msg.style.color='#4a9e56';showToast('Published '+r.filename);DATA=await(await fetch('/api/data')).json();renderCounts();renderList();renderDetail(id);}else{msg.textContent='✗ '+r.error;msg.style.color='#c44';}}catch(e){msg.textContent='✗ Network error';msg.style.color='#c44';}}
async function doDemote(id){if(!confirm('Demote this species to spotted? It will be removed from wildlife.json.'))return;const msg=document.getElementById('action-msg');msg.textContent='Demoting…';msg.style.color='#d4aa40';try{const r=await(await fetch('/api/demote',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({id})})).json();if(r.ok){msg.textContent='✓ Demoted';msg.style.color='#d4aa40';showToast(r.message);DATA=await(await fetch('/api/data')).json();renderCounts();renderList();renderDetail(id);}else{msg.textContent='✗ '+r.error;msg.style.color='#c44';}}catch(e){msg.textContent='✗ Network error';msg.style.color='#c44';}}
function showToast(t){const el=document.getElementById('toast');el.textContent=t;el.classList.add('show');setTimeout(()=>el.classList.remove('show'),3000);}
function esc(s){if(!s)return '';const d=document.createElement('div');d.textContent=s;return d.innerHTML;}
document.getElementById('search').addEventListener('input',renderList);
init();
</script>
</body>
</html>"""


# ── HTTP Server ─────────────────────────────────────────────────────────────

class DashboardHandler(http.server.BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        # Only suppress routine 200 access logs; show errors
        if args and str(args[1]).startswith("2"):
            return
        sys.stderr.write(f"  {fmt % args}\n")

    def _json(self, data, status=200):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", len(body))
        self.end_headers()
        self.wfile.write(body)

    def _html(self, html_str, status=200):
        body = html_str.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", len(body))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path in ("/", ""):
            print("  → Dashboard page requested")
            self._html(DASHBOARD_HTML)
        elif parsed.path == "/api/data":
          try:
            print(f"  → /api/data requested")
            signage = load_signage()
            print(f"  → Loaded {len(signage['species'])} species from wildlife_signage.json")
            credits = load_credits()
            print(f"  → Loaded {len(credits['photos'])} photos from photo_credits.json")
            heroes = build_hero_lookup(credits)
            galleries = build_gallery_lookup(credits)
            wj = load_wildlife_json()
            wj_lookup = {w["id"]: w for w in wj}
            heroes_out = {}
            for pid, hr in heroes.items():
                hc = resolve_hero_credit(hr)
                heroes_out[pid] = {
                    "filename": hr.get("filename") or "",
                    "photo_url": hr.get("photo_url") or "",
                    "photographer_name": hc["credit_name"],
                    "photographer": hr.get("photographer") or "",
                    "license": hc["credit_license"],
                    "credit_line": hc["credit_line"],
                    "focus": hr.get("focus") or "50% 50%",
                }
            galleries_out = {}
            for pid, photos in galleries.items():
                galleries_out[pid] = [{
                    "photo_url": p.get("photo_url") or "",
                    "photographer": p.get("photographer") or "",
                    "license": p.get("license") or "",
                    "hero": p.get("hero", False),
                } for p in photos]
            self._json({
                "species": signage["species"],
                "heroes": heroes_out,
                "galleries": galleries_out,
                "wj_lookup": wj_lookup,
                "meta": signage["meta"],
            })
            print(f"  ✓ Sent {len(signage['species'])} species, {len(heroes_out)} heroes, {len(galleries_out)} galleries")
          except Exception as e:
            import traceback
            traceback.print_exc()
            self._json({"error": str(e)}, 500)
        elif parsed.path == "/api/preview":
            qs = parse_qs(parsed.query)
            pid = qs.get("id", [None])[0]
            if not pid:
                self._html("<h1>Missing id</h1>", 400)
                return
            signage = load_signage()
            credits = load_credits()
            heroes = build_hero_lookup(credits)
            galleries = build_gallery_lookup(credits)
            sp = build_species_lookup(signage).get(pid)
            if not sp:
                self._html(f"<h1>{pid} not found</h1>", 404)
                return
            ok, reason = check_animal_group(sp)
            if not ok:
                self._html(
                    f"<h1>Cannot preview {pid}</h1>"
                    f"<p><strong>Reason:</strong> {reason}</p>"
                    f"<p>Set a valid <code>animal_group</code> value on this "
                    f"species in Edit &amp; Preview, then reload.</p>", 400)
                return
            hero = heroes.get(pid)
            preview = generate_html(sp, hero, galleries.get(pid, []))
            # Replace local hero path with iNat URL for preview
            if hero and hero.get("photo_url"):
                preview = preview.replace(f"../photos/{pid}/{hero['filename']}", hero["photo_url"])
            preview = preview.replace('<link rel="stylesheet" href="../css/site.css">', '')
            preview = preview.replace('<div id="nav-placeholder"></div>', '')
            preview = preview.replace('<div id="footer-placeholder"></div>', '')
            preview = preview.replace('<script src="../js/site.js"></script>', '')
            preview = re.sub(r"<script>if \(typeof injectShared.*?</script>", "", preview)
            self._html(preview)
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        if self.path == "/api/publish":
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length))
            pid = body.get("id")
            if not pid:
                self._json({"ok": False, "error": "Missing id"}, 400)
                return
            try:
                signage = load_signage()
                credits = load_credits()
                heroes = build_hero_lookup(credits)
                galleries = build_gallery_lookup(credits)
                sp = build_species_lookup(signage).get(pid)
                if not sp:
                    self._json({"ok": False, "error": f"{pid} not found"}, 404)
                    return
                ok, reason = check_animal_group(sp)
                if not ok:
                    self._json({"ok": False, "error": f"{pid}: {reason}"}, 400)
                    return
                hero = heroes.get(pid)
                if not hero:
                    self._json({"ok": False, "error": f"No hero photo for {pid}"}, 400)
                    return
                path, _ = write_html(sp, hero, galleries.get(pid, []))
                update_wildlife_json(sp, hero)
                if sp["status"] != "html":
                    update_signage_status(pid, "html")
                self._json({"ok": True, "filename": path.name})
                print(f"  ✓ Published {pid} → {path.name}")
            except Exception as e:
                self._json({"ok": False, "error": str(e)}, 500)

        elif self.path == "/api/demote":
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length))
            pid = body.get("id")
            if not pid:
                self._json({"ok": False, "error": "Missing id"}, 400)
                return
            try:
                signage = load_signage()
                sp = build_species_lookup(signage).get(pid)
                if not sp:
                    self._json({"ok": False, "error": f"{pid} not found"}, 404)
                    return
                if sp["status"] != "html":
                    self._json({"ok": False, "error": f"{pid} is already {sp['status']}"}, 400)
                    return
                update_signage_status(pid, "spotted")
                entries = load_wildlife_json()
                entries = [e for e in entries if e["id"] != pid]
                entries.sort(key=lambda e: e["id"])
                write_json_atomic(WILDLIFE_JSON, entries)
                # Clean up orphan HTML file(s)
                deleted = delete_species_page("wildlife", pid)
                for fname in deleted:
                    print(f"  🗑 Deleted {fname}")
                self._json({"ok": True, "message": f"{sp['common_name']} demoted to spotted"})
                print(f"  ⬇ Demoted {pid} {sp['common_name']} → spotted")
            except Exception as e:
                self._json({"ok": False, "error": str(e)}, 500)

        else:
            self.send_response(404)
            self.end_headers()


# ── CLI ─────────────────────────────────────────────────────────────────────

def cmd_dashboard():
    import webbrowser
    print(f"\n  🦜 PSBP Wildlife Publisher")
    print(f"  Dashboard: http://localhost:{PORT}")
    print(f"  Press Ctrl+C to stop\n")
    server = http.server.HTTPServer(("127.0.0.1", PORT), DashboardHandler)
    webbrowser.open(f"http://localhost:{PORT}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n  Stopped.")
        server.server_close()


def cmd_generate_all():
    signage = load_signage()
    credits = load_credits()
    heroes = build_hero_lookup(credits)
    galleries = build_gallery_lookup(credits)
    fresh = []
    count = skipped = 0
    for sp in signage["species"]:
        if sp["status"] != "html":
            continue
        ok, reason = check_animal_group(sp)
        if not ok:
            print(f"  ✗ {sp['id']} {sp['common_name']}: {reason} — refusing to publish")
            skipped += 1
            continue
        hero = heroes.get(sp["id"])
        if not hero:
            print(f"  ⚠ {sp['id']} {sp['common_name']}: no hero, skipping")
            skipped += 1
            continue
        write_html(sp, hero, galleries.get(sp["id"], []))
        fresh.append(build_wildlife_json_entry(sp, hero))
        count += 1
    fresh.sort(key=lambda e: e["id"])
    write_json_atomic(WILDLIFE_JSON, fresh)
    print(f"\n  ✓ Generated {count} HTML files, skipped {skipped}")
    print(f"  ✓ wildlife.json rebuilt with {count} entries (html-only)")


def cmd_generate_one(pid):
    signage = load_signage()
    credits = load_credits()
    heroes = build_hero_lookup(credits)
    galleries = build_gallery_lookup(credits)
    sp = build_species_lookup(signage).get(pid)
    if not sp:
        print(f"  ✗ {pid} not found"); sys.exit(1)
    ok, reason = check_animal_group(sp)
    if not ok:
        print(f"  ✗ {pid}: {reason} — refusing to publish"); sys.exit(1)
    hero = heroes.get(pid)
    if not hero:
        print(f"  ⚠ No hero for {pid} — generating with placeholder path")
    path, _ = write_html(sp, hero, galleries.get(pid, []))
    update_wildlife_json(sp, hero)
    print(f"  ✓ {path}")
    print(f"  ✓ wildlife.json updated for {pid}")


def cmd_clean():
    signage = load_signage()
    sp_lookup = build_species_lookup(signage)
    entries = load_wildlife_json()
    before = len(entries)
    kept, removed = [], []
    for e in entries:
        spec = sp_lookup.get(e["id"])
        if spec and spec["status"] == "html":
            kept.append(e)
        else:
            removed.append((e["id"], e["common"], spec["status"] if spec else "NOT IN SIGNAGE"))
    if not removed:
        print("  ✓ wildlife.json is already clean"); return
    print(f"  Removing {len(removed)} non-html entries:\n")
    for pid, name, st in removed:
        print(f"    {pid} {name} (status={st})")
    kept.sort(key=lambda e: e["id"])
    write_json_atomic(WILDLIFE_JSON, kept)
    print(f"\n  ✓ wildlife.json: {before} → {len(kept)} entries")


def cmd_validate():
    signage = load_signage()
    credits = load_credits()
    heroes = build_hero_lookup(credits)
    sp_lookup = build_species_lookup(signage)
    issues = []
    if WILDLIFE_DIR.exists():
        for f in sorted(WILDLIFE_DIR.glob("PSBP-*.html")):
            m = re.match(r"(PSBP-\d{5})", f.name)
            if not m: continue
            pid = m.group(1)
            content = f.read_text(encoding="utf-8")
            hero = heroes.get(pid)
            if hero:
                expected = f"../photos/{pid}/{hero['filename']}"
                if expected not in content:
                    issues.append(("HERO_PATH", f"{pid}: expected {expected}"))
            if pid not in sp_lookup:
                issues.append(("NO_SIGNAGE", f"{pid}: HTML exists but not in wildlife_signage.json"))
    for sid, spec in sp_lookup.items():
        if spec["status"] == "html":
            ef = WILDLIFE_DIR / page_filename(sid, spec["common_name"])
            if not ef.exists():
                issues.append(("NO_HTML", f"{sid} {spec['common_name']}: status=html but no file"))
        # Flag animal_group problems on anything that could be published
        # (published now, or ready-to-publish spotted). Research entries are
        # allowed to be incomplete.
        if spec.get("status") in ("html", "spotted"):
            ok, reason = check_animal_group(spec)
            if not ok:
                issues.append(("ANIMAL_GROUP", f"{sid} {spec.get('common_name','')}: {reason}"))
    if not issues:
        print("✓ All validated.")
    else:
        print(f"Found {len(issues)} issue(s):\n")
        for tag, msg in issues:
            print(f"  [{tag}] {msg}")


def main():
    if len(sys.argv) < 2:
        cmd_dashboard()
    elif sys.argv[1] == "--validate":
        cmd_validate()
    elif sys.argv[1] == "--generate-all":
        cmd_generate_all()
    elif sys.argv[1] == "--generate" and len(sys.argv) >= 3:
        cmd_generate_one(sys.argv[2])
    elif sys.argv[1] == "--clean":
        cmd_clean()
    elif sys.argv[1] == "--demote" and len(sys.argv) >= 3:
        cmd_demote(sys.argv[2])
    else:
        print(__doc__); sys.exit(1)


def cmd_demote(pid):
    """Demote a species from html → spotted."""
    signage = load_signage()
    sp = build_species_lookup(signage).get(pid)
    if not sp:
        print(f"  ✗ {pid} not found"); sys.exit(1)
    if sp["status"] != "html":
        print(f"  ✗ {pid} {sp['common_name']} is already status={sp['status']}"); sys.exit(1)

    update_signage_status(pid, "spotted")

    entries = load_wildlife_json()
    before = len(entries)
    entries = [e for e in entries if e["id"] != pid]
    if len(entries) < before:
        entries.sort(key=lambda e: e["id"])
        write_json_atomic(WILDLIFE_JSON, entries)
        print(f"  ✓ Removed from wildlife.json ({before} → {len(entries)})")

    # Clean up orphan HTML file(s)
    deleted = delete_species_page("wildlife", pid)
    for fname in deleted:
        print(f"  🗑 Deleted {fname}")

    print(f"  ✓ {pid} {sp['common_name']} demoted to spotted")

if __name__ == "__main__":
    main()
