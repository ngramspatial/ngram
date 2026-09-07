// @ts-nocheck
import * as THREE from 'three';
import { SPATIAL } from './spatial-design.js';
import { PanelRenderer, type PanelContentType } from './panel-renderer.js';
import {
  BEZEL_DARK,
  BEZEL_DEPTH,
  BEZEL_LIGHT,
  BEZEL_PAD,
  createRoundedPlane,
} from './panel-geometry.js';
import {
  createPanelBody,
  removeBody,
  type PanelBodyHandle,
} from './physics-world.js';

export type PanelState = 'floating' | 'grabbed' | 'thrown' | 'settled';

export interface SpatialPanelSpec {
  id: string;
  type: PanelContentType;
  title?: string;
  content: string;
  width?: number;
  height?: number;
  pinned?: boolean;
  position?: { x: number; y: number; z: number };
  quaternion?: { x: number; y: number; z: number; w: number };
}

const DEFAULT_WORLD_W = 0.45;
const SETTLE_VELOCITY = 0.005;
const BILLBOARD_SPEED = 2.0;
const RESIZE_MARGIN = 0.025;
const MIN_PANEL_W = 0.15;
const MAX_PANEL_W = 1.5;

export type ResizeEdge = 'left' | 'right' | 'top' | 'bottom' | 'top-left' | 'top-right' | 'bottom-left' | 'bottom-right' | null;

export class SpatialPanel {
  readonly id: string;
  readonly group: THREE.Group;
  state: PanelState = 'floating';

  private renderer: PanelRenderer;
  private contentMesh: THREE.Mesh;
  private bezelMesh: THREE.Mesh;
  private physicsHandle: PanelBodyHandle | null = null;
  private pinned: boolean;
  private worldW: number;
  private worldH: number;
  private targetQuat = new THREE.Quaternion();
  private opacity = 0;
  private targetOpacity = 1;
  private disposed = false;
  private dark = true;
  private spec: SpatialPanelSpec;

  constructor(spec: SpatialPanelSpec, dark = true, private immersive = false) {
    this.id = spec.id;
    this.spec = spec;
    this.dark = dark;
    this.pinned = spec.pinned ?? false;
    this.worldW = spec.width ?? DEFAULT_WORLD_W;
    this.worldH = spec.height ?? this.worldW;

    this.group = new THREE.Group();
    this.group.renderOrder = 900;

    this.renderer = new PanelRenderer(1024, 1024, immersive);
    this.renderer.setTheme(dark);
    if (spec.height !== undefined) this.renderer.setFrameAspect(this.worldW / this.worldH);

    // Temporary geometry — will be replaced after render
    const tempGeo = createRoundedPlane(this.worldW, this.worldH, 0.015);

    // Content mesh
    const contentMat = new THREE.MeshBasicMaterial({
      map: this.renderer.getTexture(),
      transparent: true,
      opacity: 0,
      side: THREE.DoubleSide,
      depthWrite: false,
      toneMapped: !immersive,
    });
    this.contentMesh = new THREE.Mesh(tempGeo, contentMat);
    this.contentMesh.renderOrder = 902;

    // Bezel (tablet frame)
    const bezelGeo = createRoundedPlane(
      this.worldW + BEZEL_PAD * 2,
      this.worldH + BEZEL_PAD * 2,
      0.02,
    );
    const bezelMat = new THREE.MeshBasicMaterial({
      color: immersive ? SPATIAL.accentHex : dark ? BEZEL_DARK : BEZEL_LIGHT,
      transparent: true,
      opacity: 0,
      side: THREE.DoubleSide,
      depthWrite: false,
      toneMapped: !immersive,
    });
    this.bezelMesh = new THREE.Mesh(bezelGeo, bezelMat);
    this.bezelMesh.position.z = -BEZEL_DEPTH;
    this.bezelMesh.renderOrder = 901;

    this.group.add(this.bezelMesh);
    this.group.add(this.contentMesh);

    // Kick off async content render
    this.renderContent(spec);
  }

