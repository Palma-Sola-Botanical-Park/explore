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

# ── Repo paths ──────────────────────────────────────────────────────────────
REPO = Path("/Users/fiona/Documents/GitHub/explore")
DATA = REPO / "data" / "sources"
SIGNAGE_JSON  = DATA / "wildlife_signage.json"
CREDITS_JSON  = DATA / "photo_credits.json"
WILDLIFE_JSON = REPO / "wildlife.json"
WILDLIFE_DIR  = REPO / "wildlife"
PORT = 8702

# ── Theme mapping ───────────────────────────────────────────────────────────
THEME_MAP = {
    "Bird": "bird",
    "Butterfly": "butterfly", "Moth": "butterfly",
    "Lizard": "reptile", "Turtle": "reptile",
    "Mammal": "mammal",
    # Invertebrates get the amphibian/green palette
    "Beetle": "amphibian", "Crustacean": "amphibian",
    "Dragonfly": "amphibian", "Grasshopper": "amphibian",
    "True Bug": "amphibian", "Spider": "amphibian",
    "Frog": "amphibian", "Toad": "amphibian",
}

def theme_for(animal_group):
    return THEME_MAP.get(animal_group, "amphibian")

# ── Data loading ────────────────────────────────────────────────────────────

def load_signage():
    with open(SIGNAGE_JSON) as f:
        return json.load(f)

def load_credits():
    with open(CREDITS_JSON) as f:
        return json.load(f)

def load_wildlife_json():
    if WILDLIFE_JSON.exists():
        with open(WILDLIFE_JSON) as f:
            return json.load(f)
    return []

def build_hero_lookup(credits):
    heroes = {}
    for p in credits["photos"]:
        if p.get("type") == "Wildlife" and p.get("hero"):
            heroes[p["psbp_id"]] = p
    return heroes

def build_gallery_lookup(credits):
    """Map psbp_id → list of gallery photos (hero first, then others)."""
    galleries = {}
    for p in credits["photos"]:
        if p.get("type") != "Wildlife":
            continue
        if "gallery" not in (p.get("role") or []):
            continue
        pid = p["psbp_id"]
        if pid not in galleries:
            galleries[pid] = []
        galleries[pid].append(p)
    # Sort: hero first, then by photo_id
    for pid in galleries:
        galleries[pid].sort(key=lambda p: (not p.get("hero", False), p.get("photo_id", "")))
    return galleries

def build_species_lookup(signage):
    return {s["id"]: s for s in signage["species"]}

# ── Slug helper ─────────────────────────────────────────────────────────────

def slugify(name):
    return re.sub(r"[^A-Za-z0-9-]", "", name.replace(" ", "-").replace("'", ""))

def page_filename(psbp_id, common_name):
    return f"{psbp_id}-{slugify(common_name)}.html"

# ── wildlife.json entry builder ─────────────────────────────────────────────

def build_wildlife_json_entry(species, hero):
    pid = species["id"]
    theme = theme_for(species.get("animal_group", ""))
    quick_hits = species.get("quick_hits") or []

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
        "sci": species["scientific_name"],
        "family": (species.get("taxonomy") or {}).get("family", ""),
        "theme": theme,
        "category": species.get("category", ""),
        "native": bool(species.get("native")),
        "quick": quick_hits[0] if quick_hits else "",
        "aliases": species.get("also_known_as") or [],
        "tags": species.get("tags") or [],
        "credit": credit,
        "photo": photo,
        "focus": focus or "50% 50%",
        "page": f"wildlife/{page_filename(pid, species['common_name'])}",
    }

# ── HTML generation ─────────────────────────────────────────────────────────

