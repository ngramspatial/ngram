// @ts-nocheck
import * as THREE from 'three';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import {
  createShapeBody,
  removeBody,
  type PanelBodyHandle,
  type PhysicsShape,
} from './physics-world.js';
import type { SceneObjectPosition, ToyType } from '@ngram-ar/core';
import type { XRPointer, XRPointerManager } from './xr-pointer.js';

const LABEL_CANVAS_SIZE = 256;
const LABEL_WORLD_SIZE = 0.1;
const SETTLE_VELOCITY = 0.005;

const PASTEL_COLORS = [
  '#ff6b6b', '#ffa06b', '#ffd96b', '#6bffa0',
  '#6bd4ff', '#a06bff', '#ff6bd4', '#6bffe0',
];

const TOY_CONFIGS: Record<ToyType, { shape: PhysicsShape; defaultColor: string; sizeMultiplier: number }> = {
  ball:        { shape: 'sphere',   defaultColor: '#ff6b6b', sizeMultiplier: 1.0 },
  bouncy_ball: { shape: 'sphere',   defaultColor: '#6bff6b', sizeMultiplier: 0.8 },
  beach_ball:  { shape: 'sphere',   defaultColor: '#ffd96b', sizeMultiplier: 1.6 },
  dice:        { shape: 'cube',     defaultColor: '#ffffff', sizeMultiplier: 0.7 },
  marble:      { shape: 'sphere',   defaultColor: '#6bd4ff', sizeMultiplier: 0.5 },
};

type SceneObjectKind = 'primitive' | 'text' | 'image' | 'toy' | 'model';
type ObjectState = 'floating' | 'grabbed' | 'thrown' | 'settled';

export interface SavedObject {
  id: string;
  kind: SceneObjectKind;
  params: Record<string, unknown>;
}

interface SavedQuaternion {
  x: number;
  y: number;
  z: number;
  w: number;
}

interface SavedVector3 {
  x: number;
  y: number;
  z: number;
}

interface SceneObject {
  id: string;
  kind: SceneObjectKind;
  group: THREE.Group;
  mesh: THREE.Mesh | THREE.Sprite;
  label?: THREE.Sprite;
  physicsHandle: PanelBodyHandle | null;
  spawnTime: number;
  state: ObjectState;
}

const POSITION_OFFSETS: Record<string, THREE.Vector3> = {
  here:  new THREE.Vector3(0, 1.0, -0.8),
  left:  new THREE.Vector3(-0.8, 1.0, -0.5),
  right: new THREE.Vector3(0.8, 1.0, -0.5),
  above: new THREE.Vector3(0, 1.8, -0.5),
  front: new THREE.Vector3(0, 1.0, -1.2),
};

function randomPastel(): string {
  return PASTEL_COLORS[Math.floor(Math.random() * PASTEL_COLORS.length)];
}

function resolvePosition(
  pos: SceneObjectPosition | undefined,
  avatarPos: THREE.Vector3,
  avatarScale = 1,
): THREE.Vector3 {
  if (!pos) pos = 'here';
  if (typeof pos === 'object' && 'x' in pos) {
    return new THREE.Vector3(pos.x, pos.y, pos.z);
  }
  const offset = POSITION_OFFSETS[pos] ?? POSITION_OFFSETS.here;
  const scaled = offset.clone().multiplyScalar(avatarScale);
  return new THREE.Vector3().addVectors(avatarPos, scaled);
}

function createTextSprite(text: string, color: string, size: number): THREE.Sprite {
  const canvas = document.createElement('canvas');
  canvas.width = LABEL_CANVAS_SIZE * 3;
  canvas.height = LABEL_CANVAS_SIZE;
  const ctx = canvas.getContext('2d')!;

  const fontSize = Math.round(LABEL_CANVAS_SIZE * 0.35);
  ctx.font = `600 ${fontSize}px -apple-system, "Helvetica Neue", system-ui, sans-serif`;
  ctx.textAlign = 'center';
  ctx.textBaseline = 'middle';

  const padX = 46;
  const padY = 24;
  const textW = ctx.measureText(text).width;
  const pillW = Math.min(textW + padX * 2, canvas.width - 32);
  const pillH = fontSize + padY;
  const x = (canvas.width - pillW) / 2;
  const y = (canvas.height - pillH) / 2;

  ctx.beginPath();
  ctx.roundRect(x, y, pillW, pillH, 6);
  ctx.fillStyle = 'rgba(10, 10, 12, 0.75)';
  ctx.fill();

  ctx.fillStyle = color;
  ctx.fillText(text, canvas.width / 2, canvas.height / 2);

  const tex = new THREE.CanvasTexture(canvas);
  tex.minFilter = THREE.LinearFilter;
  const mat = new THREE.SpriteMaterial({
    map: tex,
    transparent: true,
    depthTest: false,
  });
  const sprite = new THREE.Sprite(mat);
  const aspect = canvas.width / canvas.height;
  sprite.scale.set(size * aspect, size, 1);
  return sprite;
}

