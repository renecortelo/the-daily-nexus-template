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
      attributes:{}, style: {setProperty(){}}, setAttribute(name,value){this.attributes[name]=value;}, removeAttribute(name){delete this[name];delete this.attributes[name];},
      replaceChildren(){this.children=[]; this.textContent='';}, append(child){this.children.push(child);},
      addEventListener(event,callback){this[event]=callback;}, remove(){},
      pause(){this.paused=true;}, load(){}, scrollTo(){this.scrolls=(this.scrolls||0)+1;},
    };
  }
  const context = { URL, Date, Map, console, ARCHIVE_PAGE_SIZE: 100,
    appState: {authorized:true, authEpoch:0, user:{uid:'synthetic-user'}, subscriptions:[]},
    byId(id){if(!nodes.has(id)) nodes.set(id,node()); return nodes.get(id);},
    document: {querySelectorAll(){return []; }},
    element(_tag,_class,text){const image=node(); if(text!==undefined) image.textContent=text; images.push(image); return image;},
    applyEditionZoom(){}, clearSubscriptions(){}, updateCloudClockStatus(){}, updateArchiveButtons(){},
    clearEditionControls(){},
    syncParameterControls(form){context.syncHostControls?.(form);context.syncNewspaperControls?.(form);},
    clearPlaybackSession(){}, syncMediaSession(){}, savePlaybackPosition(){},
    clearConsoleSession(){}, clearPrivateForms(){}, clearFavoriteSession(){}, persistConsoleSession(){},
    window: {clearTimeout(){}, matchMedia(){return {matches:false};}},
    showAlert(){}, dismissAlert(){}, firebaseErrorMessage(){return 'safe error';},
  };
  vm.createContext(context);
  const parameterConstants=source.slice(source.indexOf('const GENERATION_PARAMETER_DEFAULTS'),source.indexOf('const appState'));
  vm.runInContext(parameterConstants+'\n'+names.map(extract).join('\n'),context);
  return {context,nodes,images};
}

function storageFixture() {
  const data = new Map();
  return {data, getItem:key=>data.get(key) ?? null,
    setItem:(key,value)=>data.set(key,value), removeItem:key=>data.delete(key)};
}

test('resource visibility distinguishes measurements, forecasts and unknown account balances', () => {
  const {context,nodes}=harness(['metricNumber','renderResourceSummary']);
  context.timestampText=()=> 'A TIME'; context.durationText=value=>`${value/1000}s`;
  context.appState.schedules=new Map([['synthetic',{enabled:true,weekdays:[0,1,2,3,4,5,6]}]]);
  context.appState.resourceProfile={status:'completed',elapsed_seconds:600,at:'2026-10-09T05:00:00Z',
    measurements:{setup_seconds:40,ready_by_late_seconds:60},ready_by_at:'2026-10-09T04:00:00Z',
    stage_seconds:{'7':500,'private':20},resources:{new_paper_bytes:0,new_preview_bytes:1024},
    recent:[...Array(3).fill({status:'completed',elapsed_seconds:600}),{status:'failed',elapsed_seconds:400}]};
  context.renderResourceSummary();
  const text=nodes.get('resource-summary').children.map(child=>child.textContent).join('\n');
  assert.match(text,/INCLUDING FAILED ATTEMPTS/); assert.match(text,/7 scheduled editions ≈ 70 min/);
  assert.match(text,/ACCOUNT ACTIONS BALANCE \/\/ UNKNOWN/); assert.match(text,/NOT remaining allowance/);
  assert.match(text,/NEW PDF \/\/ 0.0 MiB/); assert.doesNotMatch(text,/private/);
  context.appState.resourceProfile=null; context.renderResourceSummary();
  assert.match(nodes.get('resource-summary').children[0].textContent,/NO TERMINAL MEASUREMENT/);
});

test('unsuccessful scheduled measurement never claims ready-by delivery', () => {
  const {context,nodes}=harness(['metricNumber','renderResourceSummary']);
  context.timestampText=()=> 'A TIME'; context.durationText=()=> 'A DURATION';
  context.appState.resourceProfile={status:'failed',elapsed_seconds:400,ready_by_at:'2026-10-09T04:00:00Z',
    measurements:{ready_by_late_seconds:0},recent:[{status:'failed',elapsed_seconds:400}]};
  context.renderResourceSummary();
  const text=nodes.get('resource-summary').children.map(child=>child.textContent).join('\n');
  assert.match(text,/NOT COMPLETED; NO AUTOMATIC RETRY/); assert.doesNotMatch(text,/COMPLETED WITHIN TARGET/);
  assert.doesNotMatch(text,/7-DAY GENERATION REFERENCE/);
});

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
  const {context}=harness(['dismissAlert','clearPrivateForms']); const cleared=[];
  const form=()=>({elements:{requestedDate:{value:'2026-10-08',max:'2026-10-09'},runName:{value:'Synthetic private run'}},reset(){this.elements.runName.value='';}});
  context.generationForm=form(); context.scheduleForm=form();
  context.setSections=(f,sections)=>{cleared.push(sections.length);};
  context.syncHostControls=()=>{}; context.syncNewspaperControls=()=>{};
  context.globalAlert=context.byId('global-alert'); context.byId('global-alert-message').textContent='Synthetic private message';
  context.byId('profile-name').value='Synthetic favorite';
  context.clearPrivateForms();
  assert.equal(context.generationForm.elements.runName.value,'');
  assert.equal(context.scheduleForm.elements.runName.value,'');
  assert.deepEqual(cleared,[0,0]); assert.equal(context.byId('profile-name').value,'');
  assert.equal(context.byId('global-alert-message').textContent,''); assert.equal(context.globalAlert.hidden,true);
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
  const {context}=harness(['parameterData','syncNewspaperControls','applyParametersToForm','parseSections']);
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
  assert.deepEqual(Array.from(form.sections),['Data']);
});

