// @ts-nocheck
import * as THREE from 'three';
import { SPATIAL, setSpatialFont, spatialTexture } from './spatial-design.js';

export type VisualAgentState =
  | 'idle'
  | 'thinking'
  | 'tool_running'
  | 'planning'
  | 'listening'
  | 'messaging'
  | 'error';

interface StateConfig {
  color: string;
  label: string;
  pulseSpeed: number;
  ringOpacity: number;
}

const STATE_CONFIGS: Record<VisualAgentState, StateConfig> = {
  idle:         { color: '#ffffff', label: '',           pulseSpeed: 0,    ringOpacity: 0 },
  thinking:     { color: '#cccccc', label: 'Thinking',   pulseSpeed: 0.6,  ringOpacity: 0.18 },
  tool_running: { color: '#e0e0e0', label: 'Working',    pulseSpeed: 0.8,  ringOpacity: 0.2 },
  planning:     { color: '#bbbbbb', label: 'Planning',   pulseSpeed: 0.5,  ringOpacity: 0.18 },
  listening:    { color: '#dddddd', label: 'Listening',   pulseSpeed: 0.4,  ringOpacity: 0.15 },
  messaging:    { color: '#a7f3d0', label: 'Replying',    pulseSpeed: 0.7,  ringOpacity: 0.18 },
  error:        { color: '#f87171', label: 'Error',      pulseSpeed: 1.0,  ringOpacity: 0.25 },
};

const AR_STATE_CONFIGS = Object.fromEntries(Object.entries(STATE_CONFIGS).map(([state, config]) => [
  state, { ...config, color: state === 'idle' || state === 'error' ? config.color : SPATIAL.accent },
]));

const BRAILLE_FRAMES = ['⠋', '⠙', '⠹', '⠸', '⠼', '⠴', '⠦', '⠧', '⠇', '⠏'];

const SPINNER_SIZE = 128;
const SPINNER_WORLD = 0.12;

const BADGE_W = 320;
const BADGE_H = 80;
const BADGE_WORLD_W = 0.28;
const BADGE_SPINNER_FRAMES = 8;

/**
 * Agent processing state display.
 *
 * Desktop + AR: ground disc + floating braille spinner above the avatar's head.
 * AR only: additionally shows the labelled badge with tool name.
 */
export class AgentStateDisplay {
  private ring: THREE.Mesh | null = null;
  private spinner: THREE.Sprite | null = null;
  private spinnerCanvas: HTMLCanvasElement | null = null;
  private badge: THREE.Sprite | null = null;
  private badgeCanvas: HTMLCanvasElement | null = null;
  private state: VisualAgentState = 'idle';
  private toolName = '';
  private message = '';
  private workLabel = '';

  setWorkLabel(label: string): void { this.workLabel = label; }
  private elapsed = 0;
  private lastFrame = -1;
  private attached = false;
  private isAR = false;

  attach(scene: THREE.Scene): void {
    if (this.attached) return;
    this.attached = true;

    // Ground disc
    const geo = new THREE.RingGeometry(0.12, 0.28, 64);
    geo.rotateX(-Math.PI / 2);
    const mat = new THREE.MeshBasicMaterial({
      color: 0x4fc3f7,
      transparent: true,
      opacity: 0,
      side: THREE.DoubleSide,
      depthWrite: false,
    });
    this.ring = new THREE.Mesh(geo, mat);
    this.ring.renderOrder = 999;
    this.ring.visible = false;
    scene.add(this.ring);

    // Overhead braille spinner (desktop + AR)
    this.spinnerCanvas = document.createElement('canvas');
    this.spinnerCanvas.width = SPINNER_SIZE;
    this.spinnerCanvas.height = SPINNER_SIZE;
    const spinTex = new THREE.CanvasTexture(this.spinnerCanvas);
    spinTex.minFilter = THREE.LinearFilter;
    const spinMat = new THREE.SpriteMaterial({
      map: spinTex,
      transparent: true,
      depthTest: false,
      depthWrite: false,
    });
    this.spinner = new THREE.Sprite(spinMat);
    this.spinner.scale.set(SPINNER_WORLD, SPINNER_WORLD, 1);
    this.spinner.visible = false;
    this.spinner.renderOrder = 1001;
    scene.add(this.spinner);

    // AR floating badge
    this.badgeCanvas = document.createElement('canvas');
    this.badgeCanvas.width = BADGE_W;
    this.badgeCanvas.height = BADGE_H;
    const tex = spatialTexture(this.badgeCanvas);
    tex.minFilter = THREE.LinearFilter;
    const spriteMat = new THREE.SpriteMaterial({
      map: tex,
      transparent: true,
      depthTest: false,
      depthWrite: false,
      toneMapped: false,
    });
    this.badge = new THREE.Sprite(spriteMat);
    const aspect = BADGE_W / BADGE_H;
    this.badge.scale.set(BADGE_WORLD_W, BADGE_WORLD_W / aspect, 1);
    this.badge.visible = false;
    this.badge.renderOrder = 1000;
    scene.add(this.badge);
  }

