#!/usr/bin/env python3
"""
generate_wildlife_pages.py
==========================
Generates wildlife species HTML pages from the two authoritative JSONs:
  - wildlife_signage.json  (content)
  - photo_credits.json     (hero photo, credit, focus)

USAGE:
  python3 generate_wildlife_pages.py                    # all html-status species
  python3 generate_wildlife_pages.py --species PSBP-99999,PSBP-99998  # specific IDs
  python3 generate_wildlife_pages.py --ids 99994-99999  # ID range

OUTPUT:
  wildlife/PSBP-XXXXX-Common-Name.html
"""

import json
import os
import sys
import html as html_mod

WILDLIFE_JSON = "wildlife_signage.json"
PHOTO_CREDITS_JSON = "photo_credits.json"
OUTPUT_DIR = "wildlife"

THEME_MAP = {
    "Bird": "theme-bird",
    "Butterfly": "theme-butterfly",
    "Moth": "theme-butterfly",
    "Lizard": "theme-reptile",
    "Turtle": "theme-reptile",
    "Snake": "theme-reptile",
    "Mammal": "theme-mammal",
    "Dragonfly": "theme-amphibian",
    "Grasshopper": "theme-amphibian",
    "True Bug": "theme-amphibian",
    "Beetle": "theme-amphibian",
    "Crustacean": "theme-amphibian",
}

CONSERVATION_BADGE = {
    "Green": ('badge-green', '✅'),
    "Yellow": ('badge-warn', '⚠️'),
    "Red": ('badge-danger', '🔴'),
}

DANGER_BADGE = {
    "Green": ('badge-safe', '✅'),
    "Yellow": ('badge-warn', '⚠️'),
    "Red": ('badge-danger', '⚠️'),
}


def h(text):
    """HTML-escape a string."""
    return html_mod.escape(str(text or ""))


def get_hero(psbp_id, photos):
    """Find the hero photo for a species from photo_credits."""
    for p in photos:
        if p.get("psbp_id") == psbp_id and p.get("hero"):
            return p
    return None


