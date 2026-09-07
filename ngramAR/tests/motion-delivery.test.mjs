import assert from 'node:assert/strict';
import { EventEmitter } from 'node:events';
import { mkdtemp, mkdir, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { setImmediate } from 'node:timers/promises';
import test from 'node:test';

import { NgramArServer } from '../packages/gateway/dist/server.js';

async function setup(t, { failMotion = false } = {}) {
  const root = await mkdtemp(join(tmpdir(), 'ngram-motion-delivery-'));
  t.after(() => rm(root, { recursive: true, force: true }));
  await mkdir(join(root, 'test'));
  await writeFile(join(root, 'test', 'shell.yaml'), 'name: Test\nmodel: default\nbinding:\n  type: ngram_entity\n');

  const sent = [];
  const requests = [];
  let proactive;
  const actions = [
    { type: 'action:generate_motion', requestId: 'motion-1', prompt: 'Wave hello', durationSeconds: 2 },
    { type: 'action:speak', text: 'Hello' },
  ];
  const binding = {
    start: async () => {}, stop: async () => {},
    onProactiveAction: (callback) => { proactive = callback; },
    handleEvent: async () => actions,
    injectBehaviorPrompt: async () => actions,
  };
  const server = Object.create(NgramArServer.prototype);
  server.options = { shellsDir: root };
  server.defaultShellSlug = 'test';
  server.clients = new Map();
  server.requestHasSurfaceAccess = () => true;
  server.createShellBinding = () => binding;
  server.send = (_ws, action) => sent.push(action);
  server.synthesizeSpeech = async (action) => { action.audioData = 'test-audio'; };
  server.motionProvider = {
    generate: async (action, sessionId) => {
      requests.push({ action, sessionId });
      if (failMotion) throw new Error('Motion provider timed out');
      return {
        type: 'action:play_motion_clip', requestId: action.requestId, sessionId,
        clipUrl: 'https://assets.invalid/wave.glb', format: 'glb',
      };
    },
  };

  const ws = new EventEmitter();
  ws.close = () => ws.emit('close');
  await server.handleConnection(ws, { url: '/ws?shell=test', headers: {} });
  const client = server.clients.get(ws);
  assert.ok(client);
  t.after(() => ws.close());
  sent.length = 0;

  async function deliver(route) {
    if (route === 'live') {
      proactive(actions);
      await setImmediate();
    } else if (route === 'turn') {
      await server.handleMessage(client, Buffer.from(JSON.stringify({
        type: 'event:user_speech', text: 'Wave hello', isFinal: true,
      })));
    } else {
      client.behaviorRuntime = {
        handleTrigger: () => route === 'reactive'
          ? { type: 'action', actions }
          : { type: 'prompt', prompt: 'Greet the user', context: {} },
      };
      await server.handleBehaviorTrigger(client, { behaviorId: 'greeting', context: {} });
    }
  }
  return { deliver, sent, requests, sessionId: client.sessionId };
}

for (const route of ['live', 'turn', 'reactive', 'deliberative']) {
  test(`${route} motion requests reach the provider and deliver a playable clip`, async (t) => {
    const { deliver, sent, requests, sessionId } = await setup(t);
    await deliver(route);
    assert.equal(requests.length, 1);
    assert.equal(requests[0].action.prompt, 'Wave hello');
    assert.equal(requests[0].sessionId, sessionId);
    const clips = sent.filter((action) => action.type === 'action:play_motion_clip');
    assert.equal(clips.length, 1);
    assert.equal(clips[0].requestId, 'motion-1');
    assert.equal(clips[0].sessionId, sessionId);
    assert.equal(sent.some((action) => action.type === 'action:generate_motion'), false);
    assert.deepEqual(sent.filter((action) => action.type === 'action:set_agent_state').map((action) => action.state), ['tool_running', 'idle']);
    assert.equal(sent.find((action) => action.type === 'action:speak').audioData, 'test-audio');
  });

  test(`${route} motion failures are visible and do not suppress the remaining speech`, async (t) => {
    const { deliver, sent, requests } = await setup(t, { failMotion: true });
    await deliver(route);
    assert.equal(requests.length, 1);
    assert.equal(sent.some((action) => action.type === 'action:generate_motion'), false);
    assert.equal(sent.some((action) => action.type === 'action:play_motion_clip'), false);
    assert.match(sent.find((action) => action.type === 'action:error')?.message ?? '', /timed out/);
    assert.equal(sent.filter((action) => action.type === 'action:set_agent_state').at(-1).state, 'idle');
    assert.equal(sent.find((action) => action.type === 'action:speak').audioData, 'test-audio');
  });
}
