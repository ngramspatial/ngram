import assert from 'node:assert/strict';
import { once } from 'node:events';
import { createServer } from 'node:http';
import test from 'node:test';

import { WebSocketServer } from 'ws';

import { EntityBridgeBinding } from '../packages/bindings/dist/entity-bridge-binding.js';
import { normalizeBrainConfig, publicBrainConfig, runtimeBrainConfig } from '../packages/gateway/dist/brain-config.js';
import { MotionProviderClient } from '../packages/gateway/dist/motion-provider.js';
import { NgramArServer } from '../packages/gateway/dist/server.js';

test('brain config validates frontier settings without returning the API key', () => {
  const stored = normalizeBrainConfig({
    mode: 'frontier',
    provider: 'openai',
    model: 'gpt-6-astra',
    apiKey: 'never-return-this',
  });
  const safe = publicBrainConfig(stored);

  assert.equal(stored.apiKey, 'never-return-this');
  assert.equal(stored.embeddingMode, 'provider');
  assert.equal(stored.embeddingModel, 'text-embedding-3-small');
  assert.equal(safe.hasApiKey, true);
  assert.equal('apiKey' in safe, false);
  assert.equal(JSON.stringify(safe).includes('never-return-this'), false);
});

test('legacy frontier config migrates without silently changing its embedding route', () => {
  const legacy = {
    version: 1,
    configured: true,
    mode: 'frontier',
    provider: 'openai',
    model: 'legacy-model',
    apiKey: 'legacy-secret',
  };
  const migrated = normalizeBrainConfig(legacy, legacy);
  assert.equal(migrated.version, 2);
  assert.equal(migrated.embeddingMode, 'existing');
  assert.equal(migrated.embeddingModel, '');
  assert.equal(JSON.stringify(publicBrainConfig(migrated)).includes('legacy-secret'), false);
});

test('surface access link becomes a secure cookie and blocks unauthenticated traffic', () => {
  const server = Object.create(NgramArServer.prototype);
  server.surfaceToken = 'surface-secret-with-at-least-thirty-two-characters';
  server.isHttps = true;
  const response = {
    status: 0,
    headers: {},
    writeHead(status, headers = {}) { this.status = status; this.headers = headers; },
    end() {},
  };
  const bootstrap = {
    url: '/?access=surface-secret-with-at-least-thirty-two-characters',
    headers: { host: 'lab.local:3000' },
  };
  assert.equal(server.authorizeSurfaceRequest(bootstrap, response), false);
  assert.equal(response.status, 303);
  assert.equal(response.headers.Location, '/');
  assert.match(response.headers['Set-Cookie'], /HttpOnly; SameSite=Strict; Secure/);

  const cookie = response.headers['Set-Cookie'].split(';', 1)[0];
  assert.equal(server.requestHasSurfaceAccess({ url: '/ws', headers: { cookie } }), true);

  const denied = { ...response, status: 0, headers: {} };
  assert.equal(server.authorizeSurfaceRequest({ url: '/', headers: {} }, denied), false);
  assert.equal(denied.status, 401);
});

test('brain config keeps frontier credentials server-side across private route toggles', () => {
  const frontier = normalizeBrainConfig({
    mode: 'frontier',
    provider: 'openai',
    model: 'gpt-6-astra',
    apiKey: 'toggle-only-secret',
  });
  const privateRoute = normalizeBrainConfig({
    mode: 'private',
    provider: 'remote_gateway',
    model: '',
    apiKey: '',
  }, frontier);
  const safe = publicBrainConfig(privateRoute);

  assert.equal(privateRoute.apiKey, '');
  assert.equal(safe.mode, 'private');
  assert.equal(safe.savedFrontierProfiles.openai.hasApiKey, true);
  assert.equal(JSON.stringify(safe).includes('toggle-only-secret'), false);
  assert.equal('frontierProfiles' in runtimeBrainConfig(privateRoute), false);

  const restored = normalizeBrainConfig({
    mode: 'frontier',
    provider: 'openai',
    model: 'gpt-6-astra',
    apiKey: '',
  }, privateRoute);
  assert.equal(restored.apiKey, 'toggle-only-secret');
});

test('motion provider keeps credentials server-side and returns a playable clip action', async () => {
  let received;
  const server = createServer((request, response) => {
    const chunks = [];
    request.on('data', (chunk) => chunks.push(chunk));
    request.on('end', () => {
      received = {
        authorization: request.headers.authorization,
        body: JSON.parse(Buffer.concat(chunks).toString('utf8')),
      };
      response.writeHead(200, { 'Content-Type': 'application/json' });
      response.end(JSON.stringify({
        clip: {
          url: 'https://assets.invalid/generated.glb',
          format: 'glb',
          name: 'generated-step',
        },
      }));
    });
  });
  server.listen(0, '127.0.0.1');
  await once(server, 'listening');
  const { port } = server.address();

  try {
    const provider = new MotionProviderClient({
      endpoint: `http://127.0.0.1:${port}`,
      token: 'private-provider-token',
    });
    const action = await provider.generate({
      requestId: 'motion-1',
      prompt: 'step forward and wave',
      durationSeconds: 3,
      constraints: { rootTarget: 'forward' },
    }, 'ar-session');

    assert.equal(received.authorization, 'Bearer private-provider-token');
    assert.equal(received.body.output.skeleton, 'mixamo-humanoid');
    assert.equal(action.type, 'action:play_motion_clip');
    assert.equal(action.clipUrl, 'https://assets.invalid/generated.glb');
    assert.equal(JSON.stringify(action).includes('private-provider-token'), false);
  } finally {
    server.close();
    await once(server, 'close');
  }
});

