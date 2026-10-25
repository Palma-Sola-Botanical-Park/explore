#!/usr/bin/env python3
"""
story_publisher.py — Park Stories publisher.

Reads the stories master (private: ../Active-PSBP-Docs/stories/stories.json),
resolves every photograph through the registries, and writes:

    stories/<id>.html      one page per story and per collection
    stories.html           the catalog
    stories.json           the card index (what the home band and a TV slide read)

Build step A (2026-10-24): `--demo` renders the SAMPLE master in
Active-PSBP-Docs/prototypes/park-stories/ with a DEMO banner on every page.
Nothing in the repo's real data is read for writing, and the demo output is
not committed. The renderer that settles the look here is the one that ships.

Rules this follows (PARK_STORIES_PLAN.md):
  · a draft is in no public file. Two kinds (Randy, 2026-10-24): stage "draft"
    is off the site, and any page it had comes down; stage "draft-live" keeps
    the last published page up while the new version is written. Stage "idea"
    is a stub on the back burner: a title and a line, no hero or blocks needed,
    never written. Withdrawn
    stories get a resting page; unlisted ones get a page but no card
  · a blocked hero, poster or pair member refuses the story and says why;
    a blocked in-flow or gallery photo publishes with a loud warning
  · the credit comes from the registry, never from the story
  · deterministic output: no timestamps
  · stdlib only; imports psbp_common like everything else
"""
import argparse
import json
import re
import sys
from datetime import datetime
from html import escape as h
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from psbp_common import (  # noqa: E402
    REPO, PHOTO_CREDITS_JSON, load_json, media_url, media_page_check,
    resolve_hero_credit,
)

PRIVATE = REPO.parent / "Active-PSBP-Docs"
MASTER_DEFAULT = PRIVATE / "stories" / "stories.json"
SAMPLE_MASTER = PRIVATE / "prototypes" / "park-stories" / "sample_stories.json"
OUT_DIR = REPO / "stories"
CATALOG = REPO / "stories.html"
INDEX = REPO / "stories.json"
SITE = "Palma Sola Botanical Park"

WARN = []


def warn(msg):
    WARN.append(msg)
    print("  ! " + msg)


# ── text helpers ───────────────────────────────────────────────────────────

def bold(text):
    return re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", h(str(text)))


def paragraphs(text):
    parts = [p.strip() for p in re.split(r"\n\s*\n", str(text or "")) if p.strip()]
    return "".join(f"<p>{bold(p)}</p>" for p in parts)


def fmt_month(s):
    """'2026-02' → 'February 2026'; '2026-02-11' → 'February 11, 2026'."""
    if not s:
        return ""
    try:
        if len(s) == 7:
            return datetime.strptime(s, "%Y-%m").strftime("%B %Y")
        return datetime.strptime(s[:10], "%Y-%m-%d").strftime("%B %-d, %Y")
    except ValueError:
        return s


def fmt_day(s):
    if not s:
        return ""
    try:
        return datetime.strptime(s[:10], "%Y-%m-%d").strftime("%b %-d, %Y")
    except ValueError:
        return ""


def slug_ok(s):
    return bool(re.fullmatch(r"[a-z0-9][a-z0-9-]*", s or ""))


# ── registries ─────────────────────────────────────────────────────────────

class Registry:
    """Every photograph a story names comes through here. Returns one flat
    shape whatever the source, so the renderer never asks where it came from."""

    def __init__(self):
        creds = load_json(PHOTO_CREDITS_JSON, {"photos": []})
        self.photos = {str(r.get("photo_id")): r for r in creds.get("photos", []) if r.get("photo_id")}
        self.species = {}
        for fn, folder in (("plants.json", "plants"), ("wildlife.json", "wildlife")):
            d = load_json(REPO / fn, {})
            rows = d.get("species") if isinstance(d, dict) else d
            for r in rows or []:
                if r.get("id"):
                    self.species[r["id"]] = r

    def resolve(self, ref):
        """ref is {"photo": iNat id} or {"media": "PM-…"}, plus caption/focus.
        Returns (dict, problem). dict has url thumb by lic date src focus alt."""
        if not isinstance(ref, dict):
            return None, "a photo reference must be an object"
        if ref.get("photo"):
            pid = str(ref["photo"])
            rec = self.photos.get(pid)
            if not rec:
                return None, f"photo {pid} is not in photo_credits.json"
            if not rec.get("publish_ok"):
                return None, f"photo {pid} is not publish_ok"
            hc = resolve_hero_credit(rec)
            lic = (hc.get("credit_license") or rec.get("license") or "").upper()
            return {
                "kind": "inat", "id": pid,
                "url": media_url(rec, "web") or rec.get("photo_url", ""),
                "thumb": media_url(rec, "thumb") or rec.get("photo_url", ""),
                "by": hc["credit_name"], "lic": lic,
                "date": fmt_day(rec.get("observed_on", "")),
                "src": "via iNaturalist",
                "focus": (ref.get("focus") or rec.get("focus") or "50% 50%").strip(),
                "alt": rec.get("common_name") or "",
                "species": rec.get("psbp_id"),
            }, ""
        if ref.get("media"):
            mid = str(ref["media"])
            rec, why = media_page_check(mid)
            if not rec:
                return None, why
            thumb = next((f.get("url") for f in rec.get("files", []) if f.get("role") == "thumb" and f.get("url")), None)
            lic = (rec.get("license") or "").upper()
            return {
                "kind": "media", "id": mid,
                "url": rec["url"], "thumb": thumb or rec["url"],
                "by": (rec.get("credit_line") or rec.get("made_by") or "").strip(),
                "lic": lic if lic.startswith("CC") else "",
                "date": fmt_day(rec.get("date") or ""),
                "src": "park collection" if lic.startswith("CC") else "with permission",
                "focus": (ref.get("focus") or "50% 50%").strip(),
                "alt": rec.get("title") or "",
                "species": None,
            }, ""
        return None, "a photo reference needs `photo` or `media`"


