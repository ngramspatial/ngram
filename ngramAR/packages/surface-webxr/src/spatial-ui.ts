// @ts-nocheck
import * as THREE from 'three';
import { SPATIAL, drawBrand, drawIcon, drawSurface, setSpatialFont, spatialTexture, wrapSpatialText } from './spatial-design.js';

// ─── Theme tokens ────────────────────────────────────────────────────────────

// Immersive chrome uses the ngram identity independently of the desktop theme.
const T = {
  accent: SPATIAL.accent, accentBright: SPATIAL.text,
  text: SPATIAL.text, textMuted: SPATIAL.muted,
  bg: SPATIAL.surface, bgMedium: SPATIAL.raised,
  border: SPATIAL.line, borderAccent: SPATIAL.lineStrong,
};
const FONT = SPATIAL.font;
const FONT_MONO = SPATIAL.font;
const CANVAS_W = 640;
const BUBBLE_WORLD_W = 0.50;
const wrapText = wrapSpatialText;

function pill(
  ctx: CanvasRenderingContext2D,
  x: number, y: number, w: number, h: number, r: number,
): void {
  ctx.beginPath();
  ctx.roundRect(x, y, w, h, r);
}

function drawGlass(
  ctx: CanvasRenderingContext2D,
  x: number, y: number, w: number, h: number,
  radius: number,
  fill = T.bg, border = T.border,
): void {
  drawSurface(ctx, x, y, w, h, fill, border, Math.min(radius, 10));
}

function createPlane(
  texture: THREE.CanvasTexture, worldW: number, aspect: number,
): THREE.Mesh {
  const geo = new THREE.PlaneGeometry(worldW, worldW / aspect);
  const mat = new THREE.MeshBasicMaterial({
    map: texture,
    transparent: true,
    depthTest: false,
    depthWrite: false,
    toneMapped: false,
    side: THREE.DoubleSide,
  });
  return new THREE.Mesh(geo, mat);
}

// ─── Speech bubble ───────────────────────────────────────────────────────────

function createBubbleTexture(text: string): { texture: THREE.CanvasTexture; aspect: number } {
  const canvas = document.createElement('canvas');
  canvas.width = CANVAS_W;
  canvas.height = CANVAS_W;
  const ctx = canvas.getContext('2d')!;

  const fontSize = 26;
  const lineHeight = 36;
  const pad = 28;
  const textMaxW = CANVAS_W - pad * 2 - 16;

  setSpatialFont(ctx, `500 ${fontSize}px ${FONT}`);
  const lines = wrapText(ctx, text, textMaxW);
  const textH = lines.length * lineHeight;
  const cardH = textH + pad * 2;

  canvas.height = Math.max(cardH + 16, 80);
  setSpatialFont(ctx, `500 ${fontSize}px ${FONT}`);

  const margin = 8;
  const cardW = CANVAS_W - margin * 2;
  const radius = 18;

  drawGlass(ctx, margin, margin, cardW, cardH, radius, T.bg, T.borderAccent);

  ctx.fillStyle = SPATIAL.accent;
  ctx.fillRect(margin, margin + 16, 4, cardH - 32);

  ctx.fillStyle = T.text;
  ctx.textBaseline = 'top';
  ctx.textAlign = 'left';
  for (let i = 0; i < lines.length; i++) {
    ctx.fillText(lines[i], margin + pad, margin + pad + i * lineHeight);
  }

  const texture = spatialTexture(canvas);
  texture.needsUpdate = true;
  return { texture, aspect: canvas.width / canvas.height };
}

// ─── Status notification ─────────────────────────────────────────────────────

function createStatusTexture(text: string): { texture: THREE.CanvasTexture; aspect: number } {
  const canvas = document.createElement('canvas');
  canvas.width = 640;
  let ctx = canvas.getContext('2d')!;
  setSpatialFont(ctx, `500 23px ${FONT}`);
  const lines = wrapText(ctx, text, 536);
  canvas.height = Math.max(84, 36 + lines.length * 32);
  ctx = canvas.getContext('2d')!;
  drawSurface(ctx, 3, 3, canvas.width - 6, canvas.height - 6);
  ctx.fillStyle = SPATIAL.accent;
  ctx.fillRect(3, 15, 4, canvas.height - 30);
  drawBrand(ctx, 17, (canvas.height - 34) / 2, 34);
  setSpatialFont(ctx, `500 23px ${FONT}`);
  ctx.fillStyle = T.text;
  ctx.textAlign = 'left'; ctx.textBaseline = 'top';
  lines.forEach((line, i) => ctx.fillText(line, 68, 18 + i * 32));
  return { texture: spatialTexture(canvas), aspect: canvas.width / canvas.height };
}

