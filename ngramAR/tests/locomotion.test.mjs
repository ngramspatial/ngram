import assert from 'node:assert/strict';
import test from 'node:test';

import { makeLocomotionClipInPlace } from '../packages/surface-webxr/src/locomotion.js';

test('locomotion root drift is removed without flattening natural hip motion', () => {
  const hips = {
    name: 'mixamorigHips.position',
    times: new Float32Array([0, 0.5, 1]),
    values: new Float32Array([
      1, 90, 2,
      3, 94, 8,
      5, 90, 14,
    ]),
    getValueSize: () => 3,
  };
  const spine = {
    name: 'mixamorigSpine.position',
    times: new Float32Array([0, 1]),
    values: new Float32Array([0, 1, 2, 3, 4, 5]),
    getValueSize: () => 3,
  };
  const spineBefore = [...spine.values];

  const result = makeLocomotionClipInPlace({ tracks: [hips, spine] });

  assert.equal(result.normalizedTracks, 1);
  assert.equal(result.horizontalDistance, Math.hypot(4, 12));
  assert.deepEqual([...hips.values], [
    1, 90, 2,
    1, 94, 2,
    1, 90, 2,
  ]);
  assert.deepEqual([...spine.values], spineBefore);
});

test('already in-place locomotion is left unchanged', () => {
  const hips = {
    name: 'Armature|Hips.position',
    times: new Float32Array([0, 1]),
    values: new Float32Array([1, 90, 2, 1, 92, 2]),
    getValueSize: () => 3,
  };
  const before = [...hips.values];

  const result = makeLocomotionClipInPlace({ tracks: [hips] });

  assert.deepEqual(result, { normalizedTracks: 0, horizontalDistance: 0 });
  assert.deepEqual([...hips.values], before);
});
