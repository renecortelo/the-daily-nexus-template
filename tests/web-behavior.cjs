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
    appState: {authorized:true, authEpoch:0, user:{uid:'synthetic-user'}, subscriptions:[]},
    byId(id){if(!nodes.has(id)) nodes.set(id,node()); return nodes.get(id);},
    document: {querySelectorAll(){return []; }},
    element(){const image=node(); images.push(image); return image;},
    applyEditionZoom(){}, clearSubscriptions(){}, updateCloudClockStatus(){}, updateArchiveButtons(){},
    clearPlaybackSession(){}, syncMediaSession(){}, savePlaybackPosition(){},
    clearConsoleSession(){}, clearPrivateForms(){}, clearFavoriteSession(){}, persistConsoleSession(){},
    window: {clearTimeout(){}, matchMedia(){return {matches:false};}},
    showAlert(){}, firebaseErrorMessage(){return 'safe error';},
  };
  vm.createContext(context);
  vm.runInContext(names.map(extract).join('\n'),context);
  return {context,nodes,images};
}

function storageFixture() {
  const data = new Map();
  return {data, getItem:key=>data.get(key) ?? null,
    setItem:(key,value)=>data.set(key,value), removeItem:key=>data.delete(key)};
}

test('wake retry never creates a generation and concurrent wakes share a dispatch', async () => {
  const {context}=harness(['requestRunnerWake']); let resolveWake, calls=0, writes=0;
  context.renderRunRequestList=()=>{};
  context.addDoc=()=>{writes++;};
  context.cloudClockRequest=()=>{calls++; return new Promise(resolve=>{resolveWake=resolve;});};
  const first=context.requestRunnerWake('synthetic-request');
  const second=context.requestRunnerWake('synthetic-other');
  assert.equal(calls,1); resolveWake({status:'dispatched'}); await Promise.all([first,second]);
  assert.equal(writes,0); assert.equal(context.appState.wakeStates.get('synthetic-request'),'confirmed');
  context.cloudClockRequest=async()=>{throw Error('Synthetic network outage');};
  await context.requestRunnerWake('synthetic-request');
  assert.equal(context.appState.wakeStates.get('synthetic-request'),'unconfirmed');
  assert.equal(writes,0);
});

test('double submission queues once and failed wake does not advise another generation', async () => {
  const {context}=harness(['queueGeneration','requestRunnerWake']); let release, writes=0;
  const alerts=[]; context.showAlert=(message)=>alerts.push(message);
  context.renderRunRequestList=()=>{}; context.FormData=function() {this.get=()=> '2026-10-08';};
  context.generationForm={querySelector(){return {};},elements:{requestedDate:{max:'2026-10-09'}}};
  context.validateParameters=()=>({}); context.parameterData=()=>({});
  context.collection=()=>({}); context.serverTimestamp=()=>({});
  context.addDoc=()=>{writes++; return new Promise(resolve=>{release=resolve;});};
  context.cloudClockRequest=async()=>{throw Error('Synthetic network outage');};
  const event={preventDefault(){}};
  const first=context.queueGeneration(event); await context.queueGeneration(event);
  assert.equal(writes,1); release({id:'synthetic-request'}); await first;
  assert.match(alerts.at(-1),/WAKE RUNNER/); assert.equal(writes,1);
  assert.equal(context.appState.generationSubmitting,false);
});

test('requeue only accepts terminal records and each retry has one immutable request', async () => {
  const {context}=harness(['requeueRequest']); let writes=0, release;
  context.collection=()=>({}); context.serverTimestamp=()=>({}); context.requestRunnerWake=async()=>{};
  context.appState.runRequests=[{id:'synthetic',status:'failed',requestedDate:'2026-10-08',parameters:{}}];
  context.addDoc=()=>{writes++; return new Promise(resolve=>{release=resolve;});};
  const first=context.requeueRequest('synthetic'); await context.requeueRequest('synthetic');
  assert.equal(writes,1); release({id:'new-synthetic'}); await first;
  assert.equal(context.appState.runRequests[0].status,'failed');
  context.appState.runRequests[0].status='queued'; await context.requeueRequest('synthetic');
  assert.equal(writes,1);
});

test('retired archive media is not selectable and stale cached selections check the server', async () => {
  const {context}=harness(['archiveMediaURL','availableArchiveRecord']);
  context.safePrivateURL=(url)=>url; context.doc=()=>({}); context.renderEpisodeArchives=()=>{};
  const record={id:'synthetic-edition',status:'published',mediaState:'retired',audioUrl:'https://test.web.app/p/example/audio/edition.mp3'};
  context.appState.episodeRecords=[{...record,mediaState:'available'}];
  assert.equal(context.archiveMediaURL(record,'.mp3'),null);
  context.getDocFromServer=async()=>({exists:()=>true,data:()=>record});
  await assert.rejects(context.availableArchiveRecord(record.id,'.mp3'),/retired/);
  assert.equal(context.appState.episodeRecords[0].mediaState,'retired');
  context.getDocFromServer=async()=>{throw Error('Synthetic network outage');};
  await assert.rejects(context.availableArchiveRecord(record.id,'.mp3'),/network/);
});