function createGeometryForShape(shape: string, size: number): THREE.BufferGeometry {
  const half = size / 2;
  switch (shape) {
    case 'sphere':   return new THREE.SphereGeometry(half, 24, 16);
    case 'cylinder': return new THREE.CylinderGeometry(half * 0.6, half * 0.6, size, 24);
    case 'cone':     return new THREE.ConeGeometry(half * 0.6, size, 24);
    case 'torus':    return new THREE.TorusGeometry(half * 0.65, half * 0.25, 12, 32);
    case 'plane':    return new THREE.PlaneGeometry(size, size);
    case 'cube':
    default:         return new THREE.BoxGeometry(size, size, size);
  }
}

export class SceneObjectManager {
  private objects = new Map<string, SceneObject>();
  private savedObjects = new Map<string, SavedObject>();
  private scene: THREE.Scene | null = null;
  private envMap: THREE.Texture | null = null;
  private _avatarScale = 1;

  setAvatarScale(scale: number): void {
    this._avatarScale = scale;
  }

  private renderer: THREE.WebGLRenderer | null = null;
  private camera: THREE.Camera | null = null;
  private raycaster = new THREE.Raycaster();
  private mouseNDC = new THREE.Vector2();
  private isDragging = false;
  private dragEntry: SceneObject | null = null;
  private activeDesktopPointerId: number | null = null;
  private dragPlane = new THREE.Plane();
  private dragOffset = new THREE.Vector3();

  /** Fires when a desktop drag starts/stops so the caller can disable OrbitControls */
  onDragStateChange: ((dragging: boolean) => void) | null = null;

  /** Called whenever the saved state changes so the caller can persist to localStorage */
  onPersistChange: (() => void) | null = null;

  private isRestoringSavedState = false;

  private pointerManager: XRPointerManager | null = null;
  private arGrabs = new Map<string, {
    entry: SceneObject;
    offset: THREE.Vector3;
    pointerType: 'hand' | 'controller';
    rayDistance?: number;
    hitOffset?: THREE.Vector3;
    posHistory: Array<{ pos: THREE.Vector3; time: number }>;
  }>();
  private arRaycaster = new THREE.Raycaster();

  setPointerManager(pm: XRPointerManager): void {
    this.pointerManager = pm;
  }

  attach(scene: THREE.Scene, envMap?: THREE.Texture | null): void {
    this.scene = scene;
    this.envMap = envMap ?? null;
  }

  attachDesktop(renderer: THREE.WebGLRenderer, camera: THREE.Camera): void {
    this.renderer = renderer;
    this.camera = camera;
    const el = renderer.domElement;
    el.addEventListener('pointerdown', this.onPointerDown);
    el.addEventListener('pointermove', this.onPointerMove);
    el.addEventListener('pointerup', this.onPointerUp);
    el.addEventListener('pointercancel', this.onPointerUp);
  }

  detachDesktop(): void {
    if (!this.renderer) return;
    const el = this.renderer.domElement;
    el.removeEventListener('pointerdown', this.onPointerDown);
    el.removeEventListener('pointermove', this.onPointerMove);
    el.removeEventListener('pointerup', this.onPointerUp);
    el.removeEventListener('pointercancel', this.onPointerUp);
  }

  private updateNDC(e: PointerEvent): void {
    if (!this.renderer) return;
    const rect = this.renderer.domElement.getBoundingClientRect();
    this.mouseNDC.x = ((e.clientX - rect.left) / rect.width) * 2 - 1;
    this.mouseNDC.y = -((e.clientY - rect.top) / rect.height) * 2 + 1;
  }

  getDraggableMeshes(): Array<{ entry: SceneObject; mesh: THREE.Mesh }> {
    const result: Array<{ entry: SceneObject; mesh: THREE.Mesh }> = [];
    for (const entry of this.objects.values()) {
      if (entry.state === 'grabbed') continue;
      if (entry.mesh instanceof THREE.Mesh) {
        result.push({ entry, mesh: entry.mesh });
      }
    }
    return result;
  }

  private getDesktopDraggableTargets(): Array<{ entry: SceneObject; target: THREE.Object3D }> {
    const result: Array<{ entry: SceneObject; target: THREE.Object3D }> = [];
    for (const entry of this.objects.values()) {
      if (entry.state === 'grabbed') continue;
      result.push({ entry, target: entry.mesh });
      if (entry.label) result.push({ entry, target: entry.label });
    }
    return result;
  }