  private async renderContent(spec: SpatialPanelSpec): Promise<void> {
    const { width: texW, height: texH } = await this.renderer.render(
      spec.type,
      spec.content,
      spec.title,
    );
    if (this.disposed) return;

    // New panels follow their rendered aspect ratio. A restored panel carries
    // an explicit height, which must survive this asynchronous render pass.
    if (spec.height === undefined) {
      const aspect = texH / texW;
      this.worldH = this.worldW * aspect;
    }

    // Replace geometries
    this.contentMesh.geometry.dispose();
    this.contentMesh.geometry = createRoundedPlane(this.worldW, this.worldH, 0.015);

    this.bezelMesh.geometry.dispose();
    this.bezelMesh.geometry = createRoundedPlane(
      this.worldW + BEZEL_PAD * 2,
      this.worldH + BEZEL_PAD * 2,
      0.02,
    );

    // Create physics body now that we know dimensions
    if (!this.pinned) {
      const pos = this.group.position;
      this.physicsHandle = createPanelBody(
        pos.x, pos.y, pos.z,
        this.worldW / 2, this.worldH / 2,
      );
    }
  }

  setPosition(x: number, y: number, z: number): void {
    this.group.position.set(x, y, z);
    if (this.physicsHandle) {
      this.physicsHandle.body.setTranslation({ x, y, z }, true);
    }
  }

  getWorldSize(): { w: number; h: number } {
    return { w: this.worldW, h: this.worldH };
  }

  /** Switch to grabbed state — physics body becomes kinematic */
  grab(): void {
    this.state = 'grabbed';
    if (this.physicsHandle) {
      this.physicsHandle.body.setBodyType(2, true); // Kinematic
    }
  }

  /** Release from grab — apply velocity impulse */
  release(velocity: THREE.Vector3): void {
    if (this.physicsHandle) {
      // Commit the last visual drag transform before changing the kinematic
      // body back to dynamic. setNextKinematicTranslation may not have been
      // stepped yet when the pointer is released.
      const p = this.group.position;
      const q = this.group.quaternion;
      this.physicsHandle.body.setTranslation({ x: p.x, y: p.y, z: p.z }, true);
      this.physicsHandle.body.setRotation({ x: q.x, y: q.y, z: q.z, w: q.w }, true);
      this.state = 'thrown';
      this.physicsHandle.body.setBodyType(0, true); // Dynamic
      this.physicsHandle.body.setLinvel({ x: velocity.x, y: velocity.y, z: velocity.z }, true);
    } else {
      this.state = 'settled';
    }
  }

  /** Move kinematic body to follow hand; falls back to direct positioning for pinned panels */
  moveKinematic(x: number, y: number, z: number): void {
    if (this.state !== 'grabbed') return;
    if (this.physicsHandle) {
      this.physicsHandle.body.setNextKinematicTranslation({ x, y, z });
    }
    this.group.position.set(x, y, z);
  }

  setImmersive(value: boolean): void {
    if (this.immersive === value) return;
    this.immersive = value;
    this.renderer.setImmersive(value);
    this.renderer.setFrameAspect(this.worldW / this.worldH);
    for (const mesh of [this.contentMesh, this.bezelMesh]) {
      mesh.material.toneMapped = !value;
      mesh.material.needsUpdate = true;
    }
    this.bezelMesh.material.color.set(value ? SPATIAL.accentHex : this.dark ? BEZEL_DARK : BEZEL_LIGHT);
    this.renderer.rerender();
  }

  setTheme(dark: boolean): void {
    if (this.dark === dark) return;
    this.dark = dark;
    (this.bezelMesh.material as THREE.MeshBasicMaterial).color.set(this.immersive ? SPATIAL.accentHex : dark ? BEZEL_DARK : BEZEL_LIGHT);
    this.renderer.setTheme(dark);
    this.renderer.rerender();
  }

  fadeOut(): void {
    this.targetOpacity = 0;
  }

  isFullyFaded(): boolean {
    return this.targetOpacity === 0 && this.opacity <= 0.01;
  }