function parameterFormFixture(scheduled=false) {
  const choices={hostCount:['1','2'],soloName:['Dalia','Nox'],dialogueStyle:['broadcast','conversation'],
    primaryVoice:['af_heart','bf_emma','af_bella','am_michael','am_eric','am_puck'],
    secondaryVoice:['af_heart','bf_emma','af_bella','am_michael','am_eric','am_puck'],
    primaryTone:['warm','neutral','dry_wit','fun','formal'],secondaryTone:['dry_wit','neutral','warm','fun','formal'],
    editionScale:['standard','focused','comprehensive'],evidenceMode:['newsletter_first','newsletter_only']};
  if(scheduled) choices.dateMode=['previous_day','today'];
  const controls={};
  for(const [name,values] of Object.entries(choices)) {
    controls[name]={value:values[0],disabled:false,options:values.map(value=>({value,defaultSelected:false,
      dataset:{gender:value.startsWith('am_')?'Male':'Female'}})),listeners:[],
      addEventListener(event,callback){this.listeners.push({event,callback});}};
    Object.defineProperty(controls[name],'selectedOptions',{get(){return this.options.filter(option=>option.value===this.value);}});
  }
  for(const name of ['runName','gmailLabel','requestedDate','scheduleId','name','startTime','readyBy','timezone']) controls[name]={value:''};
  for(const name of ['includeTih','includeNewspaper','enabled']) controls[name]={checked:true,listeners:[],
    addEventListener(event,callback){this.listeners.push({event,callback});}};
  controls.namedItem=name=>controls[name];
  const label=name=>({querySelector(){return controls[name];},classList:{toggle(){}}});
  const labels={'[data-solo-control]':label('soloName'),'[data-duo-control]':label('dialogueStyle')};
  const weekdays=Array.from({length:7},(_,value)=>({value:String(value),checked:false}));
  return {elements:controls,dataset:{},sections:[],weekdays,
    querySelector(selector){return labels[selector];},querySelectorAll(selector){
      return selector==='[data-secondary-control]'?[label('secondaryVoice'),label('secondaryTone')]:weekdays;
    },scrollIntoView(){this.scrolled=true;}};
}

function parameterHarness(extra=[]) {
  const result=harness(['parameterData','applyParametersToForm','parseSections','syncParameterControls',
    'syncHostControls','syncNewspaperControls',...extra]);
  result.context.sectionValues=form=>form.sections;
  result.context.setSections=(form,sections)=>{form.sections=Array.from(sections);};
  return result;
}

test('shared forms retain inactive preferences and always publish without serializing timing or identity', () => {
  const {context}=parameterHarness();
  const parameters={runName:' Synthetic run ',gmailLabel:'Example/News',hostCount:1,soloName:'Nox',
    dialogueStyle:'conversation',primaryVoice:'am_eric',primaryTone:'formal',secondaryVoice:'am_puck',secondaryTone:'fun',
    includeNewspaper:false,includeTih:false,editionScale:'focused',evidenceMode:'newsletter_only',sections:['Data Engineering'],publish:false};
  for(const scheduled of [false,true]) {
    const form=parameterFormFixture(scheduled);
    context.applyParametersToForm(form,parameters);
    assert.equal(form.elements.hostCount.disabled,false);
    assert.equal(form.elements.secondaryVoice.disabled,true); assert.equal(form.elements.secondaryTone.disabled,true);
    assert.equal(form.elements.dialogueStyle.disabled,true); assert.equal(form.elements.editionScale.disabled,true);
    const read=context.parameterData(form);
    assert.equal(read.secondaryVoice,'am_puck'); assert.equal(read.secondaryTone,'fun');
    assert.equal(read.dialogueStyle,'conversation'); assert.equal(read.editionScale,'focused');
    assert.equal(read.publish,true); assert.equal(read.runName,'Synthetic run');
    assert.equal(read.dateMode,scheduled?'previous_day':'today');
    assert(!('timezone' in read)); assert(!('scheduleId' in read)); assert(!('requestedDate' in read));
    form.elements.hostCount.value='2'; context.syncParameterControls(form);
    assert.equal(form.elements.soloName.disabled,true); assert.equal(form.elements.dialogueStyle.disabled,false);
    assert.equal(form.elements.secondaryVoice.disabled,false); assert.equal(form.elements.primaryVoice.value,'af_heart');
    assert.equal(form.elements.secondaryVoice.value,'am_puck');
  }
});

