// @ts-nocheck
import * as THREE from 'three';

const DEFAULT_MAX_SIZE = 1024;
const JPEG_QUALITY = 0.7;

const resizeCanvas = document.createElement('canvas');

/**
 * Capture the current WebGL canvas as a base64 JPEG data URI,
 * scaled down to fit within maxSize on its longest edge.
 */
export function captureFrame(
  renderer: THREE.WebGLRenderer,
  maxSize = DEFAULT_MAX_SIZE,
): string {
  const source = renderer.domElement;
  const sw = source.width;
  const sh = source.height;

  let dw = sw;
  let dh = sh;
  if (Math.max(sw, sh) > maxSize) {
    const ratio = maxSize / Math.max(sw, sh);
    dw = Math.round(sw * ratio);
    dh = Math.round(sh * ratio);
  }

  resizeCanvas.width = dw;
  resizeCanvas.height = dh;

  const ctx = resizeCanvas.getContext('2d')!;
  ctx.drawImage(source, 0, 0, dw, dh);

  return resizeCanvas.toDataURL('image/jpeg', JPEG_QUALITY);
}

const VISION_TRIGGERS = [
  'look at this',
  'what do you see',
  'can you see',
  'do you see',
  'take a look',
  'check this out',
  'look here',
  'what is this',
  'what\'s this',
  'look around',
  'see this',
  'show you',
  'see what i see',
  'what am i looking at',
  'describe what you see',
];

/**
 * Check whether user speech text contains a vision trigger phrase.
 */
export function containsVisionTrigger(text: string): boolean {
  const lower = text.toLowerCase();
  return VISION_TRIGGERS.some(phrase => lower.includes(phrase));
}
