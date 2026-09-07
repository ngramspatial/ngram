import assert from 'node:assert/strict';
import test from 'node:test';
import { build } from 'esbuild';
import { fileURLToPath } from 'node:url';
import { dispatchWithReceipt } from '../packages/surface-webxr/src/action-receipts.ts';
import { NgramArServer } from '../packages/gateway/dist/server.js';

const bundle = await build({
  stdin: {
    contents: `export { SceneObjectManager } from './scene-object-manager.ts';
      export { initPhysics } from './physics-world.ts';
      export { Scene, Vector3 } from 'three';`,
    resolveDir: fileURLToPath(new URL('../packages/surface-webxr/src', import.meta.url)),
    loader: 'ts',
  },
  bundle: true, write: false, platform: 'node', format: 'esm', target: 'es2022',
});
const { SceneObjectManager, initPhysics, Scene, Vector3 } = await import(
  `data:text/javascript;base64,${Buffer.from(bundle.outputFiles[0].contents).toString('base64')}`
);

test('a purple bouncy ball exists in the physics scene before its completed receipt', async () => {
  await initPhysics();
  const scene = new Scene();
  const objects = new SceneObjectManager();
  objects.attach(scene);
  const action = { type: 'action:spawn_toy', actionId: 'toy-1', sessionId: 'body', timestamp: 42 };
  const receipts = [];
  dispatchWithReceipt(action, () => objects.spawnToy('purple-ball', 'bouncy_ball', new Vector3(), {
    color: '#800080', impulse: { x: 0, y: 1.5, z: 0 },
  }), (event) => {
    receipts.push(event);
    const [{ entry, mesh }] = objects.getDraggableMeshes();
    assert.equal(entry.id, 'purple-ball');
    assert.equal(mesh.geometry.type, 'SphereGeometry');
    assert.equal(mesh.material.color.getHexString(), '800080');
    assert.equal(entry.physicsHandle.body.linvel().y, 1.5);
    assert.equal(scene.children.length, 1);
    assert.equal(event.status, 'completed');
    assert.equal(event.completedActionId, 'toy-1');
    assert.equal(event.sessionId, 'body');
    assert.equal(event.actionTimestamp, 42);
  });
  assert.equal(receipts.length, 1);
  objects.clearAll();
});

test('renderer failures and disabled Vision report failure instead of success', () => {
  for (const [type, message] of [
    ['action:spawn_toy', 'Physics initialization failed'],
    ['action:request_capture', 'Vision is disabled by user'],
  ]) {
    const receipts = [];
    dispatchWithReceipt({ type, actionId: 'failed' }, () => {
      throw new Error(message);
    }, event => receipts.push(event));
    assert.equal(receipts.length, 1);
    assert.equal(receipts[0].status, 'failed');
    assert.equal(receipts[0].error, message);
  }
});

test('starting speech, motion or media does not claim completed playback', () => {
  for (const type of ['action:speak', 'action:move_to', 'action:play_motion_clip', 'action:play_youtube']) {
    dispatchWithReceipt({ type, actionId: 'async' }, () => {}, event => {
      assert.equal(event.status, 'accepted');
    });
  }
});

test('gateway forwards correlated receipts to the entity binding without a response action', async () => {
  const gateway = Object.create(NgramArServer.prototype);
  const forwarded = [];
  const client = {
    sessionId: 'body',
    bindingReady: Promise.resolve(),
    binding: { async handleEvent(event) { forwarded.push(event); return []; } },
  };
  await gateway.handleMessage(client, Buffer.from(JSON.stringify({
    type: 'event:action_completed', completedActionId: 'toy-1', status: 'completed',
  })));
  assert.equal(forwarded.length, 1);
  assert.equal(forwarded[0].completedActionId, 'toy-1');
  await gateway.handleMessage(client, Buffer.from(JSON.stringify({
    type: 'event:action_completed', action: 'legacy-gesture',
  })));
  assert.equal(forwarded.length, 1);
});