  private onPointerDown = (e: PointerEvent): void => {
    // Reserve secondary-button drags for camera pan controls.
    if (e.button !== 0) return;
    if (!this.camera || this.objects.size === 0) return;
    this.updateNDC(e);
    this.raycaster.setFromCamera(this.mouseNDC, this.camera);

    const candidates = this.getDesktopDraggableTargets();
    if (candidates.length === 0) return;

    const intersects = this.raycaster.intersectObjects(
      candidates.map(c => c.target),
      false,
    );
    if (intersects.length === 0) return;

    const hitObj = intersects[0].object;
    const hit = candidates.find(c => c.target === hitObj);
    if (!hit) return;

    this.isDragging = true;
    this.activeDesktopPointerId = e.pointerId;
    this.dragEntry = hit.entry;
    hit.entry.state = 'grabbed';
    if (hit.entry.physicsHandle) {
      hit.entry.physicsHandle.body.setBodyType(2, true);
    }

    const camDir = new THREE.Vector3();
    this.camera.getWorldDirection(camDir);
    this.dragPlane.setFromNormalAndCoplanarPoint(camDir, hit.entry.group.position);

    const hitPoint = new THREE.Vector3();
    this.raycaster.ray.intersectPlane(this.dragPlane, hitPoint);
    this.dragOffset.subVectors(hit.entry.group.position, hitPoint);

    if (this.renderer) {
      this.renderer.domElement.style.cursor = 'grabbing';
      this.renderer.domElement.setPointerCapture(e.pointerId);
    }
    this.onDragStateChange?.(true);
  };

  private onPointerMove = (e: PointerEvent): void => {
    if (!this.camera || !this.renderer) return;
    if (this.isDragging && this.activeDesktopPointerId != null && e.pointerId !== this.activeDesktopPointerId) {
      return;
    }
    this.updateNDC(e);
    this.raycaster.setFromCamera(this.mouseNDC, this.camera);

    if (this.isDragging && this.dragEntry) {
      const hitPoint = new THREE.Vector3();
      if (this.raycaster.ray.intersectPlane(this.dragPlane, hitPoint)) {
        hitPoint.add(this.dragOffset);
        if (this.dragEntry.physicsHandle) {
          this.dragEntry.physicsHandle.body.setNextKinematicTranslation({
            x: hitPoint.x, y: hitPoint.y, z: hitPoint.z,
          });
        }
        this.dragEntry.group.position.copy(hitPoint);
      }
      return;
    }

    const candidates = this.getDesktopDraggableTargets();
    if (candidates.length > 0) {
      const intersects = this.raycaster.intersectObjects(candidates.map(c => c.target), false);
      this.renderer.domElement.style.cursor = intersects.length > 0 ? 'grab' : '';
    }
  };

  private onPointerUp = (e: PointerEvent): void => {
    if (this.activeDesktopPointerId != null && e.pointerId !== this.activeDesktopPointerId) return;
    if (!this.isDragging || !this.dragEntry) return;
    if (this.dragEntry.physicsHandle) {
      const p = this.dragEntry.group.position;
      const q = this.dragEntry.group.quaternion;
      this.dragEntry.physicsHandle.body.setTranslation({ x: p.x, y: p.y, z: p.z }, true);
      this.dragEntry.physicsHandle.body.setRotation({ x: q.x, y: q.y, z: q.z, w: q.w }, true);
      this.dragEntry.physicsHandle.body.setBodyType(0, true);
      this.dragEntry.physicsHandle.body.setLinvel({ x: 0, y: 0, z: 0 }, true);
    }
    this.dragEntry.state = 'settled';
    this.syncSavedTransform(this.dragEntry);
    this.isDragging = false;
    this.dragEntry = null;
    if (this.renderer) {
      if (this.activeDesktopPointerId != null && this.renderer.domElement.hasPointerCapture(this.activeDesktopPointerId)) {
        this.renderer.domElement.releasePointerCapture(this.activeDesktopPointerId);
      }
      this.renderer.domElement.style.cursor = '';
    }
    this.activeDesktopPointerId = null;
    this.onDragStateChange?.(false);
    this.notifyPersistChange();
  };