# ── small renderers ────────────────────────────────────────────────────────

def cc_badge(lic):
    if not lic or not lic.startswith("CC"):
        return '<span class="credit-src">with permission</span>'
    term = lic.replace("CC-", "").replace("CC", "") or "0"
    return f'<span class="cc-badge"><span class="cc-mark">cc</span><span class="cc-term">{h(term)}</span></span>'


def plate(p):
    """The plate under an in-flow photograph: the site's .credit-plate."""
    src = f"{h(p['date'])} &middot; {h(p['src'])}" if p["date"] else h(p["src"])
    return ('<div class="credit-plate"><div class="credit-byline">'
            '<span class="credit-eyebrow">Photograph by</span>'
            f'<span class="credit-name">{h(p["by"])}</span></div>'
            f'<div class="credit-license">{cc_badge(p["lic"])}<span class="credit-src">{src}</span></div></div>')


def hero_attr(p):
    """The hero's credit bar, same structure as the species pages."""
    date = f'<span class="attr-date"><span class="attr-dot">&middot;</span> {h(p["date"])}</span>' if p["date"] else ""
    return ('<div class="photo-attr photo-attr--muted sp-heroattr"><span class="attr-line">'
            f'<span class="attr-credit"><span class="attr-by">{h(p["by"])}</span>{date}</span>'
            f'{cc_badge(p["lic"]) if p["lic"] else ""}'
            f'<span class="attr-src">{h(p["src"])}</span></span></div>')


def lb_meta(p):
    bits = [b for b in (p["date"], (p["lic"].replace("-", " ") if p["lic"] else ""), p["src"]) if b]
    return " · ".join(bits)


class Page:
    """Collects lightbox entries while a page renders."""

    def __init__(self):
        self.photos = []     # window.PHOTOS
        self.credits = []    # the roll at the end, in order, de-duplicated by id

    def add(self, p, alt=""):
        i = len(self.photos)
        self.photos.append({"src": p["url"], "alt": alt or p["alt"], "by": p["by"], "meta": lb_meta(p)})
        if all(c["id"] != p["id"] for c in self.credits):
            self.credits.append(p)
        return i


def figure(p, caption, page, whole=False, label=""):
    i = page.add(p, caption)
    cap = f"<figcaption>{bold(caption)}</figcaption>" if caption else ""
    lab = f'<div class="st-pair-label">{h(label)}</div>' if label else ""
    cls = "sp-figure st-figure" + (" sp-figure--whole" if whole else "")
    return (f'<figure class="{cls}" data-i="{i}">{lab}'
            f'<img src="{h(p["url"])}" alt="{h(p["alt"])}" loading="lazy" style="object-position:{h(p["focus"])}">'
            f'{cap}{plate(p)}</figure>')


# ── blocks ─────────────────────────────────────────────────────────────────

