/**
 * Conservative defaults used by WebXR as well as the desktop camera.
 * Three.js forwards these values to XRSession.updateRenderState(), so they
 * must retain enough depth precision for layered avatar geometry.
 */
export const DEFAULT_CAMERA_NEAR = 0.01;
export const DEFAULT_CAMERA_FAR = 100;

const MIN_ORBIT_NEAR = 0.0001;
const MIN_ORBIT_FAR = 100;
const NEAR_DISTANCE_DIVISOR = 250;
const FAR_DISTANCE_MULTIPLIER = 100;

/**
 * Grow the desktop clipping volume with the orbit radius without sacrificing
 * depth-buffer precision. The target remains comfortably inside the frustum,
 * while the far/near ratio never exceeds 1,000,000:1.
 *
 * @param {number} distance distance from the camera to the orbit target
 * @returns {{ near: number, far: number }}
 */
export function getOrbitCameraClipRange(distance) {
  if (!Number.isFinite(distance) || distance < 0) {
    return { near: DEFAULT_CAMERA_NEAR, far: DEFAULT_CAMERA_FAR };
  }

  return {
    near: Math.max(MIN_ORBIT_NEAR, distance / NEAR_DISTANCE_DIVISOR),
    far: Math.max(MIN_ORBIT_FAR, distance * FAR_DISTANCE_MULTIPLIER),
  };
}