  spawnObject(
    objectId: string,
    shape: string,
    avatarPos: THREE.Vector3,
    options: {
      color?: string;
      size?: number;
      position?: SceneObjectPosition;
      label?: string;
      physics?: boolean;
    } = {},
  ): void {
    if (!this.scene) return;
    this.remove(objectId);

    const size = options.size ?? 0.15;
    const color = options.color ?? randomPastel();
    const enablePhysics = options.physics !== false;
    const pos = resolvePosition(options.position, avatarPos, this._avatarScale);

    const geo = createGeometryForShape(shape, size);
    const mat = new THREE.MeshStandardMaterial({
      color: new THREE.Color(color),
      roughness: 0.4,
      metalness: 0.1,
      envMap: this.envMap,
      envMapIntensity: 0.6,
    });
    const mesh = new THREE.Mesh(geo, mat);
    mesh.castShadow = true;
    mesh.receiveShadow = true;

    const group = new THREE.Group();
    group.add(mesh);
    group.position.copy(pos);

    let labelSprite: THREE.Sprite | undefined;
    if (options.label) {
      labelSprite = createTextSprite(options.label, '#ffffff', LABEL_WORLD_SIZE);
      labelSprite.position.y = size / 2 + 0.08;
      group.add(labelSprite);
    }

    let physicsHandle: PanelBodyHandle | null = null;
    if (enablePhysics && shape !== 'plane') {
      physicsHandle = createShapeBody(
        shape as PhysicsShape,
        pos.x, pos.y, pos.z,
        size,
        false,
      );
    }

    this.scene.add(group);
    this.objects.set(objectId, {
      id: objectId,
      kind: 'primitive',
      group,
      mesh,
      label: labelSprite,
      physicsHandle,
      spawnTime: performance.now(),
      state: 'floating',
    });
    this.savedObjects.set(objectId, {
      id: objectId, kind: 'primitive',
      params: { shape, color, size, position: { x: pos.x, y: pos.y, z: pos.z }, label: options.label, physics: options.physics },
    });
    this.notifyPersistChange();
  }

  spawnText(
    objectId: string,
    text: string,
    avatarPos: THREE.Vector3,
    options: { position?: SceneObjectPosition; size?: number; color?: string } = {},
  ): void {
    if (!this.scene) return;
    this.remove(objectId);

    const size = options.size ?? 0.12;
    const color = options.color ?? '#ffffff';
    const pos = resolvePosition(options.position, avatarPos, this._avatarScale);

    const sprite = createTextSprite(text, color, size);
    const group = new THREE.Group();
    group.add(sprite);
    group.position.copy(pos);

    this.scene.add(group);
    this.objects.set(objectId, {
      id: objectId,
      kind: 'text',
      group,
      mesh: sprite,
      physicsHandle: null,
      spawnTime: performance.now(),
      state: 'floating',
    });
    this.savedObjects.set(objectId, {
      id: objectId, kind: 'text',
      params: { text, color, size, position: { x: pos.x, y: pos.y, z: pos.z } },
    });
    this.notifyPersistChange();
  }

  spawnImage(
    objectId: string,
    url: string,
    avatarPos: THREE.Vector3,
    options: { position?: SceneObjectPosition; width?: number } = {},
  ): void {
    if (!this.scene) return;
    this.remove(objectId);

    const worldW = options.width ?? 0.4;
    const pos = resolvePosition(options.position, avatarPos, this._avatarScale);

    const group = new THREE.Group();
    group.position.copy(pos);

    const placeholderGeo = new THREE.PlaneGeometry(worldW, worldW);
    const placeholderMat = new THREE.MeshBasicMaterial({
      color: 0x222222,
      transparent: true,
      opacity: 0.3,
      side: THREE.DoubleSide,
    });
    const placeholderMesh = new THREE.Mesh(placeholderGeo, placeholderMat);
    group.add(placeholderMesh);
    this.scene.add(group);

    const entry: SceneObject = {
      id: objectId,
      kind: 'image',
      group,
      mesh: placeholderMesh,
      physicsHandle: null,
      spawnTime: performance.now(),
      state: 'floating',
    };
    this.objects.set(objectId, entry);
    this.savedObjects.set(objectId, {
      id: objectId, kind: 'image',
      params: { url, width: worldW, position: { x: pos.x, y: pos.y, z: pos.z } },
    });
    this.notifyPersistChange();

    const loader = new THREE.TextureLoader();
    loader.load(url, (tex) => {
      const aspect = tex.image.width / tex.image.height;
      const h = worldW / aspect;

      placeholderMesh.geometry.dispose();
      (placeholderMesh.material as THREE.Material).dispose();

      const geo = new THREE.PlaneGeometry(worldW, h);
      const mat = new THREE.MeshBasicMaterial({
        map: tex,
        transparent: true,
        side: THREE.DoubleSide,
        depthWrite: false,
      });
      const imgMesh = new THREE.Mesh(geo, mat);
      group.remove(placeholderMesh);
      group.add(imgMesh);
      entry.mesh = imgMesh;
    });
  }

