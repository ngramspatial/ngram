// @ts-nocheck
import * as THREE from 'three';
import type { AnnotationStyle } from '@ngram-ar/core';

const FADE_IN_DURATION = 0.3;
const DEFAULT_LINE_COLOR = '#00d4ff';
const DEFAULT_LINE_WIDTH = 0.008;
const ARROW_HEAD_LENGTH = 0.06;
const ARROW_HEAD_RADIUS = 0.02;
const ANNOTATION_CANVAS_W = 512;
const ANNOTATION_CANVAS_H = 128;
const ANNOTATION_WORLD_W = 0.35;
const LEADER_LINE_OFFSET_Y = 0.12;

export type DrawingKind = 'line' | 'arrow' | 'annotation';

export interface SavedDrawing {
  id: string;
  kind: DrawingKind;
  params: Record<string, unknown>;
}

interface Drawing {
  id: string;
  kind: DrawingKind;
  group: THREE.Group;
  /** Sprites that should billboard toward camera */
  billboards: THREE.Object3D[];
  spawnTime: number;
  opacity: number;
  targetOpacity: number;
}

function makeTubeLine(
  points: THREE.Vector3[],
  color: string,
  width: number,
): THREE.Mesh {
  const curve = new THREE.CatmullRomCurve3(points, false, 'centripetal');
  const segments = Math.max(points.length * 8, 16);
  const geo = new THREE.TubeGeometry(curve, segments, width / 2, 8, false);
  const mat = new THREE.MeshBasicMaterial({
    color: new THREE.Color(color),
    transparent: true,
    opacity: 0,
  });
  return new THREE.Mesh(geo, mat);
}

function makeArrowHead(
  direction: THREE.Vector3,
  position: THREE.Vector3,
  color: string,
): THREE.Mesh {
  const geo = new THREE.ConeGeometry(ARROW_HEAD_RADIUS, ARROW_HEAD_LENGTH, 12);
  const mat = new THREE.MeshBasicMaterial({
    color: new THREE.Color(color),
    transparent: true,
    opacity: 0,
  });
  const mesh = new THREE.Mesh(geo, mat);
  mesh.position.copy(position);

  const up = new THREE.Vector3(0, 1, 0);
  const quat = new THREE.Quaternion().setFromUnitVectors(up, direction.clone().normalize());
  mesh.quaternion.copy(quat);

  return mesh;
}

function makeAnnotationSprite(
  text: string,
  color: string,
  style: AnnotationStyle,
): { sprite: THREE.Sprite; height: number } {
  const canvas = document.createElement('canvas');
  canvas.width = ANNOTATION_CANVAS_W;
  canvas.height = ANNOTATION_CANVAS_H;
  const ctx = canvas.getContext('2d')!;

  const fontSize = 22;
  ctx.font = `500 ${fontSize}px -apple-system, "Helvetica Neue", system-ui, sans-serif`;

  const pad = 16;
  const textW = ctx.measureText(text).width;
  const pillW = Math.min(textW + pad * 2, canvas.width - 8);
  const pillH = fontSize + pad * 1.5;
  const x = (canvas.width - pillW) / 2;
  const y = (canvas.height - pillH) / 2;

  ctx.beginPath();
  ctx.roundRect(x, y, pillW, pillH, pillH / 2);
  ctx.fillStyle = 'rgba(10, 10, 12, 0.82)';
  ctx.fill();
  ctx.strokeStyle = color;
  ctx.lineWidth = 1.5;
  ctx.stroke();

  if (style === 'pin') {
    const cx = canvas.width / 2;
    const pinY = y + pillH + 4;
    ctx.beginPath();
    ctx.arc(cx, pinY + 6, 4, 0, Math.PI * 2);
    ctx.fillStyle = color;
    ctx.fill();
  }

  ctx.fillStyle = '#ffffff';
  ctx.textAlign = 'center';
  ctx.textBaseline = 'middle';
  ctx.fillText(text, canvas.width / 2, canvas.height / 2);

  const tex = new THREE.CanvasTexture(canvas);
  tex.minFilter = THREE.LinearFilter;
  const mat = new THREE.SpriteMaterial({
    map: tex,
    transparent: true,
    opacity: 0,
    depthTest: false,
  });
  const sprite = new THREE.Sprite(mat);
  const aspect = canvas.width / canvas.height;
  const worldH = ANNOTATION_WORLD_W / aspect;
  sprite.scale.set(ANNOTATION_WORLD_W, worldH, 1);

  return { sprite, height: worldH };
}

function makeLeaderLine(from: THREE.Vector3, to: THREE.Vector3, color: string): THREE.Line {
  const geo = new THREE.BufferGeometry().setFromPoints([from, to]);
  const mat = new THREE.LineBasicMaterial({
    color: new THREE.Color(color),
    transparent: true,
    opacity: 0,
  });
  return new THREE.Line(geo, mat);
}