test('partial legacy records reset omitted fields instead of inheriting a previous mailbox or host', () => {
  const {context}=parameterHarness();
  const form=parameterFormFixture(true); form.elements.requestedDate.value='2026-10-08';
  form.elements.startTime.value='05:30';
  context.applyParametersToForm(form,{runName:'Earlier run',gmailLabel:'Example/Old',hostCount:2,
    primaryVoice:'af_bella',primaryTone:'formal',secondaryVoice:'am_eric',secondaryTone:'formal',dateMode:'today',
    includeTih:false,includeNewspaper:false,editionScale:'comprehensive',sections:['Old']});
  context.applyParametersToForm(form,{sections:['New'],timezone:'Asia/Tokyo',requestedDate:'2026-01-01',startTime:'01:00'});
  const read=context.parameterData(form);
  assert.equal(read.runName,''); assert.equal(read.gmailLabel,''); assert.equal(read.hostCount,1);
  assert.equal(read.primaryTone,'warm'); assert.equal(read.secondaryTone,'dry_wit');
  assert.equal(read.primaryVoice,'af_heart'); assert.equal(read.secondaryVoice,'am_michael');
  assert.equal(read.dateMode,'previous_day'); assert.equal(read.includeTih,true); assert.equal(read.includeNewspaper,true);
  assert.equal(read.editionScale,'standard'); assert.deepEqual(form.sections,['New']);
  assert.equal(form.elements.requestedDate.value,'2026-10-08'); assert.equal(form.elements.startTime.value,'05:30');
  form.elements.primaryTone.options[1].defaultSelected=true;
  context.applyParametersToForm(form,{}); assert.equal(form.elements.primaryTone.value,'neutral');
});

test('invalid saved parameters fail preflight without partially replacing current edits', () => {
  const {context}=parameterHarness(); const form=parameterFormFixture();
  context.applyParametersToForm(form,{runName:'Keep this',gmailLabel:'Example/Keep',sections:['Keep']});
  const before=JSON.stringify(context.parameterData(form));
  for(const invalid of [null,[],{runName:'Replace',primaryTone:'unavailable'},{runName:'Replace',includeTih:'false'},
    {runName:'Replace',sections:'Not a list'},{runName:'Replace',sections:[7]},
    {runName:'Replace',sections:['AI','ai']},{runName:'Replace',sections:['Cloud/Software']},
    {runName:'Replace',sections:Array.from({length:11},(_,i)=>`Section ${i}`)}]) {
    assert.throws(()=>context.applyParametersToForm(form,invalid));
    assert.equal(JSON.stringify(context.parameterData(form)),before);
  }
});

test('schedule edit shares hydration but keeps schedule timing and days separate', () => {
  const {context}=parameterHarness(['editSchedule','fillSelect']); const form=parameterFormFixture(true);
  context.scheduleForm=form; context.appState.schedules=new Map([
    ['first',{name:'First',startTime:'05:15',readyBy:'06:30',timezone:'UTC',weekdays:[1,3],enabled:false,
      parameters:{gmailLabel:'Example/First',hostCount:2,secondaryTone:'formal',sections:['AI']}}],
    ['second',{name:'Second',parameters:{gmailLabel:'Example/Second'}}],
    ['invalid',{name:'Do not load',parameters:{primaryTone:'unavailable'}}],
  ]);
  const alerts=[]; context.showAlert=(...args)=>alerts.push(args);
  context.editSchedule('first'); assert.equal(form.elements.runName.value,'First');
  assert.equal(form.elements.scheduleId.value,'first'); assert.equal(form.elements.startTime.value,'05:15');
  assert.equal(form.elements.readyBy.value,'06:30');
  assert.deepEqual(form.weekdays.filter(day=>day.checked).map(day=>Number(day.value)),[1,3]);
  assert.equal(form.elements.enabled.checked,false);
  context.editSchedule('second'); assert.equal(form.elements.runName.value,'Second');
  assert.equal(form.elements.gmailLabel.value,'Example/Second'); assert.equal(form.elements.secondaryTone.value,'dry_wit');
  assert.equal(form.elements.hostCount.value,'1'); assert.deepEqual(form.sections,[]);
  const before=JSON.stringify(context.parameterData(form));
  context.editSchedule('invalid'); assert.equal(JSON.stringify(context.parameterData(form)),before);
  assert.equal(form.elements.scheduleId.value,'second'); assert.equal(alerts.at(-1)[1],true);
});

test('shared initialization is idempotent and host controls stay switchable on both screens', () => {
  const {context}=parameterHarness(['setupParameterForm']); let setups=0;
  context.setupSectionEditor=()=>{setups++;};
  for(const scheduled of [false,true]) {
    const form=parameterFormFixture(scheduled);
    context.setupParameterForm(form); context.setupParameterForm(form);
    for(const name of ['hostCount','soloName','includeNewspaper']) assert.equal(form.elements[name].listeners.length,1);
    form.elements.hostCount.value='2';form.elements.hostCount.listeners[0].callback();
    assert.equal(form.elements.hostCount.disabled,false); assert.equal(form.elements.soloName.disabled,true);
    assert.equal(form.elements.secondaryTone.disabled,false);
    form.elements.hostCount.value='1';form.elements.soloName.value='Nox';form.elements.soloName.listeners[0].callback();
    assert.equal(form.elements.soloName.disabled,false);assert.equal(form.elements.secondaryTone.disabled,true);
    assert.equal(form.elements.primaryVoice.value,'am_michael');
  }
  assert.equal(setups,2);
});

test('invalid favorite reports a persistent error without replacing the current favorite name', () => {
  const {context}=parameterHarness(['loadFavoriteProfile']);context.generationForm=parameterFormFixture();
  context.byId('profile-name').value='Keep this';context.byId('profile-picker').value='Invalid example';
  context.savedProfiles=()=>[{name:'Invalid example',parameters:{primaryTone:'unavailable'}}];
  const alerts=[];context.showAlert=(...args)=>alerts.push(args);
  context.loadFavoriteProfile();assert.equal(context.byId('profile-name').value,'Keep this');
  assert.equal(alerts.length,1);assert.equal(alerts[0][1],true);
});
test('late reader responses cannot change the selected PDF or content', () => {
  const {context,images,nodes}=harness(['selectEdition','renderEditionView','renderEditionPages']);
  const select=id=>context.selectEdition({id,title:id,url:`https://example.com/${id}.pdf`,pageCount:2});
  select('A'); const stale=images[0]; select('B'); stale.load(); stale.error();
  assert.equal(nodes.get('edition-pdf-link').href,'https://example.com/B.pdf');
  assert.equal(nodes.get('edition-pages').textContent,'');
  assert.equal(context.appState.activeEdition.id,'B'); assert.equal(images.length,4);
});

