import assert from 'node:assert/strict';
import test from 'node:test';
import { WorkStatusStore, duration } from '../packages/surface-webxr/src/work-status.ts';
const event = (overrides = {}) => ({ runId: 'one', instanceId: 'epoch', scope: 'code_task', status: 'running', stage: 'model_wait',
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
  const store = new WorkStatusStore(); store.update(event({scope: 'turn', stage: 'rendering'}), 1000);
  store.update(event({ runId: 'goal', scope: 'code_task', phase: 4, mode: 'verify' }), 1000);
  store.stopTurns(2000);
  assert.equal(store.views(2000).length, 1);
  assert.equal(store.views(2000)[0].active, true);
  assert.ok(store.views(2000)[0].details.includes('Coding goal · Phase 4 · Verification'));
  assert.equal(store.update(event({ sequence: 2 }), 3000), false);
  assert.equal(store.views(20000).length, 1);
});

test('ordinary chat, retries and lookups stay hidden regardless of elapsed time', () => {
  for (const data of [{stage: 'model_wait'}, {stage: 'retry_wait'}, {stage: 'preparing'},
    {stage: 'tool_running', tool: 'search_web'}, {stage: 'tool_running', tool: 'read_file'},
    {stage: 'tool_running', tool: 'ar_request_capture'},
    {stage: 'tool_running', tool: 'ar_blender', operation: 'status'},
    {stage: 'tool_running', tool: 'ar_blender', operation: 'capabilities'}]) {
    const store = new WorkStatusStore(); store.update(event({scope: 'turn', ...data}), 1000);
    assert.deepEqual(store.views(2000), [], JSON.stringify(data));
  }
});

test('quick execution does not show a panel or leave a finished trace', () => {
  const store = new WorkStatusStore();
  store.update(event({scope: 'turn', stage: 'tool_running', tool: 'run_command', elapsedMs: 500, stageElapsedMs: 0}), 1000);
  assert.deepEqual(store.views(1200), []);
  store.update(event({scope: 'turn', sequence: 2, stage: 'processing', elapsedMs: 900, stageElapsedMs: 0}), 1400);
  store.update(event({scope: 'turn', sequence: 3, elapsedMs: 120000, stageElapsedMs: 100000}), 2000);
  assert.deepEqual(store.views(3000), []);
  store.update(event({scope: 'turn', sequence: 4, status: 'complete'}), 4000);
  assert.deepEqual(store.views(4000), []);
});

test('sustained execution qualifies and remains visible through the next model request', () => {
  const store = new WorkStatusStore();
  store.update(event({scope: 'turn', stage: 'rendering', elapsedMs: 2000, stageElapsedMs: 0}), 1000);
  assert.deepEqual(store.views(15999), []);
  assert.equal(store.views(16000)[0].label, 'Rendering in Blender');
  store.update(event({scope: 'turn', sequence: 2}), 18000);
  assert.equal(store.views(18000)[0].label, 'Waiting for model');
  store.update(event({scope: 'turn', sequence: 3, status: 'complete'}), 19000);
  assert.deepEqual(store.views(19000), []);
});

test('multi-step authoring qualifies after thirty seconds but a single quick edit does not', () => {
  const store = new WorkStatusStore();
  store.update(event({scope: 'turn', stage: 'tool_running', tool: 'apply_patch', elapsedMs: 1000, stageElapsedMs: 0}), 1000);
  store.update(event({scope: 'turn', sequence: 2, elapsedMs: 2000, stageElapsedMs: 0}), 2000);
  assert.deepEqual(store.views(3000), []);
  store.update(event({scope: 'turn', sequence: 3, stage: 'tool_running', tool: 'execute_python', elapsedMs: 32000, stageElapsedMs: 0}), 32000);
  assert.equal(store.views(32000).length, 1);
});

test('a throttled background tab still recognizes a completed long tool call', () => {
  const store = new WorkStatusStore();
  store.update(event({scope: 'turn', stage: 'tool_running', tool: 'run_command', elapsedMs: 1000, stageElapsedMs: 0}), 1000);
  store.update(event({scope: 'turn', sequence: 2, elapsedMs: 25000, stageElapsedMs: 0}), 25000);
  assert.equal(store.views(25000)[0].label, 'Waiting for model');
});
test('stale frames are ignored but a restarted goal may reset its sequence', () => {
  const store = new WorkStatusStore(); store.update(event({ sequence: 6 }), 1000);
  assert.equal(store.update(event({ sequence: 5 })), false);
  assert.equal(store.update(event({ instanceId: 'new-worker', timestamp: 200 })), true);
  assert.equal(duration(86400000), '24h 0m');
});

test('background progress and terminal stop reasons remain visible without claiming active work', () => {
  const store = new WorkStatusStore();
  store.update(event({ summary: 'Built six screens; checking readability.' }), 1000);
  assert.equal(store.views(1000)[0].summary, 'Built six screens; checking readability.');
  store.update(event({ sequence: 2, status: 'blocked', summary: 'Screens saved.',
    reason: 'Reconnect the original room to verify.', nextSteps: 'Inspect the tablet.' }), 2000);
  const view = store.views(2000)[0];
  assert.equal(view.active, false);
  assert.equal(view.label, 'Blocked');
  assert.equal(view.reason, 'Reconnect the original room to verify.');
  assert.equal(view.nextSteps, 'Inspect the tablet.');
  assert.deepEqual(store.views(122000), []);
});
