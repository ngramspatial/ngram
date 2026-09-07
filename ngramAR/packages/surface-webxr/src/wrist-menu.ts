// @ts-nocheck
import * as THREE from 'three';
import { SPATIAL, drawBrand, drawIcon, drawSurface, setSpatialFont, spatialTexture } from './spatial-design.js';

// ─── Menu items ──────────────────────────────────────────────────────────────

interface MenuItem {
  id: string;
  label: string;
}

const ITEMS: MenuItem[] = [
  { id: 'mic',        label: 'Mic' },
  { id: 'switch',     label: 'Shell' },
  { id: 'reposition', label: 'Move' },
  { id: 'resize',     label: 'Resize' },
  { id: 'terminal',   label: 'Terminal' },
];

// ─── Constants ───────────────────────────────────────────────────────────────

const CHARGE_START_MS = 1500;
const CHARGE_FULL_MS = 3000;
const MENU_TIMEOUT_MS = 12000;

const CHARGE_SIZE = 128;
const CHARGE_WORLD = 0.03;

const MENU_W = 360;
const MENU_ITEM_H = 62;
const MENU_PAD = 10;
const MENU_HEADER = 74;
const MENU_FOOTER = 40;
const MENU_H = MENU_HEADER + ITEMS.length * MENU_ITEM_H + MENU_PAD + MENU_FOOTER;
const MENU_WORLD_W = 0.22;
const MENU_WORLD_H = MENU_WORLD_W * (MENU_H / MENU_W);

const PINCH_DIST = 0.035;
const MENU_OFFSET_Y = 0.22;

const ACCENT = SPATIAL.accent;

type SelectCallback = (id: string) => void;
type ResizeCallback = (delta: number) => void;

type MenuState = 'idle' | 'charging' | 'open' | 'resizing';

// ─── WristMenu ───────────────────────────────────────────────────────────────

export class WristMenu {
  private scene: THREE.Scene | null = null;
  private selectCallback: SelectCallback | null = null;
  private resizeCallback: ResizeCallback | null = null;

  // Charge ring sprite
  private chargeSprite: THREE.Sprite | null = null;
  private chargeCanvas: HTMLCanvasElement | null = null;

  // Menu sprite
  private menuSprite: THREE.Sprite | null = null;
  private menuCanvas: HTMLCanvasElement | null = null;

  private state: MenuState = 'idle';
  private pinkyStartTime = 0;
  private highlightedIndex = -1;
  private micActive = false;
  private menuOpenTime = 0;

  // Resize drag tracking
  private resizePinchStartY = 0;

  // Reusable vectors
  private readonly _wristPos = new THREE.Vector3();
  private readonly _menuPos = new THREE.Vector3();
  private readonly _rightIndex = new THREE.Vector3();
  private readonly _rightThumb = new THREE.Vector3();
  private readonly _gazeDir = new THREE.Vector3();
  private readonly _gazeToMenu = new THREE.Vector3();
  private readonly _viewPosition = new THREE.Vector3();
  private readonly _viewQuaternion = new THREE.Quaternion();
  private pinchWasDown = false;
  private lastDebugLog = 0;

  get isOpen(): boolean {
    return this.state === 'open' || this.state === 'resizing';
  }

  attach(scene: THREE.Scene): void {
    this.scene = scene;

    // Charge ring sprite
    this.chargeCanvas = document.createElement('canvas');
    this.chargeCanvas.width = CHARGE_SIZE;
    this.chargeCanvas.height = CHARGE_SIZE;
    const chargeTex = spatialTexture(this.chargeCanvas);
    chargeTex.minFilter = THREE.LinearFilter;
    this.chargeSprite = new THREE.Sprite(new THREE.SpriteMaterial({
      map: chargeTex, transparent: true, depthTest: false, depthWrite: false, toneMapped: false,
    }));
    this.chargeSprite.scale.set(CHARGE_WORLD, CHARGE_WORLD, 1);
    this.chargeSprite.renderOrder = 1100;
    this.chargeSprite.visible = false;
    scene.add(this.chargeSprite);

    // Menu sprite
    this.menuCanvas = document.createElement('canvas');
    this.menuCanvas.width = MENU_W;
    this.menuCanvas.height = MENU_H;
    const menuTex = spatialTexture(this.menuCanvas);
    menuTex.minFilter = THREE.LinearFilter;
    this.menuSprite = new THREE.Sprite(new THREE.SpriteMaterial({
      map: menuTex, transparent: true, depthTest: false, depthWrite: false, toneMapped: false,
    }));
    this.menuSprite.scale.set(MENU_WORLD_W, MENU_WORLD_H, 1);
    this.menuSprite.renderOrder = 1100;
    this.menuSprite.visible = false;
    scene.add(this.menuSprite);

    this.renderMenu();
  }