def render_blocks(story, blocks, reg, page, ctx):
    """ctx: {rel, stories, collections, demo}. Returns HTML. Blocked critical
    photos raise; blocked in-flow ones warn."""
    out = []
    for b in blocks or []:
        t = b.get("type")
        key = b.get("key", "?")
        if t == "heading":
            out.append(f'<h2 class="st-h" id="{h(key)}">{h(b.get("text", ""))}</h2>')
        elif t == "text":
            lab = f'<div class="sp-block-label">{h(b["label"])}</div>' if b.get("label") else ""
            out.append(f'<div class="sp-block st-text" id="{h(key)}">{lab}{paragraphs(b.get("text", ""))}</div>')
        elif t == "photo":
            p, why = reg.resolve(b)
            if not p:
                warn(f"{story['id']} block {key}: photograph dropped, {why}. The prose around it may refer to it.")
                continue
            out.append(figure(p, b.get("caption", ""), page, whole=(b.get("fit") == "whole")))
        elif t == "pair":
            sides = []
            labels = b.get("labels") or ["", ""]
            for n, side in enumerate(("left", "right")):
                ref = b.get(side) or {}
                p, why = reg.resolve(ref)
                if not p:
                    raise ValueError(f"block {key}: pair {side} refused, {why}")
                sides.append(figure(p, ref.get("caption", ""), page, label=labels[n] if n < len(labels) else ""))
            out.append(f'<div class="st-pair" id="{h(key)}">{"".join(sides)}</div>')
        elif t == "quote":
            who = h(b.get("who", ""))
            when = f' <span class="st-when">{h(b["when"])}</span>' if b.get("when") else ""
            out.append(f'<blockquote class="st-quote" id="{h(key)}"><p>{bold(b.get("text", ""))}</p>'
                       f'<footer>{who}{when}</footer></blockquote>')
        elif t == "gallery":
            tiles = []
            for it in b.get("items") or []:
                p, why = reg.resolve(it)
                if not p:
                    warn(f"{story['id']} block {key}: gallery photograph dropped, {why}")
                    continue
                i = page.add(p, it.get("caption", ""))
                cap = f'<figcaption>{bold(it["caption"])}</figcaption>' if it.get("caption") else ""
                tiles.append(f'<figure data-i="{i}"><div class="st-tile"><img src="{h(p["thumb"] if p["kind"]=="inat" else p["url"])}" '
                             f'alt="{h(p["alt"])}" loading="lazy" style="object-position:{h(p["focus"])}"></div>{cap}</figure>')
            if not tiles:
                warn(f"{story['id']} block {key}: gallery is empty after checks, omitted")
                continue
            intro = f'<p class="st-gallery-intro">{bold(b["intro"])}</p>' if b.get("intro") else ""
            out.append(f'<div class="st-gallery" id="{h(key)}">{intro}<div class="st-grid">{"".join(tiles)}</div></div>')
        elif t == "video":
            poster, why = reg.resolve(b.get("poster") or {})
            if not poster:
                raise ValueError(f"block {key}: video poster refused, {why}")
            cap = f"<figcaption>{bold(b['caption'])}</figcaption>" if b.get("caption") else ""
            if b.get("youtube"):
                yt = re.sub(r"[^A-Za-z0-9_-]", "", str(b["youtube"]))
                media = (f'<div class="st-video" data-yt="{yt}">'
                         f'<img src="{h(poster["url"])}" alt="" loading="lazy" style="object-position:{h(poster["focus"])}">'
                         '<button type="button" class="st-play" aria-label="Play the video">'
                         '<svg viewBox="0 0 24 24" width="34" height="34" aria-hidden="true"><path d="M8 5v14l11-7z" fill="currentColor"/></svg>'
                         '<span>Play</span></button></div>')
            elif b.get("media"):
                v, why = media_page_check(str(b["media"]))
                if not v:
                    raise ValueError(f"block {key}: video file refused, {why}")
                media = (f'<video class="st-video" controls preload="none" playsinline poster="{h(poster["url"])}">'
                         f'<source src="{h(v["url"])}" type="video/mp4"></video>')
            else:
                raise ValueError(f"block {key}: video needs `youtube` or `media`")
            out.append(f'<figure class="st-videowrap" id="{h(key)}">{media}{cap}{plate(poster)}</figure>')
        elif t == "link":
            out.append(link_card(b, reg, ctx))
        else:
            warn(f"{story['id']} block {key}: unknown block type {t!r}, skipped")
    return "".join(out)


def link_card(b, reg, ctx):
    rel = ctx["rel"]
    text = b.get("text", "")
    if b.get("species"):
        sp = reg.species.get(b["species"])
        if not sp:
            warn(f"link to species {b['species']}: no published page, skipped")
            return ""
        img = sp.get("photo_thumb") or sp.get("photo") or ""
        return (f'<a class="st-link" href="{rel}{h(sp["page"])}">'
                f'<img src="{h(img)}" alt="" loading="lazy" style="object-position:{h(sp.get("focus") or "center")}">'
                f'<span><span class="st-link-eyebrow">Species page</span><span class="st-link-title">{h(sp.get("common", ""))}</span>'
                f'{f"<span class=st-link-text>{bold(text)}</span>" if text else ""}</span></a>')
    for kind, pool in (("story", ctx["stories"]), ("collection", ctx["collections"])):
        if b.get(kind):
            s = pool.get(b[kind])
            if not s or not public(s):
                warn(f"link to {kind} {b[kind]}: not public, skipped")
                return ""
            return story_card(s, reg, ctx, inline=True, text=text)
    warn("link block without a target, skipped")
    return ""


def public(s):
    return s.get("stage") == "published" and s.get("visibility") in ("listed", "unlisted")


def listed(s):
    return s.get("stage") == "published" and s.get("visibility") == "listed"