  spawnToy(
    objectId: string,
    toyType: ToyType,
    avatarPos: THREE.Vector3,
    options: { color?: string; position?: SceneObjectPosition; impulse?: { x: number; y: number; z: number } } = {},
  ): void {
    if (!this.scene) return;
    this.remove(objectId);

    const cfg = TOY_CONFIGS[toyType] ?? TOY_CONFIGS.ball;
    const baseSize = 0.12 * cfg.sizeMultiplier;
    const color = options.color ?? cfg.defaultColor;
    const pos = resolvePosition(options.position, avatarPos, this._avatarScale);

    const geo = createGeometryForShape(cfg.shape, baseSize);
    const mat = new THREE.MeshStandardMaterial({
      color: new THREE.Color(color),
      roughness: toyType === 'marble' ? 0.05 : 0.3,
      metalness: toyType === 'marble' ? 0.8 : 0.05,
      envMap: this.envMap,
      envMapIntensity: toyType === 'marble' ? 1.2 : 0.6,
    });
    const mesh = new THREE.Mesh(geo, mat);
    mesh.castShadow = true;

    const group = new THREE.Group();
    group.add(mesh);
    group.position.copy(pos);

    const physicsHandle = createShapeBody(cfg.shape, pos.x, pos.y, pos.z, baseSize, true);

    if (physicsHandle && options.impulse) {
      physicsHandle.body.setLinvel(options.impulse, true);
    }

    this.scene.add(group);
    this.objects.set(objectId, {
      id: objectId,
      kind: 'toy',
      group,
      mesh,
      physicsHandle,
      spawnTime: performance.now(),
      state: options.impulse ? 'thrown' : 'floating',
    });
    this.savedObjects.set(objectId, {
      id: objectId, kind: 'toy',
      params: { toyType, color, position: { x: pos.x, y: pos.y, z: pos.z } },
    });
    this.notifyPersistChange();
  }

  spawnModel(
    objectId: string,
    url: string,
    avatarPos: THREE.Vector3,
    options: {
      position?: SceneObjectPosition;
      scale?: number;
      rotation?: { x: number; y: number; z: number };
      physics?: boolean;
      label?: string;
    } = {},
  ): void {
    if (!this.scene) return;
    this.remove(objectId);

    const pos = resolvePosition(options.position, avatarPos, this._avatarScale);
    const scale = options.scale ?? 1.0;

    const group = new THREE.Group();
    group.position.copy(pos);
    if (options.rotation) {
      group.rotation.set(options.rotation.x, options.rotation.y, options.rotation.z);
    }

    // Placeholder while loading
    const placeholderGeo = new THREE.BoxGeometry(0.1 * scale, 0.1 * scale, 0.1 * scale);
    const placeholderMat = new THREE.MeshStandardMaterial({ color: 0x444466, transparent: true, opacity: 0.3 });
    const placeholderMesh = new THREE.Mesh(placeholderGeo, placeholderMat);
    group.add(placeholderMesh);
    this.scene.add(group);

    let labelSprite: THREE.Sprite | undefined;
    if (options.label) {
      labelSprite = createTextSprite(options.label, '#ffffff', LABEL_WORLD_SIZE);
      labelSprite.position.y = 0.15 * scale;
      group.add(labelSprite);
    }

    const entry: SceneObject = {
      id: objectId,
      kind: 'model',
      group,
      mesh: placeholderMesh,
      label: labelSprite,
      physicsHandle: null,
      spawnTime: performance.now(),
      state: 'floating',
    };
    this.objects.set(objectId, entry);
    this.savedObjects.set(objectId, {
      id: objectId, kind: 'model',
      params: { url, scale, position: { x: pos.x, y: pos.y, z: pos.z }, rotation: options.rotation, physics: options.physics, label: options.label },
    });
    this.notifyPersistChange();

    const loader = new GLTFLoader();
    loader.load(url, (gltf) => {
      if (!this.objects.has(objectId)) return;

      group.remove(placeholderMesh);
      placeholderGeo.dispose();
      placeholderMat.dispose();

      const model = gltf.scene;
      model.scale.setScalar(scale);
      model.traverse((child) => {
        if (child instanceof THREE.Mesh) {
          child.castShadow = true;
          child.receiveShadow = true;
        }
      });
      group.add(model);

      // Compute bounding box for physics
      if (options.physics) {
        const box = new THREE.Box3().setFromObject(model);
        const size = box.getSize(new THREE.Vector3());
        const maxDim = Math.max(size.x, size.y, size.z);
        if (maxDim > 0.01) {
          const currentPos = group.position;
          entry.physicsHandle = createShapeBody('cube', currentPos.x, currentPos.y, currentPos.z, maxDim, false);
          if (entry.physicsHandle) {
            const q = group.quaternion;
            entry.physicsHandle.body.setRotation({ x: q.x, y: q.y, z: q.z, w: q.w }, true);
          }
        }
      }

      // Update the label position based on model bounds
      if (labelSprite) {
        const box = new THREE.Box3().setFromObject(model);
        labelSprite.position.y = box.max.y + 0.08;
      }
    }, undefined, (err) => {
      console.warn(`[scene-objects] Failed to load model: ${url}`, err);
    });
  }

  remove(objectId: string): void {
    const entry = this.objects.get(objectId);
    const hadSavedEntry = this.savedObjects.has(objectId);
    if (entry) {
      this.disposeEntry(entry);
      this.objects.delete(objectId);
    }
    this.savedObjects.delete(objectId);
    if (entry || hadSavedEntry) this.notifyPersistChange();
  }

