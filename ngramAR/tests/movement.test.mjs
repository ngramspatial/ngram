import assert from 'node:assert/strict';
import test from 'node:test';
import { Vector3 } from 'three';
import { resolveMoveDestination } from '../packages/surface-webxr/src/movement.ts';
import { dispatchWithReceipt } from '../packages/surface-webxr/src/action-receipts.ts';

test('all named targets accepted by ar_move_to resolve to actual destinations', () => {
  const position = new Vector3(10.236, 0.4, 10.856);
  const user = new Vector3(10.236, 1.7, 15.856);
  for (const [target, expected] of Object.entries({
    left: [-1.5, 0, 0], right: [1.5, 0, 0], forward: [0, 0, -1.5], away: [0, 0, 2],
    random: [0.8, 0, 0], user: [0, 0, 3.8],
  })) {
    const destination = resolveMoveDestination(target, position, user, () => 0);
    const delta = destination.clone().sub(position);
    expected.forEach((value, index) => assert.ok(Math.abs(delta.toArray()[index] - value) < 1e-10, target));
    assert.ok(destination.distanceTo(position) > 0.5, target);
    assert.equal(destination.y, position.y);
  }
  assert.deepEqual(position.toArray(), [10.236, 0.4, 10.856]);
});

test('coordinate offsets retain existing semantics and do not change the floor height', () => {
  assert.deepEqual(resolveMoveDestination({ x: 2, y: 99, z: -3 }, new Vector3(4, 0.5, 8), new Vector3()).toArray(), [6, 0.5, 5]);
  assert.deepEqual(resolveMoveDestination({ z: 2 }, new Vector3(4, 0.5, 8), new Vector3()).toArray(), [4, 0.5, 10]);
});

test('approaching a nearby user does not overshoot or move toward the headset height', () => {
  const position = new Vector3(2, 0, 2);
  for (const user of [new Vector3(2, 1.8, 2), new Vector3(2.5, 1.8, 2)]) {
    assert.deepEqual(resolveMoveDestination('user', position, user).toArray(), position.toArray());
  }
});

test('unsupported or malformed movement fails its renderer receipt instead of being accepted', () => {
  for (const target of ['desk', 'constructor', null, {}, [], { x: '2' }, { z: Infinity }, { x: NaN }]) {
    const receipts = [];
    dispatchWithReceipt({ type: 'action:move_to', actionId: 'bad-move' }, () => {
      resolveMoveDestination(target, new Vector3(), new Vector3());
    }, receipt => receipts.push(receipt));
    assert.equal(receipts.length, 1);
    assert.equal(receipts[0].status, 'failed');
    assert.match(receipts[0].error, /Invalid movement target/);
  }
});
