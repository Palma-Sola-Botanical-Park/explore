#!/usr/bin/env python3
"""
make_signs.py — build 4x6" photo-hero QR plant signs from PSBP IDs.

Run this ON YOUR MAC (it has network + your local repo). It:
  1. reads data/sources/plant_signage.json + photo_credits.json
  2. gets the hero photo LOCAL-FIRST (your ~1600px heroes are print-sharp);
     falls back to the iNaturalist ORIGINAL (derived by size-token swap) only
     if the local file is missing or small
  3. focus-crops the photo so the subject is never chopped
  4. finds the REAL live page filename from your plants/ folder (so the QR
     can't 404), builds the sign, writes one PDF per sign + a combined PDF

USAGE
  python3 make_signs.py PSBP-00004 PSBP-00671 PSBP-00039 ...
  python3 make_signs.py --file signs.txt          # one ID per line
Requires: reportlab, Pillow (you already have both). Everything else is stdlib.

If any ORIGIN reads badly on a sign, add an override in ORIGIN_OVERRIDES below
and re-run — that's the only field that isn't always clean to auto-shorten.
"""
import sys, os, re, json, glob, urllib.request, tempfile
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
SITE_BASE = "https://palma-sola-botanical-park.github.io/explore/plants/"
# For the REAL 76-sign rollout, migrate the domain first and change SITE_BASE to
# "https://palmasolabp.org/plants/" — QR codes are permanent once in the ground.

ORIGIN_OVERRIDES = {          # PSBP-id -> short origin string, only if auto looks off
    # "PSBP-00004": "South America",
}
FAMILY_COMMON = {             # a few common plant-family names; extend as you like
    "Malvaceae":"Mallow family","Solanaceae":"Nightshade family","Fabaceae":"Legume family",
    "Arecaceae":"Palm family","Moraceae":"Fig / Mulberry family","Myrtaceae":"Myrtle family",
    "Rubiaceae":"Coffee family","Bignoniaceae":"Trumpet-vine family","Apocynaceae":"Dogbane family",
    "Euphorbiaceae":"Spurge family","Lamiaceae":"Mint family","Asteraceae":"Daisy family",
    "Amaryllidaceae":"Amaryllis family","Verbenaceae":"Verbena family","Acanthaceae":"Acanthus family",
    "Cactaceae":"Cactus family","Anacardiaceae":"Cashew family","Lauraceae":"Laurel family",
    "Rutaceae":"Citrus family","Sapindaceae":"Soapberry family","Annonaceae":"Custard-apple family",
}
# ───────────────────────────────────────────────────────────────

GREEN_DEEP=HexColor('#1a3a1f'); GREEN_MID=HexColor('#2d6a35'); GOLD=HexColor('#c5922a')
LGOLD=HexColor('#e6c579'); CREAM=HexColor('#f8f6f0'); DARK=HexColor('#2b2b2b'); GRAY=HexColor('#666660')
W,Hh=288,432; PHOTO_ASPECT=272/192.0

for n,f in [('SerifR','LiberationSerif-Regular'),('SerifB','LiberationSerif-Bold'),
            ('SerifI','LiberationSerif-Italic'),('SansR','LiberationSans-Regular'),
            ('SansB','LiberationSans-Bold'),('SansI','LiberationSans-Italic')]:
    for p in ('/usr/share/fonts/truetype/liberation/','/Library/Fonts/','/System/Library/Fonts/Supplemental/'):
        fp=p+f+'.ttf'
        if os.path.exists(fp):
            try: pdfmetrics.registerFont(TTFont(n,fp)); break
            except: pass
# Fallbacks if Liberation isn't on the Mac: map to built-ins
_FT={'SerifR':'Times-Roman','SerifB':'Times-Bold','SerifI':'Times-Italic',
     'SansR':'Helvetica','SansB':'Helvetica-Bold','SansI':'Helvetica-Oblique'}
def F(name):
    return name if name in pdfmetrics.getRegisteredFontNames() else _FT[name]

