import assert from 'node:assert/strict';
import test from 'node:test';
import { build } from 'esbuild';
import { mkdtemp, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import { pathToFileURL } from 'node:url';
import { createServer } from 'node:http';

test('Blender revisions keep human transforms, defer held objects, and reject stale or foreign links', async () => {
  const folder = await mkdtemp(join(tmpdir(), 'ngram-blender-links-'));
  let links;
  try {
    await build({ stdin: { contents: `export {BlenderProjects} from './packages/surface-webxr/src/blender-projects.ts'; export {WorldStore} from './packages/core/src/world-store.ts';`, resolveDir: resolve('.') }, bundle: true, platform: 'node', format: 'esm', outfile: join(folder, 'test.mjs'), logLevel: 'silent' });
    const { BlenderProjects, WorldStore } = await import(pathToFileURL(join(folder, 'test.mjs')));
    const entries = new Map();
    const store = new WorldStore({ commit(_before, after) {
      entries.clear();
      for (const spec of after.entities) entries.set(spec.id, { spec, status: 'ready' });
    } });
    links = new BlenderProjects({ world: { store, entries } });
    const payload = { projectId: 'sculpture', name: 'Sculpture', revision: 1, base: '/api/shells/test-agent/blender/sculpture', position: [0, 1, -2] };
    const id = links.attach(payload).id;
    assert.equal(entries.get(id).spec.asset.normalize, false, 'Keep authored metres and origin');
    store.apply({ requestId: 'human-placement', operations: [{ op: 'entity.patch', id, patch: { transform: { position: [2, 3, 4], scale: [2, 2, 2] } } }] }, 'human');
    store.locks.set(id, 'human');
    links.attach({ ...payload, revision: 3 });
    links.attach({ ...payload, revision: 2 });
    assert.equal(links.list()[0].pending, 3, 'Older delivery cannot replace the queued revision');
    assert.equal(links.list()[0].revision, 1);
    store.locks.delete(id);
    links.flush();
    assert.equal(links.list()[0].revision, 3);
    assert.deepEqual(entries.get(id).spec.transform.position, [2, 3, 4]);
    assert.deepEqual(entries.get(id).spec.transform.scale, [2, 2, 2]);
    assert.match(entries.get(id).spec.asset.url, /\/3\/preview.glb$/);
    links.attach({ ...payload, revision: 2 });
    assert.equal(links.list()[0].revision, 3);
    assert.throws(() => links.attach({ ...payload, base: 'https://foreign.invalid/model' }), /Invalid/);
    assert.throws(() => links.attach({ ...payload, projectId: '../escape' }), /Invalid/);
    store.apply({ requestId: 'remove', operations: [{ op: 'entity.delete', id }] }, 'human');
    links.attach({ ...payload, revision: 4 });
    assert.equal(entries.size, 0, 'A later update cannot undo the human deleting the object');
  } finally {
    clearInterval(links?.timer);
    await rm(folder, { recursive: true, force: true });
  }
});

test('Gateway streams configured Blender artifacts without forwarding secrets or redirects', async () => {
  const { proxyBlender } = await import('../packages/gateway/dist/blender-proxy.js');
  const requests = [];
  const upstream = createServer((req, res) => {
    requests.push({ path: req.url, auth: req.headers.authorization });
    if (req.url.endsWith('/status')) { res.writeHead(302, { Location: 'http://untrusted.invalid/' }); res.end(); }
    else { res.writeHead(200, { 'Content-Type': 'model/gltf-binary', 'Content-Length': '8' }); res.end('glTFtest'); }
  });
  await new Promise(r => upstream.listen(0, '127.0.0.1', r));
  const gateway = createServer((req, res) => proxyBlender(req, res, { bridgeUrl: `ws://127.0.0.1:${upstream.address().port}/`, token: 'test-secret' }, req.url.slice(1)));
  await new Promise(r => gateway.listen(0, '127.0.0.1', r));
  const base = `http://127.0.0.1:${gateway.address().port}`;
  try {
    const r = await fetch(base + '/model/1/preview.glb');
    assert.equal(await r.text(), 'glTFtest');
    assert.equal(r.headers.get('authorization'), null);
    assert.deepEqual(requests[0], { path: '/blender/model/1/preview.glb', auth: 'Bearer test-secret' });
    assert.equal((await fetch(base + '/model/status')).status, 502, 'Reject upstream redirects');
    assert.equal((await fetch(base + '/model/1/secrets.env')).status, 404);
    assert.equal((await fetch(base + '/model/stop')).status, 404, 'Stop cannot be triggered with GET');
    assert.equal(requests.length, 2);
  } finally {
    for (const server of [gateway, upstream]) { server.closeAllConnections(); await new Promise(r => server.close(r)); }
  }
});
