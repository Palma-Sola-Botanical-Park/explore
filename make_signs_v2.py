#!/usr/bin/env python3
"""
make_signs_v2.py — PSBP sign builder, v2 (post-beta "stronger signs" revision).

WHAT CHANGED FROM v1
  • The sign is now LANDSCAPE, 7.6" x 4.75" — a bit under half a letter page.
  • Two signs are imposed, stacked, on one PORTRAIT 8.5x11 sheet. Print on
    cardstock, trim on the crop marks, drop BOTH trimmed signs into ONE letter
    laminating pouch, laminate, then cut the pouch apart between them. Every
    finished sign ends up with ~1/2" of sealed lamination on all four sides.
  • Photo credit is now two lines: photographer NAME big, license tucked
    underneath it and smaller ("CC BY-NC · via iNaturalist").
  • New: ORIGIN chip in the scientific-name band ("Native to Florida",
    "NE Brazil", ...) — mirrors the gold family tag on the web page.
  • New: TEASER line under the photo + a gold SCAN band under the QR.
  • QR stays big (2.64"). See the URL note below — this matters.

THE QR / URL NOTE (read this once)
  Scan distance is set by MODULE size (the little squares), not by how big the
  code looks. The v1 sign was 3.87" wide but encoded an 87-character URL, so
  its modules were 0.105". If you keep that long URL on the new 2.64" code the
  modules drop to 0.071" and you lose about a third of your scan distance.
  Serve a short path — e.g. /explore/p/719 or palmasolabp.org/p/719 — and the
  2.64" code has modules of 0.08"–0.106", i.e. it scans as far as the old
  full-page sign did. Set URL_STYLE / SHORT_BASE below. The build report prints
  the module size for every sign so you can see it before you print.

USAGE
  python3 make_signs_v2.py PSBP-00717 PSBP-00719 PSBP-00720 ...
  python3 make_signs_v2.py --file signs.txt        # one ID per line
  python3 make_signs_v2.py --each-twice PSBP-00719 # two copies of one sign
Requires: reportlab, Pillow.
"""
import sys, os, re, json, glob, io, zipfile, urllib.request, tempfile, html
from PIL import Image, ImageOps
from reportlab.pdfgen import canvas
from reportlab.lib.colors import HexColor, black, white
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.graphics.barcode.qr import QrCodeWidget
from reportlab.graphics.shapes import Drawing
from reportlab.graphics import renderPDF

# ─────────────────────────── CONFIG ───────────────────────────
REPO      = os.path.expanduser("~/Documents/GitHub/explore")
OUT_DIR   = os.path.expanduser("~/Desktop/psbp_signs")
FONTS_DIR = os.path.join(REPO, "fonts")
LOGO_GREEN = os.path.join(REPO, "images", "psbp_logo_green.png")

SITE_BASE = "https://palma-sola-botanical-park.github.io/explore/plants/"
# URL_STYLE: 'full'  = the existing long /plants/PSBP-00719-Carnaba-Palm.html
#            'short' = SHORT_BASE + the numeric id  (far better QR density)
URL_STYLE  = "full"
SHORT_BASE = "https://palma-sola-botanical-park.github.io/explore/p/"
# When the domain moves, this becomes "https://palmasolabp.org/p/" and the QR
# gets denser still. QR codes are permanent once they're in the ground —
# decide the URL BEFORE the real print run.

# Copy overrides the director edits by hand. Lives at:
#   REPO/data/sources/sign_copy.json
#   { "PSBP-00719": { "origin": "NE Brazil", "teaser": "…" }, ... }
COPY_FILE  = "data/sources/sign_copy.json"
DRAFT_FILE = "sign_copy_DRAFTS.json"      # written to OUT_DIR for editing

ORIGIN_MAX = 24        # characters — it has to share the band with the sci name
TEASER_MAX = 135       # characters — four short lines under the photo
# ───────────────────────────────────────────────────────────────

