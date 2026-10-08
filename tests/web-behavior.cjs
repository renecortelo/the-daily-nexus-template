// Execute production functions against a deterministic DOM; no SDK/network.
const fs = require('node:fs');
const vm = require('node:vm');
const test = require('node:test');
const assert = require('node:assert/strict');
const source = fs.readFileSync('web/app.js', 'utf8');
function extract(name) {
  let start = source.indexOf(`function ${name}(`);
  if (source.slice(start - 6, start) === 'async ') start -= 6;
  const next = /\n(?:async )?function /.exec(source.slice(start + 1));
  return source.slice(start, next ? start + 1 + next.index : undefined);
}
function harness(names) {
  const nodes = new Map(), images = [];
  function node() {
    return { children: [], options: [], textContent: '', className: '', hidden: false,
      classList: {toggle(){}, remove(){}}, parentElement: {classList: {remove(){}}},
      style: {setProperty(){}}, setAttribute(){}, removeAttribute(name){delete this[name];},
      replaceChildren(){this.children=[]; this.textContent='';}, append(child){this.children.push(child);},
      addEventListener(event,callback){this[event]=callback;}, remove(){},
      pause(){this.paused=true;}, load(){}, scrollTo(){this.scrolls=(this.scrolls||0)+1;},
    };
  }
  const context = { URL, Date, Map, console, ARCHIVE_PAGE_SIZE: 100,
    appState: {authorized:true, user:{uid:'synthetic-user'}, subscriptions:[]},
    byId(id){if(!nodes.has(id)) nodes.set(id,node()); return nodes.get(id);},
    document: {querySelectorAll(){return []; }},
    element(){const image=node(); images.push(image); return image;},
    applyEditionZoom(){}, clearSubscriptions(){}, updateCloudClockStatus(){}, updateArchiveButtons(){},
    clearPlaybackSession(){}, syncMediaSession(){}, savePlaybackPosition(){},
    window: {clearTimeout(){}, matchMedia(){return {matches:false};}},
    showAlert(){}, firebaseErrorMessage(){return 'safe error';},
  };
  vm.createContext(context);
  vm.runInContext(names.map(extract).join('\n'),context);
  return {context,nodes,images};
}

