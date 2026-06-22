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

# ── Repo paths ──────────────────────────────────────────────────────────────
REPO = Path("/Users/fiona/Documents/GitHub/explore")
DATA = REPO / "data" / "sources"
SIGNAGE_JSON = DATA / "plant_signage.json"
CREDITS_JSON = DATA / "photo_credits.json"
PLANTS_JSON  = REPO / "plants.json"
PLANTS_DIR   = REPO / "plants"
PORT = 8701

# ── Data loading ────────────────────────────────────────────────────────────

def load_signage():
    with open(SIGNAGE_JSON) as f:
        return json.load(f)

def load_credits():
    with open(CREDITS_JSON) as f:
        return json.load(f)

def load_plants_json():
    if PLANTS_JSON.exists():
        with open(PLANTS_JSON) as f:
            return json.load(f)
    return []

def build_hero_lookup(credits):
    """Map psbp_id → hero photo record for Plant type."""
    heroes = {}
    for p in credits["photos"]:
        if p.get("type") == "Plant" and p.get("hero"):
            heroes[p["psbp_id"]] = p
    return heroes

def build_species_lookup(signage):
    return {s["id"]: s for s in signage["species"]}

# ── Slug helper ─────────────────────────────────────────────────────────────

def slugify(name):
    """Buccaneer Palm → Buccaneer-Palm"""
    return re.sub(r"[^A-Za-z0-9-]", "", name.replace(" ", "-").replace("'", ""))

def page_filename(psbp_id, common_name):
    return f"{psbp_id}-{slugify(common_name)}.html"

# ── plants.json entry builder ───────────────────────────────────────────────

def build_plants_json_entry(species, hero):
    """Build one plants.json card entry from signage + hero photo."""
    pid = species["id"]
    cat = species.get("category", "").replace(" and ", " & ")
    tox_level = (species.get("toxicity") or {}).get("level", "Green")
    ed_level  = (species.get("edibility") or {}).get("level", "Green")
    inv_level = (species.get("invasive") or {}).get("level", "Green")

    toxic  = tox_level in ("Red", "Yellow") or ed_level in ("Red", "Yellow")
    edible = ed_level == "Green" and "edible" in ((species.get("edibility") or {}).get("detail", "")).lower() if ed_level == "Green" else False
    # Simpler: edible = True only if edibility.level is Green AND detail doesn't say "not edible"
    ed_detail = ((species.get("edibility") or {}).get("detail", "")).lower()
    edible = ed_level == "Green" and "not edible" not in ed_detail and "no plant part is edible" not in ed_detail

    invasive  = inv_level in ("Red", "Yellow")
    wetland   = "wetland" in cat.lower()

    # Butterfly: check wildlife_value text for butterfly mentions
    wv = species.get("wildlife_value") or []
    wv_text = " ".join(wv) if isinstance(wv, list) else str(wv)
    butterfly = "butterfl" in wv_text.lower()

    # Quick hit: first item
    quick_hits = species.get("quick_hits") or []
    quick = quick_hits[0] if quick_hits else ""

    # Hero photo path and credit
    if hero:
        photo = f"photos/{pid}/{hero['filename']}"
        credit = hero.get("photographer", "")
        focus = hero.get("focus", "50% 50%")
    else:
        photo = f"photos/{pid}-{slugify(species['common_name'])}.jpg"
        credit = ""
        focus = "50% 50%"

    return {
        "id": pid,
        "common": species["common_name"],
        "sci": species["botanical_name"],
        "family": (species.get("taxonomy") or {}).get("family", ""),
        "aliases": species.get("alternate_names") or [],
        "cat": cat,
        "origin": "Native" if species.get("native") else "Non-native",
        "native": bool(species.get("native")),
        "butterfly": butterfly,
        "toxic": toxic,
        "edible": edible,
        "invasive": invasive,
        "wetland": wetland,
        "photo": photo,
        "page": f"plants/{page_filename(pid, species['common_name'])}",
        "quick": quick,
        "credit": credit,
        "focus": focus,
    }

# ── HTML page generator ────────────────────────────────────────────────────

