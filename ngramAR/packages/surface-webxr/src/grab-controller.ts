// @ts-nocheck
import * as THREE from 'three';
import type { SpatialPanel, ResizeEdge } from './spatial-panel.js';
import type { XRPointer, XRPointerManager } from './xr-pointer.js';

const VELOCITY_FRAMES = 6;
const HAND_GRAB_SURFACE_MARGIN = 0.06;
const HAND_GRAB_DEPTH = 0.14;

const EDGE_CURSORS: Record<string, string> = {
  'left': 'ew-resize', 'right': 'ew-resize',
  'top': 'ns-resize', 'bottom': 'ns-resize',
  'top-left': 'nwse-resize', 'bottom-right': 'nwse-resize',
  'top-right': 'nesw-resize', 'bottom-left': 'nesw-resize',
};

interface GrabState {
  panel: SpatialPanel;
  offset: THREE.Vector3;
  pointerId: string;
  pointerType: 'hand' | 'controller';
  rayDistance?: number;
  hitOffset?: THREE.Vector3;
  posHistory: Array<{ pos: THREE.Vector3; time: number }>;
}

interface ResizeState {
  panel: SpatialPanel;
  edge: ResizeEdge;
  startW: number;
  startH: number;
  startPoint: THREE.Vector3;
}

/**
 * Manages grab interaction for SpatialPanels.
 * AR: accepts XRPointer[] (hands and controllers) and raycasts against panel meshes.
 * Desktop: uses mouse raycasting with drag and edge-resize.
 */
export class GrabController {
  private panels: SpatialPanel[] = [];
  private activeGrabs = new Map<string, GrabState>();
  private raycaster = new THREE.Raycaster();
  private pointerManager: XRPointerManager | null = null;

  // Desktop drag state
  private isDragging = false;
  private dragPanel: SpatialPanel | null = null;
  private dragPlane = new THREE.Plane();
  private dragOffset = new THREE.Vector3();
  private mouseNDC = new THREE.Vector2();

  // Desktop resize state
  private isResizing = false;
  private resizeState: ResizeState | null = null;

  // AR resize mode (triggered by radial menu)
  private _resizeMode = false;
  private arResizeState: { panel: SpatialPanel; pointerId: string; startW: number; startH: number; startPos: THREE.Vector3 } | null = null;

  private renderer: THREE.WebGLRenderer | null = null;
  private camera: THREE.Camera | null = null;

  onDragStateChange: ((dragging: boolean) => void) | null = null;
  /** Fired once a user finishes moving or resizing a panel. */
  onTransformChange: ((panel: SpatialPanel) => void) | null = null;

  constructor() {
    this.onMouseDown = this.onMouseDown.bind(this);
    this.onMouseMove = this.onMouseMove.bind(this);
    this.onMouseUp = this.onMouseUp.bind(this);
  }

  setPanels(panels: SpatialPanel[]): void {
    this.panels = panels;
  }

  setPointerManager(pm: XRPointerManager): void {
    this.pointerManager = pm;
  }

  set resizeMode(v: boolean) {
    this._resizeMode = v;
  }

  get resizeMode(): boolean {
    return this._resizeMode;
  }

  /** Call once to set up desktop mouse events */
  attachDesktop(renderer: THREE.WebGLRenderer, camera: THREE.Camera): void {
    this.renderer = renderer;
    this.camera = camera;
    const el = renderer.domElement;
    el.addEventListener('mousedown', this.onMouseDown);
    el.addEventListener('mousemove', this.onMouseMove);
    el.addEventListener('mouseup', this.onMouseUp);
  }

  detachDesktop(): void {
    if (!this.renderer) return;
    const el = this.renderer.domElement;
    el.removeEventListener('mousedown', this.onMouseDown);
    el.removeEventListener('mousemove', this.onMouseMove);
    el.removeEventListener('mouseup', this.onMouseUp);
  }

  // ─── AR pointer interaction ─────────────────────────────────────────────