  onSelect(cb: SelectCallback): void {
    this.selectCallback = cb;
  }

  setMicState(active: boolean): void {
    if (this.micActive === active) return;
    this.micActive = active;
    this.renderMenu();
  }

  onResize(cb: ResizeCallback): void {
    this.resizeCallback = cb;
  }

  // Kept for API compat
  onTap(_cb: () => void): void {}
  confirmSelection(): void {}

  processFrame(frame: XRFrame, refSpace: XRReferenceSpace, camera: THREE.Camera): void {
    const session = frame.session;
    const now = performance.now();

    // Gather hand joint data
    let leftWrist: XRJointPose | null = null;
    let leftPinky: XRJointPose | null = null;
    let leftIndex: XRJointPose | null = null;
    let leftMiddle: XRJointPose | null = null;
    let leftRing: XRJointPose | null = null;
    let rightIndexPose: XRJointPose | null = null;
    let rightThumbPose: XRJointPose | null = null;

    for (const source of session.inputSources) {
      if (!source.hand) continue;

      if (source.handedness === 'left') {
        const wristJ = source.hand.get('wrist');
        const pinkyJ = source.hand.get('pinky-finger-tip');
        const indexJ = source.hand.get('index-finger-tip');
        const middleJ = source.hand.get('middle-finger-tip');
        const ringJ = source.hand.get('ring-finger-tip');
        const thumbJ = source.hand.get('thumb-tip');
        if (wristJ) leftWrist = frame.getJointPose?.(wristJ, refSpace) ?? null;
        if (pinkyJ) leftPinky = frame.getJointPose?.(pinkyJ, refSpace) ?? null;
        if (indexJ) leftIndex = frame.getJointPose?.(indexJ, refSpace) ?? null;
        if (middleJ) leftMiddle = frame.getJointPose?.(middleJ, refSpace) ?? null;
        if (ringJ) leftRing = frame.getJointPose?.(ringJ, refSpace) ?? null;
        if (thumbJ) this.leftThumbPose = frame.getJointPose?.(thumbJ, refSpace) ?? null;
      }

      if (source.handedness === 'right') {
        const indexJ = source.hand.get('index-finger-tip');
        const thumbJ = source.hand.get('thumb-tip');
        if (indexJ) rightIndexPose = frame.getJointPose?.(indexJ, refSpace) ?? null;
        if (thumbJ) rightThumbPose = frame.getJointPose?.(thumbJ, refSpace) ?? null;
      }
    }

    // Get right hand positions
    if (rightIndexPose) {
      const p = rightIndexPose.transform.position;
      this._rightIndex.set(p.x, p.y, p.z);
    }
    if (rightThumbPose) {
      const p = rightThumbPose.transform.position;
      this._rightThumb.set(p.x, p.y, p.z);
    }

    const rightPinching = rightIndexPose && rightThumbPose
      && this._rightIndex.distanceTo(this._rightThumb) < PINCH_DIST;

    // Get left wrist position
    if (leftWrist) {
      const p = leftWrist.transform.position;
      this._wristPos.set(p.x, p.y, p.z);
    }

    // Detect middle+thumb hold on left hand
    const menuGesture = this.detectMiddleThumb(leftWrist, leftMiddle, this.leftThumbPose);

    // ─── State machine ───────────────────────────────────────────────────

    switch (this.state) {
      case 'idle': {
        this.chargeSprite!.visible = false;
        this.menuSprite!.visible = false;

        if (menuGesture) {
          this.state = 'charging';
          this.pinkyStartTime = now;
        }
        break;
      }

      case 'charging': {
        if (!menuGesture) {
          this.state = 'idle';
          this.chargeSprite!.visible = false;
          break;
        }

        const elapsed = now - this.pinkyStartTime;

        if (elapsed >= CHARGE_FULL_MS) {
          console.log('[wrist-menu] Menu opened!');
          this.state = 'open';
          this.menuOpenTime = now;
          this.highlightedIndex = -1;
          this.chargeSprite!.visible = false;
          this.menuSprite!.visible = true;
          this.positionMenuAboveWrist();
          this.renderMenu();
        } else if (elapsed >= CHARGE_START_MS) {
          // Show charge ring
          const progress = (elapsed - CHARGE_START_MS) / (CHARGE_FULL_MS - CHARGE_START_MS);
          this.chargeSprite!.visible = true;
          this.chargeSprite!.position.set(
            this._wristPos.x,
            this._wristPos.y + 0.08,
            this._wristPos.z,
          );
          this.renderChargeRing(progress);
        }
        break;
      }

      case 'open': {
        if (now - this.menuOpenTime > MENU_TIMEOUT_MS) {
          this.close();
          break;
        }

        this.positionMenuAboveWrist();

        // Gaze-based selection: cast a ray from the camera to find which item the user is looking at
        const gazeIdx = this.getGazeItem(camera);
        if (gazeIdx !== this.highlightedIndex) {
          this.highlightedIndex = gazeIdx;
          this.renderMenu();
        }

        // Pinch with either hand to confirm
        if (rightPinching && this.highlightedIndex >= 0 && !this.pinchWasDown) {
          const item = ITEMS[this.highlightedIndex];
          if (item.id === 'resize') {
            this.state = 'resizing';
            this.resizePinchStartY = this._rightIndex.y;
            this.menuSprite!.visible = false;
          } else {
            this.selectCallback?.(item.id);
            this.close();
          }
        }
        this.pinchWasDown = !!rightPinching;
        break;
      }

      case 'resizing': {
        if (rightPinching) {
          const deltaY = this._rightIndex.y - this.resizePinchStartY;
          this.resizeCallback?.(deltaY * 2);
          this.resizePinchStartY = this._rightIndex.y;
        } else {
          this.state = 'idle';
        }
        break;
      }
    }
  }

