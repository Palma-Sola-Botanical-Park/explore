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
    // Flyers are JPGs in the repo. Treated as documents so they open inside
    // viewer.html — banner, title, Back — instead of dumping the visitor on a
    // bare full-screen image with nothing but the browser Back button.
    if (/\.(jpe?g|png|webp|gif)($|[?#])/i.test(u)) return 'document';
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
      // flyer_url/flyer_text are the series tab's names for the same thing
      // events and classes call link_url/link_text. Accepting both means
      // nobody has to remember which tab uses which word.
      url:  ((row.link_url || row.flyer_url || row.pdf_url || row.link || '') + '').trim(),
      // Strip a trailing arrow. Every renderer appends " →" itself, and the
      // sheet's link_text often already ends in one ("See what's coming →"),
      // which rendered as a double arrow on all four series cards. Cheaper to
      // tolerate here than to police what Bev types.
      text: (row.link_text || row.flyer_text || row.pdf_link_text || '')
              .replace(/\s*(?:→|->|»)\s*$/, '').trim()
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

// ── BROWSE ORDER ──────────────────────────────────────────────
// Rewritten 2026-08-28. The index used to render in PSBP-ID order — the order
// species happened to be CATALOGUED in — so the first screen was frozen for
// good and the rarest holdings sat on the last page (the Extinct-in-the-Wild
// Fiji Fan Palm was card 190-odd). Now:
//
//   pins  →  Feature tier  →  Standard  →  Background
//
// shuffled WITHIN each tier and dealt round-robin across form, so the grid is
// different every day, leads with the best of the collection, and never shows
// six palms in a row (69 of 233 plants are palms; a blind shuffle clumps them).
//
// The shuffle is seeded by the DATE, not by page load. This matters: a
// per-load shuffle reorders the grid when someone taps a plant and hits back,
// and makes "the third one down" meaningless. Seeded by date it changes daily
// and holds still all day.
//
// Tiers are curated in plant_signage.json / wildlife_signage.json and emitted
// as `tier` by the publishers. See FEATURE_TIER.md — review every six months.

// Optional hand-pins. Anything listed here jumps the queue, in this order, and
// is exempt from the shuffle — for a plant in bloom, or one tied to an event.
// Leave empty for the normal tiered rotation. List PSBP IDs exactly.
const FEATURED_PLANTS = [];
const FEATURED_WILDLIFE = [];

const TIER_RANK = { Feature: 0, Standard: 1, Background: 2 };

// Days since epoch — the shuffle seed. Same all day, different tomorrow.
function _daySeed() {
  return Math.floor(Date.now() / 86400000);
}

// Small deterministic PRNG (mulberry32). Same seed, same sequence, so the
// order is reproducible for everyone looking on the same day.
function _rng(seed) {
  let a = (seed >>> 0) + 0x6D2B79F5;
  return function () {
    a = (a + 0x6D2B79F5) >>> 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

function _shuffle(arr, rand) {
  const a = arr.slice();
  for (let i = a.length - 1; i > 0; i--) {
    const j = Math.floor(rand() * (i + 1));
    [a[i], a[j]] = [a[j], a[i]];
  }
  return a;
}

// Spread forms evenly across the tier so adjacent cards look different.
// NOT round-robin: the form buckets are wildly uneven (14 Feature palms vs 2
// groundcovers), and one-per-round exhausts the small buckets in the first
// few rows — which would put the same two groundcovers on screen one every
// day and leave a run of six palms at the end. Instead each item takes a
// proportional slot, (i + 0.5) / bucketSize, so a big bucket appears
// throughout and a small one is spaced right across the run.
function _interleave(list, rand) {
  const buckets = new Map();
  list.forEach(item => {
    const k = item.form || item.category || item.cat || '';
    if (!buckets.has(k)) buckets.set(k, []);
    buckets.get(k).push(item);
  });
  if (buckets.size < 2) return list.slice();
  const slotted = [];
  buckets.forEach(items => {
    items.forEach((item, i) => {
      // tiny seeded jitter breaks ties between equal-sized buckets, so the
      // same form doesn't lead every day
      slotted.push({ item, pos: (i + 0.5) / items.length + rand() * 1e-3 });
    });
  });
  return slotted.sort((a, b) => a.pos - b.pos).map(x => x.item);
}

// Order a browse pool: pins first, then by tier, shuffled and interleaved
// within each tier. Used for the default grid and for filtered pools; the
// search path sorts by relevance instead and never calls this.
function orderByFeatured(list, featuredIds) {
  const pinRank = new Map((featuredIds || []).map((id, i) => [id, i]));
  const pins = [], rest = [];
  list.forEach(p => (pinRank.has(p.id) ? pins : rest).push(p));
  pins.sort((a, b) => pinRank.get(a.id) - pinRank.get(b.id));

  const seed = _daySeed();
  const tiers = [[], [], []];
  rest.forEach(p => {
    const r = TIER_RANK[p.tier];
    tiers[r === undefined ? 1 : r].push(p);
  });

  const out = pins.slice();
  tiers.forEach((group, i) => {
    if (!group.length) return;
    const rand = _rng(seed + i * 7919);   // distinct stream per tier
    out.push(..._interleave(_shuffle(group, rand), rand));
  });
  return out;
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
      console.error(`published/${name}.json -> ${r.status}. Serving nothing for this feed.`);
    } catch (e) {
      console.error(`published/${name}.json failed to parse (${e}). Serving nothing for this feed.`);
    }
    // NO SILENT FALLBACK TO THE LIVE SHEET (changed 2026-09-03).
    // This used to `return fetchTabLive(gid)` here, which meant one missing
    // published file put every visitor back on the browser-side CSV parse —
    // the exact architecture the 2026-06-14 blackout killed, running with
    // nothing to announce it. The published files hold last-known-good and are
    // never emptied by a bad sync, so a failure here is a DEPLOY problem, and
    // masking it with stale sheet data is worse than showing nothing.
    // Break-glass is still available and now has to be asked for: set
    // DATA_SOURCE = 'live' at the top of this file.
    return [];
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
<style>
  /* Top-nav spacing + hover dropdowns (desktop only; mobile uses the grouped list) */
  @media (min-width:861px){
    #site-nav .nav-links{display:flex;align-items:stretch;gap:2.6rem;height:64px}
    #site-nav .nav-links > li{display:flex;align-items:center;margin:0;padding:0}
    #site-nav .nav-links > li.has-sub{position:relative}
    #site-nav .subnav{
      position:absolute;top:100%;left:0;min-width:192px;margin:0;padding:.4rem 0;
      list-style:none;background:var(--green-deep);
      border-top:2px solid var(--gold);border-radius:0 0 10px 10px;
      box-shadow:0 16px 32px rgba(0,0,0,.32);
      opacity:0;visibility:hidden;pointer-events:none;
      transform:translateY(-6px);transition:opacity .15s ease,transform .15s ease;z-index:950;
    }
    #site-nav .nav-links > li.has-sub:last-child .subnav{left:auto;right:0}
    #site-nav .nav-links > li.has-sub:hover > .subnav,
    #site-nav .nav-links > li.has-sub:focus-within > .subnav{
      opacity:1;visibility:visible;pointer-events:auto;transform:translateY(0);
    }
    #site-nav .subnav li{display:block;margin:0}
    #site-nav .subnav a{
      display:block;padding:.5rem 1.15rem;white-space:nowrap;border-bottom:0;
      font-size:.95rem;font-weight:600;letter-spacing:.02em;color:rgba(255,255,255,.82);
    }
    #site-nav .subnav a:hover,#site-nav .subnav a:focus{background:rgba(255,255,255,.10);color:var(--white)}
  }
  @media (min-width:861px) and (max-width:1100px){#site-nav .nav-links{gap:1.7rem}}
  /* Mobile: grouped sub-items under each top link inside the hamburger panel.

     The panel paints its own background here, exactly as the desktop .subnav
     above does, instead of inheriting one from the stylesheet. It used not to,
     and that is what broke it: these link colours were white — chosen to match
     the dark-green desktop dropdowns — while the panel background came from
     psbp.css, which is white. White on white. Every entry was still in the page
     and still tappable; none of the sub-entries could be seen.

     Keeping background and text together in one block means the menu renders
     correctly whether a page loads psbp.css or the older site.css, and cannot
     be broken again by a change to either. Every var() below carries a literal
     fallback for the same reason. */
  #navMobile{background:#fff}
  #navMobile .nm-top{
    font-weight:700;color:var(--green-deep,#1a3a1f);
    border-top:1px solid var(--border,#e6e2d6);margin-top:.35rem;
  }
  #navMobile .nm-top:first-child{border-top:0;margin-top:0}
  #navMobile .nm-sub{padding-left:2.4rem;font-size:.95rem;font-weight:500;color:var(--text-soft,#5c5f56)}
  #navMobile .nm-sub:hover,#navMobile .nm-sub:focus{color:var(--green-deep,#1a3a1f)}
  /* Make the open mobile menu fit the screen and scroll inside itself —
     otherwise the list overflows past the viewport and can't be reached,
     and touch-scroll leaks through to the page behind it. */
  #navMobile{max-height:calc(100vh - 64px);max-height:calc(100dvh - 64px);overflow-y:auto;-webkit-overflow-scrolling:touch;overscroll-behavior:contain}
</style>
<nav id="site-nav">
  <ul class="nav-links">
    <li class="has-sub">
      <a href="index.html">Home</a>
      <ul class="subnav">
        <li><a href="index.html#happening">What's On</a></li>
        <li><a href="index.html#whatsHere">What's Here</a></li>
        <li><a href="index.html#rightNowSection">Right Now</a></li>
        <li><a href="news.html">News</a></li>
      </ul>
    </li>
    <li class="has-sub">
      <a href="visit.html">Visit</a>
      <ul class="subnav">
        <li><a href="visit.html#getting-here">Getting here</a></li>
        <li><a href="visit.html#the-layout">Park Map</a></li>
        <li><a href="visit.html#wander">Where to wander</a></li>
        <li><a href="visit.html#tours">Tours</a></li>
      </ul>
    </li>
    <li class="has-sub">
      <a href="events.html">Events</a>
      <ul class="subnav">
        <li><a href="events.html#weeks">Next 2 Weeks</a></li>
        <li><a href="events.html#calendar">Full Schedule</a></li>
      </ul>
    </li>
    <li class="has-sub">
      <a href="venue.html">Venue</a>
      <ul class="subnav">
        <li><a href="venue.html#weddings">Weddings</a></li>
        <li><a href="venue.html#rentals">Other Events</a></li>
        <li><a href="venue.html#layout">Park Map</a></li>
      </ul>
    </li>
    <li class="has-sub">
      <a href="get-involved.html">Get Involved</a>
      <ul class="subnav">
        <li><a href="get-involved.html#donate">Donate</a></li>
        <li><a href="get-involved.html#member">Membership</a></li>
        <li><a href="get-involved.html#volunteer">Volunteering</a></li>
        <li><a href="get-involved.html#citizen-science">Citizen Science</a></li>
      </ul>
    </li>
    <li class="has-sub">
      <a href="contact.html">About</a>
      <ul class="subnav">
        <li><a href="contact.html#mission">Mission</a></li>
        <li><a href="contact.html#organization">Organization</a></li>
        <li><a href="contact.html#community-links">Community Links</a></li>
        <li><a href="contact.html#youtube">YouTube videos</a></li>
        <li><a href="contact.html#visit">Contact</a></li>
      </ul>
    </li>
  </ul>
  <a href="index.html" class="nav-logo">
    <img src="images/white_PSBP_logo.png" alt="Palma Sola Botanical Park">
  </a>
  <button class="nav-hamburger" id="navHamburger" aria-label="Menu">
    <span></span><span></span><span></span>
  </button>
</nav>
<div class="nav-mobile" id="navMobile">
  <a href="index.html" class="nm-top">Home</a>
  <a href="index.html#happening" class="nm-sub">What's On</a>
  <a href="index.html#whatsHere" class="nm-sub">What's Here</a>
  <a href="index.html#rightNowSection" class="nm-sub">Right Now</a>
  <a href="news.html" class="nm-sub">News</a>
  <a href="visit.html" class="nm-top">Visit</a>
  <a href="visit.html#getting-here" class="nm-sub">Getting here</a>
  <a href="visit.html#the-layout" class="nm-sub">Park Map</a>
  <a href="visit.html#wander" class="nm-sub">Where to wander</a>
  <a href="visit.html#tours" class="nm-sub">Tours</a>
  <a href="events.html" class="nm-top">Events</a>
  <a href="events.html#weeks" class="nm-sub">Next 2 Weeks</a>
  <a href="events.html#calendar" class="nm-sub">Full Schedule</a>
  <a href="venue.html" class="nm-top">Venue</a>
  <a href="venue.html#weddings" class="nm-sub">Weddings</a>
  <a href="venue.html#rentals" class="nm-sub">Other Events</a>
  <a href="venue.html#layout" class="nm-sub">Park Map</a>
  <a href="get-involved.html" class="nm-top">Get Involved</a>
  <a href="get-involved.html#donate" class="nm-sub">Donate</a>
  <a href="get-involved.html#member" class="nm-sub">Membership</a>
  <a href="get-involved.html#volunteer" class="nm-sub">Volunteering</a>
  <a href="get-involved.html#citizen-science" class="nm-sub">Citizen Science</a>
  <a href="contact.html" class="nm-top">About</a>
  <a href="contact.html#mission" class="nm-sub">Mission</a>
  <a href="contact.html#organization" class="nm-sub">Organization</a>
  <a href="contact.html#community-links" class="nm-sub">Community Links</a>
  <a href="contact.html#youtube" class="nm-sub">YouTube videos</a>
  <a href="contact.html#visit" class="nm-sub">Contact</a>
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
        <li><a href="index.html#rightNowSection">Right Now</a></li>
        <li><a href="nature.html#plants">Plants &amp; Wildlife</a></li>
        <li><a href="news.html">Park News</a></li>
      </ul>
    </div>
    <div class="footer-col">
      <h4>Plan Your Visit</h4>
      <ul>
        <li><a href="visit.html">Hours & Directions</a></li>
        <li><a href="events.html">Events & Classes</a></li>
        <li><a href="venue.html">Venue Rentals</a></li>
      </ul>
    </div>
    <div class="footer-col">
      <h4>Get Involved</h4>
      <ul>
        <li><a href="get-involved.html#donate">Donate</a></li>
        <li><a href="get-involved.html#member">Membership</a></li>
        <li><a href="get-involved.html#volunteer">Volunteer</a></li>
      </ul>
    </div>
  </div>
  <div class="footer-bottom">
    <span class="footer-util"><a href="contact.html">About</a> · <a href="contact.html#visit">Contact</a> · © 2026 Palma Sola Botanical Park Foundation, Inc. · 9800 17th Ave NW, Bradenton FL 34209</span>
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
      return;
    }
    // Close the mobile menu when a link inside it is tapped, so same-page
    // anchors (e.g. "Your Park", "Right Now") don't leave it frozen open
    // over the page while it quietly scrolls behind.
    if (e.target.closest('#navMobile a')) {
      const mob = document.getElementById('navMobile');
      if (mob) mob.classList.remove('open');
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

// Controlled category vocabulary → badge emoji + accent color.
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
  // `key` must stay 'Private' — it is one of the eight values the schemas
  // accept in the sheet's category column. `label` is what a visitor reads:
  // "Private" describes the booking, "Park Closures" describes the thing they
  // actually care about, which is whether they can come that day.
  { key:'Private',            emoji:'🔒', color:'#8a8a8a', label:'Park Closures' },
];
const catMeta = key => EVENT_CATEGORIES.find(c => c.key === (key||'').trim())
  || { key:(key||'').trim(), emoji:'📅', color:'#8a8a8a' };
const catLabel = key => { const m = catMeta(key); return m.label || m.key || key; };

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

// Where clicking an item goes, most specific first:
//   1. its own link_url        — an override: tickets, a Doc, another site
//   2. its own poster          — the flyer links ITSELF, so a normal row needs
//                                only one cell filled in, not two
//   3. its series' link_url    — several classes sharing one flyer
//   4. its series' poster      — same, when the series links nothing else
// Returns { url, text } so the caller can label a poster link sensibly.
function _itemLink(item, seriesMap){
  const own = item._link || {};
  if (own.url)     return { url: own.url, text: own.text || '' };
  if (item.poster) return { url: item.poster, text: own.text || 'See the flyer' };
  const s = seriesMap ? _seriesOf(item, seriesMap) : null;
  if (s){
    const sl = PSBP.rowLink(s);
    if (sl.url) return { url: sl.url, text: sl.text || '' };
    const sp = (s.poster || s.screen_poster || '').trim();
    if (sp) return { url: sp, text: 'See the flyer' };
  }
  return { url: '', text: '' };
}
function _itemHref(item, seriesMap){ return _itemLink(item, seriesMap).url; }

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
    date, date_end: parseDateLocal(e.date_end),
    title: e.title, time: e.time, description: e.description,
    category: eventCategory(e), cost: e.cost, series: e.series,
    fundraiser: _isYes(e.fundraiser), kid_friendly: _isYes(e.kid_friendly),
    save_the_date: _isYes(e.save_the_date),
    // The flyer artwork. `poster` is the name going forward: this column
    // stopped being screen-only on 2026-09-05, when the featured band on
    // events.html started showing it too, and `screen_poster` now misleads.
    // Both spellings are read so the sheet header can be renamed whenever —
    // nothing breaks in between, and nothing has to be renamed at once.
    poster: (e.poster || e.screen_poster || '').trim(),
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
        // A class may belong to a series (Bright Futures is a weekly class AND
        // a program with a flyer). Blank for most classes, which is the norm.
        category: eventCategory(c), cost: c.cost, series: c.series || '',
        // Carried so a class with a poster and no link_url still opens its
        // flyer — same self-linking rule events get.
        poster: (c.poster || c.screen_poster || '').trim(),
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
// NOTE: deliberately NOT given seriesMap. On an agenda card the series already
// has its own line ("Part of the … →"), so resolving the series flyer here too
// would print two links to the same file, one above the other.
function _inlineLink(item){
  const { url, text: t } = _itemLink(item);
  if (!url) return '';
  const text = t || 'more';
  return ' ' + PSBP.linkTag(url, _evEsc(text) + ' →',
    { title: item.title || '', back: _BACK(), className: 'ev-inline-link' });
}

// "Part of the {series} →" sentence, the series name linking the flyer.
function _seriesLine(item, seriesMap){
  if (!item.series) return '';
  const label = item.series.trim();
  const s = _seriesOf(item, seriesMap);
  const sLink = s ? PSBP.rowLink(s) : { url:'' };

  // A class can share its series' name — Bright Futures is both a weekly class
  // and the program. "Bright Futures — Part of the Bright Futures" helps
  // nobody, but returning nothing left the card with NO link at all, because
  // the inline link deliberately does not reach the series. So: drop the
  // sentence, keep the link.
  if (label.toLowerCase() === (item.title || '').trim().toLowerCase()){
    if (!sLink.url) return '';
    return `<div class="ev-series">${PSBP.linkTag(sLink.url, (sLink.text || 'See the flyer') + ' →',
      { title: label, back: _BACK(), className:'ev-series-link' })}</div>`;
  }

  if (s && sLink.url)
    return `<div class="ev-series">Part of the ${PSBP.linkTag(sLink.url, _evEsc(label)+' →',
      { title: label, back: _BACK(), className:'ev-series-link' })}</div>`;
  return `<div class="ev-series">Part of the ${_evEsc(label)}</div>`;
}

function _badges(item){
  const out = [];
  const cm = catMeta(item.category);
  if (item.category)
    out.push(`<span class="ev-badge" style="background:${cm.color}1a;color:${cm.color}">${cm.emoji} ${_evEsc(catLabel(item.category))}</span>`);
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
    // A wedding shuts the park for the afternoon, not the whole day — so say
    // which. Blank close_time keeps the old all-day wording, which is the safe
    // reading and what the schema documents.
    const shutAt = (item.close_time || '').trim();
    const head   = shutAt ? `🔒 Park closes ${_evEsc(shutAt)}` : '🔒 Park closed';
    const label  = (item.title || '').trim();
    const body   = item.description ? clip(item.description,160)
      : shutAt
        ? `The park is open as usual that morning and closes to the public at ${_evEsc(shutAt)} for a private event — please plan your visit around it.`
        : 'The park is closed to the public this day for a private event — please plan your visit around it.';
    return `<div class="event-card ev-card agenda-closure" data-category="Private" data-always="1">
      ${dateBox}
      <div class="event-info">
        <div class="ev-titlerow"><h4 class="ev-title">${head}${label ? ` — ${_evEsc(label)}` : ''}</h4></div>
        <p>${body}</p>
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
function renderScheduleRow(c, seriesMap){
  const dayLabel = c.day || formatWeekday(c.weekday) || '';
  // A class reaches its flyer through its SERIES when it has no link of its
  // own. That is the whole point of pointing several classes at one series:
  // the flyer lives in one row. Without this fallback, clearing a class's
  // link_url — which is exactly what the model asks you to do — silently
  // dropped the "more" link from this rail.
  let link = PSBP.rowLink(c);
  if (!link.url){
    const p = (c.poster || c.screen_poster || '').trim();
    if (p) link = { url: p, text: 'See the flyer' };
  }
  if (!link.url && c.series && seriesMap){
    const s = seriesMap[c.series.trim().toLowerCase()];
    if (s){ const sl = PSBP.rowLink(s); if (sl.url) link = { url: sl.url, text: sl.text || 'Details' }; }
  }
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
  const sl = PSBP.rowLink(s);
  const link = sl.url
    ? PSBP.linkTag(sl.url, (sl.text||'Learn more')+' →',
        { title:s.name||'', back:_BACK(), className:'series-link' })
    : '';
  return `<div class="series-card">
    <h4>${_evEsc(s.name||'')}</h4>
    ${s.category?`<span class="ev-badge" style="background:${cm.color}1a;color:${cm.color}">${cm.emoji} ${_evEsc(catLabel(s.category))}</span>`:''}
    ${s.blurb?`<p>${_evEsc(s.blurb)}</p>`:''}
    ${link?`<div class="series-actions">${link}</div>`:''}
  </div>`;
}

// One "Save the Date" rail card — a marquee event (Holiday Nights, the gala),
// pinned regardless of how far out it is. Title + date + flyer link, nothing more.
// "Oct 4"  ·  "Dec 17–20"  ·  "Dec 30 – Jan 2"
function _dateSpan(a, b){
  const MO = { month:'short', day:'numeric' };
  const start = a.toLocaleDateString('en-US', MO);
  if (!b || b <= a) return start;
  return (b.getMonth() === a.getMonth())
    ? start + '–' + b.getDate()
    : start + ' – ' + b.toLocaleDateString('en-US', MO);
}

// One featured card for the "Save the Date" band at the top of events.html.
// The flyer IS the card — Bev makes artwork for every big event, and that
// artwork carries the times, prices and address. Shown whole, never cropped,
// because on our flyers that detail sits along the bottom edge.
function renderFeature(item){
  const _l    = _itemLink(item);          // falls back to the poster itself
  const href  = _l.url;
  const label = _l.text || 'See the flyer';
  // Events lead with their date. A series has no date, so it supplies its own
  // eyebrow (its category) — same card, honest top line.
  const when  = item.eyebrow
    ? _evEsc(item.eyebrow)
    : _evEsc(_dateSpan(item.date, item.date_end)) + (item.time ? ` · ${_evEsc(item.time)}` : '');

  // Two <img>s of the same file: a blurred cover behind, the whole flyer in
  // front. Same src, so the browser fetches once. The backdrop is decorative —
  // aria-hidden and empty alt so a screen reader hears the flyer once.
  const poster = item.poster
    ? `<div class="feat-art">
        <img class="feat-art-bg" src="${item.poster}" alt="" aria-hidden="true" loading="lazy">
        <img class="feat-art-fg" src="${item.poster}" alt="${_evEsc(item.title||'')} flyer" loading="lazy">
      </div>`
    : '';

  // Flyer link and ticket link are different things; show whichever exist.
  const actions = [];
  if (href) actions.push(PSBP.linkTag(href, _evEsc(label) + ' →',
    { title:item.title||'', back:_BACK(), className:'feat-link' }));
  if (item.registration_url) actions.push(PSBP.linkTag(item.registration_url, 'Tickets →',
    { title:item.title||'Tickets', back:_BACK(), className:'feat-link feat-link-tickets' }));

  return `<article class="feat-card">
    ${poster}
    <div class="feat-body">
      <div class="feat-when">${when}</div>
      <h3 class="feat-title">${_evEsc(item.title||'')}</h3>
      ${item.description?`<p class="feat-desc">${_evEsc(clip(item.description,150))}</p>`:''}
      ${actions.length?`<div class="feat-actions">${actions.join('')}</div>`:''}
    </div>
  </article>`;
}

// Size the poster window to the artwork actually in the band.
//
// The window is shared by every card, so it has to suit the MOST PORTRAIT
// poster present: pick anything wider and a tall flyer gets letterboxed down
// to a stamp. Widest wins nothing, narrowest wins everything. So take the
// smallest ratio and clamp it — .6 stops one freakishly tall image making a
// column of cards nobody can see past, 16:9 stops a band of banners from
// collapsing to a slit.
//
// Runs once after every poster has loaded, so the band settles in a single
// step instead of twitching as each image arrives. Images that fail to load
// are skipped rather than counted as zero.
function fitBandAspect(container){
  if (!container) return;
  const imgs = [].slice.call(container.querySelectorAll('.feat-art-fg'));
  if (!imgs.length) return;

  const settled = imgs.map(img => img.complete
    ? Promise.resolve(img)
    : new Promise(res => {
        img.addEventListener('load',  () => res(img), { once: true });
        img.addEventListener('error', () => res(null), { once: true });
      }));

  Promise.all(settled).then(list => {
    const ratios = list.filter(Boolean)
      .filter(i => i.naturalWidth && i.naturalHeight)
      .map(i => i.naturalWidth / i.naturalHeight);
    if (!ratios.length) return;
    const ar = Math.min(Math.max(Math.min.apply(null, ratios), 0.6), 16 / 9);
    container.style.setProperty('--feat-ar', String(ar));
  });
}

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

// Expand a multi-day event into one entry per day, for the full calendar.
// Winter Nights runs 17–20 December; listed only on the 17th, somebody
// scanning for "is the park doing anything on the 19th?" sees nothing.
// Capped at 31 days so a typo in date_end cannot generate a thousand rows.
function _expandMultiDay(items){
  const out = [];
  items.forEach(it => {
    const end = it.date_end;
    if (!end || end <= it.date) { out.push(it); return; }
    const cur = new Date(it.date);
    let guard = 0;
    while (cur <= end && guard++ < 31){
      out.push(Object.assign({}, it, { date: new Date(cur) }));
      cur.setDate(cur.getDate() + 1);
    }
  });
  return out;
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
      // Closures lead with WHEN the park shuts, because that is the only thing
      // a visitor can act on. "— Private event" said nothing they needed: it is
      // always a private event, and naming it is not allowed anyway. A public
      // label (public_note, e.g. "Thanksgiving") still shows when there is one.
      const shutAt = (it.close_time || '').trim();
      const pub    = (it.title || '').trim();
      const label = closed
        ? `🔒 Park closed${shutAt ? ` at ${_evEsc(shutAt)}` : ''}` +
          (pub ? `<span class="ml-closure-reason"> — ${_evEsc(pub)}</span>` : '')
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
// wire show/hide. Closures filter like anything else — see the note below.
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
    order.map(k => { const m = catMeta(k); return btn(k, `${m.emoji} ${catLabel(k)}`, false); }).join('') +
    (hasKid ? btn('__kid','👪 Kid-friendly',false) : '');
  container.querySelectorAll('.ev-filter-btn').forEach(b => {
    b.addEventListener('click', () => {
      container.querySelectorAll('.ev-filter-btn').forEach(x => x.classList.remove('active'));
      b.classList.add('active');
      const cat = b.getAttribute('data-cat');
      cardContainers.forEach(c => {
        if (!c) return;
        c.querySelectorAll(itemSel).forEach(el => {
          // Closures used to carry data-always="1" and survive every filter.
          // That drowned the results: somebody who clicks "Kid-friendly" is
          // asking what they can bring a child to, not which days the park
          // shuts. Closures still appear under All, and they have their own
          // "Park Closures" button, so nothing is hidden — it is just answering
          // the question that was asked.
          const show = cat === '__all'
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

// ── Event styles moved to css/psbp.css on 2026-08-18 ──────────────
// This file used to define injectEventStyles(), which built a
// <style id="psbp-event-styles"> tag at runtime and appended it to <head>.
// Those rules now live in the "MODULE: events" block at the bottom of
// css/psbp.css, where the stylesheet can actually own them.
//
// CONSEQUENCE: any page that renders event cards must link css/psbp.css.
// Linking css/site.css alone will render those cards unstyled.
// Per-weekday .ev-date colours and .ml-dot colours are still set inline by
// the renderers below — those are data, not design, and stay here.

// ── ORCHESTRATOR for events.html ──────────────────────────────
// opts: { agenda, filters, schedule, series, monthList,
//         featured, featuredWrap, featuredMax (default 4),
//         saveDate, saveDateWrap (legacy compact rail), viewTitle, windowDays }
async function loadEventsPage(opts){
  opts = opts || {};
  const $ = id => id ? document.getElementById(id) : null;
  const agendaEl = $(opts.agenda), filtersEl = $(opts.filters),
        schedEl = $(opts.schedule), seriesEl = $(opts.series),
        listEl = $(opts.monthList), calFiltersEl = $(opts.calFilters),
        sdEl = $(opts.saveDate), sdWrap = $(opts.saveDateWrap), titleEl = $(opts.viewTitle),
        featEl = $(opts.featured), featWrap = $(opts.featuredWrap);
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
                 // Partial closures: the park shuts at close_time, not all day.
                 // Jennie has been filling this in for months and nothing read
                 // it. Blank still means closed all day — the safe reading.
                 close_time:(r.close_time||'').trim(),
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
    // Multi-day events get a card per day here too — this is a day-by-day
    // list, so a four-night run listed only on its first night leaves the
    // other three looking empty.
    const agenda = _expandMultiDay(allItems)
      .filter(i => i.date >= today && i.date <= windowEnd)
      .filter(notSuppressed).concat(classInst).sort(_byDateThenTime);

    if (agendaEl) agendaEl.innerHTML = agenda.length
      ? agenda.map(i => renderAgendaCard(i, seriesMap)).join('')
      : '<p class="text-soft" style="padding:1rem 0">Nothing scheduled in the next two weeks — switch to the full calendar to see what\'s ahead.</p>';

    buildEventFilters(filtersEl, [agendaEl]);

    // FULL CALENDAR — every dated event/closure from the 1st of this month on,
    // NO weekly class instances (those live in the schedule rail). Grouped
    // scrolling list, with its own category / kid-friendly filter.
    const monthStart = new Date(today.getFullYear(), today.getMonth(), 1);
    const monthItems = _expandMultiDay(allItems)
      .filter(i => i.date >= monthStart)
      .filter(notSuppressed).sort(_byDateThenTime);
    const groups = _groupByMonth(monthItems);
    if (listEl) listEl.innerHTML = groups.length
      ? renderMonthList(groups, seriesMap)
      : '<p class="text-soft" style="padding:1rem 0">No upcoming events on the calendar yet.</p>';

    buildEventFilters(calFiltersEl, [listEl],
      { itemSelector: '.ml-rowwrap', groupSelector: '.ml-month' });

    // SAVE THE DATE — the events Bev flags as the year's big ones.
    //
    // save_the_date WINS over closes_park. Winter Nights is a free public event
    // that also shuts the park to normal daytime use, so it carries both flags;
    // the old filter dropped every closure and the park's biggest event of the
    // year never appeared here at all. A flagged event is featured, full stop.
    //
    // A multi-day event stays featured until its LAST day, not its first.
    const seenStd = new Set();
    const flagged = evItems
      .filter(i => i.save_the_date && (i.date_end || i.date) >= today)
      .sort(_byDateThenTime)
      .filter(i => { const k=(i.title||'').trim().toLowerCase(); if(seenStd.has(k)) return false; seenStd.add(k); return true; });

    // THREE is the ceiling for the poster band — the grid is 3-up on a wide
    // screen, and a fourth poster wraps onto a lonely second row. Anything
    // past three spills into the compact rail card instead of disappearing:
    // still flagged, still dated, just without the artwork.
    // THE BAND IS A POSTER BAND. A flagged event with no artwork does not get
    // a slot — a text-only box beside two flyers is the one genuinely ugly
    // state, and there is already a better home for it. No poster, or past the
    // ceiling, and it goes to the compact rail instead. If NOTHING flagged has
    // a poster the band hides itself and the page falls back to exactly the
    // design that was here before.
    const bandMax   = opts.featuredMax || 3;
    const withArt   = flagged.filter(i => i.poster);
    const band      = withArt.slice(0, bandMax);
    const inBand    = new Set(band);
    const overflow  = flagged.filter(i => !inBand.has(i));

    // BACKFILL — in the quiet part of the year there may be only one or two
    // events worth flagging, and a band of one looks like something failed to
    // load. Active series with their own poster top it up: they are real,
    // ongoing, and they already have artwork. Dated events always win the
    // slots; a series never displaces one.
    if (band.length && band.length < bandMax){
      series.filter(isWebVisible)
        .filter(s => !s.active || _isYes(s.active))
        .filter(s => (s.poster || s.screen_poster || '').trim())
        .slice(0, bandMax - band.length)
        .forEach(s => band.push({
          kind: 'series',
          eyebrow: (s.category || 'Ongoing series').trim(),
          title: s.name,
          description: s.blurb,
          poster: (s.poster || s.screen_poster || '').trim(),
          registration_url: '',
          _link: { url: PSBP.rowLink(s).url, text: PSBP.rowLink(s).text || 'See the flyer' }
        }));
    }

    if (featEl){
      featEl.innerHTML = band.map(renderFeature).join('');
      if (featWrap) featWrap.style.display = band.length ? '' : 'none';
      fitBandAspect(featEl);
    }
    if (sdEl){
      sdEl.innerHTML = overflow.map(renderSaveDate).join('');
      if (sdWrap) sdWrap.style.display = overflow.length ? '' : 'none';
    }

    if (schedEl){
      const cls = classes.filter(isWebVisible);
      schedEl.innerHTML = cls.length
        ? cls.map(c => renderScheduleRow(c, seriesMap)).join('')
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
      '<p class="text-soft" style="padding:1rem">Could not load events. <a href="events.html">See the park calendar →</a></p>';
  }
}

// ── Simpler single-list renderers (homepage teasers, other pages) ─────
// loadEvents: upcoming dated events only (no class expansion), same card style.
// Is an announcement inside its display window today?
// show_from blank = already showing. show_until blank = never expires. Together
// they let the director pre-stage: write three monthly notes in one sitting,
// date each one, and the right one appears and retires without anyone touching
// the sheet again.
function _inWindow(row, today){
  const from  = parseDateLocal((row.show_from  || '').trim());
  const until = parseDateLocal((row.show_until || '').trim());
  if (from  && from  > today) return false;
  if (until && until < today) return false;
  return true;
}

// ── Director's note ───────────────────────────────────────────────────────
// An announcement flagged director_note renders here instead of in the top bar:
// a magazine editor's welcome, in her own voice, under her name. Randy's idea,
// 2026-09-06 — "she can welcome you in and discuss what she wants."
//
// Silent when she hasn't written one, which is the point: the default page has
// no empty slot waiting to be filled, and no stale note sitting in it.
// show_until applies here too, because a welcome dated March, read in
// September, is worse than no welcome at all.
async function loadDirectorNote(opts){
  opts = opts || {};
  const el = document.getElementById(opts.into);
  const wrap = opts.wrap ? document.getElementById(opts.wrap) : null;
  if (!el) return;
  const hide = () => { if (wrap) wrap.style.display = 'none'; };
  try {
    const rows = await fetchTab(TAB.announcements);
    // NOON, not midnight — parseDateLocal builds its dates at T12:00, so a
    // midnight 'today' sits BEFORE a show_from of the same day and hid a note
    // that was meant to start today. The rest of the site already uses noon.
    const today = new Date(); today.setHours(12,0,0,0);
    const notes = (rows || []).filter(r =>
      isWebVisible(r) && _isYes(r.director_note) && _inWindow(r, today));
    if (!notes.length) return hide();

    // With several staged, the one whose window opened MOST RECENTLY wins —
    // so September's note gives way to October's on the day it starts, without
    // anyone editing the sheet. Undated rows sort last, which keeps a
    // never-expiring note from outranking a deliberately dated one.
    notes.sort((a, b) => {
      const fa = parseDateLocal((a.show_from || '').trim());
      const fb = parseDateLocal((b.show_from || '').trim());
      return (fa ? fa.getTime() : -Infinity) - (fb ? fb.getTime() : -Infinity);
    });
    const n = notes[notes.length - 1];
    const by   = opts.byline || 'Beverly Zoller Burdette, Executive Director';
    const when = parseDateLocal((n.show_from || '').trim())
              || parseDateLocal((n.show_until || '').trim());
    const dateLine = when
      ? when.toLocaleDateString('en-US', { month: 'long', year: 'numeric' }) : '';
    const link = (n.link_url || '').trim()
      ? PSBP.linkTag(n.link_url, _evEsc((n.link_text || '').trim() || 'More park news') + ' →',
          { title: n.title || '', back: _BACK(), className: 'dnote-link' })
      : '<a class="dnote-link" href="news.html">More park news →</a>';

    // The portrait is a fixed path, not a sheet column — it is always the same
    // person, and a column she fills in once is a column she has to remember
    // forever. If the file is not there, onerror drops it and the note reads
    // exactly as it would have without it.
    const photo = opts.photo || 'images/staff/director.jpg';
    el.innerHTML =
      `<div class="dnote-lab">From the Director${dateLine ? ` · ${_evEsc(dateLine)}` : ''}</div>` +
      (n.title ? `<h3 class="dnote-title">${_evEsc(n.title)}</h3>` : '') +
      (n.body  ? `<p class="dnote-body">${_evEsc(n.body)}</p>` : '') +
      `<div class="dnote-sign">` +
        `<img class="dnote-face" src="${photo}" alt="" aria-hidden="true" loading="lazy"` +
        ` onerror="this.remove()">` +
        `<span class="dnote-by">— ${_evEsc(by)}</span>` +
      `</div>` +
      `<p class="dnote-more">${link}</p>`;
    if (wrap) wrap.style.display = '';
  } catch(err){ hide(); }
}

// ── Homepage "what's happening" ───────────────────────────────────────────
// Fills three slots from one fetch:
//   posters  — up to 3 flagged events that HAVE artwork, as poster cards
//   upcoming — the ordinary next few days, events + class instances
//   closure  — the next park closure, for the hero (answers "can I come
//              Saturday?", which the homepage never used to answer)
//
// Deliberately mirrors events.html rather than inventing a second set of
// rules: same save_the_date flag, same 3-poster ceiling, same "no poster, no
// band slot". An event in the posters does not repeat in the list below.
async function loadHomeHappening(opts){
  opts = opts || {};
  const $ = id => id ? document.getElementById(id) : null;
  const postersEl = $(opts.posters), upEl = $(opts.upcoming), closEl = $(opts.closure);
  if (!postersEl && !upEl && !closEl) return;

  try {
    const [events, classes, weddingCal] = await Promise.all([
      fetchTab(TAB.events).catch(()=>[]),
      fetchTab(TAB.classes).catch(()=>[]),
      fetchTab(TAB.wedding_calendar).catch(()=>[]),
    ]);

    const today = new Date(); today.setHours(12,0,0,0);
    const evItems = events.filter(isWebVisible).map(_eventItem).filter(Boolean);

    // POSTERS — flagged, has artwork, still to come (a run stays until its last day)
    const seen = new Set();
    const posters = evItems
      .filter(i => i.save_the_date && i.poster && (i.date_end || i.date) >= today)
      .sort(_byDateThenTime)
      .filter(i => { const k=(i.title||'').trim().toLowerCase();
                     if (seen.has(k)) return false; seen.add(k); return true; })
      .slice(0, opts.posterMax || 3);

    if (postersEl){
      postersEl.innerHTML = posters.map(renderFeature).join('');
      const wrap = opts.postersWrap && $(opts.postersWrap);
      if (wrap) wrap.style.display = posters.length ? '' : 'none';
      if (typeof fitBandAspect === 'function') fitBandAspect(postersEl);
    }

    // UPCOMING — two different things, shown differently.
    //
    // A one-off talk on the 24th is NEWS and earns a dated row. Zumba every
    // Monday is a RHYTHM, and expanding it into dated rows produced four
    // near-identical lines that said the same thing four times. So: dated
    // events as rows, standing classes as a single line underneath.
    if (upEl){
      const shown = new Set(posters.map(i => (i.title||'').trim().toLowerCase()));
      const end = new Date(today); end.setDate(end.getDate() + (opts.windowDays || 21));

      const dated = _expandMultiDay(evItems)
        .filter(i => i.kind !== 'closure')
        .filter(i => i.date >= today && i.date <= end)
        .filter(i => !shown.has((i.title||'').trim().toLowerCase()))
        .sort(_byDateThenTime)
        .slice(0, opts.upcomingMax || 3);

      const rows = dated.map(i => {
        const when = i.date.toLocaleDateString('en-US',{weekday:'short',day:'numeric',month:'short'});
        const t = i.time ? `${_evEsc(_startTime(i.time))} · ` : '';
        // Naming the series gives a one-off talk some context — it is not a
        // stray event, it is the third of four in a partnership.
        const ser = (i.series||'').trim()
          ? ` <span class="cx-ser">(part of ${_evEsc(i.series.trim())})</span>` : '';
        return `<div class="cx-row"><span class="d">${_evEsc(when)}</span>
          <span class="t">${t}<b>${_evEsc(i.title||'')}</b>${ser}</span></div>`;
      }).join('');

      // Standing classes — ONE ENTRY PER SHEET ROW, not per title. Basic Hatha
      // Yoga runs Monday 4pm and Wednesday 9am; collapsing it to "Mondays &
      // Wednesdays" was tidy until the times went in, at which point it hid the
      // difference. Five rows in the sheet, five entries here.
      const DAY_ORDER = ['Mon','Tue','Wed','Thu','Fri','Sat','Sun'];
      const weeklyItems = classes.filter(isWebVisible)
        .filter(c => (c.title||'').trim())
        .sort((a,b) => (DAY_ORDER.indexOf((a.weekday||'').trim()) - DAY_ORDER.indexOf((b.weekday||'').trim()))
                    || (_timeKey(a.time) - _timeKey(b.time)))
        .map(c => {
          const day = (c.weekday||'').trim();
          const t   = _startTime(c.time||'');
          return `<div class="cx-wk"><span class="cx-wkwhen">${_evEsc(day)}${t?' '+_evEsc(t):''}</span>
            <span class="cx-wkwhat">${_evEsc((c.title||'').trim())}</span></div>`;
        }).join('');

      const weeklyLine = weeklyItems
        ? `<div class="cx-weekly"><span class="cx-weeklab">Every week</span>
             <div class="cx-wkgrid">${weeklyItems}</div></div>` : '';

      upEl.innerHTML = (rows || weeklyLine)
        ? rows + weeklyLine
        : `<div class="cx-row"><span class="t">Nothing scheduled this week — the park is
           still open every day.</span></div>`;
    }

    // CLOSURE — the soonest one inside the window, from either source.
    if (closEl){
      const evClose = evItems.filter(i => i.kind === 'closure');
      const wedClose = (weddingCal || []).filter(r => _isYes(r.closes_park)).map(r => {
        const date = parseDateLocal(r.date);
        return date ? { date, close_time:(r.close_time||'').trim() } : null;
      }).filter(Boolean);
      const soon = new Date(today); soon.setDate(soon.getDate() + (opts.closureDays || 21));
      const next = evClose.concat(wedClose)
        .filter(c => c.date >= today && c.date <= soon)
        .sort((a,b) => a.date - b.date)[0];

      if (next){
        // DAY FIRST, then the time. Leading with "Closing 3:00 PM" read as
        // "closing at 3 today" — alarming, and wrong. "3:00 PM" also carries a
        // formality the rest of the site does not; 3PM is how anyone says it.
        const when = next.date.toLocaleDateString('en-US',{weekday:'short',day:'numeric',month:'short'});
        const at = (next.close_time || '').trim()
          .replace(/:00/, '').replace(/\s*([AP])\.?M\.?/i, (m,p) => p.toUpperCase()+'M');
        closEl.textContent = at ? `${when} — park closes ${at} for a private event`
                                : `${when} — park closed for a private event`;
        closEl.style.display = '';
      } else {
        closEl.style.display = 'none';
      }
    }
  } catch(err){
    if (postersEl) postersEl.innerHTML = '';
    if (closEl) closEl.style.display = 'none';
    if (upEl) upEl.innerHTML =
      '<div class="cx-row"><span class="t">Could not load events. ' +
      '<a href="events.html">See the calendar →</a></span></div>';
  }
}

async function loadEvents(containerId, maxItems=8){
  const el = document.getElementById(containerId);
  if (!el) return [];
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
    el.innerHTML = '<p class="text-soft" style="padding:1rem">Could not load events. <a href="events.html">See the park calendar →</a></p>';
    return [];
  }
}

// loadClasses: the weekly schedule list (each class once).
async function loadClasses(containerId){
  const el = document.getElementById(containerId);
  if (!el) return;
  try {
    // series comes along so a class with no link of its own can still reach
    // its series' flyer — same fallback as the events page.
    const [rows, series] = await Promise.all([
      fetchTab(TAB.classes).then(r => r.filter(isWebVisible)),
      fetchTab(TAB.series).catch(() => [])
    ]);
    const seriesMap = {};
    series.filter(isWebVisible).forEach(s => { if (s.name) seriesMap[s.name.trim().toLowerCase()] = s; });
    el.innerHTML = rows.length
      ? rows.map(c => renderScheduleRow(c, seriesMap)).join('')
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
    // show_until: the web bar carries NEWS, and news goes stale silently.
    // Anything past its date drops out on its own. Blank never expires, and a
    // date we cannot parse is treated as no date — failing toward "still show
    // it" rather than silently swallowing a message.
    const _today = new Date(); _today.setHours(12,0,0,0);   // noon — see _inWindow
    const visible = rows.filter(r => {
      if (!isWebVisible(r)) return false;
      // A director's note is not a notice — it renders as her welcome on the
      // home page (loadDirectorNote), never as a strip at the top of every page.
      if (_isYes(r.director_note)) return false;
      return _inWindow(r, _today);
    });
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
        ${a.emoji?`<span class="ann-emoji">${a.emoji}</span>`:''}
        <div class="ann-body" style="flex:1">
          <strong>${a.title||''}</strong>
          ${a.body?`<span class="ann-note">${a.body}</span>`:''}
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
    populatePlantForms();
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
let _activeForm = '';

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

  return `<a class="card plant-card" data-kind="plant" href="${pageUrl}" style="text-decoration:none;display:flex;flex-direction:column;height:100%">
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
    (!_activeForm || p.form === _activeForm)
    && (!_activeFilters.has('native')         || p.native)
    && (!_activeFilters.has('butterfly')      || p.butterfly)
    && (!_activeFilters.has('rare_fruit')     || p.rare_fruit)
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
  _activeForm = '';
  document.querySelectorAll('#panel-plants .filter-btn').forEach(b=>b.classList.remove('on'));
  const s=document.getElementById('plantSearch'); if(s) s.value='';
  const sel=document.getElementById('plantForm'); if(sel) sel.value='';
  filterPlants();
}

// Form dropdown
function setPlantForm(value) {
  _activeForm = value || '';
  filterPlants();
}

// Populate the form <select> from whatever plant forms actually exist in the
// loaded data (the JSON field is `form`: Tree, Shrub & Vine, Palm & Cycad,
// Foliage & Accent, Groundcover & Wildflower, Aquatic & Wetland).
function populatePlantForms() {
  const sel = document.getElementById('plantForm');
  if (!sel) return;
  const forms = [...new Set(PLANTS.map(p => p.form).filter(Boolean))].sort();
  // Preserve the existing "All forms" option (index 0) and append the rest.
  while (sel.options.length > 1) sel.remove(1);
  forms.forEach(f => {
    const opt = document.createElement('option');
    opt.value = f; opt.textContent = f;
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
// Kept intentionally short: every species falls under exactly one
// of these three buckets. See ANIMAL_GROUP_TO_THEME in psbp_common.py
// for the animal_group → theme mapping.
const WILD_THEMES = [
  { key: 'bird',      label: '🐦 Birds' },
  { key: 'butterfly', label: '🦋 Butterflies' },
  { key: 'other',     label: '🐾 Other' },
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
  /* data-kind, 2026-09-07: this card carries `plant-card` because it reuses the
     plant card's styling, and that lie cost two bugs — remember() filed animals
     under kind 'plant', and the drawer had to work out the kind from which grid
     the card happened to sit in. The card now says what it is. */
  return `<a class="card plant-card" data-kind="wild" href="${w.page}" style="text-decoration:none;display:flex;flex-direction:column;height:100%">
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
  // Added 2026-09-05. The tab could say a plant was flowering or an animal was
  // seen, but not that something NEW went in the ground — which is the park's
  // own news, and the one kind of entry nobody else could post.
  planted: 'Newly planted',
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
  const isPlanted  = kind === 'planted';
  // Three states, three looks: a sighting is deep green, a bloom is gold, and
  // something newly planted gets the mid green so it reads as park news rather
  // than as another flower.
  const glyph  = isSighting ? '🦜' : isPlanted ? '🌱' : '🌿';
  const pillBg = isSighting ? 'var(--green-deep)' : isPlanted ? 'var(--green)' : 'var(--gold)';
  const pillFg = (isSighting || isPlanted) ? '#fff' : 'var(--green-deep)';
  const wash   = isSighting ? 'rn-back--fauna' : 'rn-back--flora';

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

  // FRONT — same content as before, but no longer a whole-card <a>; the link
  // moves to the back. Credit plate stays pinned to the bottom via flex.
  const front = `
    <div class="rn-face rn-front">
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
      ${plate}
      <span class="rn-flip-hint" aria-hidden="true">↺ more</span>
    </div>`;

  // BACK — bloom wash (gold-leaf for blooms, green-feather for sightings),
  // the stamped quick_hits, and the SAME species-page link that used to wrap
  // the whole card. Link gated on `page` existing (a 'spotted' species has none).
  const hits = Array.isArray(e.quick_hits) ? e.quick_hits.filter(Boolean) : [];
  const hitsHtml = hits.length
    ? hits.map(h => `<p class="rn-hit">${_rnEsc(h)}</p>`).join('')
    : `<p class="rn-hit rn-hit--soft">${_rnEsc(e.note || 'Come find it in the park.')}</p>`;
  const link = page
    ? `<a class="rn-back-link" href="${_rnEsc(page)}" onclick="event.stopPropagation()">See the full ${isSighting ? 'wildlife' : 'plant'} page <span aria-hidden="true">→</span></a>`
    : '';
  const wmFlora = '<svg class="rn-wm" viewBox="0 0 100 100" aria-hidden="true"><path d="M18 84 C18 42 50 18 88 18 C88 58 56 84 18 84 Z M30 72 C46 56 62 46 80 34" fill="none" stroke="currentColor" stroke-width="4" stroke-linejoin="round"/></svg>';
  const wmFauna = '<svg class="rn-wm" viewBox="0 0 100 100" aria-hidden="true"><path d="M78 20 L30 84 M78 20 C60 28 48 44 42 64 M78 20 C66 26 56 36 50 50 M78 20 C70 24 62 30 58 40" fill="none" stroke="currentColor" stroke-width="4" stroke-linecap="round" stroke-linejoin="round"/></svg>';
  const back = `
    <div class="rn-face rn-back ${wash}">
      ${isSighting ? wmFauna : wmFlora}
      <span class="rn-back-eyebrow">Did you know</span>
      <h4 class="rn-back-name">${_rnEsc(e.common_name)}</h4>
      <span class="rn-back-rule"></span>
      <div class="rn-back-hits">${hitsHtml}</div>
      ${link}
      <span class="rn-back-foot" aria-hidden="true">↺ flip back</span>
    </div>`;

  return `<div class="card plant-card rn-flip" tabindex="0" role="button" aria-label="${_rnEsc(e.common_name)} — tap for more">
      <div class="rn-flip-inner">${front}${back}</div>
    </div>`;
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
  // `limit` caps the POOL, not what is on screen. With rotation on, `perPage`
  // decides how many show at once and the rest take their turn. (2026-08-27)
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

  /* ── ROTATION ────────────────────────────────────────────────────────────
     Added 2026-08-27. Randy: "I'm thinking I'll keep up to 10-20 most times.
     I think a row of 3 that swaps out every X seconds would be good."

     Same mechanism the home-page mosaic uses: two stacked pages, one visible,
     crossfaded on a timer. Not a carousel — nothing slides, and there are no
     controls to miss. A row of three simply becomes a different three.

     It only engages when there is something to rotate TO. With three or fewer
     rows the strip renders exactly as before and no timer is started, so the
     common case pays nothing. */
  const PER = opts.perPage || 3;
  const EVERY = (opts.rotateSeconds || 9) * 1000;
  if (el._rnTimer) { clearInterval(el._rnTimer); el._rnTimer = null; }

  if (rows.length > PER) {
    const cards = Array.prototype.slice.call(el.children);
    const page = i => {
      let h = '';
      for (let k = 0; k < PER; k++) h += cards[(i + k) % cards.length].outerHTML;
      return h;
    };
    // Both classes: the grid shape lives on .rn-strip and the stacking on
    // .rn-cycling, and the CSS keys on the pair. Adding rn-strip here too means
    // rotation still lays out correctly if a page ever forgets it in the markup.
    el.classList.add('rn-strip', 'rn-cycling');
    el.innerHTML = '<div class="rn-page on"></div><div class="rn-page"></div>';
    const pages = el.querySelectorAll('.rn-page');
    pages[0].innerHTML = page(0);

    let active = 0, start = 0, hovering = false;
    el.addEventListener('mouseenter', () => { hovering = true; });
    el.addEventListener('mouseleave', () => { hovering = false; });
    const anyFlipped = () => !!el.querySelector('.rn-flip.is-flipped');

    // Wait for a calm moment: never swap the card somebody is reading or has
    // just turned over.
    el._rnTimer = setInterval(() => {
      if (hovering || anyFlipped() || document.hidden) return;
      start = (start + PER) % cards.length;
      const incoming = 1 - active;
      pages[incoming].innerHTML = page(start);
      pages[incoming].classList.add('on');
      pages[active].classList.remove('on');
      active = incoming;
    }, EVERY);
  }

  // Flip on click / tap / keyboard (delegated). The back-side link calls
  // stopPropagation, so following it never also toggles the card.
  if (!el._rnFlipWired) {
    const toggle = ev => {
      const card = ev.target.closest('.rn-flip');
      if (!card) return;
      if (ev.type === 'keydown') {
        if (ev.key !== 'Enter' && ev.key !== ' ') return;
        ev.preventDefault();
      }
      card.classList.toggle('is-flipped');
    };
    el.addEventListener('click', toggle);
    el.addEventListener('keydown', toggle);
    el._rnFlipWired = true;
  }
}

/* ── ANALYTICS · GoatCounter ───────────────────────────────────────────────
   Added 2026-08-21, ahead of the QR sign beta — a baseline before the signs
   go up is what makes the after-numbers mean anything.

   Injected from here rather than pasted into each page's <head>, because
   site.js is already loaded by index.html, nature.html and all 320 generated
   species pages (plant_publisher.py line ~703). One edit, whole site, no
   republish. count.js reads the data-goatcounter attribute off its own
   <script> element, so building it this way is equivalent to the snippet
   GoatCounter hands you.

   Deliberately NOT on screen.html — the office TV doesn't load site.js, and a
   display that reloads itself daily would register as a phantom visitor.

   To exclude your own browser: visit any page once with ?skipgc=1, or use the
   setting in GoatCounter. Worth doing on the laptop and the office machine
   before the sign traffic starts arriving.

   Cookieless, no consent banner needed, ~3.5 KB.
   ─────────────────────────────────────────────────────────────────────── */
(function () {
  try {
    if (/[?&]skipgc=1\b/.test(location.search)) {
      try { localStorage.setItem('skipgc', '1'); } catch (e) {}
    }
    if (localStorage.getItem('skipgc') === '1') return;
  } catch (e) { /* private mode — carry on and count normally */ }

  var gc = document.createElement('script');
  gc.async = true;
  gc.setAttribute('data-goatcounter',
                  'https://palmasolabotanicalpark.goatcounter.com/count');
  gc.src = 'https://gc.zgo.at/count.js';
  (document.head || document.documentElement).appendChild(gc);
})();


/* ═══════════════════════════════════════════════════════════════════════════
   SPECIES SEQUENCE NAVIGATION  (added 2026-08-25 — High #4)

   A visitor who arrived from nature.html is mid-list and wants to keep going.
   A QR scanner has no list at all. One mechanism, one conditional.

   nature.html writes the FILTERED id list to sessionStorage when a card is
   clicked; a species page reads it on load. No stored list means QR scan,
   direct link or shared URL — and then no buttons render at all. That answers
   the entry-point question without sniffing referrers.

   Why this lives in site.js: every one of the 321 species pages already loads
   it, so this reaches all of them with NO regeneration.

   DESIGN NOTES
   · Buttons carry the next species' NAME, not a bare arrow. On a full page an
     arrow is ambiguous — is it the next photo, the next section, the next
     plant? A name is not.
   · Sequence context ("3 of 47 · Native") says WHY that species is next. A
     bare "3 of 47" reads as an arbitrary catalog position.
   · "Back to the list" restores scroll AND filters, which is the other half of
     not losing your place. It routes through #restore so a fresh visit to
     nature.html never silently re-applies someone's old filters.
   · Names are stored alongside the ids so a species page never has to fetch
     plants.json just to label two buttons.

   Everything is wrapped defensively: this file is shared by every page on the
   site, so a failure here must never take a page down with it.
   ═══════════════════════════════════════════════════════════════════════════ */
(function () {
  'use strict';

  var KEY = 'psbp_seq_v1';

  function save(o) { try { sessionStorage.setItem(KEY, JSON.stringify(o)); } catch (e) {} }
  function read()  { try { return JSON.parse(sessionStorage.getItem(KEY) || 'null'); } catch (e) { return null; } }

  /* A species page names itself in its own URL: PSBP-00215-Queensland-....html
     No data attribute needed, so no regeneration. */
  function idFromUrl() {
    var m = /(PSBP-\d{5})/.exec(location.pathname);
    return m ? m[1] : null;
  }

  /* ── WRITE SIDE — runs on nature.html ─────────────────────────────────── */

  function activeLabel(kind) {
    if (kind !== 'plant') return '';
    var bits = [];
    try {
      if (typeof _activeForm !== 'undefined' && _activeForm) bits.push(_activeForm);
      if (typeof _activeFilters !== 'undefined' && _activeFilters.size) {
        _activeFilters.forEach(function (f) {
          bits.push(f === 'rare_fruit' ? 'Rare fruit' : f.charAt(0).toUpperCase() + f.slice(1));
        });
      }
      var q = document.getElementById('plantSearch');
      if (q && q.value.trim()) bits.push('“' + q.value.trim() + '”');
    } catch (e) {}
    return bits.join(' · ');
  }

  function captureFilters() {
    /* wflags / wpage added 2026-09-07. Only the PLANT rail was captured, so
       returning from a wildlife species page reinstated the search text and
       nothing else — no theme filters, page one. Harmless while wildlife cards
       navigated straight out; not once the drawer and the sequence nav made
       "back to the list" a real round trip. */
    var f = { form: '', flags: [], q: '', wq: '', scroll: 0, page: 0,
              wflags: [], wpage: 0 };
    try {
      if (typeof _activeForm !== 'undefined') f.form = _activeForm || '';
      if (typeof _activeFilters !== 'undefined') _activeFilters.forEach(function (x) { f.flags.push(x); });
      if (typeof _wildFilters !== 'undefined') _wildFilters.forEach(function (x) { f.wflags.push(x); });
      var q = document.getElementById('plantSearch'); if (q) f.q = q.value || '';
      var w = document.getElementById('wildSearch');  if (w) f.wq = w.value || '';
      if (typeof _plantPage !== 'undefined') f.page = _plantPage;
      if (typeof _wildPage !== 'undefined') f.wpage = _wildPage;
      f.scroll = window.scrollY || 0;
    } catch (e) {}
    return f;
  }

  function remember(list, clickedId, kind) {
    if (!list || !list.length) return;
    var ids = [], names = [], pages = [];
    for (var i = 0; i < list.length; i++) {
      ids.push(list[i].id);
      names.push(list[i].common || list[i].name || list[i].id);
      /* Store the card's own `page` URL. Rebuilding the slug here would be a
         second implementation of a rule that already exists in plantCard(),
         and the two would drift. */
      pages.push(list[i].page || '');
    }
    save({
      kind: kind, ids: ids, names: names, pages: pages,
      label: activeLabel(kind),
      from: location.pathname.split('/').pop() || 'nature.html',
      hash: kind === 'plant' ? '#plants' : '#wildlife',
      filters: captureFilters(),
      current: clickedId || null
    });
  }

  /* Delegated, so it survives every re-render of the grid. Fires alongside the
     drawer's own handler on desktop — storing costs nothing there, and it is
     what makes the drawer's "Full plant page" button land with a sequence. */
  /* Two fixes, 2026-09-07.

     1. KIND BY GRID, NOT BY CLASS. wildCard() emits `class="card plant-card"`
        — the same class plantCard() uses — so classList.contains('plant-card')
        was true for ANIMALS. Every wildlife card stored the PLANT list under
        kind 'plant', which is why the sequence nav walked the wrong species and
        "back to the list" restored the wrong rail. The grid the card sits in is
        the only reliable signal.

     2. RE-CAPTURE ON THE WAY OUT OF THE DRAWER. This fires on card clicks only,
        so arrowing through thirty species inside the panel never updated the
        stored state — leaving the page and filters as they were at the FIRST
        card. The drawer's footer button (#dwPage) is the exit, and while the
        panel is open the URL says which record and which kind, so that is what
        it reads. */
  document.addEventListener('click', function (e) {
    var card = e.target.closest && e.target.closest('a.plant-card, a.obs-card, #dwPage');
    if (!card) return;
    try {
      var isWild, list, id;

      /* The drawer's exit. There used to be two — this footer button and a
         "Read the full story" link inside the teaser — going to the same place;
         matching only one meant arrowing to another species and leaving by the
         other recorded the species you STARTED on. The duplicate link is gone,
         so there is one way out and one thing to keep in step. */
      if (card.id === 'dwPage') {
        var h = /^#(plant|wildlife)\/(PSBP-\d{5})/.exec(location.hash || '');
        if (!h) return;                     // panel not open — nothing to record
        isWild = h[1] === 'wildlife';
        id     = h[2];
      } else {
        isWild = card.dataset.kind === 'wild' || card.classList.contains('obs-card');
        var m  = /(PSBP-\d{5})/.exec(card.getAttribute('href') || '');
        id     = m ? m[1] : null;
      }

      list = isWild ? (typeof _filteredWild   !== 'undefined' ? _filteredWild   : null)
                    : (typeof _filteredPlants !== 'undefined' ? _filteredPlants : null);
      remember(list, id, isWild ? 'wild' : 'plant');
    } catch (err) {}
  }, true);

  /* Put the visitor back in front of the record they were reading. `current`
     has been stored since this shipped and was never used, so a return landed
     on the bare grid. On a phone the drawer does not exist and this no-ops,
     which is correct — the species page WAS the quick view there. */
  function reopenPanel(s) {
    if (!s.current) return;
    try {
      if (window.PSBPDrawer && PSBPDrawer.openById) PSBPDrawer.openById(s.kind, s.current);
    } catch (e) {}
  }

  /* ── RESTORE SIDE — nature.html?#restore ──────────────────────────────── */

  function restore() {
    var s = read(); if (!s || !s.filters) return;
    var f = s.filters;
    try {
      if (typeof _activeFilters !== 'undefined') {
        _activeFilters.clear();
        f.flags.forEach(function (x) { _activeFilters.add(x); });
        document.querySelectorAll('#panel-plants .filter-btn').forEach(function (b) {
          if (b.dataset.filter) b.classList.toggle('on', _activeFilters.has(b.dataset.filter));
        });
      }
      if (typeof setPlantForm === 'function') setPlantForm(f.form || '');
      var q = document.getElementById('plantSearch'); if (q) q.value = f.q || '';
      var w = document.getElementById('wildSearch');  if (w) w.value = f.wq || '';
      if (typeof filterPlants === 'function') filterPlants();

      /* ── WILDLIFE HALF (2026-09-07) ────────────────────────────────────
         Two things make this different from the plant half:

         1. THE TAB. `s.hash` ('#plants' / '#wildlife') has been stored since
            this shipped and was never read, so returning from an animal page
            always landed on the plants tab — with the animal's filters applied
            to a grid nobody was looking at.

         2. LAZY LOADING. wildlife.json is fetched by showTab('wildlife') the
            first time that tab opens, so on a cold #restore WILDLIFE is empty
            and filtering it does nothing. The 120ms delay before restore()
            covers the feeds, not a fetch that has not started yet. So: switch
            the tab, then wait for the data, then apply. */
      if (s.kind === 'wild') {
        if (typeof showTab === 'function') showTab('wildlife');
        /* showTab() kicks off (or has already finished) the wildlife fetch and
           parks the promise on window._wildlifeReady. Awaiting it is exact —
           the old version polled every 75ms and guessed. */
        Promise.resolve(window._wildlifeReady).then(function () {
        if (typeof _wildFilters !== 'undefined' && f.wflags) {
          _wildFilters.clear();
          f.wflags.forEach(function (x) { _wildFilters.add(x); });
          document.querySelectorAll('[data-wfilter]').forEach(function (b) {
            b.classList.toggle('on', _wildFilters.has(b.dataset.wfilter));
          });
        }
        if (typeof filterWildlife === 'function') filterWildlife();
        if (typeof _wildPage !== 'undefined' && f.wpage) {
          _wildPage = f.wpage;
          if (typeof renderWildPage === 'function') renderWildPage();
        }
        reopenPanel(s);
        setTimeout(function () { window.scrollTo(0, f.scroll || 0); }, 60);
        });
        return;                       // plant half below would fight the tab
      }

      /* renderPlantPage() takes NO argument — it reads the _plantPage global.
         Passing f.page therefore did nothing, and filterPlants() above has just
         reset _plantPage to 0, so returning from a species page always landed
         on page one however deep you had browsed. Set the global, then render.
         Found 2026-09-07 while wiring the wildlife drawer. */
      if (typeof _plantPage !== 'undefined' && f.page) _plantPage = f.page;
      if (typeof renderPlantPage === 'function' && f.page) renderPlantPage();
      reopenPanel(s);
      // after the grid has re-rendered
      setTimeout(function () { window.scrollTo(0, f.scroll || 0); }, 60);
    } catch (e) {}
  }

  /* ── READ SIDE — runs on a species page ──────────────────────────────── */

  function buildNav() {
    var id = idFromUrl(); if (!id) return;
    var s = read(); if (!s || !s.ids || s.ids.length < 2) return;
    var i = s.ids.indexOf(id);
    if (i < 0) return;                       // arrived some other way — stay quiet

    var prev = i > 0 ? i - 1 : -1;
    var next = i < s.ids.length - 1 ? i + 1 : -1;
    if (prev >= 0 && !(s.pages && s.pages[prev])) prev = -1;
    if (next >= 0 && !(s.pages && s.pages[next])) next = -1;

    /* `pages` holds the card's own href — "plants/PSBP-00001-Tree-Crinum.html"
       — and a species page sits one directory down, hence the ../ */
    function href(j) {
      var u = (s.pages && s.pages[j]) || '';
      return u ? '../' + u : '#';
    }

    var ctx = (i + 1) + ' of ' + s.ids.length + (s.label ? ' · ' + s.label : '');
    var html =
      '<nav class="seq-nav" aria-label="Species sequence">' +
        (prev >= 0
          ? '<a class="seq-btn seq-prev" href="' + href(prev) + '">' +
              '<span class="seq-dir">&larr; Previous</span>' +
              '<span class="seq-name">' + String(s.names[prev]) + '</span></a>'
          : '<span class="seq-btn seq-empty"></span>') +
        '<a class="seq-back" href="../' + s.from + '#restore">' +
          '<span class="seq-ctx">' + ctx + '</span>' +
          '<span class="seq-back-label">Back to the list</span></a>' +
        (next >= 0
          ? '<a class="seq-btn seq-next" href="' + href(next) + '">' +
              '<span class="seq-dir">Next &rarr;</span>' +
              '<span class="seq-name">' + String(s.names[next]) + '</span></a>'
          : '<span class="seq-btn seq-empty"></span>') +
      '</nav>';

    /* The floating "All Plants" button exists because a species write-up runs
       long and a visitor mid-scroll needs a way out. Fair — but it is a plain
       link to nature.html#plants, so it silently discards the filters and the
       scroll position, while the nav below returns you exactly where you were.

       Two controls that look identical and behave differently is worse than
       either alone, so point it at the same #restore route. Rewritten at
       runtime rather than in the template: it is baked into all 321 pages and
       this way costs no regeneration. Left untouched when there is no stored
       list — on a QR arrival there is nothing to restore, and the plain link
       is exactly right. */
    var floater = document.querySelector('.plant-float-back, .wild-float-back');
    if (floater) floater.setAttribute('href', '../' + s.from + '#restore');

    var anchor = document.querySelector('.all-plants-link, .all-wild-link');
    if (anchor && anchor.parentNode) {
      anchor.insertAdjacentHTML('beforebegin', html);
      anchor.style.display = 'none';          // the nav supersedes it
    } else {
      var main = document.querySelector('main') || document.body;
      main.insertAdjacentHTML('beforeend', html);
    }

    var css = document.createElement('style');
    css.textContent =
      '.seq-nav{display:grid;grid-template-columns:1fr auto 1fr;gap:.75rem;align-items:stretch;' +
        'max-width:1100px;margin:2.5rem auto 1.5rem;padding:0 1rem}' +
      '.seq-btn{display:flex;flex-direction:column;justify-content:center;gap:.15rem;' +
        'padding:.7rem .9rem;border:1px solid var(--border,#dde4e0);border-radius:10px;' +
        'background:var(--cream,#faf8f3);text-decoration:none;min-height:3.4rem}' +
      '.seq-btn:hover{border-color:var(--green-mid,#2d6a35)}' +
      '.seq-empty{border:0;background:none}' +
      '.seq-next{text-align:right}' +
      '.seq-dir{font-size:.7rem;font-weight:700;letter-spacing:.07em;text-transform:uppercase;' +
        'color:var(--green-mid,#2d6a35)}' +
      '.seq-name{font-size:.95rem;font-weight:700;color:var(--text,#1e1e1e);line-height:1.25}' +
      '.seq-back{display:flex;flex-direction:column;align-items:center;justify-content:center;' +
        'gap:.15rem;padding:.7rem 1.1rem;border-radius:10px;text-decoration:none;' +
        'background:var(--green-deep,#1a3a1f);color:#fff;white-space:nowrap}' +
      '.seq-ctx{font-size:.7rem;opacity:.85;letter-spacing:.03em}' +
      '.seq-back-label{font-size:.9rem;font-weight:700}' +
      '@media(max-width:640px){.seq-nav{grid-template-columns:1fr 1fr;gap:.5rem}' +
        '.seq-back{grid-column:1/-1;order:3}.seq-empty{display:none}}';
    document.head.appendChild(css);
  }

  /* ── boot ─────────────────────────────────────────────────────────────── */

  function go() {
    try {
      if (idFromUrl()) { buildNav(); return; }
      if (location.hash === '#restore') {
        history.replaceState(null, '', location.pathname + location.search);
        setTimeout(restore, 120);            // let the feeds land first
      }
    } catch (e) { if (window.console) console.error('seq-nav:', e); }
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', go);
  else go();
})();
