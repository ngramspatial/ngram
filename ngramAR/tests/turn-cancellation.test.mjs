import assert from 'node:assert/strict';
import { once } from 'node:events';
import { createServer } from 'node:http';
import test from 'node:test';
import { WebSocketServer } from 'ws';
import { EntityBridgeBinding } from '../packages/bindings/dist/entity-bridge-binding.js';
import { NgramArServer } from '../packages/gateway/dist/server.js';
import { SpeechHandler } from '../packages/surface-webxr/src/speech.ts';

test('cancel resolves blocked bridge requests and preserves the next turn', { timeout: 3000 }, async () => {
  const server = createServer();
  const wss = new WebSocketServer({ server });
  const configurations = [];
  const events = [];
  wss.on('connection', (socket) => socket.on('message', (raw) => {
    const message = JSON.parse(raw.toString());
    if (message.type === 'session.start') socket.send(JSON.stringify({ type: 'session.ready' }));
    if (message.type === 'brain.configure') {
      configurations.push(message);
      socket.send(JSON.stringify({ type: 'brain.configured', replyTo: message.id, ok: true }));
    }
    if (message.type !== 'session.event') return;
    events.push(message.event);
    if (message.event.type === 'event:cancel_turn') {
      socket.send(JSON.stringify({ type: 'actions', actions: [{ type: 'action:turn_cancelled', reason: 'stopped' }] }));
    } else if (message.event.text === 'fresh') {
      socket.send(JSON.stringify({ type: 'actions', replyTo: message.id, actions: [{ type: 'action:speak', text: 'fresh reply' }] }));
    }
  }));
  server.listen(0, '127.0.0.1');
  await once(server, 'listening');
  const binding = new EntityBridgeBinding({
    bridgeUrl: `ws://127.0.0.1:${server.address().port}`,
    brainConfig: { mode: 'private', provider: 'remote_gateway' },
  }, { sessionId: 'stop-test', shellName: 'Test', shellSlug: 'test' });
  const proactive = [];
  binding.onProactiveAction((actions) => proactive.push(...actions));
  try {
    await binding.start('test');
    assert.equal(configurations[0].interrupt, false);
    const work = binding.handleEvent({ type: 'event:user_speech', text: 'work', isFinal: true });
    const queued = binding.handleEvent({ type: 'event:user_speech', text: 'queued', isFinal: true });
    const stopped = binding.handleEvent({ type: 'event:cancel_turn' });
    assert.deepEqual(await Promise.all([work, queued, stopped]), [[], [], []]);
    assert.equal(proactive.at(-1).type, 'action:turn_cancelled');
    const fresh = await binding.handleEvent({ type: 'event:user_speech', text: 'fresh', isFinal: true });
    assert.equal(fresh[0].text, 'fresh reply');
    await binding.configureInference({ mode: 'private', provider: 'remote_gateway' });
    assert.equal(configurations.at(-1).interrupt, true);
    assert.equal(events.length, 4);
  } finally {
    await binding.stop();
    for (const socket of wss.clients) socket.terminate();
    await new Promise((resolve) => wss.close(resolve));
    await new Promise((resolve) => server.close(resolve));
  }
});

test('speech synthesized before cancellation cannot arrive after Stop', async () => {
  const server = Object.create(NgramArServer.prototype);
  let finishSynthesis;
  server.synthesizeSpeech = () => new Promise((resolve) => { finishSynthesis = resolve; });
  const delivered = [];
  server.send = (_ws, action) => delivered.push(action);
  const client = { ws: {}, sessionId: 'test' };
  const pendingSpeech = server.sendSpatialAction(client, { type: 'action:speak', text: 'stale' });
  await server.sendSpatialAction(client, { type: 'action:turn_cancelled', reason: 'stopped' });
  finishSynthesis();
  await pendingSpeech;
  assert.deepEqual(delivered.map((action) => action.type), ['action:turn_cancelled']);
  server.synthesizeSpeech = async () => {};
  await server.sendSpatialAction(client, { type: 'action:speak', text: 'new reply' });
  assert.equal(delivered.at(-1).text, 'new reply');
});

test('Stop silences active audio, drops its queue and invalidates pending decodes', () => {
  globalThis.window = { location: { protocol: 'http:', host: 'localhost:3000' }, speechSynthesis: { cancel() {} } };
  const decodes = [];
  const sources = [];
  const speech = new SpeechHandler();
  speech.audioContext = {
    state: 'running', destination: {},
    decodeAudioData(_bytes, success) { decodes.push(success); },
    createBufferSource() {
      const source = { connect() {}, start() { this.started = true; }, stop() { this.stopped = true; } };
      sources.push(source);
      return source;
    },
  };
  let ended = 0;
  speech.playAudio('AA==', () => ended++);
  speech.playAudio('AA==', () => assert.fail('queued speech should be discarded'));
  decodes.shift()({});
  const staleEnd = sources[0].onended;
  speech.stopPlayback();
  assert.equal(sources[0].stopped, true);
  assert.equal(ended, 1);
  staleEnd();
  assert.equal(ended, 1);
  assert.equal(decodes.length, 0);
  speech.playAudio('AA==', () => ended++);
  speech.stopPlayback();
  decodes.shift()({});
  assert.equal(sources.length, 1, 'cancelled pending decode cannot start audio');
  speech.playAudio('AA==', () => ended++);
  decodes.shift()({});
  assert.equal(sources[1].started, true);
  sources[1].onended();
  assert.equal(ended, 3);
});
