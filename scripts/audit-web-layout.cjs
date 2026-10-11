// Reproducible layout audit: all views/states, synthetic data, no SDK/network.
// Optional Playwright browser tooling; not a production app dependency.
const fs = require('node:fs');
const assert = require('node:assert/strict');
const {chromium,webkit} = require(process.env.TDN_PLAYWRIGHT_MODULE || 'playwright');
fs.mkdirSync('tmp/web-layout', {recursive:true});
// Trusted repository fixture, not an HTML sanitizer. Keep original tags;
// every context aborts all network requests, including SDK scripts/styles.
let html = fs.readFileSync('web/index.html','utf8')
  .replace('</head>', `<style>${fs.readFileSync('web/styles.css','utf8')}</style></head>`);
html = html.replace(/src="(\/assets\/[A-Za-z0-9_-]+\.png)"/g, (_match,path) => {
  return `src="data:image/png;base64,${fs.readFileSync(path.slice(1)).toString('base64')}"`;
});
html=html.replaceAll('/assets/nexus-editor-poster-v1.webp',`data:image/webp;base64,${fs.readFileSync('assets/nexus-editor-poster-v1.webp').toString('base64')}`);
const production=fs.readFileSync('web/app.js','utf8');
function extract(name){
 const start=production.indexOf(`function ${name}(`);
 const next=/\n(?:async )?function /.exec(production.slice(start+1));
 return production.slice(start,next?start+1+next.index:undefined);
}
html=html.replace('</body>',`<script>
function element(tag,cls,text){const el=document.createElement(tag);el.className=cls;if(text!==undefined)el.textContent=text;return el;}
const byId=id=>document.getElementById(id);
const appState={authorized:true,activeEpisode:{transcript:[{text:'Data engineering and architecture',startMs:60000,isHeading:true},{text:'Cloud and software',startMs:120000,isHeading:true}]}};
function syncHomeFilm(){} // Layout-only fixture; real video lifecycle has its own offline audit.
${['syncCompactSelects','setupCompactSelects','setupCompactInputs','setupNavigation','renderPlayerChapters','syncHostControls','syncNewspaperControls','syncParameterControls'].map(extract).join('\n')}
setupCompactSelects();setupCompactInputs();setupNavigation();renderPlayerChapters();
document.querySelectorAll('.parameter-form').forEach(form=>{form.elements.hostCount.value='1';form.elements.soloName.value='Dalia';syncParameterControls(form);});
</script></body>`);
(async()=>{
 const safari=process.argv.includes('--webkit');
 const browser=await (safari?webkit:chromium).launch(safari?{headless:true}:{channel:process.env.TDN_CHROMIUM_CHANNEL || undefined,headless:true});
 let failures=0,checks=0;
 const inventory=[];
 try{
  for(const width of [320,375,390,414,430,768,900,1024,1440]){
   const context=await browser.newContext({isMobile:width<=430,hasTouch:width<=900,deviceScaleFactor:1});
   const page=await context.newPage();
   await page.route('**/*',route=>route.abort());
   await page.setViewportSize({width,height:860});
   await page.setContent(html);
   await page.evaluate(()=>{
    document.querySelector('#signed-in-user').textContent='reader-with-a-long-address@example.com';
    document.querySelector('#mini-player-title').textContent='10/11/2026 The Daily Nexus - A detailed edition title for the layout check - 0001';
    document.querySelector('#mini-player-edition').textContent='0001';
    document.querySelector('#mini-player-current').textContent='19:45';
    document.querySelector('#mini-player-total').textContent='29:59';
    if(!document.querySelector('#generation-form'))throw new Error('Missing form');
    for(const input of document.querySelectorAll('input[type="date"]'))input.value='2026-10-11';
    for(const input of document.querySelectorAll('input[type="time"]'))input.value='04:45';
    for(const select of document.querySelectorAll('select')){
     const option=document.createElement('option'); option.textContent='A long descriptive option for a saved edition';
     option.selected=true;select.append(option);
    }
    for(const list of document.querySelectorAll('.episode-list,.edition-list')){
     list.classList.remove('empty-state');list.replaceChildren();
     for(let i=0;i<5;i++){
      const card=document.createElement('article');
      card.className=list.classList.contains('episode-list')?'episode-list-item':'edition-list-item';
      card.innerHTML='<h3>10/11/2026 The Daily Nexus - A detailed edition title - 0001</h3><p class="item-meta">2026-10-11 // 27 MIN</p>';
      list.append(card);
     }
    }
    for(const list of document.querySelectorAll('.section-token-list')){
     list.innerHTML='<span class="section-chip"><span class="section-chip-label">Data engineering and enterprise architecture</span><span class="section-chip-actions"><button class="section-chip-move">←</button><button class="section-chip-move">→</button><button class="section-chip-remove">×</button></span></span><span class="section-drop-end"></span>';
    }
    document.querySelector('#edition-pages').classList.remove('empty-state');
    document.querySelector('#edition-pages').innerHTML='<div class="edition-page" style="width:1200px;height:800px">Synthetic newspaper zoomed to 200%</div>';
    document.querySelector('#player-title').textContent='A long podcast title with a detailed edition name and publication sequence';
    document.querySelector('#runner-detail').textContent='LAST CLOUD CHECK 07:19:13 AM // No task is due.';
    const requests=document.querySelector('#run-request-list');requests.classList.remove('empty-state');requests.replaceChildren();
    for(let i=0;i<6;i++){
     const row=document.createElement('article');row.className='request-item';
     row.innerHTML='<div class="request-header"><p class="item-meta">PUBLISHED // 2026-10-11 // Morning edition // Source/Example</p></div><p class="request-timeline">QUEUED OCT 11 AT 04:45 AM // STARTED OCT 11 AT 04:46 AM // PUBLISHED OCT 11 AT 05:10 AM // WAIT 00:01:00 // DURATION 00:24:00</p><p class="request-detail">PUBLISHED // AVAILABLE IN THE PRIVATE FEED</p>';
     if(i===0){row.querySelector('.request-header').insertAdjacentHTML('beforeend','<div class="request-actions"><button class="ghost-button requeue-button">REQUEUE</button><button class="danger-button requeue-button">DELETE</button></div>');row.querySelector('.request-detail').textContent='FAILED // AN ILLUSTRATIVE RETRYABLE REQUEST';}
     requests.append(row);
    }
    const schedules=byId('schedule-list');schedules.classList.remove('empty-state');schedules.replaceChildren();
    for(let i=0;i<3;i++){
     const item=document.createElement('article');item.className='schedule-item';
     item.innerHTML='<h3>A detailed scheduled edition name</h3><p class="item-meta">04:45 // READY BY 06:00 // EUROPE/LONDON</p><p class="item-meta">MON, TUE, WED, THU, FRI // PREVIOUS CALENDAR DAY // TWO HOSTS</p><div class="item-actions"><label class="schedule-enable-toggle"><input type="checkbox" checked>ENABLED</label><button class="ghost-button">EDIT</button><button class="danger-button">DELETE</button></div>';
     schedules.append(item);
    }
    syncCompactSelects();
   });
   for(const view of ['auth','home','gen','play','read','schedule','about']){
    for(const compact of view==='auth'||view==='play'?[false]:[false,'collapsed','expanded']){
     await page.evaluate(({view,compact})=>{
      document.querySelector('#auth-screen').hidden=view!=='auth';
      document.querySelector('#app-shell').hidden=view==='auth';
      document.querySelectorAll('.view').forEach(el=>el.classList.toggle('active',el.id===`view-${view}`));
      document.querySelectorAll('.mode-button').forEach(el=>{el.classList.toggle('active',el.dataset.view===view);if(el.dataset.view===view)el.setAttribute('aria-current','page');else el.removeAttribute('aria-current');});
      window.scrollTo({top:0,behavior:'instant'});
      document.querySelector('#mini-player').hidden=!compact;
      document.querySelector('#mini-player').classList.toggle('levels-open',compact==='expanded');
      document.querySelector('#mini-levels-button').setAttribute('aria-expanded',String(compact==='expanded'));
     },{view,compact});
     if(view!=='auth')await page.waitForFunction(()=>getComputedStyle(document.querySelector('.view.active')).opacity==='1');
     if(view!=='auth')await page.evaluate(()=>Promise.all(document.querySelector('.view.active').getAnimations().filter(a=>a.effect.getTiming().iterations!==Infinity).map(a=>a.finished.catch(()=>{}))));
     const metrics=await page.evaluate(()=>{
      const width=document.documentElement.clientWidth;
      const out=[...document.querySelectorAll('body *')].filter(el=>{
       if(!el.getClientRects().length)return false;
       const r=el.getBoundingClientRect();return r.right>width+1||r.left<-1;
      }).map(el=>({tag:el.tagName,id:el.id,cls:el.className,right:Math.round(el.getBoundingClientRect().right),width:Math.round(el.getBoundingClientRect().width)}));
      const spill=[...document.querySelectorAll('body *')].filter(el=>el.getClientRects().length&&el.scrollWidth>el.clientWidth+2)
       .map(el=>({tag:el.tagName,id:el.id,cls:el.className,type:el.getAttribute('type'),client:el.clientWidth,scroll:el.scrollWidth,overflow:getComputedStyle(el).overflowX}));
      return {width,scroll:document.documentElement.scrollWidth,body:document.body.scrollWidth,out:out.slice(0,6),spill:spill.slice(0,18)};
     });
     if(view!=='auth'){
      const footerHeight=await page.locator('.statusbar').evaluate(el=>el.getBoundingClientRect().height);
      assert.ok(footerHeight<75,`Footer must not stretch into a vacant grid track: ${footerHeight}`);
     }
     checks++;
     if(metrics.scroll>width+1||metrics.body>width+1){
      failures++; console.log(JSON.stringify({width,view,compact,...metrics}));
     }
     if(width===390&&view==='gen'&&!compact)await page.screenshot({path:'tmp/web-layout/mobile-gen-layout.png',fullPage:true});
     if([390,1440].includes(width)&&!compact){
      await page.screenshot({path:`tmp/web-layout/compact-${safari?'webkit':'chromium'}-${width}-${view}.png`});
     }
     if(view!=='auth')inventory.push({width,view,compact,controls:await page.locator('input,select,textarea,button').evaluateAll(els=>els.filter(el=>el.getClientRects().length).map(el=>({id:el.id||el.name||el.className,type:el.type||el.tagName,font:parseFloat(getComputedStyle(el).fontSize),height:Math.round(el.getBoundingClientRect().height),width:Math.round(el.getBoundingClientRect().width)})))});
     if(width<=430&&view!=='auth'){
      const smallFields=await page.locator('input:not([type="checkbox"]):not([type="range"]):not([type="hidden"]), select, textarea').evaluateAll(elements=>elements.filter(el=>el.getClientRects().length&&parseFloat(getComputedStyle(el).fontSize)<16).map(el=>el.id||el.name));
      assert.deepEqual(smallFields,[],`Focus-zoom fields in ${view}/${width}`);
      const smallButtons=await page.locator('button').evaluateAll(elements=>elements.filter(el=>el.getClientRects().length&&(el.getBoundingClientRect().height<43.9||el.getBoundingClientRect().width<43.9)).map(el=>el.id||el.className));
      assert.deepEqual(smallButtons,[],`Small touch targets in ${view}/${width}`);
      const captions=await page.locator('.select-caption').evaluateAll(els=>els.filter(el=>el.getClientRects().length).map(el=>parseFloat(getComputedStyle(el).fontSize)));
      assert.ok(captions.every(size=>size===13),`Shared 13px utility scale in ${view}/${width}`);
      const utilityInputs=await page.locator('input:not([type="checkbox"]):not([type="range"]):not([type="hidden"])').evaluateAll(els=>els.filter(el=>el.getClientRects().length).map(el=>{
       const style=getComputedStyle(el), shell=el.parentElement.getBoundingClientRect(), box=el.getBoundingClientRect();
       const scale=new DOMMatrix(style.transform).a;
       return {id:el.id||el.name, wrapped:el.parentElement.classList.contains('compact-input-shell'), paintedFont:parseFloat(style.fontSize)*scale, height:shell.height,
        inside:box.right<=shell.right+1&&box.left>=shell.left-1};
      }));
      assert.ok(utilityInputs.every(item=>item.wrapped&&Math.abs(item.paintedFont-13)<0.01&&item.height>=44&&item.inside),JSON.stringify(utilityInputs));
      const editFailures=await page.locator('input:not([type="checkbox"]):not([type="range"]):not([type="hidden"])').evaluateAll(els=>{
       const errors=[];
       for(const el of els.filter(el=>el.getClientRects().length&&!el.disabled)){
        const before=el.value, height=el.parentElement.getBoundingClientRect().height;
        el.focus({preventScroll:true});
        if(getComputedStyle(el).transform!=='none'||parseFloat(getComputedStyle(el).fontSize)!==16||el.parentElement.getBoundingClientRect().height!==height||el.value!==before)errors.push(el.id||el.name);
        el.blur();
       }
       return errors;
      });
      assert.deepEqual(editFailures,[],`Every visible native field must preserve editing geometry/value: ${view}/${width}`);
      const typography=await page.locator('button,label,legend,.card-header,.item-meta').evaluateAll(els=>els.filter(el=>el.getClientRects().length).map(el=>({id:el.id||el.className||el.tagName,size:parseFloat(getComputedStyle(el).fontSize)})));
      assert.ok(typography.every(item=>item.size<=14),JSON.stringify(typography.filter(item=>item.size>14)));
      const header=await page.locator('.topbar').evaluate(el=>el.getBoundingClientRect().height);
      assert.ok(header<=80,`Mobile header too tall: ${header}`);
      const headerRows=await page.locator('.brand img,#install-button,#sign-out-button').evaluateAll(els=>els.filter(el=>el.getClientRects().length).map(el=>{const r=el.getBoundingClientRect();return r.y+r.height/2;}));
      assert.ok(Math.max(...headerRows)-Math.min(...headerRows)<2,'Logo and account actions must share a row');
      const stretched=await page.locator('.episode-list-item,.edition-list-item').evaluateAll(els=>els.filter(el=>el.getClientRects().length).map(el=>el.getBoundingClientRect().height));
      assert.ok(stretched.every(height=>height<150),'Archive cards should not fill the vertical list frame');
      const nav=await page.locator('.mode-nav').evaluate(el=>({client:el.clientWidth,scroll:el.scrollWidth}));
      assert.ok(nav.scroll<=nav.client+1,'All six mobile navigation controls should fit');
     }
     if(compact==='collapsed'){
      const height=await page.locator('#mini-player').evaluate(el=>el.getBoundingClientRect().height);
      assert.ok(height<=(width<=640?100:60),`Collapsed mini player too tall: ${height}px at ${width}`);
      assert.equal(await page.locator('#mini-pause-button').isVisible(),true);
      assert.equal(await page.locator('#mini-next-button').isVisible(),false);
     }
     if(compact==='expanded'){
      for(const id of ['mini-previous-button','mini-play-button','mini-pause-button','mini-stop-button','mini-next-button','mini-volume','mini-speed'])assert.equal(await page.locator(`#${id}`).isVisible(),true,`Missing expanded control ${id}`);
     }
     if(width===390&&view==='about'&&compact==='collapsed'){
      await page.screenshot({path:`tmp/web-layout/minimal-${safari?'webkit':'chromium'}-mini.png`});
      await page.locator('#mini-player').screenshot({path:`tmp/web-layout/minimal-${safari?'webkit':'chromium'}-strip.png`});
     }
     if(view==='play'){
      await page.locator('#player-chapters').selectOption('120');
      assert.equal(await page.locator('.chapter-control .select-caption').textContent(),'Cloud and software');
      await page.evaluate(()=>{const details=byId('player-details');details.classList.remove('empty-state');details.innerHTML='<button class="transcript-segment">Source disclosure. An illustrative transcript excerpt.</button><button class="transcript-segment active">Dalia // A readable verified story, without oversized chapter controls.</button>';});
      if(width===390)await page.locator('#web-player').screenshot({path:`tmp/web-layout/dynamic-${safari?'webkit':'chromium'}-chapters.png`});
      await page.evaluate(()=>{
       appState.activeEpisode={transcript:[]};renderPlayerChapters();
       if(!byId('player-chapters').closest('.chapter-control').hidden)throw new Error('Chapter label must hide on missing headings');
       appState.activeEpisode={transcript:[{text:'Cloud and software',startMs:120000,isHeading:true}]};renderPlayerChapters();
      });
     }
    }
   }
   await page.evaluate(()=>{
    document.querySelector('#auth-screen').hidden=true;document.querySelector('#app-shell').hidden=false;
    document.querySelectorAll('.view').forEach(el=>el.classList.toggle('active',el.id==='view-gen'));
    document.querySelector('#mini-player').hidden=true;
   });
   for(const selector of ['#monitor-date-filter','#monitor-query-filter','#profile-name','#generation-form input[name="runName"]','#generation-form input[name="gmailLabel"]','#gen-section-input']){
    const control=page.locator(selector);
    if(width<=430){
     const before=await control.evaluate(el=>el.parentElement.getBoundingClientRect().height);
     await control.focus();
     assert.equal(await control.evaluate(el=>getComputedStyle(el).transform),'none');
     assert.equal(await control.evaluate(el=>getComputedStyle(el).fontSize),'16px');
     assert.equal(await control.evaluate(el=>el.parentElement.getBoundingClientRect().height),before);
     if(selector!=='#monitor-date-filter'){
      await control.fill('Synthetic test');assert.equal(await control.inputValue(),'Synthetic test');
      await control.fill('');
     }
     await control.evaluate(el=>el.blur());
    }
   }
   await page.evaluate(()=>{
    for(const form of document.querySelectorAll('.parameter-form')){
     form.elements.hostCount.value='2';syncParameterControls(form);
     if(!form.elements.soloName.disabled||form.elements.dialogueStyle.disabled||form.elements.secondaryVoice.disabled)throw new Error('Duo controls incorrect');
     if(form.elements.primaryVoice.selectedOptions[0].dataset.gender!=='Female'||form.elements.secondaryVoice.selectedOptions[0].dataset.gender!=='Male')throw new Error('Voice captions must reflect valid duo voices');
     form.elements.hostCount.value='1';form.elements.soloName.value='Nox';syncParameterControls(form);
     if(form.elements.soloName.disabled||!form.elements.dialogueStyle.disabled||!form.elements.secondaryVoice.disabled)throw new Error('Solo controls incorrect');
     if(form.elements.primaryVoice.selectedOptions[0].dataset.gender!=='Male')throw new Error('Solo Nox needs male voice caption');
     form.elements.soloName.value='Dalia';syncParameterControls(form);
    }
   });
   await page.locator('[data-view="home"]').click();
   assert.equal(await page.locator('#home-tour-pause,#home-tour-replay').count(),0);
   assert.equal(await page.locator('#home-film').count(),1);
   const film=await page.locator('#home-film').evaluate(el=>({loop:el.loop,muted:el.muted,inline:el.playsInline,preload:el.preload,src:el.getAttribute('src')}));
   assert.equal(film.loop,true);assert.equal(film.muted,true);assert.equal(film.inline,true);assert.equal(film.preload,'none');assert.equal(film.src,null);
   assert.equal(await page.locator('#home-film-poster').evaluate(el=>el.naturalWidth),1280);
   await page.locator('[data-view="schedule"]').click();
   const days=await page.locator('.weekday-picker label').evaluateAll(els=>els.map(el=>{const r=el.getBoundingClientRect();return {y:r.y,width:r.width,height:r.height};}));
   assert.equal(new Set(days.map(day=>Math.round(day.y))).size,1,'All seven weekdays must share one row');
   assert.ok(days.every(day=>day.width>=24&&day.height>=(width<=900?44:39)),'Weekday targets need 24px minimum width and touch-friendly height');
   const monday=page.locator('input[name="weekday"][value="0"]');
   const before=await monday.isChecked();await monday.click();assert.equal(await monday.isChecked(),!before);
   await page.locator('[data-view="home"]').click();
   await page.locator('#view-home [data-open-view="play"]').click();
   assert.equal(await page.locator('[data-view="play"]').getAttribute('aria-current'),'page');
   assert.equal(await page.locator('#home-tour').evaluate(el=>el.getAnimations({subtree:true}).length),0,'Hidden HOME must not animate');
   await page.evaluate(()=>{
    byId('global-alert').hidden=false;byId('global-alert-message').textContent='A long illustrative validation message with an_unbroken_technical_word_that_must_wrap_without_expanding_the_console.';
   });
   assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=document.documentElement.clientWidth+1),true,'Visible errors must fit');
   await page.evaluate(()=>byId('global-alert').hidden=true);
   if(width===390){
    await page.locator('[data-view="gen"]').click();
    await page.locator('#monitor-date-filter').fill('2026-10-05');
    await page.locator('#monitor-date-filter').evaluate(el=>el.blur());
    await page.evaluate(()=>{const list=document.querySelector('#run-request-list');list.replaceChildren('No requests match these filters.');list.classList.add('empty-state');});
    await page.locator('.monitor-card').screenshot({path:`tmp/web-layout/audit-${safari?'webkit':'chromium'}-monitor.png`});
   }
   await context.close();
   const reduced=await browser.newContext({viewport:{width,height:860},reducedMotion:'reduce'});
   const reducedPage=await reduced.newPage();await reducedPage.route('**/*',route=>route.abort());
   await reducedPage.setContent(html);
   assert.equal(await reducedPage.evaluate(()=>matchMedia('(prefers-reduced-motion: reduce)').matches),true);
   assert.equal(await reducedPage.locator('.view.active').evaluate(el=>getComputedStyle(el).animationName),'none');
   assert.equal(await reducedPage.locator('.view.active').evaluate(el=>getComputedStyle(el,'::before').animationName),'none');
   assert.equal(await reducedPage.locator('#home-tour-pause,#home-tour-replay').count(),0);
   const tourAnimations=await reducedPage.locator('#home-tour').evaluate(el=>el.getAnimations({subtree:true}).length);
   assert.equal(tourAnimations,0,'Reduced motion must keep the explainer static');
   await reduced.close();
  }
 }finally{await browser.close();}
 console.log(`Engine=${safari?'WebKit':'Chromium'}; layout checks=${checks}; document overflows=${failures}`);
 fs.writeFileSync(`tmp/web-layout/inventory-${safari?'webkit':'chromium'}.json`,JSON.stringify(inventory,null,2));
 assert.equal(failures,0,'No page should widen the document');
})().catch(error=>{console.error(error.stack);process.exitCode=1;});

