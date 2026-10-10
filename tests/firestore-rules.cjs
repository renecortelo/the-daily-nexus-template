// Real rules against a local emulator only; no SDK/dependency or production data.
const fs = require('node:fs');
const assert = require('node:assert/strict');
const test = require('node:test');
const host = process.env.FIRESTORE_EMULATOR_HOST;
if (!/^127\.0\.0\.1:\d{2,5}$/.test(host || '')) {
  throw Error('Set FIRESTORE_EMULATOR_HOST to a loopback emulator; production is forbidden.');
}
const project = 'demo-nexus-security';
const base = `http://${host}/v1/projects/${project}/databases/(default)/documents`;
const owner = 'synthetic-owner', runner = 'synthetic-runner';
const fields = data => Object.fromEntries(Object.entries(data).map(([key,value])=>[
  key, typeof value === 'string' ? {stringValue:value} : typeof value === 'boolean'
    ? {booleanValue:value} : {integerValue:String(value)},
]));
function token(uid, provider='google.com', age=0, extra={}) {
  const now = Math.floor(Date.now()/1000);
  return [ {alg:'none',typ:'JWT'}, {sub:uid,user_id:uid,aud:project,
    iss:`https://securetoken.google.com/${project}`,iat:now,exp:now+3600,
    auth_time:now-age,firebase:{sign_in_provider:provider,identities:{}},...extra},
  ].map(value=>Buffer.from(JSON.stringify(value)).toString('base64url')).join('.')+'.';
}
async function request(path, bearer, method='GET', data) {
  return fetch(`${base}/${path}`, {method,headers:{authorization:`Bearer ${bearer}`,
    'content-type':'application/json'},body:data===undefined?undefined:JSON.stringify({fields:fields(data)})});
}
async function allowed(path,bearer,method='GET',data) {
  const result=await request(path,bearer,method,data);
  assert.equal(result.status,200,await result.text());
}
async function denied(path,bearer,method='GET',data) {
  const result=await request(path,bearer,method,data);
  assert.equal(result.status,403,await result.text());
}
async function grant(mode='active') {
  await allowed(`automationAuthorizations/${owner}`,'owner','PATCH',
    {runnerUid:runner,mode,schemaVersion:1});
}
test.before(async()=>{
  const compile=await fetch(`http://${host}/emulator/v1/projects/${project}:securityRules`,{
    method:'PUT',headers:{'content-type':'application/json'},body:JSON.stringify({rules:{files:[{
      name:'firestore.rules',content:fs.readFileSync('firestore.rules','utf8'),
    }]}}),
  });
  assert.equal(compile.status,200,await compile.text());
});
test.beforeEach(async()=>{
  await fetch(`http://${host}/emulator/v1/projects/${project}/databases/(default)/documents`,{method:'DELETE'});
  for(const [path,data] of [
    [`owners/${owner}`,{enabled:true}],
    [`users/${owner}/schedules/synthetic`,{name:'Synthetic schedule'}],
    [`users/${owner}/episodes/synthetic`,{title:'Synthetic episode'}],
    [`users/${owner}/runner/status`,{state:'idle'}],
    [`users/${owner}/runRequests/failed`,{status:'failed'}],
    [`users/${owner}/runRequests/queued`,{status:'queued'}],
  ]) await allowed(path,'owner','PATCH',data);
});
test('legacy access remains only until explicit activation',async()=>{
  await allowed(`owners/${owner}`,token(owner,'google.com',7200));
  await grant('prepared');
  await allowed(`owners/${owner}`,token(owner,'google.com',7200));
  await grant();
  await denied(`owners/${owner}`,token(owner,'google.com',3600));
  await denied(`owners/${owner}`,token(owner,'google.com',7200));
  await allowed(`owners/${owner}`,token(owner,'google.com',30));
});
test('refresh time, future/missing auth time and wrong provider cannot bypass expiry',async()=>{
  await grant();
  for(const bearer of [token(owner,'google.com',3601),token(owner,'google.com',-90),
    token(owner,'google.com',0,{auth_time:null}),token(owner,'anonymous',10)]) {
    await denied(`users/${owner}/episodes/synthetic`,bearer);
  }
});
test('dedicated old runner reads schedules and writes state but cannot manage settings',async()=>{
  await grant(); const bearer=token(runner,'anonymous',86400);
  await allowed(`users/${owner}/schedules/synthetic`,bearer);
  await allowed(`users/${owner}/runner/status`,bearer,'PATCH',{state:'idle'});
  await allowed(`users/${owner}/episodes/synthetic`,bearer,'PATCH',{title:'New synthetic title'});
  await denied(`users/${owner}/schedules/synthetic`,bearer,'DELETE');
  await denied(`owners/${owner}`,bearer);
  await denied(`users/${owner}/clockSchedules/synthetic`,bearer);
  await denied(`automationAuthorizations/${owner}`,bearer,'PATCH',
    {runnerUid:runner,mode:'prepared',schemaVersion:1});
  await denied(`users/${owner}/episodes/synthetic`,bearer,'DELETE');
});
test('unknown/wrong-provider/cross-owner identities have no runner access',async()=>{
  await grant();
  for(const bearer of [token('synthetic-stranger','anonymous'),token(runner,'google.com')]) {
    await denied(`users/${owner}/schedules/synthetic`,bearer);
    await denied(`users/${owner}/runner/status`,bearer,'PATCH',{state:'idle'});
  }
  await denied('users/synthetic-other/runner/status',token(runner,'anonymous'),'PATCH',{state:'idle'});
});
test('active grant cannot be deleted/downgraded; revocation does not restore legacy access',async()=>{
  await grant();const bearer=token(owner);
  await denied(`automationAuthorizations/${owner}`,bearer,'DELETE');
  await denied(`automationAuthorizations/${owner}`,bearer,'PATCH',
    {runnerUid:runner,mode:'prepared',schemaVersion:1});
  await grant('revoked');
  await denied(`users/${owner}/schedules/synthetic`,token(runner,'anonymous'));
  await denied(`owners/${owner}`,token(owner,'google.com',7200));
});
test('fresh browser cannot forge publication/runner state or remove queued requests',async()=>{
  await grant();const bearer=token(owner);
  await denied(`users/${owner}/runner/status`,bearer,'PATCH',{state:'running'});
  await denied(`users/${owner}/episodes/synthetic`,bearer,'PATCH',{title:'Forged title'});
  await allowed(`users/${owner}/runRequests/failed`,bearer,'DELETE');
  await denied(`users/${owner}/runRequests/queued`,bearer,'DELETE');
});
test('only a fresh owner can prepare/activate a grant and expiry affects collection queries',async()=>{
  const prepared={runnerUid:runner,mode:'prepared',schemaVersion:1};
  await denied(`automationAuthorizations/${owner}`,token(owner,'google.com',7200),'PATCH',prepared);
  await denied(`automationAuthorizations/${owner}`,token('synthetic-stranger'),'PATCH',prepared);
  await allowed(`automationAuthorizations/${owner}`,token(owner),'PATCH',prepared);
  await allowed(`automationAuthorizations/${owner}`,token(owner),'PATCH',{...prepared,mode:'active'});
  await denied(`users/${owner}/schedules`,token(owner,'google.com',7200));
  await allowed(`users/${owner}/schedules`,token(runner,'anonymous',7200));
  await denied(`automationAuthorizations/${owner}`,token(owner),'PATCH',{
    ...prepared,runnerUid:owner,mode:'active',
  });
});