def story_card(s, reg, ctx, inline=False, text=""):
    p, why = reg.resolve(s.get("hero") or {})
    if not p:
        warn(f"card for {s['id']}: hero not resolvable, {why}")
        return ""
    href = f"{ctx['rel']}stories/{s['id']}.html"
    kicker = s.get("kicker", "")
    is_coll = "members" in s
    eyebrow = ("Collection · " if is_coll else "") + kicker
    cls = "st-link" if inline else "st-card"
    body = (f'<span class="st-link-eyebrow">{h(eyebrow)}</span><span class="st-link-title">{h(s["title"])}</span>'
            + (f'<span class="st-link-text">{bold(text or s.get("dek", ""))}</span>'))
    if inline:
        return (f'<a class="{cls}" href="{href}"><img src="{h(p["thumb"])}" alt="" loading="lazy" '
                f'style="object-position:{h(p["focus"])}"><span>{body}</span></a>')
    n = f'<span class="st-card-n">{len([m for m in s["members"] if m in ctx["stories"] and listed(ctx["stories"][m])])} stories</span>' if is_coll else ""
    return (f'<a class="st-card{" st-card--coll" if is_coll else ""}" href="{href}">'
            f'<div class="st-card-img"><img src="{h(p["url"])}" alt="" loading="lazy" style="object-position:{h(p["focus"])}">{n}</div>'
            f'<div class="st-card-body"><span class="st-link-eyebrow">{h(eyebrow)}</span>'
            f'<span class="st-card-title">{h(s["title"])}</span><span class="st-card-dek">{h(s.get("dek", ""))}</span>'
            f'<span class="st-card-go">Read {"the collection" if is_coll else "the story"} &rarr;</span></div></a>')


# ── page skeletons ─────────────────────────────────────────────────────────

def demo_banner(ctx):
    if ctx.get("preview"):
        return ('<div class="st-demo"><strong>DRAFT</strong> · a preview of an unpublished story · '
                'not on the site · the words are still being checked</div>')
    if not ctx["demo"]:
        return ""
    return ('<div class="st-demo"><strong>DEMO</strong> · sample stories · the words are placeholders, '
            'not checked facts · nothing here is published · '
            f'<a href="{ctx["rel"]}stories.html">catalog</a></div>')


def head(title, desc, rel, hero_url=""):
    og = (f'<meta property="og:title" content="{h(title)}">\n<meta property="og:description" content="{h(desc)}">\n'
          + (f'<meta property="og:image" content="{h(hero_url)}">\n' if hero_url else "")
          + '<meta property="og:type" content="article">')
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{h(title)} — {SITE}</title>
<meta name="description" content="{h(desc)}">
{og}
<link rel="icon" type="image/png" href="{rel}images/favicon.png">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Playfair+Display:ital,wght@0,700;1,600&family=Source+Sans+3:wght@400;600;700&display=swap" rel="stylesheet">
<link rel="stylesheet" href="{rel}css/psbp.css">
<link rel="stylesheet" href="{rel}css/species-v2.css">
<link rel="stylesheet" href="{rel}css/stories.css?v=3">
</head>
<body data-bands="on">
<div id="nav-placeholder"></div>
"""


LIGHTBOX = """
<div class="lb" id="lb" aria-hidden="true">
  <button class="lb-close" id="lbClose" aria-label="Close">&times;</button>
  <div class="lb-stage" id="lbStage"><img id="lbImg" src="" alt=""></div>
  <div class="lb-foot"><div class="lb-cred"><div class="lb-eyebrow">Photograph by</div>
  <div class="lb-name" id="lbName"></div><div class="lb-meta" id="lbMeta"></div></div>
  <div class="lb-ctrls"><button class="lb-btn" id="lbPrev" aria-label="Previous photograph">&#8249;</button>
  <span class="lb-count" id="lbCount"></span>
  <button class="lb-btn" id="lbNext" aria-label="Next photograph">&#8250;</button></div>
  <div class="lb-hint">Swipe to move between photographs</div></div>
</div>
"""


def foot(rel, photos_js="[]", banner=False):
    return f"""{LIGHTBOX if photos_js != "[]" else ""}