  close(): void {
    this.state = 'idle';
    this.highlightedIndex = -1;
    if (this.chargeSprite) this.chargeSprite.visible = false;
    if (this.menuSprite) this.menuSprite.visible = false;
  }

  dispose(): void {
    if (this.chargeSprite) {
      (this.chargeSprite.material as THREE.SpriteMaterial).map?.dispose();
      (this.chargeSprite.material as THREE.Material).dispose();
      this.chargeSprite.removeFromParent();
    }
    if (this.menuSprite) {
      (this.menuSprite.material as THREE.SpriteMaterial).map?.dispose();
      (this.menuSprite.material as THREE.Material).dispose();
      this.menuSprite.removeFromParent();
    }
  }

  // ─── Gesture detection ─────────────────────────────────────────────────────

  private leftThumbPose: XRJointPose | null = null;

  /** Middle fingertip touching thumb tip on the left hand */
  private detectMiddleThumb(
    wrist: XRJointPose | null,
    middle: XRJointPose | null,
    thumb: XRJointPose | null,
  ): boolean {
    if (!wrist || !middle || !thumb) return false;
    const touchDist = this.dist(middle.transform.position, thumb.transform.position);
    return touchDist < 0.035;
  }

  private dist(a: DOMPointReadOnly, b: DOMPointReadOnly): number {
    const dx = a.x - b.x;
    const dy = a.y - b.y;
    const dz = a.z - b.z;
    return Math.sqrt(dx * dx + dy * dy + dz * dz);
  }

  // ─── Menu positioning ──────────────────────────────────────────────────────

  private positionMenuAboveWrist(): void {
    if (!this.menuSprite) return;
    this._menuPos.set(
      this._wristPos.x,
      this._wristPos.y + MENU_OFFSET_Y,
      this._wristPos.z,
    );
    this.menuSprite.position.copy(this._menuPos);
  }

  private getGazeItem(camera: THREE.Camera): number {
    // Sprites face the full camera orientation, including head pitch and roll.
    // In camera space the gaze ray is (0,0,-1), so its intersection with the
    // sprite has local coordinates (-center.x, -center.y).
    camera.getWorldPosition(this._viewPosition);
    camera.getWorldQuaternion(this._viewQuaternion).invert();
    this._gazeToMenu.copy(this._menuPos).sub(this._viewPosition).applyQuaternion(this._viewQuaternion);
    if (this._gazeToMenu.z >= 0) return -1;
    const pixelX = (0.5 - this._gazeToMenu.x / MENU_WORLD_W) * MENU_W;
    const pixelY = (0.5 + this._gazeToMenu.y / MENU_WORLD_H) * MENU_H;
    if (pixelX < 10 || pixelX > MENU_W - 10) return -1;
    const idx = Math.floor((pixelY - MENU_HEADER) / MENU_ITEM_H);
    if (idx < 0 || idx >= ITEMS.length) return -1;
    const rowY = pixelY - MENU_HEADER - idx * MENU_ITEM_H;
    if (rowY < 2 || rowY > MENU_ITEM_H - 2) return -1;
    return idx;
  }

  // ─── Rendering ─────────────────────────────────────────────────────────────

