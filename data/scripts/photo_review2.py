#!/usr/bin/env python3
"""
photo_review.py
===============
Local browser tool for reviewing species photos.

USAGE:
  cd ~/Downloads/psbp-photo-run
  python3 photo_review.py

  Then open: http://localhost:8000

WHAT IT DOES:
  - Browse species, see all downloaded photos
  - Assign roles (whole, portrait, flight, feeding, juvenile, display, habitat, gallery)
  - Crown a hero (one per species)
  - Click on the hero to set the focus/crop point
  - Trash junk photos (deletes file + removes from registry)
  - Saves back to photo_credits.json

FILES NEEDED (same directory):
  - photo_credits.json
  - photos/  (subfolder tree from the download script)
"""

import http.server
import json
import os
import sys
import urllib.parse

PORT = 8000
# === The ONE line to change if you ever move the repo. Run this from ANY folder. ===
REPO = "/Users/fiona/Documents/GitHub/explore"
PHOTO_CREDITS = os.path.join(REPO, "data", "sources", "photo_credits.json")
PHOTOS_DIR = os.path.join(REPO, "photos")

# Wildlife roles
WILDLIFE_ROLES = ["whole", "portrait", "flight", "feeding", "juvenile", "display", "habitat", "gallery"]
PLANT_ROLES = ["whole", "leaf", "flower", "fruit", "bark", "gallery"]
BUTTERFLY_ROLES = ["adult", "caterpillar", "gallery", "whole"]


def load_credits():
    with open(PHOTO_CREDITS, encoding="utf-8") as f:
        return json.load(f)