export class DrawingManager {
  private drawings = new Map<string, Drawing>();
  private savedDrawings = new Map<string, SavedDrawing>();
  private scene: THREE.Scene | null = null;
  private isRestoringSavedState = false;

  /** Called whenever durable drawing state changes. */
  onPersistChange: (() => void) | null = null;

  attach(scene: THREE.Scene): void {
    this.scene = scene;
  }

  drawLine(
    drawingId: string,
    points: Array<{ x: number; y: number; z: number }>,
    options: { color?: string; width?: number } = {},
  ): void {
    if (!this.scene || points.length < 2) return;
    this.removeInternal(drawingId);

    const color = options.color ?? DEFAULT_LINE_COLOR;
    const width = options.width ?? DEFAULT_LINE_WIDTH;
    const pts = points.map(p => new THREE.Vector3(p.x, p.y, p.z));

    const tube = makeTubeLine(pts, color, width);
    const group = new THREE.Group();
    group.add(tube);

    this.scene.add(group);
    this.drawings.set(drawingId, {
      id: drawingId,
      kind: 'line',
      group,
      billboards: [],
      spawnTime: performance.now(),
      opacity: 0,
      targetOpacity: 1,
    });
    this.savedDrawings.set(drawingId, {
      id: drawingId,
      kind: 'line',
      params: {
        points: points.map(point => ({ x: point.x, y: point.y, z: point.z })),
        color,
        width,
      },
    });
    this.notifyPersistChange();
  }

  drawArrow(
    drawingId: string,
    from: THREE.Vector3,
    to: THREE.Vector3,
    options: { color?: string; label?: string } = {},
  ): void {
    if (!this.scene) return;
    const replacedExisting = this.removeInternal(drawingId);

    const color = options.color ?? DEFAULT_LINE_COLOR;
    const direction = new THREE.Vector3().subVectors(to, from);
    const len = direction.length();
    if (len < 0.01) {
      if (replacedExisting) this.notifyPersistChange();
      return;
    }

    const lineEnd = to.clone().addScaledVector(direction.normalize(), -ARROW_HEAD_LENGTH);
    const tube = makeTubeLine([from, lineEnd], color, DEFAULT_LINE_WIDTH);
    const arrowHead = makeArrowHead(direction, to, color);

    const group = new THREE.Group();
    group.add(tube);
    group.add(arrowHead);

    const billboards: THREE.Object3D[] = [];

    if (options.label) {
      const midpoint = new THREE.Vector3().lerpVectors(from, to, 0.5);
      midpoint.y += 0.06;
      const { sprite } = makeAnnotationSprite(options.label, color, 'label');
      sprite.position.copy(midpoint);
      group.add(sprite);
      billboards.push(sprite);
    }

    this.scene.add(group);
    this.drawings.set(drawingId, {
      id: drawingId,
      kind: 'arrow',
      group,
      billboards,
      spawnTime: performance.now(),
      opacity: 0,
      targetOpacity: 1,
    });
    this.savedDrawings.set(drawingId, {
      id: drawingId,
      kind: 'arrow',
      params: {
        from: { x: from.x, y: from.y, z: from.z },
        to: { x: to.x, y: to.y, z: to.z },
        color,
        label: options.label,
      },
    });
    this.notifyPersistChange();
  }

  drawAnnotation(
    drawingId: string,
    position: { x: number; y: number; z: number },
    text: string,
    options: { color?: string; style?: AnnotationStyle } = {},
  ): void {
    if (!this.scene) return;
    this.removeInternal(drawingId);

    const color = options.color ?? DEFAULT_LINE_COLOR;
    const style = options.style ?? 'label';
    const anchor = new THREE.Vector3(position.x, position.y, position.z);

    const { sprite, height } = makeAnnotationSprite(text, color, style);
    const group = new THREE.Group();
    const billboards: THREE.Object3D[] = [sprite];

    if (style === 'callout') {
      const labelPos = anchor.clone();
      labelPos.y += LEADER_LINE_OFFSET_Y + height / 2;
      sprite.position.copy(labelPos);
      const line = makeLeaderLine(anchor, new THREE.Vector3(anchor.x, anchor.y + LEADER_LINE_OFFSET_Y, anchor.z), color);
      group.add(line);
    } else if (style === 'pin') {
      sprite.position.copy(anchor);
      sprite.position.y += height / 2 + 0.02;
    } else {
      sprite.position.copy(anchor);
    }

    group.add(sprite);
    this.scene.add(group);
    this.drawings.set(drawingId, {
      id: drawingId,
      kind: 'annotation',
      group,
      billboards,
      spawnTime: performance.now(),
      opacity: 0,
      targetOpacity: 1,
    });
    this.savedDrawings.set(drawingId, {
      id: drawingId,
      kind: 'annotation',
      params: {
        position: { x: position.x, y: position.y, z: position.z },
        text,
        color,
        style,
      },
    });
    this.notifyPersistChange();
  }

