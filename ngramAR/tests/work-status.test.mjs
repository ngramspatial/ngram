import assert from 'node:assert/strict';
import test from 'node:test';
import { WorkStatusStore, duration } from '../packages/surface-webxr/src/work-status.ts';
const event = (overrides = {}) => ({ runId: 'one', instanceId: 'epoch', scope: 'turn', status: 'running', stage: 'model_wait',
  sequence: 1, timestamp: 100, elapsedMs: 120000, stageElapsedMs: 120000, idleMs: 120000,
  attempt: 1, maxAttempts: 3, timeoutMs: 1800000, ...overrides });

test('long request shows elapsed and limit; heartbeat does not erase the wait', () => {
  const store = new WorkStatusStore(); store.update(event(), 1000);
  let view = store.views(6000)[0];
  assert.equal(view.elapsed, '2m 05s');
  assert.equal(view.label, 'Waiting for model');
  assert.equal(view.caution, 'No new progress reported');
  assert.ok(view.details.includes('Request limit 30m 00s'));
  store.update(event({ sequence: 2, heartbeat: true, idleMs: 125000, elapsedMs: 125000 }), 6000);
  view = store.views(6000)[0];
  assert.equal(view.history.length, 1);
  assert.ok(view.details.includes('Last progress 2m 05s ago'));
});
test('silent or disconnected worker is not presented as live progress', () => {
  const store = new WorkStatusStore(); store.update(event(), 1000);
  assert.equal(store.views(37000)[0].caution, 'Live status unavailable');
  store.connected = false;
  assert.ok(store.views(2000)[0].details.includes('Connection lost · last known state'));
});
test('retries, Blender rendering and revision have distinct labels', () => {
  const store = new WorkStatusStore(); store.update(event({ stage: 'retry_wait', retryDelayMs: 5000, stageElapsedMs: 0 }), 1000);
  assert.ok(store.views(2000)[0].details.includes('Retry in 4s'));
  store.update(event({ sequence: 2, stage: 'rendering', project: 'orion', revision: 3 }), 2000);
  assert.equal(store.views(2000)[0].label, 'Rendering in Blender');
  assert.ok(store.views(2000)[0].details.includes('orion · Revision 3'));
});
test('stop ends chat feedback without stopping or hiding a background goal', () => {
  const store = new WorkStatusStore(); store.update(event(), 1000);
  store.update(event({ runId: 'goal', scope: 'code_task', phase: 4, mode: 'verify' }), 1000);
  store.stopTurns(2000);
  assert.equal(store.views(2000)[0].label, 'Stopped');
  assert.equal(store.views(2000)[1].active, true);
  assert.ok(store.views(2000)[1].details.includes('Coding goal · Phase 4 · Verification'));
  assert.equal(store.update(event({ sequence: 2 }), 3000), false);
  assert.equal(store.views(20000).length, 1);
});
test('stale frames are ignored but a restarted goal may reset its sequence', () => {
  const store = new WorkStatusStore(); store.update(event({ sequence: 6 }), 1000);
  assert.equal(store.update(event({ sequence: 5 })), false);
  assert.equal(store.update(event({ instanceId: 'new-worker', timestamp: 200 })), true);
  assert.equal(duration(86400000), '24h 0m');
});