test('a failed first preview never hides a successful later page and URLs stay stable', () => {
  const {context,images,nodes}=harness(['selectEdition','renderEditionView','renderEditionPages']);
  const edition={id:'A',title:'Synthetic',url:'https://example.org/a.pdf',pageCount:2,
    previews:['https://untrusted.example/a.png']};
  context.selectEdition(edition); images[0].error(); images[1].load();
  assert.equal(nodes.get('edition-pages').textContent,'');
  assert.equal(images[1].src,'https://example.org/a-2.png');
  assert.equal(images[1].referrerPolicy,'no-referrer');
  context.selectEdition(edition);
  assert.equal(images[0].src,images[3].src);
});

test('reader distinguishes all failed previews and ignores callbacks after switching views', () => {
  const {context,images,nodes}=harness(['selectEdition','renderEditionView','renderEditionPages']);
  context.selectEdition({id:'A',title:'A',url:'https://example.org/a.pdf',pageCount:2});
  const stale=images[0]; images[0].error(); images[1].error();
  assert.match(nodes.get('edition-pages').textContent,/OPEN PDF/);
  context.selectEdition({id:'B',title:'B',url:'https://example.org/b.pdf',pageCount:2});
  stale.error(); assert.equal(nodes.get('edition-pages').textContent,'');
});

test('preview revision follows server metadata rather than each selection or an arbitrary URL', () => {
  const {context,images}=harness(['selectEdition','renderEditionView','renderEditionPages']);
  const edition={id:'A',title:'A',url:'https://example.org/a.pdf',pageCount:1,revision:1234};
  context.selectEdition(edition); context.selectEdition(edition);
  assert.equal(images[0].src,images[1].src);
  assert.equal(new URL(images[0].src).searchParams.get('_tdn_revision'),'1234');
  context.selectEdition({...edition,revision:1235}); assert.notEqual(images[2].src,images[1].src);
});

test('bounded readable copy strips unknown fields and renders markup as inert text', () => {
  const {context,images,nodes}=harness(['validatedEditionReading','selectEdition','renderEditionView','renderReadableEdition','setEditionMode','renderEditionPages']);
  const fixture={version:1,headline:'A useful edition',deck:'Context',lead:'Reporting',kicker:'Example',pullQuote:'',
    articles:[{title:'A story',section:'AI',standfirst:'',body:'<img src=x onerror=alert(1)> A full condition.',
      bullets:['A second fact.'],highlights:['A full condition.','Invented']}],executive:[],visuals:[],briefs:[],dataPoints:[],privateId:'hidden'};
  const reading=context.validatedEditionReading(fixture);
  assert(!('privateId' in reading)); assert.equal(reading.articles[0].highlights.length,1);
  context.selectEdition({id:'A',title:'A',url:'https://example.org/a.pdf',pageCount:2,reading});
  assert.equal(nodes.get('edition-pages').className,'edition-pages edition-readable');
  assert.equal(nodes.get('edition-readable').attributes['aria-pressed'],'true');
  assert(images.some(node=>node.textContent.includes('<img src=x onerror=alert(1)>')));
  assert(!images.some(node=>node.src)); // Text view downloads no preview image.
  context.setEditionMode('pages'); assert(images.some(node=>node.src));
  context.setEditionMode('readable'); assert.equal(nodes.get('edition-readable').attributes['aria-pressed'],'true');
  assert.equal(context.validatedEditionReading({...fixture,articles:[...fixture.articles,...Array(8).fill(fixture.articles[0])]}),null);
  assert.equal(context.validatedEditionReading({...fixture,lead:'x'.repeat(131073)}),null);
});

test('late owner newspaper selection cannot repopulate a logged out reader', async () => {
  const {context}=harness(['openArchivedEdition']); let release, selected=0;
  context.availableArchiveRecord=()=>new Promise(resolve=>release=resolve);
  context.selectEdition=()=>selected++;
  const pending=context.openArchivedEdition({id:'A'});
  context.appState.editionSelectionToken=null; context.appState.authEpoch++; context.appState.authorized=false;
  release({}); await pending; assert.equal(selected,0);
});
test('logout unloads audio and clears private reader/player state', () => {
  const {context,nodes}=harness(['clearPrivateInterface']);
  const audio=context.byId('episode-audio'); audio.src='https://example.com/private.mp3'; audio.paused=false;
  context.appState.runRequestRows=new Map([['synthetic',{data:{gmailLabel:'Example/Private'}}]]);
  context.byId('monitor-query-filter').value='Example/Private';context.byId('monitor-date-filter').value='2026-10-08';
  context.clearPrivateInterface();
  assert.equal(audio.paused,true); assert.equal(audio.src,undefined);
  assert.equal(context.appState.readerToken,null); assert.equal(context.appState.authorized,false);
  assert.equal(context.appState.runRequestRows.size,0);
  assert.equal(context.byId('monitor-query-filter').value,'');assert.equal(context.byId('monitor-date-filter').value,'');
  assert.equal(nodes.get('mini-player').hidden,true);
});