# The CSS block is identical across all plant pages (extracted from live specimens).
PLANT_CSS = """\
  /* Plant page layout */
  .plant-wrap { max-width:680px;margin:2rem auto;background:#e8e3d8;min-height:80vh;border-radius:12px;overflow:hidden;box-shadow:0 4px 32px rgba(26,46,26,0.13); }
  @media (max-width:480px){ .plant-wrap{margin:0;border-radius:0;box-shadow:none;min-height:100vh;} }

  /* Hero -- tap the photo to open the full image in /photos/ */
  .plant-hero { position:relative;height:310px;overflow:hidden; }
  .plant-hero-link { display:block;width:100%;height:100%; }
  .plant-hero img { width:100%;height:100%;object-fit:cover;object-position:FOCUS_PLACEHOLDER;display:block; }
  .plant-hero-overlay { position:absolute;bottom:0;left:0;right:0;background:linear-gradient(transparent 0%, rgba(10,25,10,0.70) 40%, rgba(10,25,10,0.92) 100%);padding:60px 18px 0;pointer-events:none; }
  .plant-hero-category { font-size:11px;font-weight:700;letter-spacing:3px;text-transform:uppercase;color:var(--gold-light,#d4aa40);margin-bottom:5px; }
  .plant-hero-name { font-family:'Playfair Display',Georgia,serif;font-size:38px;font-weight:700;color:#fff;line-height:1.05;margin-bottom:12px; }

  /* Scientific band */
  .plant-sci-band { background:var(--moss,#2d4a2d);padding:11px 18px;display:flex;align-items:center;gap:10px;flex-wrap:wrap;border-bottom:2px solid var(--gold,#b8942a); }
  .plant-sci-name { font-family:'Playfair Display',Georgia,serif;font-style:italic;font-size:19px;color:#fff;flex:1;text-shadow:0 1px 3px rgba(0,0,0,0.3); }
  .plant-family-tag { font-size:12px;font-weight:700;letter-spacing:1.5px;text-transform:uppercase;color:var(--forest,#1a2e1a);background:var(--gold-light,#d4aa40);padding:5px 12px;border-radius:4px;text-decoration:none;transition:background .2s; }
  .plant-family-tag:hover { background:#c49a20; }

  /* Photo credit line */
  .plant-credit { font-size:12.5px;color:#5b6b73;font-style:italic;padding:7px 16px;background:var(--cream,#f5f0e8);border-bottom:1px solid rgba(90,122,74,0.12);text-align:right; }
  .plant-credit strong { font-style:normal;color:var(--moss,#2d4a2d); }

  .plant-content { padding:12px 0 56px; }

  /* Status badges */
  .plant-status-row { display:flex;gap:7px;padding:12px 14px;flex-wrap:wrap;background:var(--cream,#f5f0e8);border-bottom:1px solid rgba(90,122,74,0.15); }
  .badge { font-size:12px;font-weight:700;padding:5px 13px;border-radius:20px;letter-spacing:0.3px; }
  .badge-neutral { background:#ece3d2;color:#4a3c22;border:1.5px solid rgba(120,100,60,0.32); }
  .badge-green { background:#e7f1dc;color:#356017;border:1.5px solid rgba(74,124,30,0.35); }
  .badge-native { background:#dde9d6;color:#1f4d28;border:1.5px solid rgba(45,106,53,0.38); }
  .badge-safe { background:#e7f1dc;color:#356017;border:1.5px solid rgba(74,124,30,0.35); }
  .badge-warn { background:#f4e7c6;color:#6f5210;border:1.5px solid rgba(197,146,42,0.45); }
  .badge-danger { background:#ecdacf;color:#8a3a22;border:1.5px solid rgba(138,58,34,0.35); }

  /* Cards / sections */
  .plant-section { margin:12px 12px 0;background:#fff;border-radius:10px;overflow:hidden;box-shadow:0 2px 8px rgba(26,46,26,0.10);border:1px solid rgba(90,122,74,0.1); }
  .plant-section-header { background:var(--moss,#2d4a2d);padding:12px 16px;display:flex;align-items:center;gap:10px; }
  .plant-section-icon { font-size:18px;line-height:1; }
  .plant-section-title { font-size:12px;font-weight:700;letter-spacing:2.5px;text-transform:uppercase;color:#fff; }
  .plant-section-body { padding:16px; }
  .plant-section-body p { font-size:17px;line-height:1.7;color:var(--text-mid,#2e2e1e); }
  .plant-section-body p + p { margin-top:10px; }

  /* Quick hits */
  .quick-hits-list { list-style:none;padding:6px 16px 10px; }
  .quick-hits-list li { font-size:17px;line-height:1.65;color:var(--text-mid,#2e2e1e);padding:13px 0 13px 22px;position:relative;border-bottom:1px solid rgba(90,122,74,0.12); }
  .quick-hits-list li:last-child { border-bottom:none; }
  .quick-hits-list li::before { content:'';position:absolute;left:0;top:21px;width:8px;height:8px;background:var(--gold,#b8942a);border-radius:50%; }

  /* More info (dark card) */
  .plant-more-info { margin:12px 12px 0;background:var(--forest,#1a2e1a);border-radius:10px;overflow:hidden;box-shadow:0 3px 12px rgba(26,46,26,0.25); }
  .plant-more-info .plant-section-header { background:rgba(255,255,255,0.07);border-bottom:2px solid var(--gold,#b8942a); }
  .plant-more-info .plant-section-title { color:var(--gold-light,#d4aa40); }
  .plant-more-info .plant-section-body p { color:rgba(245,240,232,0.92);font-size:17px;line-height:1.7; }
  .plant-more-info .plant-section-body em { color:#e9e3d6; }
  .more-info-list { list-style:none;padding:0; }
  .more-info-list li { font-size:17px;line-height:1.7;color:rgba(245,240,232,0.92);padding:14px 18px;border-bottom:1px solid rgba(255,255,255,0.08); }
  .more-info-list li:last-child { border-bottom:none; }

  /* Toxicity / safety cards */
  .plant-toxic-section { margin:12px 12px 0;background:var(--danger-light,#fff0f0);border-radius:10px;overflow:hidden;box-shadow:0 2px 8px rgba(26,46,26,0.10);border:1.5px solid rgba(139,32,32,0.25); }
  .plant-toxic-section .plant-section-header { background:var(--danger,#8b2020); }
  .plant-toxic-section .plant-section-body p { font-size:17px;line-height:1.7;color:#4a0a0a;font-weight:500; }
  .plant-safe-section { margin:12px 12px 0;background:var(--safe-light,#edf7ed);border-radius:10px;overflow:hidden;box-shadow:0 2px 8px rgba(26,46,26,0.10);border:1.5px solid rgba(26,92,26,0.2); }
  .plant-safe-section .plant-section-header { background:var(--safe-dark,#1a5c1a); }
  .plant-safe-section .plant-section-body p { font-size:17px;line-height:1.7;color:#0a2a0a; }
  .plant-caution-section { margin:12px 12px 0;background:#fffbf0;border-radius:10px;overflow:hidden;box-shadow:0 2px 8px rgba(26,46,26,0.10);border:1.5px solid rgba(180,120,0,0.25); }
  .plant-caution-section .plant-section-header { background:#7a5000; }
  .plant-caution-section .plant-section-body p { font-size:17px;line-height:1.7;color:#3a2000; }

  /* Data grid */
  .data-grid { display:grid;grid-template-columns:1fr 1fr;gap:10px;padding:14px; }
  .data-item { background:var(--parchment,#e8dfc8);border-radius:8px;padding:11px 13px;border:1px solid rgba(90,122,74,0.15); }
  .data-label { font-size:11px;font-weight:700;letter-spacing:1.5px;text-transform:uppercase;color:var(--sage,#4a6a3a);margin-bottom:4px; }
  .data-value { font-size:16px;color:var(--text-dark,#1a1a14);font-weight:600;line-height:1.4; }
  .data-item.full-width { grid-column:1/-1; }

  /* Reproduction list */
  .repro-list { padding:14px 16px; }
  .repro-item { padding:10px 0;border-bottom:1px solid rgba(90,122,74,0.1); }
  .repro-item:last-child { border-bottom:none;padding-bottom:0; }
  .repro-label { font-size:13px;font-weight:700;letter-spacing:1px;text-transform:uppercase;color:var(--sage,#4a6a3a);margin-bottom:4px; }
  .repro-item p { font-size:16px;line-height:1.65;color:var(--text-mid,#2e2e1e); }

  /* Also known as */
  .alias-list { display:flex;flex-wrap:wrap;gap:8px;padding:14px 16px; }
  .alias-tag { background:var(--parchment,#e8dfc8);border:1.5px solid rgba(90,122,74,0.25);border-radius:6px;padding:6px 14px;font-size:15px;color:var(--text-mid,#2e2e1e);font-style:italic;font-weight:500; }

  /* Back to plants link */
  .all-plants-link { margin:10px 12px 0;display:flex;align-items:center;justify-content:center;gap:8px;background:var(--parchment,#e8dfc8);border-radius:10px;padding:14px 18px;text-decoration:none;border:1.5px solid rgba(90,122,74,0.25);color:var(--moss,#2d4a2d);font-weight:700;font-size:15px;transition:background 0.4s ease; }
  .all-plants-link:hover { background:var(--cream,#f5f0e8); }

  /* Fade-in animation */
  .plant-section,.plant-more-info,.plant-toxic-section,.plant-safe-section,.plant-caution-section,.all-plants-link { animation:plantFadeUp 0.6s ease both; }
  .plant-section:nth-child(1){animation-delay:.05s} .plant-section:nth-child(2){animation-delay:.10s}
  .plant-section:nth-child(3){animation-delay:.15s} .plant-section:nth-child(4){animation-delay:.20s}
  .plant-section:nth-child(5){animation-delay:.25s} .plant-section:nth-child(6){animation-delay:.30s}
  .plant-section:nth-child(7){animation-delay:.35s} .plant-more-info{animation-delay:.22s}
  .plant-safe-section,.plant-toxic-section,.plant-caution-section{animation-delay:.38s} .all-plants-link{animation-delay:.42s}
  @keyframes plantFadeUp { from{opacity:0;transform:translateY(10px)} to{opacity:1;transform:translateY(0)} }
  @media (prefers-reduced-motion:reduce){ .plant-section,.plant-more-info,.plant-toxic-section,.plant-safe-section,.plant-caution-section,.all-plants-link{animation:none} }

  /* Floating back button */
  .plant-float-back { position:fixed;bottom:24px;left:50%;transform:translateX(-50%);background:var(--moss,#2d4a2d);color:#fff;font-size:15px;font-weight:700;padding:10px 22px;border-radius:30px;text-decoration:none;box-shadow:0 4px 16px rgba(0,0,0,0.25);z-index:800;display:flex;align-items:center;gap:8px;transition:background .2s,transform .2s;white-space:nowrap; }
  .plant-float-back:hover { background:var(--forest,#1a2e1a);transform:translateX(-50%) translateY(-2px);text-decoration:none;color:#fff; }
  @media (min-width:481px){ .plant-float-back{bottom:32px} }"""


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
    """Render the status badge row."""
    badges = []
    if species.get("native"):
        badges.append('<span class="badge badge-native">🌿 Florida Native</span>')
    else:
        badges.append('<span class="badge badge-neutral">🌍 Non-Native</span>')

    inv = (species.get("invasive") or {}).get("level", "Green")
    if inv == "Green":
        badges.append('<span class="badge badge-green">✅ Not Invasive</span>')
    elif inv == "Yellow":
        badges.append('<span class="badge badge-warn">⚠️ Watch List</span>')
    else:
        badges.append('<span class="badge badge-danger">⚠️ Invasive</span>')

    tox = (species.get("toxicity") or {}).get("level", "Green")
    ed  = (species.get("edibility") or {}).get("level", "Green")
    worst = "Red" if "Red" in (tox, ed) else "Yellow" if "Yellow" in (tox, ed) else "Green"
    if worst == "Green":
        badges.append('<span class="badge badge-green">✅ Safe</span>')
    elif worst == "Yellow":
        badges.append('<span class="badge badge-warn">⚠️ Mild Caution</span>')
    else:
        badges.append('<span class="badge badge-danger">☠️ Toxic</span>')

    return "\n    ".join(badges)