  remove(drawingId: string): void {
    if (this.removeInternal(drawingId)) this.notifyPersistChange();
  }

  private removeInternal(drawingId: string): boolean {
    const entry = this.drawings.get(drawingId);
    const hadSavedEntry = this.savedDrawings.has(drawingId);
    if (entry) {
      this.disposeEntry(entry);
      this.drawings.delete(drawingId);
    }
    this.savedDrawings.delete(drawingId);
    return Boolean(entry || hadSavedEntry);
  }

  clearAll(): void {
    const changed = this.drawings.size > 0 || this.savedDrawings.size > 0;
    for (const entry of this.drawings.values()) {
      this.disposeEntry(entry);
    }
    this.drawings.clear();
    this.savedDrawings.clear();
    if (changed) this.notifyPersistChange();
  }

  update(dt: number, camera: THREE.Camera): void {
    for (const entry of this.drawings.values()) {
      // Fade animation
      const fadeSpeed = 1 / FADE_IN_DURATION;
      if (entry.opacity < entry.targetOpacity) {
        entry.opacity = Math.min(entry.opacity + fadeSpeed * dt, entry.targetOpacity);
      } else if (entry.opacity > entry.targetOpacity) {
        entry.opacity = Math.max(entry.opacity - fadeSpeed * dt, entry.targetOpacity);
      }

      // Apply opacity to all materials in the group
      entry.group.traverse((child) => {
        if (child instanceof THREE.Mesh || child instanceof THREE.Line) {
          const mat = child.material as THREE.Material & { opacity: number };
          mat.opacity = entry.opacity;
        }
        if (child instanceof THREE.Sprite) {
          (child.material as THREE.SpriteMaterial).opacity = entry.opacity;
        }
      });

      // Billboard labels toward camera
      for (const bb of entry.billboards) {
        bb.quaternion.copy(camera.quaternion);
      }
    }
  }

  getSavedState(): SavedDrawing[] {
    return Array.from(this.savedDrawings.values(), item => ({
      id: item.id,
      kind: item.kind,
      params: this.cloneParams(item.params),
    }));
  }

  loadSavedState(items: SavedDrawing[]): void {
    this.isRestoringSavedState = true;
    try {
      for (const item of items) {
        if (!item || typeof item.id !== 'string' || !item.params) continue;
        const p = item.params as any;
        switch (item.kind) {
          case 'line': {
            const points = Array.isArray(p.points)
              ? p.points.map((point: unknown) => this.readVector3(point)).filter(Boolean)
              : [];
            if (points.length >= 2) {
              this.drawLine(item.id, points, { color: p.color, width: p.width });
            }
            break;
          }
          case 'arrow': {
            const from = this.readVector3(p.from);
            const to = this.readVector3(p.to);
            if (from && to) {
              this.drawArrow(
                item.id,
                new THREE.Vector3(from.x, from.y, from.z),
                new THREE.Vector3(to.x, to.y, to.z),
                { color: p.color, label: p.label },
              );
            }
            break;
          }
          case 'annotation': {
            const position = this.readVector3(p.position);
            if (position) {
              this.drawAnnotation(item.id, position, p.text ?? '', {
                color: p.color,
                style: p.style,
              });
            }
            break;
          }
        }
      }
    } finally {
      this.isRestoringSavedState = false;
    }
    this.notifyPersistChange();
  }

  private notifyPersistChange(): void {
    if (!this.isRestoringSavedState) this.onPersistChange?.();
  }

  private readVector3(value: unknown): { x: number; y: number; z: number } | null {
    if (Array.isArray(value) && value.length >= 3
      && value.slice(0, 3).every(n => typeof n === 'number' && Number.isFinite(n))) {
      return { x: value[0], y: value[1], z: value[2] };
    }
    if (!value || typeof value !== 'object') return null;
    const point = value as Record<string, unknown>;
    if (![point.x, point.y, point.z].every(n => typeof n === 'number' && Number.isFinite(n))) return null;
    return { x: point.x as number, y: point.y as number, z: point.z as number };
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

  private disposeEntry(entry: Drawing): void {
    entry.group.traverse((child) => {
      if (child instanceof THREE.Mesh || child instanceof THREE.Line) {
        child.geometry.dispose();
        if (Array.isArray(child.material)) {
          child.material.forEach(m => m.dispose());
        } else {
          (child.material as THREE.Material).dispose();
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
