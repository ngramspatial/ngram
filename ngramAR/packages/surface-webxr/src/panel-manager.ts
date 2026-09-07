// @ts-nocheck
import * as THREE from 'three';
import { SpatialPanel, type SpatialPanelSpec } from './spatial-panel.js';
import { GrabController } from './grab-controller.js';
import { stepPhysics } from './physics-world.js';
import type { XRPointer, XRPointerManager } from './xr-pointer.js';

/** ~Human-scale reference; positions scale with measured avatar height */
const REF_AVATAR_H = 1.7;
const PANEL_Y_FRAC = 1.3 / REF_AVATAR_H;
const PANEL_X_FRAC = 0.8 / REF_AVATAR_H;
const PANEL_Z_FRAC = 0.4 / REF_AVATAR_H;

interface PanelEntry {
  panel: SpatialPanel;
  spawnTime: number;
  spec: SpatialPanelSpec;
  hiding?: boolean;
}

export class PanelManager {
  private panels = new Map<string, PanelEntry>();
  private scene: THREE.Scene | null = null;
  private panelIndex = 0;
  private grabController = new GrabController();
  private dark = true;
  private immersive = false;
  private _avatarVisualHeight = REF_AVATAR_H;
  private camera: THREE.Camera | null = null;
  onPersistChange: (() => void) | null = null;

  constructor() {
    this.grabController.onTransformChange = () => {
      this.onPersistChange?.();
    };
  }

  setAvatarVisualHeight(height: number): void {
    this._avatarVisualHeight = Math.max(0.12, height);
  }

  attach(scene: THREE.Scene): void {
    this.scene = scene;
  }

  set onDragStateChange(cb: ((dragging: boolean) => void) | null) {
    this.grabController.onDragStateChange = cb;
  }

  attachDesktop(renderer: THREE.WebGLRenderer, camera: THREE.Camera): void {
    this.camera = camera;
    this.grabController.attachDesktop(renderer, camera);
  }

  show(spec: SpatialPanelSpec, avatarPos: THREE.Vector3): void {
    if (!this.scene) {
      console.warn('[panels] no scene attached');
      return;
    }
    this.hide(spec.id);

    const panel = new SpatialPanel(spec, this.dark, this.immersive);

    // Position in an arc around the avatar, scaled to match avatar size
    const explicitPosition = spec.position
      && typeof spec.position === 'object'
      && Number.isFinite(spec.position.x)
      && Number.isFinite(spec.position.y)
      && Number.isFinite(spec.position.z);
    if (explicitPosition) {
      panel.setPosition(spec.position.x, spec.position.y, spec.position.z);
    } else {
      const vh = this._avatarVisualHeight;
      if (this.camera) {
        // Arrange panels from the viewer's perspective and slightly in front
        // of the body so the avatar cannot occlude them.
        const scale = vh / REF_AVATAR_H;
        const slots = [
          { x: -0.68, y: 1.32 },
          { x: 0.68, y: 1.32 },
          { x: -0.68, y: 0.76 },
          { x: 0.68, y: 0.76 },
          { x: 0, y: 1.88 },
        ];
        const slot = slots[this.panelIndex % slots.length];
        const towardViewer = new THREE.Vector3()
          .subVectors(this.camera.position, avatarPos);
        towardViewer.y = 0;
        if (towardViewer.lengthSq() < 0.0001) towardViewer.set(0, 0, 1);
        towardViewer.normalize();
        const viewerRight = new THREE.Vector3(1, 0, 0)
          .applyQuaternion(this.camera.quaternion);
        viewerRight.y = 0;
        if (viewerRight.lengthSq() < 0.0001) viewerRight.set(1, 0, 0);
        viewerRight.normalize();

        const pos = avatarPos.clone()
          .addScaledVector(viewerRight, slot.x * scale)
          .addScaledVector(towardViewer, 0.32 * scale);
        pos.y = avatarPos.y + slot.y * scale;
        panel.setPosition(pos.x, pos.y, pos.z);
      } else {
        const angle = (this.panelIndex * 0.7) - 0.35;
        const x = avatarPos.x + Math.sin(angle) * PANEL_X_FRAC * vh;
        const y = avatarPos.y + PANEL_Y_FRAC * vh;
        const z = avatarPos.z - Math.cos(angle) * PANEL_Z_FRAC * vh;
        panel.setPosition(x, y, z);
      }
      this.panelIndex = (this.panelIndex + 1) % 5;
    }

    if (
      spec.quaternion
      && Number.isFinite(spec.quaternion.x)
      && Number.isFinite(spec.quaternion.y)
      && Number.isFinite(spec.quaternion.z)
      && Number.isFinite(spec.quaternion.w)
    ) {
      panel.group.quaternion.set(
        spec.quaternion.x,
        spec.quaternion.y,
        spec.quaternion.z,
        spec.quaternion.w,
      );
    }

    this.scene.add(panel.group);
    this.panels.set(spec.id, { panel, spawnTime: performance.now(), spec });
    this.syncGrabController();
    this.onPersistChange?.();
  }