test('entity bridge reconnects and starts a fresh authenticated session', async () => {
  const server = createServer();
  const wss = new WebSocketServer({ server });
  let connectionCount = 0;
  let eventCount = 0;
  let activeSocket;

  wss.on('connection', (socket, request) => {
    connectionCount += 1;
    activeSocket = socket;
    assert.equal(request.headers.authorization, 'Bearer bridge-token');
    socket.on('message', (raw) => {
      const message = JSON.parse(raw.toString());
      if (message.type === 'session.start') {
        socket.send(JSON.stringify({ type: 'session.ready' }));
      } else if (message.type === 'session.event') {
        eventCount += 1;
        socket.send(JSON.stringify({
          type: 'actions',
          replyTo: message.id,
          actions: [{ type: 'action:speak', text: `reply-${eventCount}` }],
        }));
      } else if (message.type === 'ping') {
        socket.send(JSON.stringify({ type: 'pong' }));
      }
    });
  });

  server.listen(0, '127.0.0.1');
  await once(server, 'listening');
  const { port } = server.address();
  const binding = new EntityBridgeBinding(
    { bridgeUrl: `ws://127.0.0.1:${port}`, token: 'bridge-token' },
    { sessionId: 'ar-session', shellName: 'Test', shellSlug: 'test' },
  );
  const proactive = [];
  binding.onProactiveAction((actions) => proactive.push(...actions));

  try {
    await binding.start('test prompt');
    const first = await binding.handleEvent({ type: 'event:user_speech', text: 'one' });
    assert.equal(first[0].text, 'reply-1');

    activeSocket.close();
    await once(activeSocket, 'close');
    await new Promise((resolve) => setTimeout(resolve, 25));
    assert.equal(proactive.at(-1)?.type, 'action:set_agent_state');
    assert.equal(proactive.at(-1)?.state, 'idle');

    const second = await binding.handleEvent({ type: 'event:user_speech', text: 'two' });
    assert.equal(second[0].text, 'reply-2');
    assert.equal(connectionCount, 2);
  } finally {
    await binding.stop();
    for (const client of wss.clients) client.terminate();
    await new Promise((resolve) => wss.close(resolve));
    await new Promise((resolve, reject) => {
      server.close((error) => error ? reject(error) : resolve());
    });
  }
});

test('entity bridge clears replayed activity if it disconnects during brain restore', async () => {
  const server = createServer();
  const wss = new WebSocketServer({ server });

  wss.on('connection', (socket) => {
    socket.on('message', (raw) => {
      const message = JSON.parse(raw.toString());
      if (message.type === 'session.start') {
        socket.send(JSON.stringify({ type: 'session.ready' }));
        socket.send(JSON.stringify({
          type: 'actions',
          actions: [{ type: 'action:set_agent_state', state: 'messaging' }],
        }));
      } else if (message.type === 'brain.configure') {
        socket.close();
      }
    });
  });

  server.listen(0, '127.0.0.1');
  await once(server, 'listening');
  const { port } = server.address();
  const binding = new EntityBridgeBinding(
    {
      bridgeUrl: `ws://127.0.0.1:${port}`,
      brainConfig: { mode: 'private', provider: 'remote_gateway' },
    },
    { sessionId: 'ar-session', shellName: 'Test', shellSlug: 'test' },
  );
  const proactive = [];
  binding.onProactiveAction((actions) => proactive.push(...actions));

  try {
    await assert.rejects(
      binding.start('test prompt'),
      /disconnected during brain configuration/,
    );
    assert.equal(proactive[0]?.state, 'messaging');
    assert.equal(proactive.at(-1)?.state, 'idle');
  } finally {
    await binding.stop();
    for (const client of wss.clients) client.terminate();
    await new Promise((resolve) => wss.close(resolve));
    await new Promise((resolve, reject) => {
      server.close((error) => error ? reject(error) : resolve());
    });
  }
});

test('entity bridge can hot-swap the shared inference provider', async () => {
  const server = createServer();
  const wss = new WebSocketServer({ server });
  let receivedConfig;

  wss.on('connection', (socket) => {
    socket.on('message', (raw) => {
      const message = JSON.parse(raw.toString());
      if (message.type === 'session.start') {
        socket.send(JSON.stringify({ type: 'session.ready' }));
      } else if (message.type === 'brain.configure') {
        receivedConfig = message.config;
        socket.send(JSON.stringify({
          type: 'brain.configured',
          replyTo: message.id,
          ok: true,
          status: { provider: message.config.provider, model: message.config.model },
        }));
      }
    });
  });

  server.listen(0, '127.0.0.1');
  await once(server, 'listening');
  const { port } = server.address();
  const binding = new EntityBridgeBinding(
    { bridgeUrl: `ws://127.0.0.1:${port}` },
    { sessionId: 'ar-session', shellName: 'Test', shellSlug: 'test' },
  );

  try {
    await binding.start('test prompt');
    const status = await binding.configureInference({
      mode: 'frontier', provider: 'openai', model: 'gpt-6-astra', apiKey: 'bridge-only-key',
      embeddingMode: 'provider', embeddingModel: 'text-embedding-3-small',
    });
    assert.equal(status.provider, 'openai');
    assert.equal(receivedConfig.apiKey, 'bridge-only-key');
    assert.equal(receivedConfig.embeddingModel, 'text-embedding-3-small');
  } finally {
    await binding.stop();
    for (const client of wss.clients) client.terminate();
    await new Promise((resolve) => wss.close(resolve));
    await new Promise((resolve, reject) => server.close((error) => error ? reject(error) : resolve()));
  }
});
