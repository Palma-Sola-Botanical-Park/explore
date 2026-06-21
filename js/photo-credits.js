/* ============================================================================
   PSBP · photo-credits.js
   ----------------------------------------------------------------------------
   One source of truth for "hero" imagery + photographer attribution on the
   home page. Reads data/sources/photo_credits.json, builds a shuffled pool of
   publishable hero photos (plants AND wildlife), and renders:
     • the top-right hero slideshow            -> PSBPPhotos.mountHeroSlideshow()
     • the "Ten acres" 2x2 mosaic              -> PSBPPhotos.mountHeroGrid()
   It also exposes the shared attribution builder used by the live "What's been
   seen lately" mosaic in index.html so all three blocks credit photographers
   the same way:
     • PSBPPhotos.attribution({...})  -> overlay HTML
     • PSBPPhotos.ccBadge(license)    -> the Creative Commons pill

   Standalone on purpose — does NOT modify site.js. Include AFTER site.js.

   Image strategy: try the local file in photos/ first; if it isn't there,
   fall back to the photo's remote iNaturalist URL. Nothing ever renders broken,
   even if a local filename doesn't match.

   NOTE ON DATES: photo_credits.json has no observed-on field yet, so hero/grid
   attribution shows photographer + license now and lights up a date the moment
   the sync pipeline adds `observed_on` (and optional `time_observed_at`) per
   photo. The live mosaic already gets dates straight from the iNat API.
   ========================================================================== */
