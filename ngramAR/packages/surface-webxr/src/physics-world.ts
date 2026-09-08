// @ts-nocheck
import RAPIER from '@dimforge/rapier3d-compat';

let world: RAPIER.World | null = null;
let rapier: typeof RAPIER | null = null;
let eventQueue = null;
let ground = null;
const collisionListeners = new Set();
export function onPhysicsCollision(listener) {
  collisionListeners.add(listener);
  return () => collisionListeners.delete(listener);
}
export function getGroundCollider() { return ground; }

export async function initPhysics(): Promise<void> {
  await RAPIER.init();
  rapier = RAPIER;
  world = new RAPIER.World({ x: 0, y: 0, z: 0 });
  eventQueue?.free();
  eventQueue = new RAPIER.EventQueue(true);

  // Static ground plane so panels can't fall through the floor
  const groundDesc = RAPIER.RigidBodyDesc.fixed().setTranslation(0, -0.01, 0);
  const groundBody = world.createRigidBody(groundDesc);
  const groundCollider = RAPIER.ColliderDesc.cuboid(50, 0.01, 50);
  ground = world.createCollider(groundCollider, groundBody);
}

export function stepPhysics(dt: number): void {
  if (!world) return;
  world.timestep = Math.min(dt, 1 / 30);
  world.step(eventQueue);
  eventQueue.drainCollisionEvents((a, b, started) => {
    for (const listener of collisionListeners) listener(a, b, started);
  });
}

export function getWorld(): RAPIER.World | null {
  return world;
}

export function getRapier(): typeof RAPIER | null {
  return rapier;
}

export interface PanelBodyHandle {
  body: RAPIER.RigidBody;
  collider: RAPIER.Collider;
}

export function createPanelBody(
  x: number, y: number, z: number,
  halfW: number, halfH: number, halfD = 0.005,
): PanelBodyHandle | null {
  if (!world || !rapier) return null;

  const bodyDesc = rapier.RigidBodyDesc.dynamic()
    .setTranslation(x, y, z)
    .setLinearDamping(5.0)
    .setAngularDamping(5.0)
    .setCcdEnabled(true);

  const body = world.createRigidBody(bodyDesc);

  const colliderDesc = rapier.ColliderDesc.cuboid(halfW, halfH, halfD)
    .setMass(0.5)
    .setRestitution(0.2)
    .setFriction(0.4);

  const collider = world.createCollider(colliderDesc, body);

  return { body, collider };
}

export function removeBody(handle: PanelBodyHandle): void {
  if (!world) return;
  world.removeCollider(handle.collider, true);
  world.removeRigidBody(handle.body);
}

// ─── Shape-specific body creation for scene objects & toys ──────────────────

export type PhysicsShape = 'cube' | 'sphere' | 'cylinder' | 'cone' | 'torus';

export interface ShapeBodyOptions {
  restitution?: number;
  friction?: number;
  linearDamping?: number;
  angularDamping?: number;
  mass?: number;
}

const TOY_DEFAULTS: ShapeBodyOptions = {
  restitution: 0.85,
  friction: 0.3,
  linearDamping: 0.5,
  angularDamping: 0.5,
  mass: 0.3,
};

const OBJECT_DEFAULTS: ShapeBodyOptions = {
  restitution: 0.2,
  friction: 0.4,
  linearDamping: 5.0,
  angularDamping: 5.0,
  mass: 0.5,
};

export function createShapeBody(
  shape: PhysicsShape,
  x: number, y: number, z: number,
  size: number,
  isToy = false,
  opts?: ShapeBodyOptions,
): PanelBodyHandle | null {
  if (!world || !rapier) return null;

  const defaults = isToy ? TOY_DEFAULTS : OBJECT_DEFAULTS;
  const o = { ...defaults, ...opts };
  const half = size / 2;

  const bodyDesc = rapier.RigidBodyDesc.dynamic()
    .setTranslation(x, y, z)
    .setLinearDamping(o.linearDamping!)
    .setAngularDamping(o.angularDamping!)
    .setCcdEnabled(true);

  const body = world.createRigidBody(bodyDesc);

  let colliderDesc: RAPIER.ColliderDesc;
  switch (shape) {
    case 'sphere':
      colliderDesc = rapier.ColliderDesc.ball(half);
      break;
    case 'cylinder':
      colliderDesc = rapier.ColliderDesc.cylinder(half, half * 0.6);
      break;
    case 'cone':
      colliderDesc = rapier.ColliderDesc.cone(half, half * 0.6);
      break;
    case 'torus':
      // Rapier has no torus — approximate with a sphere
      colliderDesc = rapier.ColliderDesc.ball(half * 0.8);
      break;
    case 'cube':
    default:
      colliderDesc = rapier.ColliderDesc.cuboid(half, half, half);
      break;
  }

  colliderDesc
    .setMass(o.mass!)
    .setRestitution(o.restitution!)
    .setFriction(o.friction!);

  const collider = world.createCollider(colliderDesc, body);
  return { body, collider };
}
