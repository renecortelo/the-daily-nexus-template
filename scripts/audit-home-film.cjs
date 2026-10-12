// Offline browser regression for the production HOME markup, CSS and lifecycle.
// Synthetic authorization only; no Firebase, identity, newsletter or external network.
const fs=require('node:fs');
const http=require('node:http');
const assert=require('node:assert/strict');
const {chromium,webkit}=require(process.env.TDN_PLAYWRIGHT_MODULE||'playwright');
const source=fs.readFileSync('web/app.js','utf8');
const pageSource=fs.readFileSync('web/index.html','utf8');
function extract(name){const start=source.indexOf(`function ${name}(`);const next=/\n(?:async )?function /.exec(source.slice(start+1));return source.slice(start,next?start+1+next.index:undefined);}
const figure=pageSource.slice(pageSource.indexOf('<figure id="home-tour"'),pageSource.indexOf('</figure>')+9);
const fixture=`<!doctype html><meta name="viewport" content="width=device-width,initial-scale=1"><style>${fs.readFileSync('web/styles.css','utf8')}#app-shell{padding:16px}.nexus-tour{max-width:960px}.test-space{height:1600px}</style><div id="app-shell" hidden><nav><button class="mode-button" data-view="home">HOME</button><button class="mode-button" data-view="gen">GEN</button></nav><div id="mini-player" hidden></div><section id="view-home" class="view active">${figure}<div class="test-space"></div></section><section id="view-gen" class="view"><p>Synthetic view</p></section></div><script>const byId=id=>document.getElementById(id);const appState={authorized:false};${['syncHomeFilm','setupHomeFilm','setupNavigation'].map(extract).join('\n')}setupNavigation();setupHomeFilm();window.authorize=()=>{appState.authorized=true;byId('app-shell').hidden=false;syncHomeFilm();};window.lock=()=>{appState.authorized=false;syncHomeFilm();};</script>`;
const media=new Map([
 ['/assets/nexus-editor-film-v2.mp4',{type:'video/mp4',body:fs.readFileSync('assets/nexus-editor-film-v2.mp4')}],
 ['/assets/nexus-editor-poster-v2.webp',{type:'image/webp',body:fs.readFileSync('assets/nexus-editor-poster-v2.webp')}],
]);
const server=http.createServer((req,res)=>{
 if(req.url==='/'){res.writeHead(200,{'Content-Type':'text/html;charset=utf-8'});res.end(fixture);return;}
 const file=media.get(req.url);if(!file){res.writeHead(404);res.end();return;}
 const range=/^bytes=(\d+)-(\d*)$/.exec(req.headers.range||'');
 if(range){const start=Number(range[1]),end=range[2]?Math.min(Number(range[2]),file.body.length-1):file.body.length-1;
  if(start>end||start>=file.body.length){res.writeHead(416,{'Content-Range':`bytes */${file.body.length}`});res.end();return;}
  res.writeHead(206,{'Content-Type':file.type,'Accept-Ranges':'bytes','Content-Range':`bytes ${start}-${end}/${file.body.length}`,'Content-Length':end-start+1});res.end(file.body.subarray(start,end+1));
 }else{res.writeHead(200,{'Content-Type':file.type,'Accept-Ranges':'bytes','Content-Length':file.body.length});res.end(file.body);}
});
(async()=>{
 await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
 const origin=`http://127.0.0.1:${server.address().port}`;
 const safari=process.argv.includes('--webkit');let browser;
 const results=[];fs.mkdirSync('tmp/home-film-audit',{recursive:true});
 try{
  browser=await(safari?webkit:chromium).launch(safari?{headless:true}:{headless:true,channel:process.env.TDN_CHROMIUM_CHANNEL||undefined});
  for(const width of [320,390,768,1440]){
   const ctx=await browser.newContext({viewport:{width,height:860},reducedMotion:'no-preference'});const page=await ctx.newPage();let requests=0;
   await page.route('**/*',route=>route.request().url().startsWith(origin)?route.continue():route.abort());
   page.on('request',req=>{if(req.url().endsWith('.mp4'))requests++;});
   await page.goto(origin);assert.equal(await page.locator('#home-film').getAttribute('src'),null);assert.equal(requests,0);
   await page.evaluate(()=>{document.querySelector('[data-view="gen"]').click();window.authorize();});assert.equal(requests,0);
   await page.locator('[data-view="home"]').click();
   await page.waitForFunction(()=>{const v=document.querySelector('#home-film');return !v.paused&&v.currentTime>.1;});
   const video=await page.locator('#home-film').evaluate(el=>({duration:el.duration,width:el.videoWidth,height:el.videoHeight,muted:el.muted,inline:el.playsInline,loop:el.loop,controls:el.controls}));
   // The encoded size is checked separately; some media backends report scaled frames.
   assert.equal(video.duration,16);assert.ok(video.width>0&&video.height>0);assert.ok(Math.abs(video.width/video.height-16/9)<.03);assert.equal(video.muted,true);assert.equal(video.inline,true);assert.equal(video.loop,true);assert.equal(video.controls,false);
   await page.locator('#home-tour').screenshot({path:`tmp/home-film-audit/${safari?'webkit':'chromium'}-${width}-playing.png`});
   assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
   await page.locator('#home-film').evaluate(el=>{el.currentTime=15.8;});await page.waitForFunction(()=>document.querySelector('#home-film').currentTime<1);
   await page.evaluate(()=>scrollTo({top:1800,behavior:'instant'}));await page.waitForFunction(()=>document.querySelector('#home-film').paused);
   await page.evaluate(()=>scrollTo({top:0,behavior:'instant'}));await page.waitForFunction(()=>!document.querySelector('#home-film').paused);
   await page.locator('[data-view="gen"]').click();assert.equal(await page.locator('#home-film').evaluate(el=>el.paused),true);
   await page.locator('[data-view="home"]').click();await page.waitForFunction(()=>!document.querySelector('#home-film').paused);
   await page.emulateMedia({reducedMotion:'reduce'});await page.waitForFunction(()=>document.querySelector('#home-film').paused&&document.querySelector('#home-film').hidden);
   assert.equal(await page.locator('#home-film-poster').isVisible(),true);
   await page.emulateMedia({reducedMotion:'no-preference'});await page.waitForFunction(()=>!document.querySelector('#home-film').paused);
   await page.evaluate(()=>window.lock());assert.equal(await page.locator('#home-film').evaluate(el=>el.paused),true);
   await page.evaluate(()=>window.authorize());await page.waitForFunction(()=>!document.querySelector('#home-film').paused);
   await page.locator('#home-film').evaluate(el=>el.dispatchEvent(new Event('error')));assert.equal(await page.locator('#home-film-poster').isVisible(),true);
   await page.locator('#home-tour').screenshot({path:`tmp/home-film-audit/${safari?'webkit':'chromium'}-${width}.png`});
   results.push({width,video,passed:true});await ctx.close();
  }
  console.log(JSON.stringify({engine:safari?'WebKit':'Chromium',results},null,2));
 }finally{if(browser)await browser.close();await new Promise(resolve=>server.close(resolve));}
})().catch(error=>{console.error(error);process.exitCode=1;});
