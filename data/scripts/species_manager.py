#!/usr/bin/env python3
"""
species_manager.py — Unified PSBP Species Dashboard

One tool, one port (8700), five tabs matching the species pipeline:
  Overview  → Pipeline funnel, needs-attention flags (both kingdoms)
  Intake    → Import species from iNat, mint PSBP IDs
  Photos    → Triage + review (hero/gallery/roles)
  Edit      → Signage field editor with live HTML preview
  Publish   → Promote/demote, generate HTML, rebuild indexes

Usage:
    python3 species_manager.py                # Start on port 8700
    python3 species_manager.py --port 8705    # Custom port

Architecture doc:  SPECIES_MANAGER.md
Shared module:     psbp_common.py
"""

import json
import os
import sys
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

# ╔══════════════════════════════════════════════════════════════════════════╗
# ║  CONFIGURATION                                                         ║
# ╚══════════════════════════════════════════════════════════════════════════╝

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from psbp_common import REPO

PORT = 8700

# Data source paths (all relative to REPO)
PLANT_SIGNAGE      = os.path.join(REPO, "data", "sources", "plant_signage.json")
WILDLIFE_SIGNAGE   = os.path.join(REPO, "data", "sources", "wildlife_signage.json")
PHOTO_CREDITS      = os.path.join(REPO, "data", "sources", "photo_credits.json")
PHOTOGRAPHER_NAMES = os.path.join(REPO, "data", "sources", "photographer_names.json")
PLANTS_INDEX       = os.path.join(REPO, "plants.json")
WILDLIFE_INDEX     = os.path.join(REPO, "wildlife.json")
PHOTOS_DIR         = os.path.join(REPO, "photos")

# Tab definitions — order matters for the nav bar
TABS = [
    {"id": "overview", "label": "Overview",       "route": "/",        "icon": "📊"},
    {"id": "intake",   "label": "Intake",         "route": "/intake",  "icon": "📥"},
    {"id": "photos",   "label": "Photos",         "route": "/photos",  "icon": "📷"},
    {"id": "edit",     "label": "Edit & Preview",  "route": "/edit",    "icon": "✏️"},
    {"id": "publish",  "label": "Publish",         "route": "/publish", "icon": "🚀"},
]

# Required fields for promotion readiness (per kingdom)
PLANT_REQUIRED = ["common_name", "scientific_name", "description", "native_status"]
WILDLIFE_REQUIRED = ["common_name", "scientific_name", "description", "animal_group"]


# ╔══════════════════════════════════════════════════════════════════════════╗
# ║  DATA ACCESS                                                           ║
# ╚══════════════════════════════════════════════════════════════════════════╝

