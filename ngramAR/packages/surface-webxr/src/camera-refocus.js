import * as THREE from 'three';

function isFiniteVector3(value) {
  return value
    && Number.isFinite(value.x)
    && Number.isFinite(value.y)
    && Number.isFinite(value.z);
}

/**
 * Point OrbitControls at the visible model without undoing camera traversal.
 * Keeping the camera position intact makes this a true refocus operation: the
 * model becomes the new orbit pivot at the user's current viewing distance.
 */
export function refocusOrbitOnObject(controls, object, fallbackPosition, visualHeight) {
  if (!controls?.target || !object) return false;

  const target = new THREE.Vector3();
  let hasBoundsCenter = false;
  try {
    object.updateWorldMatrix?.(true, true);
    const bounds = new THREE.Box3().setFromObject(object);
    if (!bounds.isEmpty()) {
      bounds.getCenter(target);
      hasBoundsCenter = isFiniteVector3(target);
    }
  } catch {
    hasBoundsCenter = false;
  }

  if (!hasBoundsCenter) {
    if (!isFiniteVector3(fallbackPosition)) return false;
    const safeHeight = Number.isFinite(visualHeight) && visualHeight > 0
      ? visualHeight
      : 1.65;
    target.copy(fallbackPosition);
    target.y += Math.max(safeHeight, 0.2) * 0.55;
  }

  controls.target.copy(target);
  controls.update();
  return true;
}