  clearAll(): void {
    for (const entry of this.objects.values()) {
      this.disposeEntry(entry);
    }
    this.objects.clear();
    this.savedObjects.clear();
    this.notifyPersistChange();
  }

  update(dt: number, camera: THREE.Camera): void {
    let shouldPersist = false;

    for (const [, entry] of this.objects) {
      // Sync physics position
      if (entry.physicsHandle && entry.state !== 'grabbed') {
        const t = entry.physicsHandle.body.translation();
        entry.group.position.set(t.x, t.y, t.z);

        const r = entry.physicsHandle.body.rotation();
        entry.group.quaternion.set(r.x, r.y, r.z, r.w);

        if (entry.state === 'thrown') {
          const v = entry.physicsHandle.body.linvel();
          const speed = Math.sqrt(v.x * v.x + v.y * v.y + v.z * v.z);
          if (speed < SETTLE_VELOCITY) {
            entry.state = 'settled';
            this.syncSavedTransform(entry);
            shouldPersist = true;
          }
        }
      }

      // Billboard text/image toward camera
      if (entry.kind === 'text' || entry.kind === 'image') {
        entry.group.quaternion.copy(camera.quaternion);
      }

      // Billboard labels toward camera
      if (entry.label) {
        entry.label.quaternion.copy(camera.quaternion);
      }
    }

    if (shouldPersist) this.notifyPersistChange();
  }

  /** Call from AR render loop — accepts unified XRPointer[] for both hands and controllers */
  updateAR(pointers: XRPointer[]): void {
    const activePointerIds = new Set<string>();

    for (const pointer of pointers) {
      activePointerIds.add(pointer.id);
      const existing = this.arGrabs.get(pointer.id);

      if (pointer.isActive && !pointer.wasActive && !existing) {
        this.handleARGrabStart(pointer);
      } else if (pointer.isActive && existing) {
        this.handleARGrabHold(pointer, existing);
      } else if (!pointer.isActive && existing) {
        this.handleARGrabEnd(existing);
        this.arGrabs.delete(pointer.id);
      }

      if (!pointer.isActive && !existing) {
        this.updateARRayFeedback(pointer);
      }
    }

    for (const [id, grab] of this.arGrabs) {
      if (!activePointerIds.has(id)) {
        this.handleARGrabEnd(grab);
        this.arGrabs.delete(id);
      }
    }
  }

  private getARHittableMeshes(): Array<{ entry: SceneObject; mesh: THREE.Mesh }> {
    const result: Array<{ entry: SceneObject; mesh: THREE.Mesh }> = [];
    for (const entry of this.objects.values()) {
      if (entry.state === 'grabbed') continue;
      if (entry.mesh instanceof THREE.Mesh) {
        result.push({ entry, mesh: entry.mesh });
      }
    }
    return result;
  }

  private handleARGrabStart(pointer: XRPointer): void {
    this.arRaycaster.set(pointer.ray.origin, pointer.ray.direction);
    const candidates = this.getARHittableMeshes();
    if (candidates.length === 0) return;

    const intersects = this.arRaycaster.intersectObjects(candidates.map(c => c.mesh), false);
    if (intersects.length === 0) return;

    const hitMesh = intersects[0].object;
    const hit = candidates.find(c => c.mesh === hitMesh);
    if (!hit) return;

    const entry = hit.entry;
    entry.state = 'grabbed';
    if (entry.physicsHandle) {
      entry.physicsHandle.body.setBodyType(2, true);
    }

    const offset = new THREE.Vector3().subVectors(entry.group.position, pointer.position);
    const rayDistance = Math.max(
      0.05,
      new THREE.Vector3().subVectors(intersects[0].point, pointer.ray.origin).dot(pointer.ray.direction),
    );
    const hitOffset = new THREE.Vector3().subVectors(entry.group.position, intersects[0].point);

    this.arGrabs.set(pointer.id, {
      entry,
      offset,
      pointerType: pointer.type,
      rayDistance: pointer.type === 'controller' ? rayDistance : undefined,
      hitOffset: pointer.type === 'controller' ? hitOffset : undefined,
      posHistory: [{ pos: pointer.position.clone(), time: performance.now() }],
    });

    if (this.pointerManager && pointer.type === 'controller') {
      this.pointerManager.setRayHitPoint(pointer.id, intersects[0].point);
    }
  }

