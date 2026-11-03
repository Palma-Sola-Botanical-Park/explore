/* landing-guide.js: the guide half of a collection landing page (rarefruit.html, butterfly.html).
   The finder half (the map and every plant) is rarefruit-finder.html / butterfly-finder.html.

   In the page:
     <div class="pg-cards" data-picks="PSBP-00132 PSBP-00126"></div>   photo cards, by PSBP ID, in order;
                                                                      90000 and up are read from wildlife.json
     <span data-count="fruit"></span>                                  filled with counts.fruit(plants, wildlife)
     <svg class="pg-map" viewBox="x y w h">…<g class="pg-dots"></g></svg>   the small map; a gold dot per pin

   LandingGuide({ pins: { 'PSBP-00132': [[x, y, 'Rare Fruit'], …] }, counts: { fruit: function (plants, wildlife) { … } } }) */
(function () {
  var NS = 'http://www.w3.org/2000/svg';

  function esc(s) { return String(s == null ? '' : s).replace(/[&<>"]/g, function (c) { return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]; }); }
  function card(p) {
    var fit = /ND/.test(p.credit_license || '') ? ';background-size:contain' : '';
    var a = document.createElement('a'); a.className = 'pg-card'; a.href = p.page;
    a.innerHTML = '<span class="ph" style="background-image:url(\'' + esc(p.photo_web || p.photo_thumb) +
      '\');background-position:' + esc(p.focus || '50% 50%') + fit + '"></span><span class="tx">' +
      (p.tier === 'Feature' ? '<span class="tag">Don\'t miss</span>' : '') +
      '<b>' + esc(p.common) + '</b>' + (p.sci && p.sci !== p.common ? '<i class="sci">' + esc(p.sci) + '</i>' : '') +
      (p.origin_short ? '<span class="from">' + esc(p.origin_short) + '</span>' : '') +
      (p.teaser ? '<span class="story">' + esc(p.teaser) + '</span>' : '') +
      '<span class="go">Read about it →</span>' +
      (p.credit_name && window.PSBPPhotos ? PSBPPhotos.creditPlate({ by: p.credit_name, license: p.credit_license }) : '') +
      '</span>';
    return a;
  }

  window.LandingGuide = function (o) {
    o = o || {};
    // the small map: every pin as a gold dot, sized to the crop
    [].forEach.call(document.querySelectorAll('svg.pg-map'), function (svg) {
      var vb = svg.getAttribute('viewBox').split(/\s+/).map(Number), g = svg.querySelector('.pg-dots');
      var r = Math.max(3, 16 * Math.pow(vb[2] / 1408, 0.7));
      Object.keys(o.pins || {}).forEach(function (id) {
        o.pins[id].forEach(function (pin) {
          var c = document.createElementNS(NS, 'circle');
          c.setAttribute('cx', pin[0]); c.setAttribute('cy', pin[1]); c.setAttribute('r', r.toFixed(1));
          c.setAttribute('fill', '#c5922a'); c.setAttribute('stroke', '#3a2a00'); c.setAttribute('stroke-width', (r / 4).toFixed(1));
          g.appendChild(c);
        });
      });
    });

    Promise.all([
      fetch('plants.json').then(function (r) { return r.json(); }),
      fetch('wildlife.json').then(function (r) { return r.json(); })
    ]).then(function (both) {
      var plants = both[0], wildlife = both[1], byId = {};
      plants.concat(wildlife).forEach(function (p) { byId[p.id] = p; });
      [].forEach.call(document.querySelectorAll('[data-picks]'), function (el) {
        el.getAttribute('data-picks').split(/\s+/).forEach(function (id) {
          if (byId[id]) el.appendChild(card(byId[id]));
        });
      });
      [].forEach.call(document.querySelectorAll('[data-count]'), function (el) {
        var f = (o.counts || {})[el.getAttribute('data-count')];
        if (f) el.textContent = f(plants, wildlife);
      });
    });
  };
})();