function monitorHarness(extra=[]) {
  const result=harness(['updateMonitorText','runRequestTimeline','runRequestDetail','createRunRequestRow',
    'updateRunRequestRow','updateRunRequestTimes','renderRunRequestList','renderRunRequests',
    'dateValue','durationText','timestampText',...extra]);
  const {context}=result; const roots=new Map();const clock={now:Date.parse('2026-10-10T06:00:00Z')};
  context.Date=class extends Date {static now(){return clock.now;}};
  context.document={activeElement:null,visibilityState:'visible'};
  const body={isConnected:true};context.document.activeElement=body;
  const metrics={inserts:0,removes:0,textWrites:0,created:0};
  class MonitorNode {
    constructor(tag='div',className='',text=''){
      this.tag=tag;this.className=className;this._text=text;this.children=[];this.dataset={};
      this.listeners={};this.parentNode=null;this.hidden=false;this.disabled=false;this.scrollTop=0;this.textWrites=0;
      metrics.created++;
    }
    get textContent(){return this.children.length?this.children.map(child=>child.textContent).join(''):this._text;}
    set textContent(value){this.textWrites++;metrics.textWrites++;this.children.forEach(child=>{child.parentNode=null;});this.children=[];this._text=value;}
    get isConnected(){return this.root||Boolean(this.parentNode?.isConnected);}
    contains(node){return node===this||this.children.some(child=>child.contains(node));}
    closest(selector){return selector==='.request-item'&&this.className==='request-item'?this:this.parentNode?.closest(selector);}
    append(...children){for(const child of children)this.insertBefore(child,null);}
    insertBefore(child,before){child.remove();const index=before?this.children.indexOf(before):this.children.length;
      assert(index>=0);this.children.splice(index,0,child);child.parentNode=this;metrics.inserts++;}
    remove(){if(!this.parentNode)return;if(this.contains(context.document.activeElement))context.document.activeElement=body;
      this.parentNode.children.splice(this.parentNode.children.indexOf(this),1);this.parentNode=null;metrics.removes++;}
    addEventListener(name,callback){(this.listeners[name]||=[]).push(callback);}
    click(){if(!this.disabled)for(const callback of this.listeners.click||[])callback();}
    focus(options){this.focusOptions=options;context.document.activeElement=this;}
  }
  context.element=(tag,className,text)=>new MonitorNode(tag,className,text);
  context.byId=id=>{if(!roots.has(id)){const node=new MonitorNode();node.root=true;roots.set(id,node);}return roots.get(id);};
  context.cloudClockEndpoint=()=> 'https://synthetic.example';
  context.appState.runRequests=[];context.appState.runRequestRows=new Map();context.appState.wakeStates=new Map();
  const calls=[];
  for(const action of ['requestRunnerWake','requeueRequest','deleteRunRequest'])context[action]=id=>calls.push([action,id]);
  return {...result,roots,clock,metrics,calls,body};
}

function monitorRequest(id,status='failed',extra={}) {
  return {id,status,requestedDate:'2026-10-09',parameters:{runName:'Synthetic edition',gmailLabel:'Example/News'},
    requestedAt:'2026-10-10T05:45:00Z',updatedAt:'2026-10-10T05:50:00Z',...extra};
}

test('unchanged monitor snapshots retain rows, buttons, focus, scroll and text nodes', () => {
  const {context,metrics}=monitorHarness();context.appState.runRequests=[monitorRequest('first'),monitorRequest('second','queued')];
  context.renderRunRequestList();const rows=context.appState.runRequestRows;
  const card=rows.get('first').card;const retry=rows.get('first').actions.children[0];retry.focus();
  const list=context.byId('run-request-list');list.scrollTop=120;
  const before={...metrics};
  for(let i=0;i<20;i++)context.renderRunRequests({docs:context.appState.runRequests.map(data=>({id:data.id,data:()=>({...data})}))});
  assert.equal(rows.get('first').card,card);assert.equal(rows.get('first').actions.children[0],retry);
  assert.equal(context.document.activeElement,retry);assert.equal(list.scrollTop,120);
  assert.deepEqual(metrics,before);assert.equal(retry.listeners.click.length,1);
});

test('local monitor ticks change only queued/running clocks without sorting or rebuilding terminal rows', () => {
  const {context,metrics,clock}=monitorHarness();context.appState.runRequests=[
    monitorRequest('queue','queued'),monitorRequest('run','running',{startedAt:'2026-10-10T05:55:00Z'}),
    monitorRequest('done','published',{finishedAt:'2026-10-10T05:59:00Z'})];
  context.renderRunRequestList();const before={...metrics},rows=context.appState.runRequestRows;
  const terminalWrites=rows.get('done').timeline.textWrites;
  clock.now+=1000;context.updateRunRequestTimes();
  assert.equal(metrics.created,before.created);assert.equal(metrics.inserts,before.inserts);assert.equal(metrics.removes,before.removes);
  assert.equal(metrics.textWrites-before.textWrites,2);assert.equal(rows.get('done').timeline.textWrites,terminalWrites);
  assert.match(rows.get('queue').timeline.textContent,/WAIT SO FAR 00:15:01/);
  assert.match(rows.get('run').detail.textContent,/RUNNING 00:05:01/);
  const unchanged={...metrics};context.updateRunRequestTimes();assert.deepEqual(metrics,unchanged);
  context.appState.authorized=false;clock.now+=1000;context.updateRunRequestTimes();context.renderRunRequestList();
  assert.deepEqual(metrics,unchanged);
});

