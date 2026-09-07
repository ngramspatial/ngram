import * as THREE from 'three';

export const DESKTOP_MOVE_SPEED = 2.5;
export const DESKTOP_SPRINT_MULTIPLIER = 3;

const MOVEMENT_CODES = new Set(['KeyW', 'KeyA', 'KeyS', 'KeyD']);
const SPRINT_CODES = new Set(['ShiftLeft', 'ShiftRight']);

export function isTextEntryTarget(target) {
  if (!target || typeof target !== 'object') return false;
  const element = target;
  const tag = String(element.tagName ?? '').toLowerCase();
  return tag === 'input'
    || tag === 'textarea'
    || tag === 'select'
    || tag === 'option'
    || element.isContentEditable === true
    || element.getAttribute?.('role') === 'textbox';
}

/**
 * Keyboard traversal for the desktop scene. Camera and orbit target move as a
 * pair, so WASD does not change the current viewing angle or orbit distance.
 */
export class DesktopNavigationController {
  constructor(camera, controls, options = {}) {
    this.camera = camera;
    this.controls = controls;
    this.eventTarget = options.eventTarget ?? window;
    this.documentTarget = options.documentTarget ?? document;
    this.moveSpeed = options.moveSpeed ?? DESKTOP_MOVE_SPEED;
    this.sprintMultiplier = options.sprintMultiplier ?? DESKTOP_SPRINT_MULTIPLIER;
    this.isBlocked = options.isBlocked ?? (() => (
      !!this.documentTarget.querySelector?.('.modal-backdrop.open')
    ));
    this.pressed = new Set();

    this.forward = new THREE.Vector3();
    this.right = new THREE.Vector3();
    this.movement = new THREE.Vector3();
    this.up = new THREE.Vector3(0, 1, 0);

    this.onKeyDown = (event) => {
      const isMovement = MOVEMENT_CODES.has(event.code);
      const isSprint = SPRINT_CODES.has(event.code);
      if (!isMovement && !isSprint) return;
      if (event.defaultPrevented || event.ctrlKey || event.metaKey || event.altKey) return;
      if (this.isBlocked()) return;
      if (isTextEntryTarget(event.target)
        || isTextEntryTarget(this.documentTarget.activeElement)) return;

      this.pressed.add(event.code);
      if (isMovement) event.preventDefault();
    };
    this.onKeyUp = (event) => {
      if (MOVEMENT_CODES.has(event.code) || SPRINT_CODES.has(event.code)) {
        this.pressed.delete(event.code);
      }
    };
    this.clearPressed = () => this.pressed.clear();
    this.onVisibilityChange = () => {
      if (this.documentTarget.visibilityState === 'hidden') this.clearPressed();
    };
    this.onFocusIn = (event) => {
      if (isTextEntryTarget(event.target)) this.clearPressed();
    };

    this.eventTarget.addEventListener('keydown', this.onKeyDown);
    this.eventTarget.addEventListener('keyup', this.onKeyUp);
    this.eventTarget.addEventListener('blur', this.clearPressed);
    this.documentTarget.addEventListener('visibilitychange', this.onVisibilityChange);
    this.documentTarget.addEventListener('focusin', this.onFocusIn);
  }

  update(deltaTime) {
    if (!this.controls?.enabled
      || this.isBlocked()
      || isTextEntryTarget(this.documentTarget.activeElement)) {
      this.clearPressed();
      return false;
    }
    if (!Number.isFinite(deltaTime)
      || deltaTime <= 0) return false;

    const forwardAxis = Number(this.pressed.has('KeyW')) - Number(this.pressed.has('KeyS'));
    const strafeAxis = Number(this.pressed.has('KeyD')) - Number(this.pressed.has('KeyA'));
    if (forwardAxis === 0 && strafeAxis === 0) return false;

    this.camera.getWorldDirection(this.forward);
    this.forward.y = 0;
    if (this.forward.lengthSq() < 1e-8) {
      this.right.set(1, 0, 0).applyQuaternion(this.camera.quaternion);
      this.right.y = 0;
      if (this.right.lengthSq() < 1e-8) this.right.set(1, 0, 0);
      this.right.normalize();
      this.forward.crossVectors(this.up, this.right).normalize();
    } else {
      this.forward.normalize();
      this.right.crossVectors(this.forward, this.up).normalize();
    }

    this.movement.copy(this.forward).multiplyScalar(forwardAxis);
    this.movement.addScaledVector(this.right, strafeAxis);
    this.movement.normalize();

    const sprinting = this.pressed.has('ShiftLeft') || this.pressed.has('ShiftRight');
    const speed = this.moveSpeed * (sprinting ? this.sprintMultiplier : 1);
    // Avoid a large jump if the browser stalls between animation frames.
    this.movement.multiplyScalar(speed * Math.min(deltaTime, 0.1));
    this.camera.position.add(this.movement);
    this.controls.target.add(this.movement);
    return true;
  }

  dispose() {
    this.eventTarget.removeEventListener('keydown', this.onKeyDown);
    this.eventTarget.removeEventListener('keyup', this.onKeyUp);
    this.eventTarget.removeEventListener('blur', this.clearPressed);
    this.documentTarget.removeEventListener('visibilitychange', this.onVisibilityChange);
    this.documentTarget.removeEventListener('focusin', this.onFocusIn);
    this.clearPressed();
  }
}
