/* ══ PARK STORIES — behaviour ═════════════════════════════════════════════
   Shared by every generated story page and the catalog. The page defines
   `window.PHOTOS` (src, alt, by, meta) before loading this file.

   The lightbox is the species one (same markup, same classes in
   species-v2.css, same 60px swipe test as the nature drawer), carried here
   rather than loaded from species-v2.js because that file writes a fixed
   "CC-BY-NC, via iNaturalist" line under every photograph, and a story
   mixes iNaturalist photographs with park-collection ones. Each entry here
   brings its own `meta` line instead. One line in species-v2.js would let
   both pages share it again; that change waits for Randy.
   ═══════════════════════════════════════════════════════════════════════ */
(function () {
  var PHOTOS = window.PHOTOS || [];

  /* ── lightbox ─────────────────────────────────────────────────────────── */
  var lb = document.getElementById('lb');
  if (lb && PHOTOS.length) {
    var lbImg = document.getElementById('lbImg'), lbName = document.getElementById('lbName'),
        lbMeta = document.getElementById('lbMeta'), lbCount = document.getElementById('lbCount'),
        lbStage = document.getElementById('lbStage'), cur = -1;
    function paint() {
      var p = PHOTOS[cur]; if (!p) return;
      lbImg.src = p.src; lbImg.alt = p.alt || '';
      lbName.textContent = p.by || '';
      lbMeta.textContent = p.meta || '';
      lbCount.textContent = (cur + 1) + ' / ' + PHOTOS.length;
    }
    function open(i) { cur = i; paint(); lb.classList.add('on'); lb.setAttribute('aria-hidden', 'false'); document.body.style.overflow = 'hidden'; }
    function close() { lb.classList.remove('on'); lb.setAttribute('aria-hidden', 'true'); document.body.style.overflow = ''; cur = -1; }
    function step(d) { cur = (cur + d + PHOTOS.length) % PHOTOS.length; paint(); }

    document.querySelectorAll('[data-i]').forEach(function (f) {
      var img = f.querySelector('img');
      if (img) img.addEventListener('click', function () { open(+f.dataset.i); });
    });
    document.getElementById('lbPrev').addEventListener('click', function () { step(-1); });
    document.getElementById('lbNext').addEventListener('click', function () { step(1); });
    document.getElementById('lbClose').addEventListener('click', close);
    lbStage.addEventListener('click', function (e) { if (e.target === lbStage) close(); });
    document.addEventListener('keydown', function (e) {
      if (!lb.classList.contains('on')) return;
      if (e.key === 'Escape') close();
      else if (e.key === 'ArrowRight') step(1);
      else if (e.key === 'ArrowLeft') step(-1);
    });
    (function () {
      var x0 = null, y0 = null;
      lbStage.addEventListener('touchstart', function (e) { var t = e.touches[0]; x0 = t.clientX; y0 = t.clientY; }, { passive: true });
      lbStage.addEventListener('touchend', function (e) {
        if (x0 == null) return;
        var t = e.changedTouches[0], dx = t.clientX - x0, dy = t.clientY - y0; x0 = null;
        if (Math.abs(dx) > 60 && Math.abs(dx) > Math.abs(dy) * 1.6) step(dx < 0 ? 1 : -1);
      }, { passive: true });
    })();
  }

  /* ── video: the poster is all that loads until the tap ────────────────── */
  document.querySelectorAll('.st-video[data-yt]').forEach(function (box) {
    box.addEventListener('click', function () {
      var id = box.getAttribute('data-yt');
      if (!id) return;
      var f = document.createElement('iframe');
      f.src = 'https://www.youtube-nocookie.com/embed/' + id + '?autoplay=1&rel=0';
      f.title = 'Video';
      f.allow = 'autoplay; encrypted-media; picture-in-picture';
      f.setAttribute('allowfullscreen', '');
      box.innerHTML = '';
      box.appendChild(f);
    });
  });

  /* ── catalog filters: plain links, one kicker at a time ───────────────── */
  var filters = document.querySelectorAll('.st-filters a');
  if (filters.length) {
    filters.forEach(function (a) {
      a.addEventListener('click', function (e) {
        e.preventDefault();
        var k = a.getAttribute('data-k') || '';
        filters.forEach(function (b) { b.classList.toggle('on', b === a); });
        document.querySelectorAll('.st-cards .st-card').forEach(function (c) {
          c.classList.toggle('is-hidden', !!k && c.getAttribute('data-k') !== k);
        });
      });
    });
  }
})();
