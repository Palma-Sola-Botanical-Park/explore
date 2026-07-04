#!/usr/bin/env python3
"""
make_signs.py — build full-page (8.5x11") photo-hero QR plant signs from PSBP IDs.

Run this ON YOUR MAC (it has network + your local repo). Each sign is:
  • hero photo up top, common name (Playfair Bold) + scientific name (Playfair
    Italic) exactly matching the web plant page
  • a big, balanced QR on the cream band, with the PSBP logo (recolored dark
    green) centered beside it
  • the photographer credit — big — along the bottom, because the contributors
    are the point

FONTS ARE AUTOMATIC. On the first run, if Playfair Display isn't already in
your repo's fonts/ folder, the script downloads it from Google Fonts and tucks
it there (then commit that folder once — it never downloads again). If the
download ever fails, the sign still builds in a fallback serif instead of
breaking, and tells you so.

USAGE
  python3 make_signs.py PSBP-00004 PSBP-00671 PSBP-00039 ...
  python3 make_signs.py --file signs.txt          # one ID per line
Requires: reportlab, Pillow (you already have both). Everything else is stdlib.
"""
import sys, os, re, json, glob, io, zipfile, urllib.request, tempfile
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
REPO      = os.path.expanduser("~/Documents/GitHub/explore")   # <-- your repo
OUT_DIR   = os.path.expanduser("~/Desktop/psbp_signs")
FONTS_DIR = os.path.join(REPO, "fonts")
LOGO_GREEN = os.path.join(REPO, "images", "psbp_logo_green.png")   # <-- put the file here
SITE_BASE = "https://palma-sola-botanical-park.github.io/explore/plants/"
# For the REAL rollout, migrate the domain first and change SITE_BASE to
# "https://palmasolabp.org/plants/" — QR codes are permanent once in the ground.
# ───────────────────────────────────────────────────────────────

GREEN_DEEP=HexColor('#1a3a1f'); GREEN_MID=HexColor('#2d6a35'); GOLD=HexColor('#c5922a')
LGOLD=HexColor('#e6c579'); CREAM=HexColor('#f8f6f0'); DARK=HexColor('#2b2b2b'); GRAY=HexColor('#666660')
CREDIT2=HexColor('#cdc9b6')
W,Hh=288,432; PHOTO_ASPECT=272/192.0            # native sign design box (4x6")
PAGE_W,PAGE_H=612,792                            # output: 8.5x11" letter
SCALE=min(PAGE_W/float(W), PAGE_H/float(Hh)); OFFX=(PAGE_W-W*SCALE)/2; OFFY=(PAGE_H-Hh*SCALE)/2
LOGO_ASPECT=1.778

# ─────────────── Playfair Display: auto-install + register ───────────────
def ensure_playfair():
    """If the Playfair TTFs aren't in fonts/, fetch them from Google Fonts once."""
    need={'PlayfairDisplay-Bold.ttf','PlayfairDisplay-Italic.ttf','PlayfairDisplay-Regular.ttf'}
    have=set(os.listdir(FONTS_DIR)) if os.path.isdir(FONTS_DIR) else set()
    if need<=have: return True
    os.makedirs(FONTS_DIR, exist_ok=True)
    print("Fonts: Playfair Display not found locally — downloading from Google Fonts (one time)…")
    try:
        url="https://fonts.google.com/download?family=Playfair%20Display"
        req=urllib.request.Request(url, headers={'User-Agent':'PSBP-sign-builder/1.0'})
        data=urllib.request.urlopen(req, timeout=60).read()
        z=zipfile.ZipFile(io.BytesIO(data))
        got=0
        for name in z.namelist():
            base=os.path.basename(name)
            if base in need and '/static/' in ('/'+name):
                with z.open(name) as f, open(os.path.join(FONTS_DIR,base),'wb') as out:
                    out.write(f.read()); got+=1
        if got<len(need):   # some zips omit the static/ folder; fall back to any match
            for name in z.namelist():
                base=os.path.basename(name)
                if base in need and not os.path.exists(os.path.join(FONTS_DIR,base)):
                    with z.open(name) as f, open(os.path.join(FONTS_DIR,base),'wb') as out:
                        out.write(f.read()); got+=1
        ok = {n for n in need if os.path.exists(os.path.join(FONTS_DIR,n))}
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

