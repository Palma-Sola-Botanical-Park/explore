/* landing-map.js: the small map on a collection landing page (rarefruit.html, butterfly.html).
   A crop of images/park-map.svg (the same true-to-scale map palms.html uses), a gold dot for
   each pinned plant, and a card under it showing the plant tapped. ‹ › step through them
   north to south. The page also gets a grid of every plant in the collection.

   LandingMap({
     stage:   the element that holds the map,
     card:    the element the chosen plant is shown in,
     grid:    the element the plant cards go in (optional),
     viewBox: [x, y, w, h] on the 1408 × 1408 park map,
     pins:    { 'PSBP-00132': [[x, y, 'Rare Fruit'], …] }   a snapshot from placements.json,
     pick:    function (plant) → true for the plants this page is about (defaults to the pinned ones),
     noun:    'fruit tree' or 'plant', for the card's wording,
     tag:     function (plant) → short line under the name in the grid (optional)
   }) */
(function () {
  var NS = 'http://www.w3.org/2000/svg';
  var MAP = 'images/park-map.svg?v=2026-11-02e';   // keep in step with palms.html

  function esc(s) { return String(s == null ? '' : s).replace(/[&<>"]/g, function (c) { return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]; }); }
  function plate(p) {
    return p.credit_name && window.PSBPPhotos ? PSBPPhotos.creditPlate({ by: p.credit_name, license: p.credit_license }) : '';
  }
  function fit(p) { return /ND/.test(p.credit_license || '') ? ';background-size:contain' : ''; }
  function sciLine(p) { return p.sci && p.sci !== p.common ? '<i class="sci">' + esc(p.sci) + '</i>' : ''; }

  window.LandingMap = function (o) {
    var vb = o.viewBox, noun = o.noun || 'plant';
    var svg = document.createElementNS(NS, 'svg');
    svg.setAttribute('viewBox', vb.join(' '));
    svg.setAttribute('role', 'img');
    svg.setAttribute('aria-label', 'Map of this part of the park, with each ' + noun + ' marked');
    var base = document.createElementNS(NS, 'g'), dotsG = document.createElementNS(NS, 'g');
    svg.appendChild(base); svg.appendChild(dotsG);
    o.stage.appendChild(svg);
    o.stage.style.aspectRatio = vb[2] + ' / ' + vb[3];

    fetch(MAP).then(function (r) { return r.text(); }).then(function (t) {
      var root = new DOMParser().parseFromString(t, 'image/svg+xml').documentElement;
      [].slice.call(root.childNodes).forEach(function (n) { base.appendChild(document.importNode(n, true)); });
      // place names above the dots, and let taps through to them
      var pm = base.querySelector('g.pm'), names = pm && pm.querySelector('.pm-labels');
      if (names) { var top = pm.cloneNode(false); top.setAttribute('pointer-events', 'none'); top.appendChild(names); svg.appendChild(top); }
    });

    function inView(pin) { return pin[0] >= vb[0] && pin[0] <= vb[0] + vb[2] && pin[1] >= vb[1] && pin[1] <= vb[1] + vb[3]; }
    var k = vb[2] / 1408, R = Math.max(3.5, 15 * Math.pow(k, 0.7));

    fetch('plants.json').then(function (r) { return r.json(); }).then(function (all) {
      var byId = {};
      all.forEach(function (p) { byId[p.id] = p; });
      var plants = o.pick ? all.filter(o.pick)
        : Object.keys(o.pins).map(function (id) { return byId[id]; }).filter(Boolean);

      // dots, for the pins inside this crop
      var dots = [];
      plants.forEach(function (p) {
        (o.pins[p.id] || []).filter(inView).forEach(function (pin) {
          var dot = document.createElementNS(NS, 'circle'), hit = document.createElementNS(NS, 'circle');
          dot.setAttribute('class', 'lm-dot'); hit.setAttribute('class', 'lm-hit');
          [dot, hit].forEach(function (c) { c.setAttribute('cx', pin[0]); c.setAttribute('cy', pin[1]); });
          dot.setAttribute('r', R.toFixed(1)); hit.setAttribute('r', (R * 2.2).toFixed(1));
          var rec = { p: p, y: pin[1], el: dot };
          hit.addEventListener('click', function () { choose(p); });
          dotsG.appendChild(dot); dotsG.appendChild(hit);
          dots.push(rec);
        });
      });
      [].slice.call(dotsG.querySelectorAll('.lm-hit')).forEach(function (h) { dotsG.appendChild(h); });

      // the walk: pinned plants north to south, by their northernmost pin in the crop
      var top = {};
      dots.forEach(function (d) { top[d.p.id] = Math.min(top[d.p.id] == null ? Infinity : top[d.p.id], d.y); });
      var walk = plants.filter(function (p) { return top[p.id] != null; })
        .sort(function (a, b) { return top[a.id] - top[b.id]; });

      var chosen = null;
      function intro() {
        o.card.innerHTML = '<div>Tap a gold dot to see which ' + esc(noun) + ' it is, or step through them.</div>' +
          (walk.length > 1 ? '<div class="lm-step"><button type="button" class="prev" aria-label="Last ' + esc(noun) + '">‹</button>' +
            '<span class="n">' + walk.length + ' on the map</span><button type="button" class="next" aria-label="First ' + esc(noun) + '">›</button></div>' : '');
      }
      function choose(p) {
        chosen = p;
        var firstHit = dotsG.querySelector('.lm-hit');
        dots.forEach(function (d) {
          var on = d.p === p;
          d.el.classList.toggle('sel', on);
          if (on) dotsG.insertBefore(d.el, firstHit);
        });
        var i = walk.indexOf(p);
        var elsewhere = (o.pins[p.id] || []).filter(function (pin) { return !inView(pin); })
          .map(function (pin) { return pin[2]; }).filter(function (a, j, arr) { return a && arr.indexOf(a) === j; });
        var img = p.photo_web || p.photo_thumb;
        o.card.innerHTML =
          '<div class="lm-head"><span class="lm-eyebrow">Meet this ' + esc(noun) + '</span>' +
          (i >= 0 && walk.length > 1 ? '<div class="lm-step"><button type="button" class="prev" aria-label="Previous">‹</button>' +
            '<span class="n">' + (i + 1) + ' of ' + walk.length + '</span><button type="button" class="next" aria-label="Next">›</button></div>' : '') +
          '</div>' +
          (img ? '<div class="lm-media"><span class="lm-photo" role="img" aria-label="' + esc(p.common) + '" style="background-image:url(\'' +
            esc(img) + '\');background-position:' + esc(p.focus || '50% 50%') + fit(p) + '"></span>' + plate(p) + '</div>' : '') +
          '<div class="lm-info"><span class="nm">' + esc(p.common) + '</span>' + sciLine(p) +
          (p.origin_short ? '<span class="where">' + esc(p.origin_short) + (elsewhere.length ? ' · also at ' + esc(elsewhere.join(', ')) : '') + '</span>' : '') +
          (p.teaser ? '<p>' + esc(p.teaser) + '</p>' : '') +
          '<a href="' + esc(p.page) + '">Read about it →</a></div>';
      }
      function step(dir) {
        if (!walk.length) return;
        var i = walk.indexOf(chosen);
        choose(walk[i < 0 ? (dir > 0 ? 0 : walk.length - 1) : (i + dir + walk.length) % walk.length]);
      }
      o.card.addEventListener('click', function (e) {
        var b = e.target.closest('.lm-step button');
        if (b) step(b.classList.contains('next') ? 1 : -1);
      });
      var t0 = null;
      o.card.addEventListener('touchstart', function (e) { var t = e.touches[0]; t0 = { x: t.clientX, y: t.clientY }; }, { passive: true });
      o.card.addEventListener('touchend', function (e) {
        if (!t0) return;
        var t = e.changedTouches[0], dx = t.clientX - t0.x, dy = t.clientY - t0.y; t0 = null;
        if (Math.abs(dx) > 50 && Math.abs(dx) > 1.5 * Math.abs(dy)) step(dx < 0 ? 1 : -1);
      });
      intro();

      // the grid: Feature plants first, then A to Z; each card says whether it is on the map
      if (o.grid) {
        plants.slice().sort(function (a, b) {
          return (b.tier === 'Feature') - (a.tier === 'Feature') || a.common.localeCompare(b.common);
        }).forEach(function (p) {
          var a = document.createElement('a');
          a.className = 'lm-sp'; a.href = p.page;
          var line = o.tag ? o.tag(p) : p.origin_short;
          a.innerHTML = '<span class="ph" style="background-image:url(\'' + esc(p.photo_thumb || p.photo_web) +
            '\');background-position:' + esc(p.focus || '50% 50%') + fit(p) + '"></span><span class="tx">' +
            '<b>' + esc(p.common) + '</b>' + sciLine(p) +
            (line ? '<span class="from">' + esc(line) + '</span>' : '') + '</span>' + plate(p);
          o.grid.appendChild(a);
        });
      }
      if (o.done) o.done(plants);
    });
  };
})();
