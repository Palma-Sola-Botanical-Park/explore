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
}
function openLb(i){ cur=i; paintLb(); lb.classList.add('on'); lb.setAttribute('aria-hidden','false');
  document.body.style.overflow='hidden'; }
function closeLb(){ lb.classList.remove('on'); lb.setAttribute('aria-hidden','true');
  document.body.style.overflow=''; cur=-1; }
/* WRAPS. Past the last photograph you land back on the first, and before the
   first you land on the last. Randy, 2026-09-21: "loop from the last gallery
   photo back to the 1/x photo if you keep clicking or swiping same direction".
   It previously dead-ended — stepLb returned early at either end and paintLb
   disabled the arrow you had just run out of road on. Outdoors, on a phone,
   that reads as the page having frozen rather than as having reached the end;
   the counter (3 / 6) is what tells you where you are, not a greyed arrow.
   Clicks, swipes and arrow keys all route through here, so they wrap together. */
function stepLb(d){ cur=(cur+d+PHOTOS.length)%PHOTOS.length; paintLb(); }

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

/* ── SPECIES MAP ──────────────────────────────────────────────────────
   The thumbnail in "Where to find it here" opens the full-screen park map.
   The page defines `window.SPMAP` (dots in map pixels, the opening frame,
   the metre projection and the fit for "Where am I?") only when the plant
   has placements, so everything here is guarded on it. Escape, the close
   button and the phone's back gesture all close it; the back gesture works
   because opening pushes a history entry and popstate closes. */
(function(){
  var M=window.SPMAP, open=document.getElementById('spmOpen'), panel=document.getElementById('spm');
  if(!M||!open||!panel) return;
  var svg=document.getElementById('spmSvg'), card=document.getElementById('spmCard'),
      whole=document.getElementById('spmWhole'), where=document.getElementById('spmWhere'),
      here=document.getElementById('spmHere');
  var isOpen=false, pushed=false, wide=false;
  function esc(t){return String(t==null?'':t).replace(/[&<>"']/g,function(c){return{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c];});}
  function frame(f){ svg.setAttribute('viewBox',f.join(' ')); }
  function describe(d){
    var s='<b>'+esc(d.area||'A planting here')+'</b>';
    if(d.cultivar) s+=' <span class=soft>&middot; planted as &lsquo;'+esc(d.cultivar)+'&rsquo;</span>';
    if(d.near&&d.near.length){
      s+='<br>Near it: '+d.near.map(function(n){return '<a href="'+esc(n.href)+'">'+esc(n.name)+'</a>';}).join(', ')+'.';
    }
    card.innerHTML=s;
  }
  function select(id){
    svg.querySelectorAll('.spm-dot').forEach(function(c){ c.classList.toggle('on',c.getAttribute('data-id')===id); });
    var d=M.dots.filter(function(x){return x.id===id;})[0]; if(d) describe(d);
  }
  svg.querySelectorAll('.spm-hit').forEach(function(c){
    c.addEventListener('click',function(){ select(c.getAttribute('data-id')); });
  });
  function show(){
    panel.classList.add('on'); panel.setAttribute('aria-hidden','false');
    document.body.style.overflow='hidden'; isOpen=true;
    wide=false; frame(M.frame); if(whole) whole.textContent='Whole park';
    if(M.dots.length===1) select(M.dots[0].id);
  }
  function hide(){
    panel.classList.remove('on'); panel.setAttribute('aria-hidden','true');
    document.body.style.overflow=''; isOpen=false;
  }
  open.addEventListener('click',function(){
    try{ history.pushState({spm:1},''); pushed=true; }catch(e){ pushed=false; }
    show();
  });
  function close(){ if(pushed){ pushed=false; history.back(); } else hide(); }
  /* Randy, 2026-10-30: two visitors in one day could not find the way out; a bare
     x in the top corner is the hardest spot to reach on a phone. The top x becomes
     a labelled pill, and a big button at the bottom, where the thumb is, goes back
     to the page. Added here so every published map page gets it without a rebuild. */
  var topX=document.getElementById('spmClose');
  topX.innerHTML='&#x2715; Close'; topX.classList.add('spm-close-pill');
  topX.addEventListener('click',close);
  var foot=panel.querySelector('.spm-foot');
  if(foot){
    var back=document.createElement('button');
    back.type='button'; back.className='spm-back'; back.innerHTML='&larr; Back to the page';
    back.addEventListener('click',close);
    foot.appendChild(back);
  }
  window.addEventListener('popstate',function(){ if(isOpen){ pushed=false; hide(); } });
  document.addEventListener('keydown',function(e){ if(isOpen&&e.key==='Escape') close(); });
  if(whole) whole.addEventListener('click',function(){
    wide=!wide; frame(wide?M.whole:M.frame); whole.textContent=wide?'Closer':'Whole park';
  });

  /* Where am I? A blue dot through the same fit as the plantings. The
     direction is the nearest named place on the map, not a compass bearing:
     "about 60 feet away, toward the Big Pond" is what a person can act on. */
  function toPx(lat,lng){
    var x=(lng-M.bounds.w)*M.klng, y=(M.bounds.n-lat)*M.klat;
    return {x:x,y:y,px:M.u[0]*x+M.u[1]*y+M.u[2],py:M.v[0]*x+M.v[1]*y+M.v[2]};
  }
  where.addEventListener('click',function(){
    if(!navigator.geolocation){ card.textContent='This phone cannot tell where you are.'; return; }
    card.textContent='Finding you…';
    navigator.geolocation.getCurrentPosition(function(pos){
      var la=pos.coords.latitude, ln=pos.coords.longitude, B=M.bounds, m=0.0012;
      if(la<B.s-m||la>B.n+m||ln<B.w-m||ln>B.e+m){ card.textContent='You are not in the park right now.'; return; }
      var p=toPx(la,ln), acc=Math.max(5,Math.min(40,pos.coords.accuracy||10)), sc=Math.hypot(M.u[0],M.v[0]);
      here.innerHTML='<circle class="spm-here-ring" cx="'+p.px+'" cy="'+p.py+'" r="'+(acc*sc)+'"/>'
                    +'<circle class="spm-here" cx="'+p.px+'" cy="'+p.py+'" r="14"/>';
      var best=null;
      M.dots.forEach(function(d){ var ft=Math.hypot(d.px-p.px,d.py-p.py)/sc*3.28084; if(!best||ft<best.ft) best={d:d,ft:ft}; });
      var s='You are the blue dot.';
      if(best){
        var ft=Math.round(best.ft/10)*10, mark=null;
        M.marks.forEach(function(k){ var f=Math.hypot(k.px-best.d.px,k.py-best.d.py); if(!mark||f<mark.f) mark={k:k,f:f}; });
        s+=ft<30?' The nearest planting is right here.':' The nearest planting is about '+ft+' feet away'+(mark?', toward '+esc(mark.k.name):'')+'.';
      }
      card.innerHTML=s;
      /* bring the blue dot into the frame if it is outside */
      var f=svg.getAttribute('viewBox').split(' ').map(Number);
      if(p.px<f[0]||p.px>f[0]+f[2]||p.py<f[1]||p.py>f[1]+f[3]){ wide=true; frame(M.whole); if(whole) whole.textContent='Closer'; }
    },function(){ card.textContent='Could not get your location.'; },{enableHighAccuracy:true,timeout:10000});
  });
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