# Register Playfair (serif roles) + Liberation/Helvetica (sans roles)
def _try_register(name, filename, dirs):
    for d in dirs:
        fp=os.path.join(d, filename)
        if os.path.exists(fp):
            try: pdfmetrics.registerFont(TTFont(name, fp)); return True
            except: pass
    return False

_SANS_DIRS=['/usr/share/fonts/truetype/liberation/','/Library/Fonts/','/System/Library/Fonts/Supplemental/']
for n,f in [('SansR','LiberationSans-Regular'),('SansB','LiberationSans-Bold'),('SansI','LiberationSans-Italic'),
            ('SerifR','LiberationSerif-Regular'),('SerifB','LiberationSerif-Bold'),('SerifI','LiberationSerif-Italic')]:
    _try_register(n, f+'.ttf', _SANS_DIRS)

# Playfair for the title + scientific name (the whole point of this change)
_pf_b=_try_register('PlayfairB','PlayfairDisplay-Bold.ttf',[FONTS_DIR])
_pf_i=_try_register('PlayfairI','PlayfairDisplay-Italic.ttf',[FONTS_DIR])
_pf_r=_try_register('PlayfairR','PlayfairDisplay-Regular.ttf',[FONTS_DIR])

_FT={'SerifR':'Times-Roman','SerifB':'Times-Bold','SerifI':'Times-Italic',
     'SansR':'Helvetica','SansB':'Helvetica-Bold','SansI':'Helvetica-Oblique',
     'PlayfairB':'Times-Bold','PlayfairI':'Times-Italic','PlayfairR':'Times-Roman'}
def F(name):
    return name if name in pdfmetrics.getRegisteredFontNames() else _FT.get(name,'Helvetica')
# Serif roles used by the sign: Playfair if present, else Liberation serif, else Times
TITLE_FONT = 'PlayfairB' if _pf_b else 'SerifB'
SCI_FONT   = 'PlayfairI' if _pf_i else 'SerifI'

# ─────────────────────────── logo (dark green) ───────────────────────────
def find_color_logo():
    for pat in ('logopalmsolo400.png','*palmsolo*.png','*PSBP*logo*color*.png'):
        hits=glob.glob(os.path.join(REPO,'**',pat), recursive=True)
        if hits: return hits[0]
    return None

def resolve_logo():
    """Prefer the ready-made dark-green logo file (deterministic). If it isn't
    there, search the repo for it, then fall back to recoloring the color logo."""
    if os.path.exists(LOGO_GREEN):
        print("Logo:", LOGO_GREEN); return LOGO_GREEN
    hits=glob.glob(os.path.join(REPO,'**','psbp_logo_green.png'), recursive=True)
    if hits: print("Logo:", hits[0]); return hits[0]
    g=green_logo_tmp()
    if g: print("Logo: recolored from color logo at runtime"); return g
    print("!! Logo: no psbp_logo_green.png found, and no color logo to recolor.")
    print("   Drop the provided psbp_logo_green.png into:", os.path.dirname(LOGO_GREEN))
    return None

def green_logo_tmp():
    """Recolor the color logo to solid dark green (keeps its transparency)."""
    src=find_color_logo()
    if not src or not os.path.exists(src): return None
    try:
        import numpy as np
        im=Image.open(src).convert('RGBA'); a=np.array(im); alpha=a[:,:,3]
        g=np.zeros_like(a); g[:,:,0]=0x1a; g[:,:,1]=0x3a; g[:,:,2]=0x1f; g[:,:,3]=alpha
        t=tempfile.NamedTemporaryFile(suffix='.png', delete=False)
        Image.fromarray(g,'RGBA').save(t.name); t.close(); return t.name
    except Exception:
        return src   # numpy missing? fall back to the color logo

# ─────────────────────────── photo helpers ───────────────────────────
def orig_url(u):
    return re.sub(r'/(square|small|medium|large|original)\.(jpe?g|png)', r'/original.\2', u or '')