def _load(path):
    """Read and parse a JSON file. Returns empty dict/list on missing file."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return {}
    except json.JSONDecodeError as e:
        print(f"[WARN] Bad JSON in {path}: {e}")
        return {}


def _species_has_hero(species_id, credits):
    """Check if a species has at least one hero photo in photo_credits."""
    # photo_credits.json structure: dict keyed by species_id → list of photo dicts
    # Each photo dict has "hero": true/false
    photos = credits.get(species_id, [])
    if isinstance(photos, list):
        return any(p.get("hero") for p in photos)
    # Fallback: if credits is a flat list, scan for matching species
    return False


def _hero_on_disk(species_id):
    """Check if the hero photo file exists on disk."""
    hero_dir = os.path.join(PHOTOS_DIR, species_id)
    if not os.path.isdir(hero_dir):
        return False
    return any(f.lower().endswith((".jpg", ".jpeg", ".png", ".webp"))
               for f in os.listdir(hero_dir))


def _check_required_fields(species_data, required):
    """Return list of missing required field names."""
    missing = []
    for field in required:
        val = species_data.get(field, "")
        if not val or (isinstance(val, str) and not val.strip()):
            missing.append(field)
    return missing


def _get_all_photographer_logins(credits):
    """Extract all unique photographer logins from photo_credits."""
    logins = set()
    for species_id, photos in credits.items():
        if isinstance(photos, list):
            for p in photos:
                login = p.get("login") or p.get("photographer") or p.get("user")
                if login:
                    logins.add(login)
    return logins


def get_overview_data():
    """
    Compute the full Overview payload.

    Returns dict with plants, wildlife, photographers, and attention sections.
    This is the single function that powers the Overview tab.
    """
    plants = _load(PLANT_SIGNAGE)
    wildlife = _load(WILDLIFE_SIGNAGE)
    credits = _load(PHOTO_CREDITS)
    names = _load(PHOTOGRAPHER_NAMES)

    def analyze_kingdom(signage, required_fields, kingdom_label):
        by_status = {}
        attention = []

        for sid, info in signage.items():
            status = info.get("status", "unknown")
            by_status.setdefault(status, []).append(sid)

            # Attention checks for spotted species
            if status == "spotted":
                issues = []

                # Hero photo check
                if not _species_has_hero(sid, credits):
                    issues.append("No hero photo")
                elif not _hero_on_disk(sid):
                    issues.append("Hero not on disk")

                # Required fields check
                missing = _check_required_fields(info, required_fields)
                if missing:
                    issues.append(f"Missing: {', '.join(missing)}")

                if issues:
                    attention.append({
                        "id": sid,
                        "name": info.get("common_name", sid),
                        "scientific": info.get("scientific_name", ""),
                        "issues": issues,
                    })

        status_counts = {k: len(v) for k, v in by_status.items()}
        return {
            "total": len(signage),
            "by_status": status_counts,
            "attention": sorted(attention, key=lambda x: x["id"]),
        }

    # Photographer analysis
    all_logins = _get_all_photographer_logins(credits)
    resolved = {login for login in all_logins if login in names}
    unresolved = sorted(all_logins - resolved)

    # Count total photos
    total_photos = sum(
        len(photos) if isinstance(photos, list) else 0
        for photos in credits.values()
    )

    return {
        "plants": analyze_kingdom(plants, PLANT_REQUIRED, "Plants"),
        "wildlife": analyze_kingdom(wildlife, WILDLIFE_REQUIRED, "Wildlife"),
        "photographers": {
            "total_logins": len(all_logins),
            "resolved": len(resolved),
            "unresolved": unresolved,
            "total_photos": total_photos,
        },
    }


def get_species_list(kingdom):
    """
    Return a list of species for a kingdom, sorted by ID.

    Used by Intake, Edit, Publish tabs to populate species pickers.
    kingdom: "plants" or "wildlife"
    """
    path = PLANT_SIGNAGE if kingdom == "plants" else WILDLIFE_SIGNAGE
    signage = _load(path)
    result = []
    for sid in sorted(signage.keys()):
        info = signage[sid]
        result.append({
            "id": sid,
            "common_name": info.get("common_name", ""),
            "scientific_name": info.get("scientific_name", ""),
            "status": info.get("status", "unknown"),
        })
    return result


# ╔══════════════════════════════════════════════════════════════════════════╗
# ║  API HANDLERS                                                          ║
# ║                                                                        ║
# ║  Each handler takes (params) and returns a JSON-serializable dict.     ║
# ║  Add new endpoints here, then register in API_ROUTES below.            ║
# ╚══════════════════════════════════════════════════════════════════════════╝

def handle_api_overview(params):
    """GET /api/overview — full dashboard stats."""
    return get_overview_data()


def handle_api_species_list(params):
    """GET /api/species?kingdom=plants — species list for pickers."""
    kingdom = params.get("kingdom", ["plants"])[0]
    return {"kingdom": kingdom, "species": get_species_list(kingdom)}


# ── Future API stubs ───────────────────────────────────────────────────────
# These return descriptive placeholders. Replace with real logic as each
# tab gets built out. The route is already wired up.

def handle_api_intake_check(params):
    """POST /api/intake/check — duplicate check before minting. STUB."""
    return {"status": "stub", "message": "Intake duplicate check not yet implemented."}

def handle_api_photos_species(params):
    """GET /api/photos/species?id=PSBP-00001 — photos for a species. STUB."""
    return {"status": "stub", "message": "Photos API not yet implemented."}

def handle_api_preview(params):
    """GET /api/preview?id=PSBP-00001 — HTML preview. STUB."""
    return {"status": "stub", "message": "Preview API not yet implemented."}

def handle_api_publish_ready(params):
    """GET /api/publish/ready?id=PSBP-00001 — readiness checklist. STUB."""
    return {"status": "stub", "message": "Publish readiness API not yet implemented."}


# ╔══════════════════════════════════════════════════════════════════════════╗
# ║  HTML SHELL                                                            ║
# ║                                                                        ║
# ║  page_shell() wraps tab content in the shared nav, CSS, and brand.     ║
# ╚══════════════════════════════════════════════════════════════════════════╝

BRAND_CSS = """
:root {
    --green-deep:  #1a3a1f;
    --green-mid:   #2d6a35;
    --green-light: #4a9e56;
    --gold:        #c5922a;
    --cream:       #faf8f3;
    --gray-100:    #f5f5f5;
    --gray-200:    #e8e8e8;
    --gray-400:    #999;
    --gray-600:    #555;
    --gray-800:    #222;
    --status-research: #78909c;
    --status-spotted:  #c5922a;
    --status-html:     #2d6a35;
    --radius:      6px;
    --shadow:      0 1px 3px rgba(0,0,0,0.08);
}
* { box-sizing: border-box; margin: 0; padding: 0; }
body {
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    background: var(--cream);
    color: var(--gray-800);
    line-height: 1.5;
}

