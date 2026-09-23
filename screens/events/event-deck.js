/* ============================================================================
   EVENT DECK — the rotation for screens/events/*.html
   ----------------------------------------------------------------------------
   Each page defines window.SLIDES = [ { id, html, seconds?, bg?, photo? } ].
   The first slide is the WELCOME — home base. The deck plays welcome, then
   one other slide picked at random (never the one just shown), then welcome
   again, and so on. That is the memorial deck's rhythm: the room always comes
   back to "you are here", and everything else is a visit.

     ?seconds=14   dwell for the other slides (welcome gets seconds × 1.4)
     ?speed=5      time warp — divides every dwell, for reviewing
     ?order=seq    play in order instead of random, for proofreading
     ?start=N      begin on slide N (proofing screenshots)

   No feeds, no network. A page that never fetches cannot go dark.
============================================================================ */
(function(){
  var Q = new URLSearchParams(location.search);
  var SPEED = Math.max(0.1, parseFloat(Q.get('speed')) || 1);
  var SECONDS = (parseFloat(Q.get('seconds')) || 14) / SPEED;
  var SEQ = Q.get('order') === 'seq';
  var slides = window.SLIDES || [];
  var deck = document.querySelector('.deck'), bar = document.getElementById('bar');

  slides.forEach(function(s, i){
    var el = document.createElement('div');
    el.className = 'slide'; el.id = 'slide-' + (s.id || i);
    var bg = '<div class="bg' + (s.bg ? ' photo' : '') + '"' + (s.bg ? ' style="background-image:url(' + s.bg + ')"' : '') + '></div>';
    el.innerHTML = bg + s.html;
    deck.appendChild(el); s.el = el;
  });

  var clock = document.getElementById('clock');
  function tick(){ var d = new Date(), h = d.getHours(), m = d.getMinutes();
    clock.textContent = ((h % 12) || 12) + ':' + (m < 10 ? '0' : '') + m + (h < 12 ? ' am' : ' pm'); }
  tick(); setInterval(tick, 15000);

  var cur = -1, last = -1, onWelcome = false, seqI = 0;
  function pick(){
    if(slides.length < 2) return 0;
    if(SEQ){ seqI = (seqI + 1) % slides.length; return seqI; }
    if(!onWelcome) return 0;                       // always come home
    var pool = []; for(var i = 1; i < slides.length; i++) if(i !== last) pool.push(i);
    return pool[Math.floor(Math.random() * pool.length)];
  }
  function show(i){
    if(cur > -1) slides[cur].el.classList.remove('on');
    slides[i].el.classList.add('on');
    if(i !== 0) last = i; onWelcome = (i === 0); cur = i;
    var dwell = (slides[i].seconds ? slides[i].seconds / SPEED : (i === 0 ? SECONDS * 1.4 : SECONDS));
    bar.classList.remove('run'); bar.style.width = '0';
    requestAnimationFrame(function(){ requestAnimationFrame(function(){
      bar.classList.add('run'); bar.style.transitionDuration = dwell + 's'; bar.style.width = '100%'; }); });
    setTimeout(function(){ show(pick()); }, dwell * 1000);
  }
  var START = Math.min(slides.length - 1, Math.max(0, parseInt(Q.get('start'), 10) || 0));
  if(slides.length) show(START);
  if(SPEED !== 1) console.log('event deck at ' + SPEED + '× — ' + SECONDS.toFixed(1) + 's per slide');
})();