  setState(state: VisualAgentState, toolName?: string, message?: string): void {
    this.state = state;
    this.toolName = toolName ?? '';
    this.message = message ?? '';
    this.elapsed = 0;
    this.lastFrame = -1;

    const cfg = this.stateConfig();

    if (this.ring) {
      const mat = this.ring.material as THREE.MeshBasicMaterial;
      mat.color.set(cfg.color);
      this.ring.visible = state !== 'idle';
    }

    const active = state !== 'idle';

    if (this.spinner) {
      this.spinner.visible = active;
      if (active) this.renderSpinner();
    }

    if (this.badge) {
      this.badge.visible = this.isAR && active;
      if (active) this.renderBadge();
    }
  }

  update(dt: number, avatarPosition: THREE.Vector3, isAR: boolean, avatarScale = 1): void {
    this.elapsed += dt;
    const modeChanged = this.isAR !== isAR;
    this.isAR = isAR;
    if (modeChanged) {
      this.lastFrame = -1;
      if (this.ring) {
        const material = this.ring.material as THREE.MeshBasicMaterial;
        material.color.set(this.stateConfig().color);
        material.toneMapped = !isAR;
        material.needsUpdate = true;
      }
      if (isAR) this.renderBadge();
    }

    if (this.state === 'idle') {
      if (this.spinner) this.spinner.visible = false;
      if (this.badge) {
        this.badge.visible = isAR && !!this.workLabel;
        if (this.badge.visible) {
          this.badge.position.set(avatarPosition.x, avatarPosition.y + 1.95 * avatarScale, avatarPosition.z);
          this.renderBadge();
        }
      }
      return;
    }

    const cfg = this.stateConfig();
    const s = avatarScale;

    // Ground disc — slow opacity breathe, no scale change
    if (this.ring && this.ring.visible) {
      this.ring.position.set(avatarPosition.x, avatarPosition.y + 0.01, avatarPosition.z);
      const breathe = 0.7 + 0.3 * Math.sin(this.elapsed * cfg.pulseSpeed * Math.PI * 2);
      (this.ring.material as THREE.MeshBasicMaterial).opacity = cfg.ringOpacity * breathe;
    }

    // Braille spinner — desktop only, floats just above head
    if (this.spinner) {
      this.spinner.visible = !isAR;
      if (!isAR) {
        const headY = avatarPosition.y + 1.75 * s;
        this.spinner.position.set(avatarPosition.x, headY, avatarPosition.z);

        const frame = Math.floor(this.elapsed * 8) % BRAILLE_FRAMES.length;
        if (frame !== this.lastFrame) {
          this.lastFrame = frame;
          this.renderSpinner();
        }
      }
    }

    // AR badge — float above head, below spinner
    if (this.badge && isAR) {
      this.badge.visible = true;
      this.badge.position.set(avatarPosition.x, avatarPosition.y + 1.95 * s, avatarPosition.z);

      if (Math.floor(this.elapsed * 12) !== Math.floor((this.elapsed - dt) * 12)) {
        this.renderBadge();
      }
    } else if (this.badge && !isAR) {
      this.badge.visible = false;
    }
  }

  dispose(): void {
    if (this.ring) {
      this.ring.geometry.dispose();
      (this.ring.material as THREE.Material).dispose();
      this.ring.removeFromParent();
    }
    if (this.spinner) {
      (this.spinner.material as THREE.SpriteMaterial).map?.dispose();
      (this.spinner.material as THREE.Material).dispose();
      this.spinner.removeFromParent();
    }
    if (this.badge) {
      (this.badge.material as THREE.SpriteMaterial).map?.dispose();
      (this.badge.material as THREE.Material).dispose();
      this.badge.removeFromParent();
    }
  }

  private stateConfig(): StateConfig {
    return (this.isAR ? AR_STATE_CONFIGS : STATE_CONFIGS)[this.state];
  }

  // ─── Braille Spinner Renderer ───────────────────────────────────────────────

