#!/usr/bin/env node
/* Enumerate event assets with the actual builders, without a browser/network. */
const fs = require('node:fs'), path = require('node:path'), vm = require('node:vm');
const root = path.resolve(process.argv[2] || path.join(__dirname, '../..'));
const html = fs.readFileSync(path.join(root, 'screens/events/screen-groovin.html'), 'utf8');
const PUBLIC = 'https://palma-sola-botanical-park.github.io/explore/';
const urls = new Set(), feeds = new Set();
const storage = new Map();
const sandbox = {
  console:{log(){},warn(){},error(...args){process.stderr.write(args.join(' ')+'\n');}},
  URL, URLSearchParams, Date, Math, Promise, setTimeout(){return 0;}, clearTimeout(){}, setInterval(){return 0;},
  location:{href:PUBLIC+'screens/events/screen-groovin.html', protocol:'https:',hostname:'palma-sola-botanical-park.github.io',pathname:'/explore/screens/events/screen-groovin.html',search:''},
  navigator:{}, addEventListener(){}, Image:function(){},
  document:{getElementById(){return {};},addEventListener(){}},
  localStorage:{getItem(k){return storage.get(k)||null;},setItem(k,v){storage.set(k,v);}},
  fetch:async function(url){
    const u=new URL(url, PUBLIC+'screens/events/screen-groovin.html');
    const relative=decodeURIComponent(u.pathname.replace(/^\/explore\//,''));feeds.add(relative);
    const data=JSON.parse(fs.readFileSync(path.join(root,relative),'utf8'));
    return {ok:true,json:async()=>structuredClone(data)};
  }
};
vm.createContext(sandbox);
const code = [...html.matchAll(/<script(?:\s[^>]*)?>([\s\S]*?)<\/script>/g)].map(m=>m[1]);
vm.runInContext(code[0], sandbox, {filename:'screen-groovin.html'});
function add(url){if(url&&!/^(data:|blob:)/.test(url))urls.add(new URL(url,PUBLIC+'screens/events/screen-groovin.html').href);}
function slide(s){
  if(!s)return;
  sandbox.slidePhotoURLs(s).forEach(add);
  (s.venuePhotos||[]).concat(s.communityPhotos||[]).forEach(p=>add(p.src));
  for(const m of (s.html||'').matchAll(/\bsrc=["']([^"']+)["']/g))add(m[1].replace(/&amp;/g,'&'));
}
(async()=>{
  /* BASE is relative on public Pages. Resolve it exactly as fetch() does. */
  sandbox.BASE=PUBLIC;sandbox.FEEDS=PUBLIC+'data/published/';
  await sandbox.loadAll();
  for (const key of ['WELCOME_ART','POPS_ART','MUSIC_ART','JAZZ_POSTER'])add(sandbox[key]);
  for(let i=0;i<30;i++)for(const item of sandbox.groovinDeck())slide(item.make());
  for(const maker of Object.values(sandbox.reviewBuilders)){try{slide(maker());}catch(_) {}}
  // Every eligible nature shot, not a random subset, keeps future rotations covered.
  const features=sandbox.D.plants.concat(sandbox.D.wildlife).filter(sandbox.featurePhoto);
  const species=new Set(features.map(s=>s.id));
  for(const row of Object.values(sandbox.CREDITS)){
    if(row.publish_ok&&row.photo_id&&species.has(row.psbp_id))add(sandbox.mediaOf(row,'original'));
  }
  for(const p of sandbox.BF.showcase)add(sandbox.mediaOf(p,'original'));
  const pops=sandbox.slidePopsGallery();
  const popsIds=pops?[...pops.crewInitial,...pops.crewSwaps].map(p=>p.id):[];
  if(new Set(popsIds).size!==popsIds.length)throw Error('Duplicate photo in Pops gallery');
  if(!html.includes('.slide.crew-gallery:not(.pops-gallery) .crewgrid img{object-fit:contain;'))throw Error('Volunteer crop protection missing');
  const stats={pops:sandbox.EVENT_MEDIA.pops.length,students:sandbox.BF.crew.length,adults:sandbox.EVENT_MEDIA.adults.length,
    featureSpecies:features.length,studentNature:sandbox.BF.showcase.length,popsTiles:pops?.crewInitial.length,popsSwaps:pops?.crewSwapBatches.map(b=>b.length),popsDistinct:popsIds.length};
  process.stdout.write(JSON.stringify({schema:1,feeds:[...feeds].sort(),urls:[...urls].sort(),stats},null,2)+'\n');
})().catch(e=>{process.stderr.write(e.stack+'\n');process.exitCode=1;});
