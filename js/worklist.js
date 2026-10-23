/* ════════════════════════════════════════════════════════════════════════════
   worklist.js — "see this week's list" sheets on the Citizen Science cards.

   Any element with data-worklist="observe" or data-worklist="identify" opens
   a <dialog>. Observers get two columns (species that need better or more
   photos / species that need a fresh sighting); identifiers get two columns
   (observations that need a second look / observations that can go deeper).

   Reads data/published/maintenance_todo.json (written by maintenance_todo.py,
   refreshed from Species Manager's To do tab) and plants.json (for each
   species page's address). Paths are relative so the site can move domains.
   The identifier list is links to iNaturalist only: no observer, no photo.
   ════════════════════════════════════════════════════════════════════════════ */
(function(){
  var INAT_PROJECT = 'https://www.inaturalist.org/projects/palma-sola-botanical-park';
  var cache = null, dialogs = {};

  function esc(s){ return String(s == null ? '' : s).replace(/[&<>"']/g, function(c){
    return { '&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;' }[c]; }); }
  function fetchJson(url){
    return fetch(url, { cache:'no-cache' }).then(function(r){ if(!r.ok) throw 0; return r.json(); });
  }
  function load(){
    if(cache) return cache;
    cache = Promise.all([
      fetchJson('data/published/maintenance_todo.json'),
      fetchJson('plants.json').catch(function(){ return []; })
    ]).then(function(r){
      var pages = {};
      (r[1] || []).forEach(function(p){ if(p && p.id && p.page) pages[p.id] = p.page; });
      return { todo: r[0], pages: pages };
    });
    cache.catch(function(){ cache = null; });          /* let a later click try again */
    return cache;
  }
  function monthYear(iso){
    var d = iso ? new Date(iso + 'T12:00') : null;
    return d && !isNaN(d) ? d.toLocaleDateString('en-US', { month:'long', year:'numeric' }) : '';
  }
  function shortDate(iso){
    var d = iso ? new Date(iso + 'T12:00') : null;
    return d && !isNaN(d) ? d.toLocaleDateString('en-US', { month:'short', day:'numeric' }) : '';
  }
  function asOf(todo){
    var g = todo && todo.meta && todo.meta.generated;
    var d = g ? new Date(g) : null;
    return d && !isNaN(d) ? 'List updated ' + d.toLocaleDateString('en-US', { month:'long', day:'numeric' }) : '';
  }
  function nameLink(x, pages){
    var href = pages[x.id];
    return href ? '<a class="wl-name" href="' + esc(href) + '">' + esc(x.name) + '</a>'
                : '<span class="wl-name">' + esc(x.name) + '</span>';
  }
  var GROUP = { Plantae:'Plant', Aves:'Bird', Insecta:'Insect', Arachnida:'Spider', Reptilia:'Reptile',
                Fungi:'Fungus', Mammalia:'Mammal', Amphibia:'Amphibian', Mollusca:'Snail or slug', Unknown:'' };

  /* ── observers ─────────────────────────────────────────────────────────── */
  var featureOnly = false;                            /* the toggle; each page load starts on everything */
  function byKey(k1, k2){
    return function(a, b){
      var x = [a[k1], a[k2]], y = [b[k1], b[k2]];
      for(var i = 0; i < 2; i++){ var u = x[i] == null ? '' : x[i], v = y[i] == null ? '' : y[i]; if(u < v) return -1; if(u > v) return 1; }
      return 0;
    };
  }
  function observeHtml(data){
    var todo = data.todo, pages = data.pages, ph = todo.photos || {}, st = todo.stale || {};
    function pick(lists){
      return (lists.feature || []).concat(featureOnly ? [] : (lists.standard || []));
    }
    var photos = pick(ph).sort(function(a, b){ return a.photos - b.photos || byKey('newest', 'id')(a, b); }).slice(0, 10);
    var onPhotos = {}; photos.forEach(function(x){ onPhotos[x.id] = 1; });
    var sights = pick(st).filter(function(x){ return !onPhotos[x.id]; }).sort(byKey('newest', 'id')).slice(0, 10);
    function col(title, hint, rows, note){
      return '<div class="wl-col"><h3>' + esc(title) + '</h3><p class="wl-sub">' + esc(hint) + '</p>'
        + (rows.length ? '<ol>' + rows.map(function(x){
            return '<li>' + nameLink(x, pages) + '<span class="wl-note">' + esc(note(x)) + '</span></li>'; }).join('') + '</ol>'
          : '<p class="wl-empty">Nothing on this list right now.</p>') + '</div>';
    }
    return '<h2 class="wl-title" id="wl-title">This week’s list</h2>'
      + '<p class="wl-lede">These are the plants we could use you on. Tap a name to see its page, then go find it.</p>'
      + '<div class="wl-filter"><button type="button" class="wl-toggle" aria-pressed="' + featureOnly + '">Feature plants only</button></div>'
      + '<div class="wl-cols">'
      +   col('Better & more photos', 'Artsy or close and sharp. Both go on the page.', photos,
              function(x){ return 'we have ' + x.photos + (x.photos === 1 ? ' photo' : ' photos'); })
      +   col('Fresh sightings', 'Not logged in a while. A current one keeps it on the list.', sights,
              function(x){ return 'last logged ' + monthYear(x.newest); })
      + '</div>'
      + '<div class="wl-foot"><a class="btn btn-green btn-sm" href="' + INAT_PROJECT + '" target="_blank" rel="noopener">Post it on iNaturalist &rarr;</a>'
      + '<a href="get-started.html#observe">How observing works</a><span class="wl-asof">' + esc(asOf(todo)) + '</span></div>';
  }

  /* ── identifiers ───────────────────────────────────────────────────────── */
  function identifyHtml(data){
    var todo = data.todo, items = (todo.needs_id && todo.needs_id.items) || [];
    var second = items.filter(function(i){ return i.rank === 'species'; });
    var deeper = items.filter(function(i){ return i.rank !== 'species'; });
    function row(i, kind){
      var g = GROUP[i.group] || '', when = shortDate(i.observed_on);
      var label = kind === 'second' ? i.what : (i.rank ? i.what : 'Not identified at all yet');
      var meta = kind === 'second' ? [g, when] : [i.rank ? 'at ' + i.rank : '', g, when];
      return '<li><a class="wl-name" href="' + esc(i.url) + '" target="_blank" rel="noopener">' + esc(label) + ' &#8599;</a>'
           + '<span class="wl-note">' + esc(meta.filter(Boolean).join(' · ')) + '</span></li>';
    }
    function col(title, hint, rows, kind){
      var shown = rows.slice(0, 10);
      return '<div class="wl-col"><h3>' + esc(title) + '<small>' + rows.length + ' right now</small></h3><p class="wl-sub">' + esc(hint) + '</p>'
        + (shown.length ? '<ol>' + shown.map(function(i){ return row(i, kind); }).join('') + '</ol>'
          : '<p class="wl-empty">Nothing waiting here right now.</p>')
        + (rows.length > shown.length ? '<p class="wl-more">…and ' + (rows.length - shown.length) + ' more on iNaturalist.</p>' : '') + '</div>';
    }
    return '<h2 class="wl-title" id="wl-title">Needs a look</h2>'
      + '<p class="wl-lede">Recent observations from the park. Tap one to open it on iNaturalist and add your identification.</p>'
      + '<div class="wl-cols wl-scratchpad">'
      +   col('Needs a second look', 'An ID is in, and nobody has confirmed it yet.', second, 'second')
      +   col('Can go deeper', 'Stuck at family, order or higher. If you know the group, narrow it down.', deeper, 'deeper')
      + '</div>'
      + '<div class="wl-foot"><a class="btn btn-green btn-sm" href="' + INAT_PROJECT + '" target="_blank" rel="noopener">The park’s iNaturalist project &rarr;</a>'
      + '<a href="get-started.html#identify">How identifying works</a><span class="wl-asof">' + esc(asOf(todo)) + '</span></div>';
  }

  /* ── the dialog ────────────────────────────────────────────────────────── */
  function dialogFor(kind){
    if(dialogs[kind]) return dialogs[kind];
    var d = document.createElement('dialog');
    d.className = 'wl-dialog'; d.setAttribute('aria-labelledby', 'wl-title-' + kind);
    d.innerHTML = '<div class="wl-paper ' + (kind === 'observe' ? 'notebook' : 'scratch') + '">'
      + '<button class="wl-close" type="button" aria-label="Close the list">&times;</button>'
      + '<div class="wl-body"><p class="wl-empty">Opening the list…</p></div></div>';
    d.addEventListener('click', function(e){ if(e.target === d) d.close(); });      /* a click on the backdrop */
    d.querySelector('.wl-close').addEventListener('click', function(){ d.close(); });
    document.body.appendChild(d);
    return dialogs[kind] = d;
  }
  function open(kind){
    var d = dialogFor(kind), body = d.querySelector('.wl-body');
    if(typeof d.showModal === 'function') d.showModal(); else d.setAttribute('open', '');
    load().then(function(data){
      function render(){
        body.innerHTML = kind === 'observe' ? observeHtml(data) : identifyHtml(data);
        var t = body.querySelector('.wl-title'); if(t) t.id = 'wl-title-' + kind;
        var tg = body.querySelector('.wl-toggle');
        if(tg) tg.addEventListener('click', function(){ featureOnly = !featureOnly; render(); var n = body.querySelector('.wl-toggle'); if(n) n.focus(); });
      }
      render();
    }).catch(function(){
      body.innerHTML = '<p class="wl-empty">The list isn’t loading right now. Try again in a minute.</p>';
    });
  }

  function init(){
    document.querySelectorAll('[data-worklist]').forEach(function(b){
      b.addEventListener('click', function(e){ e.preventDefault(); open(b.getAttribute('data-worklist')); });
    });
  }
  if(document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init); else init();
})();
