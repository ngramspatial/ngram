import assert from 'node:assert/strict';
import test from 'node:test';
import { EntityBridgeBinding } from '../packages/bindings/dist/entity-bridge-binding.js';
import { resolveEntityBridgeConfig } from '../packages/gateway/dist/resolve-entity-bridge.js';

test('long bridge requests survive the former ten-minute cutoff and still stop', async (t) => {
  t.mock.timers.enable({ apis: ['setTimeout'] });
  const config = resolveEntityBridgeConfig({ options: { bridgeUrl: 'ws://localhost:1', turnTimeoutSeconds: 86460 } });
  const binding = new EntityBridgeBinding(config, { sessionId: 'long', shellName: 'Rook', shellSlug: 'rook' });
  binding.ensureConnected = async () => {};
  binding.ws = { readyState: 1, send() {}, close() {} };
  let settled = false;
  const work = binding.handleEvent({ type: 'event:user_speech', text: 'build' }).finally(() => { settled = true; });
  await Promise.resolve();
  t.mock.timers.tick(600001);
  await Promise.resolve();
  assert.equal(settled, false);
  assert.equal(binding.pending.size, 1);
  await binding.routeMessage(JSON.stringify({ type: 'actions', actions: [{ type: 'action:turn_cancelled', reason: 'stopped' }] }));
  assert.deepEqual(await work, []);
  assert.equal(binding.pending.size, 0);
  await binding.stop();
});

test('invalid timeouts cannot overflow Node timers into an immediate cutoff', () => {
  for (const value of [0, -1, 'broken', Infinity, 2147484]) {
    assert.throws(() => resolveEntityBridgeConfig({ options: { bridgeUrl: 'ws://localhost:1', turnTimeoutSeconds: value } }), /timeout/);
  }
});