WILD_CSS = """\
  :root{
    --gold:#b8942a; --gold-light:#d4aa40;
    --cream:#f5f0e8; --parchment:#e8dfc8;
    --text-mid:#2e2e1e; --text-dark:#1a1a14;
    --safe-dark:#1a5c1a; --safe-light:#edf7ed;
    --danger:#8b2020; --danger-light:#fff0f0;
  }
  .theme-bird{
    --theme:#235e86; --theme-dark:#143b54; --theme-sage:#3f7ba3;
    --hero-a:#6ba3d4; --hero-b:#235e86; --hero-c:#102e43;
  }
  .theme-butterfly{
    --theme:#a23a6e; --theme-dark:#6b2247; --theme-sage:#bd5d8c;
    --hero-a:#e294bc; --hero-b:#a23a6e; --hero-c:#5a1d3c;
  }
  .theme-reptile{
    --theme:#9c5a33; --theme-dark:#5e3318; --theme-sage:#b97d50;
    --hero-a:#d39b6c; --hero-b:#9c5a33; --hero-c:#4f2a13;
  }
  .theme-mammal{
    --theme:#6b4a2b; --theme-dark:#3f2c19; --theme-sage:#8a6b48;
    --hero-a:#a98a63; --hero-b:#6b4a2b; --hero-c:#33230f;
  }
  .theme-amphibian{
    --theme:#3d7a52; --theme-dark:#234a30; --theme-sage:#5d9670;
    --hero-a:#7bb38d; --hero-b:#3d7a52; --hero-c:#1d3a26;
  }
  .wild-wrap{ max-width:680px; margin:2rem auto; background:#e8e3d8; min-height:80vh; border-radius:12px; overflow:hidden; box-shadow:0 4px 32px rgba(20,30,40,0.16); }
  @media (max-width:480px){ .wild-wrap{ margin:0; border-radius:0; box-shadow:none; min-height:100vh; } }
  .wild-hero{ position:relative; height:300px; overflow:hidden;
    background: radial-gradient(120% 80% at 75% 8%, rgba(255,255,255,0.55) 0%, rgba(255,255,255,0) 42%), linear-gradient(160deg, var(--hero-a) 0%, var(--hero-b) 52%, var(--hero-c) 100%); }
  .wild-hero img{ position:absolute;inset:0;width:100%;height:100%; object-fit:cover;display:block; }
  .wild-hero-overlay{ position:absolute; bottom:0; left:0; right:0; background:linear-gradient(transparent 0%, rgba(8,20,30,0.55) 45%, rgba(8,20,30,0.85) 100%); padding:60px 18px 16px; }
  .wild-hero-category{ font-size:11px;font-weight:700;letter-spacing:3px; text-transform:uppercase;color:var(--gold-light);margin-bottom:5px; }
  .wild-hero-name{ font-family:'Playfair Display',Georgia,serif;font-size:38px; font-weight:700;color:#fff;line-height:1.05;text-shadow:0 2px 12px rgba(0,0,0,0.35); }
  .wild-sci-band{ background:var(--theme); padding:11px 18px; display:flex; align-items:center; gap:10px; flex-wrap:wrap; border-bottom:2px solid var(--gold); }
  .wild-sci-name{ font-family:'Playfair Display',Georgia,serif;font-style:italic; font-size:19px;color:#fff;flex:1;text-shadow:0 1px 3px rgba(0,0,0,0.3); }
  .wild-family-tag{ font-size:12px;font-weight:700;letter-spacing:1.5px; text-transform:uppercase;color:var(--theme-dark);background:var(--gold-light); padding:5px 12px;border-radius:4px;text-decoration:none;transition:background .2s; }
  .wild-family-tag:hover{ background:#c49a20; }
  .wild-credit{ font-size:12px;color:#5b6b73;font-style:italic;padding:7px 16px;background:var(--cream); border-bottom:1px solid rgba(60,90,110,0.12);text-align:right; }
  .wild-credit strong{ font-style:normal;color:var(--theme-dark); }
  .wild-content{ padding:12px 0 64px; }
  .wild-status-row{ display:flex;gap:7px;padding:12px 14px;flex-wrap:wrap; background:var(--cream);border-bottom:1px solid rgba(60,90,110,0.15); }
  .badge{ font-size:12px;font-weight:700;padding:5px 13px;border-radius:20px;letter-spacing:.3px; }
  .badge-native{ background:#d0e8ff;color:#0a2a5a;border:1.5px solid rgba(10,42,90,0.3); }
  .badge-green { background:#d8eed8;color:#1a4a1a;border:1.5px solid rgba(45,74,45,0.35); }
  .badge-safe  { background:var(--safe-light);color:var(--safe-dark);border:1.5px solid rgba(26,92,26,0.3); }
  .badge-warn  { background:#fff3d8;color:#6a3a00;border:1.5px solid rgba(180,120,0,0.3); }
  .badge-danger{ background:var(--danger-light);color:var(--danger);border:1.5px solid rgba(139,32,32,0.3); }
  .badge-neutral{ background:var(--parchment);color:var(--text-mid);border:1.5px solid rgba(60,90,110,0.3); }
  .wild-section{ margin:12px 12px 0;background:#fff;border-radius:10px;overflow:hidden; box-shadow:0 2px 8px rgba(20,30,40,0.10);border:1px solid rgba(60,90,110,0.10); }
  .wild-section-header{ background:var(--theme);padding:12px 16px;display:flex;align-items:center;gap:10px; }
  .wild-section-icon{ font-size:18px;line-height:1; }
  .wild-section-title{ font-size:12px;font-weight:700;letter-spacing:2.5px;text-transform:uppercase;color:#fff; }
  .wild-section-body{ padding:16px; }
  .wild-section-body p{ font-size:17px;line-height:1.7;color:var(--text-mid); }
  .wild-section-body p + p{ margin-top:10px; }
  .quick-hits-list{ list-style:none;padding:14px 16px; }
  .quick-hits-list li{ font-size:17px;line-height:1.6;color:var(--text-mid); padding:11px 0 11px 22px;position:relative;border-bottom:1px solid rgba(60,90,110,0.10); }
  .quick-hits-list li:last-child{ border-bottom:none; }
  .quick-hits-list li::before{ content:'';position:absolute;left:0;top:19px;width:8px;height:8px; background:var(--gold);border-radius:50%; }
  .spot-list{ padding:14px 16px; }
  .spot-item{ padding:10px 0;border-bottom:1px solid rgba(60,90,110,0.10); }
  .spot-item:last-child{ border-bottom:none;padding-bottom:0; }
  .spot-label{ font-size:13px;font-weight:700;letter-spacing:1px;text-transform:uppercase; color:var(--theme-sage);margin-bottom:4px; }
  .spot-item p{ font-size:16px;line-height:1.65;color:var(--text-mid); }
  .spot-item.look p{ font-weight:500; }
  .alias-list{ display:flex;flex-wrap:wrap;gap:8px;padding:14px 16px; }
  .alias-tag{ background:var(--parchment);border:1.5px solid rgba(60,90,110,0.22); border-radius:6px;padding:6px 14px;font-size:15px;color:var(--text-mid);font-style:italic;font-weight:500; }
  .wild-tags{ display:flex;flex-wrap:wrap;gap:8px;margin-top:14px; }
  .wild-tag{ background:rgba(35,94,134,0.10);border:1.5px solid var(--theme-sage); border-radius:6px;padding:6px 13px;font-size:14px;color:var(--theme-dark);font-weight:600; }
  .wild-caution-section{ margin:12px 12px 0;background:#fffbf0;border-radius:10px;overflow:hidden; box-shadow:0 2px 8px rgba(20,30,40,0.10);border:1.5px solid rgba(180,120,0,0.25); }
  .wild-caution-section .wild-section-header{ background:#7a5000; }
  .wild-caution-section .wild-section-body p{ color:#3a2000; }
  .wild-safe-section{ margin:12px 12px 0;background:var(--safe-light);border-radius:10px;overflow:hidden; box-shadow:0 2px 8px rgba(20,30,40,0.10);border:1.5px solid rgba(26,92,26,0.2); }
  .wild-safe-section .wild-section-header{ background:var(--safe-dark); }
  .wild-safe-section .wild-section-body p{ color:#0a2a0a; }
  .wild-danger-section{ margin:12px 12px 0;background:var(--danger-light);border-radius:10px;overflow:hidden; box-shadow:0 2px 8px rgba(20,30,40,0.10);border:1.5px solid rgba(139,32,32,0.25); }
  .wild-danger-section .wild-section-header{ background:var(--danger); }
  .wild-danger-section .wild-section-body p{ color:#4a0a0a;font-weight:500; }
  .all-wild-link{ margin:14px 12px 0;display:flex;align-items:center;justify-content:center;gap:8px; background:var(--parchment);border-radius:10px;padding:14px 18px;text-decoration:none; border:1.5px solid rgba(60,90,110,0.22);color:var(--theme-dark);font-weight:700;font-size:15px;transition:background .4s ease; }
  .all-wild-link:hover{ background:var(--cream); }
  .wild-float-back{ position:fixed;bottom:24px;left:50%;transform:translateX(-50%); background:var(--theme);color:#fff;font-size:15px;font-weight:700;padding:10px 22px;border-radius:30px; text-decoration:none;box-shadow:0 4px 16px rgba(0,0,0,0.25);z-index:800;display:flex;align-items:center;gap:8px; transition:background .2s,transform .2s;white-space:nowrap; }
  .wild-float-back:hover{ background:var(--theme-dark);transform:translateX(-50%) translateY(-2px);color:#fff; }
  @media (min-width:481px){ .wild-float-back{ bottom:32px; } }
  .wild-section,.wild-caution-section,.wild-safe-section,.wild-danger-section,.all-wild-link{ animation:wildFadeUp .6s ease both; }
  .wild-section:nth-child(1){animation-delay:.05s}
  .wild-section:nth-child(2){animation-delay:.10s}
  .wild-section:nth-child(3){animation-delay:.15s}
  .wild-section:nth-child(4){animation-delay:.20s}
  .wild-section:nth-child(5){animation-delay:.25s}
  .wild-section:nth-child(6){animation-delay:.30s}
  .wild-caution-section,.wild-safe-section,.wild-danger-section{animation-delay:.32s}
  .all-wild-link{animation-delay:.38s}
  @keyframes wildFadeUp{ from{opacity:0;transform:translateY(10px)} to{opacity:1;transform:translateY(0)} }
  @media (prefers-reduced-motion:reduce){ .wild-section,.wild-caution-section,.wild-safe-section,.wild-danger-section,.all-wild-link{ animation:none; } }
  .gal-note{ font-size:13px;color:#888;padding:10px 16px 4px;font-style:italic; }
  .gal-grid{ display:grid;grid-template-columns:repeat(auto-fill,minmax(140px,1fr));gap:8px;padding:8px 16px 16px; }
  .gal-item{ position:relative;border-radius:8px;overflow:hidden;cursor:pointer;aspect-ratio:1;background:#111; }
  .gal-item img{ width:100%;height:100%;object-fit:cover;transition:transform .3s; }
  .gal-item:hover img{ transform:scale(1.05); }
  .gal-credit{ position:absolute;bottom:0;left:0;right:0;background:rgba(0,0,0,0.7);color:#ccc;font-size:11px;padding:4px 8px;opacity:0;transition:opacity .2s; }
  .gal-item:hover .gal-credit{ opacity:1; }
  .lightbox{ display:none;position:fixed;inset:0;background:rgba(0,0,0,0.92);z-index:999;justify-content:center;align-items:center; }
  .lightbox.active{ display:flex; }
  .lb-inner{ position:relative;max-width:90vw;max-height:90vh;display:flex;flex-direction:column;align-items:center; }
  .lb-img{ max-width:90vw;max-height:80vh;object-fit:contain;border-radius:4px; }
  .lb-credit{ color:#ccc;font-size:13px;margin-top:10px;font-style:italic; }
  .lb-close{ position:fixed;top:16px;right:20px;background:none;border:none;color:#fff;font-size:36px;cursor:pointer;z-index:1001; }
  .lb-prev,.lb-next{ position:fixed;top:50%;background:none;border:none;color:#fff;font-size:48px;cursor:pointer;padding:20px;transform:translateY(-50%); }
  .lb-prev{ left:8px; }
  .lb-next{ right:8px; }
  .lb-counter{ color:#888;font-size:12px;margin-top:4px; }"""