test('late archive responses cannot override newer selections or load after logout', async () => {
  const {context}=harness(['openArchivedEpisode']); const pending=new Map(), selected=[];
  context.availableArchiveRecord=id=>new Promise(resolve=>pending.set(id,resolve));
  context.archiveMediaURL=record=>record.audioUrl; context.selectEpisode=record=>selected.push(record.id);
  const first=context.openArchivedEpisode({id:'first'}); const second=context.openArchivedEpisode({id:'second'});
  pending.get('second')({audioUrl:'second.mp3'}); await second;
  pending.get('first')({audioUrl:'first.mp3'}); await first;
  assert.deepEqual(selected,['second']);
  const third=context.openArchivedEpisode({id:'third'});
  context.appState.audioSelectionToken=null; context.appState.authorized=false;
  pending.get('third')({audioUrl:'third.mp3'}); await third;
  assert.deepEqual(selected,['second']);
});

test('historic references hide personalized parameters and reject credential-bearing URLs', () => {
  const {context}=harness(['minimizeReferenceURL','safeReference']);
  Object.assign(context,{URL});
  const reference=context.safeReference('Synthetic source - https://example.com/story?id=12&utm_source=mail&cs_email=synthetic%40example.com&regi_id=synthetic#footer');
  assert.equal(reference.text,'Synthetic source');
  assert.equal(reference.url,'https://example.com/story?id=12');
  for (const value of ['https://example.com/?token=synthetic','https://example.com/?upn=synthetic',
    'https://synthetic:synthetic@example.com/story','https://example.com:invalid/story']) {
    assert.equal(context.safeReference(`Synthetic - ${value}`),null);
  }
  assert.equal(context.minimizeReferenceURL('http://example.com/story'),null);
});

test('newsletter-only references retain coverage and lineage even without public URLs', () => {
  const {context,nodes}=harness(['renderPlayerDetails','minimizeReferenceURL','safeReference']);
  context.element=(tag,className,text)=>({tag,className,textContent:text});
  context.appState.activeEpisode={references:[],sourceMix:{
    mode:'newsletter_only',newsletter_messages:3,newsletter_backed_stories:7,
    extracted_story_records:9,duplicate_story_records:2,omitted_news_stories:1,
    source_passages_cited:6,
  },episodeBudget:{selected_news_stories:6,available_stories:7,represented_newsletters:3}};
  context.renderPlayerDetails('references');
  const texts=nodes.get('player-details').children.map(item=>item.textContent).join('\n');
  assert.match(texts,/3 NEWSLETTERS/);
  assert.match(texts,/9 EXTRACTED RECORDS/);
  assert.match(texts,/2 DUPLICATES MERGED/);
  assert.match(texts,/COUNTS ARE NOT A GUARANTEE/);
  assert.match(texts,/Newsletter evidence can still support/);
});

test('reload and refreshed ID token retain the original absolute and idle deadlines', async () => {
  const {context}=harness(['consoleSessionFor']);
  const storage=storageFixture(); const started=Date.parse('2026-10-09T06:00:00Z');
  Object.assign(context,{SESSION_MAX_MS:3600000,IDLE_LIMIT_MS:900000,CONSOLE_SESSION_KEY:'synthetic-session'});
  context.window.sessionStorage=storage; context.Date={parse:Date.parse,now:()=>started+600000};
  storage.setItem('synthetic-session',JSON.stringify({authenticatedAt:started,idleAt:started+840000}));
  const user={async getIdTokenResult(){return {authTime:new Date(started).toUTCString(),issuedAtTime:new Date(started+600000).toUTCString()};}};
  const first=await context.consoleSessionFor(user);
  context.Date.now=()=>started+660000;
  const second=await context.consoleSessionFor(user);
  assert.equal(first.sessionEndsAt,started+3600000);
  assert.equal(second.sessionEndsAt,first.sessionEndsAt);
  assert.equal(second.idleAt,started+840000);
  context.Date.now=()=>started+840001;
  await assert.rejects(context.consoleSessionFor(user),/expired/);
});