def render_quick_hits(species):
    items = species.get("quick_hits") or []
    if not items:
        return ""
    lines = []
    for item in items:
        lines.append(f"    <li>{item}</li>")
    return f"""  <div class="plant-section">
    <div class="plant-section-header"><span class="plant-section-icon">⚡</span><span class="plant-section-title">Quick Hits</span></div>
    <ul class="quick-hits-list">
{chr(10).join(lines)}
    </ul>
  </div>"""


def render_origin(species):
    origin = species.get("origin")
    if not origin:
        return ""
    return f"""  <div class="plant-section">
    <div class="plant-section-header"><span class="plant-section-icon">🌍</span><span class="plant-section-title">Origin</span></div>
    <div class="plant-section-body"><p>{h(origin)}</p></div>
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
    paras = "".join(f"<p>{h(item)}</p>" for item in items)
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
        parts.append(f'<div class="repro-item"><div class="repro-label">{h(b["label"])}</div><p>{h(b["text"])}</p></div>')
    if wtlf:
        parts.append(f'<div class="repro-item"><div class="repro-label">What to Look For</div><p>{h(wtlf)}</p></div>')
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

    worst = "Red" if "Red" in (tox_level, ed_level) else "Yellow" if "Yellow" in (tox_level, ed_level) else "Green"

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

    # Build paragraphs
    paras = []
    if ed.get("detail"):
        paras.append(f"<p>{h(ed['detail'])}</p>")
    tox_parts = []
    if tox.get("people"):
        tox_parts.append(tox["people"])
    dogs = tox.get("dogs")
    if dogs:
        tox_parts.append(dogs)
    if tox_parts:
        paras.append(f"<p>{h(' '.join(tox_parts))}</p>")

    if not paras:
        return ""

    return f"""  <div class="{section_cls}">
    <div class="plant-section-header"><span class="plant-section-icon">{icon}</span><span class="plant-section-title">Edibility &amp; Toxicity</span></div>
    <div class="plant-section-body">{"".join(paras)}</div>
  </div>"""


def render_notes(species):
    notes = species.get("other_notes")
    if not notes:
        return ""
    return f"""  <div class="plant-section">
    <div class="plant-section-header"><span class="plant-section-icon">📝</span><span class="plant-section-title">Notes</span></div>
    <div class="plant-section-body"><p>{h(notes)}</p></div>
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