def find_logo():
    for c in [REPO+"/images/white_PSBP_logo.png", REPO+"/images/logo/white_PSBP_logo.png"]:
        if os.path.exists(c): return c
    hits=glob.glob(REPO+"/**/white_PSBP_logo.png", recursive=True)
    return hits[0] if hits else None

def orig_url(u):   # iNat CDN size-token -> original
    return re.sub(r'/(square|small|medium|large|original)\.(jpe?g|png)', r'/original.\2', u or '')

def get_photo(rec, pid):
    """Local hero first (print-sharp); else iNat original; else stored large."""
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
        except Exception as e:
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

def short_origin(origin_field, pid):
    if pid in ORIGIN_OVERRIDES: return ORIGIN_OVERRIDES[pid]
    txt=' '.join(origin_field) if isinstance(origin_field,list) else str(origin_field or '')
    regions=["South America","Central America","North America","Southeast Asia","East Asia",
             "West Africa","East Africa","Mexico","Caribbean","Madagascar","Australia","Africa",
             "China","India","Brazil","Florida","Asia","Europe","Pacific"]
    found=[r for r in regions if r.lower() in txt.lower()]
    if found:
        out=[]
        for r in found:
            if not any(r!=o and r in o for o in found): out.append(r)
        return " / ".join(list(dict.fromkeys(out))[:2])
    m=re.split(r'[,.;(]', txt)
    return (m[0].replace('Native to','').strip()[:40] or "—") if m else "—"

def get_family(s):
    """Family field name varies; check the likely spots, first non-empty wins."""
    tax = s.get('taxonomy') or {}
    for v in (tax.get('family'), tax.get('Family'),
              s.get('family'), s.get('plant_family'), s.get('familyName')):
        if v: return v
    return ''

def title(s): return ' '.join(w[:1].upper()+w[1:] for w in s.split())

def wrap_lines(text, font, size, maxw):
    words=str(text if text else '\u2014').split(); lines=[]; cur=''
    for w in words:
        t=(cur+' '+w).strip()
        if pdfmetrics.stringWidth(t,font,size)<=maxw or not cur: cur=t
        else: lines.append(cur); cur=w
    if cur: lines.append(cur)
    return lines or ['\u2014']

def smart_field_lines(value, font, size, maxw):
    """One line if it fits; else break at logical separators (\u00b7 or /),
    keeping each element whole. Word-wrap only as a last resort per element."""
    value=str(value if value else '\u2014')
    if pdfmetrics.stringWidth(value,font,size)<=maxw:
        return [value]
    parts=[p for p in re.split(r'\s*[\u00b7/]\s*', value) if p]
    lines=[]
    for p in parts:
        if pdfmetrics.stringWidth(p,font,size)<=maxw: lines.append(p)
        else: lines.extend(wrap_lines(p,font,size,maxw))
    return lines or ['\u2014']

