// @ts-nocheck
import * as THREE from 'three';

export const BEZEL_PAD = 0.012;
export const BEZEL_DEPTH = 0.004;

const CORNER_SEGMENTS = 8;

export const BEZEL_DARK = 0x0c0c0e;
export const BEZEL_LIGHT = 0xf0f0f2;

/** Rounded rectangle in X/Y, rotated flat for vertical panels (same as SpatialPanel). */
export function createRoundedPlane(w: number, h: number, r: number): THREE.ShapeGeometry {
  const shape = new THREE.Shape();
  const hw = w / 2;
  const hh = h / 2;
  shape.moveTo(-hw + r, -hh);
  shape.lineTo(hw - r, -hh);
  shape.quadraticCurveTo(hw, -hh, hw, -hh + r);
  shape.lineTo(hw, hh - r);
  shape.quadraticCurveTo(hw, hh, hw - r, hh);
  shape.lineTo(-hw + r, hh);
  shape.quadraticCurveTo(-hw, hh, -hw, hh - r);
  shape.lineTo(-hw, -hh + r);
  shape.quadraticCurveTo(-hw, -hh, -hw + r, -hh);
  const geo = new THREE.ShapeGeometry(shape, CORNER_SEGMENTS);

  // ShapeGeometry UVs are emitted in local coordinate space (often around -0.5..0.5).
  // Normalize to 0..1 so panel textures map across the full rounded surface.
  geo.computeBoundingBox();
  const bb = geo.boundingBox;
  const pos = geo.getAttribute('position') as THREE.BufferAttribute | undefined;
  const uv = geo.getAttribute('uv') as THREE.BufferAttribute | undefined;
  if (bb && pos && uv) {
    const sx = Math.max(1e-6, bb.max.x - bb.min.x);
    const sy = Math.max(1e-6, bb.max.y - bb.min.y);
    for (let i = 0; i < uv.count; i++) {
      const x = pos.getX(i);
      const y = pos.getY(i);
      uv.setXY(i, (x - bb.min.x) / sx, (y - bb.min.y) / sy);
    }
    uv.needsUpdate = true;
  }

  return geo;
}
