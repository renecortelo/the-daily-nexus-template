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
    return { children: [], textContent: '', className: '', hidden: false,
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
    window: {clearTimeout(){}, matchMedia(){return {matches:false};}},
    showAlert(){}, firebaseErrorMessage(){return 'safe error';},
  };
  vm.createContext(context);
  vm.runInContext(names.map(extract).join('\n'),context);
  return {context,nodes,images};
}
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
