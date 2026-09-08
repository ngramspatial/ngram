import assert from 'node:assert/strict';
import test, { after } from 'node:test';
import { build } from 'esbuild';
import { mkdtemp, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join, resolve, sep } from 'node:path';
import { pathToFileURL } from 'node:url';
const dir = await mkdtemp(join(tmpdir(), 'ngram-environment-'));
after(async () => { assert.ok(resolve(dir).startsWith(resolve(tmpdir()) + sep)); await rm(dir, { recursive: true, force: true }); });
await build({ stdin: { contents: `
export { SceneEnvironment } from './packages/surface-webxr/src/scene-environment.ts';
export { EnvironmentManager } from './packages/surface-webxr/src/environment-manager.ts';
export { parseEnvironmentPatch } from './packages/surface-webxr/src/environment-contract.ts';
export * as THREE from 'three';`, resolveDir: resolve('.') }, bundle: true, platform: 'node', format: 'esm', outfile: join(dir, 'suite.mjs'), logLevel: 'silent' });
const { SceneEnvironment, EnvironmentManager, parseEnvironmentPatch, THREE: T } = await import(pathToFileURL(join(dir, 'suite.mjs')));
function fixture(prepare) {
  const scene = new T.Scene(); scene.background = new T.Color('#aabbcc'); scene.environment = new T.Texture();
  const ground = new T.Group(); ground.name = 'ground-stage'; scene.add(ground);
  const lights = { key: new T.DirectionalLight(), fill: new T.DirectionalLight(), rim: new T.DirectionalLight(), hemi: new T.HemisphereLight(), ambient: new T.AmbientLight() };
  Object.values(lights).forEach(light => scene.add(light));
  const renderer = { toneMappingExposure: 1 };
  const resources = [];
  const factory = async () => { const resource = { texture: new T.Texture(), environment: new T.Texture(), disposed: 0, dispose() { this.disposed++; } }; resources.push(resource); return resource; };
  return { scene, renderer, lights, ground, resources, factory, env: new SceneEnvironment(scene, renderer, lights, prepare ?? factory) };
}

test('environment validates blocks before changing a live scene', async () => {
  const { env } = fixture();
  for (const patch of [{ exposure: Infinity }, { sky: { type: 'panorama', url: 'file:///secret' } }, { sky: { elevation: 100 } }, { lighting: { key: { intensity: -1 } } }, { fog: { type: 'linear', near: 5, far: 1 } }, { typo: 1 }]) await assert.rejects(env.configure(patch));
  assert.equal(env.revision, 0);
  assert.deepEqual(env.config, {});
  assert.equal(parseEnvironmentPatch({ sky: {} }).sky.immersive, false);
});

test('skies drive reflections, visibility, lighting and fog; clear restores the scene', async () => {
  const { env, scene, lights, renderer, ground, resources } = fixture();
  const background = scene.background, reflection = scene.environment;
  await env.configure({ sky: { elevation: 8 }, exposure: .7, ground: false, lighting: { fill: { intensity: 2, color: '#6e7dff', position: [2,3,4], target: [1,0,0] } }, fog: { color: '#ffffff', density: .02 } });
  assert.equal(scene.background, resources[0].texture); assert.equal(scene.environment, resources[0].environment);
  assert.equal(renderer.toneMappingExposure, .7); assert.equal(ground.visible, false);
  assert.equal(lights.fill.intensity, 2); assert.equal(lights.fill.color.getHexString(), '6e7dff');
  assert.deepEqual(lights.fill.target.position.toArray(), [1,0,0]); assert.ok(scene.fog.isFogExp2);
  await env.configure({ sky: { ...env.config.sky, visible: false, rotation: 1 } });
  assert.equal(resources.length, 1); assert.equal(scene.background, background); assert.equal(scene.environment, resources[0].environment);
  assert.equal(scene.environmentRotation.y, 1);
  env.clear();
  assert.equal(resources[0].disposed, 1); assert.equal(scene.environment, reflection); assert.equal(scene.fog, null);
  assert.equal(renderer.toneMappingExposure, 1); assert.equal(ground.visible, true); assert.equal(lights.fill.intensity, 1);
  assert.equal(lights.fill.target.matrixWorld.elements[12], 0);
});

