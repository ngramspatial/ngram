import assert from 'node:assert/strict';
import test from 'node:test';
import { once } from 'node:events';
import { mkdtemp, readFile, readdir, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join, resolve, sep } from 'node:path';
import { WebSocketServer } from 'ws';
import { normalizeConnection, normalizeCreation, verifyConnection, writeConnectedShell } from '../packages/gateway/dist/onboarding.js';
import { resolveEntityBridgeConfig } from '../packages/gateway/dist/resolve-entity-bridge.js';
import { loadShellDefinition } from '../packages/runtime/dist/shell-loader.js';
import { NgramArServer } from '../packages/gateway/dist/server.js';

test('pairing normalizes Railway domains without changing the Python bridge root path', () => {
  assert.equal(normalizeConnection({bridgeUrl:'https://worker.example',token:'pair'}).bridgeUrl,'wss://worker.example/');
  assert.equal(normalizeConnection({bridgeUrl:'ws://127.0.0.1:7878/'}).target,'local');
  for (const bridgeUrl of ['http://worker.example/', 'file:///etc/passwd', 'https://user:secret@worker.example', 'https://worker.example/?token=secret']) {
    assert.throws(()=>normalizeConnection({bridgeUrl,token:'pair'}));
  }
  assert.throws(()=>normalizeConnection({bridgeUrl:'https://worker.example'}), /token/);
});
test('creation rejects configuration injection and unsupported body or voice values', () => {
  for (const name of ['', '../../', 'Nova\nbinding: injected', '<img src=x>']) assert.throws(()=>normalizeCreation({name}));
  assert.throws(()=>normalizeCreation({name:'Nova',voice:'injected'}));
  assert.equal(normalizeCreation({name:'Nova: the second'}).slug,'nova-the-second');
});
async function fixture(t, setup = {}, rejectKey = false) {
  const messages=[];
  const worker=new WebSocketServer({port:0,host:'127.0.0.1'});
  await once(worker,'listening');
  worker.on('connection',(ws,req)=>{
    if (rejectKey) { ws.close(); return; }
    assert.equal(req.headers.authorization,'Bearer worker-secret');
    ws.on('message',raw=>{
      const message=JSON.parse(raw.toString()); messages.push(message);
      if(message.type==='session.start') ws.send(JSON.stringify({type:'session.ready',setup:{entityName:'Nova',embeddingModel:'text-embedding-3-small',hasMemories:false,...setup}}));
      if(message.type==='brain.configure') ws.send(JSON.stringify({type:'brain.configured',replyTo:message.id,ok:true,status:{verified:true,embeddingDimensions:768}}));
      if(message.type==='session.stop') ws.close();
    });
  });
  t.after(()=>new Promise(resolve=>{for(const client of worker.clients)client.terminate();worker.close(resolve);}));
  return {connection:normalizeConnection({bridgeUrl:`ws://127.0.0.1:${worker.address().port}/`,token:'worker-secret'}),messages};
}
test('verified setup authenticates and probes hosted embeddings without starting a conversation', async t=>{
  const {connection,messages}=await fixture(t);
  const {brain}=normalizeCreation({name:'Nova',brain:{mode:'frontier',provider:'openai',model:'test-model',apiKey:'provider-secret'}});
  const status=await verifyConnection(connection,brain);
  assert.equal(status.embeddingVerified,true);
  assert.equal(status.embeddingDimensions,768);
  assert.equal(messages.some(m=>m.type==='session.event'),false);
  assert.equal(messages.find(m=>m.type==='brain.configure').config.embeddingModel,'text-embedding-3-small');
});
test('existing memories block a silent embedding model change', async t=>{
  const {connection,messages}=await fixture(t,{embeddingModel:'nomic-embed-text',hasMemories:true});
  const {brain}=normalizeCreation({name:'Nova',brain:{mode:'frontier',provider:'openai',model:'test-model',apiKey:'provider-secret'}});
  await assert.rejects(verifyConnection(connection,brain),/migrate/);
  assert.equal(messages.some(m=>m.type==='brain.configure'),false);
});
test('keep worker settings never changes the inference or embedding route',async t=>{
  const {connection,messages}=await fixture(t,{embeddingModel:'nomic-embed-text',hasMemories:true});
  const result=await verifyConnection(connection);
  assert.equal(result.embeddingModel,'nomic-embed-text');
  assert.equal(result.embeddingVerified,false);
  assert.equal(messages.some(m=>m.type==='brain.configure'),false);
});
test('rejected pairing fails with a useful error and no success result',async t=>{
  const {connection}=await fixture(t,{},true);
  await assert.rejects(verifyConnection(connection),/closed the connection/);
});
test('connected shells persist isolated credentials and survive loading without environment changes',async t=>{
  const root=await mkdtemp(join(tmpdir(),'ngram-onboarding-'));
  t.after(async()=>{assert.ok(resolve(root).startsWith(resolve(tmpdir())+sep));await rm(root,{recursive:true,force:true});});
  const shells=join(root,'shells');
  const creation=normalizeCreation({name:'Nova: second',brain:{mode:'frontier',provider:'openai',model:'test',apiKey:'provider-secret'}});
  const result=await writeConnectedShell(shells,creation,{bridgeUrl:'wss://worker.example/',token:'worker-secret'}, {connected:true});
  const yaml=await readFile(join(shells,result.slug,'shell.yaml'),'utf8');
  assert.equal(yaml.includes('worker-secret'),false);assert.equal(yaml.includes('provider-secret'),false);
  assert.equal(JSON.stringify(result).includes('secret'),false);
  const shell=await loadShellDefinition(join(shells,result.slug));
  assert.equal(shell.name,'Nova: second');
  const connection=resolveEntityBridgeConfig(shell.binding,shells);
  assert.equal(connection.token,'worker-secret');assert.equal(connection.brainConfig.apiKey,'provider-secret');
  await assert.rejects(writeConnectedShell(shells,creation,{token:'replacement'},{connected:true}),{code:'EEXIST'});
  assert.equal(resolveEntityBridgeConfig(shell.binding,shells).token,'worker-secret');
  assert.equal((await readdir(join(root,'.runtime/connections'))).length,1);
});
test('HTTP creation requires pairing, keeps failures retryable, and returns no credentials',async t=>{
  const root=await mkdtemp(join(tmpdir(),'ngram-onboarding-http-'));
  const server=new NgramArServer({shellsDir:join(root,'shells'),port:0,host:'127.0.0.1',https:false});
  await server.start();
  t.after(async()=>{await server.stop();assert.ok(resolve(root).startsWith(resolve(tmpdir())+sep));await rm(root,{recursive:true,force:true});});
  const {connection}=await fixture(t);
  const base=`http://127.0.0.1:${server.httpServer.address().port}`;
  const create=data=>fetch(`${base}/api/shells`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)});
  const first=await fetch(`${base}/api/onboarding`).then(r=>r.json());assert.equal(first.needsSetup,true);
  assert.equal((await create({name:'Nova'})).status,400);
  assert.equal((await fetch(`${base}/api/shells`,{method:'POST',headers:{Origin:'https://other.example'},body:'{}'})).status,403);
  const response=await create({name:'Nova',connection});assert.equal(response.status,201);
  const result=await response.json();assert.equal(result.connected,true);assert.equal(JSON.stringify(result).includes('worker-secret'),false);
  const second=await create({name:'Echo',reuseShell:'nova'});assert.equal(second.status,201);
  const listing=await fetch(`${base}/api/onboarding`).then(r=>r.json());assert.equal(listing.connections.length,2);assert.equal(JSON.stringify(listing).includes('worker-secret'),false);
  assert.equal((await fetch(`${base}/shells/nova/.env`)).status,403);
});