  private renderChargeRing(progress: number): void {
    if (!this.chargeCanvas || !this.chargeSprite) return;
    const ctx = this.chargeCanvas.getContext('2d')!;
    const s = CHARGE_SIZE;
    const cx = s / 2;
    const cy = s / 2;
    const r = s / 2 - 6;

    ctx.clearRect(0, 0, s, s);

    // Background ring
    ctx.beginPath();
    ctx.arc(cx, cy, r, 0, Math.PI * 2);
    ctx.strokeStyle = 'rgba(255, 255, 255, 0.08)';
    ctx.lineWidth = 4;
    ctx.stroke();

    // Progress arc
    const startAngle = -Math.PI / 2;
    const endAngle = startAngle + progress * Math.PI * 2;
    ctx.beginPath();
    ctx.arc(cx, cy, r, startAngle, endAngle);
    ctx.strokeStyle = ACCENT;
    ctx.lineWidth = 4;
    ctx.lineCap = 'round';
    ctx.stroke();

    // Center dot
    ctx.beginPath();
    ctx.arc(cx, cy, 4, 0, Math.PI * 2);
    ctx.fillStyle = ACCENT;
    ctx.globalAlpha = 0.5 + progress * 0.5;
    ctx.fill();
    ctx.globalAlpha = 1;

    (this.chargeSprite.material as THREE.SpriteMaterial).map!.needsUpdate = true;
  }

  private renderMenu(): void {
    if (!this.menuCanvas || !this.menuSprite) return;
    const ctx = this.menuCanvas.getContext('2d')!;
    const w = MENU_W;
    const h = MENU_H;

    ctx.clearRect(0, 0, w, h);

    drawSurface(ctx, 2, 2, w - 4, h - 4);
    ctx.save();
    ctx.beginPath(); ctx.roundRect(2, 2, w - 4, h - 4, 10); ctx.clip();
    ctx.fillStyle = SPATIAL.accent;
    ctx.fillRect(2, 2, w - 4, 64);
    ctx.restore();
    drawBrand(ctx, 16, 14, 38);
    setSpatialFont(ctx, `700 25px ${SPATIAL.font}`, -0.065);
    ctx.textAlign = 'left'; ctx.textBaseline = 'middle'; ctx.fillStyle = SPATIAL.text;
    ctx.fillText('ngram', 64, 33);
    setSpatialFont(ctx, `500 12px ${SPATIAL.font}`, 0.08);
    ctx.textAlign = 'right'; ctx.fillText('IN YOUR SPACE', w - 18, 34);

    for (let i = 0; i < ITEMS.length; i++) {
      const item = ITEMS[i];
      const iy = MENU_HEADER + i * MENU_ITEM_H;
      const isHl = i === this.highlightedIndex;
      const mic = item.id === 'mic';
      const ink = mic && !this.micActive && !isHl ? SPATIAL.micInk : SPATIAL.text;
      const field = mic ? (this.micActive ? SPATIAL.recording : isHl ? SPATIAL.micInk : SPATIAL.micOff) : SPATIAL.accent;
      if (isHl || mic) drawSurface(ctx, 10, iy + 2, w - 20, MENU_ITEM_H - 4, field, isHl ? SPATIAL.text : SPATIAL.lineStrong, 5);
      drawIcon(ctx, item.id, 24, iy + 17, 27, ink);
      setSpatialFont(ctx, `500 21px ${SPATIAL.font}`);
      ctx.textAlign = 'left'; ctx.textBaseline = 'middle'; ctx.fillStyle = ink;
      ctx.fillText(mic ? (this.micActive ? 'Mic on' : 'Mic off') : item.label, 70, iy + MENU_ITEM_H / 2);
      setSpatialFont(ctx, `500 12px ${SPATIAL.font}`, 0.06);
      ctx.textAlign = 'right'; ctx.fillStyle = mic ? ink : isHl ? SPATIAL.text : SPATIAL.muted;
      ctx.fillText(isHl ? 'PINCH' : `0${i + 1}`, w - 24, iy + MENU_ITEM_H / 2);
      if (!isHl && i < ITEMS.length - 1) {
        ctx.fillStyle = SPATIAL.line;
        ctx.fillRect(20, iy + MENU_ITEM_H - 1, w - 40, 1);
      }
    }
    setSpatialFont(ctx, `400 13px ${SPATIAL.font}`);
    ctx.textAlign = 'center'; ctx.fillStyle = SPATIAL.muted;
    ctx.fillText('Look to choose · Pinch to select', w / 2, h - 23);
    (this.menuSprite.material as THREE.SpriteMaterial).map!.needsUpdate = true;
  }
}