<div id="footer-placeholder"></div>
<script>window.PHOTOS={photos_js};</script>
{'<script src="' + rel + 'js/photo-credits.js"></script>' if banner else ''}
<script src="{rel}js/site.js"></script>
<script src="{rel}js/stories.js?v=1"></script>
<script>if (typeof injectShared === 'function') {{ injectShared({{ inatBar: false }}); }}{' PSBPPhotos.mountBanner();' if banner else ''}</script>
</body>
</html>"""


def credits_roll(page):
    if len(page.credits) < 2:
        return ""
    figs = []
    for p in page.credits:
        figs.append(f'<figure><div class="shot"><img src="{h(p["thumb"])}" alt="" loading="lazy" '
                    f'style="object-position:{h(p["focus"])}"></div><figcaption>{plate(p)}</figcaption></figure>')
    return ('<section class="st-roll" id="photos"><h2 class="st-h">Photographs</h2>'
            '<p class="st-roll-intro">Every one taken in this park, by the people who walk it.</p>'
            f'<div class="sp-gal st-gal">{"".join(figs)}</div></section>')


def related_section(s, reg, ctx, member_of):
    cards = []
    rel = s.get("related") or {}
    for sid in rel.get("stories") or []:
        t = ctx["stories"].get(sid)
        if t and listed(t) and t["id"] != s["id"]:
            cards.append(story_card(t, reg, ctx, inline=True))
        elif not t:
            warn(f"{s['id']}: related story {sid} does not exist")
    for pid in rel.get("species") or []:
        if pid in reg.species:
            cards.append(link_card({"species": pid}, reg, ctx))
        else:
            warn(f"{s['id']}: related species {pid} has no published page")
    for c in member_of:
        cards.insert(0, story_card(c, reg, ctx, inline=True, text="This story is one part of a larger one."))
    if not cards:
        return ""
    return f'<section class="st-related"><h2 class="st-h">Keep reading</h2><div class="st-related-grid">{"".join(cards)}</div></section>'


def render_story(s, reg, ctx):
    page = Page()
    hero, why = reg.resolve(s.get("hero") or {})
    if not hero:
        raise ValueError(f"hero refused, {why}")
    member_of = [c for c in ctx["collections"].values() if s["id"] in (c.get("members") or []) and listed(c)]
    body = render_blocks(s, s.get("blocks"), reg, page, ctx)
    meta_bits = []
    if s.get("byline"):
        meta_bits.append(h(s["byline"]))
    if s.get("date"):
        meta_bits.append(h(fmt_month(s["date"])))
    if member_of:
        c = member_of[0]
        meta_bits.append(f'Part of <a href="{c["id"]}.html">{h(c["title"])}</a>')
    meta = f'<div class="st-meta">{" &middot; ".join(meta_bits)}</div>' if meta_bits else ""
    updated = f'<div class="st-updated">Story updated {h(fmt_day(s["updated"]))}</div>' if s.get("updated") else ""
    html = head(s["title"], s.get("dek", ""), ctx["rel"], hero["url"]) + demo_banner(ctx) + f"""
<div class="sp-hero st-hero">
  <img class="sp-hero-fg" src="{h(hero["url"])}" alt="" style="object-position:{h(hero["focus"])}">
  <div class="sp-hero-scrim"></div>
  {hero_attr(hero)}
  <div class="sp-hero-inner">
    <div class="sp-eyebrow">{h(s.get("kicker", ""))}</div>
    <h1 class="sp-name">{h(s["title"])}</h1>
    <div class="st-dek">{h(s.get("dek", ""))}</div>
  </div>
</div>
<main class="st-body">
  {meta}
  {body}
  {updated}
  {related_section(s, reg, ctx, member_of)}
  {credits_roll(page)}
</main>
<a class="plant-float-back" href="{ctx["rel"]}stories.html">All stories</a>
""" + foot(ctx["rel"], json.dumps(page.photos, ensure_ascii=False))
    return html


def render_collection(c, reg, ctx):
    page = Page()
    hero, why = reg.resolve(c.get("hero") or {})
    if not hero:
        raise ValueError(f"hero refused, {why}")
    intro = render_blocks(c, c.get("intro"), reg, page, ctx)
    members = []
    for n, mid in enumerate(c.get("members") or [], 1):
        m = ctx["stories"].get(mid)
        if not m:
            warn(f"collection {c['id']}: member {mid} does not exist")
            continue
        if not listed(m):
            warn(f"collection {c['id']}: member {mid} is not listed, left off the page")
            continue
        card = story_card(m, reg, ctx)
        if c.get("sequence"):
            card = card.replace('<div class="st-card-body">', f'<div class="st-card-body"><span class="st-card-seq">{n}</span>', 1)
        members.append(card)
    grid = f'<div class="st-members grid-auto">{"".join(members)}</div>' if members else '<p class="text-soft">Stories are on their way.</p>'
    html = head(c["title"], c.get("dek", ""), ctx["rel"], hero["url"]) + demo_banner(ctx) + f"""
<div class="sp-hero st-hero">
  <img class="sp-hero-fg" src="{h(hero["url"])}" alt="" style="object-position:{h(hero["focus"])}">
  <div class="sp-hero-scrim"></div>
  {hero_attr(hero)}
  <div class="sp-hero-inner">
    <div class="sp-eyebrow">A collection &middot; {h(c.get("kicker", ""))}</div>
    <h1 class="sp-name">{h(c["title"])}</h1>
    <div class="st-dek">{h(c.get("dek", ""))}</div>
  </div>
</div>
<main class="st-body">
  {intro}
  <h2 class="st-h">The stories{" , in order" if c.get("sequence") else ""}</h2>
</main>
<div class="st-wide">{grid}</div>
<main class="st-body">
  {related_section(c, reg, ctx, [])}
  {credits_roll(page)}
</main>
<a class="plant-float-back" href="{ctx["rel"]}stories.html">All stories</a>
""" + foot(ctx["rel"], json.dumps(page.photos, ensure_ascii=False))
    return html


def render_resting(s, ctx):
    return head(s["title"], "This story is resting.", ctx["rel"]) + demo_banner(ctx) + f"""
