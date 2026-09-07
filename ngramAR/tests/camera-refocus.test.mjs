import assert from 'node:assert/strict';
import test from 'node:test';

import * as THREE from 'three';
import { refocusOrbitOnObject } from '../packages/surface-webxr/src/camera-refocus.js';

function makeControls() {
  return {
    target: new THREE.Vector3(),
    updateCalls: 0,
    update() { this.updateCalls++; },
  };
}

test('refocus targets the world-space center of a moved and scaled model', () => {
  const controls = makeControls();
  const group = new THREE.Group();
  group.position.set(4, 0, -2);
  group.scale.setScalar(2);
  const geometry = new THREE.BoxGeometry(1, 2, 1);
  const mesh = new THREE.Mesh(geometry, new THREE.MeshBasicMaterial());
  mesh.position.y = 1;
  group.add(mesh);

  const focused = refocusOrbitOnObject(
    controls,
    group,
    new THREE.Vector3(99, 99, 99),
    10,
  );

  assert.equal(focused, true);
  assert.deepEqual(controls.target.toArray(), [4, 2, -2]);
  assert.equal(controls.updateCalls, 1);
  geometry.dispose();
  mesh.material.dispose();
});

test('refocus falls back to the avatar anchor when geometry has no bounds', () => {
  const controls = makeControls();
  const group = new THREE.Group();

  const focused = refocusOrbitOnObject(
    controls,
    group,
    new THREE.Vector3(1, 2, 3),
    2,
  );

  assert.equal(focused, true);
  assert.deepEqual(controls.target.toArray(), [1, 3.1, 3]);
  assert.equal(controls.updateCalls, 1);
});

test('refocus fails closed when no usable model position exists', () => {
  const controls = makeControls();
  assert.equal(refocusOrbitOnObject(controls, null, null, 1), false);
  assert.equal(
    refocusOrbitOnObject(controls, new THREE.Group(), { x: NaN, y: 0, z: 0 }, 1),
    false,
  );
  assert.equal(controls.updateCalls, 0);
});
