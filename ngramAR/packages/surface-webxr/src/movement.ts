import { Vector3 } from 'three';

// Match the relative world-space offsets already emitted by the other bindings.
const OFFSETS: Record<string, [number, number]> = {
  left: [-1.5, 0],
  right: [1.5, 0],
  forward: [0, -1.5],
  away: [0, 2],
};

export function resolveMoveDestination(
  target: unknown,
  position: Vector3,
  userPosition: Vector3,
  random: () => number = Math.random,
): Vector3 {
  if (target === 'user') {
    const direction = userPosition.clone().sub(position);
    direction.y = 0;
    const distance = Math.max(0, direction.length() - 1.2);
    return position.clone().add(direction.normalize().multiplyScalar(distance));
  }
  if (target === 'random') {
    const angle = random() * Math.PI * 2;
    const distance = 0.8 + random() * 1.2;
    return position.clone().add(new Vector3(
      Math.cos(angle) * distance, 0, Math.sin(angle) * distance,
    ));
  }
  if (typeof target === 'string' && Object.hasOwn(OFFSETS, target)) {
    const [x, z] = OFFSETS[target];
    return position.clone().add(new Vector3(x, 0, z));
  }
  if (target && typeof target === 'object' && !Array.isArray(target)) {
    const offset = target as { x?: unknown; z?: unknown };
    const x = offset.x ?? 0;
    const z = offset.z ?? 0;
    if (('x' in offset || 'z' in offset) && Number.isFinite(x) && Number.isFinite(z)) {
      return position.clone().add(new Vector3(x as number, 0, z as number));
    }
  }
  throw new Error('Invalid movement target: use user, left, right, forward, away, random, or a finite x/z offset.');
}