(function () {
  'use strict';

  // ---- config -------------------------------------------------------------
  // If photo_credits.json is served from a different path, change this one line.
  var SRC        = 'data/sources/photo_credits.json';
  var LOCAL_DIR  = 'photos/';   // where curated local hero JPGs live
  var HERO_COUNT = 10;          // slides in the top-right slideshow
  var GRID_COUNT = 4;           // tiles in the "Ten acres" mosaic

  var _pool = null;             // shuffled, de-duplicated hero pool (Promise-cached)

  // ---- small utilities ----------------------------------------------------
  function shuffle(a) {
    a = a.slice();
    for (var i = a.length - 1; i > 0; i--) {
      var j = Math.floor(Math.random() * (i + 1));
      var t = a[i]; a[i] = a[j]; a[j] = t;
    }
    return a;
  }

  function esc(s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  // ---- license handling ---------------------------------------------------
  // Normalize the messy values (cc-by-nc / CC-BY-NC / CC-BY-ND / nan / "") to
  // a clean upper form, then split into rights tokens for the badge.
  function normLicense(raw) {
    var s = String(raw == null ? '' : raw).trim().toUpperCase().replace(/_/g, '-');
    if (!s || s === 'NAN' || s === 'NONE') return null;
    if (s.indexOf('CC') !== 0) s = 'CC-' + s;       // tolerate "BY-NC"
    return s.replace(/^CC-?/, 'CC-').replace(/-+/g, '-'); // -> CC-BY-NC
  }

  function licenseTokens(lic) {
    if (!lic) return [];
    return lic.replace(/^CC-?/, '').split('-').filter(Boolean); // ['BY','NC']
  }

  // The Creative Commons pill — mirrors the badge iNaturalist shows.
  function ccBadge(rawLicense) {
    var lic = normLicense(rawLicense);
    var toks = licenseTokens(lic);
    if (!toks.length) {
      return '<span class="cc-badge cc-badge--unknown" title="License on file at iNaturalist">'
           + '<span class="cc-mark">cc</span></span>';
    }
    var human = toks.join('-');
    return '<span class="cc-badge" title="Creative Commons ' + esc(human)
         + ' \u2014 some rights reserved, free to share with credit">'
         + '<span class="cc-mark">cc</span>'
         + toks.map(function (t) { return '<span class="cc-term">' + esc(t) + '</span>'; }).join('')
         + '</span>';
  }

  // ---- date handling (graceful when absent) -------------------------------
  function fmtDate(dateStr, timeStr) {
    if (!dateStr) return '';
    var src = timeStr || dateStr;
    var m = /^(\d{4})-(\d{2})-(\d{2})(?:[T ](\d{2}):(\d{2}))?/.exec(src);
    var d;
    if (m) {
      // bare date -> build in LOCAL time so it never slips a day in a
      // negative timezone; date+time -> parse whole string (honors offset)
      d = (m[4] != null) ? new Date(src.replace(' ', 'T'))
                         : new Date(+m[1], +m[2] - 1, +m[3]);
    } else {
      d = new Date(src);
    }
    if (isNaN(d)) return '';
    var out = d.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' });
    if (timeStr) {
      out += ' \u00b7 ' + d.toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit' });
    }
    return out;
  }

  // ---- the shared attribution overlay -------------------------------------
  // Used by all three blocks so credit looks identical everywhere.
  //   species   : common name (optional)
  //   scientific: italicized latin (optional)
  //   by        : photographer login / name  (required)
  //   license   : raw license string
  //   date,time : optional ISO-ish strings
  function attribution(opts) {
    opts = opts || {};
    var rows = [];

    if (opts.species) {
      rows.push('<span class="attr-species">' + esc(opts.species)
        + (opts.scientific ? ' \u00b7 <em>' + esc(opts.scientific) + '</em>' : '')
        + '</span>');
    }

    // photographer + date join with dots; the CC badge and source sit apart
    // (spaced by flex-gap) so no separator dot is ever orphaned on a line wrap.
    var textBits = ['<span class="attr-by">\uD83D\uDCF7 ' + esc(opts.by || 'community member') + '</span>'];
    var dateStr = fmtDate(opts.date, opts.time);
    if (dateStr) textBits.push('<span class="attr-date"><span class="attr-dot">\u00b7</span> ' + esc(dateStr) + '</span>');

    var parts = [
      '<span class="attr-credit">' + textBits.join('') + '</span>',
      ccBadge(opts.license),
      '<span class="attr-src">via iNaturalist</span>'
    ];
    rows.push('<span class="attr-line">' + parts.join('') + '</span>');
    return '<div class="photo-attr">' + rows.join('') + '</div>';
  }

  // Split attribution, for the "What's been seen lately" mosaic:
  // species name rides INSIDE the photo, the photographer gets a byline OUTSIDE.
  function speciesTag(o) {
    o = o || {};
    if (!o.species && !o.scientific) return '';
    return '<div class="species-tag"><span class="attr-species">'
         + esc(o.species || '')
         + (o.scientific ? ' \u00b7 <em>' + esc(o.scientific) + '</em>' : '')
         + '</span></div>';
  }

  function creditPlate(o) {
    o = o || {};
    var dateStr = fmtDate(o.date);                 // date only, no time
    var label = o.dateLabel ? esc(o.dateLabel) + ' ' : '';
    return '<div class="credit-plate">'
         +   '<div class="credit-byline">'
         +     '<span class="credit-eyebrow">Photograph by</span>'
         +     '<span class="credit-name">' + esc(o.by || 'community member') + '</span>'
         +     (dateStr ? '<span class="credit-date">' + label + esc(dateStr) + '</span>' : '')
         +   '</div>'
         +   '<div class="credit-license">' + ccBadge(o.license)
         +     '<span class="credit-src">via iNaturalist</span></div>'
         + '</div>';
  }

  // ---- data load + hero pool ---------------------------------------------
  function usableHero(p) {
    return p && p.hero === true && p.publish_ok === true && p.photo_url
        && String(p.status || '').indexOf('OK') === 0;
  }

  function loadPool() {
    if (_pool) return _pool;
    _pool = fetch(SRC)
      .then(function (r) {
        if (!r.ok) throw new Error('photo_credits.json ' + r.status);
        return r.json();
      })
      .then(function (data) {
        var photos = (data && data.photos) || [];
        var heroes = photos.filter(usableHero);
        // interleave so plants and wildlife both surface near the top
        var plants = shuffle(heroes.filter(function (p) { return p.type === 'Plant'; }));
        var fauna  = shuffle(heroes.filter(function (p) { return p.type !== 'Plant'; }));
        var mixed = [], i = 0;
        while (plants[i] || fauna[i]) {
          if (plants[i]) mixed.push(plants[i]);
          if (fauna[i])  mixed.push(fauna[i]);
          i++;
        }
        return mixed;
      })
      .catch(function (err) {
        console.warn('[photo-credits] could not load pool:', err);
        return [];
      });
    return _pool;
  }

  // local path candidates, newest architecture first:
  //   1. photos/<psbp_id>/<filename>   (subfolder collection model, DATA_ARCHITECTURE §3)
  //   2. photos/<filename dashes>       (current flat layout, mid-migration)
  // resolveSrc walks these, then falls back to the remote iNat URL — so it
  // works before AND after the photos/ subfolder migration with no edit.
  function localCandidates(p) {
    var c = [];
    if (p.psbp_id && p.filename) c.push(LOCAL_DIR + p.psbp_id + '/' + p.filename);
    if (p.filename)              c.push(LOCAL_DIR + p.filename.replace(/_/g, '-'));
    return c;
  }

  // resolve to a known-good URL: first local candidate that loads, else remote
  function resolveSrc(p) {
    var cands = localCandidates(p);
    var remote = p.photo_url;
    return new Promise(function (res) {
      var i = 0;
      function tryNext() {
        if (i >= cands.length) return res(remote || cands[0]);
        var url = cands[i++];
        var img = new Image();
        img.onload = function () { res(url); };
        img.onerror = tryNext;
        img.src = url;
      }
      if (!cands.length) return res(remote);
      tryNext();
    });
  }

  // crop anchor — DATA_ARCHITECTURE §3 `focus` field (e.g. "65% 66%"),
  // so the subject stays in frame when a hero is squeezed into a banner/tile.
  function focusOf(p) { return p.focus || '50% 50%'; }

  function attrFor(p, withSpecies) {
    return attribution({
      species:    withSpecies ? p.common_name : null,
      scientific: withSpecies ? p.scientific_name : null,
      by:         p.photographer,
      license:    p.license,
      // reads whichever date field the import pipeline lands on; absent today,
      // appears automatically once observed_on/date is captured at import time.
      date:       p.observed_on || p.date || null,
      time:       p.time_observed_at || null
    });
  }

  // ---- renderers ----------------------------------------------------------
  // Top-right hero slideshow.
  //
  // Two stacked layers that we crossfade between, painting the next photo
  // just-in-time and pre-resolving the one after during each dwell. Only ever
  // ~2 images live in memory no matter how long it runs, so the sequence can
  // walk the ENTIRE hero pool without repeating, then reshuffle and keep going.
  // No realistic cap — memory and bandwidth stay flat. (count is ignored now;
  // kept for call-site compatibility.)
  function mountHeroSlideshow(elId, count) {
    var host = document.getElementById(elId);
    if (!host) return;

    loadPool().then(function (pool) {
      if (!pool.length) return;                  // leave the static fallback in place

      // reshuffling queue across the whole pool — long non-repeating runs,
      // reshuffled each lap, avoiding an immediate repeat at the seam
      var queue = shuffle(pool), qi = 0, last = null;
      function nextPhoto() {
        if (qi >= queue.length) {
          var re = shuffle(pool);
          if (re.length > 1 && re[0] === last) re.push(re.shift());
          queue = re; qi = 0;
        }
        var p = queue[qi++]; last = p; return p;
      }

      function paint(layer, p, src) {
        layer.style.backgroundImage = "url('" + src + "')";
        layer.style.backgroundPosition = focusOf(p);
        layer.innerHTML = attrFor(p, true);
      }

      // resolve the first image BEFORE swapping out the fallback (no blank flash)
      var first = nextPhoto();
      resolveSrc(first).then(function (firstSrc) {
        host.innerHTML = '<div class="hero-slide"></div><div class="hero-slide"></div>';
        var layers = host.querySelectorAll('.hero-slide');
        var active = 0;
        paint(layers[0], first, firstSrc);
        layers[0].classList.add('on');

        // pre-resolve the next so its swap is instant
        var nextP = nextPhoto();
        var nextSrc = resolveSrc(nextP);

        setInterval(function () {
          var incoming = 1 - active;
          var showP = nextP, showSrc = nextSrc;
          showSrc.then(function (src) {
            paint(layers[incoming], showP, src);
            layers[incoming].classList.add('on');
            layers[active].classList.remove('on');
            active = incoming;
            // warm the following one during this dwell
            nextP = nextPhoto();
            nextSrc = resolveSrc(nextP);
          });
        }, 5000);
      });
    });
  }

  // "Ten acres" mosaic: fills tiles from a different slice of the same pool
  // (offset so it never duplicates whatever the slideshow is showing).
  function mountHeroGrid(elId, count, offset) {
    var host = document.getElementById(elId);
    if (!host) return;
    count  = count  || GRID_COUNT;
    offset = offset || HERO_COUNT;

    loadPool().then(function (pool) {
      if (!pool.length) return;
      var picks = pool.slice(offset, offset + count);
      if (picks.length < count) picks = pool.slice(0, count); // tiny pool fallback

      Promise.all(picks.map(resolveSrc)).then(function (srcs) {
        host.innerHTML = picks.map(function (p, i) {
          return '<figure class="hero-grid-tile" style="background-image:url(\'' + esc(srcs[i]) + '\');'
               + 'background-position:' + esc(focusOf(p)) + '">'
               + attrFor(p, true)
               + '</figure>';
        }).join('');
      });
    });
  }

  // ---- public API ---------------------------------------------------------
  window.PSBPPhotos = {
    attribution:        attribution,
    speciesTag:         speciesTag,
    creditPlate:        creditPlate,
    ccBadge:            ccBadge,
    fmtDate:            fmtDate,
    loadPool:           loadPool,
    mountHeroSlideshow: mountHeroSlideshow,
    mountHeroGrid:      mountHeroGrid
  };
})();
