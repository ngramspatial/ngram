import assert from 'node:assert/strict';
import test from 'node:test';

import * as THREE from 'three';
import {
  DesktopNavigationController,
  isTextEntryTarget,
} from '../packages/surface-webxr/src/desktop-navigation.js';

class FakeDocument extends EventTarget {
  activeElement = null;
  visibilityState = 'visible';
  modalOpen = false;

  querySelector(selector) {
    return selector === '.modal-backdrop.open' && this.modalOpen ? {} : null;
  }
}

function keyEvent(type, code, options = {}) {
  const event = new Event(type, { cancelable: true });
  Object.defineProperties(event, {
    code: { value: code },
    ctrlKey: { value: options.ctrlKey ?? false },
    metaKey: { value: options.metaKey ?? false },
    altKey: { value: options.altKey ?? false },
  });
  return event;
}

function makeController(options = {}) {
  const camera = new THREE.PerspectiveCamera();
  camera.position.set(0, 1.2, 2.5);
  camera.lookAt(0, 0.9, 0);
  const controls = {
    enabled: true,
    target: new THREE.Vector3(0, 0.9, 0),
  };
  const eventTarget = new EventTarget();
  const documentTarget = new FakeDocument();
  const controller = new DesktopNavigationController(camera, controls, {
    eventTarget,
    documentTarget,
    moveSpeed: options.moveSpeed ?? 2,
    sprintMultiplier: options.sprintMultiplier ?? 3,
  });
  return { camera, controls, controller, eventTarget, documentTarget };
}

test('WASD translates the camera and orbit target together on the ground plane', () => {
  const { camera, controls, controller, eventTarget } = makeController();
  const originalOffset = camera.position.clone().sub(controls.target);
  const keyDown = keyEvent('keydown', 'KeyW');
  eventTarget.dispatchEvent(keyDown);

  assert.equal(keyDown.defaultPrevented, true);
  assert.equal(controller.update(0.1), true);
  assert.ok(camera.position.z < 2.5);
  assert.equal(camera.position.y, 1.2);
  assert.deepEqual(
    camera.position.clone().sub(controls.target).toArray(),
    originalOffset.toArray(),
  );

  controller.dispose();
});

test('diagonal WASD movement is normalized and Shift provides a sprint boost', () => {
  const normal = makeController();
  normal.eventTarget.dispatchEvent(keyEvent('keydown', 'KeyW'));
  normal.eventTarget.dispatchEvent(keyEvent('keydown', 'KeyD'));
  const normalStart = normal.camera.position.clone();
  normal.controller.update(0.1);
  const normalDistance = normal.camera.position.distanceTo(normalStart);

  const sprint = makeController();
  sprint.eventTarget.dispatchEvent(keyEvent('keydown', 'KeyW'));
  sprint.eventTarget.dispatchEvent(keyEvent('keydown', 'KeyD'));
  sprint.eventTarget.dispatchEvent(keyEvent('keydown', 'ShiftLeft'));
  const sprintStart = sprint.camera.position.clone();
  sprint.controller.update(0.1);
  const sprintDistance = sprint.camera.position.distanceTo(sprintStart);

  assert.ok(Math.abs(normalDistance - 0.2) < 1e-10);
  assert.ok(Math.abs(sprintDistance - 0.6) < 1e-10);
  normal.controller.dispose();
  sprint.controller.dispose();
});

test('WASD pauses for text entry, disabled controls, and modified shortcuts', () => {
  const { camera, controls, controller, eventTarget, documentTarget } = makeController();
  const start = camera.position.clone();

  documentTarget.activeElement = { tagName: 'TEXTAREA' };
  eventTarget.dispatchEvent(keyEvent('keydown', 'KeyW'));
  assert.equal(controller.update(0.1), false);

  documentTarget.activeElement = null;
  eventTarget.dispatchEvent(keyEvent('keydown', 'KeyW', { ctrlKey: true }));
  assert.equal(controller.update(0.1), false);

  eventTarget.dispatchEvent(keyEvent('keydown', 'KeyW'));
  controls.enabled = false;
  assert.equal(controller.update(0.1), false);
  assert.deepEqual(camera.position.toArray(), start.toArray());

  controller.dispose();
});

test('open dialogs and disabled scene controls clear held movement', () => {
  const { camera, controls, controller, eventTarget, documentTarget } = makeController();
  const start = camera.position.clone();

  eventTarget.dispatchEvent(keyEvent('keydown', 'KeyW'));
  documentTarget.modalOpen = true;
  assert.equal(controller.update(0.1), false);

  documentTarget.modalOpen = false;
  controls.enabled = false;
  assert.equal(controller.update(0.1), false);
  controls.enabled = true;
  assert.equal(controller.update(0.1), false);
  assert.deepEqual(camera.position.toArray(), start.toArray());

  controller.dispose();
});

test('text-entry detection covers the interactive editor surfaces', () => {
  assert.equal(isTextEntryTarget({ tagName: 'INPUT' }), true);
  assert.equal(isTextEntryTarget({ tagName: 'select' }), true);
  assert.equal(isTextEntryTarget({ isContentEditable: true }), true);
  assert.equal(isTextEntryTarget({ getAttribute: (name) => name === 'role' ? 'textbox' : null }), true);
  assert.equal(isTextEntryTarget({ tagName: 'CANVAS' }), false);
});