<main class="st-body st-resting">
  <div class="sp-eyebrow">Park Stories</div>
  <h1>{h(s["title"])}</h1>
  <p>This story is resting for now. It may come back in a new form.</p>
  <p><a class="btn btn-green" href="{ctx["rel"]}stories.html">Read the other stories</a></p>
</main>
""" + foot(ctx["rel"])


def render_catalog(stories, collections, reg, ctx):
    colls = [story_card(c, reg, ctx) for c in collections.values() if listed(c)]
    order = sorted([s for s in stories.values() if listed(s)],
                   key=lambda s: (not s.get("pinned"), s.get("date", "") == "", ""), reverse=False)
    order.sort(key=lambda s: s.get("date", ""), reverse=True)
    order.sort(key=lambda s: not s.get("pinned"))
    kickers = sorted({s.get("kicker", "") for s in order if s.get("kicker")})
    filt = "".join(f'<a href="#" data-k="{h(k)}">{h(k)}</a>' for k in kickers)
    cards = "".join(story_card(s, reg, ctx).replace('<a class="st-card"', f'<a class="st-card" data-k="{h(s.get("kicker", ""))}"', 1) for s in order)
    coll_html = f'<div class="st-colls">{"".join(colls)}</div>' if colls else ""
    html = head("Stories from the park", "People, projects, storms and the park's photographers.", ctx["rel"]) + demo_banner(ctx) + f"""
<div class="page-hero">
  <div class="page-hero-bg"></div>
  <div class="page-hero-content" style="text-align:center;margin:0 auto">
    <div class="label label-light">Park Stories</div>
    <h1>Stories from the park</h1>
    <p>The people who made it, the storms it came through, and the photographers who show it to us.</p>
  </div>
  <div class="page-hero-credit"></div>
</div>
<section class="section-inner st-catalog">
  {coll_html}
  <nav class="st-filters" aria-label="Collections"><a href="#" class="on" data-k="">All</a>{filt}</nav>
  <div class="grid-auto st-cards" id="storyGrid">{cards}</div>