test('expired, missing and future sign-in times fail closed without opening private views', async () => {
  const {context}=harness(['consoleSessionFor']); const started=Date.parse('2026-10-09T06:00:00Z');
  Object.assign(context,{SESSION_MAX_MS:3600000,IDLE_LIMIT_MS:900000,CONSOLE_SESSION_KEY:'synthetic-session'});
  context.window.sessionStorage=storageFixture(); context.Date={parse:Date.parse,now:()=>started+3600001};
  await assert.rejects(context.consoleSessionFor({async getIdTokenResult(){return {authTime:new Date(started).toUTCString()};}}),/expired/);
  await assert.rejects(context.consoleSessionFor({async getIdTokenResult(){return {};}}),/could not be verified/);
  await assert.rejects(context.consoleSessionFor({async getIdTokenResult(){return {authTime:new Date(started+7200000).toUTCString()};}}),/could not be verified/);
});

test('late authentication response cannot reopen the console after logout', async () => {
  const {context}=harness(['handleAuthState']); let resolveSession, opens=0, owners=0;
  context.consoleSessionFor=()=>new Promise(resolve=>{resolveSession=resolve;});
  context.verifyOwner=async()=>{owners++;}; context.showApp=()=>{opens++;};
  context.showAuth=()=>{}; context.setAuthStatus=()=>{};
  const pending=context.handleAuthState({uid:'synthetic-user'});
  await context.handleAuthState(null);
  resolveSession({sessionEndsAt:Date.now()+3600000,idleAt:Date.now()+900000});
  await pending; assert.equal(opens,0); assert.equal(owners,0);
});

test('logout empties private generation and schedule controls and favorite labels', () => {
  const {context}=harness(['clearPrivateForms']); const cleared=[];
  const form=()=>({elements:{requestedDate:{value:'2026-10-08',max:'2026-10-09'},runName:{value:'Synthetic private run'}},reset(){this.elements.runName.value='';}});
  context.generationForm=form(); context.scheduleForm=form();
  context.setSections=(f,sections)=>{cleared.push(sections.length);};
  context.syncHostControls=()=>{}; context.syncNewspaperControls=()=>{};
  context.globalAlert=context.byId('global-alert'); context.globalAlert.textContent='Synthetic private message';
  context.byId('profile-name').value='Synthetic favorite';
  context.clearPrivateForms();
  assert.equal(context.generationForm.elements.runName.value,'');
  assert.equal(context.scheduleForm.elements.runName.value,'');
  assert.deepEqual(cleared,[0,0]); assert.equal(context.byId('profile-name').value,'');
  assert.equal(context.globalAlert.textContent,''); assert.equal(context.globalAlert.hidden,true);
});

test('new favorites are session-only; device retention is explicit and reversible', () => {
  const {context}=harness(['profileStorageKey','restoreFavoritePreference','savedProfiles','storeProfiles','changeFavoriteStorage','clearFavoriteSession']);
  const local=storageFixture(), session=storageFixture();
  Object.assign(context.window,{localStorage:local,sessionStorage:session});
  context.restoreFavoritePreference(); assert.equal(context.appState.rememberFavorites,false);
  context.storeProfiles([{name:'Synthetic favorite',parameters:{gmailLabel:'Example/News'}}]);
  assert.equal(local.data.size,0); assert.equal(session.data.size,1);
  context.byId('remember-favorites').checked=true; context.changeFavoriteStorage();
  assert.equal(local.data.size,2); assert.equal(session.data.size,0);
  context.byId('remember-favorites').checked=false; context.changeFavoriteStorage();
  assert.equal(local.data.size,0); assert.equal(session.data.size,1);
  context.clearFavoriteSession(); assert.equal(session.data.size,0);
});

test('legacy device favorites remain visible and are not silently erased on logout', () => {
  const {context}=harness(['profileStorageKey','restoreFavoritePreference','savedProfiles','clearFavoriteSession']);
  const local=storageFixture(), session=storageFixture();
  Object.assign(context.window,{localStorage:local,sessionStorage:session});
  local.setItem(context.profileStorageKey(),JSON.stringify([{name:'Synthetic existing favorite'}]));
  context.restoreFavoritePreference();
  assert.equal(context.appState.rememberFavorites,true);
  assert.equal(context.savedProfiles()[0].name,'Synthetic existing favorite');
  context.clearFavoriteSession(); assert.equal(local.data.size,1);
});

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

test('returning to an idle tab cannot revive an expired console with a click', () => {
  const {context}=harness(['resetIdleTimer']); let signouts=0;
  context.IDLE_LIMIT_MS=900000; context.Date={now:()=>100000};
  context.appState.idleAt=99999; context.appState.sessionEndsAt=200000;
  context.signOutUser=()=>{signouts++;};
  context.resetIdleTimer();
  assert.equal(signouts,1); assert.equal(context.appState.idleAt,99999);
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
