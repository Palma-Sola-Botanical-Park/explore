/* Groovin only: online images with a verified hard-drive backup.
   Inactive on public previews and until a complete local package is installed. */
(function () {
  'use strict';
  var ROOT = '/event-media/groovin-2026-10-18/';
  var backup = null, PUBLIC = 'https://palma-sola-botanical-park.github.io/explore/';
  var TIMEOUT = 2500, originalFetch = window.fetch.bind(window);
  window.groovinBackupState = { active:false, online:0, local:0, failures:0 };
  function absolute(url) { return new URL(url, location.href).href; }
  function entry(url) {
    if (!backup || !url || /^(data:|blob:)/.test(url)) return null;
    try { return backup.assets[absolute(url)] || null; } catch (_) { return null; }
  }
  function diskURL(row) { return ROOT + row.path; }
  function bounded(url, options, ms) {
    var controller = new AbortController(), timer = setTimeout(function () { controller.abort(); }, ms);
    return originalFetch(url, Object.assign({}, options, { signal:controller.signal }))
      .then(function (r) { if (!r.ok) throw Error('HTTP ' + r.status); return r; })
      .finally(function () { clearTimeout(timer); });
  }
  function imageBlob(response) {
    if (!/^image\//i.test(response.headers.get('Content-Type') || '')) throw Error('Not an image');
    return response.blob().then(function (blob) { if (!blob.size) throw Error('Empty image'); return blob; });
  }
  function decode(blob) {
    var url = URL.createObjectURL(blob), image = new Image(); image.src = url;
    return image.decode().then(function () { URL.revokeObjectURL(url); return blob; }, function (e) {
      URL.revokeObjectURL(url); throw e;
    });
  }
  function collect(s) {
    var urls = slidePhotoURLs(s).concat([WELCOME_ART, POPS_ART, MUSIC_ART]);
    (s.venuePhotos || []).concat(s.communityPhotos || []).forEach(function (p) { urls.push(p.src); });
    var match, regex = /\bsrc=["']([^"']+)["']/g;
    while ((match = regex.exec(s.html || ''))) urls.push(match[1].replace(/&amp;/g, '&'));
    return urls.filter(function (u, i, all) { return u && all.indexOf(u) === i; });
  }
  function rewriteHTML(html) {
    return (html || '').replace(/\bsrc=(["'])([^"']+)\1/g, function (all, quote, url) {
      return 'src=' + quote + photoDisplayURL(url.replace(/&amp;/g, '&')) + quote;
    });
  }
  function install() {
    window.groovinBackupState.active = true;
    BASE = PUBLIC; FEEDS = PUBLIC + 'data/published/';
    /* Full frozen metadata keeps credits/statistics accurate and makes a cold
       offline restart independent of GitHub. Public BASE and QR targets stay public. */
    grab = function (url) {
      var relative;
      try {
        var full = absolute(url);
        relative = full.indexOf(PUBLIC) === 0 ? full.slice(PUBLIC.length) : '';
      } catch (_) { relative = ''; }
      var path = backup.feeds[relative];
      if (!path) return Promise.resolve(null);
      return bounded(ROOT + path, { cache:'no-store' }, 4000).then(function (r) { return r.json(); });
    };
    Q.set('cache', 'off'); // hard-drive package replaces the expiring browser cache
    cacheablePhoto = function (url) { return !!entry(url); };
    cachedPhotoBlob = function (url) {
      var row = entry(url);
      if (!row) return Promise.resolve(null);
      if (photoFlights[url]) return photoFlights[url];
      /* A bounded online attempt includes body download and decoding. Decode
         failures count as failures too; an HTTP 200 with bad bytes cannot win. */
      var controller = new AbortController(), timer;
      var online = originalFetch(absolute(url), { mode:'cors', credentials:'omit', cache:'default', signal:controller.signal })
        .then(function (r) { if (!r.ok) throw Error('HTTP ' + r.status); return imageBlob(r); }).then(decode);
      var deadline = new Promise(function (_, reject) { timer = setTimeout(function () {
        controller.abort(); reject(Error('Image deadline'));
      }, TIMEOUT); });
      var job = Promise.race([online, deadline]).then(function (blob) {
        window.groovinBackupState.online++; return blob;
      }).catch(function () {
        return bounded(diskURL(row), {cache:'force-cache'}, 4000).then(imageBlob).then(decode).then(function (blob) {
          window.groovinBackupState.local++; return blob;
        });
      }).finally(function () { clearTimeout(timer); delete photoFlights[url]; });
      photoFlights[url] = job;
      return job;
    };
    var oldDisplay = photoDisplayURL;
    photoDisplayURL = function (url) {
      var ready = oldDisplay(url), row = entry(url);
      return ready !== url ? ready : row ? diskURL(row) : url;
    };
    warmSlide = function (s) {
      return Promise.all(collect(s).map(function (url) {
        var row = entry(url);
        if (!row) {
          if (/^data:/.test(url)) return null;
          throw Error('Asset absent from Groovin backup: ' + url);
        }
        return prepareCachedPhoto(url).then(function (ready) {
          if (ready === url) throw Error('Asset unavailable: ' + url);
          return ready;
        });
      }));
    };
    var oldRender = render;
    render = function (s) {
      var copy = Object.assign({}, s, {html:rewriteHTML(s.html)});
      /* Nested renderers append these after the ordinary image pass. */
      if (s.photoArt) copy.photoArt = Object.assign({}, s.photoArt, {src:photoDisplayURL(s.photoArt.src), fb:photoDisplayURL(s.photoArt.fb)});
      var duration = oldRender(copy);
      Array.prototype.forEach.call(cur.querySelectorAll('img'), function (image) {
        var url = image.getAttribute('src'), local = photoDisplayURL(url);
        if (url !== local) image.src = local;
      });
      return duration;
    };
    /* The old slide stays visible until all images for its successor decode.
       Usually prefetch completes many seconds before this timer fires. */
    var transitioning = false;
    next = function () {
      if (transitioning) return;
      transitioning = true;
      var slide = pending || makeNext(); pending = null;
      if (!slide) { transitioning = false; timer = setTimeout(next, SECONDS * 1000); return; }
      warmSlide(slide).then(function () {
        var duration = render(slide);
        if (Q.get('hold') !== '1') timer = setTimeout(next, duration * 1000);
        try {
          pending = makeNext();
          if (pending) warmSlide(pending).catch(function () {});
        } catch (_) { pending = null; }
      }).catch(function () {
        window.groovinBackupState.failures++;
        pending = slide; timer = setTimeout(next, 2000);
      }).finally(function () { transitioning = false; });
    };
    /* Local fonts prevent layout changes or missing type on an offline restart. */
    var fonts = document.createElement('link'); fonts.rel = 'stylesheet'; fonts.href = ROOT + 'fonts.css';
    Array.prototype.forEach.call(document.querySelectorAll('link[href*="fonts.googleapis.com"]'), function (link) { link.remove(); });
    document.head.appendChild(fonts);
    return new Promise(function (resolve) {
      fonts.onload = resolve; fonts.onerror = resolve; setTimeout(resolve, 2000);
    }).then(function () { return document.fonts.ready; });
  }
  window.initGroovinBackup = function () {
    if (!/^(localhost|127\.0\.0\.1)$/i.test(location.hostname) && location.pathname.indexOf('/content/') !== 0)
      return Promise.resolve(false);
    return bounded(ROOT + 'manifest.json', {cache:'no-store'}, 1500).then(function (r) { return r.json(); })
      .then(function (data) {
        if (data.schema !== 1 || data.complete !== true || !data.assets || !data.feeds) return false;
        backup = data;
        return install().then(function () { return true; });
      }).catch(function () { return false; });
  };
})();