  updateAR(pointers: XRPointer[], camera: THREE.Camera): void {
    const activePointerIds = new Set<string>();

    for (const pointer of pointers) {
      activePointerIds.add(pointer.id);
      const existing = this.activeGrabs.get(pointer.id);

      if (pointer.isActive && !pointer.wasActive && !existing) {
        this.handleARGrabStart(pointer, camera);
      } else if (pointer.isActive && existing) {
        this.handleARGrabHold(pointer, existing);
      } else if (!pointer.isActive && existing) {
        this.handleARGrabEnd(existing);
        this.activeGrabs.delete(pointer.id);
      }

      if (!pointer.isActive && !existing) {
        this.updateRayHitFeedback(pointer);
      }
    }

    for (const [id, grab] of this.activeGrabs) {
      if (!activePointerIds.has(id)) {
        this.handleARGrabEnd(grab);
        this.activeGrabs.delete(id);
      }
    }
  }

  private handleARGrabStart(pointer: XRPointer, camera: THREE.Camera): void {
    let hit: { panel: SpatialPanel; point: THREE.Vector3 } | null = null;

    // Hand tracking feels more natural when pinch can "touch grab" a nearby panel,
    // instead of requiring a perfectly aligned wrist ray.
    if (pointer.type === 'hand') {
      hit = this.hitTestPanelsByProximity(pointer.position);
    }
    if (!hit) {
      this.raycaster.set(pointer.ray.origin, pointer.ray.direction);
      hit = this.hitTestPanels();
    }

    if (!hit) return;

    if (this._resizeMode) {
      const { w, h } = hit.panel.getWorldSize();
      hit.panel.grab();
      this.arResizeState = {
        panel: hit.panel,
        pointerId: pointer.id,
        startW: w,
        startH: h,
        startPos: pointer.position.clone(),
      };
      // Track the pointer through hold/end just like a normal grab; otherwise
      // the resize state would never receive subsequent XR pointer frames.
      this.activeGrabs.set(pointer.id, {
        panel: hit.panel,
        offset: new THREE.Vector3(),
        pointerId: pointer.id,
        pointerType: pointer.type,
        posHistory: [{ pos: pointer.position.clone(), time: performance.now() }],
      });
      this._resizeMode = false;
      return;
    }

    const offset = new THREE.Vector3().subVectors(hit.panel.group.position, pointer.position);
    const rayDistance = Math.max(
      0.05,
      new THREE.Vector3().subVectors(hit.point, pointer.ray.origin).dot(pointer.ray.direction),
    );
    const hitOffset = new THREE.Vector3().subVectors(hit.panel.group.position, hit.point);
    hit.panel.grab();

    this.activeGrabs.set(pointer.id, {
      panel: hit.panel,
      offset,
      pointerId: pointer.id,
      pointerType: pointer.type,
      rayDistance: pointer.type === 'controller' ? rayDistance : undefined,
      hitOffset: pointer.type === 'controller' ? hitOffset : undefined,
      posHistory: [{ pos: pointer.position.clone(), time: performance.now() }],
    });

    if (this.pointerManager && pointer.type === 'controller') {
      this.pointerManager.setRayHitPoint(pointer.id, hit.point);
    }
  }

  private handleARGrabHold(pointer: XRPointer, grab: GrabState): void {
    if (this.arResizeState && this.arResizeState.pointerId === pointer.id) {
      const delta = pointer.position.distanceTo(this.arResizeState.startPos);
      const dir = pointer.position.y > this.arResizeState.startPos.y ? 1 : -1;
      const scale = 1 + dir * delta * 3;
      this.arResizeState.panel.resizeTo(
        this.arResizeState.startW * scale,
        this.arResizeState.startH * scale,
      );
      return;
    }

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
    grab.panel.moveKinematic(targetPos.x, targetPos.y, targetPos.z);

    const now = performance.now();
    grab.posHistory.push({ pos: pointer.position.clone(), time: now });
    if (grab.posHistory.length > VELOCITY_FRAMES) {
      grab.posHistory.shift();
    }
  }

