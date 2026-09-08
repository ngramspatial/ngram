import assert from 'node:assert/strict';
import test from 'node:test';
import { createServer } from 'node:http';
import { proxyAttachment } from '../packages/gateway/dist/attachment-proxy.js';

async function listen(handler) {
  const server = createServer(handler);
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  return { server, url: `http://127.0.0.1:${server.address().port}` };
}
const close = server => new Promise(resolve => { server.closeAllConnections(); server.close(resolve); });

test('attachment proxy streams original bytes privately and preserves range downloads', async () => {
  const seen = [];
  const id = 'a'.repeat(32);
  const worker = await listen(async (req, res) => {
    const parts = []; for await (const chunk of req) parts.push(chunk);
    seen.push({ method: req.method, url: req.url, headers: req.headers, body: Buffer.concat(parts) });
    if (req.method === 'POST') res.writeHead(201, {'Content-Type':'application/json'}).end(JSON.stringify({id,name:'sample.png',mime:'image/png',size:4}));
    else res.writeHead(206, {'Content-Type':'image/png','Content-Range':'bytes 0-3/10','Accept-Ranges':'bytes','Content-Security-Policy':"default-src 'none'; sandbox"}).end('test');
  });
  const gateway = await listen((req, res) => proxyAttachment(req, res, {bridgeUrl:worker.url + '/bridge?private=query',token:'worker-only'}, req.url === '/' ? undefined : req.url.slice(1)));
  try {
    const uploaded = await fetch(gateway.url, {method:'POST', headers:{Origin:gateway.url,Authorization:'Bearer surface-only','X-Ngram-Filename':'sample.png','Content-Type':'image/png'},body:Buffer.from([0,1,2,255])});
    assert.equal(uploaded.status,201);
    assert.equal((await uploaded.json()).id,id);
    assert.deepEqual(seen[0].body,Buffer.from([0,1,2,255]));
    assert.equal(seen[0].headers.authorization,'Bearer worker-only');
    assert.equal(seen[0].url,'/bridge/attachments');
    const download = await fetch(gateway.url + '/' + id, {headers:{Range:'bytes=0-3'}});
    assert.equal(download.status,206); assert.equal(await download.text(),'test');
    assert.equal(download.headers.get('content-range'),'bytes 0-3/10');
    assert.equal(download.headers.get('content-security-policy'),"default-src 'none'; sandbox");
    assert.equal(seen[1].headers.range,'bytes=0-3');
    assert.equal((await fetch(gateway.url,{method:'POST',headers:{Origin:'https://unrelated.invalid'},body:'evil'})).status,403);
    assert.equal((await fetch(gateway.url + '/invalid',{method:'GET'})).status,405);
    assert.equal(seen.length,2);
  } finally { await close(gateway.server); await close(worker.server); }
});

test('worker errors return an actionable safe error without credentials or an inference call', async () => {
  const worker = await listen((_req,res) => res.writeHead(404).end('private worker details'));
  const gateway = await listen((req,res) => proxyAttachment(req,res,{bridgeUrl:worker.url,token:'private-token'}));
  try {
    const result = await fetch(gateway.url,{method:'POST',body:'file'});
    assert.equal(result.status,404);
    const data = await result.text();
    assert.match(data,/worker may need updating/); assert.ok(!data.includes('private'));
  } finally { await close(gateway.server); await close(worker.server); }
});
