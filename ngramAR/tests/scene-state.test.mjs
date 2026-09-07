import assert from 'node:assert/strict';
import test from 'node:test';

import {
  SCENE_STATE_KEY,
  loadSceneState,
  normalizeSceneState,
  removeLegacySceneState,
  writeSceneState,
} from '../packages/surface-webxr/src/scene-state.js';

function memoryStorage(initial = {}) {
  const values = new Map(Object.entries(initial));
  return {
    getItem(key) { return values.has(key) ? values.get(key) : null; },
    setItem(key, value) { values.set(key, String(value)); },
    removeItem(key) { values.delete(key); },
    values,
  };
}

test('scene state round-trips every durable workspace domain', () => {
  const storage = memoryStorage();
  const original = {
    version: 1,
    camera: { position: [1, 2, 3], quaternion: [0, 0.5, 0, 0.866], target: [4, 5, 6] },
    avatar: { position: [7, 8, 9], quaternion: [0, 1, 0, 0], scale: 1.6 },
    environment: {
      preset: 'night',
      lighting: { color: '#abcdef', intensity: 0.7, mood: 'cool' },
      background: { gradient: { center: '#111111', edge: '#000000' } },
    },
    objects: [{ id: 'cube', kind: 'primitive', params: { shape: 'cube', position: { x: 3, y: 2, z: 1 } } }],
    panels: [{ id: 'note', type: 'card', content: 'remember', position: { x: 2, y: 2, z: 2 } }],
    drawings: [{ id: 'line', kind: 'line', params: { points: [{ x: 0, y: 0, z: 0 }, { x: 1, y: 1, z: 1 }] } }],
    overlayPanels: {
      terminal: { visible: true, entries: [{ command: 'ngram status' }] },
      youtube: { open: true, videoId: 'abcdefghijk', title: 'Demo' },
      browser: { open: true, url: 'https://example.com', title: 'Reference' },
      apps: [{ id: 'plan', title: 'Plan', htmlContent: '<p>Ship it</p>' }],
    },
    overlayTransforms: { __terminal: { position: { x: 1, y: 1, z: -1 } } },
  };

  writeSceneState(storage, original);
  const restored = loadSceneState(storage);

  assert.deepEqual(restored.camera, original.camera);
  assert.deepEqual(restored.avatar, original.avatar);
  assert.deepEqual(restored.environment, original.environment);
  assert.deepEqual(restored.objects, original.objects);
  assert.deepEqual(restored.panels, original.panels);
  assert.deepEqual(restored.drawings, original.drawings);
  assert.deepEqual(restored.overlayPanels, original.overlayPanels);
  assert.deepEqual(restored.overlayTransforms, original.overlayTransforms);
  assert.ok(restored.updatedAt > 0);
});

test('legacy scene keys migrate atomically without losing existing content', () => {
  const legacyObjects = [{ id: 'old-cube', kind: 'object', params: { position: { x: 4, y: 0, z: -2 } } }];
  const legacyPanels = [{ id: 'old-note', type: 'card', content: 'legacy', pinned: true }];
  const storage = memoryStorage({
    ngram_ar_objects: JSON.stringify(legacyObjects),
    ngram_ar_sticky_notes: JSON.stringify(legacyPanels),
    ngram_ar_placement: JSON.stringify({ x: 8, y: 1, z: 3 }),
    ngram_ar_env: 'cozy',
    ngram_ar_bg: JSON.stringify({ color: '#123456' }),
  });

  const migrated = loadSceneState(storage);

  assert.deepEqual(migrated.objects, legacyObjects);
  assert.deepEqual(migrated.panels, legacyPanels);
  assert.deepEqual(migrated.avatar.position, [8, 1, 3]);
  assert.deepEqual(migrated.environment, {
    preset: 'cozy',
    background: { color: '#123456' },
  });
  assert.ok(storage.getItem(SCENE_STATE_KEY));

  removeLegacySceneState(storage);
  assert.equal(storage.getItem('ngram_ar_objects'), null);
  assert.equal(storage.getItem('ngram_ar_env'), null);
});

test('a legacy background-only override keeps the theme lighting baseline', () => {
  const storage = memoryStorage({
    ngram_ar_bg: JSON.stringify({ color: '#123456' }),
  });

  const migrated = loadSceneState(storage);
  assert.deepEqual(migrated.environment, {
    background: { color: '#123456' },
  });
});

test('corrupt and non-finite transforms fail closed', () => {
  const normalized = normalizeSceneState({
    version: 1,
    camera: { position: [0, Number.NaN, 2], target: [0, 0, 0] },
    avatar: { position: { x: 0, y: Infinity, z: 0 }, scale: 2 },
    objects: 'not-an-array',
    drawings: null,
  });

  assert.equal(normalized.version, 1);
  assert.equal(normalized.camera, undefined);
  assert.equal(normalized.avatar, undefined);
  assert.deepEqual(normalized.objects, []);
  assert.deepEqual(normalized.drawings, []);
});

test('a corrupt unified record falls back to the legacy snapshot', () => {
  const storage = memoryStorage({
    [SCENE_STATE_KEY]: '{broken',
    ngram_ar_placement: JSON.stringify({ x: 1, y: 2, z: 3 }),
  });

  const state = loadSceneState(storage);
  assert.deepEqual(state.avatar.position, [1, 2, 3]);
});

test('an unsupported unified version falls back to readable legacy state', () => {
  const storage = memoryStorage({
    [SCENE_STATE_KEY]: JSON.stringify({ version: 999, objects: [{ id: 'future' }] }),
    ngram_ar_placement: JSON.stringify({ x: 4, y: 5, z: 6 }),
  });

  const state = loadSceneState(storage);
  assert.deepEqual(state.avatar.position, [4, 5, 6]);
  assert.deepEqual(state.objects, []);
});

test('a blocked migration write never prevents legacy state from loading', () => {
  const storage = memoryStorage({
    ngram_ar_placement: JSON.stringify({ x: 9, y: 8, z: 7 }),
  });
  storage.setItem = () => { throw new Error('blocked'); };

  const state = loadSceneState(storage);
  assert.deepEqual(state.avatar.position, [9, 8, 7]);
  assert.equal(storage.getItem('ngram_ar_placement') !== null, true);
});