  hide(panelId: string): void {
    const entry = this.panels.get(panelId);
    if (!entry) return;
    entry.hiding = true;
    entry.panel.fadeOut();
    this.onPersistChange?.();

    setTimeout(() => {
      entry.panel.dispose();
      if (this.panels.get(panelId) !== entry) return;
      this.panels.delete(panelId);
      this.syncGrabController();
    }, 500);
  }

  hideAll(): void {
    for (const [id] of this.panels) this.hide(id);
    this.panelIndex = 0;
  }

  update(camera: THREE.Camera, dt: number): void {
    stepPhysics(dt);

    for (const [, entry] of this.panels) {
      if (entry.panel.update(dt, camera)) {
        // Persist the final post-throw transform once damping has settled.
        this.onPersistChange?.();
      }
    }
  }

  setImmersive(value: boolean): void {
    this.immersive = value;
    for (const entry of this.panels.values()) entry.panel.setImmersive(value);
  }

  setTheme(dark: boolean): void {
    this.dark = dark;
    for (const entry of this.panels.values()) {
      entry.panel.setTheme(dark);
    }
  }

  setPointerManager(pm: XRPointerManager): void {
    this.grabController.setPointerManager(pm);
  }

  /** Enter AR resize mode — next grab will resize instead of move */
  enterResizeMode(): void {
    this.grabController.resizeMode = true;
  }

  /** Exit AR resize mode without requiring a grab. */
  exitResizeMode(): void {
    this.grabController.resizeMode = false;
  }

  /** Call from AR render loop with unified XR pointers */
  updateAR(pointers: XRPointer[], camera: THREE.Camera): void {
    this.grabController.updateAR(pointers, camera);
  }

  private syncGrabController(): void {
    this.grabController.setPanels(
      Array.from(this.panels.values()).map(e => e.panel),
    );
  }

  /** Snapshot the actual transforms and dimensions, not the creation specs. */
  getSavedState(): SpatialPanelSpec[] {
    return Array.from(this.panels.values())
      .filter((entry) => !entry.hiding)
      .map((entry) => this.toSavedSpec(entry));
  }

  /** Restore both the new all-panel snapshot and legacy pinned specs. */
  loadSavedState(items: SpatialPanelSpec[], avatarPos: THREE.Vector3): void {
    if (!Array.isArray(items)) return;
    for (const spec of items) {
      if (!spec || typeof spec.id !== 'string' || typeof spec.content !== 'string') continue;
      this.show(spec, avatarPos);
    }
  }

  private toSavedSpec(entry: PanelEntry): SpatialPanelSpec {
    const p = entry.panel.group.position;
    const q = entry.panel.group.quaternion;
    const { w, h } = entry.panel.getWorldSize();
    return {
      ...entry.spec,
      width: w,
      height: h,
      position: { x: p.x, y: p.y, z: p.z },
      quaternion: { x: q.x, y: q.y, z: q.z, w: q.w },
    };
  }

  dispose(): void {
    for (const [, entry] of this.panels) {
      entry.panel.dispose();
    }
    this.panels.clear();
    this.grabController.dispose();
  }
}
