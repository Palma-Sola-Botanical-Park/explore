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
W,Hh=288,432; PHOTO_ASPECT=272/156.0

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
        # de-dup keeping the broadest useful 1-2
        out=[]
        for r in found:
            if not any(r!=o and r in o for o in found): out.append(r)
        return " / ".join(dict.fromkeys(out)[:2]) if False else " / ".join(list(dict.fromkeys(out))[:2])
    m=re.split(r'[,.;(]', txt)
    return (m[0].replace('Native to','').strip()[:40] or "—") if m else "—"

def title(s): return ' '.join(w[:1].upper()+w[1:] for w in s.split())

def draw_sign(c, common, sci, family, family_common, origin, url, pid, photo_path, focus, credit_line, logo):
    fx,fy=[float(v.strip().rstrip('%'))/100 for v in (focus or "50% 50%").split()]
    im=crop_focus(photo_path, fx, fy)
    tmp=tempfile.NamedTemporaryFile(suffix='.jpg', delete=False); im.save(tmp.name, quality=88); tmp.close()
    c.setFillColor(CREAM); c.rect(0,0,W,Hh,fill=1,stroke=0)
    c.setStrokeColor(GOLD); c.setLineWidth(1.2); c.rect(8,8,W-16,Hh-16,fill=0,stroke=1)
    c.drawImage(ImageReader(tmp.name),8,268,272,156)
    c.setFillColor(GREEN_DEEP); c.rect(8,226,272,42,fill=1,stroke=0)
    s=19
    while s>10 and pdfmetrics.stringWidth(common,F('SerifB'),s)>258: s-=1
    c.setFillColor(white); c.setFont(F('SerifB'),s); c.drawCentredString(W/2,249,common)
    c.setFillColor(LGOLD); c.setFont(F('SerifI'),11.5); c.drawCentredString(W/2,233,sci)
    if credit_line:
        c.setFillColor(GOLD); c.rect(20,214,4,4,fill=1,stroke=0)
        c.setFillColor(GRAY); c.setFont(F('SansR'),7); c.drawString(28,213,credit_line)
    def fact(y,lab,val):
        lab=lab+'   '; wl=pdfmetrics.stringWidth(lab,F('SansB'),8.5); wv=pdfmetrics.stringWidth(val,F('SansR'),8.5)
        x=(W-(wl+wv))/2
        c.setFillColor(GREEN_MID); c.setFont(F('SansB'),8.5); c.drawString(x,y,lab)
        c.setFillColor(DARK); c.setFont(F('SansR'),8.5); c.drawString(x+wl,y,val)
    fam = f'{family} \u00b7 {family_common}' if family_common else family
    fact(197,'FAMILY',fam); fact(183,'ORIGIN',origin)
    c.setFillColor(GREEN_MID); c.setFont(F('SansI'),10); c.drawCentredString(W/2,166,'Scan to meet this plant  \u2192')
    c.setFillColor(white); c.setStrokeColor(HexColor('#e5e1d6')); c.setLineWidth(0.8)
    c.roundRect(88,46,112,112,6,fill=1,stroke=1)
    qr=QrCodeWidget(url); qr.barLevel='M'; qr.barFillColor=black; qr.barStrokeColor=black
    b=qr.getBounds(); S=100; d=Drawing(S,S,transform=[S/(b[2]-b[0]),0,0,S/(b[3]-b[1]),0,0]); d.add(qr)
    renderPDF.draw(d,c,94,52)
    c.setFillColor(GREEN_DEEP); c.rect(8,8,272,30,fill=1,stroke=0)
    if logo and os.path.exists(logo):
        c.drawImage(ImageReader(logo),16,12,36,22,mask='auto',preserveAspectRatio=True)
    c.setFillColor(white); c.setFont(F('SansB'),8); c.drawCentredString(W/2,20,'palmasolabp.org')
    c.setFillColor(LGOLD); c.setFont(F('SansR'),6); c.drawRightString(272,19,pid)
    c.showPage(); os.unlink(tmp.name)

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
        fam=(s.get('taxonomy') or {}).get('family','')
        origin=short_origin(s.get('origin'), pid)
        pname=h.get('photographer_name') or h.get('photographer','')
        lic=h.get('license',''); 
        credit=f"Photo by {pname}" + (f" \u00b7 {lic}" if lic else "") + " \u00b7 via iNaturalist"
        kw=dict(common=common, sci=sci, family=fam, family_common=FAMILY_COMMON.get(fam,''),
                origin=origin, url=url, pid=pid, photo_path=photo, focus=h.get('focus','50% 50%'),
                credit_line=credit, logo=logo)
        # individual PDF
        one=canvas.Canvas(os.path.join(OUT_DIR,f"sign_{pid}_{re.sub(r'[^A-Za-z0-9]+','-',common)}.pdf"), pagesize=(W,Hh))
        draw_sign(one,**kw); one.save()
        # combined page
        draw_sign(combo,**kw)
        rows.append((pid,common,fam,origin,src,os.path.basename(url)))
        if photo.startswith('/tmp') or photo.startswith(tempfile.gettempdir()):
            try: os.unlink(photo)
            except: pass
    combo.save()
    print(f"\nBuilt {len(rows)} signs → {OUT_DIR}")
    print(f"{'ID':12} {'COMMON':22} {'FAMILY':14} {'ORIGIN':18} {'PHOTO':13} PAGE")
    for r in rows: print(f"{r[0]:12} {r[1][:22]:22} {r[2][:14]:14} {r[3][:18]:18} {r[4]:13} {r[5]}")
    print("\nReview the ORIGIN + PHOTO columns. Fix any origin via ORIGIN_OVERRIDES and re-run.")
    print("SCAN-TEST one printed sign before doing the whole batch.")

if __name__=='__main__':
    main()