def _sign_body(c, common, sci, family, family_common, origin, url, pid, photo_path, focus, pname, lic, logo):
    fx,fy=[float(v.strip().rstrip('%'))/100 for v in (focus or "50% 50%").split()]
    im=crop_focus(photo_path, fx, fy)
    tmp=tempfile.NamedTemporaryFile(suffix='.jpg', delete=False); im.save(tmp.name, quality=90); tmp.close()

    # background + frame
    c.setFillColor(CREAM); c.rect(0,0,W,Hh,fill=1,stroke=0)
    c.setStrokeColor(GOLD); c.setLineWidth(1.2); c.rect(8,8,W-16,Hh-16,fill=0,stroke=1)

    # hero photo (top)
    PX,PY,PW,PH = 8,232,272,192
    c.drawImage(ImageReader(tmp.name), PX,PY,PW,PH)

    # gradient fade at photo bottom -> blends seamlessly into the name band
    c.saveState()
    steps=80; fade=80.0; strip=fade/steps
    for i in range(steps):
        a=0.96*((1 - i/steps)**1.5)
        if i<3: a=1.0
        c.setFillColor(GREEN_DEEP); c.setFillAlpha(a)
        c.rect(PX, PY + i*strip, PW, strip+0.7, fill=1, stroke=0)
    c.setFillAlpha(1); c.restoreState()

    # sci-name band (same deep green -> continuous with the fade above it)
    c.setFillColor(GREEN_DEEP); c.rect(8,206,272,26,fill=1,stroke=0)

    # title, left-justified over the darkened photo, bigger
    s=23
    while s>12 and pdfmetrics.stringWidth(common,F('SerifB'),s)>250: s-=1
    c.setFillColor(white); c.setFont(F('SerifB'),s); c.drawString(20, 240, common)
    # scientific name in the band
    c.setFillColor(LGOLD); c.setFont(F('SerifI'),13); c.drawString(20, 213, sci)

    # ── photographer credit — enlarged + highlighted ──
    cy=190
    plain=(f'Photo by {pname} \u00b7 {lic} \u00b7 via iNaturalist' if lic
           else f'Photo by {pname} \u00b7 via iNaturalist')
    cs=9.5
    while cs>7 and pdfmetrics.stringWidth(plain,F('SansR'),cs)>244: cs-=0.5
    c.setFillColor(GOLD); c.rect(20,cy+0.5,5.5,5.5,fill=1,stroke=0)
    x=31
    c.setFillColor(GRAY);       c.setFont(F('SansR'),cs); c.drawString(x,cy,'Photo by '); x+=pdfmetrics.stringWidth('Photo by ',F('SansR'),cs)
    c.setFillColor(GREEN_DEEP); c.setFont(F('SansB'),cs); c.drawString(x,cy,pname);       x+=pdfmetrics.stringWidth(pname,F('SansB'),cs)
    if lic:
        c.setFillColor(GRAY); c.setFont(F('SansR'),cs); seg=' \u00b7 '; c.drawString(x,cy,seg); x+=pdfmetrics.stringWidth(seg,F('SansR'),cs)
        c.setFillColor(GOLD); c.setFont(F('SansB'),cs); c.drawString(x,cy,lic);              x+=pdfmetrics.stringWidth(lic,F('SansB'),cs)
    c.setFillColor(GRAY); c.setFont(F('SansR'),cs); c.drawString(x,cy,' \u00b7 via iNaturalist')

    # ── lower zone: FAMILY / ORIGIN (left) + bigger QR (right), no card ──
    qr=QrCodeWidget(url); qr.barLevel='M'; qr.barFillColor=black; qr.barStrokeColor=black
    b=qr.getBounds(); QS=126; qrx,qry=150,46
    d=Drawing(QS,QS,transform=[QS/(b[2]-b[0]),0,0,QS/(b[3]-b[1]),0,0]); d.add(qr)
    renderPDF.draw(d,c, qrx, qry)

    colx=20; colw=118; y=[166]
    def field(label,value):
        c.setFillColor(GREEN_MID); c.setFont(F('SansB'),8.5); c.drawString(colx,y[0],label); y[0]-=13
        c.setFillColor(DARK); c.setFont(F('SansR'),9)
        for ln in smart_field_lines(value,F('SansR'),9,colw):
            c.drawString(colx,y[0],ln); y[0]-=11
        y[0]-=7
    fam_disp=(f'{family} \u00b7 {family_common}' if family_common else family) or '\u2014'
    field('FAMILY', fam_disp)
    field('ORIGIN', origin)
    c.setFillColor(GREEN_MID); c.setFont(F('SansI'),9.5)
    c.drawString(colx, 62, 'Scan to meet this plant  \u2192')

    # footer (no PSBP-id)
    c.setFillColor(GREEN_DEEP); c.rect(8,8,272,30,fill=1,stroke=0)
    if logo and os.path.exists(logo):
        c.drawImage(ImageReader(logo),16,12,40,22,mask='auto',preserveAspectRatio=True)
    c.setFillColor(white); c.setFont(F('SansB'),9); c.drawCentredString(W/2,19,'palmasolabp.org')

    os.unlink(tmp.name)

def draw_sign(c, **kw):
    """One sign that fills its own 288x432 page."""
    _sign_body(c, **kw); c.showPage()

