// @ts-nocheck
/**
 * Spatial Overlay Bridge
 *
 * When the user enters AR/VR mode, DOM overlay panels (app panels,
 * YouTube, terminal, browser) can't be seen spatially. This bridge
 * creates 3D panel proxies that display the overlay content on
 * textured meshes in the scene.
 */

import * as THREE from 'three';
import { SPATIAL } from './spatial-design.js';
import { PanelRenderer } from './panel-renderer.js';
import {
  BEZEL_DEPTH,
  BEZEL_PAD,
  createRoundedPlane,
} from './panel-geometry.js';
import type { XRPointer, XRPointerManager } from './xr-pointer.js';

const REF_AVATAR_H = 1.7;
const PROXY_Y_FRAC = 1.4 / REF_AVATAR_H;
const HAND_GRAB_SURFACE_MARGIN = 0.06;
const HAND_GRAB_DEPTH = 0.14;

/** Per-frame fade increment — do not use Clock.getDelta() here; in WebXR it is often 0. */
const FADE_PER_FRAME = 0.1;

interface SpatialProxy {
  id: string;
  kind: 'app' | 'youtube' | 'terminal' | 'browser' | 'music';
  group: THREE.Group;
  contentMesh: THREE.Mesh;
  bezelMesh: THREE.Mesh;
  texture: THREE.Texture;
  renderer: PanelRenderer | null;
  updateInterval: ReturnType<typeof setInterval> | null;
  fade: number;
  worldW: number;
  worldH: number;
}

interface ProxyGrabState {
  group: THREE.Group;
  offset: THREE.Vector3;
  pointerId: string;
  pointerType: 'hand' | 'controller';
  rayDistance?: number;
  hitOffset?: THREE.Vector3;
}

export interface SavedOverlayTransform {
  position: { x: number; y: number; z: number };
}

export type SavedOverlayTransforms = Record<string, SavedOverlayTransform>;

export class SpatialOverlayBridge {
  private scene: THREE.Scene | null = null;
  private proxies = new Map<string, SpatialProxy>();
  private active = false;
  private dark = true;
  private _avatarScale = 1;
  private _anchorVisualHeight = REF_AVATAR_H;
  private raycaster = new THREE.Raycaster();
  private pointerManager: XRPointerManager | null = null;
  private arGrabs = new Map<string, ProxyGrabState>();
  private savedTransforms = new Map<string, SavedOverlayTransform>();
  private isRestoringSavedTransforms = false;

  /** Called whenever a durable proxy position changes. */
  onPersistChange: (() => void) | null = null;

  attach(scene: THREE.Scene): void {
    this.scene = scene;
  }

  getSavedTransforms(): SavedOverlayTransforms {
    for (const proxy of this.proxies.values()) this.captureProxyTransform(proxy);

    const result: SavedOverlayTransforms = {};
    for (const [id, transform] of this.savedTransforms) {
      result[id] = { position: { ...transform.position } };
    }
    return result;
  }

  loadSavedTransforms(transforms: SavedOverlayTransforms | null | undefined): void {
    if (!transforms || typeof transforms !== 'object') return;

    this.isRestoringSavedTransforms = true;
    try {
      this.savedTransforms.clear();
      for (const [id, raw] of Object.entries(transforms)) {
        const position = this.readSavedPosition(raw);
        if (!position) continue;
        this.savedTransforms.set(id, { position });
      }

      for (const proxy of this.proxies.values()) {
        const saved = this.savedTransforms.get(proxy.id);
        if (saved) proxy.group.position.set(saved.position.x, saved.position.y, saved.position.z);
      }
    } finally {
      this.isRestoringSavedTransforms = false;
    }
    this.notifyPersistChange();
  }

  setPointerManager(pm: XRPointerManager): void {
    this.pointerManager = pm;
  }

  setTheme(dark: boolean): void {
    if (this.dark === dark) return;
    this.dark = dark;
    const c = SPATIAL.accentHex;
    for (const proxy of this.proxies.values()) {
      (proxy.bezelMesh.material as THREE.MeshBasicMaterial).color.setHex(c);
      if (proxy.renderer) {
        proxy.renderer.setTheme(dark);
        proxy.renderer.rerender().catch(() => {});
      }
    }
  }

