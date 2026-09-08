import assert from 'node:assert/strict';
import test from 'node:test';
import { createServer } from 'node:http';
import { mkdtemp, mkdir, writeFile, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join, resolve, sep } from 'node:path';
import { NgramArServer } from '../packages/gateway/dist/server.js';

test('HTTP attachment-only messages retain streamed say replies and isolate concurrent requests', async () => {
  const directory = await mkdtemp(join(tmpdir(),'ngram-attachment-message-'));
  await mkdir(join(directory,'agent'));
  await writeFile(join(directory,'agent/shell.yaml'),'name: Fixture');
  let release, started, callback, received, calls=0;
  const ready = new Promise(resolve => { started=resolve; });
  const gate = new Promise(resolve => { release=resolve; });
  const binding = {
    onProactiveAction(fn) { callback=fn; },
    async handleEvent(event) {
      calls++; received=event; started();
      callback([{type:'action:speak',text:'PDF and image received',audioData:'not-in-http',visemes:[]}]);
      await gate;
      return [{type:'action:set_agent_state',state:'idle'}];
    },
  };
  const api = Object.create(NgramArServer.prototype);
  api.options={shellsDir:directory};
  api.sessions=new Map([['fixture',{binding,shellSlug:'agent',lastActivity:0}]]);
  const server=createServer((req,res) => api.handleShellMessage(req,res,'agent'));
  await new Promise(resolve => server.listen(0,'127.0.0.1',resolve));
  const url=`http://127.0.0.1:${server.address().port}`;
  const body=JSON.stringify({session:'fixture',attachments:['a'.repeat(32)]});
  try {
    const first=fetch(url,{method:'POST',body});
    await ready;
    const busy=await fetch(url,{method:'POST',body});
    assert.equal(busy.status,409);
    api.sessions.get('fixture').lastActivity=0;
    api.cleanupSessions();
    assert.equal(api.sessions.size,1,'active message must survive idle-session cleanup');
    release();
    const result=await (await first).json();
    assert.equal(calls,1);
    assert.deepEqual(received.attachments,['a'.repeat(32)]);
    assert.equal(received.text,'');
    assert.deepEqual(result.actions[0],{type:'action:speak',text:'PDF and image received'});
    assert.equal(result.actions[1].state,'idle');
    assert.equal(api.sessions.get('fixture').messageActive,false);
    assert.equal((await fetch(url,{method:'POST',body:JSON.stringify({session:'fixture',text:'x',attachments:['../../file']})})).status,400);
  } finally {
    release();server.closeAllConnections();await new Promise(resolve=>server.close(resolve));
    assert.ok(resolve(directory).startsWith(resolve(tmpdir())+sep));
    await rm(directory,{recursive:true,force:true});
  }
});