GREEN_DEEP=HexColor('#1a3a1f'); GREEN_MID=HexColor('#2d6a35'); GOLD=HexColor('#c5922a')
LGOLD=HexColor('#e6c579'); CREAM=HexColor('#f8f6f0'); DARK=HexColor('#2b2b2b'); GRAY=HexColor('#666660')
CREDIT2=HexColor('#cdc9b6'); NATIVE_BG=HexColor('#dde9d6'); NATIVE_FG=HexColor('#1f4d28')
MARK=HexColor('#9a9a9a')

# ── sign + sheet geometry (inches → points) ──
IN = 72.0
SIGN_W, SIGN_H = 7.6*IN, 4.75*IN          # 547.2 x 342 — the TRIM size
PAGE_W, PAGE_H = 8.5*IN, 11.0*IN          # portrait letter print sheet
SHEET_GAP      = 0.70*IN                  # space between the two signs
SIGN_X  = (PAGE_W - SIGN_W)/2.0
SIGN_Y0 = (PAGE_H - 2*SIGN_H - SHEET_GAP)/2.0     # bottom sign
SIGN_Y1 = SIGN_Y0 + SIGN_H + SHEET_GAP            # top sign

B = 6                                     # border inset
FOOTER_H = 40
GUTTER   = 6
COL_L0, COL_L1 = B, 330.0                 # left column (photo/name/teaser)
COL_R0, COL_R1 = 336.0, SIGN_W - B        # right column (QR/scan/logo)
PHOTO_H  = 196.0
PHOTO_ASPECT = (COL_L1-COL_L0)/PHOTO_H    # 324/196 = 1.653
SCI_BAND_H = 26.0
QR_SIZE  = 190.0                          # 2.64"
LOGO_ASPECT = 1.778

# ─────────────── Playfair Display: auto-install + register ───────────────
def ensure_playfair():
    need={'PlayfairDisplay-Bold.ttf','PlayfairDisplay-Italic.ttf','PlayfairDisplay-Regular.ttf'}
    have=set(os.listdir(FONTS_DIR)) if os.path.isdir(FONTS_DIR) else set()
    if need<=have: return True
    os.makedirs(FONTS_DIR, exist_ok=True)
    print("Fonts: Playfair Display not found locally — downloading from Google Fonts (one time)…")
    try:
        url="https://fonts.google.com/download?family=Playfair%20Display"
        req=urllib.request.Request(url, headers={'User-Agent':'PSBP-sign-builder/2.0'})
        data=urllib.request.urlopen(req, timeout=60).read()
        z=zipfile.ZipFile(io.BytesIO(data)); got=0
        for name in z.namelist():
            base=os.path.basename(name)
            if base in need and '/static/' in ('/'+name):
                with z.open(name) as f, open(os.path.join(FONTS_DIR,base),'wb') as out:
                    out.write(f.read()); got+=1
        if got<len(need):
            for name in z.namelist():
                base=os.path.basename(name)
                if base in need and not os.path.exists(os.path.join(FONTS_DIR,base)):
                    with z.open(name) as f, open(os.path.join(FONTS_DIR,base),'wb') as out:
                        out.write(f.read()); got+=1
        ok={n for n in need if os.path.exists(os.path.join(FONTS_DIR,n))}
        if len(ok)==len(need):
            print(f"Fonts: installed Playfair Display into {FONTS_DIR} — commit that folder to keep it.")
            return True
        print("Fonts: download didn't contain the static Playfair files. Using fallback serif.")
        return False
    except Exception as e:
        print(f"Fonts: couldn't download Playfair ({e.__class__.__name__}). Using fallback serif.")
        print("       You can also add them manually to:", FONTS_DIR)
        return False

_PLAYFAIR = ensure_playfair()

def _try_register(name, filename, dirs):
    for d in dirs:
        fp=os.path.join(d, filename)
        if os.path.exists(fp):
            try: pdfmetrics.registerFont(TTFont(name, fp)); return True
            except Exception: pass
    return False