  setAvatarScale(scale: number): void {
    this._avatarScale = Math.max(0.05, scale);
  }

  /** World-space avatar height (from bounding box); used with scale for proxy offsets. */
  setAnchorVisualHeight(height: number): void {
    this._anchorVisualHeight = Math.max(0.12, height);
  }

  enable(): void {
    this.active = true;
  }

  disable(): void {
    this.active = false;
    this.arGrabs.clear();
    for (const [id] of this.proxies) this.removeProxy(id);
  }

  isActive(): boolean {
    return this.active;
  }

  // ─── App Panel ──────────────────────────────────────────────────────────────

  addAppPanel(id: string, title: string, htmlContent: string, anchorPos: THREE.Vector3): void {
    if (!this.active || !this.scene) return;
    this.removeProxy(id);

    const renderer = new PanelRenderer(800, 600, true);
    renderer.setTheme(this.dark);
    const texture = renderer.getTexture();

    const { group, contentMesh, bezelMesh } = this.createPanelMesh(texture, 0.5, 0.4);
    renderer.setFrameAspect(0.5 / 0.4);
    this.positionProxy(group, anchorPos, id);
    this.scene.add(group);

    const proxy: SpatialProxy = {
      id, kind: 'app', group, contentMesh, bezelMesh, texture, renderer,
      updateInterval: null, fade: 0, worldW: 0.5, worldH: 0.4,
    };
    this.proxies.set(id, proxy);
    if (this.captureProxyTransform(proxy)) this.notifyPersistChange();
    this.renderAppContent(renderer, title, htmlContent);
  }

  updateAppPanel(id: string, title: string, htmlContent: string): void {
    const proxy = this.proxies.get(id);
    if (!proxy || proxy.kind !== 'app' || !proxy.renderer) return;
    this.renderAppContent(proxy.renderer, title, htmlContent);
  }

  private renderAppContent(renderer: PanelRenderer, title: string, htmlContent: string): void {
    renderer.render('html', htmlContent, title || undefined).catch(() => {});
  }

  // ─── YouTube ────────────────────────────────────────────────────────────────

  addYouTube(title: string, url: string, anchorPos: THREE.Vector3): void {
    if (!this.active || !this.scene) return;
    this.removeProxy('__youtube');

    const renderer = new PanelRenderer(640, 360, true);
    renderer.setTheme(this.dark);
    const texture = renderer.getTexture();

    const { group, contentMesh, bezelMesh } = this.createPanelMesh(texture, 0.62, 0.35);
    renderer.setFrameAspect(0.62 / 0.35);
    this.positionProxy(group, anchorPos, '__youtube');
    this.scene.add(group);

    const proxy: SpatialProxy = {
      id: '__youtube', kind: 'youtube', group, contentMesh, bezelMesh, texture, renderer,
      updateInterval: null, fade: 0, worldW: 0.62, worldH: 0.35,
    };
    this.proxies.set('__youtube', proxy);
    if (this.captureProxyTransform(proxy)) this.notifyPersistChange();
    this.renderYouTubeContent(renderer, title, url, true);
  }

  updateYouTube(title: string, url: string, playing: boolean): void {
    const proxy = this.proxies.get('__youtube');
    if (!proxy || !proxy.renderer) return;
    this.renderYouTubeContent(proxy.renderer, title, url, playing);
  }

  private renderYouTubeContent(renderer: PanelRenderer, title: string, url: string, playing: boolean): void {
    const icon = playing ? '\u25B6' : '\u23F8';
    const stateLabel = playing ? 'Playing' : 'Paused';
    const body = [
      `${icon}  ${stateLabel}`,
      '',
      `${title || 'YouTube'}`,
      '',
      `[Open in New Tab](${url})`,
      '',
      url,
    ].join('\n');
    renderer.render('card', body, 'YouTube').catch(() => {});
  }

  // ─── Music ──────────────────────────────────────────────────────────────────