test('monitor status and wake changes patch existing rows and retain correct single action handlers', () => {
  const {context,calls}=monitorHarness();context.appState.runRequests=[monitorRequest('one','queued')];
  context.renderRunRequestList();const row=context.appState.runRequestRows.get('one');const wake=row.wake;wake.focus();
  context.appState.wakeStates.set('one','requesting');context.renderRunRequestList();
  assert.equal(row.wake,wake);assert.equal(wake.disabled,true);assert.match(row.detail.textContent,/REQUESTING WAKE/);
  context.appState.wakeStates.set('one','confirmed');context.renderRunRequestList();wake.click();
  assert.deepEqual(calls,[['requestRunnerWake','one']]);assert.equal(wake.listeners.click.length,1);
  context.appState.runRequests=[monitorRequest('one','failed',{detail:'Synthetic failure',finishedAt:'2026-10-10T05:58:00Z'})];
  context.renderRunRequestList();assert.equal(context.appState.runRequestRows.get('one'),row);
  assert.equal(wake.isConnected,false);assert.equal(context.document.activeElement,row.card);
  assert.equal(row.detail.textContent,'Synthetic failure');assert.match(row.timeline.textContent,/FAILED/);
  row.actions.children[0].click();row.actions.children[1].click();
  assert.deepEqual(calls.slice(1),[['requeueRequest','one'],['deleteRunRequest','one']]);
  const retry=row.actions.children[0];context.appState.runRequests[0].status='expired';context.renderRunRequestList();
  assert.equal(row.actions.children[0],retry);
  context.appState.runRequests[0].status='published';context.renderRunRequestList();
  assert.equal(row.actions,null);assert.equal(row.detail.textContent,'PUBLISHED // AVAILABLE IN THE PRIVATE FEED');
});

test('monitor filters and sorting preserve retained identities and remove cached private rows', () => {
  const {context}=monitorHarness();const first=monitorRequest('first','queued',{updatedAt:'2026-10-10T05:51:00Z'});
  const second=monitorRequest('second','failed',{requestedDate:'2026-10-08'});
  context.appState.runRequests=[first,second];context.renderRunRequestList();
  const list=context.byId('run-request-list'),row=context.appState.runRequestRows.get('first');row.wake.focus();
  context.byId('monitor-sort-filter').value='oldest';context.renderRunRequestList();
  assert.equal(list.children[1],row.card);assert.equal(context.document.activeElement,row.wake);
  context.byId('monitor-sort-filter').value='newest';context.renderRunRequestList();
  assert.equal(list.children[0],row.card);assert.equal(context.document.activeElement,row.wake);
  assert.equal(row.wake.focusOptions.preventScroll,true);
  context.byId('monitor-date-filter').value='2026-10-09';context.renderRunRequestList();
  assert.equal(list.children.length,1);assert.equal(list.children[0],row.card);assert.equal(context.appState.runRequestRows.size,1);
  context.byId('monitor-date-filter').value='';context.byId('monitor-status-filter').value='failed';context.renderRunRequestList();
  assert.equal(row.card.isConnected,false);assert.equal(context.appState.runRequestRows.has('first'),false);
  context.byId('monitor-query-filter').value='does not match';context.renderRunRequestList();
  assert.equal(context.appState.runRequestRows.size,0);assert.match(list.textContent,/No requests match/);
  context.byId('monitor-query-filter').value='Example/News';context.renderRunRequestList();assert.equal(list.children.length,1);
  context.appState.runRequests=[];context.renderRunRequestList();assert.equal(context.appState.runRequestRows.size,0);
  assert.equal(list.textContent,'No queued tasks.');assert.equal(context.byId('monitor-scope').textContent,'0 RECENT REQUESTS LOADED');
});

test('request snapshots use immutable document IDs and cannot refill the monitor after logout', () => {
  const {context,metrics}=monitorHarness();context.renderRunRequests({docs:[
    {id:'first',data:()=>monitorRequest('spoofed')},{id:'second',data:()=>monitorRequest('spoofed')} ]});
  assert.deepEqual(Array.from(context.appState.runRequestRows.keys()).sort(),['first','second']);
  assert.deepEqual(Array.from(context.appState.runRequests,item=>item.id),['first','second']);
  context.appState.runRequestRows.clear();context.appState.runRequests=[];context.appState.authorized=false;
  const before={...metrics};context.renderRunRequests({docs:[{id:'late',data:()=>monitorRequest('late')}]});
  assert.equal(context.appState.runRequests.length,0);assert.equal(context.appState.runRequestRows.size,0);assert.deepEqual(metrics,before);
});

