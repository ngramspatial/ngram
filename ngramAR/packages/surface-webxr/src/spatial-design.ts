import * as THREE from 'three';

/** The website identity, translated to opaque, legible spatial surfaces. */
export const SPATIAL = Object.freeze({
  accent: '#6E7DFF',
  accentHex: 0x6e7dff,
  ink: '#152064',
  surface: '#152064',
  raised: '#222F7B',
  text: '#FFFFFF',
  muted: '#CCD2FF',
  line: 'rgba(255,255,255,0.30)',
  lineStrong: 'rgba(255,255,255,0.62)',
  recording: '#6E7DFF',
  micOff: '#FFFFFF',
  micInk: '#111111',
  font: '"Azeret Mono", ui-monospace, monospace',
});

let ready: Promise<void> | undefined;
let logo: HTMLImageElement | undefined;

/** Load once before any canvas texture is painted, including cached menu states. */
export function loadSpatialAssets(): Promise<void> {
  return ready ??= Promise.all([
    ...[400, 500, 600, 700].map(weight => document.fonts.load(`${weight} 24px "Azeret Mono"`)),
    new Promise<void>(resolve => {
      logo = new Image();
      logo.onload = () => resolve();
      logo.onerror = () => { logo = undefined; resolve(); };
      logo.src = '/ngram-logo.svg';
    }),
  ]).then(() => undefined).catch(error => {
    console.warn('[spatial-ui] Brand assets unavailable; using monospace fallback.', error);
  });
}

/** Compact website tracking, relaxed for small text at a distance. Code stays untracked. */
export function setSpatialFont(ctx: CanvasRenderingContext2D, font: string, tracking = -0.035): void {
  ctx.font = font;
  const size = Number(font.match(/([\d.]+)px/)?.[1] ?? 16);
  ctx.letterSpacing = `${size * tracking}px`;
}

export function spatialTexture(canvas: HTMLCanvasElement): THREE.CanvasTexture {
  const texture = new THREE.CanvasTexture(canvas);
  texture.colorSpace = THREE.SRGBColorSpace;
  texture.minFilter = THREE.LinearMipmapLinearFilter;
  texture.magFilter = THREE.LinearFilter;
  return texture;
}

export function drawBrand(ctx: CanvasRenderingContext2D, x: number, y: number, size: number): void {
  if (!logo?.complete || !logo.naturalWidth) return;
  ctx.save();
  ctx.filter = 'brightness(0) invert(1)';
  ctx.drawImage(logo, x, y, size, size);
  ctx.restore();
}

/** Flat field, fine rule, restrained corners: the site's cards in physical space. */
export function drawSurface(ctx: CanvasRenderingContext2D, x: number, y: number, w: number, h: number,
  fill = SPATIAL.surface, border = SPATIAL.line, radius = 10): void {
  ctx.beginPath();
  ctx.roundRect(x, y, w, h, radius);
  ctx.fillStyle = fill;
  ctx.fill();
  ctx.strokeStyle = border;
  ctx.lineWidth = 1.5;
  ctx.stroke();
}

/** A single stroke icon family; no platform-dependent emoji in spatial controls. */
export function drawIcon(ctx: CanvasRenderingContext2D, id: string, x: number, y: number,
  size: number, color = SPATIAL.text): void {
  ctx.save();
  ctx.translate(x, y);
  ctx.scale(size / 24, size / 24);
  ctx.strokeStyle = color;
  ctx.fillStyle = color;
  ctx.lineWidth = 1.7;
  ctx.lineCap = 'round';
  ctx.lineJoin = 'round';
  ctx.beginPath();
  switch (id) {
    case 'mic':
      ctx.roundRect(9, 2, 6, 13, 3);
      ctx.moveTo(5, 11); ctx.bezierCurveTo(5, 22, 19, 22, 19, 11);
      ctx.moveTo(12, 19); ctx.lineTo(12, 23);
      ctx.moveTo(8, 23); ctx.lineTo(16, 23);
      break;
    case 'switch':
      ctx.roundRect(3, 3, 12, 14, 2);
      ctx.moveTo(18, 7); ctx.lineTo(21, 7); ctx.lineTo(21, 21); ctx.lineTo(9, 21);
      ctx.moveTo(7, 10); ctx.lineTo(11, 10);
      break;
    case 'vision':
      ctx.moveTo(3, 6); ctx.lineTo(7, 6); ctx.lineTo(9, 3); ctx.lineTo(15, 3);
      ctx.lineTo(17, 6); ctx.lineTo(21, 6); ctx.lineTo(21, 21); ctx.lineTo(3, 21); ctx.closePath();
      ctx.moveTo(16, 13); ctx.arc(12, 13, 4, 0, Math.PI * 2);
      break;
    case 'resize':
      ctx.moveTo(4, 10); ctx.lineTo(4, 4); ctx.lineTo(10, 4);
      ctx.moveTo(4, 4); ctx.lineTo(10, 10);
      ctx.moveTo(20, 14); ctx.lineTo(20, 20); ctx.lineTo(14, 20);
      ctx.moveTo(20, 20); ctx.lineTo(14, 14);
      break;
    case 'reposition':
      ctx.moveTo(12, 2); ctx.lineTo(12, 22); ctx.moveTo(2, 12); ctx.lineTo(22, 12);
      ctx.moveTo(8, 6); ctx.lineTo(12, 2); ctx.lineTo(16, 6);
      ctx.moveTo(8, 18); ctx.lineTo(12, 22); ctx.lineTo(16, 18);
      ctx.moveTo(6, 8); ctx.lineTo(2, 12); ctx.lineTo(6, 16);
      ctx.moveTo(18, 8); ctx.lineTo(22, 12); ctx.lineTo(18, 16);
      break;
    case 'terminal':
      ctx.roundRect(1, 3, 22, 18, 2);
      ctx.moveTo(5, 8); ctx.lineTo(9, 12); ctx.lineTo(5, 16);
      ctx.moveTo(12, 16); ctx.lineTo(18, 16);
      break;
  }
  ctx.stroke();
  ctx.restore();
}

export function wrapSpatialText(ctx: CanvasRenderingContext2D, text: string, maxWidth: number): string[] {
  const lines: string[] = [];
  for (const paragraph of text.split('\n')) {
    let line = '';
    for (const word of paragraph.split(/\s+/).filter(Boolean)) {
      const next = line ? `${line} ${word}` : word;
      if (ctx.measureText(next).width <= maxWidth) { line = next; continue; }
      if (line) lines.push(line);
      line = '';
      for (const char of Array.from(word)) {
        if (line && ctx.measureText(line + char).width > maxWidth) { lines.push(line); line = ''; }
        line += char;
      }
    }
    lines.push(line);
  }
  return lines;
}