_SANS_DIRS=['/usr/share/fonts/truetype/liberation/','/Library/Fonts/','/System/Library/Fonts/Supplemental/']
for n,f in [('SansR','LiberationSans-Regular'),('SansB','LiberationSans-Bold'),('SansI','LiberationSans-Italic'),
            ('SerifR','LiberationSerif-Regular'),('SerifB','LiberationSerif-Bold'),('SerifI','LiberationSerif-Italic')]:
    _try_register(n, f+'.ttf', _SANS_DIRS)

_pf_b=_try_register('PlayfairB','PlayfairDisplay-Bold.ttf',[FONTS_DIR])
_pf_i=_try_register('PlayfairI','PlayfairDisplay-Italic.ttf',[FONTS_DIR])
_pf_r=_try_register('PlayfairR','PlayfairDisplay-Regular.ttf',[FONTS_DIR])

_FT={'SerifR':'Times-Roman','SerifB':'Times-Bold','SerifI':'Times-Italic',
     'SansR':'Helvetica','SansB':'Helvetica-Bold','SansI':'Helvetica-Oblique',
     'PlayfairB':'Times-Bold','PlayfairI':'Times-Italic','PlayfairR':'Times-Roman'}
def F(name):
    return name if name in pdfmetrics.getRegisteredFontNames() else _FT.get(name,'Helvetica')
TITLE_FONT = 'PlayfairB' if _pf_b else 'SerifB'
SCI_FONT   = 'PlayfairI' if _pf_i else 'SerifI'
TEASER_FONT= 'PlayfairI' if _pf_i else 'SerifI'

# ─────────────────────────── logo ───────────────────────────
def find_color_logo():
    for pat in ('logopalmsolo400.png','*palmsolo*.png','*PSBP*logo*color*.png'):
        hits=glob.glob(os.path.join(REPO,'**',pat), recursive=True)
        if hits: return hits[0]
    return None

def green_logo_tmp():
    src=find_color_logo()
    if not src or not os.path.exists(src): return None
    try:
        import numpy as np
        im=Image.open(src).convert('RGBA'); a=np.array(im); alpha=a[:,:,3]
        g=np.zeros_like(a); g[:,:,0]=0x1a; g[:,:,1]=0x3a; g[:,:,2]=0x1f; g[:,:,3]=alpha
        t=tempfile.NamedTemporaryFile(suffix='.png', delete=False)
        Image.fromarray(g,'RGBA').save(t.name); t.close(); return t.name
    except Exception:
        return src

def resolve_logo():
    if os.path.exists(LOGO_GREEN):
        print("Logo:", LOGO_GREEN); return LOGO_GREEN
    hits=glob.glob(os.path.join(REPO,'**','psbp_logo_green.png'), recursive=True)
    if hits: print("Logo:", hits[0]); return hits[0]
    g=green_logo_tmp()
    if g: print("Logo: recolored from color logo at runtime"); return g
    print("!! Logo: no psbp_logo_green.png found, and no color logo to recolor.")
    return None

# ─────────────────────────── photos ───────────────────────────
def orig_url(u):
    return re.sub(r'/(square|small|medium|large|original)\.(jpe?g|png)', r'/original.\2', u or '')

def get_photo(rec, pid):
    fn=rec.get('filename') or (rec.get('photo_id','')+'.jpg')
    local=os.path.join(REPO,'photos',pid,fn)
    if os.path.exists(local):
        try:
            if Image.open(local).size[0]>=1000: return local,'local'
        except Exception: pass
    for url in [orig_url(rec.get('photo_url','')), rec.get('photo_url','')]:
        if not url: continue
        try:
            req=urllib.request.Request(url, headers={'User-Agent':'PSBP-sign-builder/2.0'})
            data=urllib.request.urlopen(req, timeout=30).read()
            t=tempfile.NamedTemporaryFile(suffix='.jpg', delete=False); t.write(data); t.close()
            return t.name, ('iNat-original' if 'original' in url else 'iNat-large')
        except Exception:
            continue
    if os.path.exists(local): return local,'local-small'
    return None,'MISSING'