// ─── Mic indicator ───────────────────────────────────────────────────────────

const MIC_CANVAS_W = 384;
const MIC_CANVAS_H = 112;
const MIC_WORLD_W = 0.14;
const MIC_BAR_COUNT = 5;

function renderMicCanvas(
  canvas: HTMLCanvasElement,
  active: boolean,
  level: number,
  bins: Float32Array,
): void {
  const ctx = canvas.getContext('2d')!;
  ctx.clearRect(0, 0, MIC_CANVAS_W, MIC_CANVAS_H);
  // One quiet, opaque control stays legible against any passthrough background.
  drawSurface(ctx, 4, 4, MIC_CANVAS_W - 8, MIC_CANVAS_H - 8,
    active ? SPATIAL.recording : SPATIAL.micOff, 'rgba(17,17,17,0.30)', 24);
  const ink = active ? SPATIAL.text : SPATIAL.micInk;
  drawIcon(ctx, 'mic', 25, 32, 46, ink);
  if (!active) {
    ctx.beginPath(); ctx.moveTo(26, 31); ctx.lineTo(71, 77);
    ctx.strokeStyle = ink; ctx.lineWidth = 3.2; ctx.lineCap = 'round'; ctx.stroke();
  }
  setSpatialFont(ctx, `600 32px ${FONT}`);
  ctx.fillStyle = ink; ctx.textAlign = 'left'; ctx.textBaseline = 'middle';
  ctx.fillText(active ? 'Listening' : 'Mic off', 96, 56);
  ctx.fillStyle = active ? 'rgba(255,255,255,0.35)' : 'rgba(17,17,17,0.20)'; ctx.fillRect(284, 30, 1, 52);
  // Actual audio drives the five bars. Silence stays still; there is no fake pulse.
  for (let i = 0; i < MIC_BAR_COUNT; i++) {
    const envelope = 1 - Math.abs(i - 2) * 0.22;
    const amplitude = active ? Math.min(1, Math.max(0, bins[i], level * envelope)) : 0;
    const height = 5 + amplitude * 43;
    ctx.beginPath(); ctx.roundRect(302 + i * 11, 56 - height / 2, 5, height, 2.5);
    ctx.fillStyle = ink; ctx.fill();
  }
}

// Help panel

function createHelpTexture(): { texture: THREE.CanvasTexture; aspect: number } {
  const canvas = document.createElement('canvas');
  canvas.width = 800; canvas.height = 570;
  const ctx = canvas.getContext('2d')!;
  drawSurface(ctx, 4, 4, 792, 562);
  ctx.save();
  ctx.beginPath(); ctx.roundRect(4, 4, 792, 562, 10); ctx.clip();
  ctx.fillStyle = SPATIAL.accent; ctx.fillRect(4, 4, 792, 128);
  ctx.restore();
  drawBrand(ctx, 26, 23, 40);
  setSpatialFont(ctx, `600 18px ${FONT}`, 0.08);
  ctx.textAlign = 'left'; ctx.textBaseline = 'middle'; ctx.fillStyle = T.text;
  ctx.fillText('NGRAM / SPATIAL', 82, 43);
  setSpatialFont(ctx, `700 34px ${FONT}`, -0.065);
  ctx.fillText('Make yourself at home.', 32, 94);
  const controls = [
    ['Trigger / right pinch', 'Place your ngram'],
    ['Squeeze grip', 'Toggle microphone'],
    ['A / B', 'Reposition / leave AR'],
    ['Right stick click', 'Open quick menu'],
    ['Left middle + thumb hold', 'Open wrist menu'],
    ['Wave / thumbs up', 'Greet / react'],
    ['Open palm', 'Go idle'],
  ];
  controls.forEach(([gesture, action], i) => {
    const y = 166 + i * 48;
    setSpatialFont(ctx, `500 19px ${FONT}`);
    ctx.fillStyle = T.text; ctx.fillText(gesture, 32, y);
    setSpatialFont(ctx, `400 19px ${FONT}`);
    ctx.fillStyle = T.textMuted; ctx.fillText(action, 430, y);
    ctx.fillStyle = T.border; ctx.fillRect(32, y + 24, 736, 1);
  });
  setSpatialFont(ctx, `400 16px ${FONT}`);
  ctx.fillStyle = T.textMuted;
  ctx.fillText('Your space. Your entity. Same ngram.', 32, 534);
  return { texture: spatialTexture(canvas), aspect: canvas.width / canvas.height };
}

