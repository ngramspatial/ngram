// @ts-nocheck
import * as THREE from 'three';
import { SPATIAL, drawBrand, drawIcon, drawSurface, setSpatialFont, spatialTexture } from './spatial-design.js';

interface MenuItem {
  id: string;
  label: string;
  angle: number;
}

const MENU_ITEMS: MenuItem[] = [
  { id: 'mic',        label: 'Mic',        angle: Math.PI / 2 },
  { id: 'switch',     label: 'Shell',      angle: Math.PI / 2 + Math.PI * 2 / 6 },
  { id: 'vision',     label: 'Share View', angle: Math.PI / 2 + Math.PI * 4 / 6 },
  { id: 'resize',     label: 'Resize',     angle: Math.PI / 2 + Math.PI * 6 / 6 },
  { id: 'reposition', label: 'Move',       angle: Math.PI / 2 + Math.PI * 8 / 6 },
  { id: 'terminal',   label: 'Terminal',   angle: Math.PI / 2 + Math.PI * 10 / 6 },
];

const RING_RADIUS = 0.12;
const ITEM_WIDTH = 0.10;
const ITEM_HEIGHT = 0.045;
const CANVAS_W = 320;
const CANVAS_H = 140;
const HIGHLIGHT_DEADZONE = 0.3;
const TWO_PI = Math.PI * 2;

export class RadialMenu {
  private group = new THREE.Group();
  private scene: THREE.Scene | null = null;
  private itemMeshes: THREE.Mesh[] = [];
  private normalTextures: THREE.CanvasTexture[] = [];
  private highlightTextures: THREE.CanvasTexture[] = [];
  private normalMaterials: THREE.MeshBasicMaterial[] = [];
  private highlightMaterials: THREE.MeshBasicMaterial[] = [];
  private centerMesh: THREE.Mesh | null = null;
  private highlightedIndex = -1;
  private _isOpen = false;
  private micActive = false;

  get isOpen(): boolean {
    return this._isOpen;
  }

  attach(scene: THREE.Scene): void {
    this.scene = scene;
    this.group.visible = false;
    this.group.name = 'radial-menu';
    this.group.renderOrder = 1000;

    const centerCanvas = document.createElement('canvas');
    centerCanvas.width = centerCanvas.height = 256;
    const center = centerCanvas.getContext('2d')!;
    center.beginPath(); center.arc(128, 128, 116, 0, Math.PI * 2);
    center.fillStyle = SPATIAL.surface; center.fill();
    center.strokeStyle = SPATIAL.lineStrong; center.lineWidth = 2; center.stroke();
    drawBrand(center, 83, 46, 90);
    setSpatialFont(center, `600 32px ${SPATIAL.font}`, -0.065);
    center.fillStyle = SPATIAL.text; center.textAlign = 'center'; center.textBaseline = 'middle';
    center.fillText('ngram', 128, 175);
    const dotGeo = new THREE.PlaneGeometry(0.066, 0.066);
    const dotMat = new THREE.MeshBasicMaterial({
      map: spatialTexture(centerCanvas),
      transparent: true,
      depthTest: false,
      depthWrite: false,
      toneMapped: false,
      side: THREE.DoubleSide,
    });
    this.centerMesh = new THREE.Mesh(dotGeo, dotMat);
    this.centerMesh.renderOrder = 1001;
    this.group.add(this.centerMesh);

    for (let i = 0; i < MENU_ITEMS.length; i++) {
      const item = MENU_ITEMS[i];

      const normalTex = this.renderItem(item, false);
      const highlightTex = this.renderItem(item, true);
      this.normalTextures.push(normalTex);
      this.highlightTextures.push(highlightTex);

      const normalMat = new THREE.MeshBasicMaterial({
        map: normalTex,
        transparent: true,
        depthTest: false,
        depthWrite: false,
        toneMapped: false,
        side: THREE.DoubleSide,
      });
      const highlightMat = new THREE.MeshBasicMaterial({
        map: highlightTex,
        transparent: true,
        depthTest: false,
        depthWrite: false,
        toneMapped: false,
        side: THREE.DoubleSide,
      });
      this.normalMaterials.push(normalMat);
      this.highlightMaterials.push(highlightMat);

      const geo = new THREE.PlaneGeometry(ITEM_WIDTH, ITEM_HEIGHT);
      const mesh = new THREE.Mesh(geo, normalMat);
      mesh.renderOrder = 1001;

      const x = Math.cos(item.angle) * RING_RADIUS;
      const y = Math.sin(item.angle) * RING_RADIUS;
      mesh.position.set(x, y, 0);

      this.group.add(mesh);
      this.itemMeshes.push(mesh);
    }

    scene.add(this.group);
  }

  open(camera: THREE.Camera): void {
    if (!this.scene) return;
    this._isOpen = true;
    this.highlightedIndex = -1;
    this.resetHighlights();

    const dir = new THREE.Vector3();
    camera.getWorldDirection(dir);
    const pos = camera.position.clone().add(dir.multiplyScalar(0.45));
    this.group.position.copy(pos);
    this.group.quaternion.copy(camera.quaternion);
    this.group.visible = true;
  }

