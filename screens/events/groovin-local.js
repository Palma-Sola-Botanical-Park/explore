/* INTERNET OUT Groovin: a separate local-file player. No network detection or controller writes. */
(function(global){
  'use strict';
  var D=global.GROOVIN_LOCAL_DATA;
  function shuffle(rows,rng){
    var out=rows.slice();rng=rng||Math.random;
    for(var i=out.length-1;i>0;i--){var j=Math.floor(rng()*(i+1)),v=out[i];out[i]=out[j];out[j]=v;}
    return out;
  }
  function Scheduler(data,rng){this.data=data;this.rng=rng||Math.random;this.round=0;this.lastNature='';}
  Scheduler.prototype.cycle=function(){
    var self=this,photos=shuffle(this.data.nature,this.rng);
    if(photos.length>1&&photos[0]===this.lastNature){var t=photos[0];photos[0]=photos[1];photos[1]=t;}
    this.lastNature=photos[photos.length-1];
    var names=['pops','adult-volunteers','student-crew'],galleries=[];
    for(var i=0;i<10;i++)galleries.push({type:'gallery',kind:names[(i+this.round)%3],seconds:36});
    galleries=shuffle(galleries,this.rng);
    var mixed=[],g=0;
    photos.forEach(function(id,i){
      mixed.push({type:'photo',id:id,seconds:30});
      if(g<galleries.length&&(i+1)>=Math.ceil((g+1)*photos.length/galleries.length))mixed.push(galleries[g++]);
    });
    var chunks=[],offset=0;
    for(i=0;i<6;i++){
      chunks.push({type:i%3===2?'partnership':'welcome',seconds:20});
      var end=Math.floor((i+1)*mixed.length/6);chunks=chunks.concat(mixed.slice(offset,end));offset=end;
    }
    this.round++;
    return chunks;
  };
  function GalleryPool(ids,rng){this.ids=ids.slice();this.rng=rng||Math.random;this.counts={};this.lastSlots={};this.lastLayout=-1;}
  GalleryPool.prototype.take=function(n,excluded){
    var held=new Set(excluded||[]),out=[];
    while(out.length<n){
      var options=this.ids.filter(function(id){return !held.has(id);});if(!options.length)break;
      var counts=this.counts,least=Math.min.apply(null,options.map(function(id){return counts[id]||0;}));
      options=options.filter(function(id){return (counts[id]||0)===least;});
      var id=options[Math.floor(this.rng()*options.length)];
      this.counts[id]=(this.counts[id]||0)+1;held.add(id);out.push(id);
    }
    return out;
  };
  GalleryPool.prototype.positions=function(ids){
    var self=this,out=shuffle(ids,this.rng);
    // Resolve fixed slots by swaps rather than relying on a lucky shuffle.
    for(var i=0;i<out.length;i++){
      if(this.lastSlots[out[i]]!==i)continue;
      for(var j=0;j<out.length;j++){
        if(i!==j&&this.lastSlots[out[j]]!==i&&this.lastSlots[out[i]]!==j){var t=out[i];out[i]=out[j];out[j]=t;break;}
      }
    }
    out.forEach(function(id,index){self.lastSlots[id]=index;});return out;
  };
  GalleryPool.prototype.plan=function(kind){
    var count=Math.min(kind==='pops'?5:6,this.ids.length),used=[],initial=this.take(count,used);used=used.concat(initial);
    var n=kind==='pops'?count:2,one=this.take(n,used);used=used.concat(one);
    var two=this.take(n,used);used=used.concat(two);
    var previousLayout=this.lastLayout,variants=[0,1].filter(function(x){return x!==previousLayout;});this.lastLayout=variants[Math.floor(this.rng()*variants.length)];
    return {initial:this.positions(initial),batches:[one,two],layout:this.lastLayout,all:used};
  };
  var API={Scheduler:Scheduler,GalleryPool:GalleryPool,shuffle:shuffle};
  if(typeof module!=='undefined'&&module.exports){module.exports=API;return;}
  global.GroovinLocal=API;
  var Q=new URLSearchParams(location.search),speed=Math.max(.1,Math.min(60,Number(Q.get('speed'))||1));
  var hold=Q.has('hold')&&Q.get('hold')!=='0',base;
  try{
    base=new URL(Q.get('media')||'../../../media/Groovin-Emergency-Photos/',location.href);
    if(!['http:','https:','file:'].includes(base.protocol))throw Error('Unsupported local media address');
    if(base.protocol!=='file:'&&base.origin!==location.origin)throw Error('The pictures must use this local page’s address');
    if(!base.pathname.endsWith('/'))base.pathname+='/';
  }catch(error){showMessage('The local picture address needs checking.',error.message,'');return;}
  var stage=document.getElementById('stage'),bar=document.getElementById('bar'),current=null,cycle=[],timer=0,swapTimers=[],failed=new Set(),serial=0;
  var scheduler,pools={},stopped=false,missingPaths=[];
  function elem(tag,className,text){var e=document.createElement(tag);if(className)e.className=className;if(text)e.textContent=text;return e;}
  function showMessage(title,body,detail){
    document.getElementById('message-title').textContent=title;
    document.getElementById('message-body').textContent=body;
    document.getElementById('message-detail').textContent=detail||'';
    document.getElementById('message').hidden=false;
  }
  function source(path){return new URL(path,base).href;}
  function load(path,alt){return new Promise(function(resolve){
    var im=new Image(),done=false;
    var timeout=setTimeout(function(){finish(false);},6000);
    function finish(ok){if(done)return;done=true;clearTimeout(timeout);im.onload=null;im.onerror=null;if(!ok)missingPaths.push(path);resolve(ok?im:null);}
    im.alt=alt||'';im.decoding='async';im.onload=function(){finish(im.naturalWidth>0);};im.onerror=function(){finish(false);};im.src=source(path);
    if(im.complete)finish(im.naturalWidth>0);
  });}
  function art(id){return D.art[id];}
  function titleFor(kind){return {
    pops:['The Pops Orchestra','Music brings us together.','From the concert hall to the gardens, thank you for sharing the joy of live music.','The Pops Orchestra of Bradenton and Sarasota'],
    'adult-volunteers':['Our volunteer community','Many hands. One beautiful place.','Neighbors coming together to care for the gardens, welcome visitors, and make days like this possible.','Thank you, park volunteers.'],
    'student-crew':['Bright Futures volunteers','Learning outdoors. Giving back.','High school volunteers learn new skills and help care for Palma Sola Botanical Park.','Growing through service.']
  }[kind];}
  function photoCredit(record){
    var c=elem('div','photo-credit');
    if(record.credit){c.appendChild(elem('span','credit-label','Photographed here at Palma Sola'));c.appendChild(elem('strong','',record.credit));}
    if(record.license)c.appendChild(elem('span','terms',record.license.replace(/^CC-/,'CC ')+' · via iNaturalist'));
    return c;
  }
  async function makePhoto(item){
    var p=D.photos[item.id];if(!p||failed.has(item.id))return null;
    var im=await load(p.path,p.name||'A photograph from Palma Sola Botanical Park');
    if(!im){failed.add(item.id);return null;}
    var s=elem('section','slide photo-page'),figure=elem('figure');figure.appendChild(im);s.appendChild(figure);
    var caption=elem('div','caption'),label=elem('div','caption-title');
    label.appendChild(elem('div','eyebrow',/student/.test(p.category)?'Bright Futures photography':'From the collection'));
    label.appendChild(elem('h1','',p.name||'A discovery in the gardens'));caption.appendChild(label);caption.appendChild(photoCredit(p));s.appendChild(caption);
    s.dataset.photoId=item.id;return s;
  }
  async function makeWelcome(item){
    var s=elem('section','slide '+item.type),im=await load(art(item.type==='welcome'?'WELCOME_ART':'MUSIC_ART'),'Groovin’ in the Gardens');
    if(im){im.className='full-art';s.appendChild(im);}
    if(item.type==='welcome'){
      if(!im)return null;
      var note=elem('div','welcome-note');note.appendChild(elem('b','','The Pops Orchestra Jazz Combo'));
      note.appendChild(elem('span','','Enjoy the music at Palma Sola Botanical Park'));s.appendChild(note);
    }else{
      var copy=elem('div','partnership-copy'),marks=elem('div','marks');
      var images=await Promise.all([load(art('psbp_logo_green'),'Palma Sola Botanical Park'),load(art('50-pops'),'The Pops Orchestra')]);
      if(images[0])marks.appendChild(images[0]);marks.appendChild(elem('span','','&'));
      if(images[1]){images[1].className='pops';marks.appendChild(images[1]);}
      copy.appendChild(marks);copy.appendChild(elem('div','eyebrow','Our community · Our gardens · Our music'));
      copy.appendChild(elem('h1','','A beautiful place to come together.'));
      copy.appendChild(elem('p','','Thank you for spending this evening with Palma Sola Botanical Park and The Pops Orchestra.'));s.appendChild(copy);
    }
    return s;
  }
  async function makeGallery(item){
    var pool=pools[item.kind],plan=pool.plan(item.kind),info=titleFor(item.kind),s=elem('section','slide montage');
    var copy=elem('div','gallery-copy');copy.appendChild(elem('div','eyebrow',info[0]));copy.appendChild(elem('h1','',info[1]));copy.appendChild(elem('p','',info[2]));copy.appendChild(elem('div','signature',info[3]));
    if(item.kind==='pops'){
      var logo=await load(art('50-pops'),'The Pops Orchestra');if(logo){logo.className='gallery-logo';copy.appendChild(logo);}
    }else if(item.kind==='student-crew'){
      var logos=elem('div','program-logos'),marks=await Promise.all([load(art('psbp_logo_green'),'Palma Sola Botanical Park'),load(art('bright-futures'),'Florida Bright Futures Scholarship Program')]);
      marks.filter(Boolean).forEach(function(im){logos.appendChild(im);});copy.appendChild(logos);
    }
    s.appendChild(copy);var grid=elem('div','gallery-grid '+(plan.layout?'feature-right':'feature-left'));
    var loaded=await Promise.all(plan.initial.map(async function(id){var p=D.photos[id],im=await load(p.path,info[0]);if(!im){failed.add(id);return null;}return {id:id,im:im};}));
    loaded.filter(Boolean).forEach(function(entry,i){var tile=elem('figure','tile'+(item.kind==='pops'&&i===0?' feature':''));tile.dataset.photoId=entry.id;tile.dataset.slot=String(i);tile.appendChild(entry.im);grid.appendChild(tile);});
    if(!grid.children.length)return null;
    s.appendChild(grid);s.dataset.galleryKind=item.kind;s.dataset.layout=String(plan.layout);s._plan=plan;s._pool=pool;s._galleryInfo=info;s._grid=grid;
    return s;
  }
  async function swapGallery(s,batch,token){
    if(!batch.length||current!==s||serial!==token)return;
    var grid=s._grid,tiles=Array.from(grid.children),pool=s._pool;
    var usable=batch.filter(function(id){return !failed.has(id);});
    var images=await Promise.all(usable.map(async function(id){var im=await load(D.photos[id].path,s._galleryInfo[0]);if(!im){failed.add(id);return null;}return {id:id,im:im};}));
    if(current!==s||serial!==token)return;
    images=images.filter(Boolean);if(!images.length)return;
    var slots=shuffle(tiles.map(function(_,i){return i;}));
    images.forEach(function(entry){
      var at=slots.findIndex(function(slot){return pool.lastSlots[entry.id]!==slot;});
      var slot=slots.splice(at<0?0:at,1)[0];if(slot===undefined)return;
      pool.lastSlots[entry.id]=slot;var tile=tiles[slot];tile.replaceChildren(entry.im);tile.dataset.photoId=entry.id;
    });
  }
  function runBar(seconds){
    bar.style.transitionDuration='0s';bar.style.width='0';
    requestAnimationFrame(function(){requestAnimationFrame(function(){bar.style.transitionDuration=seconds+'s';bar.style.width='100%';});});
  }
  function display(s,item){
    swapTimers.forEach(clearTimeout);swapTimers=[];
    var previous=current;serial++;var token=serial;s.dataset.slideId=String(serial)+'-'+item.type;
    s.style.transitionDuration=(hold?0:.7/speed)+'s';
    stage.appendChild(s);requestAnimationFrame(function(){s.classList.add('on');if(previous)previous.classList.remove('on');});current=s;
    if(previous)setTimeout(function(){previous.remove();},850/speed);
    document.getElementById('message').hidden=true;
    if(!hold){
      runBar(item.seconds/speed);
      if(s._plan)s._plan.batches.forEach(function(batch,i){swapTimers.push(setTimeout(function(){swapGallery(s,batch,token);},(i+1)*12*1000/speed));});
      timer=setTimeout(next,item.seconds*1000/speed);
    }
    API.status={slide:item.type,id:item.id||item.kind||item.type,round:scheduler.round,missing:failed.size,mediaRoot:base.href};
  }
  function reviewItem(){
    var r=Q.get('review');if(!r)return null;
    if(r==='welcome'||r==='partnership')return {type:r,seconds:20};
    if(['pops','adults','crew'].includes(r))return {type:'gallery',kind:{pops:'pops',adults:'adult-volunteers',crew:'student-crew'}[r],seconds:36};
    if(r==='photo'||r==='otters'){
      var id=Q.get('photo')||(r==='otters'?D.otters[0]:D.nature[Number(Q.get('item'))||0]);
      return {type:'photo',id:id,seconds:30};
    }
    return null;
  }
  async function next(){
    clearTimeout(timer);if(stopped)return;
    var review=reviewItem(),attempts=review?1:70;
    while(attempts--){
      if(!cycle.length)cycle=scheduler.cycle();
      var item=review||cycle.shift(),s;
      try{s=await (item.type==='photo'?makePhoto(item):item.type==='gallery'?makeGallery(item):makeWelcome(item));}
      catch(error){console.warn('Local slide skipped:',error.message);}
      if(s){display(s,item);return;}
      if(missingPaths.length>10&&!current)break;
    }
    if(current){timer=setTimeout(next,30000);return;}
    showMessage('The local pictures aren’t ready yet.','Check that all four ZIPs were extracted into the same media folder. Open this HTML file on the display computer to test it directly.',base.href);
  }
  function tick(){document.getElementById('clock').textContent=new Date().toLocaleTimeString('en-US',{hour:'numeric',minute:'2-digit'});}
  if(!D||!Array.isArray(D.nature)||!D.nature.length){showMessage('The picture list is missing.','Keep the HTML file and both Groovin local scripts together.','');return;}
  scheduler=new Scheduler(D);
  Object.keys(D.galleries).forEach(function(kind){pools[kind]=new GalleryPool(D.galleries[kind]);});
  tick();setInterval(tick,15000);
  global.addEventListener('pagehide',function(){stopped=true;clearTimeout(timer);swapTimers.forEach(clearTimeout);});
  next();
})(typeof window==='undefined'?globalThis:window);