/* ── Header ─────────────────────────────────────────────────── */
header {
    background: var(--green-deep);
    color: white;
    padding: 14px 24px;
    display: flex;
    align-items: center;
    gap: 12px;
}
header h1 {
    font-size: 18px;
    font-weight: 600;
    letter-spacing: 0.3px;
}
header .subtitle {
    font-size: 13px;
    opacity: 0.7;
    margin-left: auto;
}

/* ── Tab Nav ────────────────────────────────────────────────── */
nav.tabs {
    background: white;
    border-bottom: 1px solid var(--gray-200);
    display: flex;
    gap: 0;
    padding: 0 16px;
    box-shadow: var(--shadow);
}
nav.tabs a {
    text-decoration: none;
    color: var(--gray-600);
    padding: 12px 20px;
    font-size: 14px;
    font-weight: 500;
    border-bottom: 2px solid transparent;
    transition: all 0.15s;
    display: flex;
    align-items: center;
    gap: 6px;
}
nav.tabs a:hover {
    color: var(--green-mid);
    background: var(--gray-100);
}
nav.tabs a.active {
    color: var(--green-deep);
    border-bottom-color: var(--green-mid);
}
nav.tabs a .tab-icon { font-size: 15px; }

/* ── Main Content ───────────────────────────────────────────── */
main {
    max-width: 1100px;
    margin: 0 auto;
    padding: 24px;
}

/* ── Cards ──────────────────────────────────────────────────── */
.card {
    background: white;
    border-radius: var(--radius);
    box-shadow: var(--shadow);
    padding: 20px;
    margin-bottom: 16px;
}
.card h2 {
    font-size: 15px;
    font-weight: 600;
    color: var(--green-deep);
    margin-bottom: 14px;
    display: flex;
    align-items: center;
    gap: 8px;
}

/* ── Two-column grid ────────────────────────────────────────── */
.grid-2 {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 16px;
}
@media (max-width: 700px) {
    .grid-2 { grid-template-columns: 1fr; }
}

/* ── Funnel bars ────────────────────────────────────────────── */
.funnel-row {
    display: flex;
    align-items: center;
    margin-bottom: 10px;
    gap: 10px;
}
.funnel-label {
    width: 80px;
    font-size: 13px;
    font-weight: 500;
    text-transform: capitalize;
}
.funnel-bar-track {
    flex: 1;
    height: 24px;
    background: var(--gray-100);
    border-radius: 4px;
    overflow: hidden;
    position: relative;
}
.funnel-bar-fill {
    height: 100%;
    border-radius: 4px;
    transition: width 0.4s ease;
    min-width: 2px;
}
.funnel-bar-fill.research { background: var(--status-research); }
.funnel-bar-fill.spotted  { background: var(--status-spotted); }
.funnel-bar-fill.html     { background: var(--status-html); }
.funnel-count {
    width: 40px;
    font-size: 14px;
    font-weight: 600;
    text-align: right;
}
.funnel-total {
    font-size: 22px;
    font-weight: 700;
    color: var(--green-deep);
    margin-bottom: 4px;
}
.funnel-total-label {
    font-size: 12px;
    color: var(--gray-400);
    text-transform: uppercase;
    letter-spacing: 0.5px;
    margin-bottom: 14px;
}