// ─── SpatialUI ───────────────────────────────────────────────────────────────

export class SpatialUI {
  private bubbleGroup: THREE.Group | null = null;
  private bubbleMesh: THREE.Mesh | null = null;
  private hideTimer: ReturnType<typeof setTimeout> | null = null;
  private scene: THREE.Scene | null = null;

  private micSprite: THREE.Sprite | null = null;
  private micCanvas: HTMLCanvasElement | null = null;
  private micTex: THREE.CanvasTexture | null = null;
  private micActive = false;
  private micSmoothedLevel = 0;
  private micSmoothedBins: Float32Array = new Float32Array(MIC_BAR_COUNT);

  private statusGroup: THREE.Group | null = null;
  private statusMesh: THREE.Mesh | null = null;

  private helpGroup: THREE.Group | null = null;

  attach(scene: THREE.Scene): void {
    this.scene = scene;
    this.createMicIndicator();
    this.createHelpPanel();
  }

  // ─── Speech bubble ─────────────────────────────────────────────────────────

  showBubble(text: string, avatarPos: THREE.Vector3, headOffset = 2.0): void {
    if (!this.scene) return;
    this.hideBubble();

    const { texture, aspect } = createBubbleTexture(text);
    this.bubbleMesh = createPlane(texture, BUBBLE_WORLD_W, aspect);

    this.bubbleGroup = new THREE.Group();
    this.bubbleGroup.add(this.bubbleMesh);
    this.bubbleGroup.position.set(avatarPos.x, avatarPos.y + headOffset, avatarPos.z);

    const mat = this.bubbleMesh.material as THREE.MeshBasicMaterial;
    mat.opacity = 0;
    let progress = 0;
    const fadeIn = () => {
      progress += 0.04;
      if (progress >= 1) { mat.opacity = 1; return; }
      mat.opacity = easeOutCubic(progress);
      requestAnimationFrame(fadeIn);
    };
    requestAnimationFrame(fadeIn);

    this.scene.add(this.bubbleGroup);
    this.hideTimer = setTimeout(() => this.hideBubble(), 10000);
  }

  showThinking(avatarPos: THREE.Vector3, headOffset = 2.0): void {
    this.showBubble('...', avatarPos, headOffset);
  }

  hideBubble(): void {
    if (this.hideTimer) {
      clearTimeout(this.hideTimer);
      this.hideTimer = null;
    }
    if (this.bubbleGroup && this.scene) {
      this.scene.remove(this.bubbleGroup);
      (this.bubbleMesh?.material as THREE.MeshBasicMaterial)?.map?.dispose();
      this.bubbleMesh?.geometry.dispose();
      (this.bubbleMesh?.material as THREE.Material)?.dispose();
      this.bubbleGroup = null;
      this.bubbleMesh = null;
    }
  }

  updateBubblePosition(avatarPos: THREE.Vector3, headOffset = 2.0): void {
    if (this.bubbleGroup) {
      this.bubbleGroup.position.set(avatarPos.x, avatarPos.y + headOffset, avatarPos.z);
    }
  }

  // ─── Billboard ─────────────────────────────────────────────────────────────

  billboardToCamera(camera: THREE.Camera): void {
    if (this.bubbleGroup) this.bubbleGroup.quaternion.copy(camera.quaternion);
    if (this.statusGroup?.visible) this.statusGroup.quaternion.copy(camera.quaternion);
    if (this.helpGroup?.visible) this.helpGroup.quaternion.copy(camera.quaternion);
  }

  // ─── Mic indicator ─────────────────────────────────────────────────────────

