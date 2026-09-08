// @ts-nocheck
import * as THREE from "three";

/** Explicit collision geometry is independent of visual mesh revisions. */
export function colliderDescriptor(R, c, scale) {
  const [x, y, z] = c.size;
  let geometry, descriptor;
  if (c.shape === "convex") {
    const points = new Float32Array(c.vertices.length);
    for (let i = 0; i < points.length; i++) points[i] = c.vertices[i];
    geometry = new THREE.BufferGeometry().setAttribute("position", new THREE.BufferAttribute(points, 3));
  } else if (c.shape === "box" && c.rotation.every(n => n === 0)) {
    descriptor = R.ColliderDesc.cuboid(x * scale[0] / 2, y * scale[1] / 2, z * scale[2] / 2);
  } else if (c.shape === "sphere" && x * scale[0] === y * scale[1] && y * scale[1] === z * scale[2]) {
    descriptor = R.ColliderDesc.ball(x * scale[0] / 2);
  } else {
    if (c.shape === "box") geometry = new THREE.BoxGeometry(x, y, z);
    else if (c.shape === "sphere") geometry = new THREE.SphereGeometry(.5, 20, 12).scale(x, y, z);
    else if (c.shape === "capsule") {
      const radius = Math.max(x, z) / 2;
      geometry = new THREE.CapsuleGeometry(radius, Math.max(0, y - radius * 2), 8, 16).scale(x / (radius * 2), 1, z / (radius * 2));
    } else geometry = new THREE.CylinderGeometry(c.shape === "cone" ? 0 : x / 2, x / 2, y, 24).scale(1, 1, z / x);
  }
  if (geometry) {
    // Rotate before applying object scale, including nonuniform scales.
    geometry.applyQuaternion(new THREE.Quaternion().setFromEuler(new THREE.Euler(...c.rotation)));
    geometry.scale(...scale);
    descriptor = R.ColliderDesc.convexHull(new Float32Array(geometry.getAttribute("position").array));
    geometry.dispose();
  }
  if (!descriptor) throw Error(`Cannot construct collider ${c.id}; convex vertices must span a volume`);
  return descriptor.setTranslation(...c.position.map((n, i) => n * scale[i]))
    .setSensor(c.sensor).setCollisionGroups(c.groups)
    .setActiveEvents(R.ActiveEvents.COLLISION_EVENTS);
}

export function collisionDetail(world, a, b) {
  const va = a.parent()?.linvel() ?? { x: 0, y: 0, z: 0 };
  const vb = b.parent()?.linvel() ?? { x: 0, y: 0, z: 0 };
  const detail = { relativeSpeed: Math.hypot(va.x-vb.x, va.y-vb.y, va.z-vb.z), point: null, normal: null, impulse: 0 };
  world.contactPair(a, b, (m, flipped) => {
    if (m.numSolverContacts() && !detail.point) {
      detail.point = Object.values(m.solverContactPoint(0));
      detail.normal = Object.values(m.normal()).map(n => flipped ? -n : n);
    }
    for (let i = 0; i < m.numContacts(); i++) detail.impulse += m.contactImpulse(i);
  });
  return detail;
}