  private handleARGrabEnd(grab: GrabState): void {
    if (this.arResizeState && this.arResizeState.pointerId === grab.pointerId) {
      this.arResizeState.panel.release(new THREE.Vector3());
      this.onTransformChange?.(this.arResizeState.panel);
      this.arResizeState = null;
      return;
    }

    const velocity = this.computeVelocity(grab.posHistory);
    grab.panel.release(velocity);
    this.onTransformChange?.(grab.panel);
  }

  private updateRayHitFeedback(pointer: XRPointer): void {
    if (pointer.type !== 'controller' || !this.pointerManager) return;

    this.raycaster.set(pointer.ray.origin, pointer.ray.direction);
    const hit = this.hitTestPanels();
    if (hit) {
      this.pointerManager.setRayHitPoint(pointer.id, hit.point);
    }
  }

  private computeVelocity(history: Array<{ pos: THREE.Vector3; time: number }>): THREE.Vector3 {
    if (history.length < 2) return new THREE.Vector3();

    const first = history[0];
    const last = history[history.length - 1];
    const dt = (last.time - first.time) / 1000;
    if (dt < 0.001) return new THREE.Vector3();

    return new THREE.Vector3()
      .subVectors(last.pos, first.pos)
      .divideScalar(dt)
      .clampLength(0, 3);
  }

  // ─── Desktop mouse interaction ────────────────────────────────────────────

  private updateMouseNDC(e: MouseEvent): void {
    if (!this.renderer) return;
    const rect = this.renderer.domElement.getBoundingClientRect();
    this.mouseNDC.x = ((e.clientX - rect.left) / rect.width) * 2 - 1;
    this.mouseNDC.y = -((e.clientY - rect.top) / rect.height) * 2 + 1;
  }

  private hitTestPanels(): { panel: SpatialPanel; point: THREE.Vector3 } | null {
    const meshes = this.panels
      .filter(p => p.state !== 'grabbed')
      .map(p => ({ panel: p, mesh: p.getContentMesh() }));
    if (meshes.length === 0) return null;

    const intersects = this.raycaster.intersectObjects(meshes.map(m => m.mesh), false);
    if (intersects.length === 0) return null;

    const hitMesh = intersects[0].object;
    const entry = meshes.find(m => m.mesh === hitMesh);
    if (!entry) return null;
    return { panel: entry.panel, point: intersects[0].point.clone() };
  }

  private hitTestPanelsByProximity(pos: THREE.Vector3): { panel: SpatialPanel; point: THREE.Vector3 } | null {
    let best: { panel: SpatialPanel; point: THREE.Vector3; score: number } | null = null;

    for (const panel of this.panels) {
      if (panel.state === 'grabbed') continue;

      const { w, h } = panel.getWorldSize();
      const local = panel.group.worldToLocal(pos.clone());
      const hw = w / 2;
      const hh = h / 2;

      if (Math.abs(local.z) > HAND_GRAB_DEPTH) continue;
      if (Math.abs(local.x) > hw + HAND_GRAB_SURFACE_MARGIN) continue;
      if (Math.abs(local.y) > hh + HAND_GRAB_SURFACE_MARGIN) continue;

      const cx = THREE.MathUtils.clamp(local.x, -hw, hw);
      const cy = THREE.MathUtils.clamp(local.y, -hh, hh);
      const surfacePoint = panel.group.localToWorld(new THREE.Vector3(cx, cy, 0));
      const score = pos.distanceToSquared(surfacePoint);

      if (!best || score < best.score) {
        best = { panel, point: surfacePoint, score };
      }
    }

    return best ? { panel: best.panel, point: best.point } : null;
  }