  private renderSpinner(): void {
    if (!this.spinnerCanvas || !this.spinner) return;

    const ctx = this.spinnerCanvas.getContext('2d')!;
    const s = SPINNER_SIZE;
    const cfg = this.stateConfig();
    const frame = Math.floor(this.elapsed * 8) % BRAILLE_FRAMES.length;
    const ch = BRAILLE_FRAMES[frame];

    ctx.clearRect(0, 0, s, s);

    ctx.font = `${Math.round(s * 0.85)}px monospace`;
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    ctx.fillStyle = cfg.color;
    ctx.globalAlpha = 0.9;
    ctx.fillText(ch, s / 2, s / 2);
    ctx.globalAlpha = 1;

    (this.spinner.material as THREE.SpriteMaterial).map!.needsUpdate = true;
  }

  // ─── AR Badge Renderer ──────────────────────────────────────────────────────

  private renderBadge(): void {
    if (!this.badgeCanvas || !this.badge) return;

    const ctx = this.badgeCanvas.getContext('2d')!;
    const w = BADGE_W;
    const h = BADGE_H;
    const cfg = this.stateConfig();

    ctx.clearRect(0, 0, w, h);

    if (this.state === 'idle' && !this.workLabel) {
      (this.badge.material as THREE.SpriteMaterial).map!.needsUpdate = true;
      return;
    }

    // Glass background pill
    const pad = 6;
    const radius = 8;
    const bx = pad;
    const by = pad;
    const bw = w - pad * 2;
    const bh = h - pad * 2;

    ctx.save();
    ctx.beginPath();
    this.pillPath(ctx, bx, by, bw, bh, radius);
    ctx.fillStyle = SPATIAL.surface;
    ctx.fill();

    ctx.strokeStyle = SPATIAL.line;
    ctx.lineWidth = 1;
    ctx.stroke();
    ctx.restore();

    // Dot spinner
    const spinnerR = 10;
    const spinnerCx = bx + 22;
    const spinnerCy = h / 2;
    const frame = Math.floor(this.elapsed * 12) % BADGE_SPINNER_FRAMES;

    for (let i = 0; i < BADGE_SPINNER_FRAMES; i++) {
      const angle = (i / BADGE_SPINNER_FRAMES) * Math.PI * 2 - Math.PI / 2;
      const dist = (BADGE_SPINNER_FRAMES - ((i - frame + BADGE_SPINNER_FRAMES) % BADGE_SPINNER_FRAMES)) / BADGE_SPINNER_FRAMES;
      const alpha = 0.15 + dist * 0.85;
      const dotR = 1.5 + dist * 1;

      ctx.beginPath();
      ctx.arc(
        spinnerCx + Math.cos(angle) * spinnerR,
        spinnerCy + Math.sin(angle) * spinnerR,
        dotR, 0, Math.PI * 2,
      );
      ctx.fillStyle = cfg.color;
      ctx.globalAlpha = alpha;
      ctx.fill();
      ctx.globalAlpha = 1;
    }

    // Label text
    let label = cfg.label;
    if (this.state === 'tool_running' && this.toolName) {
      label = this.toolName;
    }
    if (this.message) {
      label = this.message;
    }

    setSpatialFont(ctx, `500 18px ${SPATIAL.font}`);
    ctx.fillStyle = 'rgba(255, 255, 255, 0.92)';
    ctx.textBaseline = 'middle';
    ctx.textAlign = 'left';

    const textX = spinnerCx + spinnerR + 14;
    const maxTextW = w - textX - 16;
    if (this.workLabel) label = this.workLabel;
    let displayText = label;
    if (ctx.measureText(displayText).width > maxTextW) {
      while (displayText.length > 0 && ctx.measureText(displayText + '…').width > maxTextW) {
        displayText = displayText.slice(0, -1);
      }
      displayText += '…';
    }
    ctx.fillText(displayText, textX, h / 2);

    if (this.state === 'thinking' || this.state === 'planning') {
      const dotCount = Math.floor(this.elapsed * 2) % 4;
      const dots = '.'.repeat(dotCount);
      const labelW = ctx.measureText(displayText).width;
      ctx.fillStyle = 'rgba(255, 255, 255, 0.5)';
      ctx.fillText(dots, textX + labelW + 2, h / 2);
    }

    (this.badge.material as THREE.SpriteMaterial).map!.needsUpdate = true;
  }

  private pillPath(ctx: CanvasRenderingContext2D, x: number, y: number, w: number, h: number, r: number): void {
    ctx.moveTo(x + r, y);
    ctx.lineTo(x + w - r, y);
    ctx.quadraticCurveTo(x + w, y, x + w, y + r);
    ctx.lineTo(x + w, y + h - r);
    ctx.quadraticCurveTo(x + w, y + h, x + w - r, y + h);
    ctx.lineTo(x + r, y + h);
    ctx.quadraticCurveTo(x, y + h, x, y + h - r);
    ctx.lineTo(x, y + r);
    ctx.quadraticCurveTo(x, y, x + r, y);
    ctx.closePath();
  }
}