test('newspaper option preserves edition scale when disabled and defaults on for old favorites', () => {
  const {context}=harness(['parameterData','syncNewspaperControls','applyParametersToForm']);
  const values=new Map([['runName','Example'],['gmailLabel','Example/News'],['hostCount','1'],['includeNewspaper','on']]);
  context.FormData=class {get(key){return values.get(key);}};
  context.sectionValues=()=>['Data'];
  context.setSections=(form,sections)=>{form.sections=sections;};
  context.syncHostControls=()=>{};
  context.HTMLInputElement=class {};
  const form={elements:{editionScale:{value:'focused'},includeNewspaper:{checked:true},includeTih:{checked:true},namedItem(name){return this[name];}}};
  assert.equal(context.parameterData(form).includeNewspaper,true);
  values.delete('includeNewspaper'); form.elements.includeNewspaper.checked=false;
  context.syncNewspaperControls(form);
  assert.equal(form.elements.editionScale.disabled,true);
  assert.equal(context.parameterData(form).editionScale,'focused');
  context.applyParametersToForm(form,{sections:['Data']});
  assert.equal(form.elements.includeNewspaper.checked,true);
  assert.equal(form.elements.editionScale.disabled,false);
  context.applyParametersToForm(form,{includeNewspaper:false,sections:['Data']});
  assert.equal(form.elements.includeNewspaper.checked,false);
  assert.deepEqual(form.sections,['Data']);
});
test('late reader responses cannot change the selected PDF or content', () => {
  const {context,images,nodes}=harness(['selectEdition']);
  const select=id=>context.selectEdition({id,title:id,url:`https://example.com/${id}.pdf`,pageCount:2});
  select('A'); const stale=images[0]; select('B'); stale.load(); stale.error();
  assert.equal(nodes.get('edition-pdf-link').href,'https://example.com/B.pdf');
  assert.equal(nodes.get('edition-pages').textContent,'');
  assert.equal(context.appState.activeEdition.id,'B'); assert.equal(images.length,4);
});
test('logout unloads audio and clears private reader/player state', () => {
  const {context,nodes}=harness(['clearPrivateInterface']);
  const audio=context.byId('episode-audio'); audio.src='https://example.com/private.mp3'; audio.paused=false;
  context.clearPrivateInterface();
  assert.equal(audio.paused,true); assert.equal(audio.src,undefined);
  assert.equal(context.appState.readerToken,null); assert.equal(context.appState.authorized,false);
  assert.equal(nodes.get('mini-player').hidden,true);
});
test('monitor refresh has consistent page size and ignores logged-out responses', async () => {
  const {context}=harness(['refreshMonitor']); let observedLimit, renders=0;
  Object.assign(context,{doc(){},collection(){},orderBy(){},limit(n){observedLimit=n;},query(){},
    async getDoc(){return {};},async getDocs(){return {};},renderRunner(){renders++;},renderRunRequests(){renders++;}});
  await context.refreshMonitor(); assert.equal(observedLimit,100); assert.equal(renders,2);
  const pending=context.refreshMonitor(); context.appState.authorized=false;
  await pending; assert.equal(renders,2);
});
test('transcript follows only a changed segment without scrolling the page', () => {
  const {context}=harness(['formatPlaybackTime','setRangeProgress','syncPlayer']);
  const audio=context.byId('episode-audio'); Object.assign(audio,{duration:60,currentTime:2,paused:false,ended:false});
  const container=context.byId('player-details'); Object.assign(container,{offsetTop:0,scrollTop:0,clientHeight:50});
  const segment={dataset:{startMs:'0',nextStartMs:'5000'},offsetTop:100,offsetHeight:20,classList:{toggle(){}}};
  context.document.querySelectorAll=()=>[segment]; context.appState.playerDetailMode='transcript';
  context.syncPlayer(); context.syncPlayer(); assert.equal(container.scrolls,1);
});
test('live snapshots retain previously loaded older episodes', () => {
  const {context}=harness(['renderEpisodes']); context.renderEpisodeArchives=()=>{};
  context.appState.olderEpisodes=new Map([['old',{id:'old'}]]);
  context.renderEpisodes({docs:[{id:'new',data(){return {title:'New edition'};}}]});
  assert.equal(context.appState.episodeRecords.length,2);
});

test('active listening extends inactivity but never the absolute session limit', async () => {
  const {context}=harness(['resetIdleTimer','checkIdleTimer']); let signouts=0;
  context.IDLE_LIMIT_MS=15*60*1000;
  context.Date={now(){return 100000;}};
  context.appState.sessionEndsAt=200000; context.appState.idleAt=1;
  Object.assign(context.byId('episode-audio'),{src:'https://example.com/audio.mp3',paused:false,ended:false});
  context.signOutUser=async()=>{signouts++;};
  await context.checkIdleTimer(); assert.equal(signouts,0);
  assert.equal(context.appState.idleAt,200000);
  context.Date.now=()=>200001;
  await context.checkIdleTimer(); assert.equal(signouts,1);
});

