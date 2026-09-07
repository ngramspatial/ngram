import assert from 'node:assert/strict';
import test from 'node:test';

import {
  DEFAULT_CAMERA_FAR,
  DEFAULT_CAMERA_NEAR,
  getOrbitCameraClipRange,
} from '../packages/surface-webxr/src/camera-clipping.js';

test('WebXR camera defaults retain room-scale depth precision', () => {
  assert.equal(DEFAULT_CAMERA_NEAR, 0.01);
  assert.equal(DEFAULT_CAMERA_FAR, 100);
  assert.equal(DEFAULT_CAMERA_FAR / DEFAULT_CAMERA_NEAR, 10_000);
});

test('adaptive desktop clipping stays precise across orbit scales', () => {
  for (const distance of [0, 0.001, 0.01, 0.1, 2.5, 100, 1_000_000]) {
    const { near, far } = getOrbitCameraClipRange(distance);
    assert.ok(near > 0);
    assert.ok(far > near);
    assert.ok(far / near <= 1_000_000, `${distance}m produced ${far / near}:1`);
    if (distance >= 0.001) {
      assert.ok(near < distance);
      assert.ok(far > distance);
    }
  }

  assert.deepEqual(getOrbitCameraClipRange(2.5), { near: 0.01, far: 250 });
});

test('human-scale geometry has sub-millimetre depth resolution', () => {
  const { near, far } = getOrbitCameraClipRange(2.5);
  const depthBits = 24;
  const depthStepAtAvatar = (
    2.5 ** 2 * (far - near)
    / (far * near * ((2 ** depthBits) - 1))
  );

  assert.ok(depthStepAtAvatar < 0.001, `depth step was ${depthStepAtAvatar}m`);
});

test('invalid orbit distances fall back to safe XR defaults', () => {
  assert.deepEqual(getOrbitCameraClipRange(Number.NaN), {
    near: DEFAULT_CAMERA_NEAR,
    far: DEFAULT_CAMERA_FAR,
  });
  assert.deepEqual(getOrbitCameraClipRange(-1), {
    near: DEFAULT_CAMERA_NEAR,
    far: DEFAULT_CAMERA_FAR,
  });
});