  close(): void {
    this._isOpen = false;
    this.group.visible = false;
    this.highlightedIndex = -1;
    this.resetHighlights();
  }

  highlight(x: number, y: number): void {
    if (!this._isOpen) return;

    const mag = Math.sqrt(x * x + y * y);
    if (mag < HIGHLIGHT_DEADZONE) {
      if (this.highlightedIndex !== -1) {
        this.highlightedIndex = -1;
        this.resetHighlights();
      }
      return;
    }

    const stickAngle = ((Math.atan2(-y, x) % TWO_PI) + TWO_PI) % TWO_PI;

    let bestIdx = -1;
    let bestDist = Infinity;
    for (let i = 0; i < MENU_ITEMS.length; i++) {
      const itemAngle = ((MENU_ITEMS[i].angle % TWO_PI) + TWO_PI) % TWO_PI;
      let diff = Math.abs(stickAngle - itemAngle);
      if (diff > Math.PI) diff = TWO_PI - diff;
      if (diff < bestDist) {
        bestDist = diff;
        bestIdx = i;
      }
    }

    if (bestIdx !== this.highlightedIndex) {
      this.highlightedIndex = bestIdx;
      for (let i = 0; i < this.itemMeshes.length; i++) {
        this.itemMeshes[i].material =
          i === bestIdx ? this.highlightMaterials[i] : this.normalMaterials[i];
      }
    }
  }

  select(): string | null {
    if (this.highlightedIndex < 0 || this.highlightedIndex >= MENU_ITEMS.length) return null;
    return MENU_ITEMS[this.highlightedIndex].id;
  }

  update(camera: THREE.Camera): void {
    if (!this._isOpen) return;
    this.group.quaternion.copy(camera.quaternion);
  }

  dispose(): void {
    for (const tex of this.normalTextures) tex.dispose();
    for (const tex of this.highlightTextures) tex.dispose();
    for (const mat of this.normalMaterials) mat.dispose();
    for (const mat of this.highlightMaterials) mat.dispose();
    for (const mesh of this.itemMeshes) mesh.geometry.dispose();
    if (this.centerMesh) {
      this.centerMesh.geometry.dispose();
      (this.centerMesh.material as THREE.MeshBasicMaterial).map?.dispose();
      (this.centerMesh.material as THREE.Material).dispose();
    }
    if (this.scene) this.scene.remove(this.group);
  }

  setTheme(_dark: boolean): void {
    // Spatial controls keep the same identity and focus across desktop themes.
  }

  setMicState(active: boolean): void {
    if (this.micActive === active) return;
    this.micActive = active;
    const i = MENU_ITEMS.findIndex(item => item.id === 'mic');
    if (!this.itemMeshes[i]) return;
    this.normalTextures[i].dispose();
    this.highlightTextures[i].dispose();
    this.normalTextures[i] = this.renderItem(MENU_ITEMS[i], false);
    this.highlightTextures[i] = this.renderItem(MENU_ITEMS[i], true);
    this.normalMaterials[i].map = this.normalTextures[i];
    this.highlightMaterials[i].map = this.highlightTextures[i];
  }

  private resetHighlights(): void {
    for (let i = 0; i < this.itemMeshes.length; i++) {
      this.itemMeshes[i].material = this.normalMaterials[i];
    }
  }

  private renderItem(item: MenuItem, highlighted: boolean): THREE.CanvasTexture {
    const canvas = document.createElement('canvas');
    canvas.width = CANVAS_W;
    canvas.height = CANVAS_H;
    const ctx = canvas.getContext('2d')!;

    const mic = item.id === 'mic';
    const ink = mic && !this.micActive && !highlighted ? SPATIAL.micInk : SPATIAL.text;
    const field = mic ? (this.micActive ? SPATIAL.recording : highlighted ? SPATIAL.micInk : SPATIAL.micOff)
      : highlighted ? SPATIAL.accent : SPATIAL.surface;
    drawSurface(ctx, 5, 5, CANVAS_W - 10, CANVAS_H - 10, field,
      highlighted ? SPATIAL.text : SPATIAL.lineStrong, 10);
    ctx.fillStyle = mic ? ink : highlighted ? SPATIAL.text : SPATIAL.accent;
    ctx.fillRect(22, 25, 3, CANVAS_H - 50);
    drawIcon(ctx, item.id, 43, 48, 38, ink);
    setSpatialFont(ctx, `600 26px ${SPATIAL.font}`);
    ctx.fillStyle = ink;
    ctx.textAlign = 'left';
    ctx.textBaseline = 'middle';
    ctx.fillText(mic ? (this.micActive ? 'Mic on' : 'Mic off') : item.label, 102, CANVAS_H / 2);
    return spatialTexture(canvas);
  }
}