def generate_html(species, hero):
    """Generate the complete HTML page for a species."""
    pid = species["id"]
    common = species["common_name"]
    sci = species["botanical_name"]
    family = (species.get("taxonomy") or {}).get("family", "")
    cat_display = h(species.get("category", "").replace(" and ", " &amp; "))
    # Undo double-escape: h() escapes &amp; to &amp;amp;
    cat_display = species.get("category", "").replace(" and ", " & ")
    cat_html = h(cat_display)  # This properly escapes & to &amp;

    focus = hero.get("focus", "50% 50%") if hero else "50% 50%"
    css = PLANT_CSS.replace("FOCUS_PLACEHOLDER", focus)

    # Hero image path (relative from plants/ directory)
    if hero:
        hero_path = f"../photos/{pid}/{hero['filename']}"
    else:
        hero_path = f"../photos/{pid}-{slugify(common)}.jpg"

    # Credit line
    if hero:
        photog_name = hero.get("photographer_name", hero.get("photographer", "Unknown"))
        license_str = hero.get("license", "")
        credit_html = f'📷 Photo by <strong>{h(photog_name)}</strong> · {h(license_str)} · via iNaturalist'
    else:
        credit_html = "📷 Photo credit pending"

    # Build all sections
    sections = []
    sections.append(render_quick_hits(species))
    sections.append(render_origin(species))
    sections.append(render_more_info(species))
    sections.append(render_wildlife(species))
    sections.append(render_reproduction(species))
    sections.append(render_size_and_growing(species))
    sections.append(render_safety(species))
    sections.append(render_notes(species))
    sections.append(render_aliases(species))
    content = "\n".join(s for s in sections if s)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{h(common)} · Palma Sola Botanical Park</title>