test('the existing activity timer never reconciles rows and suspends clock updates in hidden or signed-out tabs', () => {
  const {context,clock}=monitorHarness(['setupActivityTracking']);const timers=[];let details=0,renders=0;
  context.window.addEventListener=()=>{};context.document.addEventListener=()=>{};
  context.window.setInterval=callback=>timers.push(callback);context.resetIdleTimer=()=>{};context.checkIdleTimer=()=>{};
  context.updateRunnerDetail=()=>details++;
  context.appState.runRequests=[monitorRequest('run','running',{startedAt:'2026-10-10T05:55:00Z'})];
  context.renderRunRequestList();context.renderRunRequestList=()=>renders++;
  context.setupActivityTracking();assert.equal(timers.length,2);context.appState.runner={state:'running'};
  clock.now+=1000;timers[1]();assert.equal(details,1);assert.equal(renders,0);
  const row=context.appState.runRequestRows.get('run'),before=row.detail.textContent;
  context.document.visibilityState='hidden';clock.now+=1000;timers[1]();assert.equal(row.detail.textContent,before);
  context.document.visibilityState='visible';context.appState.authorized=false;timers[1]();assert.equal(details,1);
});
test('monitor refresh has consistent page size and ignores logged-out responses', async () => {
  const {context}=harness(['refreshMonitor']); let observedLimit, renders=0;
  Object.assign(context,{doc(){},collection(){},orderBy(){},limit(n){observedLimit=n;},query(){},
    async getDoc(){return {};},async getDocFromServer(){return {};},async getDocs(){return {};},renderResourceProfile(){},
    renderRunner(){assert(context.appState.monitorRefreshedAt instanceof Date);renders++;},renderRunRequests(){renders++;}});
  await context.refreshMonitor(); assert.equal(observedLimit,100); assert.equal(renders,2);
  const pending=context.refreshMonitor(); context.appState.authorized=false;
  await pending; assert.equal(renders,2);
});