def build_page(species, hero_photo, all_photos):
    """Build the full HTML page string."""
    sid = species["id"]
    name = species["common_name"]
    sci = species["scientific_name"]
    family = species.get("taxonomy", {}).get("family", "")
    category = species.get("category", "")
    group = species.get("animal_group", "Bird")
    theme = THEME_MAP.get(group, "theme-bird")

    # Hero image
    if hero_photo:
        fn = hero_photo.get("filename", "")
        # Subfolder model: photos/PSBP-XXXXX/filename.jpg
        if fn and not fn.startswith("PSBP-"):
            img_src = f"../photos/{sid}/{fn}"
        else:
            img_src = f"../photos/{fn}"
        focus = hero_photo.get("focus", "50% 50%") or "50% 50%"
        focus_style = f' style="object-position:{focus};"' if focus != "50% 50%" else ""
        photographer = hero_photo.get("photographer", "Unknown")
        license_code = hero_photo.get("license", "")
        credit_html = f'<div class="wild-credit">📷 Photo by <strong>{h(photographer)}</strong> · {h(license_code)} · via iNaturalist</div>'
    else:
        img_src = ""
        focus_style = ""
        credit_html = ""

    # Badges
    native_badge = '<span class="badge badge-native">🌿 Native to Florida</span>' if species.get("native") else '<span class="badge badge-neutral">Introduced</span>'

    cons = species.get("conservation", {})
    cons_level = cons.get("level", "Green")
    cons_cls, cons_icon = CONSERVATION_BADGE.get(cons_level, ("badge-green", "✅"))
    cons_text = cons.get("status", "Least Concern")
    # Shorten for badge display
    cons_short = cons_text.split(".")[0].split("—")[0].strip()
    if len(cons_short) > 40:
        cons_short = cons_level
    cons_badge = f'<span class="badge {cons_cls}">{cons_icon} {h(cons_short)}</span>'

    danger = species.get("danger", {})
    danger_level = danger.get("people_level", "Green")
    danger_cls, danger_icon = DANGER_BADGE.get(danger_level, ("badge-safe", "✅"))
    danger_text = danger.get("people", "Harmless")
    danger_short = danger_text.split(".")[0].strip()
    danger_badge = f'<span class="badge {danger_cls}">{danger_icon} {h(danger_short)}</span>'

    # Quick hits
    qh_items = ""
    for qh in species.get("quick_hits", []):
        if qh and qh != "—":
            qh_items += f"<li>{h(qh)}</li>"
    quick_hits_section = f"""<div class="wild-section"><div class="wild-section-header"><span class="wild-section-icon">⚡</span><span class="wild-section-title">Quick Hits</span></div><ul class="quick-hits-list">{qh_items}</ul></div>""" if qh_items else ""

    # Identification (How to Spot It)
    ident = species.get("identification", {})
    blocks = ident.get("blocks", [])
    wtlf = ident.get("what_to_look_for", "")
    spot_items = ""
    for b in blocks:
        label = b.get("label", "")
        text = b.get("text", "")
        if label and text and label != "Summary":
            spot_items += f'<div class="spot-item"><div class="spot-label">{h(label)}</div><p>{h(text)}</p></div>'
    # Add sounds if present
    sounds = species.get("sounds", "")
    if sounds and sounds != "To be documented." and sounds != "Silent.":
        spot_items += f'<div class="spot-item"><div class="spot-label">Voice</div><p>{h(sounds)}</p></div>'
    if wtlf and wtlf != "To be expanded.":
        spot_items += f'<div class="spot-item look"><div class="spot-label">What to Look For</div><p>{h(wtlf)}</p></div>'
    id_section = f"""<div class="wild-section"><div class="wild-section-header"><span class="wild-section-icon">🔎</span><span class="wild-section-title">How to Spot It</span></div><div class="spot-list">{spot_items}</div></div>""" if spot_items else ""

    # Diet
    diet = species.get("diet", "")
    diet_section = ""
    if diet and diet != "To be documented.":
        diet_section = f"""<div class="wild-section"><div class="wild-section-header"><span class="wild-section-icon">🍽️</span><span class="wild-section-title">What It Eats</span></div><div class="wild-section-body"><p>{h(diet)}</p></div></div>"""

    # Where & When
    where = species.get("where_to_look", "")
    when = species.get("when_to_see", "")
    ww_items = ""
    if where:
        ww_items += f'<div class="spot-item"><div class="spot-label">Where in the park</div><p>{h(where)}</p></div>'
    if when:
        ww_items += f'<div class="spot-item"><div class="spot-label">When to see it</div><p>{h(when)}</p></div>'
    ww_section = f"""<div class="wild-section"><div class="wild-section-header"><span class="wild-section-icon">📍</span><span class="wild-section-title">Where &amp; When</span></div><div class="spot-list">{ww_items}</div></div>""" if ww_items else ""

    # Interaction / etiquette
    interaction = species.get("interaction", {})
    int_guidance = interaction.get("guidance", "")
    int_level = interaction.get("level", "Green")
    if int_guidance and int_guidance != "Observe and enjoy.":
        int_section = f"""<div class="wild-caution-section"><div class="wild-section-header"><span class="wild-section-icon">🤝</span><span class="wild-section-title">Watching It Respectfully</span></div><div class="wild-section-body"><p>{h(int_guidance)}</p></div></div>"""
    else:
        int_section = f"""<div class="wild-safe-section"><div class="wild-section-header"><span class="wild-section-icon">🤝</span><span class="wild-section-title">Watching It Respectfully</span></div><div class="wild-section-body"><p>Enjoy from a distance and please do not feed or approach any park wildlife.</p></div></div>"""

    # Also Known As + Tags
    aka = species.get("also_known_as", [])
    tags = species.get("tags", [])
    aka_section = ""
    if aka or tags:
        aka_chips = ""
        if aka:
            aka_chips = '<div class="alias-list">' + "".join(f'<span class="alias-tag">{h(a)}</span>' for a in aka) + "</div>"
        tag_chips = ""
        if tags:
            tag_chips = '<div class="alias-list" style="padding-top:0">' + "".join(f'<span class="wild-tag">{h(t)}</span>' for t in tags) + "</div>"
        aka_section = f"""<div class="wild-section"><div class="wild-section-header"><span class="wild-section-icon">🏷️</span><span class="wild-section-title">Also Known As</span></div>{aka_chips}{tag_chips}</div>"""

    # Photo Gallery — only photos with "gallery" in role
    gallery_photos = [p for p in all_photos if not p.get("hero") and p.get("filename") and "gallery" in (p.get("role") or [])]
    gallery_section = ""
    # Build lightbox data: hero always first, then gallery-tagged photos
    lb_all = []
    if hero_photo:
        lb_all.append({"src": f"../photos/{sid}/{hero_photo['filename']}", "credit": hero_photo.get("photographer", "Unknown"), "license": hero_photo.get("license", "")})
    for p in gallery_photos:
        lb_all.append({"src": f"../photos/{sid}/{p['filename']}", "credit": p.get("photographer", "Unknown"), "license": p.get("license", "")})

    if gallery_photos:
        gallery_items = ""
        for i, p in enumerate(gallery_photos):
            fn = p["filename"]
            photog = p.get("photographer", "Unknown")
            img_path = f"../photos/{sid}/{fn}"
            lb_idx = i + 1  # offset by 1 because hero is index 0
            gallery_items += f'<div class="gal-item" onclick="openLB({lb_idx})"><img src="{img_path}" loading="lazy" alt="{h(name)} — photo by {h(photog)}"><div class="gal-credit">📷 {h(photog)}</div></div>'

        gallery_section = f"""<div class="wild-section"><div class="wild-section-header"><span class="wild-section-icon">📸</span><span class="wild-section-title">Photo Gallery</span></div>
    <div class="gal-note">Photos contributed by park visitors and volunteers via iNaturalist</div>
    <div class="gal-grid">{gallery_items}</div></div>
    <div class="lightbox" id="lb" onclick="closeLB(event)">
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
    var lbData={json.dumps(lb_all)};
    var lbIdx=0;
    function openLB(i){{lbIdx=i;var d=lbData[i];document.getElementById('lbImg').src=d.src;document.getElementById('lbCredit').innerHTML='📷 '+d.credit+' · '+d.license+' · via iNaturalist';document.getElementById('lbCounter').textContent=(i+1)+' / '+lbData.length;document.getElementById('lb').classList.add('active');document.body.style.overflow='hidden';}}
    function closeLB(e){{if(e&&e.target!==document.getElementById('lb')&&!e.target.classList.contains('lb-close'))return;document.getElementById('lb').classList.remove('active');document.body.style.overflow='';}}
    function stepLB(dir){{lbIdx=(lbIdx+dir+lbData.length)%lbData.length;openLB(lbIdx);}}
    document.addEventListener('keydown',function(e){{if(!document.getElementById('lb').classList.contains('active'))return;if(e.key==='Escape')closeLB();if(e.key==='ArrowRight')stepLB(1);if(e.key==='ArrowLeft')stepLB(-1);}});
    </script>"""

    # Hero img tag
    hero_img = ""
    if img_src:
        style_parts = ["cursor:pointer"]
        if focus and focus != "50% 50%":
            style_parts.append(f"object-position:{focus}")
        hero_style = ";".join(style_parts)
        hero_img = f'<img style="{hero_style}" src="{img_src}" alt="{h(name)} at Palma Sola Botanical Park" onclick="openLB(0)">'

    # Float back icon based on group
    float_icon = "🦜" if group == "Bird" else "🦋" if group in ("Butterfly", "Moth") else "🦎" if group in ("Lizard", "Turtle", "Snake") else "🐾" if group == "Mammal" else "🦜"

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{h(name)} · Palma Sola Botanical Park</title>
<link rel="stylesheet" href="../css/site.css">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Playfair+Display:ital,wght@0,700;1,600&display=swap" rel="stylesheet">
<style>
  :root{{
    --gold:#b8942a; --gold-light:#d4aa40;
    --cream:#f5f0e8; --parchment:#e8dfc8;
    --text-mid:#2e2e1e; --text-dark:#1a1a14;
    --safe-dark:#1a5c1a; --safe-light:#edf7ed;
    --danger:#8b2020; --danger-light:#fff0f0;
  }}
  .theme-bird{{
    --theme:#235e86; --theme-dark:#143b54; --theme-sage:#3f7ba3;
    --hero-a:#6ba3d4; --hero-b:#235e86; --hero-c:#102e43;
  }}
  .theme-butterfly{{
    --theme:#a23a6e; --theme-dark:#6b2247; --theme-sage:#bd5d8c;
    --hero-a:#e294bc; --hero-b:#a23a6e; --hero-c:#5a1d3c;
  }}
  .theme-reptile{{
    --theme:#9c5a33; --theme-dark:#5e3318; --theme-sage:#b97d50;
    --hero-a:#d39b6c; --hero-b:#9c5a33; --hero-c:#4f2a13;
  }}
  .theme-mammal{{
    --theme:#6b4a2b; --theme-dark:#3f2c19; --theme-sage:#8a6b48;
    --hero-a:#a98a63; --hero-b:#6b4a2b; --hero-c:#33230f;
  }}
  .theme-amphibian{{
    --theme:#3d7a52; --theme-dark:#234a30; --theme-sage:#5d9670;
    --hero-a:#7bb38d; --hero-b:#3d7a52; --hero-c:#1d3a26;
  }}
  .wild-wrap{{ max-width:680px; margin:2rem auto; background:#e8e3d8; min-height:80vh; border-radius:12px; overflow:hidden; box-shadow:0 4px 32px rgba(20,30,40,0.16); }}
  @media (max-width:480px){{ .wild-wrap{{ margin:0; border-radius:0; box-shadow:none; min-height:100vh; }} }}
  .wild-hero{{ position:relative; height:300px; overflow:hidden;
    background: radial-gradient(120% 80% at 75% 8%, rgba(255,255,255,0.55) 0%, rgba(255,255,255,0) 42%), linear-gradient(160deg, var(--hero-a) 0%, var(--hero-b) 52%, var(--hero-c) 100%); }}
  .wild-hero img{{ position:absolute;inset:0;width:100%;height:100%; object-fit:cover;object-position:center 35%;display:block; }}
  .wild-hero-overlay{{ position:absolute; bottom:0; left:0; right:0; background:linear-gradient(transparent 0%, rgba(8,20,30,0.55) 45%, rgba(8,20,30,0.85) 100%); padding:60px 18px 16px; }}
  .wild-hero-category{{ font-size:11px;font-weight:700;letter-spacing:3px; text-transform:uppercase;color:var(--gold-light);margin-bottom:5px; }}
  .wild-hero-name{{ font-family:'Playfair Display',Georgia,serif;font-size:38px; font-weight:700;color:#fff;line-height:1.05;text-shadow:0 2px 12px rgba(0,0,0,0.35); }}
  .wild-sci-band{{ background:var(--theme); padding:11px 18px; display:flex; align-items:center; gap:10px; flex-wrap:wrap; border-bottom:2px solid var(--gold); }}
  .wild-sci-name{{ font-family:'Playfair Display',Georgia,serif;font-style:italic; font-size:19px;color:#fff;flex:1;text-shadow:0 1px 3px rgba(0,0,0,0.3); }}
  .wild-family-tag{{ font-size:12px;font-weight:700;letter-spacing:1.5px; text-transform:uppercase;color:var(--theme-dark);background:var(--gold-light); padding:5px 12px;border-radius:4px;text-decoration:none;transition:background .2s; }}
  .wild-family-tag:hover{{ background:#c49a20; }}
  .wild-credit{{ font-size:12px;color:#5b6b73;font-style:italic;padding:7px 16px;background:var(--cream); border-bottom:1px solid rgba(60,90,110,0.12);text-align:right; }}
  .wild-credit strong{{ font-style:normal;color:var(--theme-dark); }}
  .wild-content{{ padding:12px 0 64px; }}
  .wild-status-row{{ display:flex;gap:7px;padding:12px 14px;flex-wrap:wrap; background:var(--cream);border-bottom:1px solid rgba(60,90,110,0.15); }}
  .badge{{ font-size:12px;font-weight:700;padding:5px 13px;border-radius:20px;letter-spacing:.3px; }}
  .badge-native{{ background:#d0e8ff;color:#0a2a5a;border:1.5px solid rgba(10,42,90,0.3); }}
  .badge-green {{ background:#d8eed8;color:#1a4a1a;border:1.5px solid rgba(45,74,45,0.35); }}
  .badge-safe  {{ background:var(--safe-light);color:var(--safe-dark);border:1.5px solid rgba(26,92,26,0.3); }}
  .badge-warn  {{ background:#fff3d8;color:#6a3a00;border:1.5px solid rgba(180,120,0,0.3); }}
  .badge-danger{{ background:var(--danger-light);color:var(--danger);border:1.5px solid rgba(139,32,32,0.3); }}
  .badge-neutral{{ background:var(--parchment);color:var(--text-mid);border:1.5px solid rgba(60,90,110,0.3); }}
  .wild-section{{ margin:12px 12px 0;background:#fff;border-radius:10px;overflow:hidden; box-shadow:0 2px 8px rgba(20,30,40,0.10);border:1px solid rgba(60,90,110,0.10); }}
  .wild-section-header{{ background:var(--theme);padding:12px 16px;display:flex;align-items:center;gap:10px; }}
  .wild-section-icon{{ font-size:18px;line-height:1; }}
  .wild-section-title{{ font-size:12px;font-weight:700;letter-spacing:2.5px;text-transform:uppercase;color:#fff; }}
  .wild-section-body{{ padding:16px; }}
  .wild-section-body p{{ font-size:17px;line-height:1.7;color:var(--text-mid); }}
  .wild-section-body p + p{{ margin-top:10px; }}
  .quick-hits-list{{ list-style:none;padding:14px 16px; }}
  .quick-hits-list li{{ font-size:17px;line-height:1.6;color:var(--text-mid); padding:11px 0 11px 22px;position:relative;border-bottom:1px solid rgba(60,90,110,0.10); }}
  .quick-hits-list li:last-child{{ border-bottom:none; }}
  .quick-hits-list li::before{{ content:'';position:absolute;left:0;top:19px;width:8px;height:8px; background:var(--gold);border-radius:50%; }}
  .spot-list{{ padding:14px 16px; }}
  .spot-item{{ padding:10px 0;border-bottom:1px solid rgba(60,90,110,0.10); }}
  .spot-item:last-child{{ border-bottom:none;padding-bottom:0; }}
  .spot-label{{ font-size:13px;font-weight:700;letter-spacing:1px;text-transform:uppercase; color:var(--theme-sage);margin-bottom:4px; }}
  .spot-item p{{ font-size:16px;line-height:1.65;color:var(--text-mid); }}
  .spot-item.look p{{ font-weight:500; }}
  .alias-list{{ display:flex;flex-wrap:wrap;gap:8px;padding:14px 16px; }}
  .alias-tag{{ background:var(--parchment);border:1.5px solid rgba(60,90,110,0.22); border-radius:6px;padding:6px 14px;font-size:15px;color:var(--text-mid);font-style:italic;font-weight:500; }}
  .wild-tags{{ display:flex;flex-wrap:wrap;gap:8px;margin-top:14px; }}
  .wild-tag{{ background:rgba(35,94,134,0.10);border:1.5px solid var(--theme-sage); border-radius:6px;padding:6px 13px;font-size:14px;color:var(--theme-dark);font-weight:600; }}
  .wild-caution-section{{ margin:12px 12px 0;background:#fffbf0;border-radius:10px;overflow:hidden; box-shadow:0 2px 8px rgba(20,30,40,0.10);border:1.5px solid rgba(180,120,0,0.25); }}
  .wild-caution-section .wild-section-header{{ background:#7a5000; }}
  .wild-caution-section .wild-section-body p{{ color:#3a2000; }}
  .wild-safe-section{{ margin:12px 12px 0;background:var(--safe-light);border-radius:10px;overflow:hidden; box-shadow:0 2px 8px rgba(20,30,40,0.10);border:1.5px solid rgba(26,92,26,0.2); }}
  .wild-safe-section .wild-section-header{{ background:var(--safe-dark); }}
  .wild-safe-section .wild-section-body p{{ color:#0a2a0a; }}
  .all-wild-link{{ margin:14px 12px 0;display:flex;align-items:center;justify-content:center;gap:8px; background:var(--parchment);border-radius:10px;padding:14px 18px;text-decoration:none; border:1.5px solid rgba(60,90,110,0.22);color:var(--theme-dark);font-weight:700;font-size:15px;transition:background .4s ease; }}
  .all-wild-link:hover{{ background:var(--cream); }}
  .wild-float-back{{ position:fixed;bottom:24px;left:50%;transform:translateX(-50%); background:var(--theme);color:#fff;font-size:15px;font-weight:700;padding:10px 22px;border-radius:30px; text-decoration:none;box-shadow:0 4px 16px rgba(0,0,0,0.25);z-index:800;display:flex;align-items:center;gap:8px; transition:background .2s,transform .2s;white-space:nowrap; }}
  .wild-float-back:hover{{ background:var(--theme-dark);transform:translateX(-50%) translateY(-2px);color:#fff; }}
  @media (min-width:481px){{ .wild-float-back{{ bottom:32px; }} }}
  .wild-section,.wild-caution-section,.wild-safe-section,.all-wild-link{{ animation:wildFadeUp .6s ease both; }}
  .wild-section:nth-child(1){{animation-delay:.05s}}
  .wild-section:nth-child(2){{animation-delay:.10s}}
  .wild-section:nth-child(3){{animation-delay:.15s}}
  .wild-section:nth-child(4){{animation-delay:.20s}}
  .wild-section:nth-child(5){{animation-delay:.25s}}
  .wild-section:nth-child(6){{animation-delay:.30s}}
  .wild-caution-section,.wild-safe-section{{animation-delay:.32s}}
  .all-wild-link{{animation-delay:.38s}}
  @keyframes wildFadeUp{{ from{{opacity:0;transform:translateY(10px)}} to{{opacity:1;transform:translateY(0)}} }}
  @media (prefers-reduced-motion:reduce){{ .wild-section,.wild-caution-section,.wild-safe-section,.all-wild-link{{ animation:none; }} }}
  .gal-note{{ font-size:13px;color:#888;padding:10px 16px 4px;font-style:italic; }}
  .gal-grid{{ display:grid;grid-template-columns:repeat(auto-fill,minmax(140px,1fr));gap:8px;padding:8px 16px 16px; }}
  .gal-item{{ position:relative;border-radius:8px;overflow:hidden;cursor:pointer;aspect-ratio:1;background:#111; }}
  .gal-item img{{ width:100%;height:100%;object-fit:cover;transition:transform .3s; }}
  .gal-item:hover img{{ transform:scale(1.05); }}
  .gal-credit{{ position:absolute;bottom:0;left:0;right:0;background:rgba(0,0,0,0.7);color:#ccc;font-size:11px;padding:4px 8px;opacity:0;transition:opacity .2s; }}
  .gal-item:hover .gal-credit{{ opacity:1; }}
  .lightbox{{ display:none;position:fixed;inset:0;background:rgba(0,0,0,0.92);z-index:999;justify-content:center;align-items:center; }}
  .lightbox.active{{ display:flex; }}
  .lb-inner{{ position:relative;max-width:90vw;max-height:90vh;display:flex;flex-direction:column;align-items:center; }}
  .lb-img{{ max-width:90vw;max-height:80vh;object-fit:contain;border-radius:4px; }}
  .lb-credit{{ color:#ccc;font-size:13px;margin-top:10px;font-style:italic; }}
  .lb-close{{ position:fixed;top:16px;right:20px;background:none;border:none;color:#fff;font-size:36px;cursor:pointer;z-index:1001; }}
  .lb-prev,.lb-next{{ position:fixed;top:50%;background:none;border:none;color:#fff;font-size:48px;cursor:pointer;padding:20px;transform:translateY(-50%); }}
  .lb-prev{{ left:8px; }}
  .lb-next{{ right:8px; }}
  .lb-counter{{ color:#888;font-size:12px;margin-top:4px; }}
</style>
</head>
<body>
<div id="nav-placeholder"></div>

<div class="wild-wrap {theme}">
  <div class="wild-hero">
    {hero_img}
    <div class="wild-hero-overlay">
      <div class="wild-hero-category">{h(category)}</div>
      <div class="wild-hero-name">{h(name)}</div>
    </div>
  </div>
  <div class="wild-sci-band">
    <span class="wild-sci-name">{h(sci)}</span>
    <a class="wild-family-tag" href="../nature.html?wfamily={h(family)}">{h(family)}</a>
  </div>
  {credit_html}
  <div class="wild-content">
    <div class="wild-status-row">{native_badge}{cons_badge}{danger_badge}</div>
    {quick_hits_section}
    {id_section}
    {diet_section}
    {ww_section}
    {int_section}
    {gallery_section}
    {aka_section}
    <a class="all-wild-link" href="../nature.html#wildlife">{float_icon} Explore More Wildlife</a>
  </div>
</div>

<a class="wild-float-back" href="../nature.html#wildlife">{float_icon} All Wildlife</a>

<div id="footer-placeholder"></div>
<script src="../js/site.js"></script>
<script>if (typeof injectShared === 'function') {{ injectShared({{ inatBar: false }}); }}</script>
</body>
</html>"""


def main():
    ws = json.load(open(WILDLIFE_JSON, encoding="utf-8"))
    pc = json.load(open(PHOTO_CREDITS_JSON, encoding="utf-8"))

    # Parse args
    target_ids = None
    if "--species" in sys.argv:
        idx = sys.argv.index("--species") + 1
        target_ids = set(sys.argv[idx].split(","))
    elif "--ids" in sys.argv:
        idx = sys.argv.index("--ids") + 1
        rng = sys.argv[idx]
        if "-" in rng:
            lo, hi = rng.split("-")
            target_ids = {f"PSBP-{i:05d}" for i in range(int(lo), int(hi) + 1)}

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    generated = 0

    for sp in ws["species"]:
        if target_ids and sp["id"] not in target_ids:
            continue

        hero = get_hero(sp["id"], pc["photos"])
        species_photos = [p for p in pc["photos"] if p.get("psbp_id") == sp["id"]]
        page_html = build_page(sp, hero, species_photos)

        slug = sp["common_name"].replace(" ", "-").replace("'", "")
        filename = f'{sp["id"]}-{slug}.html'
        filepath = os.path.join(OUTPUT_DIR, filename)

        with open(filepath, "w", encoding="utf-8") as f:
            f.write(page_html)

        hero_fn = hero["filename"] if hero else "NO HERO"
        print(f"  ✓ {filename:45} hero={hero_fn}")
        generated += 1

    print(f"\n{generated} pages generated in {OUTPUT_DIR}/")


if __name__ == "__main__":
    main()
