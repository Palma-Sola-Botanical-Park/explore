/* Local media/data adapter for the unchanged Groovin slide builders and player. */
(function(g){
  'use strict';
  var data=g.GROOVIN_LOCAL_DATA,base=new URL('../../../media/Groovin-Emergency-Photos/',location.href);
  var repo=new URL('../../',location.href),publicRoot='https://palma-sola-botanical-park.github.io/explore/';
  var api=g.GroovinLocal={};
  function copy(value){return JSON.parse(JSON.stringify(value));}
  function source(url){
    if(!url||/^data:|^blob:/.test(url))return url;
    if(data.urls[url])return new URL(data.urls[url],base).href;
    if(url.indexOf(publicRoot)===0)return new URL(url.slice(publicRoot.length),repo).href;
    if(url.indexOf(base.href)===0||url.indexOf(repo.href)===0)return url;
    if(/^https?:\/\//.test(url))throw Error('Missing local picture: '+url);
    return url;
  }
  function imagesInHTML(html){
    return html.replace(/\bsrc=(["'])(.*?)\1/g,function(all,quote,url){
      return 'src='+quote+source(url.replace(/&amp;/g,'&'))+quote;
    });
  }
  function localSlide(value,key){
    if(Array.isArray(value))return value.map(function(row){return localSlide(row,key);});
    if(value&&typeof value==='object'){
      var out={};Object.keys(value).forEach(function(k){out[k]=localSlide(value[k],k);});return out;
    }
    if(typeof value==='string'){
      if(key==='html')return imagesInHTML(value);
      if(/^(bg|bgFallback|poster|donorPhoto|src|fb|preload)$/.test(key||''))return source(value);
    }
    return value;
  }
  api.source=source;api.localSlide=localSlide;
  api.install=function(){
    // All slide builders, scheduling, styles and timing remain in the approved HTML.
    // This snapshot only narrows the available picture pools and supplies local data.
    function loadSnapshot(){
      Object.keys(data.snapshot).forEach(function(k){g[k]=copy(data.snapshot[k]);});
      return Promise.resolve();
    }
    g.loadAll=loadSnapshot;
    g.grab=function(){return Promise.reject(Error('The local deck uses saved data.'));};
    g.cacheablePhoto=function(){return false;};
    g.photoDisplayURL=source;
    g.warmSlide=function(slide){
      g.slidePhotoURLs(localSlide(slide)).forEach(function(url){var im=new Image();im.src=url;});
      return Promise.resolve();
    };
    var approvedRender=g.render;
    g.render=function(slide){return approvedRender(localSlide(slide));};
    api.fontsReady=Promise.resolve();
    if(typeof FontFace!=='undefined'&&document.fonts){
      api.fontsReady=Promise.all(data.fonts.map(function(font){
        var face=new FontFace(font.family,'url("'+new URL(font.path,base).href+'")',
          {style:font.style,weight:font.weight,display:'swap'});
        return face.load().then(function(loaded){document.fonts.add(loaded);});
      })).catch(function(error){console.warn('Local Groovin fonts:',error.message);});
    }
  };
})(window);