test('failed and superseded sky loads preserve the last good state and dispose late resources', async () => {
  const f = fixture(); await f.env.configure({ sky: {} });
  const original = f.env.resource;
  f.env.prepare = async () => { throw Error('Image unavailable'); };
  await assert.rejects(f.env.configure({ sky: { type: 'panorama', url: '/missing.jpg' }, exposure: 4 }), /unavailable/);
  assert.equal(f.env.resource, original); assert.equal(f.renderer.toneMappingExposure, 1); assert.equal(original.disposed, 0);
  assert.equal(f.env.inspect().status, 'error');
  let finish;
  f.env.prepare = () => new Promise(resolve => { finish = resolve; });
  const pending = f.env.configure({ sky: { elevation: 1 } });
  f.env.clear();
  const late = await f.factory(); finish(late);
  await assert.rejects(pending);
  assert.equal(late.disposed, 1); assert.equal(f.env.resource, null); assert.equal(f.env.status, 'ready');
});

test('XR passthrough hides sky and fog until immersion is explicitly enabled', async () => {
  const { env, scene, resources } = fixture();
  await env.configure({ sky: {}, fog: { density: .05 } });
  env.setAR(true); assert.equal(scene.background, null); assert.equal(scene.fog, null);
  assert.equal(scene.environment, resources[0].environment);
  await env.configure({ sky: { ...env.config.sky, immersive: true } });
  assert.equal(scene.background, resources[0].texture); assert.ok(scene.fog);
  await env.configure({ sky: { ...env.config.sky, immersive: false } });
  assert.equal(scene.background, null);
  env.setAR(false); assert.equal(scene.background, resources[0].texture); assert.ok(scene.fog);
});

test('environment save/restore and theme refresh preserve skies and human adjustments', async () => {
  const oldDocument = globalThis.document;
  globalThis.document = { createElement: () => ({ getContext: () => ({ createRadialGradient: () => ({ addColorStop() {} }), fillRect() {} }) }) };
  try {
    const a = fixture(), b = fixture();
    const manager = new EnvironmentManager(); manager.attach(a.scene, a.renderer, a.lights); manager.sceneEnvironment.prepare = a.factory;
    await manager.handle('configure', { sky: { elevation: 12 }, exposure: 1.5 });
    const state = manager.getSavedState();
    const restored = new EnvironmentManager(); restored.attach(b.scene, b.renderer, b.lights); restored.sceneEnvironment.prepare = b.factory;
    await restored.loadSavedState(state);
    b.scene.background = new T.Color('#ffffff'); b.renderer.toneMappingExposure = .5;
    restored.reapplyState(); restored.update(2);
    assert.equal(b.scene.background, b.resources[0].texture); assert.equal(b.renderer.toneMappingExposure, 1.5);
    assert.deepEqual(restored.getSavedState(), state);
    state.scene.sky.elevation = 99; assert.equal(restored.sceneEnvironment.config.sky.elevation, 12);
    await restored.handle('configure', { sky: { ...restored.sceneEnvironment.config.sky, visible: false } });
    const themeBackground = new T.Color('#111111'); b.scene.background = themeBackground;
    restored.reapplyState(); restored.update(2);
    assert.equal(b.scene.background, themeBackground);
    restored.setEnvironment('night'); restored.update(2);
    assert.deepEqual(restored.sceneEnvironment.config, {}); assert.equal(b.resources[0].disposed, 1);
  } finally { globalThis.document = oldDocument; }
});

test('an unavailable sky host during restore retains its saved URL for the next session', async () => {
  const f = fixture();
  const manager = new EnvironmentManager(); manager.attach(f.scene, f.renderer, f.lights);
  manager.sceneEnvironment.prepare = async () => { throw Error('Host offline'); };
  const saved = { scene: { sky: { type: 'panorama', url: '/api/sky.jpg' } } };
  await assert.rejects(manager.loadSavedState(saved), /offline/);
  assert.deepEqual(manager.getSavedState(), saved);
  manager.sceneEnvironment.prepare = f.factory;
  await manager.handle('configure', { sky: { type: 'atmosphere' } });
  assert.equal(manager.getSavedState().scene.sky.type, 'atmosphere');
});