/* ── Attention list ─────────────────────────────────────────── */
.attention-item {
    display: flex;
    align-items: flex-start;
    gap: 10px;
    padding: 10px 0;
    border-bottom: 1px solid var(--gray-100);
    font-size: 13px;
}
.attention-item:last-child { border-bottom: none; }
.attention-id {
    font-family: "SF Mono", Menlo, monospace;
    font-size: 12px;
    color: var(--gray-600);
    background: var(--gray-100);
    padding: 2px 6px;
    border-radius: 3px;
    white-space: nowrap;
}
.attention-name {
    font-weight: 500;
    color: var(--gray-800);
    min-width: 140px;
}
.attention-issues {
    color: var(--gold);
    font-size: 12px;
}
.attention-empty {
    color: var(--gray-400);
    font-style: italic;
    padding: 12px 0;
    font-size: 13px;
}

/* ── Photographer badges ────────────────────────────────────── */
.photog-resolved {
    display: inline-block;
    background: #e8f5e9;
    color: var(--green-mid);
    padding: 2px 8px;
    border-radius: 3px;
    font-size: 12px;
    margin: 2px;
}
.photog-unresolved {
    display: inline-block;
    background: #fff3e0;
    color: #e65100;
    padding: 2px 8px;
    border-radius: 3px;
    font-size: 12px;
    font-family: "SF Mono", Menlo, monospace;
    margin: 2px;
}

/* ── Stub tab content ───────────────────────────────────────── */
.stub-banner {
    background: white;
    border-radius: var(--radius);
    box-shadow: var(--shadow);
    padding: 32px;
    text-align: center;
}
.stub-banner h2 {
    font-size: 18px;
    color: var(--green-deep);
    margin-bottom: 8px;
    justify-content: center;
}
.stub-banner .stub-status {
    display: inline-block;
    background: var(--gray-100);
    color: var(--gray-600);
    padding: 3px 10px;
    border-radius: 12px;
    font-size: 12px;
    font-weight: 500;
    margin-bottom: 16px;
}
.stub-spec {
    text-align: left;
    max-width: 560px;
    margin: 0 auto;
}
.stub-spec h3 {
    font-size: 14px;
    font-weight: 600;
    color: var(--green-deep);
    margin: 16px 0 6px;
}
.stub-spec ul {
    padding-left: 20px;
    font-size: 13px;
    color: var(--gray-600);
}
.stub-spec li { margin-bottom: 4px; }

/* ── Mode toggle (plant/wildlife) ───────────────────────────── */
.mode-toggle {
    display: flex;
    gap: 0;
    background: var(--gray-100);
    border-radius: var(--radius);
    padding: 3px;
    width: fit-content;
    margin-bottom: 20px;
}
.mode-toggle button {
    padding: 6px 18px;
    border: none;
    background: transparent;
    border-radius: 4px;
    font-size: 13px;
    font-weight: 500;
    cursor: pointer;
    color: var(--gray-600);
    transition: all 0.15s;
}
.mode-toggle button.active {
    background: white;
    color: var(--green-deep);
    box-shadow: var(--shadow);
}

/* ── Stat badges ────────────────────────────────────────────── */
.stat-row {
    display: flex;
    gap: 16px;
    flex-wrap: wrap;
    margin-bottom: 16px;
}
.stat-badge {
    background: var(--gray-100);
    border-radius: var(--radius);
    padding: 10px 16px;
    text-align: center;
    min-width: 90px;
}
.stat-badge .stat-num {
    font-size: 20px;
    font-weight: 700;
    color: var(--green-deep);
}
.stat-badge .stat-label {
    font-size: 11px;
    color: var(--gray-400);
    text-transform: uppercase;
    letter-spacing: 0.3px;
}

/* ── Loading state ──────────────────────────────────────────── */
.loading {
    text-align: center;
    padding: 40px;
    color: var(--gray-400);
    font-size: 14px;
}
"""


def page_shell(active_tab_id, body_html):
    """Wrap tab content in the shared page shell (header, nav, CSS)."""
    nav_items = ""
    for tab in TABS:
        cls = "active" if tab["id"] == active_tab_id else ""
        nav_items += (
            f'<a href="{tab["route"]}" class="{cls}">'
            f'<span class="tab-icon">{tab["icon"]}</span>'
            f'{tab["label"]}</a>\n'
        )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>PSBP Species Manager</title>
    <style>{BRAND_CSS}</style>
</head>
<body>
    <header>
        <h1>🌿 PSBP Species Manager</h1>
        <span class="subtitle">port {PORT}</span>
    </header>
    <nav class="tabs">
        {nav_items}
    </nav>
    <main>
        {body_html}
    </main>
</body>
</html>"""