test('a resource read outage does not suppress the runner and queue refresh', async () => {
  const {context}=harness(['refreshMonitor']); let renders=0;
  Object.assign(context,{doc(){},collection(){},orderBy(){},limit(){},query(){},
    async getDoc(){return {};}, async getDocs(){return {};}, async getDocFromServer(){throw new Error('synthetic outage');},
    renderRunner(){renders++;},renderRunRequests(){renders++;},renderResourceSummary(){renders++;}});
  await context.refreshMonitor();
  assert.equal(renders,3);assert.equal(context.appState.resourceReadUnavailable,true);
  assert(context.appState.monitorRefreshedAt instanceof Date);
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

function sectionHarness() {
  const {context}=harness(['parseSections','sectionEditor','sectionValues','reorderSections',
    'sectionDropBefore','moveSection','renderSectionTokens','setupSectionEditor','setSections']);
  function dom(tag='',className='',text='') {
    const node={tag,className,textContent:text,children:[],dataset:{},attributes:{},value:'',
      append(child){this.children.push(child);},replaceChildren(){this.children=[];},
      setAttribute(name,value){this.attributes[name]=value;},removeAttribute(name){delete this.attributes[name];},
      addEventListener(name,callback){this[name]=callback;},focus(){context.focused=this;},
      querySelector(selector){return this.querySelectorAll(selector)[0] || null;},
      querySelectorAll(selector){
        const descendants=this.children.flatMap(child=>[child,...child.querySelectorAll('*')]);
        return descendants.filter(child=>selector==='*' || (selector==='button' && child.tag==='button')
          || (selector==='textarea' && child.tag==='textarea')
          || (selector.startsWith('.') && child.className.split(' ').includes(selector.slice(1).split(':')[0])
              && (!selector.includes(':not') || !child.className.includes('dragging'))));
      },
    };
    node.classList={add(name){if(!node.className.split(' ').includes(name)) node.className+=` ${name}`;},
      remove(name){node.className=node.className.split(' ').filter(value=>value!==name).join(' ');},
      toggle(name,on){if(on) this.add(name);else this.remove(name);}};
    return node;
  }
  context.element=dom;
  const editor=dom(), list=dom('div','section-token-list'), input=dom('input','section-token-input');
  const textarea=dom('textarea'),status=dom('span','section-order-status');
  editor.append(list);editor.append(input);editor.append(textarea);editor.append(status);
  const form={querySelector:()=>editor};
  const chips=()=>list.querySelectorAll('.section-chip');
  const buttons=chip=>chip.querySelectorAll('button');
  return {context,form,list,input,textarea,status,chips,buttons};
}

test('section ordering preserves membership and rejects foreign or unknown drop targets', () => {
  const {context}=harness(['reorderSections']); const sections=['AI','Data','Cloud'];
  assert.deepEqual([...context.reorderSections(sections,'AI')],['Data','Cloud','AI']);
  assert.deepEqual([...context.reorderSections(sections,'Cloud','Data')],['AI','Cloud','Data']);
  for(const [moved,before] of [['Foreign','AI'],['AI','Missing'],['AI','AI']]) {
    assert.deepEqual([...context.reorderSections(sections,moved,before)],sections);
  }
  assert.deepEqual(sections,['AI','Data','Cloud']);
});

test('wrapped chip rows support drop at the beginning, middle and final position', () => {
  const {context}=harness(['sectionDropBefore']);
  const chip=(name,left,top)=>({dataset:{section:name},getBoundingClientRect:()=>({left,top,bottom:top+40,width:100})});
  const chips=[chip('A',0,0),chip('B',110,0),chip('C',0,50),chip('D',110,50)];
  assert.equal(context.sectionDropBefore(chips,10,20),chips[0]);
  assert.equal(context.sectionDropBefore(chips,100,20),chips[1]);
  assert.equal(context.sectionDropBefore(chips,250,20),chips[2]);
  assert.equal(context.sectionDropBefore(chips,10,70),chips[2]);
  assert.equal(context.sectionDropBefore(chips,250,70),null);
  assert.equal(context.sectionDropBefore(chips,10,200),null);
});

test('click and keyboard section ordering keep values, focus and position feedback', () => {
  const h=sectionHarness();h.textarea.value='AI\nData\nCloud';h.context.renderSectionTokens(h.form);
  assert.equal(h.buttons(h.chips()[0])[0].disabled,true);
  assert.equal(h.buttons(h.chips()[2])[1].disabled,true);
  h.buttons(h.chips()[1])[0].click();
  assert.equal(h.textarea.value,'Data\nAI\nCloud');
  assert.match(h.status.textContent,/Data moved to position 1 of 3/);
  assert.equal(h.context.focused,h.chips()[0]); // boundary: retain the group, not destructive Remove
  let prevented=false;
  h.chips()[0].keydown({altKey:true,key:'ArrowRight',preventDefault(){prevented=true;}});
  assert.equal(prevented,true);assert.equal(h.textarea.value,'AI\nData\nCloud');
  assert.equal(h.context.focused,h.buttons(h.chips()[1])[1]);
  h.context.moveSection(h.form,'AI',-1);h.context.moveSection(h.form,'Unknown',1);
  assert.equal(h.textarea.value,'AI\nData\nCloud');
});

test('removing sections returns focus to the labelled input and invalid additions retain data', () => {
  const h=sectionHarness(); const errors=[]; h.context.showAlert=(message,error)=>errors.push({message,error});
  h.textarea.value='AI\nData';h.context.setupSectionEditor(h.form);
  h.buttons(h.chips()[0])[2].click();assert.equal(h.textarea.value,'Data');
  assert.equal(h.context.focused,h.input);assert.equal(h.status.textContent,'AI removed.');
  h.input.value='Data';h.input.keydown({key:'Enter',preventDefault(){}});
  assert.equal(h.textarea.value,'Data');assert.equal(h.input.attributes['aria-invalid'],'true');
  assert.equal(errors[0].error,true);
  h.input.value='Cloud';h.input.keydown({key:'Enter',preventDefault(){}});
  assert.equal(h.textarea.value,'Data\nCloud');assert.equal(h.input.attributes['aria-invalid'],undefined);
  h.list.dataset.dropBefore='';h.list.ondrop({preventDefault(){},dataTransfer:{getData:()=> 'Foreign'}});
  assert.equal(h.textarea.value,'Data\nCloud');
  h.context.setSections(h.form,[]);assert.equal(h.textarea.value,'');assert.equal(h.status.textContent,'');
});

test('errors persist, stale notice timers cannot hide them, dismissal clears private text', () => {
  const {context}=harness(['showAlert','dismissAlert']);const callbacks=[];
  context.globalAlert=context.byId('global-alert');context.window.setTimeout=callback=>{callbacks.push(callback);return callbacks.length;};
  context.showAlert('A notice');assert.equal(callbacks.length,1);
  context.showAlert('A synthetic error',true);callbacks[0]();
  assert.equal(context.globalAlert.hidden,false);assert.equal(context.globalAlert.attributes.role,'alert');
  assert.equal(context.byId('global-alert-message').textContent,'A synthetic error');
  assert.equal(callbacks.length,1);context.dismissAlert();
  assert.equal(context.byId('global-alert-message').textContent,'');assert.equal(context.globalAlert.hidden,true);
});

test('navigation identifies the active view and respects reduced motion', () => {
  const {context}=harness(['setupNavigation']);const buttons=['gen','play'].map(view=>{
    const node=context.byId(view);node.dataset={view};return node;});
  const views=['gen','play'].map(view=>{const node=context.byId(`view-${view}`);node.id=`view-${view}`;return node;});
  context.document.querySelectorAll=selector=>selector==='.mode-button'?buttons:views;
  let scroll;context.window.scrollTo=options=>{scroll=options;};context.window.matchMedia=()=>({matches:true});
  context.setupNavigation();buttons[1].click();assert.equal(scroll.behavior,'auto');
  assert.equal(buttons[1].attributes['aria-current'],'page');assert.equal(buttons[0].attributes['aria-current'],undefined);
});

test('both progress controls announce time and paused compact playback offers Resume', () => {
  const {context}=harness(['formatPlaybackTime','setRangeProgress','syncPlayer']);
  Object.assign(context.byId('episode-audio'),{duration:90,currentTime:32,paused:true,ended:false});
  context.syncPlayer();
  for(const id of ['episode-progress','mini-player-progress']) assert.equal(context.byId(id).attributes['aria-valuetext'],'00:32 of 01:30');
  for(const id of ['player-pause-button','mini-pause-button']) assert.equal(context.byId(id).attributes['aria-label'],'Resume');
  context.byId('episode-audio').paused=false;context.syncPlayer();
  assert.equal(context.byId('mini-pause-button').attributes['aria-label'],'Pause');
});

test('sticky focus insets follow wrapped navigation and a shown or hidden compact player', () => {
  const {context}=harness(['setupStickyInsets']);let measure,navHeight=64,miniHeight=180;
  const values={}; const observed=[];
  context.document.querySelector=()=>({getBoundingClientRect:()=>({height:navHeight})});
  context.document.documentElement={style:{setProperty:(key,value)=>{values[key]=value;}}};
  context.byId('mini-player').getBoundingClientRect=()=>({height:miniHeight});
  context.ResizeObserver=class{constructor(callback){measure=callback;}observe(node){observed.push(node);}};
  context.window.addEventListener=()=>{};context.setupStickyInsets();
  assert.equal(observed.length,2);assert.equal(values['--mode-nav-height'],'64px');
  assert.equal(values['--mini-player-height'],'180px');
  navHeight=72;miniHeight=0;measure();
  assert.equal(values['--mode-nav-height'],'72px');assert.equal(values['--mini-player-height'],'0px');
});
