import assert from 'node:assert/strict';
import test, { after } from 'node:test';
import { build } from 'esbuild';
import { mkdtemp, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join, resolve, sep } from 'node:path';
import { pathToFileURL } from 'node:url';
import { runInNewContext } from 'node:vm';

const dir = await mkdtemp(join(tmpdir(), 'ngram-program-data-'));
after(async () => { assert.ok(resolve(dir).startsWith(resolve(tmpdir()) + sep)); await rm(dir, { recursive: true }); });
await build({ stdin: { contents: `export {ProgramDataFeed} from './packages/surface-webxr/src/program-data.ts'; export {workerBootstrap} from './packages/surface-webxr/src/creation-programs.ts'; export {parseFigment} from './packages/core/src/figment-contract.ts';`, resolveDir: resolve('.') }, bundle: true, platform: 'node', format: 'esm', outfile: join(dir, 'data.mjs'), logLevel: 'silent' });
const { ProgramDataFeed, workerBootstrap, parseFigment } = await import(pathToFileURL(join(dir, 'data.mjs')));
const source = { url: '/api/shells/rook/blender/tablet/feeds/quotes.json', intervalSeconds: 5 };

test('published feeds update across intervals and retain last good data on failures', async () => {
  const original = globalThis.fetch;
  let count = 0, fail = false;
  globalThis.fetch = async (url, options) => {
    assert.equal(url, source.url); assert.equal(options.redirect, 'error');
    count++;
    return fail ? new Response('unavailable', { status: 503 }) : Response.json({ revision: count });
  };
  const feed = new ProgramDataFeed(source);
  try {
    await feed.refresh(100); assert.equal(count, 0);
    feed.start(); await feed.refresh(100); assert.equal(feed.data.revision, 1);
    feed.start(); await feed.refresh(200); assert.equal(count, 1, 'pump start must not reset the interval');
    await feed.refresh(5100); assert.equal(feed.data.revision, 2);
    fail = true; await feed.refresh(10100);
    assert.equal(feed.status.status, 'error'); assert.equal(feed.data.revision, 2);
    assert.ok(feed.status.receivedAt > 0);
    feed.stop(); await feed.refresh(15100); assert.equal(count, 3);
    feed.start(); fail = false; await feed.refresh(15100); assert.equal(feed.data.revision, 4);
  } finally { feed.stop(); globalThis.fetch = original; }
});

test('feed source boundaries reject remote URLs, traversal, queries and rapid polling', () => {
  for (const url of ['https://evil.invalid/x.json', '//host/x.json', '/api/shells/rook/blender/../feeds/x.json', source.url + '?key=secret', '/api/shells/rook/attachments/x'])
    assert.throws(() => new ProgramDataFeed({ ...source, url }));
  assert.throws(() => new ProgramDataFeed({ ...source, intervalSeconds: 1 }));
  const definition = parseFigment({ behavior: { source: 'return {}', dataSource: source } });
  assert.deepEqual(definition.behavior.dataSource, source);
});

test('oversized or invalid feeds and cancelled requests cannot replace good data', async () => {
  const original = globalThis.fetch;
  const feed = new ProgramDataFeed(source);
  try {
    feed.start(); globalThis.fetch = async () => Response.json({ revision: 1 }); await feed.refresh(0);
    globalThis.fetch = async () => new Response('x'.repeat(65537)); await feed.refresh(5000);
    assert.match(feed.status.error, /64 KiB/); assert.equal(feed.data.revision, 1);
    globalThis.fetch = async () => Response.json([]); await feed.refresh(10000);
    assert.match(feed.status.error, /JSON object/);
    let release;
    globalThis.fetch = async () => new Promise(resolve => { release = resolve; });
    const pending = feed.refresh(15000); feed.stop(); release(Response.json({ revision: 99 })); await pending;
    assert.equal(feed.data.revision, 1);
  } finally { feed.stop(); globalThis.fetch = original; }
});

test('sandbox consumes host JSON and status without receiving fetch or other network tools', async () => {
  const output = [];
  const sandbox = { self: { postMessage: value => output.push(value) } };
  runInNewContext(`(${workerBootstrap.toString()})()`, sandbox);
  await sandbox.self.onmessage({ data: { type: 'init', state: {}, params: {}, source: 'return {tick(){api.state.seen=api.data.price;api.state.status=api.dataStatus.status;api.state.network=typeof fetch;}}' } });
  await sandbox.self.onmessage({ data: { type: 'step', entities: [], params: {}, events: [], dt: .1, time: .1, feed: { price: 123.45 }, feedStatus: { status: 'ready' } } });
  assert.equal(output[1].state.seen, 123.45); assert.equal(output[1].state.status, 'ready'); assert.equal(output[1].state.network, 'undefined');
});