  private createMicIndicator(): void {
    if (!this.scene) return;

    this.micCanvas = document.createElement('canvas');
    this.micCanvas.width = MIC_CANVAS_W;
    this.micCanvas.height = MIC_CANVAS_H;

    this.micTex = spatialTexture(this.micCanvas);
    this.micTex.minFilter = THREE.LinearFilter;

    const mat = new THREE.SpriteMaterial({
      map: this.micTex,
      transparent: true,
      depthTest: false,
      depthWrite: false,
      toneMapped: false,
    });
    this.micSprite = new THREE.Sprite(mat);
    this.micSprite.scale.set(MIC_WORLD_W, MIC_WORLD_W * MIC_CANVAS_H / MIC_CANVAS_W, 1);
    this.micSprite.renderOrder = 1050;
    this.micSprite.visible = false;
    this.scene.add(this.micSprite);
    this.updateMicPulse(0);
  }

  setMicState(active: boolean, _camera: THREE.Camera): void {
    if (this.micActive === active) return;
    this.micActive = active;
    this.micSmoothedLevel = 0;
    this.micSmoothedBins.fill(0);
    this.updateMicPulse(0);
  }

  positionMicIndicator(camera: THREE.Camera): void {
    if (!this.micSprite) return;
    const dir = new THREE.Vector3();
    camera.getWorldDirection(dir);
    const right = new THREE.Vector3().crossVectors(dir, camera.up).normalize();
    const down = new THREE.Vector3().crossVectors(right, dir).normalize();

    const pos = camera.position.clone()
      .add(dir.multiplyScalar(0.45))
      .add(right.multiplyScalar(0.14))
      .add(down.multiplyScalar(0.09));

    this.micSprite.position.copy(pos);
    this.micSprite.visible = true;
  }

  updateMicPulse(dt: number, audioLevel = 0, frequencyBins: Float32Array | null = null): void {
    if (!this.micSprite || !this.micCanvas || !this.micTex) return;
    this.micSmoothedLevel += (audioLevel - this.micSmoothedLevel) * Math.min(dt * 12, 1);

    if (frequencyBins && frequencyBins.length > 0) {
      const step = frequencyBins.length / MIC_BAR_COUNT;
      for (let i = 0; i < MIC_BAR_COUNT; i++) {
        const idx = Math.floor(i * step);
        const target = frequencyBins[Math.min(idx, frequencyBins.length - 1)];
        this.micSmoothedBins[i] += (target - this.micSmoothedBins[i]) * Math.min(dt * 14, 1);
      }
    } else {
      for (let i = 0; i < MIC_BAR_COUNT; i++) {
        this.micSmoothedBins[i] *= 0.9;
      }
    }

    // Re-render canvas
    renderMicCanvas(
      this.micCanvas,
      this.micActive,
      this.micSmoothedLevel,
      this.micSmoothedBins,
    );

    this.micTex.needsUpdate = true;
  }

  // ─── Status ────────────────────────────────────────────────────────────────

  showStatus(text: string): void {
    if (!this.scene) return;
    this.hideStatusMesh();

    const { texture, aspect } = createStatusTexture(text);
    this.statusMesh = createPlane(texture, 0.32, aspect);

    this.statusGroup = new THREE.Group();
    this.statusGroup.add(this.statusMesh);
    this.scene.add(this.statusGroup);
  }

  positionStatus(camera: THREE.Camera): void {
    if (!this.statusGroup) return;
    const dir = new THREE.Vector3();
    camera.getWorldDirection(dir);
    const up = new THREE.Vector3(0, 1, 0);
    const pos = camera.position.clone()
      .add(dir.multiplyScalar(0.55))
      .add(up.multiplyScalar(0.18));
    this.statusGroup.position.copy(pos);
    this.statusGroup.visible = true;
  }

  hideStatus(): void {
    if (this.statusGroup) this.statusGroup.visible = false;
  }

  private hideStatusMesh(): void {
    if (this.statusGroup && this.scene) {
      this.scene.remove(this.statusGroup);
      (this.statusMesh?.material as THREE.MeshBasicMaterial)?.map?.dispose();
      this.statusMesh?.geometry.dispose();
      (this.statusMesh?.material as THREE.Material)?.dispose();
      this.statusGroup = null;
      this.statusMesh = null;
    }
  }