  private onMouseDown(e: MouseEvent): void {
    // Reserve secondary-button drags for camera pan controls.
    if (e.button !== 0) return;
    if (!this.camera || this.panels.length === 0) return;
    this.updateMouseNDC(e);
    this.raycaster.setFromCamera(this.mouseNDC, this.camera);

    const hit = this.hitTestPanels();
    if (!hit) return;

    const edge = hit.panel.hitEdge(hit.point);
    const camDir = new THREE.Vector3();
    this.camera.getWorldDirection(camDir);
    this.dragPlane.setFromNormalAndCoplanarPoint(camDir, hit.panel.group.position);

    if (edge) {
      const { w, h } = hit.panel.getWorldSize();
      this.isResizing = true;
      this.resizeState = {
        panel: hit.panel,
        edge,
        startW: w,
        startH: h,
        startPoint: hit.point,
      };
      hit.panel.grab();
      if (this.renderer) this.renderer.domElement.style.cursor = EDGE_CURSORS[edge] ?? 'nwse-resize';
      this.onDragStateChange?.(true);
    } else {
      this.isDragging = true;
      this.dragPanel = hit.panel;
      hit.panel.grab();
      if (this.renderer) this.renderer.domElement.style.cursor = 'grabbing';
      this.onDragStateChange?.(true);

      const hitPoint = new THREE.Vector3();
      this.raycaster.ray.intersectPlane(this.dragPlane, hitPoint);
      this.dragOffset.subVectors(hit.panel.group.position, hitPoint);
    }
  }

  private onMouseMove(e: MouseEvent): void {
    if (!this.camera) return;
    this.updateMouseNDC(e);
    this.raycaster.setFromCamera(this.mouseNDC, this.camera);

    // Active resize
    if (this.isResizing && this.resizeState) {
      const hitPoint = new THREE.Vector3();
      if (!this.raycaster.ray.intersectPlane(this.dragPlane, hitPoint)) return;

      const local = this.resizeState.panel.group.worldToLocal(hitPoint.clone());
      const startLocal = this.resizeState.panel.group.worldToLocal(this.resizeState.startPoint.clone());
      const dx = local.x - startLocal.x;
      const dy = local.y - startLocal.y;
      const edge = this.resizeState.edge!;

      let newW = this.resizeState.startW;
      let newH = this.resizeState.startH;

      if (edge.includes('right')) newW = this.resizeState.startW + dx * 2;
      if (edge.includes('left')) newW = this.resizeState.startW - dx * 2;
      if (edge.includes('top')) newH = this.resizeState.startH + dy * 2;
      if (edge.includes('bottom')) newH = this.resizeState.startH - dy * 2;

      this.resizeState.panel.resizeTo(newW, newH);
      return;
    }

    // Active drag
    if (this.isDragging && this.dragPanel) {
      const hitPoint = new THREE.Vector3();
      if (this.raycaster.ray.intersectPlane(this.dragPlane, hitPoint)) {
        hitPoint.add(this.dragOffset);
        this.dragPanel.moveKinematic(hitPoint.x, hitPoint.y, hitPoint.z);
      }
      return;
    }

    // Hover cursor feedback
    if (this.panels.length > 0) {
      const hit = this.hitTestPanels();
      if (hit) {
        const edge = hit.panel.hitEdge(hit.point);
        if (this.renderer) {
          this.renderer.domElement.style.cursor = edge ? (EDGE_CURSORS[edge] ?? 'nwse-resize') : 'grab';
        }
      } else if (this.renderer) {
        this.renderer.domElement.style.cursor = '';
      }
    }
  }

  private onMouseUp(_e: MouseEvent): void {
    if (this.isResizing && this.resizeState) {
      const panel = this.resizeState.panel;
      panel.release(new THREE.Vector3());
      this.isResizing = false;
      this.resizeState = null;
      if (this.renderer) this.renderer.domElement.style.cursor = '';
      this.onDragStateChange?.(false);
      this.onTransformChange?.(panel);
      return;
    }
    if (!this.isDragging || !this.dragPanel) return;
    const panel = this.dragPanel;
    panel.release(new THREE.Vector3());
    this.isDragging = false;
    this.dragPanel = null;
    if (this.renderer) this.renderer.domElement.style.cursor = '';
    this.onDragStateChange?.(false);
    this.onTransformChange?.(panel);
  }

  dispose(): void {
    this.detachDesktop();
    for (const grab of this.activeGrabs.values()) {
      grab.panel.release(new THREE.Vector3());
    }
    this.activeGrabs.clear();
    if (this.arResizeState) {
      this.arResizeState.panel.release(new THREE.Vector3());
      this.arResizeState = null;
    }
  }
}