def save_credits(data):
    data["meta"]["photo_count"] = len(data["photos"])
    with open(PHOTO_CREDITS, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


HTML_PAGE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>PSBP Photo Review</title>
<style>
* { box-sizing: border-box; margin: 0; padding: 0; }
body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; background: #1a1a1a; color: #e0e0e0; }

.header { background: #1a3a1f; padding: 12px 24px; display: flex; align-items: center; gap: 16px; position: sticky; top: 0; z-index: 100; }
.header h1 { font-size: 18px; color: #4a9e56; white-space: nowrap; }
.header select { padding: 8px 12px; font-size: 14px; border-radius: 6px; border: 1px solid #4a9e56; background: #2a2a2a; color: #e0e0e0; min-width: 300px; }
.header button { padding: 8px 20px; border-radius: 6px; border: none; cursor: pointer; font-weight: 600; font-size: 14px; }
.btn-save { background: #4a9e56; color: white; }
.btn-save:hover { background: #2d6a35; }
.btn-save.has-changes { background: #c5922a; animation: pulse 1.5s infinite; }
@keyframes pulse { 0%,100% { opacity: 1; } 50% { opacity: 0.7; } }
.status-msg { font-size: 13px; color: #aaa; margin-left: auto; }

.grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(280px, 1fr)); gap: 16px; padding: 24px; }

.card { background: #2a2a2a; border-radius: 10px; overflow: hidden; border: 2px solid transparent; transition: border-color 0.2s; }
.card.is-hero { border-color: #c5922a; }
.card.is-trashed { opacity: 0.3; pointer-events: none; }

.card-img-wrap { position: relative; width: 100%; padding-top: 75%; overflow: hidden; cursor: pointer; background: #111; }
.card-img-wrap img { position: absolute; top: 0; left: 0; width: 100%; height: 100%; object-fit: cover; }
.hero-badge { position: absolute; top: 8px; right: 8px; background: #c5922a; color: #1a1a1a; padding: 3px 10px; border-radius: 12px; font-size: 11px; font-weight: 700; text-transform: uppercase; }

.card-body { padding: 12px; }
.card-meta { font-size: 12px; color: #999; margin-bottom: 8px; }
.card-meta .photographer { color: #4a9e56; font-weight: 600; }

.roles { display: flex; flex-wrap: wrap; gap: 6px; margin-bottom: 10px; }
.roles label { font-size: 12px; padding: 3px 8px; border-radius: 4px; background: #333; cursor: pointer; user-select: none; display: flex; align-items: center; gap: 4px; transition: background 0.15s; }
.roles label.checked { background: #2d6a35; color: white; }

.card-actions { display: flex; gap: 8px; align-items: center; }
.card-actions button { padding: 4px 12px; border-radius: 4px; border: none; cursor: pointer; font-size: 12px; }
.btn-hero { background: #c5922a; color: #1a1a1a; font-weight: 600; }
.btn-hero.active { background: #e8d5a0; }
.btn-trash { background: #8b2020; color: white; }
.btn-primary { background: #2d6a35; color: white; font-size: 11px; }

/* Focus overlay */
.focus-overlay { display: none; position: fixed; top: 0; left: 0; right: 0; bottom: 0; background: rgba(0,0,0,0.85); z-index: 200; justify-content: center; align-items: center; flex-direction: column; }
.focus-overlay.active { display: flex; }
.focus-overlay img { max-width: 90vw; max-height: 75vh; cursor: crosshair; }
.focus-overlay .instructions { color: #c5922a; margin-bottom: 12px; font-size: 14px; }
.focus-overlay .focus-dot { position: absolute; width: 20px; height: 20px; border: 3px solid #c5922a; border-radius: 50%; transform: translate(-50%, -50%); pointer-events: none; box-shadow: 0 0 0 2px rgba(0,0,0,0.5); }
.focus-overlay .btn-close { margin-top: 16px; padding: 8px 24px; background: #333; color: white; border: none; border-radius: 6px; cursor: pointer; font-size: 14px; }

.empty { text-align: center; padding: 80px 24px; color: #666; font-size: 16px; }
.summary { padding: 8px 24px; font-size: 13px; color: #888; border-bottom: 1px solid #333; }
</style>
</head>
<body>

<div class="header">
  <h1>🌿 PSBP Photo Review</h1>
  <select id="speciesSelect" onchange="loadSpecies()"></select>
  <button class="btn-save" id="saveBtn" onclick="saveChanges()">Save Changes</button>
  <span class="status-msg" id="statusMsg">Loading...</span>
</div>

<div class="summary" id="summary"></div>
<div class="grid" id="grid"></div>

<div class="focus-overlay" id="focusOverlay">
  <div class="instructions">Click on the subject to set the crop focus point</div>
  <div style="position:relative; display:inline-block;">
    <img id="focusImg" onclick="setFocus(event)">
    <div class="focus-dot" id="focusDot"></div>
  </div>
  <button class="btn-close" onclick="closeFocus()">Done</button>
</div>

<script>
let allData = null;
let currentSpecies = null;
let changes = {};
let trashed = new Set();
let hasUnsaved = false;

async function init() {
  const resp = await fetch('/api/data');
  allData = await resp.json();
  buildSpeciesDropdown();
  if (currentSpecies) loadSpecies();
  else document.getElementById('statusMsg').textContent = 'Select a species';
}

function buildSpeciesDropdown() {
  const sel = document.getElementById('speciesSelect');
  // Group photos by species — only show species with a local subfolder
  const localFolders = new Set(allData._local_folders || []);
  const species = {};
  allData.photos.forEach(p => {
    if (!p.psbp_id) return;
    if (!localFolders.has(p.psbp_id)) return; // skip if no local subfolder
    if (!species[p.psbp_id]) species[p.psbp_id] = { id: p.psbp_id, name: p.common_name, count: 0, hasHero: false };
    species[p.psbp_id].count++;
    if (p.hero) species[p.psbp_id].hasHero = true;
  });
  const sorted = Object.values(species).sort((a, b) => a.id < b.id ? 1 : -1);
  sel.innerHTML = '<option value="">— Select species —</option>';
  sorted.forEach(s => {
    const heroMark = s.hasHero ? '' : ' ⚠️ no hero';
    sel.innerHTML += `<option value="${s.id}">${s.id} — ${s.name} (${s.count} photos${heroMark})</option>`;
  });
}

function loadSpecies() {
  const sel = document.getElementById('speciesSelect');
  currentSpecies = sel.value;
  if (!currentSpecies) return;

  const photos = allData.photos.filter(p => p.psbp_id === currentSpecies && !trashed.has(p.photo_id));
  const grid = document.getElementById('grid');
  const summary = document.getElementById('summary');

  if (!photos.length) {
    grid.innerHTML = '<div class="empty">No photos for this species</div>';
    summary.textContent = '';
    return;
  }

  const heroPhoto = photos.find(p => p.hero);
  const type = photos[0].type || 'Wildlife';
  const roles = type === 'Plant' ? ['whole','leaf','flower','fruit','bark','gallery'] :
    (photos[0].common_name && ['Butterfly','Moth'].some(g => (photos[0].tags||[]).includes(g))) ?
    ['adult','caterpillar','whole','gallery'] :
    ['whole','portrait','flight','feeding','juvenile','display','habitat','gallery'];

  summary.textContent = `${currentSpecies} — ${photos[0].common_name} — ${photos.length} photos — Hero: ${heroPhoto ? '✓ ' + heroPhoto.filename : '⚠️ none set'}`;

  grid.innerHTML = '';
  photos.forEach(p => {
    const isHero = p.hero;
    const photoRoles = changes[p.photo_id]?.role || p.role || [];

    const card = document.createElement('div');
    card.className = 'card' + (isHero ? ' is-hero' : '');
    card.dataset.photoId = p.photo_id;

    // Determine image path
    const imgPath = `/photos/${p.psbp_id}/${p.filename}`;

    card.innerHTML = `
      <div class="card-img-wrap" onclick="${isHero ? `openFocus('${p.photo_id}', '${imgPath}', '${p.focus || '50% 50%'}')` : ''}">
        <img src="${imgPath}" loading="lazy" onerror="this.src='data:image/svg+xml,<svg xmlns=%22http://www.w3.org/2000/svg%22 viewBox=%220 0 1 1%22><rect fill=%22%23333%22 width=%221%22 height=%221%22/><text x=%22.5%22 y=%22.6%22 fill=%22%23666%22 font-size=%22.15%22 text-anchor=%22middle%22>Not found</text></svg>'">
        ${isHero ? '<div class="hero-badge">★ Hero</div>' : ''}
      </div>
      <div class="card-body">
        <div class="card-meta">
          <span class="photographer">📷 ${p.photographer}</span> · ${p.license || 'unknown'} · ${p.filename}
          ${p.focus && isHero ? ` · Focus: ${p.focus}` : ''}
        </div>
        <div class="roles">
          ${roles.map(r => `
            <label class="${photoRoles.includes(r) ? 'checked' : ''}" onclick="toggleRole('${p.photo_id}', '${r}', this)">
              ${r}
            </label>
          `).join('')}
        </div>
        <div class="card-actions">
          <button class="btn-hero ${isHero ? 'active' : ''}" onclick="makeHero('${p.photo_id}')">
            ${isHero ? '★ Hero' : 'Make Hero'}
          </button>
          <button class="btn-trash" onclick="trashPhoto('${p.photo_id}', '${p.psbp_id}', '${p.filename}')">🗑 Trash</button>
        </div>
      </div>
    `;
    grid.appendChild(card);
  });

  document.getElementById('statusMsg').textContent = `${photos.length} photos loaded`;
}

function toggleRole(photoId, role, label) {
  if (!changes[photoId]) {
    const photo = allData.photos.find(p => p.photo_id === photoId);
    changes[photoId] = { role: [...(photo.role || [])] };
  }
  const roles = changes[photoId].role;
  const idx = roles.indexOf(role);
  if (idx >= 0) {
    roles.splice(idx, 1);
    label.classList.remove('checked');
  } else {
    roles.push(role);
    label.classList.add('checked');
  }
  markUnsaved();
}

function makeHero(photoId) {
  // Remove hero from all photos of this species
  allData.photos.forEach(p => {
    if (p.psbp_id === currentSpecies) {
      p.hero = false;
      if (p.focus && p.photo_id !== photoId) {
        // keep focus data but it's now dormant
      }
    }
  });
  // Set new hero
  const photo = allData.photos.find(p => p.photo_id === photoId);
  if (photo) {
    photo.hero = true;
    if (!photo.focus) photo.focus = "50% 50%";
    // Ensure hero has 'whole' and 'gallery' in roles
    if (!photo.role.includes('whole')) photo.role.push('whole');
    if (!photo.role.includes('gallery')) photo.role.push('gallery');
    if (!photo.primary_for.includes('whole')) photo.primary_for.push('whole');
  }
  markUnsaved();
  loadSpecies(); // refresh
}

let focusPhotoId = null;

function openFocus(photoId, imgPath, currentFocus) {
  focusPhotoId = photoId;
  const overlay = document.getElementById('focusOverlay');
  const img = document.getElementById('focusImg');
  const dot = document.getElementById('focusDot');

  img.src = imgPath;
  overlay.classList.add('active');

  // Show current focus dot
  img.onload = () => {
    const parts = currentFocus.split(/\s+/);
    const xPct = parseFloat(parts[0]) / 100;
    const yPct = parseFloat(parts[1]) / 100;
    dot.style.left = (xPct * img.clientWidth) + 'px';
    dot.style.top = (yPct * img.clientHeight) + 'px';
    dot.style.display = 'block';
  };
}

function setFocus(event) {
  const img = document.getElementById('focusImg');
  const dot = document.getElementById('focusDot');
  const rect = img.getBoundingClientRect();
  const x = event.clientX - rect.left;
  const y = event.clientY - rect.top;
  const xPct = Math.round((x / rect.width) * 100);
  const yPct = Math.round((y / rect.height) * 100);

  dot.style.left = x + 'px';
  dot.style.top = y + 'px';
  dot.style.display = 'block';

  const focusValue = `${xPct}% ${yPct}%`;
  const photo = allData.photos.find(p => p.photo_id === focusPhotoId);
  if (photo) {
    photo.focus = focusValue;
  }
  markUnsaved();
  document.getElementById('statusMsg').textContent = `Focus set: ${focusValue}`;
}

function closeFocus() {
  document.getElementById('focusOverlay').classList.remove('active');
  focusPhotoId = null;
  loadSpecies(); // refresh to show updated focus
}

async function trashPhoto(photoId, psbpId, filename) {
  if (!confirm(`Delete ${filename}? This removes the file and registry entry.`)) return;

  const resp = await fetch('/api/trash', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ photo_id: photoId, psbp_id: psbpId, filename: filename })
  });
  const result = await resp.json();

  if (result.ok) {
    // Remove from local data
    allData.photos = allData.photos.filter(p => p.photo_id !== photoId);
    trashed.add(photoId);
    loadSpecies();
    document.getElementById('statusMsg').textContent = `Trashed: ${filename}`;
    buildSpeciesDropdown(); // update counts
    // Re-select current species
    document.getElementById('speciesSelect').value = currentSpecies;
  } else {
    alert('Error: ' + result.error);
  }
}

function markUnsaved() {
  hasUnsaved = true;
  document.getElementById('saveBtn').classList.add('has-changes');
  document.getElementById('saveBtn').textContent = 'Save Changes *';
}

async function saveChanges() {
  // Apply role changes to allData
  Object.entries(changes).forEach(([photoId, c]) => {
    const photo = allData.photos.find(p => p.photo_id === photoId);
    if (photo && c.role) {
      photo.role = c.role;
    }
  });

  const resp = await fetch('/api/save', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(allData)
  });
  const result = await resp.json();

  if (result.ok) {
    changes = {};
    hasUnsaved = false;
    document.getElementById('saveBtn').classList.remove('has-changes');
    document.getElementById('saveBtn').textContent = 'Save Changes';
    document.getElementById('statusMsg').textContent = `Saved — ${result.count} photos in registry`;
  } else {
    alert('Error saving: ' + result.error);
  }
}

// Warn on navigation with unsaved changes
window.addEventListener('beforeunload', e => {
  if (hasUnsaved) { e.preventDefault(); e.returnValue = ''; }
});

init();
</script>
</body>
</html>
"""


class ReviewHandler(http.server.SimpleHTTPRequestHandler):

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path

        if path == "/" or path == "/index.html":
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(HTML_PAGE.encode("utf-8"))
            return

        if path == "/api/data":
            data = load_credits()
            # Scan for local subfolders so the UI only shows downloaded species
            local_folders = []
            if os.path.isdir(PHOTOS_DIR):
                for name in os.listdir(PHOTOS_DIR):
                    if os.path.isdir(os.path.join(PHOTOS_DIR, name)) and name.startswith("PSBP-"):
                        local_folders.append(name)
            data["_local_folders"] = local_folders
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(data).encode("utf-8"))
            return

        # Serve photo files
        if path.startswith("/photos/"):
            file_path = os.path.join(REPO, path.lstrip("/"))
            if os.path.isfile(file_path):
                self.send_response(200)
                ext = os.path.splitext(file_path)[1].lower()
                ct = {"jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png", "gif": "image/gif"}
                self.send_header("Content-Type", ct.get(ext.lstrip("."), "application/octet-stream"))
                self.end_headers()
                with open(file_path, "rb") as f:
                    self.wfile.write(f.read())
                return

        self.send_error(404)

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        content_len = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_len)

        if path == "/api/save":
            try:
                data = json.loads(body)
                save_credits(data)
                self.send_json({"ok": True, "count": len(data.get("photos", []))})
            except Exception as e:
                self.send_json({"ok": False, "error": str(e)})
            return

        if path == "/api/trash":
            try:
                req = json.loads(body)
                photo_id = req["photo_id"]
                psbp_id = req["psbp_id"]
                filename = req["filename"]

                # Delete file
                file_path = os.path.join(PHOTOS_DIR, psbp_id, filename)
                if os.path.exists(file_path):
                    os.remove(file_path)

                # Remove from JSON
                data = load_credits()
                data["photos"] = [p for p in data["photos"] if p.get("photo_id") != photo_id]
                save_credits(data)

                self.send_json({"ok": True})
            except Exception as e:
                self.send_json({"ok": False, "error": str(e)})
            return

        self.send_error(404)

    def send_json(self, obj):
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(obj).encode("utf-8"))

    def log_message(self, format, *args):
        # Quiet down the access log
        if "/api/" in args[0] or "/photos/" in args[0]:
            return
        super().log_message(format, *args)


def main():
    # Check files exist
    if not os.path.isfile(PHOTO_CREDITS):
        print(f"ERROR: {PHOTO_CREDITS} not found in current directory.")
        print(f"  Run this script from your psbp-photo-run folder.")
        sys.exit(1)

    if not os.path.isdir(PHOTOS_DIR):
        print(f"ERROR: {PHOTOS_DIR}/ folder not found.")
        sys.exit(1)

    server = http.server.HTTPServer(("", PORT), ReviewHandler)
    print(f"🌿 PSBP Photo Review")
    print(f"   Open: http://localhost:{PORT}")
    print(f"   Press Ctrl+C to stop")
    print()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down.")
        server.server_close()


if __name__ == "__main__":
    main()