<link rel="stylesheet" href="../css/site.css">
<style>
{css}
</style>
</head>
<body>
<div id="nav-placeholder"></div>

<div class="plant-wrap">
<div class="plant-hero">
  <a class="plant-hero-link" href="{hero_path}" target="_blank" rel="noopener">
    <img src="{hero_path}" alt="{h(common)} at Palma Sola Botanical Park" loading="lazy">
  </a>
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
</html>"""


# ── File writers ────────────────────────────────────────────────────────────

def write_html(species, hero, dry_run=False):
    """Write HTML file to plants/ directory. Returns the path written."""
    html_content = generate_html(species, hero)
    filename = page_filename(species["id"], species["common_name"])
    path = PLANTS_DIR / filename
    if dry_run:
        return path, html_content
    PLANTS_DIR.mkdir(parents=True, exist_ok=True)
    # Atomic write: write to temp then rename
    tmp = path.with_suffix(".tmp")
    tmp.write_text(html_content, encoding="utf-8")
    tmp.rename(path)
    return path, html_content


def update_plants_json(species, hero):
    """Add or update a species entry in plants.json. Preserves sort order by ID."""
    entries = load_plants_json()
    entry = build_plants_json_entry(species, hero)
    # Replace existing or append
    found = False
    for i, e in enumerate(entries):
        if e["id"] == entry["id"]:
            entries[i] = entry
            found = True
            break
    if not found:
        entries.append(entry)
    # Sort by ID
    entries.sort(key=lambda e: e["id"])
    # Atomic write
    tmp = PLANTS_JSON.with_suffix(".tmp")
    tmp.write_text(json.dumps(entries, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.rename(PLANTS_JSON)
    return entry


def update_signage_status(species_id, new_status):
    """Update the status field in plant_signage.json for a species."""
    signage = load_signage()
    for s in signage["species"]:
        if s["id"] == species_id:
            s["status"] = new_status
            break
    tmp = SIGNAGE_JSON.with_suffix(".tmp")
    tmp.write_text(json.dumps(signage, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.rename(SIGNAGE_JSON)


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
    html += dataSection('Origin', `<div class="text-block">${esc(s.origin)}</div>`);
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

  // Safety
  let safetyHtml = '';
  if (s.edibility) {
    safetyHtml += `<div class="data-row"><div class="label">Edibility (${s.edibility.level})</div><div class="value">${esc(s.edibility.detail || '')}</div></div>`;
  }
  if (s.toxicity) {
    safetyHtml += `<div class="data-row"><div class="label">Toxicity (${s.toxicity.level})</div><div class="value">${esc(s.toxicity.people || '')}</div></div>`;
    if (s.toxicity.dogs) {
      safetyHtml += `<div class="data-row"><div class="label">Dogs (${s.toxicity.dogs_level})</div><div class="value">${esc(s.toxicity.dogs)}</div></div>`;
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
                heroes_out[pid] = {
                    "filename": hr["filename"],
                    "photo_url": hr.get("photo_url", ""),
                    "photographer_name": hr.get("photographer_name", ""),
                    "photographer": hr.get("photographer", ""),
                    "license": hr.get("license", ""),
                    "focus": hr.get("focus", "50% 50%"),
                    "credit_line": hr.get("credit_line", ""),
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
            species_lookup = build_species_lookup(signage)
            species = species_lookup.get(pid)
            if not species:
                self._html_response(f"<h1>Species {pid} not found</h1>", 404)
                return
            hero = heroes.get(pid)
            # Generate preview with absolute image URLs (iNat) for browser viewing
            preview_html = generate_html(species, hero)
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
                path, _ = write_html(species, hero)

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
    print(f"\n  Summary: {len(issues)} HTML issues, {pj_issues} plants.json issues")


def cmd_generate_all():
    signage = load_signage()
    credits = load_credits()
    heroes = build_hero_lookup(credits)

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
        path, _ = write_html(species, hero)
        fresh_entries.append(build_plants_json_entry(species, hero))
        count += 1

    # Atomic write of the complete, clean plants.json
    fresh_entries.sort(key=lambda e: e["id"])
    tmp = PLANTS_JSON.with_suffix(".tmp")
    tmp.write_text(json.dumps(fresh_entries, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.rename(PLANTS_JSON)

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
    tmp = PLANTS_JSON.with_suffix(".tmp")
    tmp.write_text(json.dumps(kept, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.rename(PLANTS_JSON)

    print(f"\n  ✓ plants.json: {before} → {len(kept)} entries")


def cmd_generate_one(pid):
    signage = load_signage()
    credits = load_credits()
    heroes = build_hero_lookup(credits)
    species_lookup = build_species_lookup(signage)

    species = species_lookup.get(pid)
    if not species:
        print(f"  ✗ Species {pid} not found in plant_signage.json")
        sys.exit(1)

    hero = heroes.get(pid)
    if not hero:
        print(f"  ⚠ No hero photo for {pid} — generating with placeholder path")

    path, _ = write_html(species, hero)
    entry = update_plants_json(species, hero)
    print(f"  ✓ {path}")
    print(f"  ✓ plants.json updated for {pid}")


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
    else:
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    main()