test('resume bookmarks contain only IDs and positions and disappear at logout', () => {
  const {context}=harness(['playbackSessionKey','playbackBookmarks','savePlaybackPosition','clearPlaybackSession']);
  const storage=new Map(); const actions=new Map();
  Object.assign(context,{sessionStorage:{getItem:key=>storage.get(key),setItem:(key,value)=>storage.set(key,value),removeItem:key=>storage.delete(key)},
    navigator:{mediaSession:{setActionHandler:(key,value)=>actions.set(key,value),setPositionState(){}}}});
  context.appState.activeEpisode={id:'synthetic-episode',title:'Private title',audioURL:'https://example.com/private.mp3'};
  context.appState.positionRestored=true;
  Object.assign(context.byId('episode-audio'),{duration:100,currentTime:25,paused:true,ended:false});
  context.savePlaybackPosition(true);
  const entries=JSON.parse(storage.values().next().value);
  assert.deepEqual(entries,[{id:'synthetic-episode',seconds:25}]);
  context.appState.positionRestored=false; context.appState.playbackStopped=true;
  context.savePlaybackPosition(true); assert.deepEqual(JSON.parse(storage.values().next().value),[]);
  context.clearPlaybackSession(); assert.equal(storage.size,0);
  assert.equal(context.navigator.mediaSession.metadata,null);
  assert.equal([...actions.values()].every(value=>value===null),true);
});

test('resume rejects stale media events and out-of-range positions', () => {
  const {context}=harness(['restorePlaybackPosition']);
  context.playbackBookmarks=()=>[{id:'selected',seconds:25}];
  context.appState.activeEpisode={id:'selected'};
  const audio=context.byId('episode-audio');
  Object.assign(audio,{duration:100,currentTime:0,src:'new',currentSrc:'old'});
  context.restorePlaybackPosition(); assert.equal(audio.currentTime,0);
  audio.currentSrc='new'; context.restorePlaybackPosition(); assert.equal(audio.currentTime,25);
  context.appState.positionRestored=false; audio.duration=20; audio.currentTime=0;
  context.restorePlaybackPosition(); assert.equal(audio.currentTime,0);
});

test('Media Session is feature-detected and never leaks private URLs', () => {
  const {context}=harness(['updateMediaMetadata','syncMediaSession']);
  context.appState.activeEpisode={id:'A',title:'Synthetic title',audioURL:'https://example.com/private.mp3'};
  context.updateMediaMetadata(); context.syncMediaSession(); // Unsupported browser.
  const handlers=new Map();
  context.navigator={mediaSession:{setActionHandler:(action,handler)=>handlers.set(action,handler),setPositionState(){throw Error('Unsupported');}}};
  context.MediaMetadata=function(data){Object.assign(this,data);};
  let invoked=0; context.appState.mediaActions={play(){invoked++;}};
  context.updateMediaMetadata();
  assert.equal(JSON.stringify(context.navigator.mediaSession.metadata).includes('https:'),false);
  handlers.get('play')({}); assert.equal(invoked,1);
  context.appState.authorized=false; handlers.get('play')({}); assert.equal(invoked,1);
});

test('chapters use explicit heading flags, not guessed prose', () => {
  const {context}=harness(['renderPlayerChapters']);
  context.appState.activeEpisode={transcript:[{text:'AI',startMs:5000,isHeading:true},{text:'Ordinary prose',startMs:9000}]};
  context.renderPlayerChapters();
  const chapters=context.byId('player-chapters');
  assert.equal(chapters.children.length,2); assert.equal(chapters.children[1].value,'5');
  context.appState.activeEpisode={transcript:[{text:'Legacy',startMs:5000}]};
  context.renderPlayerChapters(); assert.equal(chapters.parentElement.hidden,true);
});

test('time estimates use observed completed runs and never failed attempts', () => {
  const {context}=harness(['runEstimateRange']);
  context.SESSION_MAX_MS=3600000; context.dateValue=value=>value?new Date(value):null;
  context.appState.runRequests=[];
  let range=context.runEstimateRange('Example'); assert.equal(range.observed,false);
  context.appState.runRequests=[20,30,40].map(minutes=>({status:'published',parameters:{runName:'Example'},startedAt:1000,finishedAt:1000+minutes*60000}));
  context.appState.runRequests.push({status:'failed',parameters:{runName:'Example'},startedAt:1000,finishedAt:100000000});
  range=context.runEstimateRange('Example'); assert.equal(range.observed,true);
  assert.equal(range.low,20*60000); assert.equal(range.high,40*60000);
  assert.equal(context.runEstimateRange('Other').observed,false);
});