# ╔══════════════════════════════════════════════════════════════════════════╗
# ║  TAB RENDERERS                                                         ║
# ║                                                                        ║
# ║  Each render_*() returns an HTML string for the <main> content.        ║
# ║  Add new tabs here and register in PAGE_ROUTES below.                  ║
# ╚══════════════════════════════════════════════════════════════════════════╝

def render_overview():
    """Overview tab — pipeline funnel and attention flags. Loads via fetch()."""
    return """
    <div id="overview-loading" class="loading">Loading dashboard…</div>
    <div id="overview-content" style="display:none;">

        <!-- Quick stats row -->
        <div class="stat-row" id="stat-row"></div>

        <!-- Pipeline funnels — two columns -->
        <div class="grid-2">
            <div class="card" id="plants-funnel">
                <h2>🌱 Plants</h2>
                <div id="plants-funnel-body"></div>
            </div>
            <div class="card" id="wildlife-funnel">
                <h2>🦎 Wildlife</h2>
                <div id="wildlife-funnel-body"></div>
            </div>
        </div>

        <!-- Attention items -->
        <div class="grid-2">
            <div class="card">
                <h2>⚠️ Plants Needing Attention</h2>
                <div id="plants-attention"></div>
            </div>
            <div class="card">
                <h2>⚠️ Wildlife Needing Attention</h2>
                <div id="wildlife-attention"></div>
            </div>
        </div>

        <!-- Photographers -->
        <div class="card">
            <h2>📸 Photographer Registry</h2>
            <div id="photog-status"></div>
        </div>
    </div>

    <script>
    async function loadOverview() {
        try {
            const resp = await fetch('/api/overview');
            const data = await resp.json();
            renderOverview(data);
        } catch (err) {
            document.getElementById('overview-loading').textContent =
                'Error loading data: ' + err.message;
        }
    }

    function renderOverview(data) {
        document.getElementById('overview-loading').style.display = 'none';
        document.getElementById('overview-content').style.display = 'block';

        // Quick stats
        const totalSpecies = data.plants.total + data.wildlife.total;
        const totalPublished = (data.plants.by_status.html || 0) +
                               (data.wildlife.by_status.html || 0);
        const totalSpotted = (data.plants.by_status.spotted || 0) +
                             (data.wildlife.by_status.spotted || 0);
        document.getElementById('stat-row').innerHTML = `
            <div class="stat-badge">
                <div class="stat-num">${totalSpecies}</div>
                <div class="stat-label">Total Species</div>
            </div>
            <div class="stat-badge">
                <div class="stat-num">${totalPublished}</div>
                <div class="stat-label">Published</div>
            </div>
            <div class="stat-badge">
                <div class="stat-num">${totalSpotted}</div>
                <div class="stat-label">Spotted</div>
            </div>
            <div class="stat-badge">
                <div class="stat-num">${data.photographers.total_photos}</div>
                <div class="stat-label">Photos</div>
            </div>
            <div class="stat-badge">
                <div class="stat-num">${data.photographers.total_logins}</div>
                <div class="stat-label">Photographers</div>
            </div>
        `;

        // Funnels
        renderFunnel('plants-funnel-body', data.plants);
        renderFunnel('wildlife-funnel-body', data.wildlife);

        // Attention
        renderAttention('plants-attention', data.plants.attention);
        renderAttention('wildlife-attention', data.wildlife.attention);

        // Photographers
        renderPhotographers(data.photographers);
    }

    function renderFunnel(containerId, kingdomData) {
        const el = document.getElementById(containerId);
        const total = kingdomData.total || 1;
        const statuses = ['research', 'spotted', 'html'];
        let html = `<div class="funnel-total">${kingdomData.total}</div>`;
        html += `<div class="funnel-total-label">total species</div>`;

        for (const status of statuses) {
            const count = kingdomData.by_status[status] || 0;
            const pct = Math.round((count / total) * 100);
            html += `
                <div class="funnel-row">
                    <span class="funnel-label">${status}</span>
                    <div class="funnel-bar-track">
                        <div class="funnel-bar-fill ${status}"
                             style="width: ${Math.max(pct, 1)}%"></div>
                    </div>
                    <span class="funnel-count">${count}</span>
                </div>
            `;
        }

        // Show any other statuses that aren't in the standard three
        for (const [status, count] of Object.entries(kingdomData.by_status)) {
            if (!statuses.includes(status)) {
                const pct = Math.round((count / total) * 100);
                html += `
                    <div class="funnel-row">
                        <span class="funnel-label">${status}</span>
                        <div class="funnel-bar-track">
                            <div class="funnel-bar-fill"
                                 style="width: ${Math.max(pct, 1)}%; background: #aaa"></div>
                        </div>
                        <span class="funnel-count">${count}</span>
                    </div>
                `;
            }
        }
        el.innerHTML = html;
    }

    function renderAttention(containerId, items) {
        const el = document.getElementById(containerId);
        if (!items || items.length === 0) {
            el.innerHTML = '<div class="attention-empty">All clear — nothing needs attention.</div>';
            return;
        }
        let html = '';
        for (const item of items) {
            html += `
                <div class="attention-item">
                    <span class="attention-id">${item.id}</span>
                    <span class="attention-name">${item.name}</span>
                    <span class="attention-issues">${item.issues.join(' · ')}</span>
                </div>
            `;
        }
        el.innerHTML = html;
    }

    function renderPhotographers(photog) {
        const el = document.getElementById('photog-status');
        let html = `<div style="margin-bottom: 10px; font-size: 13px;">
            <strong>${photog.resolved}</strong> of <strong>${photog.total_logins}</strong>
            photographer handles resolved to real names
        </div>`;

        if (photog.unresolved.length > 0) {
            html += '<div style="margin-top: 8px;">';
            html += '<span style="font-size: 12px; color: var(--gray-600); margin-right: 6px;">Unresolved:</span>';
            for (const handle of photog.unresolved) {
                html += `<span class="photog-unresolved">${handle}</span> `;
            }
            html += '</div>';
            html += `<div style="margin-top: 8px; font-size: 12px; color: var(--gray-400);">
                Add real names in <code>data/sources/photographer_names.json</code>, then propagate.
            </div>`;
        } else {
            html += '<div class="photog-resolved">All handles resolved ✓</div>';
        }
        el.innerHTML = html;
    }

    loadOverview();
    </script>
    """