def crop_focus(path, fx, fy):
    im=ImageOps.exif_transpose(Image.open(path)).convert('RGB')
    w,h=im.size; cur=w/h
    if cur>PHOTO_ASPECT:
        nw=int(h*PHOTO_ASPECT); cx=int(fx*w); l=max(0,min(cx-nw//2,w-nw)); im=im.crop((l,0,l+nw,h))
    else:
        nh=int(w/PHOTO_ASPECT); cy=int(fy*h); t=max(0,min(cy-nh//2,h-nh)); im=im.crop((0,t,w,t+nh))
    return im

def title(s): return ' '.join(w[:1].upper()+w[1:] for w in s.split())

# ─────────────────────────── text fitting ───────────────────────────
def wrap_lines(text, font, size, maxw, maxlines):
    """Greedy wrap. Returns (lines, everything_fit)."""
    words=(text or '').split(); lines=[]; cur=''
    for i,w in enumerate(words):
        t=(cur+' '+w).strip()
        if pdfmetrics.stringWidth(t,font,size)<=maxw:
            cur=t
        else:
            if cur: lines.append(cur)
            cur=w
            if len(lines)>=maxlines:
                return lines[:maxlines], False
    if cur:
        if len(lines)>=maxlines: return lines[:maxlines], False
        lines.append(cur)
    return lines, True

def fit_paragraph(text, font, maxw, maxlines, hi, lo):
    s=hi
    while s>=lo:
        lines, ok = wrap_lines(text, font, s, maxw, maxlines)
        if ok: return s, lines
        s-=0.25
    lines,_ = wrap_lines(text, font, lo, maxw, maxlines)
    if lines: lines[-1]=lines[-1].rstrip(' ,;:.—-')+'…'
    return lo, lines

def norm_license(lic):
    if not lic: return ''
    s=str(lic).strip().upper().replace('_','-').replace(' ','-')
    s=re.sub(r'^CC-?', 'CC ', s)
    return re.sub(r'\s+',' ',s).strip()

# ─────────────────────────── the sign ───────────────────────────
def _qr(c, url, x, y, size):
    qr=QrCodeWidget(url); qr.barLevel='M'; qr.barFillColor=black; qr.barStrokeColor=black
    b=qr.getBounds()
    d=Drawing(size,size,transform=[size/(b[2]-b[0]),0,0,size/(b[3]-b[1]),0,0]); d.add(qr)
    renderPDF.draw(d,c,x,y)
    try:    return qr.qr.getModuleCount()
    except Exception: return None

def _sign_body(c, common, sci, url, photo_path, focus, pname, lic, origin, teaser, logo_green):
    fx,fy=[float(v.strip().rstrip('%'))/100 for v in (focus or "50% 50%").split()]
    im=crop_focus(photo_path, fx, fy)
    tmp=tempfile.NamedTemporaryFile(suffix='.jpg', delete=False); im.save(tmp.name, quality=92); tmp.close()

    # ground + gold keyline
    c.setFillColor(CREAM); c.rect(0,0,SIGN_W,SIGN_H,fill=1,stroke=0)
    c.setStrokeColor(GOLD); c.setLineWidth(1.2); c.rect(B,B,SIGN_W-2*B,SIGN_H-2*B,fill=0,stroke=1)

    # ── hero photo (left column, top) ──
    PX,PW = COL_L0, COL_L1-COL_L0
    PY = SIGN_H - B - PHOTO_H
    c.drawImage(ImageReader(tmp.name), PX,PY,PW,PHOTO_H)
    # gradient fade into the name
    c.saveState(); steps=80; band=72.0; strip=band/steps
    for i in range(steps):
        a=0.96*((1-i/steps)**1.5)
        if i<3: a=1.0
        c.setFillColor(GREEN_DEEP); c.setFillAlpha(a); c.rect(PX,PY+i*strip,PW,strip+0.7,fill=1,stroke=0)
    c.setFillAlpha(1); c.restoreState()

    # ── scientific-name band + origin chip ──
    SB_Y = PY - SCI_BAND_H
    c.setFillColor(GREEN_DEEP); c.rect(COL_L0,SB_Y,PW,SCI_BAND_H,fill=1,stroke=0)

    chip_w=0
    if origin:
        native = origin.strip().lower().startswith('native')
        cs=8.0
        while cs>6.0 and pdfmetrics.stringWidth(origin.upper(),F('SansB'),cs)>140: cs-=0.25
        tw=pdfmetrics.stringWidth(origin.upper(),F('SansB'),cs)
        chip_w=tw+16; chip_h=15.0
        cx=COL_L1-10-chip_w; cy=SB_Y+(SCI_BAND_H-chip_h)/2
        c.setFillColor(NATIVE_BG if native else LGOLD)
        c.roundRect(cx,cy,chip_w,chip_h,3.5,fill=1,stroke=0)
        c.setFillColor(NATIVE_FG if native else GREEN_DEEP); c.setFont(F('SansB'),cs)
        c.drawString(cx+8, cy+4.6, origin.upper())
        chip_w+=14

    # common name over the fade — auto-shrink
    name_maxw = PW-24
    s=30.0
    while s>15 and pdfmetrics.stringWidth(common,F(TITLE_FONT),s)>name_maxw: s-=0.5
    c.setFillColor(white); c.setFont(F(TITLE_FONT),s); c.drawString(COL_L0+12, PY+4, common)
    # scientific name
    sci_maxw = PW-24-chip_w
    ss=15.0
    while ss>9.0 and pdfmetrics.stringWidth(sci,F(SCI_FONT),ss)>sci_maxw: ss-=0.25
    c.setFillColor(LGOLD); c.setFont(F(SCI_FONT),ss); c.drawString(COL_L0+12, SB_Y+8.0, sci)

    # ── teaser: the hook, under the photo ──
    T_TOP = SB_Y-8; T_BOT = B+FOOTER_H+6
    if teaser:
        tw_max = COL_L1-COL_L0-34
        tsize, tlines = fit_paragraph(teaser, F(TEASER_FONT), tw_max, 4, 12.5, 9.0)
        lead=tsize*1.22
        blockh=lead*len(tlines)
        ty=T_TOP-(T_TOP-T_BOT-blockh)/2.0-tsize*0.85
        c.setFillColor(GOLD)
        c.rect(COL_L0+12, ty-lead*(len(tlines)-1)-2, 2.0, blockh, fill=1, stroke=0)
        c.setFillColor(DARK); c.setFont(F(TEASER_FONT),tsize)
        for i,ln in enumerate(tlines):
            c.drawString(COL_L0+22, ty-i*lead, ln)

    # ── right column: QR, scan band, logo ──
    qx = COL_R0 + ((COL_R1-COL_R0)-QR_SIZE)/2.0
    qy = SIGN_H - B - 2 - QR_SIZE
    modules = _qr(c, url, qx, qy, QR_SIZE)

    band_y = qy-6-26
    c.setFillColor(GOLD); c.roundRect(COL_R0, band_y, COL_R1-COL_R0, 26, 4, fill=1, stroke=0)
    cta="SCAN FOR THE FULL STORY"
    cs=10.0
    while cs>7 and pdfmetrics.stringWidth(cta,F('SansB'),cs)>(COL_R1-COL_R0-16): cs-=0.25
    c.setFillColor(GREEN_DEEP); c.setFont(F('SansB'),cs)
    c.drawCentredString((COL_R0+COL_R1)/2.0, band_y+9, cta)

    if logo_green and os.path.exists(logo_green):
        lw=96.0; lh=lw/LOGO_ASPECT
        lx=(COL_R0+COL_R1)/2.0-lw/2
        ly=B+FOOTER_H+((band_y-(B+FOOTER_H))-lh)/2.0
        c.drawImage(ImageReader(logo_green), lx, ly, lw, lh, mask='auto', preserveAspectRatio=True)

    # ── footer: photographer NAME big, license tucked under it, smaller ──
    c.setFillColor(GREEN_DEEP); c.rect(B,B,SIGN_W-2*B,FOOTER_H,fill=1,stroke=0)
    name=pname or '\u2014'; PRE='Photo by '
    ns=15.0; x0=B+14; maxw=SIGN_W-2*B-28
    def w1(a):
        return pdfmetrics.stringWidth(PRE,F('SansR'),a-3.0)+pdfmetrics.stringWidth(name,F('SansB'),a)
    while ns>10.0 and w1(ns)>maxw: ns-=0.5
    ps=ns-3.0
    y1=B+FOOTER_H-17
    c.setFillColor(white); c.setFont(F('SansR'),ps); c.drawString(x0,y1,PRE)
    namex=x0+pdfmetrics.stringWidth(PRE,F('SansR'),ps)
    c.setFillColor(LGOLD); c.setFont(F('SansB'),ns); c.drawString(namex,y1,name)
    sub='  ·  '.join([p for p in [norm_license(lic),'via iNaturalist'] if p])
    if sub:
        s2=9.0
        while s2>6.5 and pdfmetrics.stringWidth(sub,F('SansR'),s2)>(SIGN_W-2*B-14-(namex-B)): s2-=0.25
        c.setFillColor(CREDIT2); c.setFont(F('SansR'),s2); c.drawString(namex, B+9, sub)

    os.unlink(tmp.name)
    return modules

# ─────────────────────────── imposition ───────────────────────────
def crop_marks(c, x, y, w, h, off=5, ln=14):
    c.saveState(); c.setStrokeColor(MARK); c.setLineWidth(0.4)
    for cx,dx in ((x,-1),(x+w,1)):
        for cy,dy in ((y,-1),(y+h,1)):
            c.line(cx+dx*off, cy, cx+dx*(off+ln), cy)
            c.line(cx, cy+dy*off, cx, cy+dy*(off+ln))
    c.restoreState()

def place(c, ox, oy, kw):
    c.saveState(); c.translate(ox,oy); m=_sign_body(c,**kw); c.restoreState()
    crop_marks(c, ox, oy, SIGN_W, SIGN_H)
    return m

# ─────────────────────────── copy resolution ───────────────────────────
def load_copy():
    p=os.path.join(REPO, COPY_FILE)
    if os.path.exists(p):
        try:
            print("Copy:", p)
            return json.load(open(p))
        except Exception as e:
            print(f"Copy: couldn't read {p} ({e.__class__.__name__}) — using drafts.")
    else:
        print(f"Copy: no {COPY_FILE} yet — drafting from the plant pages.")
    return {}

_ORIGIN_KEYS=['origin_short','sign_origin','nativity','native_status','origin_label']
_TEASER_KEYS=['sign_teaser','teaser','tagline','sign_tagline','hook']

def _strip_tags(s):
    return html.unescape(re.sub(r'<[^>]+>','',s)).strip()

RAW={}

def draft_from_page(pid):
    """Best-effort draft copy pulled from the published plant page."""
    origin=teaser=None
    hits=glob.glob(os.path.join(REPO,'plants',pid+'-*.html'))
    if not hits: return origin, teaser
    try: h=open(hits[0], encoding='utf-8').read()
    except Exception: return origin, teaser
    m=re.search(r'Origin</span>.*?<p>(.*?)</p>', h, re.S)
    if m:
        txt=_strip_tags(m.group(1))
        RAW.setdefault(pid,{})['_origin_paragraph']=txt
        n=re.search(r'native to (?:the )?([^,.;—(]+)', txt, re.I)
        if n:
            place=re.sub(r'\s+',' ',n.group(1)).strip()
            place=re.sub(r'^(biome|region|coast|coastal plain) of ','',place,flags=re.I)
            origin=('Native to '+place) if len(place)<=13 else place
    m=re.search(r'quick-hits-list">\s*<li>(.*?)</li>', h, re.S)
    if m:
        t=_strip_tags(m.group(1))
        RAW.setdefault(pid,{})['_first_quick_hit']=t
        t=re.split(r'(?<=[.!?])\s', t)[0]
        teaser=t
    return origin, teaser

def clamp(s, n):
    if not s: return s, False
    s=re.sub(r'\s+',' ',s).strip()
    if len(s)<=n: return s, False
    cut=s[:n].rsplit(' ',1)[0].rstrip(' ,;:.—-')
    return cut+'…', True

def resolve_copy(pid, species, overrides):
    """sign_copy.json wins, then plant_signage.json fields, then a page draft."""
    o=overrides.get(pid, {}) if isinstance(overrides, dict) else {}
    origin=o.get('origin'); teaser=o.get('teaser'); src='sign_copy.json'
    if not origin:
        for k in _ORIGIN_KEYS:
            if species.get(k): origin=str(species[k]); src='plant_signage.json'; break
    if not teaser:
        for k in _TEASER_KEYS:
            if species.get(k): teaser=str(species[k]); src='plant_signage.json'; break
        if not teaser:
            qh=species.get('quick_hits') or species.get('quick_facts')
            if isinstance(qh, list) and qh: teaser=str(qh[0]); src='plant_signage.json'
    if not origin or not teaser:
        do,dt=draft_from_page(pid)
        if not origin and do: origin=do; src='DRAFT (plant page)'
        if not teaser and dt: teaser=dt; src='DRAFT (plant page)'
    origin,c1=clamp(origin, ORIGIN_MAX)
    teaser,c2=clamp(teaser, TEASER_MAX)
    return origin, teaser, src, (c1 or c2)

# ─────────────────────────── main ───────────────────────────
def main():
    args=[a for a in sys.argv[1:]]
    each_twice = '--each-twice' in args
    if each_twice: args.remove('--each-twice')
    if args and args[0]=='--file':
        ids=[l.strip() for l in open(args[1]) if l.strip() and not l.startswith('#')]
    else:
        ids=[a.strip() for a in args if a.strip()]
    if not ids:
        print(__doc__); sys.exit(1)

    os.makedirs(OUT_DIR, exist_ok=True)
    sg=json.load(open(os.path.join(REPO,'data/sources/plant_signage.json')))
    pc=json.load(open(os.path.join(REPO,'data/sources/photo_credits.json')))
    species={s['id']:s for s in sg.get('species',sg)}
    heroes={}
    for p in pc.get('photos',pc):
        if p.get('hero') and p.get('psbp_id') not in heroes: heroes[p['psbp_id']]=p
    logo_green=resolve_logo()
    overrides=load_copy()

    built=[]; drafts={}
    for pid in ids:
        s=species.get(pid); h=heroes.get(pid)
        if not s: print(f"  SKIP {pid}: not in plant_signage.json"); continue
        if not h: print(f"  SKIP {pid}: no hero in photo_credits.json"); continue
        photo,psrc=get_photo(h,pid)
        if not photo: print(f"  SKIP {pid}: no photo available"); continue

        if URL_STYLE=='short':
            url=SHORT_BASE+re.sub(r'\D','',pid).lstrip('0')
        else:
            pages=glob.glob(os.path.join(REPO,'plants',pid+'-*.html'))
            if pages: url=SITE_BASE+os.path.basename(pages[0])
            else:     url=SITE_BASE+f"{pid}-"+re.sub(r'[^A-Za-z0-9]+','-',title(s['common_name'])).strip('-')+".html"

        origin,teaser,csrc,clamped=resolve_copy(pid,s,overrides)
        drafts[pid]=dict({'origin':origin or '','teaser':teaser or ''}, **RAW.get(pid,{}))
        kw=dict(common=title(s['common_name']), sci=s.get('botanical_name',''), url=url,
                photo_path=photo, focus=h.get('focus','50% 50%'),
                pname=h.get('photographer_name') or h.get('photographer',''),
                lic=h.get('license',''), origin=origin, teaser=teaser, logo_green=logo_green)

        one=canvas.Canvas(os.path.join(OUT_DIR,f"sign_{pid}_{re.sub(r'[^A-Za-z0-9]+','-',title(s['common_name']))}.pdf"),
                          pagesize=(SIGN_W,SIGN_H))
        mods=_sign_body(one,**kw); one.showPage(); one.save()
        built.append(dict(pid=pid, kw=kw, common=kw['common'], pname=kw['pname'],
                          psrc=psrc, mods=mods, url=url, csrc=csrc, clamped=clamped,
                          tmp_photo=photo if str(psrc).startswith('iNat') else None))
        if each_twice: built.append(built[-1])

    if not built:
        print("Nothing built."); sys.exit(1)

    sheet=canvas.Canvas(os.path.join(OUT_DIR,'PRINT_SHEETS_2up.pdf'), pagesize=(PAGE_W,PAGE_H))
    for i in range(0,len(built),2):
        pair=built[i:i+2]
        place(sheet, SIGN_X, SIGN_Y1, pair[0]['kw'])
        if len(pair)>1: place(sheet, SIGN_X, SIGN_Y0, pair[1]['kw'])
        sheet.setFillColor(MARK); sheet.setFont(F('SansR'),6.5)
        sheet.drawCentredString(PAGE_W/2, SIGN_Y0-16,
            "trim on the marks  ·  both signs into ONE letter pouch  ·  laminate  ·  cut between them")
        sheet.showPage()
    sheet.save()

    # Clean up downloaded photos now that both PDFs are done
    for r in built:
        if r.get('tmp_photo'):
            try: os.unlink(r['tmp_photo'])
            except Exception: pass

    with open(os.path.join(OUT_DIR,DRAFT_FILE),'w') as f:
        json.dump(drafts,f,indent=2,ensure_ascii=False)

    serif='Playfair Display' if _pf_b else 'FALLBACK serif (Playfair not installed)'
    sheets=(len(built)+1)//2
    print(f"\nBuilt {len(built)} signs ({SIGN_W/IN:.2f} x {SIGN_H/IN:.2f} in, {serif}) → {OUT_DIR}")
    print(f"   PRINT_SHEETS_2up.pdf   {sheets} portrait letter sheet(s), 2 signs each — PRINT THIS")
    print(f"   sign_<ID>_<Name>.pdf   single sign at trim size (proofs / reprints)")
    print(f"   {DRAFT_FILE}   ← edit this, save it to {COPY_FILE}, rerun")
    print(f"\n{'ID':12} {'COMMON':20} {'PHOTOGRAPHER':15} {'QR mod':7} {'COPY':22} PHOTO")
    seen=set()
    for r in built:
        if r['pid'] in seen: continue
        seen.add(r['pid'])
        mm=f"{(QR_SIZE/IN)/r['mods']:.3f}\"" if r['mods'] else "  ?  "
        flag='!' if r['clamped'] else ' '
        print(f"{r['pid']:12} {r['common'][:20]:20} {str(r['pname'])[:15]:15} {mm:7} {flag}{r['csrc'][:21]:21} {r['psrc']}")
    worst=min([(QR_SIZE/IN)/r['mods'] for r in built if r['mods']], default=0)
    print(f"\nQR module size (smallest): {worst:.3f}\"  — v1 full-page signs were 0.105\".")
    if worst < 0.095:
        print("  ↳ Shorten the URL (URL_STYLE='short') to get back to v1 scan distance.")
    print("! = copy was auto-trimmed to fit. DRAFT = auto-drafted from the plant page; have the director edit it.")
    print("\nSCAN-TEST one printed, laminated sign from 3 feet before running the batch.")

if __name__=='__main__':
    main()