# ── Section renderers ───────────────────────────────────────────────────────

def render_badges(species):
    badges = []
    if species.get("native"):
        badges.append('<span class="badge badge-native">🌿 Native to Florida</span>')
    else:
        badges.append('<span class="badge badge-neutral">🌍 Non-Native</span>')

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


def render_quick_hits(species):
    items = species.get("quick_hits") or []
    if not items:
        return ""
    li = "".join(f"<li>{h(q)}</li>" for q in items)
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
        lb_data.append({
            "src": f"../photos/{pid}/{hero['filename']}",
            "credit": hero.get("photographer", "Unknown"),
            "license": hero.get("license", ""),
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
        photographer = p.get("photographer", "Unknown")
        lb_data.append({
            "src": url,
            "credit": photographer,
            "license": p.get("license", ""),
        })
        grid_items.append(
            f'<div class="gal-item" onclick="openLB({idx})">'
            f'<img src="{h(url)}" loading="lazy" alt="{h(common)} — photo by {h(photographer)}">'
            f'<div class="gal-credit">📷 {h(photographer)}</div></div>'
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
    function openLB(i){{lbIdx=i;var d=lbData[i];document.getElementById('lbImg').src=d.src;document.getElementById('lbCredit').innerHTML='📷 '+d.credit+' · '+d.license+' · via iNaturalist';document.getElementById('lbCounter').textContent=(i+1)+' / '+lbData.length;document.getElementById('lb').classList.add('active');document.body.style.overflow='hidden';}}
    function closeLB(e){{if(e&&e.target!==document.getElementById('lb')&&!e.target.classList.contains('lb-close'))return;document.getElementById('lb').classList.remove('active');document.body.style.overflow='';}}
    function stepLB(dir){{lbIdx=(lbIdx+dir+lbData.length)%lbData.length;openLB(lbIdx);}}
    document.addEventListener('keydown',function(e){{if(!document.getElementById('lb').classList.contains('active'))return;if(e.key==='Escape')closeLB();if(e.key==='ArrowRight')stepLB(1);if(e.key==='ArrowLeft')stepLB(-1);}});
    </script>"""

    return gallery_html, lightbox_html


def render_tags(species):
    tags = species.get("tags") or []
    aliases = species.get("also_known_as") or []
    if not tags and not aliases:
        return ""
    parts = []
    if aliases:
        parts.append("".join(f'<span class="alias-tag">{h(a)}</span>' for a in aliases))
    if tags:
        if aliases:
            parts.append('<div class="wild-tags">' + "".join(f'<span class="wild-tag">{h(t)}</span>' for t in tags) + '</div>')
        else:
            parts.append('<div class="alias-list" style="padding-top:0">' + "".join(f'<span class="wild-tag">{h(t)}</span>' for t in tags) + '</div>')

    inner = "".join(parts)
    if aliases and tags:
        inner = f'<div class="alias-list">{parts[0]}{parts[1]}</div>'
    return f'<div class="wild-section"><div class="wild-section-header"><span class="wild-section-icon">🏷️</span><span class="wild-section-title">Also Known As</span></div>{inner}</div>'


def generate_html(species, hero, gallery_photos):
    pid = species["id"]
    common = species["common_name"]
    sci = species["scientific_name"]
    family = (species.get("taxonomy") or {}).get("family", "")
    category = species.get("category", "")
    theme = theme_for(species.get("animal_group", ""))
    focus = hero.get("focus", "50% 50%") if hero else "50% 50%"

    # Hero image
    if hero:
        hero_path = f"../photos/{pid}/{hero['filename']}"
        photog = hero.get("photographer", "Unknown")
        license_str = hero.get("license", "")
        credit_html = f'📷 Photo by <strong>{h(photog)}</strong> · {h(license_str)} · via iNaturalist'
    else:
        hero_path = f"../photos/{pid}-{slugify(common)}.jpg"
        credit_html = "📷 Photo credit pending"

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
<style>
{WILD_CSS}
</style>
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
    <div class="wild-status-row">{render_badges(species)}</div>
    {content}
{lightbox_section}
    <a class="all-wild-link" href="../nature.html#wildlife">🦜 Explore More Wildlife</a>
  </div>
</div>

<a class="wild-float-back" href="../nature.html#wildlife">🦜 All Wildlife</a>

<div id="footer-placeholder"></div>
<script src="../js/site.js"></script>
<script>if (typeof injectShared === 'function') {{ injectShared({{ inatBar: false }}); }}</script>
</body>
</html>"""


# ── File writers ────────────────────────────────────────────────────────────

def write_html(species, hero, gallery_photos, dry_run=False):
    html_content = generate_html(species, hero, gallery_photos)
    filename = page_filename(species["id"], species["common_name"])
    path = WILDLIFE_DIR / filename
    if dry_run:
        return path, html_content
    WILDLIFE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(html_content, encoding="utf-8")
    tmp.rename(path)
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
    tmp = WILDLIFE_JSON.with_suffix(".tmp")
    tmp.write_text(json.dumps(entries, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.rename(WILDLIFE_JSON)
    return entry


def update_signage_status(species_id, new_status):
    signage = load_signage()
    for s in signage["species"]:
        if s["id"] == species_id:
            s["status"] = new_status
            break
    tmp = SIGNAGE_JSON.with_suffix(".tmp")
    tmp.write_text(json.dumps(signage, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.rename(SIGNAGE_JSON)


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
async function init(){DATA=await(await fetch('/api/data')).json();renderCounts();renderFilters();renderList();}
function renderCounts(){const c={html:0,spotted:0,research:0};DATA.species.forEach(s=>c[s.status]=(c[s.status]||0)+1);document.getElementById('counts').textContent=c.html+' html · '+c.spotted+' spotted · '+c.research+' research · '+DATA.species.length+' total';}
function renderFilters(){const bar=document.getElementById('filters');['html','spotted','research'].forEach(st=>{const b=document.createElement('button');b.className='filter-btn active';b.dataset.status=st;b.textContent=st;b.onclick=()=>{activeFilters.has(st)?activeFilters.delete(st):activeFilters.add(st);b.classList.toggle('active');renderList();};bar.appendChild(b);});}
function renderList(){const q=(document.getElementById('search').value||'').toLowerCase();const list=document.getElementById('species-list');list.innerHTML='';DATA.species.filter(s=>{if(!activeFilters.has(s.status))return false;if(q){const hay=(s.common_name+' '+s.scientific_name+' '+s.id+' '+(s.animal_group||'')+' '+(s.tags||[]).join(' ')).toLowerCase();if(!hay.includes(q))return false;}return true;}).forEach(s=>{const d=document.createElement('div');d.className='species-item'+(s.id===selectedId?' selected':'');d.innerHTML='<div class="dot '+s.status+'"></div><div class="info"><div class="name">'+esc(s.common_name)+'</div><div class="sci">'+esc(s.scientific_name)+'</div></div><div class="id-tag">'+s.id+'</div>';d.onclick=()=>{selectedId=s.id;renderList();renderDetail(s.id);};list.appendChild(d);});}
function renderDetail(id){const s=DATA.species.find(x=>x.id===id),hero=DATA.heroes[id]||null,gallery=DATA.galleries[id]||[],hasHero=!!hero;const main=document.getElementById('main');const heroUrl=hasHero?hero.photo_url:'';const heroHtml=hasHero?'<img src="'+esc(heroUrl)+'" alt="'+esc(s.common_name)+'">':'<div class="no-hero">No hero photo</div>';const pj=DATA.wj_lookup[id];
main.innerHTML='<div class="detail"><div class="detail-header"><div class="detail-hero">'+heroHtml+'</div><div class="detail-meta"><h2>'+esc(s.common_name)+'</h2><div class="sci">'+esc(s.scientific_name)+'</div><div class="meta-row"><strong>ID:</strong> '+s.id+'</div><div class="meta-row"><strong>Group:</strong> '+esc(s.animal_group||'')+'</div><div class="meta-row"><strong>Category:</strong> '+esc(s.category||'')+'</div><div class="meta-row"><strong>Gallery photos:</strong> '+gallery.length+'</div><div class="meta-row"><strong>In wildlife.json:</strong> '+(pj?'Yes':'No')+'</div>'+(hasHero?'<div class="meta-row"><strong>Hero:</strong> '+esc(hero.photographer_name||hero.photographer)+' · '+esc(hero.filename||'')+'</div>':'<div class="meta-row" style="color:#c49a20"><strong>⚠ No hero photo</strong></div>')+'</div></div><div class="action-bar"><span class="status-badge '+s.status+'">'+s.status.toUpperCase()+'</span><button class="btn btn-publish" onclick="doPublish(\''+id+'\')" '+(hasHero?'':'disabled title="Needs hero photo"')+'>'+(s.status==='html'?'♻️ Regenerate':'🚀 Publish')+'</button><button class="btn btn-preview" onclick="window.open(\'/api/preview?id='+id+'\',\'_blank\')">👁 Preview</button><span class="action-msg" id="action-msg"></span></div>'+buildSections(s,gallery)+'</div>';}
function buildSections(s,gallery){let h='';if(s.quick_hits?.length)h+=ds('Quick Hits',s.quick_hits.map((q,i)=>'<div class="text-block">'+(i+1)+'. '+esc(q)+'</div>').join(''));if(s.identification){let r='';(s.identification.blocks||[]).forEach(b=>{r+='<div class="data-row"><div class="label">'+esc(b.label)+'</div><div class="value">'+esc(b.text)+'</div></div>';});if(s.identification.what_to_look_for)r+='<div class="data-row"><div class="label">Look for</div><div class="value">'+esc(s.identification.what_to_look_for)+'</div></div>';h+=ds('Identification',r);}if(s.diet)h+=ds('Diet','<div class="text-block">'+esc(s.diet)+'</div>');if(s.where_to_look||s.when_to_see)h+=ds('Where & When','<div class="data-row"><div class="label">Where</div><div class="value">'+esc(s.where_to_look||'')+'</div></div><div class="data-row"><div class="label">When</div><div class="value">'+esc(s.when_to_see||'')+'</div></div>');if(s.more_information?.length)h+=ds('More Information',s.more_information.map(p=>'<div class="text-block">'+esc(p)+'</div>').join(''));if(s.interaction)h+=ds('Interaction','<div class="data-row"><div class="label">Level: '+esc(s.interaction.level||'')+'</div><div class="value">'+esc(s.interaction.guidance||'')+'</div></div>');if(gallery.length>1){let g='<div class="gal-preview">';gallery.forEach(p=>{if(p.photo_url)g+='<img src="'+esc(p.photo_url)+'">';});g+='</div>';h+=ds('Gallery ('+gallery.length+' photos)',g);}if(s.tags?.length)h+=ds('Tags','<div>'+s.tags.map(t=>'<span class="tag">'+esc(t)+'</span>').join('')+'</div>');return h;}
function ds(title,body){return '<div class="data-section"><div class="data-section-header" onclick="this.nextElementSibling.classList.toggle(\'collapsed\')">'+esc(title)+' <span class="toggle">▾</span></div><div class="data-section-body">'+body+'</div></div>';}
async function doPublish(id){const msg=document.getElementById('action-msg');msg.textContent='Publishing…';msg.style.color='#d4aa40';try{const r=await(await fetch('/api/publish',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({id})})).json();if(r.ok){msg.textContent='✓ Published';msg.style.color='#4a9e56';showToast('Published '+r.filename);DATA=await(await fetch('/api/data')).json();renderCounts();renderList();renderDetail(id);}else{msg.textContent='✗ '+r.error;msg.style.color='#c44';}}catch(e){msg.textContent='✗ Network error';msg.style.color='#c44';}}
function showToast(t){const el=document.getElementById('toast');el.textContent=t;el.classList.add('show');setTimeout(()=>el.classList.remove('show'),3000);}
function esc(s){if(!s)return '';const d=document.createElement('div');d.textContent=s;return d.innerHTML;}
document.getElementById('search').addEventListener('input',renderList);
init();
</script>
</body>
</html>"""


# ── HTTP Server ─────────────────────────────────────────────────────────────

class DashboardHandler(http.server.BaseHTTPRequestHandler):
    def log_message(self, fmt, *args): pass

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
            self._html(DASHBOARD_HTML)
        elif parsed.path == "/api/data":
            signage = load_signage()
            credits = load_credits()
            heroes = build_hero_lookup(credits)
            galleries = build_gallery_lookup(credits)
            wj = load_wildlife_json()
            wj_lookup = {w["id"]: w for w in wj}
            heroes_out = {}
            for pid, hr in heroes.items():
                heroes_out[pid] = {
                    "filename": hr.get("filename", ""),
                    "photo_url": hr.get("photo_url", ""),
                    "photographer_name": hr.get("photographer_name", ""),
                    "photographer": hr.get("photographer", ""),
                    "license": hr.get("license", ""),
                    "focus": hr.get("focus", "50% 50%"),
                }
            galleries_out = {}
            for pid, photos in galleries.items():
                galleries_out[pid] = [{
                    "photo_url": p.get("photo_url", ""),
                    "photographer": p.get("photographer", ""),
                    "license": p.get("license", ""),
                    "hero": p.get("hero", False),
                } for p in photos]
            self._json({
                "species": signage["species"],
                "heroes": heroes_out,
                "galleries": galleries_out,
                "wj_lookup": wj_lookup,
                "meta": signage["meta"],
            })
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
        hero = heroes.get(sp["id"])
        if not hero:
            print(f"  ⚠ {sp['id']} {sp['common_name']}: no hero, skipping")
            skipped += 1
            continue
        write_html(sp, hero, galleries.get(sp["id"], []))
        fresh.append(build_wildlife_json_entry(sp, hero))
        count += 1
    fresh.sort(key=lambda e: e["id"])
    tmp = WILDLIFE_JSON.with_suffix(".tmp")
    tmp.write_text(json.dumps(fresh, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.rename(WILDLIFE_JSON)
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
    tmp = WILDLIFE_JSON.with_suffix(".tmp")
    tmp.write_text(json.dumps(kept, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.rename(WILDLIFE_JSON)
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
    else:
        print(__doc__); sys.exit(1)

if __name__ == "__main__":
    main()
