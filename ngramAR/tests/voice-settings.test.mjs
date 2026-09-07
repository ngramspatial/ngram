import assert from 'node:assert/strict';
import { once } from 'node:events';
import { mkdtemp, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import test from 'node:test';
import { createVoiceEngine } from '../packages/runtime/dist/voice.js';
import { loadVoiceConfig, normalizeVoiceConfig, publicVoiceConfig } from '../packages/gateway/dist/voice-config.js';
import { NgramArServer } from '../packages/gateway/dist/server.js';

test('Cartesia uses current API authentication, voice format and decodable WAV output', async t => {
  let request;
  t.mock.method(globalThis, 'fetch', async (url, options) => {
    request = { url, ...options }; return { ok: true, arrayBuffer: async () => new Uint8Array([82, 73, 70, 70]).buffer };
  });
  const config = normalizeVoiceConfig({ provider: 'cartesia', apiKey: 'test-only-key', speed: 1.2 });
  const result = await createVoiceEngine(config).synthesize('Hello spatial world.');
  assert.equal(request.url, 'https://api.cartesia.ai/tts/bytes');
  assert.equal(request.headers.Authorization, 'Bearer test-only-key');
  assert.equal(request.headers['Cartesia-Version'], '2026-08-14');
  const body = JSON.parse(request.body);
  assert.equal(body.voice, config.voice);
  assert.equal(body.model_id, 'sonic-3.6');
  assert.deepEqual(body.output_format, { container: 'wav', encoding: 'pcm_s16le', sample_rate: 44100 });
  assert.equal(body.generation_config.speed, 1.2);
  assert.equal(result.audioBase64, 'UklGRg==');
  assert.equal(JSON.stringify(publicVoiceConfig(config)).includes('test-only-key'), false);
  assert.equal(publicVoiceConfig(config).hasApiKey, true);
  assert.equal(normalizeVoiceConfig({ ...config, apiKey: '' }, config).apiKey, config.apiKey);
  assert.throws(() => normalizeVoiceConfig({ ...config, speed: 9 }), /speed/);
  assert.throws(() => normalizeVoiceConfig({ ...config, voice: 'invalid' }), /voice ID/);
});

test('blank UI keys use gateway environment credentials without returning them', async t => {
  const previous = process.env.CARTESIA_API_KEY;
  process.env.CARTESIA_API_KEY = 'test-env-key';
  t.after(() => { if (previous === undefined) delete process.env.CARTESIA_API_KEY; else process.env.CARTESIA_API_KEY = previous; });
  t.mock.method(globalThis, 'fetch', async (_url, options) => {
    assert.equal(options.headers.Authorization, 'Bearer test-env-key');
    return { ok: true, arrayBuffer: async () => new ArrayBuffer(1) };
  });
  const config = normalizeVoiceConfig({ provider: 'cartesia', apiKey: '' });
  assert.equal(config.apiKey, '');
  await createVoiceEngine(config).synthesize('Test');
});

test('voice API persists settings, updates connected voices, previews and rejects cross-origin writes', async () => {
  const directory = await mkdtemp(join(tmpdir(), 'ngram-voice-'));
  const shellsDir = join(directory, 'shells');
  const server = new NgramArServer({ shellsDir, https: false, host: '127.0.0.1', port: 0 });
  server.httpServer.listen(0, '127.0.0.1');
  await once(server.httpServer, 'listening');
  const url = `http://127.0.0.1:${server.httpServer.address().port}/api/voice`;
  const client = { voice: null }; const session = { voice: null };
  server.clients.set('test', client); server.sessions.set('test', session);
  const payload = { provider: 'browser', voice: 'Device voice', speed: 1.25 };
  const options = { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) };
  try {
    assert.equal((await (await fetch(url)).json()).providers.some(item => item.id === 'cartesia'), true);
    const forbidden = await fetch(url, { ...options, headers: { ...options.headers, Origin: 'https://elsewhere.invalid' } });
    assert.equal(forbidden.status, 403);
    const saved = await fetch(url, options);
    assert.equal(saved.status, 200);
    assert.equal(saved.headers.get('cache-control'), 'no-store');
    assert.equal((await saved.json()).config.speed, 1.25);
    assert.equal((await loadVoiceConfig(shellsDir)).voice, 'Device voice');
    assert.deepEqual((await client.voice.synthesize('Test')).voiceConfig, { voice: 'Device voice', speed: 1.25 });
    assert.equal(session.voice, client.voice);
    const preview = await fetch(`${url}/preview`, { ...options, method: 'POST' });
    const sample = await preview.json();
    assert.equal(sample.audioBase64, '');
    assert.match(sample.text, /Hello from ngram/);
    assert.equal('apiKey' in sample.config, false);
  } finally {
    server.httpServer.closeAllConnections();
    await new Promise(resolve => server.httpServer.close(resolve));
    server.wss.close();
    await rm(directory, { recursive: true, force: true });
  }
});