  addMusic(
    title: string,
    url: string,
    playing: boolean,
    volume: number,
    anchorPos: THREE.Vector3,
  ): void {
    if (!this.active || !this.scene) return;
    this.removeProxy('__music');

    const renderer = new PanelRenderer(700, 420, true);
    renderer.setTheme(this.dark);
    const texture = renderer.getTexture();

    const { group, contentMesh, bezelMesh } = this.createPanelMesh(texture, 0.58, 0.34);
    renderer.setFrameAspect(0.58 / 0.34);
    this.positionProxy(group, anchorPos, '__music');
    this.scene.add(group);

    const proxy: SpatialProxy = {
      id: '__music', kind: 'music', group, contentMesh, bezelMesh, texture, renderer,
      updateInterval: null, fade: 0, worldW: 0.58, worldH: 0.34,
    };
    this.proxies.set('__music', proxy);
    if (this.captureProxyTransform(proxy)) this.notifyPersistChange();
    this.renderMusicContent(renderer, title, url, playing, volume);
  }

  updateMusic(title: string, url: string, playing: boolean, volume: number): void {
    const proxy = this.proxies.get('__music');
    if (!proxy || proxy.kind !== 'music' || !proxy.renderer) return;
    this.renderMusicContent(proxy.renderer, title, url, playing, volume);
  }

  private renderMusicContent(
    renderer: PanelRenderer,
    title: string,
    url: string,
    playing: boolean,
    volume: number,
  ): void {
    const icon = playing ? '\u25B6' : '\u23F8';
    const stateLabel = playing ? 'Playing' : 'Paused';
    const vol = Math.round(Math.max(0, Math.min(1, volume || 0)) * 100);
    const host = this.getHostLabel(url);
    const body = [
      `${icon}  ${stateLabel}`,
      '',
      `${title || 'Audio stream'}`,
      '',
      `Volume: ${vol}%`,
      `Source: ${host}`,
      '',
      url || '(no URL)',
    ].join('\n');
    renderer.render('card', body, 'Music').catch(() => {});
  }

  // ─── Terminal ───────────────────────────────────────────────────────────────

  addTerminal(getInnerHTML: () => string, anchorPos: THREE.Vector3): void {
    if (!this.active || !this.scene) return;
    this.removeProxy('__terminal');

    const renderer = new PanelRenderer(1024, 768, true);
    renderer.setTheme(this.dark);
    const texture = renderer.getTexture();

    const { group, contentMesh, bezelMesh } = this.createPanelMesh(texture, 0.62, 0.46);
    renderer.setFrameAspect(0.62 / 0.46);
    this.positionProxy(group, anchorPos, '__terminal');
    this.scene.add(group);

    const renderTerminal = () => {
      const inner = getInnerHTML();
      const text = this.extractTerminalText(inner);
      renderer.render('markdown', this.formatTerminalMarkdown(text), 'Terminal').catch(() => {});
    };

    const proxy: SpatialProxy = {
      id: '__terminal', kind: 'terminal', group, contentMesh, bezelMesh, texture, renderer,
      updateInterval: setInterval(renderTerminal, 500),
      fade: 0, worldW: 0.62, worldH: 0.46,
    };
    this.proxies.set('__terminal', proxy);
    if (this.captureProxyTransform(proxy)) this.notifyPersistChange();

    renderTerminal();
  }

  // ─── Browser ────────────────────────────────────────────────────────────────

  addBrowser(url: string, title: string | undefined, anchorPos: THREE.Vector3): void {
    if (!this.active || !this.scene) return;
    this.removeProxy('__browser');

    const renderer = new PanelRenderer(700, 500, true);
    renderer.setTheme(this.dark);
    const texture = renderer.getTexture();

    const { group, contentMesh, bezelMesh } = this.createPanelMesh(texture, 0.5, 0.38);
    renderer.setFrameAspect(0.5 / 0.38);
    this.positionProxy(group, anchorPos, '__browser');
    this.scene.add(group);

    const proxy: SpatialProxy = {
      id: '__browser', kind: 'browser', group, contentMesh, bezelMesh, texture, renderer,
      updateInterval: null, fade: 0, worldW: 0.5, worldH: 0.38,
    };
    this.proxies.set('__browser', proxy);
    if (this.captureProxyTransform(proxy)) this.notifyPersistChange();
    this.renderBrowserContent(renderer, url, title);
  }