</section>
""" + foot(ctx["rel"], banner=True)
    return html


def orphan_pages(outputs, keep_ids=()):
    """Pages in stories/ whose id is no longer in the master at all. Pages of
    stories still in the master are handled by stage (draft-live keeps its page,
    draft loses it). Draft previews (_draft-*.html) are never orphans."""
    if not OUT_DIR.is_dir():
        return []
    written = {p.resolve() for p in outputs}
    return sorted(p for p in OUT_DIR.glob("*.html")
                  if p.resolve() not in written and not p.name.startswith("_draft-")
                  and p.stem not in keep_ids)


# ── checks ─────────────────────────────────────────────────────────────────

def audit(stories, collections, reg):
    """Every story check, in one place: the Stories-mode Health page reads
    this, and `--check` prints it. Returns [{id, title, level, msg}], level
    "error" (Make it so would refuse or drop something) or "warn"."""
    out = []
    def add(s, level, msg):
        out.append({"id": s.get("id", "?"), "title": s.get("title", ""), "level": level, "msg": msg})
    names = load_json(REPO / "data" / "sources" / "photographer_names.json", {}) or {}
    everything = list(stories.values()) + list(collections.values())
    leads = [s["id"] for s in stories.values() if listed(s) and s.get("home")]
    for s in everything:
        is_coll = "members" in s
        if s.get("stage") not in ("idea", "draft", "draft-live", "published"):
            add(s, "error", f"unknown status {s.get('stage')!r}")
        if s.get("visibility") not in ("listed", "unlisted", "withdrawn"):
            add(s, "error", f"unknown visibility {s.get('visibility')!r}")
        if not s.get("title"):
            add(s, "error", "no title")
        if s.get("stage") == "idea":
            continue                                   # a stub: nothing else to check yet
        if not s.get("hero"):
            add(s, "error", "no hero photo yet")
        else:
            hero, why = reg.resolve(s["hero"])
            if not hero:
                add(s, "error", f"hero: {why}")
        blocks = s.get("intro") if is_coll else s.get("blocks")
        if not blocks:
            add(s, "warn", "no blocks yet" if not is_coll else "no introduction yet")
        keys = set()
        for b in blocks or []:
            k = b.get("key", "?")
            if k in keys:
                add(s, "warn", f"block key {k} used twice")
            keys.add(k)
            t = b.get("type")
            if t in ("heading", "text", "quote"):
                if not (b.get("text") or "").strip():
                    add(s, "warn", f"block {k}: empty {t}")
            elif t == "photo":
                p_, why = reg.resolve(b)
                if not p_:
                    add(s, "warn", f"block {k}: photograph would be dropped, {why}")
            elif t == "pair":
                for side in ("left", "right"):
                    p_, why = reg.resolve(b.get(side) or {})
                    if not p_:
                        add(s, "error", f"block {k}: pair {side}, {why}")
            elif t == "gallery":
                items = b.get("items") or []
                if not items:
                    add(s, "warn", f"block {k}: empty gallery")
                for it in items:
                    p_, why = reg.resolve(it)
                    if not p_:
                        add(s, "warn", f"block {k}: gallery photograph would be dropped, {why}")
            elif t == "video":
                p_, why = reg.resolve(b.get("poster") or {})
                if not p_:
                    add(s, "error", f"block {k}: video poster, {why}")
                if not (b.get("youtube") or b.get("media")):
                    add(s, "error", f"block {k}: video has no youtube id or media file")
                elif b.get("media"):
                    v, why = media_page_check(str(b["media"]))
                    if not v:
                        add(s, "error", f"block {k}: video file, {why}")
            elif t == "link":
                if b.get("species") and b["species"] not in reg.species:
                    add(s, "warn", f"block {k}: species {b['species']} has no published page; card would be skipped")
                if b.get("story") and b["story"] not in stories:
                    add(s, "warn", f"block {k}: story {b['story']} does not exist")
                if b.get("collection") and b["collection"] not in collections:
                    add(s, "warn", f"block {k}: collection {b['collection']} does not exist")
            else:
                add(s, "warn", f"block {k}: unknown block type {t!r}; it would be skipped")
        rel = s.get("related") or {}
        for sid in rel.get("stories") or []:
            if sid not in stories:
                add(s, "warn", f"related story {sid} does not exist")
        for pid in rel.get("species") or []:
            if pid not in reg.species:
                add(s, "warn", f"related species {pid} has no published page")
        for who in s.get("people") or []:
            if who not in names:
                add(s, "warn", f"person {who} is not in photographer_names.json")
        if is_coll:
            for mid in s.get("members") or []:
                if mid not in stories:
                    add(s, "error", f"member {mid} does not exist")
                elif not listed(stories[mid]):
                    add(s, "warn", f"member {mid} is not published and in the catalog, so it will not show")
        if s.get("home") and not is_coll and s.get("stage") == "published" and s.get("visibility") != "listed":
            add(s, "error", "lead story, but not in the catalog")
    problem = lead_problem(stories)
    if problem:
        out.append({"id": "", "title": "Lead story", "level": "error", "msg": problem})
    return out


# ── main ───────────────────────────────────────────────────────────────────

def load_master(path):
    m = load_json(path, None)
    if not m:
        sys.exit(f"cannot read the stories master at {path}")
    stories = {}
    collections = {}
    for s in m.get("stories", []):
        if not slug_ok(s.get("id")):
            warn(f"story id {s.get('id')!r} is not a slug, skipped")
            continue
        if s["id"] in stories:
            warn(f"story id {s['id']} used twice, second one skipped")
            continue
        stories[s["id"]] = s
    for c in m.get("collections", []):
        if not slug_ok(c.get("id")) or c["id"] in stories or c["id"] in collections:
            warn(f"collection id {c.get('id')!r} is missing, a duplicate, or collides with a story; skipped")
            continue
        collections[c["id"]] = c
    return stories, collections


def lead_problem(stories):
    """One and only one lead story among the published, catalog-listed ones
    (Randy, 2026-10-24). None needed while nothing is published. Returns the
    sentence to show, or "" when all is well."""
    live = [s for s in stories.values() if listed(s)]
    leads = [s["id"] for s in live if s.get("home")]
    if not live:
        return ""
    if not leads:
        return "No lead story. Set one published story to Lead story first."
    if len(leads) > 1:
        return "More than one lead story: " + ", ".join(leads) + ". Only one can lead."
    return ""


def main():
    ap = argparse.ArgumentParser(description="Park Stories publisher")
    ap.add_argument("--master", help="path to stories.json (default: the private master)")
    ap.add_argument("--demo", action="store_true", help="render the SAMPLE master with a DEMO banner")
    ap.add_argument("--dry-run", action="store_true", help="resolve and render, write nothing")
    ap.add_argument("--clean", action="store_true",
                    help="after writing, delete pages in stories/ that this run did not write (demo leftovers, retired ids); never _draft-* previews")
    ap.add_argument("--check", action="store_true", help="run every story check and print the findings; writes nothing")
    ap.add_argument("--preview", metavar="ID",
                    help="render one story whatever its stage, with a DRAFT banner, to stories/_draft-ID.html; nothing else is written")
    args = ap.parse_args()

    master = Path(args.master) if args.master else (SAMPLE_MASTER if args.demo else MASTER_DEFAULT)
    print(f"master: {master}")
    stories, collections = load_master(master)
    reg = Registry()
    problem = lead_problem(stories)
    if problem and not args.preview and not args.dry_run:
        sys.exit("Nothing written. " + problem)
    if problem:
        warn(problem)

    if args.check:
        findings = audit(stories, collections, reg)
        for f in findings:
            print(f"  {'!!' if f['level'] == 'error' else ' !'} {f['id'] or f['title']}: {f['msg']}")
        print("all clear" if not findings else f"{len(findings)} finding(s)")
        return 1 if any(f["level"] == "error" for f in findings) else 0

    if args.preview:
        s = stories.get(args.preview) or collections.get(args.preview)
        if not s:
            sys.exit(f"no story or collection with id {args.preview!r} in {master}")
        ctx = {"rel": "../", "stories": stories, "collections": collections, "demo": False, "preview": True}
        try:
            html = (render_collection if "members" in s else render_story)(s, reg, ctx)
        except ValueError as e:
            sys.exit(f"{s['id']} cannot be rendered: {e}")
        path = OUT_DIR / f"_draft-{s['id']}.html"
        OUT_DIR.mkdir(exist_ok=True)
        path.write_text(html, encoding="utf-8")
        print(f"preview written: {path.relative_to(REPO)}  (stage {s.get('stage')}, {s.get('visibility')})")
        if WARN:
            print(f"\n{len(WARN)} warning(s) above need a look.")
        return 0
    ctx_page = {"rel": "../", "stories": stories, "collections": collections, "demo": args.demo}
    ctx_root = {"rel": "", "stories": stories, "collections": collections, "demo": args.demo}

    outputs = {}       # path → html
    index = {"meta": {"source": master.name, "demo": args.demo}, "collections": [], "stories": []}
    for s in list(stories.values()) + list(collections.values()):
        is_coll = "members" in s
        if s.get("stage") not in ("published", "draft", "draft-live", "idea"):
            warn(f"{s['id']}: unknown stage {s.get('stage')!r}; treated as draft, off the site")
        if s.get("stage") != "published":
            print(f"  - {s['id']}: {s.get('stage')}, not written")
            continue
        vis = s.get("visibility")
        path = OUT_DIR / f"{s['id']}.html"
        if vis == "withdrawn":
            outputs[path] = render_resting(s, ctx_page)
            print(f"  · {s['id']}: withdrawn, resting page")
            continue
        if vis not in ("listed", "unlisted"):
            warn(f"{s['id']}: unknown visibility {vis!r}, not written")
            continue
        try:
            outputs[path] = (render_collection if is_coll else render_story)(s, reg, ctx_page)
        except ValueError as e:
            warn(f"{s['id']} REFUSED: {e}")
            continue
        print(f"  ✓ {s['id']}: {'collection' if is_coll else 'story'}, {vis}")
        if vis == "listed":
            hero, _ = reg.resolve(s.get("hero") or {})
            row = {"id": s["id"], "title": s["title"], "dek": s.get("dek", ""), "kicker": s.get("kicker", ""),
                   "page": f"stories/{s['id']}.html", "date": s.get("date", ""),
                   "teaser": s.get("teaser", ""), "home": bool(s.get("home")), "pinned": bool(s.get("pinned")),
                   "hero": hero["url"], "hero_thumb": hero["thumb"], "focus": hero["focus"], "credit": hero["by"]}
            if is_coll:
                row["members"] = [m for m in s.get("members", []) if m in stories and listed(stories[m])]
                index["collections"].append(row)
            else:
                row["collections"] = [c["id"] for c in collections.values() if s["id"] in (c.get("members") or []) and listed(c)]
                index["stories"].append(row)

    outputs[CATALOG] = render_catalog(stories, collections, reg, ctx_root)
    index["stories"].sort(key=lambda r: (not r["pinned"], r["date"]), reverse=False)
    index["stories"].sort(key=lambda r: r["date"], reverse=True)
    index["stories"].sort(key=lambda r: not r["pinned"])

    if args.dry_run:
        print(f"dry run: {len(outputs)} pages rendered, nothing written")
    else:
        OUT_DIR.mkdir(exist_ok=True)
        for path, html in outputs.items():
            path.write_text(html, encoding="utf-8")
        INDEX.write_text(json.dumps(index, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"wrote {len(outputs)} pages, {INDEX.name} ({len(index['stories'])} stories, {len(index['collections'])} collections)")
        # stage "draft": the row says off the site, so the old page comes down now
        for s in list(stories.values()) + list(collections.values()):
            page = OUT_DIR / f"{s['id']}.html"
            if s.get("stage") in ("draft", "idea") and page.is_file():
                page.unlink()
                print(f"taken down ({s.get('stage')}, off the site): {page.name}")
        orphans = orphan_pages(outputs, keep_ids=set(stories) | set(collections))
        if orphans:
            if args.clean:
                for o in orphans:
                    o.unlink()
                print(f"cleaned {len(orphans)} page(s) this run did not write: " + ", ".join(o.name for o in orphans))
            else:
                print(f"{len(orphans)} page(s) in stories/ whose id is not in the master (demo leftovers or deleted "
                      "stories; --clean removes them): " + ", ".join(o.name for o in orphans))
        kept = [s["id"] for s in stories.values() if s.get("stage") == "draft-live" and (OUT_DIR / f"{s['id']}.html").is_file()]
        if kept:
            print("kept on the site while in draft (the last published version): " + ", ".join(kept))
    if WARN:
        print(f"\n{len(WARN)} warning(s) above need a look.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