def get_photo(rec, pid):
    fn=rec.get('filename') or (rec.get('photo_id','')+'.jpg')
    local=os.path.join(REPO,'photos',pid,fn)
    if os.path.exists(local):
        try:
            if Image.open(local).size[0]>=1000: return local,'local'
        except: pass
    for url in [orig_url(rec.get('photo_url','')), rec.get('photo_url','')]:
        if not url: continue
        try:
            req=urllib.request.Request(url, headers={'User-Agent':'PSBP-sign-builder/1.0'})
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

# ─────────────────────────── the sign ───────────────────────────
def _qr(c, url, x, y, size):
    qr=QrCodeWidget(url); qr.barLevel='M'; qr.barFillColor=black; qr.barStrokeColor=black
    b=qr.getBounds()
    d=Drawing(size,size,transform=[size/(b[2]-b[0]),0,0,size/(b[3]-b[1]),0,0]); d.add(qr)
    renderPDF.draw(d,c,x,y)

def _sign_body(c, common, sci, url, photo_path, focus, pname, lic, logo_green):
    fx,fy=[float(v.strip().rstrip('%'))/100 for v in (focus or "50% 50%").split()]
    im=crop_focus(photo_path, fx, fy)
    tmp=tempfile.NamedTemporaryFile(suffix='.jpg', delete=False); im.save(tmp.name, quality=90); tmp.close()

    c.setFillColor(CREAM); c.rect(0,0,W,Hh,fill=1,stroke=0)
    c.setStrokeColor(GOLD); c.setLineWidth(1.2); c.rect(8,8,W-16,Hh-16,fill=0,stroke=1)

    # hero photo
    PX,PY,PW,PH = 8,232,272,192
    c.drawImage(ImageReader(tmp.name), PX,PY,PW,PH)
    # gradient fade -> name band
    c.saveState(); steps=80; strip=80.0/steps
    for i in range(steps):
        a=0.96*((1-i/steps)**1.5)
        if i<3: a=1.0
        c.setFillColor(GREEN_DEEP); c.setFillAlpha(a); c.rect(PX,PY+i*strip,PW,strip+0.7,fill=1,stroke=0)
    c.setFillAlpha(1); c.restoreState()
    c.setFillColor(GREEN_DEEP); c.rect(8,206,272,26,fill=1,stroke=0)   # sci band

    # common name (Playfair Bold) — lowered to hug the band, auto-shrink to fit
    s=23
    while s>12 and pdfmetrics.stringWidth(common,F(TITLE_FONT),s)>250: s-=1
    c.setFillColor(white); c.setFont(F(TITLE_FONT),s); c.drawString(20,232,common)
    # scientific name (Playfair Italic)
    c.setFillColor(LGOLD); c.setFont(F(SCI_FONT),13); c.drawString(20,213,sci)

    # ── cream band: balanced QR (left) + dark-green logo (centered, right) ──
    FH=38                                   # footer height (one-line credit)
    ztop, zbot = 206, 8+FH                  # cream zone 52..206
    M=4                                     # equal top/left/bottom margin
    QS=(ztop-zbot)-2*M; qrx=8+M; qry=zbot+M
    _qr(c, url, qrx, qry, QS)
    if logo_green and os.path.exists(logo_green):
        space_l=qrx+QS; space_r=W-8; lw=98; lh=lw/LOGO_ASPECT
        lx=space_l+(space_r-space_l-lw)/2; ly=(ztop+zbot)/2 - lh/2
        c.drawImage(ImageReader(logo_green), lx, ly, lw, lh, mask='auto', preserveAspectRatio=True)

    # ── footer: contributor credit — ONE LINE, left-justified, guaranteed to
    #    fit (never wraps, never overflows). The photographer NAME is never
    #    trimmed; for very long names we shrink to a readable floor, then drop
    #    the least-important tail (source wording, then license) as needed. ──
    c.setFillColor(GREEN_DEEP); c.rect(8,8,272,FH,fill=1,stroke=0)
    name=pname or '\u2014'; PRE='Photo by '; x0=20; maxw=252
    def _cw(suf,s):
        return (pdfmetrics.stringWidth(PRE,F('SansR'),s)
               +pdfmetrics.stringWidth(name,F('SansB'),s+0.5)
               +pdfmetrics.stringWidth(suf,F('SansR'),s))
    def _suf(parts):
        ps=[p for p in parts if p]
        return ('  \u00b7  '+'  \u00b7  '.join(ps)) if ps else ''
    levels=[_suf([lic,'via iNaturalist']), _suf([lic,'iNat']), _suf([lic]), _suf([])]
    chosen=None
    for suf in levels:                       # keep fullest tail that fits at a readable size
        s=11.5
        while s>9 and _cw(suf,s)>maxw: s-=0.5
        if _cw(suf,s)<=maxw: chosen=(s,suf); break
    if not chosen:                           # extreme name: name-only, shrink below floor
        s=9.0; suf=''
        while s>6 and _cw(suf,s)>maxw: s-=0.5
        chosen=(s,suf)
    ss,suffix=chosen
    yb=8+FH/2-ss*0.34; x=x0
    c.setFillColor(white);   c.setFont(F('SansR'),ss);     c.drawString(x,yb,PRE);  x+=pdfmetrics.stringWidth(PRE,F('SansR'),ss)
    c.setFillColor(LGOLD);   c.setFont(F('SansB'),ss+0.5); c.drawString(x,yb,name); x+=pdfmetrics.stringWidth(name,F('SansB'),ss+0.5)
    if suffix:
        c.setFillColor(CREDIT2); c.setFont(F('SansR'),ss); c.drawString(x,yb,suffix)

    os.unlink(tmp.name)