  updateBrowserUrl(url: string, title?: string): void {
    const proxy = this.proxies.get('__browser');
    if (!proxy || !proxy.renderer) return;
    this.renderBrowserContent(proxy.renderer, url, title);
  }

  private renderBrowserContent(renderer: PanelRenderer, url: string, title?: string): void {
    const body = `${title || 'Web Page'}\n\n${url}`;
    renderer.render('card', body, 'Browser').catch(() => {});
  }

  private extractTerminalText(innerHtml: string): string {
    if (!innerHtml) return '';
    const root = document.createElement('div');
    root.innerHTML = innerHtml;
    const lines = Array.from(root.querySelectorAll('.tv-line'))
      .map((el) => (el.textContent ?? '').trimEnd())
      .filter((line) => line.length > 0);
    if (lines.length > 0) {
      const tail = lines.slice(-120);
      return tail.join('\n');
    }
    const text = (root.textContent ?? '').replace(/\r/g, '').trim();
    return text;
  }

  private formatTerminalMarkdown(text: string): string {
    const clean = (text || '').replace(/\r/g, '').trim();
    const body = clean || '# waiting for terminal output';
    const safe = body.replace(/```/g, '` ` `');
    const now = new Date().toLocaleTimeString();
    return [
      `**Live stream** · ${now}`,
      '',
      '```bash',
      safe,
      '```',
    ].join('\n');
  }

  // ─── Common ─────────────────────────────────────────────────────────────────

  removeProxy(id: string): void {
    const proxy = this.proxies.get(id);
    if (!proxy) return;
    const transformChanged = this.captureProxyTransform(proxy);
    for (const [pid, g] of [...this.arGrabs]) {
      if (g.group === proxy.group) this.arGrabs.delete(pid);
    }
    if (proxy.updateInterval) clearInterval(proxy.updateInterval);
    proxy.group.traverse((obj) => {
      if (obj instanceof THREE.Mesh) {
        obj.geometry.dispose();
        const mats = Array.isArray(obj.material) ? obj.material : [obj.material];
        for (const m of mats) m.dispose();
      }
    });
    proxy.group.removeFromParent();
    proxy.renderer?.dispose();
    this.proxies.delete(id);
    if (transformChanged) this.notifyPersistChange();
  }

  hasProxy(id: string): boolean {
    return this.proxies.has(id);
  }

  /** Hand / controller pinch drag for overlay proxies (same idea as GrabController + SpatialPanel). */
  updateAR(pointers: XRPointer[], _camera: THREE.Camera): void {
    if (!this.active || this.proxies.size === 0) return;

    const activeIds = new Set<string>();
    for (const pointer of pointers) {
      activeIds.add(pointer.id);
      const existing = this.arGrabs.get(pointer.id);

      if (pointer.isActive && !pointer.wasActive && !existing) {
        let hit: { group: THREE.Group; point: THREE.Vector3 } | null = null;
        if (pointer.type === 'hand') {
          hit = this.hitTestProxiesByProximity(pointer.position);
        }
        if (!hit) {
          this.raycaster.set(pointer.ray.origin, pointer.ray.direction);
          hit = this.hitTestProxies();
        }
        if (hit) {
          const offset = new THREE.Vector3().subVectors(hit.group.position, pointer.position);
          const rayDistance = Math.max(
            0.05,
            new THREE.Vector3().subVectors(hit.point, pointer.ray.origin).dot(pointer.ray.direction),
          );
          const hitOffset = new THREE.Vector3().subVectors(hit.group.position, hit.point);
          this.arGrabs.set(pointer.id, {
            group: hit.group,
            offset,
            pointerId: pointer.id,
            pointerType: pointer.type,
            rayDistance: pointer.type === 'controller' ? rayDistance : undefined,
            hitOffset: pointer.type === 'controller' ? hitOffset : undefined,
          });
          if (this.pointerManager && pointer.type === 'controller') {
            this.pointerManager.setRayHitPoint(pointer.id, hit.point);
          }
        }
      } else if (pointer.isActive && existing) {
        let target: THREE.Vector3;
        if (existing.pointerType === 'controller' && existing.rayDistance != null && existing.hitOffset) {
          const rayPoint = new THREE.Vector3()
            .copy(pointer.ray.direction)
            .multiplyScalar(existing.rayDistance)
            .add(pointer.ray.origin);
          target = rayPoint.add(existing.hitOffset);
        } else {
          target = new THREE.Vector3().addVectors(pointer.position, existing.offset);
        }
        existing.group.position.copy(target);
      } else if (!pointer.isActive && existing) {
        if (this.captureGroupTransform(existing.group)) this.notifyPersistChange();
        this.arGrabs.delete(pointer.id);
      }

      if (!pointer.isActive && !existing && pointer.type === 'controller' && this.pointerManager) {
        this.raycaster.set(pointer.ray.origin, pointer.ray.direction);
        const hit = this.hitTestProxies();
        if (hit) this.pointerManager.setRayHitPoint(pointer.id, hit.point);
      }
    }

    for (const id of [...this.arGrabs.keys()]) {
      if (!activeIds.has(id)) {
        const grab = this.arGrabs.get(id);
        if (grab && this.captureGroupTransform(grab.group)) this.notifyPersistChange();
        this.arGrabs.delete(id);
      }
    }
  }

  private hitTestProxies(): { group: THREE.Group; point: THREE.Vector3 } | null {
    const grabbedGroups = new Set<THREE.Group>();
    for (const g of this.arGrabs.values()) grabbedGroups.add(g.group);

    const meshes: THREE.Mesh[] = [];
    for (const p of this.proxies.values()) {
      if (grabbedGroups.has(p.group)) continue;
      meshes.push(p.contentMesh, p.bezelMesh);
    }
    if (meshes.length === 0) return null;

    const hits = this.raycaster.intersectObjects(meshes, false);
    if (hits.length === 0) return null;

    const obj = hits[0].object as THREE.Mesh;
    for (const p of this.proxies.values()) {
      if (p.contentMesh === obj || p.bezelMesh === obj) {
        return { group: p.group, point: hits[0].point.clone() };
      }
    }
    return null;
  }

  private hitTestProxiesByProximity(pos: THREE.Vector3): { group: THREE.Group; point: THREE.Vector3 } | null {
    const grabbedGroups = new Set<THREE.Group>();
    for (const g of this.arGrabs.values()) grabbedGroups.add(g.group);

    let best: { group: THREE.Group; point: THREE.Vector3; score: number } | null = null;
    for (const p of this.proxies.values()) {
      if (grabbedGroups.has(p.group)) continue;

      const hw = p.worldW / 2;
      const hh = p.worldH / 2;
      const local = p.group.worldToLocal(pos.clone());
      if (Math.abs(local.z) > HAND_GRAB_DEPTH) continue;
      if (Math.abs(local.x) > hw + HAND_GRAB_SURFACE_MARGIN) continue;
      if (Math.abs(local.y) > hh + HAND_GRAB_SURFACE_MARGIN) continue;

      const cx = THREE.MathUtils.clamp(local.x, -hw, hw);
      const cy = THREE.MathUtils.clamp(local.y, -hh, hh);
      const surfacePoint = p.group.localToWorld(new THREE.Vector3(cx, cy, 0));
      const score = pos.distanceToSquared(surfacePoint);
      if (!best || score < best.score) {
        best = { group: p.group, point: surfacePoint, score };
      }
    }

    return best ? { group: best.group, point: best.point } : null;
  }

  update(_dt: number, camera: THREE.Camera): void {
    if (!this.active) return;
    const grabbedGroups = new Set<THREE.Group>();
    for (const g of this.arGrabs.values()) grabbedGroups.add(g.group);

    for (const proxy of this.proxies.values()) {
      if (!grabbedGroups.has(proxy.group)) {
        proxy.group.lookAt(camera.position);
      }
      if (proxy.fade < 1) {
        proxy.fade = Math.min(1, proxy.fade + FADE_PER_FRAME);
        const a = proxy.fade;
        (proxy.contentMesh.material as THREE.MeshBasicMaterial).opacity = a;
        (proxy.bezelMesh.material as THREE.MeshBasicMaterial).opacity = a * 0.92;
      }
      // WebXR: canvas→GPU uploads can be skipped unless flagged each frame.
      proxy.texture.needsUpdate = true;
    }
  }

  private createPanelMesh(
    texture: THREE.Texture,
    w: number,
    h: number,
  ): { group: THREE.Group; contentMesh: THREE.Mesh; bezelMesh: THREE.Mesh } {
    const group = new THREE.Group();
    group.renderOrder = 900;

    const contentGeo = createRoundedPlane(w, h, 0.015);
    const contentMat = new THREE.MeshBasicMaterial({
      map: texture,
      transparent: true,
      opacity: 0,
      side: THREE.DoubleSide,
      depthWrite: false,
      toneMapped: false,
    });
    const contentMesh = new THREE.Mesh(contentGeo, contentMat);
    contentMesh.renderOrder = 902;

    const bezelGeo = createRoundedPlane(w + BEZEL_PAD * 2, h + BEZEL_PAD * 2, 0.02);
    const bezelMat = new THREE.MeshBasicMaterial({
      color: SPATIAL.accentHex,
      transparent: true,
      opacity: 0,
      side: THREE.DoubleSide,
      depthWrite: false,
      toneMapped: false,
    });
    const bezelMesh = new THREE.Mesh(bezelGeo, bezelMat);
    bezelMesh.position.z = -BEZEL_DEPTH;
    bezelMesh.renderOrder = 901;

    group.add(bezelMesh);
    group.add(contentMesh);

    return { group, contentMesh, bezelMesh };
  }

  private positionProxy(group: THREE.Group, anchorPos: THREE.Vector3, id: string): void {
    const saved = this.savedTransforms.get(id);
    if (saved) {
      group.position.set(saved.position.x, saved.position.y, saved.position.z);
      return;
    }

    const vh = this._anchorVisualHeight;
    const sc = this._avatarScale;
    const index = this.proxies.size;
    const angle = (index * 0.6) - 0.3;
    // Y from world visual height; horizontal depth scales with user resize (group scale).
    group.position.set(
      anchorPos.x + Math.sin(angle) * 0.9 * sc,
      anchorPos.y + PROXY_Y_FRAC * vh,
      anchorPos.z - Math.cos(angle) * 0.5 * sc,
    );
  }

  private captureProxyTransform(proxy: SpatialProxy): boolean {
    const position = proxy.group.position;
    const previous = this.savedTransforms.get(proxy.id)?.position;
    if (
      previous
      && Math.abs(previous.x - position.x) < 1e-6
      && Math.abs(previous.y - position.y) < 1e-6
      && Math.abs(previous.z - position.z) < 1e-6
    ) {
      return false;
    }

    this.savedTransforms.set(proxy.id, {
      position: { x: position.x, y: position.y, z: position.z },
    });
    return true;
  }

  private captureGroupTransform(group: THREE.Group): boolean {
    for (const proxy of this.proxies.values()) {
      if (proxy.group === group) return this.captureProxyTransform(proxy);
    }
    return false;
  }

  private readSavedPosition(raw: unknown): { x: number; y: number; z: number } | null {
    if (!raw || typeof raw !== 'object') return null;
    const record = raw as Record<string, unknown>;
    // Accept the public nested format and a legacy flat vector defensively.
    const candidate = record.position && typeof record.position === 'object'
      ? record.position as Record<string, unknown>
      : record;
    if (![candidate.x, candidate.y, candidate.z].every((value) => (
      typeof value === 'number' && Number.isFinite(value)
    ))) return null;
    return { x: candidate.x as number, y: candidate.y as number, z: candidate.z as number };
  }

  private notifyPersistChange(): void {
    if (!this.isRestoringSavedTransforms) this.onPersistChange?.();
  }

  private getHostLabel(url: string): string {
    if (!url) return 'unknown';
    try {
      const host = new URL(url).hostname.toLowerCase();
      return host.replace(/^www\./, '');
    } catch {
      return 'custom source';
    }
  }
}

function escapeHTML(str: string): string {
  return str.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}
