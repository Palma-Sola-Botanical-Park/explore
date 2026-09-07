/* ══ SPECIES PAGE v2 — behaviour ══════════════════════════════════════════
   Extracted 2026-09-07 from the hand-built Brown Anole page. Shared by every
   generated species page; the only per-species part is `window.PHOTOS`, which
   the page defines before loading this file.

   Lightbox notes: swipe is the primary control (same 60px / horizontal-
   dominance test as the nature.html drawer, so a visitor learns one gesture),
   controls sit at the bottom for one-handed reach in bright sun, and the panel
   is z-index 1000 because #site-nav is 900 and would otherwise crop the image
   and hide the close button.
   ══════════════════════════════════════════════════════════════════════════ */
(function () {
  var PHOTOS = window.PHOTOS || [];


/* ── GALLERY LIGHTBOX ─────────────────────────────────────────────────
   Swipe is the primary control. The threshold (60px horizontal, and at least
   1.6x the vertical movement) is lifted from the nature.html drawer so the two
   feel identical — a visitor should not have to learn two swipes. */
var lb=document.getElementById('lb'), lbImg=document.getElementById('lbImg'),
    lbName=document.getElementById('lbName'), lbMeta=document.getElementById('lbMeta'),
    lbCount=document.getElementById('lbCount'), lbPrev=document.getElementById('lbPrev'),
    lbNext=document.getElementById('lbNext'), lbStage=document.getElementById('lbStage');
var cur=-1;

function paintLb(){
  var p=PHOTOS[cur]; if(!p) return;
  lbImg.src=p.src; lbImg.alt=p.alt;
  lbName.textContent=p.by;
  lbMeta.innerHTML = p.date + ' &middot; CC-BY-NC, via iNaturalist';
  lbCount.textContent=(cur+1)+' / '+PHOTOS.length;
  lbPrev.disabled = cur===0; lbNext.disabled = cur===PHOTOS.length-1;
}
function openLb(i){ cur=i; paintLb(); lb.classList.add('on'); lb.setAttribute('aria-hidden','false');
  document.body.style.overflow='hidden'; }
function closeLb(){ lb.classList.remove('on'); lb.setAttribute('aria-hidden','true');
  document.body.style.overflow=''; cur=-1; }
function stepLb(d){ var j=cur+d; if(j<0||j>=PHOTOS.length) return; cur=j; paintLb(); }

/* Apply the stored focus point to every photograph that has one. In the real
   publisher this is written server-side from photo_credits.json; done here at
   runtime so the demo reads the same field rather than a hardcoded value.
   Photos with no focus fall back to centre, which is what five of these six
   currently do — worth setting for tall portraits, where centre-cropping a
   4:5 thumbnail can lose the animal entirely. */
document.querySelectorAll('img[data-focus]').forEach(function(im){
  im.style.objectPosition = im.dataset.focus;
});

var heroGal=document.getElementById('heroGal');
if(heroGal) heroGal.addEventListener('click',function(){ openLb(0); });
document.querySelectorAll('#heroStrip button').forEach(function(b){
  b.addEventListener('click',function(){ openLb(+b.dataset.i); });
});

document.querySelectorAll('#gal figure').forEach(function(f){
  f.querySelector('.shot').addEventListener('click',function(){ openLb(+f.dataset.i); });
});
lbPrev.addEventListener('click',function(){stepLb(-1);});
lbNext.addEventListener('click',function(){stepLb(1);});
document.getElementById('lbClose').addEventListener('click',closeLb);
/* Tapping the dark area around the photograph closes it — the first thing most
   people try. Guarded on e.target so a tap ON the image does not close, and so
   a swipe that ends on the backdrop is not read as a click. */
lbStage.addEventListener('click',function(e){ if(e.target===lbStage) closeLb(); });
document.addEventListener('keydown',function(e){
  if(!lb.classList.contains('on')) return;
  if(e.key==='Escape') closeLb();
  else if(e.key==='ArrowRight') stepLb(1);
  else if(e.key==='ArrowLeft')  stepLb(-1);
});

/* Swipe — the control that matters outdoors. */
(function(){
  var x0=null,y0=null;
  lbStage.addEventListener('touchstart',function(e){
    var t=e.touches[0]; x0=t.clientX; y0=t.clientY;
  },{passive:true});
  lbStage.addEventListener('touchend',function(e){
    if(x0==null) return;
    var t=e.changedTouches[0], dx=t.clientX-x0, dy=t.clientY-y0;
    x0=null;
    if(Math.abs(dx)>60 && Math.abs(dx)>Math.abs(dy)*1.6) stepLb(dx<0?1:-1);
  },{passive:true});
})();

/* Rail highlight — marks the section you are actually reading. */
var links={};
document.querySelectorAll('.sp-rail a').forEach(function(a){
  links[a.getAttribute('href').slice(1)] = a;
});
var io = new IntersectionObserver(function(entries){
  entries.forEach(function(en){
    if (!en.isIntersecting) return;
    Object.keys(links).forEach(function(k){ links[k].classList.remove('on'); });
    var a = links[en.target.id];
    if (a) a.classList.add('on');
  });
}, { rootMargin: '-10% 0px -70% 0px' });
document.querySelectorAll('.sp-sec').forEach(function(s){ io.observe(s); });
})();