def draw_sign(c, **kw):
    """One sign, scaled + centered to fill the letter page."""
    c.saveState(); c.translate(OFFX,OFFY); c.scale(SCALE,SCALE)
    _sign_body(c, **kw)
    c.restoreState(); c.showPage()

# ─────────────────────────── main ───────────────────────────
def main():
    args=sys.argv[1:]
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

    combo=canvas.Canvas(os.path.join(OUT_DIR,'ALL_SIGNS_combined.pdf'), pagesize=(PAGE_W,PAGE_H))
    rows=[]
    for pid in ids:
        s=species.get(pid); h=heroes.get(pid)
        if not s: print(f"  SKIP {pid}: not in plant_signage.json"); continue
        if not h: print(f"  SKIP {pid}: no hero in photo_credits.json"); continue
        photo,src=get_photo(h,pid)
        if not photo: print(f"  SKIP {pid}: no photo available"); continue
        pages=glob.glob(os.path.join(REPO,'plants',pid+'-*.html'))
        if pages: url=SITE_BASE+os.path.basename(pages[0])
        else:     url=SITE_BASE+f"{pid}-"+re.sub(r'[^A-Za-z0-9]+','-',title(s['common_name'])).strip('-')+".html"
        common=title(s['common_name']); sci=s.get('botanical_name','')
        pname=h.get('photographer_name') or h.get('photographer','')
        lic=h.get('license','')
        kw=dict(common=common, sci=sci, url=url, photo_path=photo,
                focus=h.get('focus','50% 50%'), pname=pname, lic=lic, logo_green=logo_green)
        one=canvas.Canvas(os.path.join(OUT_DIR,f"sign_{pid}_{re.sub(r'[^A-Za-z0-9]+','-',common)}.pdf"),
                          pagesize=(PAGE_W,PAGE_H))
        draw_sign(one,**kw); one.save()
        draw_sign(combo,**kw)
        rows.append((pid,common,pname,src,os.path.basename(url)))
        if str(src).startswith('iNat'):   # only delete photos we downloaded, never local repo files
            try: os.unlink(photo)
            except: pass
    combo.save()
    serif = 'Playfair Display' if _pf_b else 'FALLBACK serif (Playfair not installed)'
    print(f"\nBuilt {len(rows)} signs (8.5x11 letter, {serif}) → {OUT_DIR}")
    print(f"   sign_<ID>_<Name>.pdf     (one file per sign — print + laminate these)")
    print(f"   ALL_SIGNS_combined.pdf   (all signs, one per page)")
    print(f"{'ID':12} {'COMMON':22} {'PHOTOGRAPHER':16} {'PHOTO':13} PAGE")
    for r in rows: print(f"{r[0]:12} {r[1][:22]:22} {str(r[2])[:16]:16} {r[3]:13} {r[4]}")
    print("\nSCAN-TEST one printed sign before doing the whole batch.")

if __name__=='__main__':
    main()