def render_stub(tab_id, title, features, pipeline_step):
    """
    Render a stub tab with its planned feature spec.

    This gives Randy a preview of what each tab will do, and gives
    future Claudes a clear build spec right in the UI.
    """
    li_items = "\n".join(f"<li>{f}</li>" for f in features)
    return f"""
    <div class="stub-banner">
        <h2>{title}</h2>
        <div class="stub-status">Coming next · {pipeline_step}</div>
        <div class="stub-spec">
            <h3>Planned features</h3>
            <ul>{li_items}</ul>
            <h3>How to build this tab</h3>
            <ul>
                <li>Add API handlers in the <strong>API HANDLERS</strong> section</li>
                <li>Replace this function's content in the <strong>TAB RENDERERS</strong> section</li>
                <li>See <code>SPECIES_MANAGER.md</code> for the full spec and data flow</li>
            </ul>
        </div>
    </div>
    """


def render_intake():
    return render_stub("intake", "📥 Intake — Import Species from iNat", [
        "Paste an iNat observation URL → auto-extract taxon, names, photo",
        "Fuzzy duplicate check against existing signage entries",
        "Mint next available PSBP-xxxxx ID",
        "Plant / Wildlife mode toggle (determines target JSON)",
        "Write new entry to signage JSON with status <code>spotted</code>",
    ], "Pipeline step 1 of 4")


def render_photos():
    return render_stub("photos", "📷 Photos — Triage & Review", [
        "<strong>Triage mode:</strong> Scan iNat for CC-licensed photos, promote to hero/gallery/skip/block",
        "<strong>Review mode:</strong> Crown heroes, set focus points, tag roles (leaf/flower/bark/fruit)",
        "Gallery ordering and trash management",
        "Photographer name resolution status per species",
        "Replaces <code>photo_workbench.py</code> (port 8001) and <code>photo_review.py</code> (port 8000)",
    ], "Pipeline step 2 of 4")


def render_edit():
    return render_stub("edit", "✏️ Edit & Preview — Signage Content", [
        "Species picker filtered by status (spotted + html)",
        "Editable form for all signage fields (names, description, quick hits, native status…)",
        "Live HTML preview panel — see the page before publishing",
        "Field-level validation with required fields highlighted",
        "Save edits back to signage JSON",
    ], "Pipeline step 3 of 4")