  private handleARGrabHold(
    pointer: XRPointer,
    grab: {
      entry: SceneObject;
      offset: THREE.Vector3;
      pointerType: 'hand' | 'controller';
      rayDistance?: number;
      hitOffset?: THREE.Vector3;
      posHistory: Array<{ pos: THREE.Vector3; time: number }>;
    },
  ): void {
    let targetPos: THREE.Vector3;
    if (grab.pointerType === 'controller' && grab.rayDistance != null && grab.hitOffset) {
      const rayPoint = new THREE.Vector3()
        .copy(pointer.ray.direction)
        .multiplyScalar(grab.rayDistance)
        .add(pointer.ray.origin);
      targetPos = rayPoint.add(grab.hitOffset);
    } else {
      targetPos = new THREE.Vector3().addVectors(pointer.position, grab.offset);
    }

    if (grab.entry.physicsHandle) {
      grab.entry.physicsHandle.body.setNextKinematicTranslation({
        x: targetPos.x, y: targetPos.y, z: targetPos.z,
      });
    }
    grab.entry.group.position.copy(targetPos);

    const now = performance.now();
    grab.posHistory.push({ pos: pointer.position.clone(), time: now });
    if (grab.posHistory.length > 6) grab.posHistory.shift();
  }

  private handleARGrabEnd(
    grab: { entry: SceneObject; offset: THREE.Vector3; posHistory: Array<{ pos: THREE.Vector3; time: number }> },
  ): void {
    const velocity = this.computeARVelocity(grab.posHistory);

    if (grab.entry.physicsHandle) {
      const p = grab.entry.group.position;
      const q = grab.entry.group.quaternion;
      grab.entry.physicsHandle.body.setTranslation({ x: p.x, y: p.y, z: p.z }, true);
      grab.entry.physicsHandle.body.setRotation({ x: q.x, y: q.y, z: q.z, w: q.w }, true);
      grab.entry.physicsHandle.body.setBodyType(0, true);
      grab.entry.physicsHandle.body.setLinvel({ x: velocity.x, y: velocity.y, z: velocity.z }, true);
      grab.entry.state = 'thrown';
    } else {
      grab.entry.state = 'settled';
    }
    this.syncSavedTransform(grab.entry);
    this.notifyPersistChange();
  }

  private computeARVelocity(history: Array<{ pos: THREE.Vector3; time: number }>): THREE.Vector3 {
    if (history.length < 2) return new THREE.Vector3();
    const first = history[0];
    const last = history[history.length - 1];
    const dt = (last.time - first.time) / 1000;
    if (dt < 0.001) return new THREE.Vector3();
    return new THREE.Vector3().subVectors(last.pos, first.pos).divideScalar(dt).clampLength(0, 3);
  }

  private updateARRayFeedback(pointer: XRPointer): void {
    if (pointer.type !== 'controller' || !this.pointerManager) return;
    this.arRaycaster.set(pointer.ray.origin, pointer.ray.direction);
    const candidates = this.getARHittableMeshes();
    if (candidates.length === 0) return;
    const intersects = this.arRaycaster.intersectObjects(candidates.map(c => c.mesh), false);
    if (intersects.length > 0) {
      this.pointerManager.setRayHitPoint(pointer.id, intersects[0].point);
    }
  }

  getObjectCount(): number {
    return this.objects.size;
  }

  getSavedState(): SavedObject[] {
    for (const entry of this.objects.values()) {
      this.syncSavedTransform(entry);
    }

    // Return detached state so callers cannot accidentally mutate the manager's
    // persistence baseline after placing an object.
    return Array.from(this.savedObjects.values(), item => ({
      id: item.id,
      kind: item.kind,
      params: this.cloneParams(item.params),
    }));
  }

  loadSavedState(items: SavedObject[], avatarPos: THREE.Vector3): void {
    this.isRestoringSavedState = true;
    try {
      for (const item of items) {
        if (!item || typeof item.id !== 'string' || !item.params) continue;
        const p = item.params as any;
        const pos = this.readVector3(p.position) ?? { x: 0, y: 1, z: 0 };
        switch (item.kind) {
          case 'primitive':
            this.spawnObject(item.id, p.shape ?? 'cube', avatarPos, {
              color: p.color, size: p.size, position: pos, label: p.label, physics: p.physics,
            });
            break;
          case 'text':
            this.spawnText(item.id, p.text ?? '', avatarPos, {
              position: pos, size: p.size, color: p.color,
            });
            break;
          case 'image':
            this.spawnImage(item.id, p.url ?? '', avatarPos, {
              position: pos, width: p.width,
            });
            break;
          case 'toy':
            this.spawnToy(item.id, p.toyType ?? 'ball', avatarPos, {
              color: p.color, position: pos,
            });
            break;
          case 'model':
            this.spawnModel(item.id, p.url ?? '', avatarPos, {
              position: pos, scale: p.scale, rotation: p.rotation, physics: p.physics, label: p.label,
            });
            break;
          default:
            continue;
        }
        this.restoreSavedTransform(item.id, p);
      }
    } finally {
      this.isRestoringSavedState = false;
    }
    this.notifyPersistChange();
  }

  getObjectIds(): string[] {
    return Array.from(this.objects.keys());
  }

