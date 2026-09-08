// @ts-nocheck
import * as THREE from "three";

/** Resolve authored node names without silently snapping to the wrong part. */
export function resolveFigmentAnchors(world, rootId) {
  const root = world.entries.get(rootId);
  if (!root?.spec.figment) return {};
  const result = {};
  for (const [name, anchor] of Object.entries(root.spec.figment.anchors)) {
    const id = anchor.part === "self" ? rootId : root.spec.figment.parts[anchor.part];
    const entry = world.entries.get(id);
    let node = entry?.node;
    let error = !node ? `Missing part ${anchor.part}` : null;
    if (anchor.node && node) {
      const matches = [];
      entry.visual?.traverse(child => { if (child.name === anchor.node) matches.push(child); });
      node = matches.length === 1 ? matches[0] : null;
      if (!node) error = matches.length ? `Ambiguous mesh node ${anchor.node}` : `Missing mesh node ${anchor.node}`;
    }
    if (error) { result[name] = { ready: false, error }; continue; }
    node.updateWorldMatrix(true, false);
    const matrix = new THREE.Matrix4().compose(new THREE.Vector3(...anchor.position), new THREE.Quaternion().setFromEuler(new THREE.Euler(...anchor.rotation)), new THREE.Vector3(1, 1, 1));
    matrix.premultiply(node.matrixWorld);
    const position = new THREE.Vector3(), rotation = new THREE.Quaternion(), scale = new THREE.Vector3();
    matrix.decompose(position, rotation, scale);
    result[name] = { ready: entry.status === "ready", part: id, position: position.toArray(), quaternion: rotation.toArray(), error: entry.error ?? null };
  }
  return result;
}

export function nearestFigmentGrip(world, id, point, hand = "either", excluded = null) {
  const figment = world.entries.get(id)?.spec.figment;
  if (!figment) return null;
  const anchors = resolveFigmentAnchors(world, id);
  return Object.entries(figment.grips).flatMap(([name, grip]) => {
    const anchor = anchors[grip.anchor];
    if (name === excluded || !anchor?.ready || (hand !== "either" && grip.hand !== "either" && grip.hand !== hand)) return [];
    const distance = point.distanceTo(new THREE.Vector3(...anchor.position));
    return distance <= grip.radius ? [{ name, ...grip, pose: anchor, distance }] : [];
  }).sort((a, b) => a.distance - b.distance)[0] ?? null;
}

/** Solve the object pose that puts an authored grip at a world-space hand pose. */
export function poseAtGrip(node, anchor, position, quaternion) {
  node.updateWorldMatrix(true, false);
  const localPosition = node.worldToLocal(new THREE.Vector3(...anchor.position));
  const localRotation = node.getWorldQuaternion(new THREE.Quaternion()).invert().multiply(new THREE.Quaternion(...anchor.quaternion));
  const rotation = quaternion.clone().multiply(localRotation.invert());
  const scale = node.getWorldScale(new THREE.Vector3());
  const origin = position.clone().sub(localPosition.multiply(scale).applyQuaternion(rotation));
  node.parent.worldToLocal(origin);
  rotation.premultiply(node.parent.getWorldQuaternion(new THREE.Quaternion()).invert());
  const euler = new THREE.Euler().setFromQuaternion(rotation);
  return { position: origin.toArray(), rotation: [euler.x, euler.y, euler.z], scale: node.scale.toArray() };
}
