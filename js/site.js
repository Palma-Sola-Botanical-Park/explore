/* ── PALMA SOLA BOTANICAL PARK · SHARED JS v2 ── */

/* ============================================================
   PSBP LINK ROUTER  —  one place decides how every link behaves
     • internal  → same window   (your own pages)
     • external  → new tab       (other websites)
     • document  → viewer.html   (PDFs & PUBLISHED Google Docs/
                                   Sheets/Slides), framed in-site
     • direct    → mailto: / tel:, opened natively
   Usage:
     PSBP.linkTag(url, label, { title, back, className, style })
     PSBP.linkAttrs(url, { title, back })  → { href, target, rel, kind }
     PSBP.linkKind(url)                     → 'internal'|'external'|'document'|'direct'
     PSBP.rowLink(row)                      → { url, text }  (reads new or legacy column names)
   ============================================================ */
window.PSBP = window.PSBP || {};
(function (P) {

  P.linkKind = function (url) {
    var u = (url || '').trim();
    if (!u) return 'internal';
    if (/^(mailto:|tel:)/i.test(u)) return 'direct';
    if (/\.pdf($|[?#])/i.test(u)) return 'document';
    if (/docs\.google\.com\/.+\/pub(html)?($|[?#])/i.test(u)) return 'document';
    if (/^https?:\/\//i.test(u)) {
      try { if (new URL(u).host === location.host) return 'internal'; } catch (e) {}
      return 'external';
    }
    return 'internal';
  };

  P.linkAttrs = function (url, opts) {
    opts = opts || {};
    var u = (url || '').trim();
    var kind = P.linkKind(u);
    if (kind === 'document') {
      var back = opts.back || (location.pathname.split('/').pop() || '');
      var href = 'viewer.html?url=' + encodeURIComponent(u) +
                 '&title=' + encodeURIComponent(opts.title || 'Document') +
                 (back ? '&back=' + encodeURIComponent(back) : '');
      return { href: href, target: '', rel: '', kind: kind };
    }
    if (kind === 'external') {
      return { href: u, target: '_blank', rel: 'noopener', kind: kind };
    }
    return { href: u, target: '', rel: '', kind: kind }; // internal + direct
  };

  P.linkTag = function (url, label, opts) {
    opts = opts || {};
    var a = P.linkAttrs(url, opts);
    var attrs = 'href="' + a.href + '"';
    if (a.target)        attrs += ' target="' + a.target + '"';
    if (a.rel)           attrs += ' rel="' + a.rel + '"';
    if (opts.className)  attrs += ' class="' + opts.className + '"';
    if (opts.style)      attrs += ' style="' + opts.style + '"';
    return '<a ' + attrs + '>' + (label == null ? '' : label) + '</a>';
  };

  P.rowLink = function (row) {
    row = row || {};
    return {
      url:  ((row.link_url || row.pdf_url || row.link || '') + '').trim(),
      text: row.link_text || row.pdf_link_text || ''
    };
  };

})(window.PSBP);

const INAT_PROJECT = 'palma-sola-botanical-park';
const SHEET_ID     = '12gRB-c4gND8qJWPmwBoV2X4adqTfRROYHtA8jR4-kS4';

// Sheet tab GIDs — update if Bev renames tabs
const TAB = {
  events:        992316234,
  classes:       141740803,
  series:        926436540,
  volunteer:     269225929,
  announcements: 673905300,
  newsletters:   1749891854,
  news:          195499912,
  venues:        1744975586,
  wedding_calendar: 1260078193,
  wedding_gallery:  874456476,
  right_now:        1545501058,
};

// display filter: which values should appear on the website
const WEB_DISPLAY = new Set(['web', 'both']);

// ── FEATURED ORDER ────────────────────────────────────────────
// The "biggies" that lead the default (un-searched) grid, in this order.
// Everything else follows in PSBP-ID order. List PSBP IDs exactly.
// Searching or filtering uses relevance instead — featured items just
// float to the top of whatever pool is showing.
// Leave an array empty to fall back to plain ID order.
const FEATURED_PLANTS = [
  'PSBP-00011', // Baobab
  'PSBP-00004', // Silk Floss Tree
  'PSBP-00003', // Buccaneer Palm
  'PSBP-00007', // Jacaranda
  'PSBP-00001', // Tree Crinum
];
const FEATURED_WILDLIFE = [
  'PSBP-99983', // Bald Eagle
  'PSBP-99987', // Roseate Spoonbill
  'PSBP-99982', // Osprey
  'PSBP-99971', // Florida Zebra Longwing (state butterfly)
  'PSBP-99977', // Yellow-crowned Night Heron
];

// Float featured IDs to the front of a list, in the order listed above.
// Unknown IDs are ignored; non-featured items keep their existing order.
function orderByFeatured(list, featuredIds) {
  const rank = new Map(featuredIds.map((id, i) => [id, i]));
  return list.slice().sort((a, b) => {
    const ra = rank.has(a.id) ? rank.get(a.id) : Infinity;
    const rb = rank.has(b.id) ? rank.get(b.id) : Infinity;
    return ra - rb; // stable sort keeps non-featured in their original order
  });
}

// ── SHEET FETCH HELPER ────────────────────────────────────────
// Column-header tokens seen across the live tabs. Used to LOCATE the header row by
// its CONTENT rather than trusting a fixed row index — so an inserted row, a stray
// blank, or a Google Sheets "Table" wrapper can't silently shove the feed off its
// rails (that exact thing took the News feed dark on 2026-06-14).
const KNOWN_HEADERS = new Set([
  'display','date','pinned','headline','subhead','blurb','hero_image','intro',
  'image1','image1_caption','aside','body2','title','role','name','bio','body',
  'description','photo_url','link','link_text','url','time','start','end',
  'location','status','note','category','tags',
  // events / classes / series model (see EVENTS_DATA_MODEL.md)
  'series','weekday','day','instructor','link_url','registration_url','cost',
  'fundraiser','closes_park','active','active_from','active_to',
  'flyer_url','flyer_text'
]);
const normHeader = s => (s || '').trim().toLowerCase().replace(/\s+/g, '_');

// ── DATA SOURCE SWITCH (see SHEET_SYNC_ARCHITECTURE.md §6) ─────────────────────
// Normal operation reads validated static JSON that a GitHub Action keeps fresh.
// A migrated tab is served from data/published/<name>.json; un-migrated tabs
// still fetch the live sheet. Flip DATA_SOURCE to 'live' as a break-glass switch
// to force the WHOLE site back onto the live sheet during an extended Actions
// outage (one-character commit). 'live' is the LESS-safe path — it reintroduces
// the unguarded client-side parse the gate exists to remove — so flip it back
// the moment the pipeline recovers.
const DATA_SOURCE = 'published';            // 'published' (default) | 'live' (break-glass)
const MIGRATED = new Set(['events', 'classes', 'volunteer', 'news', 'newsletters','series', 'announcements', 'venues', 'wedding_calendar', 'wedding_gallery', 'right_now']);      // tabs served from validated JSON; grow as templated
const GID_TO_NAME = Object.fromEntries(Object.entries(TAB).map(([k, v]) => [v, k]));

async function fetchTab(gid) {
  const name = GID_TO_NAME[gid] || String(gid);
  if (DATA_SOURCE !== 'live' && MIGRATED.has(name)) {
    try {
      const r = await fetch(`data/published/${name}.json`, { cache: 'no-store' });
      if (r.ok) return await r.json();   // the gate guarantees this is clean
      console.warn(`published/${name}.json -> ${r.status}; falling back to live sheet`);
    } catch (e) {
      console.warn(`published/${name}.json fetch failed (${e}); falling back to live sheet`);
    }
  }
  return fetchTabLive(gid);
}

// The original live-CSV path, kept dormant as the break-glass fallback (§6).
// All the brittle parsing now also runs server-side in fetch_sheets.py under the
// gate; this stays so DATA_SOURCE='live' (or a missing published file) still works.
async function fetchTabLive(gid) {
  const url = `https://docs.google.com/spreadsheets/d/${SHEET_ID}/export?format=csv&gid=${gid}`;
  const resp = await fetch(url);
  if (!resp.ok) throw new Error(`Sheet tab ${gid} failed`);
  const text = await resp.text();
  const lines = text.trim().split('\n');

  // Convention: [section title] / [column headers] / [hint] / data…
  // Find the header row by content (the row with the most cells matching known
  // column names) instead of assuming it's always line 2. If rows shift up or down,
  // this self-corrects; data is taken from two rows below it (skipping the hint row).
  let headerIdx = 1, best = 0;
  for (let i = 0; i < Math.min(lines.length, 12); i++) {
    const hits = parseCSVLine(lines[i]).map(normHeader).filter(c => KNOWN_HEADERS.has(c)).length;
    if (hits > best) { best = hits; headerIdx = i; }
  }
  if (best < 2) headerIdx = 1; // not confident → fall back to the documented layout

  const headers = parseCSVLine(lines[headerIdx]).map(normHeader);
  return lines.slice(headerIdx + 2) // skip the hint row directly beneath the header
    .map(line => {
      const vals = parseCSVLine(line);
      const obj = {};
      headers.forEach((h, i) => obj[h] = (vals[i] || '').trim());
      return obj;
    })
    .filter(r => Object.values(r).some(v => v)); // skip blank rows
}

function parseCSVLine(line) {
  const result = [];
  let current = '';
  let inQuotes = false;
  for (let i = 0; i < line.length; i++) {
    const ch = line[i];
    if (ch === '"') {
      if (inQuotes && line[i+1] === '"') { current += '"'; i++; }
      else inQuotes = !inQuotes;
    } else if (ch === ',' && !inQuotes) {
      result.push(current); current = '';
    } else {
      current += ch;
    }
  }
  result.push(current);
  return result;
}

function isWebVisible(row) {
  const d = (row.display || '').toLowerCase().trim();
  return WEB_DISPLAY.has(d);
}

// ── NAV HTML ─────────────────────────────────────────────────
const NAV_HTML = `
<nav id="site-nav">
  <ul class="nav-links">
    <li><a href="index.html">Home</a></li>
    <li><a href="visit.html">Visit</a></li>
    <li><a href="events.html">Events</a></li>
    <li><a href="venue.html">Venue</a></li>
    <li><a href="get-involved.html">Get Involved</a></li>
    <li><a href="contact.html">About</a></li>
  </ul>
  <a href="index.html" class="nav-logo">
    <img src="images/white_PSBP_logo.png" alt="Palma Sola Botanical Park">
  </a>
  <button class="nav-hamburger" id="navHamburger" aria-label="Menu">
    <span></span><span></span><span></span>
  </button>
</nav>
<div class="nav-mobile" id="navMobile">
  <a href="index.html">Home</a>
  <a href="visit.html">Visit</a>
  <a href="events.html">Events</a>
  <a href="venue.html">Venue</a>
  <a href="get-involved.html">Get Involved</a>
  <a href="contact.html">About</a>
</div>`;

// ── FOOTER HTML ───────────────────────────────────────────────
const FOOTER_HTML = `
<footer id="site-footer">
  <div class="footer-inat" id="footerInatStrip" style="display:none">
    <div class="footer-inat-stats">
      <div class="footer-inat-stat"><strong id="fTotal" class="pulse">—</strong><span>Observations</span></div>
      <div class="footer-inat-stat"><strong id="fSpecies" class="pulse">—</strong><span>Species</span></div>
      <div class="footer-inat-stat"><strong id="fObservers" class="pulse">—</strong><span>Observers</span></div>
      <div class="footer-inat-stat"><strong id="fWeek" class="pulse">—</strong><span>This Week</span></div>
    </div>
    <a href="https://www.inaturalist.org/projects/palma-sola-botanical-park" target="_blank" rel="noopener" class="inat-bar-link">
      📷 Palma Sola on iNaturalist →
    </a>
  </div>
  <div class="footer-grid">
    <div class="footer-col footer-brand">
      <img src="images/white_PSBP_logo.png" alt="PSBP" style="height:44px;opacity:.85;margin-bottom:.65rem">
      <p>A 501(c)(3) nonprofit botanical park on the shore of Palma Sola Bay.<br>
      Free every day. No government funding. Powered by community.</p>
      <div class="social-links" style="margin-top:.9rem">
        <a href="https://www.facebook.com/people/Palma-Sola-Botanical-Park/100064517386906/" target="_blank" rel="noopener" class="social-link">f</a>
        <a href="https://www.instagram.com/palmasolabotanical/" target="_blank" rel="noopener" class="social-link">ig</a>
        <a href="https://www.inaturalist.org/projects/palma-sola-botanical-park" target="_blank" rel="noopener" class="social-link">iN</a>
      </div>
    </div>
    <div class="footer-col">
      <h4>Explore</h4>
      <ul>
        <li><a href="nature.html#right-now">Right Now in the Park</a></li>
        <li><a href="nature.html#plants">Plants &amp; Wildlife at the Park</a></li>
        <li><a href="news.html">Park News</a></li>
        <li><a href="visit.html#nursery">Hidden Garden Nursery</a></li>
      </ul>
    </div>
    <div class="footer-col">
      <h4>Visit</h4>
      <ul>
        <li><a href="visit.html">Hours & Directions</a></li>
        <li><a href="events.html">Events & Classes</a></li>
        <li><a href="venue.html">Venue Rentals</a></li>
        <li><a href="venue.html#art">Art in the Park</a></li>
        <li><a href="visit.html#rarefruit">Rare Fruit Trail</a></li>
      </ul>
    </div>
    <div class="footer-col">
      <h4>Support</h4>
      <ul>
        <li><a href="get-involved.html#donate">Donate</a></li>
        <li><a href="get-involved.html#member">Membership</a></li>
        <li><a href="get-involved.html#volunteer">Volunteer</a></li>
        <li><a href="contact.html">About</a></li>
        <li><a href="https://palmasolabp.org/wp-content/uploads/2024/08/Photo-Policy.pdf" target="_blank" rel="noopener">Photo Policy</a></li>
      </ul>
    </div>
  </div>
  <div class="footer-bottom">
    <span>© 2026 Palma Sola Botanical Park Foundation, Inc. · 9800 17th Ave NW, Bradenton FL 34209</span>
    <span style="color:rgba(255,255,255,.3)">Free. Always.</span>
  </div>
</footer>`;

// ── INAT BAR HTML ─────────────────────────────────────────────
const INAT_BAR_HTML = `
<div id="inat-bar">
  <div class="inat-bar-stat"><span class="inat-bar-num pulse" id="barTotal">—</span><span class="inat-bar-lbl">Observations</span></div>
  <div class="inat-bar-stat"><span class="inat-bar-num pulse" id="barSpecies">—</span><span class="inat-bar-lbl">Species</span></div>
  <div class="inat-bar-stat"><span class="inat-bar-num pulse" id="barWeek">—</span><span class="inat-bar-lbl">This Week</span></div>
  <div class="inat-bar-stat" style="display:flex;flex-direction:column;gap:.3rem">
    <span class="inat-bar-lbl">Latest</span>
    <div class="inat-photos" id="barPhotos"></div>
  </div>
  <a href="https://www.inaturalist.org/projects/palma-sola-botanical-park" target="_blank" rel="noopener" class="inat-bar-link">Join the project →</a>
</div>`;

// ── INJECT SHARED ELEMENTS ────────────────────────────────────
function injectShared(opts = {}) {
  // Detect if we're in a subfolder (e.g. /plants/) and prefix links accordingly
  const pathParts = window.location.pathname.split('/').filter(Boolean);
  const repoName = 'explore';   // ← update to '' after custom-domain migration
  const repoIdx = repoName ? pathParts.indexOf(repoName) : -1;
  // Only treat as subfolder if there's a directory segment between the repo and the file
  // e.g. /explore/plants/PSBP-00001.html → inSubfolder = true
  // e.g. /explore/nature.html → inSubfolder = false
  // After custom domain: /plants/PSBP-00001.html → use fallback depth check
  const inSubfolder = repoIdx >= 0
    ? pathParts.length > repoIdx + 2
    : pathParts.length >= 2 && pathParts[pathParts.length - 1].includes('.');
  const base = inSubfolder ? '../' : '';

  // Replace relative paths in NAV and FOOTER with correct base
  const fixPaths = html => html
    .replace(/href="(?!http|#|\/\/|mailto:|tel:|\.\.\/|\/[^"])([^"]+)"/g, (m, p) => `href="${base}${p}"`)
    .replace(/src="(?!http|\/\/|data:|\.\.\/|\/[^"])([^"]+)"/g, (m, p) => `src="${base}${p}"`);

  const link = document.createElement('link');
  link.rel = 'stylesheet';
  link.href = 'https://fonts.googleapis.com/css2?family=Playfair+Display:ital,wght@0,400;0,700;1,400&family=Source+Sans+3:wght@300;400;600;700&display=swap';
  document.head.appendChild(link);

  // favicon + apple-touch icon — base-aware so /plants/ and /wildlife/ subpages resolve correctly
  if (!document.querySelector('link[rel="icon"]')) {
    const icon = document.createElement('link');
    icon.rel = 'icon'; icon.type = 'image/png'; icon.href = base + 'images/favicon.png';
    document.head.appendChild(icon);
    const touch = document.createElement('link');
    touch.rel = 'apple-touch-icon'; touch.href = base + 'images/apple-touch-icon.png';
    document.head.appendChild(touch);
  }

  const navDiv = document.getElementById('nav-placeholder');
  if (navDiv) navDiv.outerHTML = fixPaths(NAV_HTML);

  if (opts.inatBar) {
    const barDiv = document.getElementById('inat-bar-placeholder');
    if (barDiv) barDiv.outerHTML = fixPaths(INAT_BAR_HTML);
  }

  const footDiv = document.getElementById('footer-placeholder');
  if (footDiv) footDiv.outerHTML = fixPaths(FOOTER_HTML);

  // Show footer iNat stats strip only on Nature and Home pages
  if (opts.inatFooter) {
    const strip = document.getElementById('footerInatStrip');
    if (strip) strip.style.display = '';
  }

  const path = window.location.pathname.split('/').pop() || 'index.html';
  document.querySelectorAll('#site-nav a, #navMobile a').forEach(a => {
    const href = a.getAttribute('href') || '';
    if (href.endsWith(path)) a.classList.add('active');
  });

  // Use document-level delegation for hamburger — works regardless of DOM timing
  document.addEventListener('click', function(e) {
    const btn = e.target.closest('#navHamburger');
    if (btn) {
      const mob = document.getElementById('navMobile');
      if (mob) mob.classList.toggle('open');
    }
  });
}

// ── INAT API ──────────────────────────────────────────────────
async function loadINat() {
  const base = `https://api.inaturalist.org/v1`;
  try {
    const [totR, spR, obR] = await Promise.all([
      fetch(`${base}/observations?project_id=${INAT_PROJECT}&per_page=1`).then(r=>r.json()),
      fetch(`${base}/observations/species_counts?project_id=${INAT_PROJECT}`).then(r=>r.json()),
      fetch(`${base}/observations/observers?project_id=${INAT_PROJECT}`).then(r=>r.json()),
    ]);
    const total = totR.total_results || 0;
    const species = spR.total_results || 0;
    const observers = obR.total_results || 0;

    const weekAgo = new Date(); weekAgo.setDate(weekAgo.getDate()-7);
    const wR = await fetch(`${base}/observations?project_id=${INAT_PROJECT}&created_d1=${weekAgo.toISOString().split('T')[0]}&per_page=1`).then(r=>r.json());
    const week = wR.total_results || 0;

    const set = (id, val) => document.querySelectorAll(`#${id}`).forEach(el => {
      el.textContent = typeof val === 'number' ? val.toLocaleString() : val;
      el.classList.remove('pulse');
    });
    set('barTotal', total); set('barSpecies', species); set('barWeek', week);
    set('fTotal', total); set('fSpecies', species); set('fObservers', observers); set('fWeek', week);
    // Nature page stats — species, observers, total (no time-based stats)
    ['statSpecies','statObservers','statTotal'].forEach((id,i) => {
      const el = document.getElementById(id);
      if (el) { el.textContent = [species,observers,total][i].toLocaleString(); el.classList.remove('pulse'); }
    });
    // Index page inline stats (same IDs, same values — works on both pages)


    const photoR = await fetch(`${base}/observations?project_id=${INAT_PROJECT}&per_page=6&order=desc&order_by=created_at&photos=true`).then(r=>r.json());
    const photosEl = document.getElementById('barPhotos');
    if (photosEl) {
      photosEl.innerHTML = '';
      (photoR.results||[]).slice(0,5).forEach(o => {
        if (o.photos?.[0]) {
          const img = document.createElement('img');
          img.src = (o.photos[0].url||'').replace('square','small');
          img.className = 'inat-thumb';
          img.title = `${o.species_guess||'Unknown'} · ${o.user?.login||''}`;
          img.onclick = () => window.open(`https://www.inaturalist.org/observations/${o.id}`,'_blank');
          photosEl.appendChild(img);
        }
      });
    }
    return { total, species, observers, week, recentObs: photoR.results||[] };
  } catch(e) { console.warn('iNat error',e); return {}; }
}

async function loadRecentObs(opts={}) {
  const params = new URLSearchParams({
    project_id: INAT_PROJECT, per_page: opts.count||12,
    order:'desc', order_by:'created_at', photos:'true',
  });
  if (opts.iconicTaxon) params.set('iconic_taxa', opts.iconicTaxon);
  const r = await fetch(`https://api.inaturalist.org/v1/observations?${params}`).then(r=>r.json());
  return r.results||[];
}

function renderObsGrid(containerId, obs) {
  const el = document.getElementById(containerId);
  if (!el) return;
  if (!obs.length) { el.innerHTML='<p class="text-soft" style="grid-column:1/-1;padding:2rem;text-align:center">No observations found.</p>'; return; }
  el.innerHTML = obs.map(o => {
    const photo = o.photos?.[0]?.url?.replace('square','medium')||'';
    const date = o.observed_on ? new Date(o.observed_on+'T12:00').toLocaleDateString('en-US',{month:'short',day:'numeric'}) : '';
    return photo ? `<a class="card obs-card" href="https://www.inaturalist.org/observations/${o.id}" target="_blank" rel="noopener">
      <div class="obs-card-img" style="background-image:url('${photo}')"></div>
      <div class="obs-card-body">
        <div class="obs-species">${o.species_guess||o.taxon?.name||'Unknown'}</div>
        <div class="obs-by">📷 ${o.user?.login||'observer'}</div>
        <div class="obs-date">${date}</div>
      </div></a>` : '';
  }).join('');
}

// ── EVENTS (with PDF links, display filter) ───────────────────
// Truncate at a word boundary with an ellipsis (no mid-word "plus mor" cutoffs).
function clip(s, n = 140) {
  if (!s) return '';
  if (s.length <= n) return s;
  const cut = s.slice(0, n);
  const lastSpace = cut.lastIndexOf(' ');
  return (lastSpace > 40 ? cut.slice(0, lastSpace) : cut).replace(/[\s,;:.!–—-]+$/, '') + '…';
}

/* ============================================================
   EVENTS ENGINE  —  events.html (see EVENTS_DATA_MODEL.md)

   Three kinds of content:
     • one-off EVENT      → events tab (a dated row)
     • standing CLASS     → classes tab (a weekday RULE, expanded
                            into dated instances only inside a window)
     • SERIES             → series tab (a label bundling dated events)

   Three views, all rendered by loadEventsPage():
     1. AGENDA   — next 14 days, MERGED: events + series sessions +
                   expanded class instances, sorted by date.
     2. AHEAD    — dated events/sessions beyond the window (NO class
                   instances — that's what stops infinite repeats).
     3. RHYTHM   — weekly class SCHEDULE (each class once) + SERIES
                   index (each series once, with its flyer link).

   Closures (events with closes_park = yes) announce the park is shut
   and SUPPRESS any other programming on that date.

   loadEvents()/loadClasses() are kept as simpler single-list
   renderers for other pages (e.g. a homepage teaser).
   ============================================================ */

// Controlled category vocabulary → badge emoji + accent colour.
// Order here is also the filter-button order. Keep in sync with the
// sheet's `category` dropdown (the 8 terms in EVENTS_DATA_MODEL.md §2).
const EVENT_CATEGORIES = [
  { key:'Fitness & Wellness', emoji:'🧘', color:'#5b8db8' },
  { key:'Talks & Learning',   emoji:'📚', color:'#2d6a35' },
  { key:'Workshops',          emoji:'✂️', color:'#b07d2b' },
  { key:'Family & Kids',      emoji:'🎨', color:'#c8643c' },
  { key:'Arts & Music',       emoji:'🎵', color:'#8a5a9b' },
  { key:'Community',          emoji:'🎉', color:'#d29a1f' },
  { key:'Volunteer',          emoji:'🌱', color:'#4a8b3b' },
  { key:'Private',            emoji:'🔒', color:'#8a8a8a' },
];
const catMeta = key => EVENT_CATEGORIES.find(c => c.key === (key||'').trim())
  || { key:(key||'').trim(), emoji:'📅', color:'#8a8a8a' };

// Map a row's category, tolerating the legacy `type` values during migration.
function eventCategory(row){
  const c = (row.category || row.type || '').trim();
  const legacy = { education:'Talks & Learning', social:'Community',
                   event:'Community', wedding:'Private' };
  return legacy[c.toLowerCase()] || c;
}

const _evEsc = s => (s==null?'':(''+s)).replace(/[&<>"']/g,
  c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const _isYes = v => /^(yes|y|true|1)$/i.test(((v||'')+'').trim());
const _BACK  = () => location.pathname.split('/').pop() || 'events.html';

// 'YYYY-MM-DD' → local Date at noon (noon anchor avoids timezone day-shift).
function parseDateLocal(s){
  if (!s) return null;
  const d = new Date(((s+'').trim()) + 'T12:00');
  return isNaN(d) ? null : d;
}
const _ymd = d => d.getFullYear() + '-' +
  String(d.getMonth()+1).padStart(2,'0') + '-' + String(d.getDate()).padStart(2,'0');
const _mo  = d => d.toLocaleDateString('en-US',{month:'short'}).toUpperCase();

const DAY_CODES = ['Sun','Mon','Tue','Wed','Thu','Fri','Sat'];
const DAY_FULL  = {Sun:'Sundays',Mon:'Mondays',Tue:'Tuesdays',Wed:'Wednesdays',
                   Thu:'Thursdays',Fri:'Fridays',Sat:'Saturdays'};
const formatWeekday = w => (w||'').split(',')
  .map(s => DAY_FULL[s.trim().slice(0,3)] || s.trim()).filter(Boolean).join(' & ');

// Muted weekday palette — same hue down a column = same weekday, so the eye
// feels days passing (Mon red-ish … Sun rose). Index by Date.getDay() (0=Sun).
const DOW_COLOR = ['#8f5675','#a14b3e','#9a7b2b','#4f7d3a','#3d7873','#45648f','#6f5790'];
const DOW_ABBR  = ['SUN','MON','TUE','WED','THU','FRI','SAT'];
const dowColor  = d => DOW_COLOR[d.getDay()];
const _dowNice  = d => { const a = DOW_ABBR[d.getDay()]; return a[0] + a.slice(1).toLowerCase(); };

// A link target for an item: its own link, else its series flyer, else ''.
function _itemHref(item, seriesMap){
  let url = item._link && item._link.url;
  if (!url && seriesMap){ const s = _seriesOf(item, seriesMap); if (s) url = s.flyer_url || ''; }
  return url || '';
}

// Sort by date, then by start time (best-effort time parse).
function _timeKey(t){
  const m = (t||'').match(/(\d{1,2})(?::(\d{2}))?\s*([ap]\.?m)?/i);
  if (!m) return 9999;
  let h = +m[1]; const min = +(m[2]||0); const ap = (m[3]||'').toLowerCase();
  if (ap.startsWith('p') && h < 12) h += 12;
  if (ap.startsWith('a') && h === 12) h = 0;
  return h*60 + min;
}
const _byDateThenTime = (a,b) => (a.date - b.date) || (_timeKey(a.time) - _timeKey(b.time));

// Pull just the START time out of a free-form time string and compact it:
// "9AM - 12PM" → "9AM",  "10AM - 11:30AM" → "10AM",
// "1PM - you can stand it" → "1PM",  "9:30 AM" → "9:30AM"
function _startTime(t){
  if (!t) return '';
  let s = String(t).split(/\s*(?:[-–—]|to\b)\s*/i)[0].trim(); // take part before any dash/"to"
  s = s.replace(/\s+/g, '').toUpperCase();                    // "9 AM" → "9AM"
  return s;
}

// Turn one event row into an agenda item.
function _eventItem(e){
  const date = parseDateLocal(e.date);
  if (!date) return null;
  return {
    kind: _isYes(e.closes_park) ? 'closure' : 'event',
    date, title: e.title, time: e.time, description: e.description,
    category: eventCategory(e), cost: e.cost, series: e.series,
    fundraiser: _isYes(e.fundraiser), kid_friendly: _isYes(e.kid_friendly),
    save_the_date: _isYes(e.save_the_date),
    registration_url: e.registration_url,
    instructor: '', _link: PSBP.rowLink(e)
  };
}

// Expand visible classes into dated instances inside [start, end],
// honouring weekday rule(s) and any active_from / active_to season.
function expandClasses(classes, start, end){
  const out = [];
  classes.forEach(c => {
    const days = (c.weekday||'').split(',').map(s => DAY_CODES.indexOf(s.trim().slice(0,3)))
                  .filter(i => i >= 0);
    if (!days.length) return;                 // no weekday rule → not expandable
    const from = parseDateLocal(c.active_from);
    const to   = parseDateLocal(c.active_to);
    const cur  = new Date(start);
    for (; cur <= end; cur.setDate(cur.getDate()+1)){
      if (!days.includes(cur.getDay())) continue;
      if (from && cur < from) continue;
      if (to   && cur > to)   continue;
      out.push({
        kind: 'class', date: new Date(cur), title: c.title, time: c.time,
        instructor: c.instructor, description: c.description,
        category: eventCategory(c), cost: c.cost, series: '',
        fundraiser: false, kid_friendly: _isYes(c.kid_friendly),
        registration_url: c.registration_url, _link: PSBP.rowLink(c)
      });
    }
  });
  return out;
}

// Resolve a series row from a session's `series` name.
const _seriesOf = (item, map) => item.series ? map[item.series.trim().toLowerCase()] : null;

// Inline prose link tacked onto the end of a description — the words ARE the
// link, no button. Uses link_text, or a quiet "more" default when blank.
function _inlineLink(item){
  const url  = item._link && item._link.url;
  if (!url) return '';
  const text = (item._link && item._link.text) || 'more';
  return ' ' + PSBP.linkTag(url, _evEsc(text) + ' →',
    { title: item.title || '', back: _BACK(), className: 'ev-inline-link' });
}

// "Part of the {series} →" sentence, the series name linking the flyer.
function _seriesLine(item, seriesMap){
  if (!item.series) return '';
  const label = item.series.trim();
  const s = _seriesOf(item, seriesMap);
  if (s && s.flyer_url)
    return `<div class="ev-series">Part of the ${PSBP.linkTag(s.flyer_url, _evEsc(label)+' →',
      { title: label, back: _BACK(), className:'ev-series-link' })}</div>`;
  return `<div class="ev-series">Part of the ${_evEsc(label)}</div>`;
}

function _badges(item){
  const out = [];
  const cm = catMeta(item.category);
  if (item.category)
    out.push(`<span class="ev-badge" style="background:${cm.color}1a;color:${cm.color}">${cm.emoji} ${_evEsc(item.category)}</span>`);
  if (item.kid_friendly)
    out.push(`<span class="ev-badge ev-badge-kid">👪 Kid-friendly</span>`);
  if (/^free$/i.test((item.cost||'').trim()))
    out.push(`<span class="ev-badge ev-badge-free">Free</span>`);
  else if (item.cost)
    out.push(`<span class="ev-badge ev-badge-cost">${_evEsc(item.cost)}</span>`);
  if (item.registration_url)
    out.push(`<span class="ev-badge ev-badge-reg">Sign-up</span>`);
  if (item.fundraiser)
    out.push(`<span class="ev-badge ev-badge-fund">💛 Fundraiser</span>`);
  return out.length ? `<div class="ev-badges">${out.join('')}</div>` : '';
}

// One agenda card (events, series sessions, class instances, closures).
function renderAgendaCard(item, seriesMap){
  const d = item.date;
  const isClosure = item.kind === 'closure';
  const boxColor = isClosure ? '#6b6b6b' : dowColor(d);
  const dateBox = `<div class="ev-date" style="background:${boxColor}">
      <span class="ev-dow">${DOW_ABBR[d.getDay()]}</span>
      <span class="ev-dnum">${d.getDate()}</span>
      <span class="ev-dmo">${_mo(d)}</span>
      ${item.time ? `<span class="ev-dtime">${_evEsc(item.time)}</span>` : ''}
    </div>`;

  if (isClosure){
    return `<div class="event-card ev-card agenda-closure" data-category="Private" data-always="1">
      ${dateBox}
      <div class="event-info">
        <div class="ev-titlerow"><h4 class="ev-title">🔒 Park closed — ${_evEsc((item.title||'').trim() || 'Private event')}</h4></div>
        <p>${item.description ? clip(item.description,160) : 'The park is closed to the public this day for a private event — please plan your visit around it.'}</p>
      </div>
    </div>`;
  }

  const reg = item.registration_url
    ? PSBP.linkTag(item.registration_url, 'Register →',
        { title:item.title||'Register', back:_BACK(), className:'btn btn-sm btn-gold', style:'margin-top:.5rem' })
    : '';
  const descText = item.description ? clip(item.description,140) : '';
  const inline   = _inlineLink(item);
  const descHtml = (descText || inline) ? `<p>${descText}${inline}</p>` : '';
  const instr = (item.kind==='class' && item.instructor)
    ? `<span class="ev-instr">, ${_evEsc(item.instructor)}</span>` : '';

  return `<div class="event-card ev-card" data-category="${_evEsc(item.category)}"${item.kid_friendly?' data-kid="1"':''}>
    ${dateBox}
    <div class="event-info">
      <div class="ev-titlerow">
        <h4 class="ev-title">${_evEsc(item.title||'')}${instr}</h4>
        ${_badges(item)}
      </div>
      ${descHtml}
      ${_seriesLine(item, seriesMap)}
      ${reg ? `<div class="ev-actions">${reg}</div>` : ''}
    </div>
  </div>`;
}

// One weekly-schedule row (a class shown ONCE, as a rule not an instance).
function renderScheduleRow(c){
  const dayLabel = c.day || formatWeekday(c.weekday) || '';
  const link = PSBP.rowLink(c);
  const more = link.url
    ? ' <span class="text-soft">·</span> ' + PSBP.linkTag(link.url, (link.text||'more')+' →',
        { title:c.title||'', back:_BACK(), className:'sched-link' })
    : '';
  return `<div class="sched-row">
    <div class="sched-day">${_evEsc(dayLabel)}${c.time?`<span>${_evEsc(c.time)}</span>`:''}</div>
    <div class="sched-body">
      <strong>${_evEsc(c.title||'')}</strong>${c.instructor?` <span class="text-soft">· ${_evEsc(c.instructor)}</span>`:''}
      ${c.cost?` <span class="sched-cost">· ${_evEsc(c.cost)}</span>`:''}${more}
    </div>
  </div>`;
}

// One series-index card (a series shown ONCE, with its flyer link).
function renderSeriesCard(s){
  const cm = catMeta(s.category);
  const link = s.flyer_url
    ? PSBP.linkTag(s.flyer_url, (s.flyer_text||'Learn more')+' →',
        { title:s.name||'', back:_BACK(), className:'series-link' })
    : '';
  return `<div class="series-card">
    <h4>${_evEsc(s.name||'')}</h4>
    ${s.category?`<span class="ev-badge" style="background:${cm.color}1a;color:${cm.color}">${cm.emoji} ${_evEsc(s.category)}</span>`:''}
    ${s.blurb?`<p>${_evEsc(s.blurb)}</p>`:''}
    ${link?`<div class="series-actions">${link}</div>`:''}
  </div>`;
}

// One "Save the Date" rail card — a marquee event (Holiday Nights, the gala),
// pinned regardless of how far out it is. Title + date + flyer link, nothing more.
function renderSaveDate(item){
  const href = item._link && item._link.url;
  const dateStr = item.date.toLocaleDateString('en-US',{month:'short',day:'numeric'});
  const link = href
    ? PSBP.linkTag(href, (item._link.text||'See the flyer')+' →',
        { title:item.title||'', back:_BACK(), className:'std-link' })
    : '';
  return `<div class="std-card">
    <span class="std-star">⭐</span>
    <div class="std-body">
      <strong>${_evEsc(item.title||'')}</strong>
      <span class="std-date">${dateStr}</span>
      ${link ? `<div class="std-linkrow">${link}</div>` : ''}
    </div>
  </div>`;
}

// Group dated items (events + closures, NO classes) into chronological months.
function _groupByMonth(items){
  const map = new Map();
  items.forEach(it => {
    const key = it.date.getFullYear()*100 + it.date.getMonth();
    if (!map.has(key)) map.set(key, { year:it.date.getFullYear(), month:it.date.getMonth(), items:[] });
    map.get(key).items.push(it);
  });
  return [...map.values()];          // items pre-sorted by date → months in order
}

// Months as grouped scrolling lists (the full-calendar view, all screen sizes).
function renderMonthList(groups, seriesMap){
  return groups.map(g => {
    const first = new Date(g.year, g.month, 1);
    const rows = g.items.map(it => {
      const href   = _itemHref(it, seriesMap);
      const closed = it.kind === 'closure';
      const dot   = `<span class="ml-dot" style="background:${closed?'#6b6b6b':dowColor(it.date)}"></span>`;
      const date  = `<span class="ml-date">${_dowNice(it.date)} ${it.date.getDate()}</span>`;
      // start time only, shown inline as "9AM: Event name" (drop any end time)
      const start = (!closed && it.time) ? _startTime(it.time) : '';
      const timePre = start ? `<span class="ml-time">${_evEsc(start)}:</span> ` : '';
      // closures show a reason: public_note if set, otherwise "Private event"
      const reason = (it.title || '').trim() || 'Private event';
      const label = closed
        ? `🔒 Park closed<span class="ml-closure-reason"> — ${_evEsc(reason)}</span>`
        : `${timePre}${_evEsc(it.title || '')}`;
      const title = `<span class="ml-title">${label}</span>`;
      const inner = `${dot}${date}${title}<span class="ml-chev">›</span>`;
      const row = href
        ? PSBP.linkTag(href, inner, { title:it.title||'', back:_BACK(), className:'ml-row' })
        : `<div class="ml-row">${inner}</div>`;
      // Wrap so the calendar filter can show/hide by category / kid-friendly.
      return `<div class="ml-rowwrap" data-category="${_evEsc(it.category||'')}"${
        it.kid_friendly?' data-kid="1"':''}${closed?' data-always="1"':''}>${row}</div>`;
    }).join('');
    return `<section class="ml-month">
      <h3 class="ml-title-h">${first.toLocaleDateString('en-US',{month:'long',year:'numeric'})}</h3>
      ${rows}
    </section>`;
  }).join('');
}

// Build the category (+ kid-friendly) filter from what's actually present, and
// wire show/hide. Closure items (data-always="1") stay visible under every filter.
// opts.itemSelector picks which elements to toggle (default agenda cards);
// opts.groupSelector, when set, hides group wrappers left with no visible items
// (used to drop empty month headers in the calendar list).
function buildEventFilters(container, cardContainers, opts){
  if (!container) return;
  opts = opts || {};
  const itemSel  = opts.itemSelector || '[data-category]';
  const groupSel = opts.groupSelector || null;
  const present = new Set();
  let hasKid = false;
  cardContainers.forEach(c => c && c.querySelectorAll(itemSel)
    .forEach(el => {
      const v = el.getAttribute('data-category'); if (v) present.add(v);
      if (el.getAttribute('data-kid') === '1') hasKid = true;
    }));
  const order = EVENT_CATEGORIES.map(c => c.key).filter(k => present.has(k));
  if (order.length < 2 && !hasKid){ container.innerHTML = ''; return; }   // not worth a filter
  const btn = (cat,label,active) =>
    `<button class="ev-filter-btn${active?' active':''}" data-cat="${_evEsc(cat)}">${label}</button>`;
  container.innerHTML = btn('__all','All',true) +
    order.map(k => { const m = catMeta(k); return btn(k, `${m.emoji} ${k}`, false); }).join('') +
    (hasKid ? btn('__kid','👪 Kid-friendly',false) : '');
  container.querySelectorAll('.ev-filter-btn').forEach(b => {
    b.addEventListener('click', () => {
      container.querySelectorAll('.ev-filter-btn').forEach(x => x.classList.remove('active'));
      b.classList.add('active');
      const cat = b.getAttribute('data-cat');
      cardContainers.forEach(c => {
        if (!c) return;
        c.querySelectorAll(itemSel).forEach(el => {
          const always = el.getAttribute('data-always') === '1';
          const show = always || cat === '__all'
                    || (cat === '__kid' ? el.getAttribute('data-kid') === '1'
                                        : el.getAttribute('data-category') === cat);
          el.style.display = show ? '' : 'none';
        });
        // Hide month sections that ended up empty under this filter.
        if (groupSel) c.querySelectorAll(groupSel).forEach(g => {
          const anyVisible = [...g.querySelectorAll(itemSel)].some(el => el.style.display !== 'none');
          g.style.display = anyVisible ? '' : 'none';
        });
      });
    });
  });
}

// ── Inject all events CSS once, so cards render correctly on ANY page
// (events.html, homepage teaser, etc.). Colors are inline per-weekday; this
// handles layout, typography, and responsive behavior.
function injectEventStyles(){
  if (document.getElementById('psbp-event-styles')) return;
  const css = `
  .ev-card{display:flex;gap:1rem;align-items:stretch;background:var(--white,#fff);
    border:1px solid #e7e2d6;border-radius:14px;padding:0;overflow:hidden;
    box-shadow:0 1px 3px rgba(40,50,30,.06);margin-bottom:1rem}
  .ev-card .event-info{flex:1;min-width:0;padding:.85rem 1.1rem .95rem 0}
  .ev-card .ev-date{flex:0 0 auto;width:84px;display:flex;flex-direction:column;
    align-items:center;justify-content:center;gap:.05rem;color:#fff;padding:.6rem .3rem;text-align:center}
  .ev-date .ev-dow{font-size:.7rem;font-weight:800;letter-spacing:.08em;opacity:.92}
  .ev-date .ev-dnum{font-size:1.7rem;font-weight:800;line-height:1}
  .ev-date .ev-dmo{font-size:.7rem;font-weight:700;letter-spacing:.08em;opacity:.92;text-transform:uppercase}
  .ev-date .ev-dtime{margin-top:.3rem;font-size:.8rem;font-weight:700;line-height:1.25;
    border-top:1px solid rgba(255,255,255,.4);padding-top:.3rem}
  .ev-titlerow{display:flex;justify-content:space-between;align-items:flex-start;
    gap:.6rem;flex-wrap:wrap}
  .ev-title{margin:0;font-size:1.18rem;line-height:1.25;flex:1 1 auto;min-width:0}
  .ev-title .ev-instr{font-weight:500;color:var(--text-soft,#6b6f63)}
  .ev-titlerow .ev-badges{margin:.1rem 0 0;justify-content:flex-end}
  .ev-card .event-info > p{margin:.45rem 0 0;color:var(--text-soft,#54584c);line-height:1.5}
  .ev-badges{display:flex;flex-wrap:wrap;gap:.4rem;margin:.55rem 0 0}
  .ev-badge{display:inline-block;font-size:.74rem;font-weight:700;padding:.2rem .55rem;
    border-radius:999px;white-space:nowrap;background:#eef0ea;color:#4a5040}
  .ev-badge-free{background:#e3f0e2;color:#2d6a35}
  .ev-badge-cost{background:#f6ecca;color:#7a5a12}
  .ev-badge-reg{background:#e2edf6;color:#2b5d86}
  .ev-badge-fund{background:#fbe6d6;color:#9a5a1e}
  .ev-badge-kid{background:#e8e3f3;color:#5d4a8a}
  .ev-inline-link{color:var(--green-mid,#2d6a35);font-weight:600;white-space:nowrap}
  .ev-series{margin:.55rem 0 0;font-size:.9rem;color:var(--text-soft,#6b6f63)}
  .ev-series-link{font-weight:600;color:var(--green-mid,#2d6a35)}
  .ev-actions{margin-top:.35rem}
  .agenda-closure{background:#f4f3ef}
  .agenda-closure .ev-title{color:#5a5a5a}

  /* view toggle */
  .ev-viewbar{display:flex;justify-content:space-between;align-items:center;
    gap:1rem;flex-wrap:wrap;margin-bottom:1rem}
  .ev-viewtoggle{display:inline-flex;background:#eceadf;border-radius:999px;padding:.2rem}
  .ev-vbtn{border:0;background:transparent;font:inherit;font-weight:700;font-size:.9rem;
    color:var(--text-soft,#6b6f63);padding:.4rem 1rem;border-radius:999px;cursor:pointer}
  .ev-vbtn.active{background:var(--green-deep,#1e3a24);color:#fff}

  /* save the date */
  .std-card{display:flex;gap:.6rem;align-items:flex-start;background:#fffdf6;
    border:1px solid #ecd9a6;border-left:4px solid var(--gold,#c79a3a);
    border-radius:12px;padding:.7rem .85rem;margin-bottom:.7rem}
  .std-star{font-size:1.1rem;line-height:1.2}
  .std-body{display:flex;flex-direction:column;min-width:0}
  .std-body strong{font-size:1rem;color:var(--green-deep,#1e3a24)}
  .std-date{font-size:.85rem;font-weight:700;color:var(--gold-deep,#9a7414);margin-top:.1rem}
  .std-linkrow{margin-top:.3rem}
  .std-link{font-weight:600;font-size:.88rem;color:var(--green-mid,#2d6a35)}

  /* full calendar — grouped scrolling list, all screen sizes */
  .ml-month{margin-bottom:.5rem;padding:0}
  .ml-title-h{font-size:1.25rem;margin:.4rem 0 .25rem;position:sticky;top:0;z-index:1;
    background:var(--cream,#f7f5ee);padding:.2rem 0;border-bottom:2px solid #d4cab2}
  .ml-month:first-child .ml-title-h{margin-top:0}
  .ml-row{display:flex;align-items:center;gap:.75rem;padding:.55rem .3rem;
    border-bottom:1px solid #ece9df;text-decoration:none;color:inherit;transition:background .12s}
  a.ml-row:hover{background:#faf9f4}
  .ml-dot{flex:0 0 auto;width:11px;height:11px;border-radius:50%}
  .ml-date{flex:0 0 auto;width:72px;font-size:.86rem;font-weight:700;color:var(--text-soft,#6b6f63)}
  .ml-time{font-weight:700;color:var(--text-soft,#6b6f63)}
  .ml-title{flex:1;min-width:0;font-weight:600;font-size:1.02rem;color:var(--green-deep,#23402a);
    overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
  .ml-closure-reason{font-weight:500;color:#8a8d80}
  .ml-chev{flex:0 0 auto;color:#b3b1a4;font-size:1.2rem}

  @media (max-width:760px){
    .ev-card .ev-date{width:72px}
    .ev-date .ev-dnum{font-size:1.45rem}
    .ev-title{flex-basis:100%}
    .ev-titlerow .ev-badges{justify-content:flex-start}
    .ml-title{font-size:.96rem}
  }`;
  const tag = document.createElement('style');
  tag.id = 'psbp-event-styles';
  tag.textContent = css;
  document.head.appendChild(tag);
}

// ── ORCHESTRATOR for events.html ──────────────────────────────
// opts: { agenda, filters, schedule, series, monthList,
//         saveDate, saveDateWrap, viewTitle, windowDays }
async function loadEventsPage(opts){
  opts = opts || {};
  injectEventStyles();
  const $ = id => id ? document.getElementById(id) : null;
  const agendaEl = $(opts.agenda), filtersEl = $(opts.filters),
        schedEl = $(opts.schedule), seriesEl = $(opts.series),
        listEl = $(opts.monthList), calFiltersEl = $(opts.calFilters),
        sdEl = $(opts.saveDate), sdWrap = $(opts.saveDateWrap), titleEl = $(opts.viewTitle);
  try {
    const [events, classes, series, weddingCal] = await Promise.all([
      fetchTab(TAB.events).catch(()=>[]),
      fetchTab(TAB.classes).catch(()=>[]),
      fetchTab(TAB.series).catch(()=>[]),
      fetchTab(TAB.wedding_calendar).catch(()=>[]),
    ]);

    // series lookup (visible only)
    const seriesMap = {};
    series.filter(isWebVisible).forEach(s => { if (s.name) seriesMap[s.name.trim().toLowerCase()] = s; });

    const today = new Date(); today.setHours(12,0,0,0);
    const windowEnd = new Date(today); windowEnd.setDate(windowEnd.getDate() + (opts.windowDays || 14));

    const evItems = events.filter(isWebVisible).map(_eventItem).filter(Boolean);

    // PARK CLOSURES from the wedding_calendar tab: rows flagged closes_park
    // become GENERIC public closures — no private names. (public_note, if set,
    // gives a public label like "Thanksgiving"; otherwise it's generic.)
    const evClosureDays = new Set(evItems.filter(i => i.kind === 'closure').map(i => _ymd(i.date)));
    const seenWed = new Set();
    const wedClosures = (weddingCal || [])
      .filter(r => _isYes(r.closes_park))
      .map(r => {
        const date = parseDateLocal(r.date);
        if (!date) return null;
        return { kind:'closure', date, title:(r.public_note||'').trim(),
                 description:'', category:'Private', _link:{} };
      })
      .filter(Boolean)
      // dedup: skip dates already closed via the events tab, and any repeats.
      .filter(c => { const k = _ymd(c.date);
        if (evClosureDays.has(k) || seenWed.has(k)) return false; seenWed.add(k); return true; });

    const allItems = evItems.concat(wedClosures);
    const closureDays = new Set(allItems.filter(i => i.kind === 'closure').map(i => _ymd(i.date)));
    const notSuppressed = i => i.kind === 'closure' || !closureDays.has(_ymd(i.date));

    // AGENDA — within window: events + sessions + class instances (closures preempt)
    const classInst = expandClasses(classes.filter(isWebVisible), today, windowEnd).filter(notSuppressed);
    const agenda = allItems.filter(i => i.date >= today && i.date <= windowEnd)
      .filter(notSuppressed).concat(classInst).sort(_byDateThenTime);

    if (agendaEl) agendaEl.innerHTML = agenda.length
      ? agenda.map(i => renderAgendaCard(i, seriesMap)).join('')
      : '<p class="text-soft" style="padding:1rem 0">Nothing scheduled in the next two weeks — switch to the full calendar to see what\'s ahead.</p>';

    buildEventFilters(filtersEl, [agendaEl]);

    // FULL CALENDAR — every dated event/closure from the 1st of this month on,
    // NO weekly class instances (those live in the schedule rail). Grouped
    // scrolling list, with its own category / kid-friendly filter.
    const monthStart = new Date(today.getFullYear(), today.getMonth(), 1);
    const monthItems = allItems.filter(i => i.date >= monthStart)
      .filter(notSuppressed).sort(_byDateThenTime);
    const groups = _groupByMonth(monthItems);
    if (listEl) listEl.innerHTML = groups.length
      ? renderMonthList(groups, seriesMap)
      : '<p class="text-soft" style="padding:1rem 0">No upcoming events on the calendar yet.</p>';

    buildEventFilters(calFiltersEl, [listEl],
      { itemSelector: '.ml-rowwrap', groupSelector: '.ml-month' });

    // SAVE THE DATE — flagged marquee events, deduped by title, soonest 2, pinned.
    if (sdEl){
      const seen = new Set();
      const sd = evItems.filter(i => i.kind !== 'closure' && i.save_the_date && i.date >= today)
        .sort(_byDateThenTime)
        .filter(i => { const k=(i.title||'').trim().toLowerCase(); if(seen.has(k)) return false; seen.add(k); return true; })
        .slice(0, 2);
      sdEl.innerHTML = sd.map(renderSaveDate).join('');
      if (sdWrap) sdWrap.style.display = sd.length ? '' : 'none';
    }

    if (schedEl){
      const cls = classes.filter(isWebVisible);
      schedEl.innerHTML = cls.length
        ? cls.map(renderScheduleRow).join('')
        : '<p class="text-soft" style="font-size:.9rem">No weekly classes scheduled right now.</p>';
    }

    if (seriesEl){
      const act = series.filter(isWebVisible).filter(s => !s.active || _isYes(s.active));
      seriesEl.innerHTML = act.length ? act.map(renderSeriesCard).join('') : '';
    }

    // VIEW TOGGLE — "Next 2 weeks" ⇄ "Full calendar"
    const weeksView = $('view-weeks'), calView = $('view-calendar');
    document.querySelectorAll('.ev-vbtn').forEach(btn => {
      btn.addEventListener('click', () => {
        const v = btn.getAttribute('data-view');
        document.querySelectorAll('.ev-vbtn').forEach(x => x.classList.toggle('active', x === btn));
        if (weeksView) weeksView.style.display = (v === 'weeks') ? '' : 'none';
        if (calView)   calView.style.display   = (v === 'calendar') ? '' : 'none';
        if (titleEl)   titleEl.textContent = (v === 'weeks') ? 'The next two weeks' : 'Full calendar';
      });
    });
  } catch(err){
    if (agendaEl) agendaEl.innerHTML =
      '<p class="text-soft" style="padding:1rem">Could not load events. <a href="https://palmasolabp.org/calendar/" target="_blank" rel="noopener">See the park calendar →</a></p>';
  }
}

// ── Simpler single-list renderers (homepage teasers, other pages) ─────
// loadEvents: upcoming dated events only (no class expansion), same card style.
async function loadEvents(containerId, maxItems=8){
  const el = document.getElementById(containerId);
  if (!el) return [];
  injectEventStyles();
  try {
    const [events, series] = await Promise.all([
      fetchTab(TAB.events).catch(()=>[]), fetchTab(TAB.series).catch(()=>[])
    ]);
    const seriesMap = {};
    series.filter(isWebVisible).forEach(s => { if (s.name) seriesMap[s.name.trim().toLowerCase()] = s; });
    const today = new Date(); today.setHours(12,0,0,0);
    const items = events.filter(isWebVisible).map(_eventItem).filter(Boolean)
      .filter(i => i.date >= today).sort(_byDateThenTime).slice(0, maxItems);
    el.innerHTML = items.length
      ? items.map(i => renderAgendaCard(i, seriesMap)).join('')
      : '<p class="text-soft" style="padding:1rem 0">No upcoming events scheduled. Check back soon.</p>';
    return events;
  } catch(err){
    el.innerHTML = '<p class="text-soft" style="padding:1rem">Could not load events. <a href="https://palmasolabp.org/calendar/" target="_blank" rel="noopener">See the park calendar →</a></p>';
    return [];
  }
}

// loadClasses: the weekly schedule list (each class once).
async function loadClasses(containerId){
  const el = document.getElementById(containerId);
  if (!el) return;
  try {
    const rows = (await fetchTab(TAB.classes)).filter(isWebVisible);
    el.innerHTML = rows.length
      ? rows.map(renderScheduleRow).join('')
      : '<p class="text-soft">No classes currently scheduled.</p>';
  } catch(e){
    el.innerHTML = '<p class="text-soft">Could not load classes.</p>';
  }
}

// Announcement link button. No type column needed — PSBP.linkKind reads the
// URL and routes it: a .pdf or published Google Doc frames inside viewer.html,
// an internal page opens same-window, any other site opens in a new tab.
function annButton(a){
  const { url, text } = PSBP.rowLink(a);
  if (!url) return '';
  const isDoc = PSBP.linkKind(url) === 'document';
  const label = (isDoc ? '📄 ' : '') + (text || (isDoc ? 'Read More' : 'Learn more')) + ' →';
  return PSBP.linkTag(url, label, {
    title: a.title || '',
    back: location.pathname.split('/').pop() || 'index.html',
    className: 'ann-link',
    style: 'margin-left:.75rem'
  });
}

// ── ANNOUNCEMENTS — slow-cycling, one at a time ───────────────
async function loadAnnouncements(containerId) {
  const el = document.getElementById(containerId);
  if (!el) return;
  try {
    const rows = await fetchTab(TAB.announcements);
    const visible = rows.filter(r => isWebVisible(r));
    if (!visible.length) { el.style.display='none'; return; }

    // Show the bar
    const bar = el.closest('#announcements-bar') || el.parentElement;
    if (bar) bar.style.display = 'block';

    // Build items — absolute positioned so they fade over each other
    el.style.position = 'relative';
    el.style.minHeight = '48px';

    el.innerHTML = visible.map((a, i) => {
      return `<div class="ann-cycle-item ${i===0?'active':''}" data-idx="${i}" style="
        ${i===0 ? 'position:relative' : 'position:absolute;top:0;left:0;right:0'};
        display:flex;align-items:center;gap:1rem;padding:.6rem 0;
        opacity:${i===0?'1':'0'};transition:opacity 1.2s ease;pointer-events:${i===0?'auto':'none'}
      ">
        ${a.emoji?`<span style="font-size:1.4rem;flex-shrink:0">${a.emoji}</span>`:''}
        <div style="flex:1">
          <strong style="color:var(--white)">${a.title||''}</strong>
          ${a.body?`<span style="color:rgba(255,255,255,.78);font-size:.9rem;margin-left:.5rem">${a.body}</span>`:''}
          ${annButton(a)}
        </div>
      </div>`;
    }).join('');

    // Cycle if more than one
    if (visible.length > 1) {
      let cur = 0;
      setInterval(() => {
        const items = el.querySelectorAll('.ann-cycle-item');
        // Fade out current
        items[cur].style.opacity = '0';
        items[cur].style.pointerEvents = 'none';
        items[cur].style.position = 'absolute';
        // Fade in next
        cur = (cur + 1) % items.length;
        items[cur].style.position = 'relative';
        items[cur].style.opacity = '1';
        items[cur].style.pointerEvents = 'auto';
      }, 5000);
    }

  } catch(e) {
    el.style.display = 'none';
  }
}

// ── VOLUNTEER OF MONTH ────────────────────────────────────────
async function loadVolunteerOfMonth() {
  try {
    const rows = await fetchTab(TAB.volunteer);
    const vol = rows.find(r => isWebVisible(r));
    if (!vol) return;
    const set = (id, val) => { const el=document.getElementById(id); if(el&&val) el.textContent=val; };
    set('volName', vol.name);
    set('volTitle', vol.title || 'Volunteer of the Month');
    set('volBio', vol.bio);
    set('volHours', vol.hours);
    set('volYears', vol.seasons || vol.years);
    if (vol.photo_url) {
      const img = document.getElementById('volAvatar');
      if (img) { img.style.backgroundImage=`url('${vol.photo_url}')`; img.style.backgroundSize='cover'; img.textContent=''; }
    }
  } catch(e) { /* silent */ }
}

// ── PLANT DATA — loaded from plants.json ─────────────────────
// plants.json is generated by generate_plants_json.py
// Run that script any time plant pages are added or updated.
let PLANTS = [];

async function loadPlants() {
  const grid = document.getElementById('plantGrid');
  const ctr  = document.getElementById('plantCount');
  if (!grid) return;

  try {
    // Determine correct path based on subfolder depth
    const pathParts = window.location.pathname.split('/').filter(Boolean);
    const repoIdx   = pathParts.indexOf('explore');
    const inSubfolder = repoIdx >= 0 && pathParts.length > repoIdx + 2;
    const base = inSubfolder ? '../' : '';

    const resp = await fetch(base + 'plants.json');
    if (!resp.ok) throw new Error('plants.json not found');
    PLANTS = await resp.json();
    // Precompute lowercased search fields ONCE on load, so each keystroke is a
    // cheap lookup instead of re-lowercasing every field of every plant.
    PLANTS.forEach(p => {
      p._common  = (p.common  || '').toLowerCase();
      p._sci     = (p.sci     || '').toLowerCase();
      p._family  = (p.family  || '').toLowerCase();
      p._quick   = (p.quick   || '').toLowerCase();
      p._aliases = (p.aliases || []).join(' ').toLowerCase();
    });
    if (ctr) ctr.textContent = PLANTS.length;
    // Update the collection count in the intro text
    const collectionCount = document.getElementById('plantCollectionCount');
    if (collectionCount) collectionCount.textContent = PLANTS.length + '+';
    populatePlantCategories();
    await _ensurePhotoCredits();   // warm the shared credit map before first paint
    renderPlants(orderByFeatured(PLANTS, FEATURED_PLANTS));
    // Apply any URL search/family filter after load
    const searchEl = document.getElementById('plantSearch');
    if (searchEl && searchEl.value) filterPlants();
  } catch(e) {
    console.warn('Could not load plants.json — falling back to empty list.', e);
    if (grid) grid.innerHTML = '<p class="text-soft" style="grid-column:1/-1;padding:2rem;text-align:center">Plant data unavailable. Please try again.</p>';
  }
}

// ── PLANT FILTER ENGINE ───────────────────────────────────────
let _activeFilters = new Set();
let _activeCategory = '';

// ── SHARED PHOTO-CREDIT JOIN ──────────────────────────────────
// One psbp_id -> hero photo record map (photographer, license, observed_on),
// built once from the PSBPPhotos pool (photo_credits.json) and reused by the
// plant, wildlife, AND Right Now cards so every credit block looks identical
// and carries the date. loadPool() is Promise-cached, so this is one fetch.
let _photoCreditById = null;

async function _ensurePhotoCredits() {
  if (_photoCreditById) return _photoCreditById;
  const map = {};
  if (typeof PSBPPhotos !== 'undefined' && PSBPPhotos.loadPool) {
    try {
      const pool = await PSBPPhotos.loadPool();
      (pool || []).forEach(p => { if (p.psbp_id && !map[p.psbp_id]) map[p.psbp_id] = p; });
    } catch (_) { /* fail-soft: cards fall back to their own credit fields */ }
  }
  _photoCreditById = map;
  return map;
}

// Build the standard credit plate for a species record (plant or wildlife).
// Prefers the hero-pool record (gets the date); falls back to the record's own
// credit_name/credit_license; last resort is a bare "community member" plate.
function _speciesCreditPlate(rec) {
  const cr = (_photoCreditById && rec && _photoCreditById[rec.id]) || null;
  const by      = (cr && (cr.photographer_name || cr.photographer)) || rec.credit_name || rec.credit || 'community member';
  const license = (cr && cr.license) || rec.credit_license || '';
  const date    = (cr && cr.observed_on) || null;
  if (typeof PSBPPhotos !== 'undefined' && PSBPPhotos.creditPlate) {
    return PSBPPhotos.creditPlate({ by: by, license: license, date: date });
  }
  return '<div class="credit-plate"><div class="credit-byline">'
       + '<span class="credit-eyebrow">Photograph by</span>'
       + '<span class="credit-name">' + by + '</span></div></div>';
}

function plantCard(p) {
  const slug = p.id + '-' + p.common.replace(/[^a-zA-Z0-9]+/g, '-').replace(/-+/g, '-').replace(/^-|-$/g, '');
  const photoUrl = p.photo || ('plants/' + p.id + '_' + p.common.replace(/[^a-zA-Z0-9]+/g, '_').replace(/_+/g, '_').replace(/^_|_$/g, '') + '.jpg');
  const pageUrl  = p.page  || ('plants/' + slug + '.html');

  return `<a class="card plant-card" href="${pageUrl}" style="text-decoration:none;display:flex;flex-direction:column;height:100%">
    <div style="height:160px;overflow:hidden;position:relative;background:var(--sand)">
      <img src="${photoUrl}" alt="${p.common}"
        style="width:100%;height:100%;object-fit:cover;object-position:${p.focus || 'center'};display:block;transition:transform .4s ease"
        onerror="this.style.display='none';this.nextElementSibling.style.display='flex'"
        loading="lazy">
      <div style="display:none;height:100%;align-items:center;justify-content:center;font-size:2.5rem;color:var(--text-soft);opacity:.3">🌿</div>
    </div>
    <div class="card-body" style="flex:1">
      <h4 style="font-size:.97rem;color:var(--green-deep);line-height:1.3;margin-bottom:.2rem">${p.common}</h4>
      <div class="sci-name">${p.sci}</div>
    </div>
    ${_speciesCreditPlate(p)}
  </a>`;
}

// ── PLANT PAGINATION ─────────────────────────────────────────
const PLANTS_PER_PAGE = 12;
let _plantPage = 0;
let _filteredPlants = [];

function renderPlants(list) {
  _filteredPlants = list;
  _plantPage = 0;
  renderPlantPage();
}

function renderPlantPage() {
  const grid  = document.getElementById('plantGrid');
  const ctr   = document.getElementById('plantCount');
  const label = document.getElementById('plantPageLabel');
  const prev  = document.getElementById('plantPrev');
  const next  = document.getElementById('plantNext');
  const pag   = document.getElementById('plantPagination');
  const info  = document.getElementById('plantPageInfo');
  if (!grid) return;

  const total     = _filteredPlants.length;
  const totalPages = Math.ceil(total / PLANTS_PER_PAGE);
  const start     = _plantPage * PLANTS_PER_PAGE;
  const slice     = _filteredPlants.slice(start, start + PLANTS_PER_PAGE);

  if (ctr) ctr.textContent = total;
  if (info && totalPages > 1) info.textContent = ` — page ${_plantPage + 1} of ${totalPages}`;
  else if (info) info.textContent = '';

  grid.innerHTML = slice.length ? slice.map(plantCard).join('') :
    '<p class="text-soft" style="grid-column:1/-1;padding:2rem;text-align:center">No plants match. Try clearing some filters.</p>';

  // Show/hide pagination
  if (pag) pag.style.display = totalPages > 1 ? 'flex' : 'none';
  if (label) label.textContent = `${_plantPage + 1} of ${totalPages}`;
  if (prev) prev.style.opacity = _plantPage === 0 ? '.3' : '1';
  if (next) next.style.opacity = _plantPage >= totalPages - 1 ? '.3' : '1';

  // Scroll to top of plant grid when page changes
  if (_plantPage > 0) {
    document.getElementById('tabSection')?.scrollIntoView({ behavior: 'smooth', block: 'start' });
  }
}

function stepPlants(dir) {
  const total = Math.ceil(_filteredPlants.length / PLANTS_PER_PAGE);
  _plantPage  = Math.max(0, Math.min(total - 1, _plantPage + dir));
  renderPlantPage();
}

function filterPlants() {
  const q = (document.getElementById('plantSearch')?.value||'').toLowerCase().trim();

  // Apply category + tag filters first
  let pool = PLANTS.filter(p =>
    (!_activeCategory || p.cat === _activeCategory)
    && (!_activeFilters.has('native')    || p.native)
    && (!_activeFilters.has('butterfly') || p.butterfly)
    && (!_activeFilters.has('edible')    || p.edible)
    && (!_activeFilters.has('wetland')   || p.wetland)
  );

  if (!q) { renderPlants(orderByFeatured(pool, FEATURED_PLANTS)); return; }

  // Score each plant — higher score = better match = shown first.
  // Fields were lowercased once at load (see loadPlants), so this is cheap.
  const scored = pool.map(p => {
    let score = 0;
    if (p._common.startsWith(q))      score += 100; // starts with query — top priority
    else if (p._common.includes(q))   score += 80;  // name contains query
    if (p._aliases.includes(q))       score += 70;  // alternate names
    if (p._sci.includes(q))           score += 60;  // scientific name
    if (p._family.includes(q))        score += 40;  // family name
    if (p._quick.includes(q))         score += 20;  // quick hits text

    return { p, score };
  })
  .filter(x => x.score > 0)
  .sort((a, b) => b.score - a.score)
  .map(x => x.p);

  renderPlants(scored);
}

function toggleFilter(type) {
  if (_activeFilters.has(type)) _activeFilters.delete(type);
  else _activeFilters.add(type);
  // Scoped to the Plants panel — Wildlife has its own filter buttons
  document.querySelectorAll('#panel-plants .filter-btn').forEach(b => {
    if (b.dataset.filter===type) b.classList.toggle('on', _activeFilters.has(type));
  });
  filterPlants();
}

function clearFilters() {
  _activeFilters.clear();
  _activeCategory = '';
  document.querySelectorAll('#panel-plants .filter-btn').forEach(b=>b.classList.remove('on'));
  const s=document.getElementById('plantSearch'); if(s) s.value='';
  const sel=document.getElementById('plantCategory'); if(sel) sel.value='';
  filterPlants();
}

// Category dropdown
function setCategory(value) {
  _activeCategory = value || '';
  filterPlants();
}

// Populate the category <select> from whatever categories actually exist in
// the loaded plant data (the JSON field is `cat`). "Plants to Watch & Invasive
// Awareness" is hidden from the dropdown by design — those pages still exist
// and are still searchable, we just don't surface invasives as a browse option.
const _CATEGORY_HIDE = new Set(['Plants to Watch & Invasive Awareness']);
function populatePlantCategories() {
  const sel = document.getElementById('plantCategory');
  if (!sel) return;
  const cats = [...new Set(PLANTS.map(p => p.cat).filter(Boolean))]
    .filter(c => !_CATEGORY_HIDE.has(c))
    .sort();
  // Preserve the existing "All categories" option (index 0) and append the rest.
  while (sel.options.length > 1) sel.remove(1);
  cats.forEach(c => {
    const opt = document.createElement('option');
    opt.value = c; opt.textContent = c;
    sel.appendChild(opt);
  });
}

// Plant modal removed — plant cards now link directly to full detail pages

// ── WILDLIFE DATA — loaded from wildlife.json ─────────────────
// wildlife.json is generated by generate_wildlife_json.py
// Run that script any time wildlife pages are added or updated.
let WILDLIFE = [];

// Category buttons — built dynamically from whatever themes exist
// in wildlife.json, in this order. Add a line here if a new
// theme-* class is ever introduced on the wildlife pages.
const WILD_THEMES = [
  { key: 'bird',      label: '🐦 Birds' },
  { key: 'butterfly', label: '🦋 Butterflies' },
  { key: 'reptile',   label: '🐢 Reptiles' },
  { key: 'amphibian', label: '🐸 Amphibians' },
  { key: 'mammal',    label: '🦝 Mammals' },
];

async function loadWildlife() {
  const grid = document.getElementById('wildGrid');
  if (!grid) return;

  try {
    // Determine correct path based on subfolder depth (same logic as loadPlants)
    const pathParts = window.location.pathname.split('/').filter(Boolean);
    const repoIdx   = pathParts.indexOf('explore');
    const inSubfolder = repoIdx >= 0 && pathParts.length > repoIdx + 2;
    const base = inSubfolder ? '../' : '';

    const resp = await fetch(base + 'wildlife.json');
    if (!resp.ok) throw new Error('wildlife.json not found');
    WILDLIFE = await resp.json();

    // Update the collection count in the intro text
    const collectionCount = document.getElementById('wildCollectionCount');
    if (collectionCount) collectionCount.textContent = WILDLIFE.length + '+';

    renderWildFilterButtons();
    await _ensurePhotoCredits();   // warm the shared credit map before first paint
    renderWildlife(orderByFeatured(WILDLIFE, FEATURED_WILDLIFE));

    // Apply any URL search filter after load
    const searchEl = document.getElementById('wildSearch');
    if (searchEl && searchEl.value) filterWildlife();
  } catch(e) {
    console.warn('Could not load wildlife.json — falling back to empty list.', e);
    grid.innerHTML = '<p class="text-soft" style="grid-column:1/-1;padding:2rem;text-align:center">Wildlife data unavailable. Please try again.</p>';
  }
}

// Build category buttons from the themes actually present in the data
function renderWildFilterButtons() {
  const bar = document.getElementById('wildFilterButtons');
  if (!bar) return;
  const present = new Set(WILDLIFE.map(w => w.theme).filter(Boolean));
  bar.innerHTML = WILD_THEMES
    .filter(t => present.has(t.key))
    .map(t => `<button class="filter-btn" data-wfilter="${t.key}" onclick="toggleWildFilter('${t.key}')">${t.label}</button>`)
    .join('');
}

// ── WILDLIFE CARD ─────────────────────────────────────────────
// Standardized to match plantCard: photo, name, scientific, credit block. No
// chips — theme + native are selectable from the filter bar above the grid.
function wildCard(w) {
  return `<a class="card plant-card" href="${w.page}" style="text-decoration:none;display:flex;flex-direction:column;height:100%">
    <div style="height:160px;overflow:hidden;position:relative;background:var(--sand)">
      <img src="${w.photo}" alt="${w.common}"
        style="width:100%;height:100%;object-fit:cover;object-position:${w.focus || 'center'};display:block;transition:transform .4s ease"
        onerror="this.style.display='none';this.nextElementSibling.style.display='flex'"
        loading="lazy">
      <div style="display:none;height:100%;align-items:center;justify-content:center;font-size:2.5rem;color:var(--text-soft);opacity:.3">🦜</div>
    </div>
    <div class="card-body" style="flex:1">
      <h4 style="font-size:.97rem;color:var(--green-deep);line-height:1.3;margin-bottom:.2rem">${w.common}</h4>
      <div class="sci-name">${w.sci}</div>
    </div>
    ${_speciesCreditPlate(w)}
  </a>`;
}

// ── WILDLIFE PAGINATION ───────────────────────────────────────
const WILD_PER_PAGE = 12;   // matches PLANTS_PER_PAGE for a consistent grid
let _wildPage = 0;
let _filteredWild = [];

function renderWildlife(list) {
  _filteredWild = list;
  _wildPage = 0;
  renderWildPage();
}

function renderWildPage() {
  const grid  = document.getElementById('wildGrid');
  const ctr   = document.getElementById('wildCount');
  const label = document.getElementById('wildPageLabel');
  const prev  = document.getElementById('wildPrev');
  const next  = document.getElementById('wildNext');
  const pag   = document.getElementById('wildPagination');
  const info  = document.getElementById('wildPageInfo');
  if (!grid) return;

  const total      = _filteredWild.length;
  const totalPages = Math.ceil(total / WILD_PER_PAGE);
  const start      = _wildPage * WILD_PER_PAGE;
  const slice      = _filteredWild.slice(start, start + WILD_PER_PAGE);

  if (ctr) ctr.textContent = total;
  if (info && totalPages > 1) info.textContent = ` — page ${_wildPage + 1} of ${totalPages}`;
  else if (info) info.textContent = '';

  grid.innerHTML = slice.length ? slice.map(wildCard).join('') :
    '<p class="text-soft" style="grid-column:1/-1;padding:2rem;text-align:center">No animals match. Try clearing some filters.</p>';

  if (pag) pag.style.display = totalPages > 1 ? 'flex' : 'none';
  if (label) label.textContent = `${_wildPage + 1} of ${totalPages}`;
  if (prev) prev.style.opacity = _wildPage === 0 ? '.3' : '1';
  if (next) next.style.opacity = _wildPage >= totalPages - 1 ? '.3' : '1';

  // Scroll back to top of tab section when page changes
  if (_wildPage > 0) {
    document.getElementById('tabSection')?.scrollIntoView({ behavior: 'smooth', block: 'start' });
  }
}

function stepWild(dir) {
  const total = Math.ceil(_filteredWild.length / WILD_PER_PAGE);
  _wildPage   = Math.max(0, Math.min(total - 1, _wildPage + dir));
  renderWildPage();
}

// ── WILDLIFE FILTER ENGINE ────────────────────────────────────
// Category filters are OR'd together (an animal is one category),
// unlike plant tag filters which are AND'd.
let _wildFilters = new Set();

function filterWildlife() {
  const q = (document.getElementById('wildSearch')?.value||'').toLowerCase().trim();

  // Category filter first — empty set means show all
  let pool = _wildFilters.size
    ? WILDLIFE.filter(w => _wildFilters.has(w.theme))
    : WILDLIFE.slice();

  if (!q) { renderWildlife(orderByFeatured(pool, FEATURED_WILDLIFE)); return; }

  // Score each animal — higher score = better match = shown first
  const scored = pool.map(w => {
    const common  = (w.common||'').toLowerCase();
    const sci     = (w.sci||'').toLowerCase();
    const family  = (w.family||'').toLowerCase();
    const quick   = (w.quick||'').toLowerCase();
    const aliases = (w.aliases||[]).join(' ').toLowerCase();
    const tags    = (w.tags||[]).join(' ').toLowerCase();
    const cat     = (w.category||'').toLowerCase();

    let score = 0;
    if (common.startsWith(q))         score += 100; // starts with query — top priority
    else if (common.includes(q))      score += 80;  // name contains query
    if (aliases.includes(q))          score += 70;  // alternate names
    if (sci.includes(q))              score += 60;  // scientific name
    if (family.includes(q))           score += 40;  // family name
    if (cat.includes(q))              score += 35;  // category label
    if (tags.includes(q))             score += 30;  // keyword tags
    if (quick.includes(q))            score += 20;  // quick hits text

    return { w, score };
  })
  .filter(x => x.score > 0)
  .sort((a, b) => b.score - a.score)
  .map(x => x.w);

  renderWildlife(scored);
}

function toggleWildFilter(theme) {
  if (_wildFilters.has(theme)) _wildFilters.delete(theme);
  else _wildFilters.add(theme);
  document.querySelectorAll('#panel-wildlife .filter-btn').forEach(b => {
    if (b.dataset.wfilter===theme) b.classList.toggle('on', _wildFilters.has(theme));
  });
  filterWildlife();
}

function clearWildFilters() {
  _wildFilters.clear();
  document.querySelectorAll('#panel-wildlife .filter-btn').forEach(b=>b.classList.remove('on'));
  const s=document.getElementById('wildSearch'); if(s) s.value='';
  filterWildlife();
}

// ── RIGHT NOW IN THE PARK — the now-lens (blooms + sightings) ─────────────────
// Reads data/published/right_now.json (a MIGRATED flat-array feed), keeps the
// web-visible rows, joins psbp_id -> species record (by its `id`) for the hero
// photo + profile link, and paints cards in the plant/wildlife style. Fail-soft
// on every join: a blank or unmatched psbp_id still renders a clean glyph card.
const RN_PILL = {
  blooming: 'In bloom', budding: 'Budding', fruiting: 'Fruiting',
  fading: 'Fading', sighting: 'Spotted',
};
let _rnSpeciesById = null;

async function _rnSpeciesIndex(base) {
  if (_rnSpeciesById) return _rnSpeciesById;
  const map = {};
  await Promise.all(['plants.json', 'wildlife.json'].map(async f => {
    try {
      const r = await fetch(base + f);
      if (!r.ok) return;
      (await r.json()).forEach(rec => { if (rec && rec.id) map[rec.id] = rec; });
    } catch (_) { /* fail-soft: the join just won't resolve */ }
  }));
  _rnSpeciesById = map;
  return map;
}

function _rnEsc(s) {
  return String(s == null ? '' : s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;')
    .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

function _rnCard(e, rec, cr) {
  const kind  = (e.kind || 'blooming').toLowerCase();
  const pill  = RN_PILL[kind] || 'In the park';
  const sci   = e.scientific_name || (rec && rec.sci) || (cr && cr.scientific_name) || '';
  const photo = (rec && rec.photo) || (cr && cr.photo_url) || '';
  const page  = (rec && rec.page)  || '';
  const focus = (rec && rec.focus) || (cr && cr.focus) || 'center';
  const isSighting = kind === 'sighting';
  const glyph  = isSighting ? '🦜' : '🌿';
  const pillBg = isSighting ? 'var(--green-deep)' : 'var(--gold)';
  const pillFg = isSighting ? '#fff' : 'var(--green-deep)';

  // Attribution through the shared PSBPPhotos layer — the SAME plate the plant
  // cards use (photographer + date + CC license). Species already sits in the
  // card body, so creditPlate (not the fuller attribution overlay) is right.
  let plate = '';
  if (cr && typeof PSBPPhotos !== 'undefined' && PSBPPhotos.creditPlate) {
    plate = PSBPPhotos.creditPlate({
      by:      cr.photographer_name || cr.photographer,
      license: cr.license,
      date:    cr.observed_on || null,
    });
  }

  const inner = `
    <div style="height:170px;overflow:hidden;position:relative;background:var(--sand)">
      ${photo ? `<img src="${_rnEsc(photo)}" alt="${_rnEsc(e.common_name)}" style="width:100%;height:100%;object-fit:cover;object-position:${_rnEsc(focus)};display:block" onerror="this.style.display='none';this.nextElementSibling.style.display='flex'" loading="lazy">` : ''}
      <div style="display:${photo ? 'none' : 'flex'};height:100%;align-items:center;justify-content:center;font-size:2.6rem;color:var(--text-soft);opacity:.3">${glyph}</div>
      <span style="position:absolute;top:.6rem;left:.6rem;background:${pillBg};color:${pillFg};font-size:.68rem;font-weight:700;text-transform:uppercase;letter-spacing:.05em;padding:.22rem .6rem;border-radius:20px;box-shadow:0 1px 5px rgba(0,0,0,.25)">${_rnEsc(pill)}</span>
    </div>
    <div class="card-body" style="flex:1">
      <h4 style="font-size:1rem;color:var(--green-deep);line-height:1.3;margin-bottom:.2rem">${_rnEsc(e.common_name)}</h4>
      ${sci ? `<div class="sci-name" style="margin-bottom:.45rem">${_rnEsc(sci)}</div>` : ''}
      ${e.note ? `<p style="font-size:.86rem;color:var(--text-soft);line-height:1.5;margin:0 0 .55rem">${_rnEsc(e.note)}</p>` : ''}
      ${e.area ? `<div style="font-size:.78rem;color:var(--green-mid);font-weight:600">📍 ${_rnEsc(e.area)}</div>` : ''}
    </div>
    ${plate}`;
  return page
    ? `<a class="card plant-card" href="${_rnEsc(page)}" style="text-decoration:none;display:flex;flex-direction:column;height:100%">${inner}</a>`
    : `<div class="card plant-card" style="display:flex;flex-direction:column;height:100%">${inner}</div>`;
}

// loadRightNow(targetId, { limit, sectionId })
//   targetId  — grid container to fill
//   limit     — max cards (home strip ~6; the full view omits it for all)
//   sectionId — optional wrapping <section> to HIDE when the feed is empty
//               (an empty Right Now is a valid state — the page falls through)
async function loadRightNow(targetId, opts) {
  opts = opts || {};
  const el = document.getElementById(targetId);
  if (!el) return;

  const pathParts = window.location.pathname.split('/').filter(Boolean);
  const repoIdx = pathParts.indexOf('explore');
  const inSubfolder = repoIdx >= 0 && pathParts.length > repoIdx + 2;
  const base = inSubfolder ? '../' : '';

  let rows;
  try { rows = await fetchTab(TAB.right_now); }   // MIGRATED -> data/published/right_now.json
  catch (_) { rows = []; }

  rows = (rows || []).filter(r => WEB_DISPLAY.has(r.display));   // web + both only
  if (opts.limit) rows = rows.slice(0, opts.limit);

  const section = opts.sectionId ? document.getElementById(opts.sectionId) : null;
  if (!rows.length) {
    if (section) section.style.display = 'none';   // empty is valid — hide the band
    el.innerHTML = '';
    return;
  }
  const byId = await _rnSpeciesIndex(base);
  const creditById = await _ensurePhotoCredits();   // shared psbp_id -> hero credit map

  el.innerHTML = rows.map(e => {
    const rec = e.psbp_id ? byId[e.psbp_id] : null;
    const cr  = e.psbp_id ? creditById[e.psbp_id] : null;
    return _rnCard(e, rec, cr);
  }).join('');
}