  private notifyPersistChange(): void {
    if (!this.isRestoringSavedState) this.onPersistChange?.();
  }

  private syncSavedTransform(entry: SceneObject): void {
    const saved = this.savedObjects.get(entry.id);
    if (!saved) return;

    // A physics step can advance between render updates, so prefer Rapier's
    // current transform unless the user is actively positioning the group.
    if (entry.physicsHandle && entry.state !== 'grabbed') {
      const t = entry.physicsHandle.body.translation();
      const r = entry.physicsHandle.body.rotation();
      entry.group.position.set(t.x, t.y, t.z);
      entry.group.quaternion.set(r.x, r.y, r.z, r.w);
    }

    const position: SavedVector3 = {
      x: entry.group.position.x,
      y: entry.group.position.y,
      z: entry.group.position.z,
    };
    const quaternion: SavedQuaternion = {
      x: entry.group.quaternion.x,
      y: entry.group.quaternion.y,
      z: entry.group.quaternion.z,
      w: entry.group.quaternion.w,
    };
    const groupScale: SavedVector3 = {
      x: entry.group.scale.x,
      y: entry.group.scale.y,
      z: entry.group.scale.z,
    };

    saved.params = {
      ...saved.params,
      position,
      quaternion,
      // `scale` is already the model asset scale. Keep the scene-node scale in
      // a distinct field so legacy model snapshots retain their semantics.
      groupScale,
    };
  }

  private restoreSavedTransform(objectId: string, params: Record<string, unknown>): void {
    const entry = this.objects.get(objectId);
    if (!entry) return;

    const position = this.readVector3(params.position);
    const quaternion = this.readQuaternion(params.quaternion);
    const groupScale = this.readVector3(params.groupScale);

    if (position) entry.group.position.set(position.x, position.y, position.z);
    if (quaternion) entry.group.quaternion.set(quaternion.x, quaternion.y, quaternion.z, quaternion.w).normalize();
    if (groupScale) entry.group.scale.set(groupScale.x, groupScale.y, groupScale.z);

    if (entry.physicsHandle) {
      const p = entry.group.position;
      const q = entry.group.quaternion;
      entry.physicsHandle.body.setTranslation({ x: p.x, y: p.y, z: p.z }, true);
      entry.physicsHandle.body.setRotation({ x: q.x, y: q.y, z: q.z, w: q.w }, true);
    }

    // Re-base the internal snapshot onto the normalized live transform while
    // retaining unknown fields from newer/older snapshot versions.
    const saved = this.savedObjects.get(objectId);
    if (saved) saved.params = { ...params };
    this.syncSavedTransform(entry);
  }

  private readVector3(value: unknown): SavedVector3 | null {
    if (Array.isArray(value) && value.length >= 3
      && value.slice(0, 3).every(n => typeof n === 'number' && Number.isFinite(n))) {
      return { x: value[0], y: value[1], z: value[2] };
    }
    if (!value || typeof value !== 'object') return null;
    const v = value as Record<string, unknown>;
    if (![v.x, v.y, v.z].every(n => typeof n === 'number' && Number.isFinite(n))) return null;
    return { x: v.x as number, y: v.y as number, z: v.z as number };
  }

  private readQuaternion(value: unknown): SavedQuaternion | null {
    if (Array.isArray(value) && value.length >= 4
      && value.slice(0, 4).every(n => typeof n === 'number' && Number.isFinite(n))) {
      return { x: value[0], y: value[1], z: value[2], w: value[3] };
    }
    if (!value || typeof value !== 'object') return null;
    const q = value as Record<string, unknown>;
    if (![q.x, q.y, q.z, q.w].every(n => typeof n === 'number' && Number.isFinite(n))) return null;
    return { x: q.x as number, y: q.y as number, z: q.z as number, w: q.w as number };
  }

  private cloneParams(params: Record<string, unknown>): Record<string, unknown> {
    const cloneValue = (value: unknown): unknown => {
      if (Array.isArray(value)) return value.map(cloneValue);
      if (value && typeof value === 'object') {
        return Object.fromEntries(Object.entries(value as Record<string, unknown>).map(([key, nested]) => [key, cloneValue(nested)]));
      }
      return value;
    };
    return cloneValue(params) as Record<string, unknown>;
  }

  private disposeEntry(entry: SceneObject): void {
    if (entry.physicsHandle) {
      removeBody(entry.physicsHandle);
    }
    entry.group.traverse((child) => {
      if (child instanceof THREE.Mesh) {
        child.geometry.dispose();
        if (Array.isArray(child.material)) {
          child.material.forEach(m => m.dispose());
        } else {
          child.material.dispose();
        }
      }
      if (child instanceof THREE.Sprite) {
        (child.material as THREE.SpriteMaterial).map?.dispose();
        child.material.dispose();
      }
    });
    entry.group.removeFromParent();
  }
}