def place_sign(c, ox, oy, **kw):
    """Draw one sign with its bottom-left corner at (ox,oy) — for imposition."""
    c.saveState(); c.translate(ox,oy); _sign_body(c, **kw); c.restoreState()

def draw_crop_marks(c, sx, sy, w, h, off=5, ln=11):
    """Light corner ticks just outside the trim box, so UPS knows where to cut."""
    c.saveState(); c.setStrokeColor(HexColor('#9a9a9a')); c.setLineWidth(0.4)
    for cx,cy,dx,dy in [(sx,sy,-1,-1),(sx+w,sy,1,-1),(sx,sy+h,-1,1),(sx+w,sy+h,1,1)]:
        c.line(cx+dx*off, cy, cx+dx*(off+ln), cy)
        c.line(cx, cy+dy*off, cx, cy+dy*(off+ln))
    c.restoreState()

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
    logo=find_logo()
    if not logo: print("!! logo not found under",REPO,"— signs will build without it")

    combo=canvas.Canvas(os.path.join(OUT_DIR,'ALL_SIGNS_combined.pdf'), pagesize=(W,Hh))
    # 2-up print sheets: two portrait signs side by side on Letter landscape
    SW,SH = 792,612                 # 11 x 8.5 in
    GAP   = 36                      # 0.5 in between the two signs
    SLOT_X = [(SW-2*W-GAP)/2, (SW-2*W-GAP)/2 + W + GAP]   # left, right x
    SLOT_Y = (SH-Hh)/2                                     # vertically centered
    sheet=canvas.Canvas(os.path.join(OUT_DIR,'PRINT_2up_landscape.pdf'), pagesize=(SW,SH))
    placed=0
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
        fam=get_family(s)
        if not fam:
            print(f"  !! {pid}: FAMILY blank — record keys: {list(s.keys())}")
            tax=s.get('taxonomy')
            if isinstance(tax,dict): print(f"       taxonomy keys: {list(tax.keys())}")
        origin=short_origin(s.get('origin'), pid)
        pname=h.get('photographer_name') or h.get('photographer','')
        lic=h.get('license','')
        kw=dict(common=common, sci=sci, family=fam, family_common=FAMILY_COMMON.get(fam,''),
                origin=origin, url=url, pid=pid, photo_path=photo, focus=h.get('focus','50% 50%'),
                pname=pname, lic=lic, logo=logo)
        # individual PDF
        one=canvas.Canvas(os.path.join(OUT_DIR,f"sign_{pid}_{re.sub(r'[^A-Za-z0-9]+','-',common)}.pdf"), pagesize=(W,Hh))
        draw_sign(one,**kw); one.save()
        # combined page
        draw_sign(combo,**kw)
        # 2-up print sheet
        slot=placed%2
        place_sign(sheet, SLOT_X[slot], SLOT_Y, **kw)
        draw_crop_marks(sheet, SLOT_X[slot], SLOT_Y, W, Hh)
        if slot==1: sheet.showPage()
        placed+=1
        rows.append((pid,common,fam,origin,src,os.path.basename(url)))
        if photo.startswith('/tmp') or photo.startswith(tempfile.gettempdir()):
            try: os.unlink(photo)
            except: pass
    combo.save()
    if placed%2==1: sheet.showPage()   # flush a half-filled last sheet
    sheet.save()
    print(f"\nBuilt {len(rows)} signs → {OUT_DIR}")
    print(f"   ALL_SIGNS_combined.pdf   (one sign per page)")
    print(f"   PRINT_2up_landscape.pdf  (two per landscape page — send this to UPS)")
    print(f"{'ID':12} {'COMMON':22} {'FAMILY':14} {'ORIGIN':18} {'PHOTO':13} PAGE")
    for r in rows: print(f"{r[0]:12} {r[1][:22]:22} {r[2][:14]:14} {r[3][:18]:18} {r[4]:13} {r[5]}")
    print("\nReview the ORIGIN + PHOTO + FAMILY columns. Fix any origin via ORIGIN_OVERRIDES and re-run.")
    print("SCAN-TEST one printed sign before doing the whole batch.")

if __name__=='__main__':
    main()
