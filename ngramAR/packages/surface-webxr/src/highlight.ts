// @ts-nocheck
import * as THREE from 'three';
import { CSS2DObject } from 'three/addons/renderers/CSS2DRenderer.js';

interface ActiveHighlight {
  obj: CSS2DObject;
  el: HTMLDivElement;
  mesh: THREE.Mesh;
  timer: ReturnType<typeof setTimeout>;
}

export class HighlightManager {
  private highlights = new Map<string, ActiveHighlight>();
  private scene: THREE.Scene | null = null;
  private idCounter = 0;

  attach(scene: THREE.Scene): void {
    this.scene = scene;
  }

  show(
    target: { x: number; y: number; z: number } | string,
    color?: string,
    duration?: number,
  ): string {
    if (!this.scene) return '';
    const id = `hl-${++this.idCounter}`;
    const pos = typeof target === 'object'
      ? new THREE.Vector3(target.x, target.y, target.z)
      : new THREE.Vector3(0, 1, -1);

    const parsedColor = new THREE.Color(color ?? '#00d4ff');

    const ringGeo = new THREE.TorusGeometry(0.15, 0.015, 8, 32);
    const ringMat = new THREE.MeshBasicMaterial({
      color: parsedColor,
      transparent: true,
      opacity: 0.8,
    });
    const mesh = new THREE.Mesh(ringGeo, ringMat);
    mesh.position.copy(pos);
    mesh.rotation.x = -Math.PI / 2;
    this.scene.add(mesh);

    const el = document.createElement('div');
    el.className = 'highlight-marker';
    el.style.boxShadow = `0 0 20px ${color ?? '#00d4ff'}, 0 0 40px ${color ?? '#00d4ff'}`;
    const cssObj = new CSS2DObject(el);
    cssObj.position.copy(pos);
    cssObj.position.y += 0.2;
    this.scene.add(cssObj);

    const dur = duration ?? 5;
    const timer = setTimeout(() => this.hide(id), dur * 1000);

    this.highlights.set(id, { obj: cssObj, el, mesh, timer });
    return id;
  }

  hide(id: string): void {
    const entry = this.highlights.get(id);
    if (!entry || !this.scene) return;
    clearTimeout(entry.timer);
    this.scene.remove(entry.obj);
    this.scene.remove(entry.mesh);
    entry.mesh.geometry.dispose();
    (entry.mesh.material as THREE.Material).dispose();
    this.highlights.delete(id);
  }

  update(dt: number, elapsed: number): void {
    for (const [, h] of this.highlights) {
      h.mesh.rotation.z = elapsed * 2;
      const scale = 1 + Math.sin(elapsed * 4) * 0.15;
      h.mesh.scale.setScalar(scale);
    }
  }

  hideAll(): void {
    for (const [id] of this.highlights) {
      this.hide(id);
    }
  }
}