def render_publish():
    return render_stub("publish", "🚀 Publish — Promote & Demote", [
        "Species list with readiness indicators (hero ✓ credits ✓ fields ✓)",
        "One-click promote: spotted → html (generate HTML, stamp credits, update search index)",
        "One-click demote: html → spotted (delete HTML, clean search index)",
        "Re-generate all button with confirmation gate",
        "Replaces <code>plant_publisher.py</code> (port 8701) and <code>wildlife_publisher.py</code> (port 8702)",
    ], "Pipeline step 4 of 4")


# ╔══════════════════════════════════════════════════════════════════════════╗
# ║  HTTP SERVER                                                           ║
# ║                                                                        ║
# ║  Routing: PAGE_ROUTES for HTML pages, API_ROUTES for JSON endpoints.   ║
# ║  Add new routes to the appropriate dict.                               ║
# ╚══════════════════════════════════════════════════════════════════════════╝

# Page routes: path → (tab_id, render_function)
PAGE_ROUTES = {
    "/":        ("overview", render_overview),
    "/intake":  ("intake",   render_intake),
    "/photos":  ("photos",   render_photos),
    "/edit":    ("edit",     render_edit),
    "/publish": ("publish",  render_publish),
}

# API routes: path → handler_function
API_ROUTES = {
    "/api/overview":       handle_api_overview,
    "/api/species":        handle_api_species_list,
    "/api/intake/check":   handle_api_intake_check,
    "/api/photos/species": handle_api_photos_species,
    "/api/preview":        handle_api_preview,
    "/api/publish/ready":  handle_api_publish_ready,
}


class DashboardHandler(BaseHTTPRequestHandler):
    """HTTP request handler for the species manager dashboard."""

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        params = parse_qs(parsed.query)

        # API routes
        if path in API_ROUTES:
            try:
                result = API_ROUTES[path](params)
                self._json_response(200, result)
            except Exception as e:
                self._json_response(500, {"error": str(e)})
            return

        # Page routes
        if path in PAGE_ROUTES:
            tab_id, render_fn = PAGE_ROUTES[path]
            body = render_fn()
            html = page_shell(tab_id, body)
            self._html_response(200, html)
            return

        # 404
        self._html_response(404, page_shell("overview",
            '<div class="stub-banner"><h2>404 — Page not found</h2></div>'))

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        params = parse_qs(parsed.query)

        # Read POST body
        content_len = int(self.headers.get("Content-Length", 0))
        if content_len > 0:
            body = self.rfile.read(content_len)
            try:
                params["_body"] = json.loads(body)
            except json.JSONDecodeError:
                pass

        if path in API_ROUTES:
            try:
                result = API_ROUTES[path](params)
                self._json_response(200, result)
            except Exception as e:
                self._json_response(500, {"error": str(e)})
            return

        self._json_response(404, {"error": "Not found"})

    def _json_response(self, status, data):
        body = json.dumps(data, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", len(body))
        self.end_headers()
        self.wfile.write(body)

    def _html_response(self, status, html):
        body = html.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", len(body))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        """Quieter logging — just method and path."""
        print(f"  {args[0]}" if args else "")


def main():
    port = PORT
    if "--port" in sys.argv:
        idx = sys.argv.index("--port")
        if idx + 1 < len(sys.argv):
            port = int(sys.argv[idx + 1])

    server = HTTPServer(("", port), DashboardHandler)
    print(f"╔══════════════════════════════════════════════════╗")
    print(f"║  PSBP Species Manager                           ║")
    print(f"║  http://localhost:{port:<5}                       ║")
    print(f"╠══════════════════════════════════════════════════╣")
    print(f"║  Overview .... http://localhost:{port}/           ║")
    print(f"║  Intake ...... http://localhost:{port}/intake     ║")
    print(f"║  Photos ...... http://localhost:{port}/photos     ║")
    print(f"║  Edit ........ http://localhost:{port}/edit       ║")
    print(f"║  Publish ..... http://localhost:{port}/publish    ║")
    print(f"╚══════════════════════════════════════════════════╝")
    print(f"  Data: {REPO}")
    print(f"  Ctrl+C to stop\n")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down.")
        server.server_close()


if __name__ == "__main__":
    main()
