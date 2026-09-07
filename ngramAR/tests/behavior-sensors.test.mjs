import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';
import { transform } from 'esbuild';

// The browser package bundles its TypeScript instead of emitting individual modules.
const source = await readFile(new URL('../packages/surface-webxr/src/behavior-sensors.ts', import.meta.url), 'utf8');
const { code } = await transform(source, { loader: 'ts', format: 'esm', target: 'es2022' });
const { BehaviorSensorManager } = await import(`data:text/javascript;base64,${Buffer.from(code).toString('base64')}`);

function setup({ type = 'proximity', params = { distance: 2, duration: 2 }, cooldown = 120 } = {}) {
  const events = [];
  const sensors = new BehaviorSensorManager({ send: (event) => events.push(event) });
  sensors.configure([{
    id: 'encounter', trigger: { type, params }, cooldown, priority: 5, mode: 'deliberative',
  }]);
  return { sensors, events };
}

function context(overrides = {}) {
  return {
    userPosition: { x: 0, y: 0, z: 0 },
    userGaze: { x: 1, y: 0, z: 0 },
    agentPosition: { x: 1, y: 0, z: 0 },
    agentState: 'idle', isSpeaking: false,
    timeSinceLastInteraction: 0, timeSinceLastSpeech: 0,
    sceneAnchorCount: 0, prevSceneAnchorCount: 0,
    ...overrides,
  };
}

const away = () => context({ userPosition: { x: 10, y: 0, z: 0 } });

test('first encounter fires after its dwell time, without an initial cooldown', () => {
  const { sensors, events } = setup();
  sensors.update(context(), 0);
  sensors.update(context(), 1999);
  assert.equal(events.length, 0);
  sensors.update(context(), 2000);
  assert.equal(events.length, 1);
  assert.equal(events[0].behaviorId, 'encounter');
  assert.equal(events[0].context.userDistance, 1);
});

test('an uninterrupted encounter does not fire again when cooldown expires', () => {
  const { sensors, events } = setup({ cooldown: 5 });
  sensors.update(context(), 10000);
  sensors.update(context(), 12000);
  sensors.update(context(), 17000);
  sensors.update(context(), 60000);
  assert.equal(events.length, 1);
});

test('leaving and returning during cooldown rearms the next encounter', () => {
  const { sensors, events } = setup({ cooldown: 5 });
  sensors.update(context(), 10000);
  sensors.update(context(), 12000);
  sensors.update(away(), 13000);
  sensors.update(context(), 14000);
  sensors.update(context(), 16000);
  assert.equal(events.length, 1);
  sensors.update(context(), 17000);
  assert.equal(events.length, 2);
});

test('an interrupted dwell during cooldown does not count toward the next encounter', () => {
  const { sensors, events } = setup({ cooldown: 5 });
  sensors.update(context(), 10000);
  sensors.update(context(), 12000);
  sensors.update(away(), 13000);
  sensors.update(context(), 14000);
  sensors.update(away(), 15900);
  sensors.update(context(), 16900);
  sensors.update(context(), 17000);
  sensors.update(context(), 18899);
  assert.equal(events.length, 1);
  sensors.update(context(), 18900);
  assert.equal(events.length, 2);
});

test('a first immediate trigger at clock zero still starts a real cooldown', () => {
  const { sensors, events } = setup({ params: { distance: 2 }, cooldown: 5 });
  sensors.update(context(), 0);
  assert.equal(events.length, 1);
  sensors.update(away(), 1000);
  sensors.update(context(), 2000);
  sensors.update(context(), 4999);
  assert.equal(events.length, 1);
  sensors.update(context(), 5000);
  assert.equal(events.length, 2);
});

test('looking away resets the gaze dwell before the first reaction', () => {
  const { sensors, events } = setup({ type: 'gaze', params: { duration: 3, angle: 0.85 } });
  sensors.update(context(), 0);
  sensors.update(context({ userGaze: { x: -1, y: 0, z: 0 } }), 2000);
  sensors.update(context(), 2500);
  sensors.update(context(), 5499);
  assert.equal(events.length, 0);
  sensors.update(context(), 5500);
  assert.equal(events.length, 1);
});

test('non-spatial triggers can fire without an agent pose', () => {
  const { sensors, events } = setup({ type: 'idle_timeout', params: { seconds: 30 }, cooldown: 0 });
  assert.doesNotThrow(() => sensors.update(context({
    agentPosition: null, timeSinceLastInteraction: 31,
  }), 31000));
  assert.equal(events.length, 1);
  assert.equal('userDistance' in events[0].context, false);
  assert.equal(events[0].context.idleSeconds, 31);
});

test('reconfiguring a shell starts a fresh encounter', () => {
  const { sensors, events } = setup({ params: { distance: 2 }, cooldown: 5 });
  sensors.update(context(), 10000);
  sensors.configure([{
    id: 'new-shell', trigger: { type: 'proximity', params: { distance: 2 } },
    cooldown: 120, priority: 5, mode: 'deliberative',
  }]);
  sensors.update(context(), 11000);
  assert.equal(events.length, 2);
  assert.equal(events[1].behaviorId, 'new-shell');
});