  /** Advance presentation/physics and report a newly settled throw. */
  update(dt: number, camera: THREE.Camera): boolean {
    if (this.disposed) return false;
    let settledThisFrame = false;

    // Sync position from physics
    if (this.physicsHandle && this.state !== 'grabbed') {
      const t = this.physicsHandle.body.translation();
      this.group.position.set(t.x, t.y, t.z);

      const r = this.physicsHandle.body.rotation();
      this.group.quaternion.set(r.x, r.y, r.z, r.w);

      // Check if settled
      if (this.state === 'thrown') {
        const v = this.physicsHandle.body.linvel();
        const speed = Math.sqrt(v.x * v.x + v.y * v.y + v.z * v.z);
        if (speed < SETTLE_VELOCITY) {
          this.state = 'settled';
          settledThisFrame = true;
        }
      }
    }

    // Billboard toward camera (when not grabbed)
    if (this.state !== 'grabbed') {
      this.targetQuat.copy(camera.quaternion);
      this.group.quaternion.slerp(this.targetQuat, Math.min(dt * BILLBOARD_SPEED, 1));

      // Write orientation back to physics
      if (this.physicsHandle) {
        const q = this.group.quaternion;
        this.physicsHandle.body.setRotation({ x: q.x, y: q.y, z: q.z, w: q.w }, true);
      }
    }

    // Animate opacity
    const fadeSpeed = 0.06;
    if (this.opacity < this.targetOpacity) {
      this.opacity = Math.min(this.opacity + fadeSpeed, this.targetOpacity);
    } else if (this.opacity > this.targetOpacity) {
      this.opacity = Math.max(this.opacity - fadeSpeed, this.targetOpacity);
    }
    (this.contentMesh.material as THREE.MeshBasicMaterial).opacity = this.opacity;
    (this.bezelMesh.material as THREE.MeshBasicMaterial).opacity = this.opacity * 0.95;
    return settledThisFrame;
  }

  /** Determine which edge/corner a local-space point is near, or null for center */
  hitEdge(worldPoint: THREE.Vector3): ResizeEdge {
    const local = this.group.worldToLocal(worldPoint.clone());
    const hw = this.worldW / 2;
    const hh = this.worldH / 2;
    const m = RESIZE_MARGIN;

    const nearLeft = local.x < -hw + m;
    const nearRight = local.x > hw - m;
    const nearTop = local.y > hh - m;
    const nearBottom = local.y < -hh + m;

    if (nearTop && nearLeft) return 'top-left';
    if (nearTop && nearRight) return 'top-right';
    if (nearBottom && nearLeft) return 'bottom-left';
    if (nearBottom && nearRight) return 'bottom-right';
    if (nearLeft) return 'left';
    if (nearRight) return 'right';
    if (nearTop) return 'top';
    if (nearBottom) return 'bottom';
    return null;
  }

  /** Resize the panel geometry and update the physics collider */
  resizeTo(newW: number, newH: number): void {
    this.worldW = Math.max(MIN_PANEL_W, Math.min(MAX_PANEL_W, newW));
    this.worldH = Math.max(MIN_PANEL_W, Math.min(MAX_PANEL_W, newH));
    this.renderer.setFrameAspect(this.worldW / this.worldH);

    this.contentMesh.geometry.dispose();
    this.contentMesh.geometry = createRoundedPlane(this.worldW, this.worldH, 0.015);

    this.bezelMesh.geometry.dispose();
    this.bezelMesh.geometry = createRoundedPlane(
      this.worldW + BEZEL_PAD * 2,
      this.worldH + BEZEL_PAD * 2,
      0.02,
    );

    if (this.physicsHandle) {
      const pos = this.physicsHandle.body.translation();
      removeBody(this.physicsHandle);
      this.physicsHandle = createPanelBody(pos.x, pos.y, pos.z, this.worldW / 2, this.worldH / 2);
      if (this.physicsHandle && this.state === 'grabbed') {
        this.physicsHandle.body.setBodyType(2, true);
      }
    }
  }

  /** Get the mesh for raycasting / grab proximity checks */
  getContentMesh(): THREE.Mesh {
    return this.contentMesh;
  }

  dispose(): void {
    if (this.disposed) return;
    this.disposed = true;

    this.renderer.dispose();

    if (this.physicsHandle) {
      removeBody(this.physicsHandle);
      this.physicsHandle = null;
    }

    this.contentMesh.geometry.dispose();
    (this.contentMesh.material as THREE.Material).dispose();
    this.bezelMesh.geometry.dispose();
    (this.bezelMesh.material as THREE.Material).dispose();

    this.group.removeFromParent();
  }
}
