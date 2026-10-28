/* ============================================================
   PSBPPhotoSearch — the one photo search, shared.
   Used by photos.html (the photo directory). Written to be reused by
   The Dirt's Stories Photos tab, so it knows nothing about any page:
   give it records and a function that returns each record's searchable
   fields, and it answers queries.

     const s = PSBPPhotoSearch.create(items, it => ({
       title: it.title, tags: it.tags, other: [it.caption]
     }));
     s.search('pond palm')   // → matching items, best first

   Every word of the query must appear somewhere (title, a tag, or the
   other text). A word matching the title or a whole tag ranks higher.
   Case and accents are ignored. An empty query returns everything.
   ============================================================ */
(function (root) {
  function norm(s) {
    return String(s == null ? '' : s)
      .normalize('NFD').replace(/[̀-ͯ]/g, '')
      .toLowerCase().replace(/[^a-z0-9]+/g, ' ').trim();
  }

  function create(items, fieldsOf) {
    const rows = items.map(item => {
      const f = fieldsOf(item);
      const title = norm(f.title);
      const tags = (f.tags || []).map(norm).filter(Boolean);
      const other = norm((f.other || []).join(' '));
      return { item, title, tags, all: ' ' + [title, tags.join(' '), other].join(' ') + ' ' };
    });

    function search(query) {
      const words = norm(query).split(' ').filter(Boolean);
      if (!words.length) return items.slice();
      const hits = [];
      for (const r of rows) {
        let score = 0, ok = true;
        for (const w of words) {
          if (!r.all.includes(w)) { ok = false; break; }
          if (r.tags.includes(w)) score += 3;
          if ((' ' + r.title + ' ').includes(' ' + w)) score += 2;
          score += 1;
        }
        if (ok) hits.push({ item: r.item, score });
      }
      hits.sort((a, b) => b.score - a.score);
      return hits.map(h => h.item);
    }

    return { search };
  }

  root.PSBPPhotoSearch = { create, norm };
})(window);