  // ─── Help panel ────────────────────────────────────────────────────────────

  private createHelpPanel(): void {
    if (!this.scene) return;
    this.helpGroup = new THREE.Group();
    this.helpGroup.visible = false;

    const { texture, aspect } = createHelpTexture();
    const mesh = createPlane(texture, 0.60, aspect);
    this.helpGroup.add(mesh);
    this.scene.add(this.helpGroup);
  }

  showHelp(camera: THREE.Camera): void {
    if (!this.helpGroup) return;
    const dir = new THREE.Vector3();
    camera.getWorldDirection(dir);
    const pos = camera.position.clone().add(dir.multiplyScalar(0.75));
    pos.y -= 0.04;
    this.helpGroup.position.copy(pos);
    this.helpGroup.visible = true;

    const mesh = this.helpGroup.children[0] as THREE.Mesh;
    if (mesh) {
      const mat = mesh.material as THREE.MeshBasicMaterial;
      mat.opacity = 0;
      let progress = 0;
      const fadeIn = () => {
        progress += 0.03;
        if (progress >= 1) { mat.opacity = 1; return; }
        mat.opacity = easeOutCubic(progress);
        requestAnimationFrame(fadeIn);
      };
      requestAnimationFrame(fadeIn);
    }

    setTimeout(() => this.hideHelp(), 8000);
  }

  hideHelp(): void {
    if (this.helpGroup) this.helpGroup.visible = false;
  }

  // ─── Theme ─────────────────────────────────────────────────────────────────

  setTheme(_theme: string): void {
    // Spatial chrome keeps its identity when the desktop appearance changes.
  }

  // ─── Capture flash ─────────────────────────────────────────────────────────

  showCaptureFlash(camera: THREE.Camera): void {
    if (!this.scene) return;

    const size = 128;
    const c = document.createElement('canvas');
    c.width = size;
    c.height = size;
    const ctx = c.getContext('2d')!;

    ctx.clearRect(0, 0, size, size);
    pill(ctx, 8, 8, size - 16, size - 16, 20);
    ctx.fillStyle = T.bg;
    ctx.fill();
    ctx.strokeStyle = T.borderAccent;
    ctx.lineWidth = 2;
    ctx.stroke();

    setSpatialFont(ctx, `48px ${FONT}`);
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    ctx.fillStyle = T.text;
    drawIcon(ctx, 'vision', size / 2 - 20, size / 2 - 28, 40);

    setSpatialFont(ctx, `bold 14px ${FONT}`);
    ctx.fillStyle = T.accent;
    ctx.fillText('Captured', size / 2, size / 2 + 28);

    const tex = spatialTexture(c);
    tex.minFilter = THREE.LinearFilter;
    const mat = new THREE.SpriteMaterial({ map: tex, transparent: true, depthTest: false, depthWrite: false, toneMapped: false });
    const sprite = new THREE.Sprite(mat);
    sprite.scale.set(0.06, 0.06, 1);
    sprite.renderOrder = 1200;

    const dir = new THREE.Vector3();
    camera.getWorldDirection(dir);
    sprite.position.copy(camera.position).addScaledVector(dir, 0.5);
    sprite.position.y -= 0.08;

    this.scene.add(sprite);

    setTimeout(() => {
      this.scene?.remove(sprite);
      mat.dispose();
      tex.dispose();
    }, 1200);
  }

  // ─── Dispose ───────────────────────────────────────────────────────────────

  dispose(): void {
    this.hideBubble();
    this.hideStatusMesh();
    if (this.micSprite && this.scene) {
      this.scene.remove(this.micSprite);
      (this.micSprite.material as THREE.Material).dispose();
    }
    this.micTex?.dispose();
    if (this.helpGroup && this.scene) {
      this.scene.remove(this.helpGroup);
      for (const child of this.helpGroup.children) {
        const mesh = child as THREE.Mesh;
        (mesh.material as THREE.MeshBasicMaterial).map?.dispose();
        (mesh.material as THREE.Material).dispose();
        mesh.geometry.dispose();
      }
    }
  }
}

function easeOutCubic(t: number): number {
  return 1 - Math.pow(1 - t, 3);
}
