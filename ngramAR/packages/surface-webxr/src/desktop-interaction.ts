// @ts-nocheck
/**
 * Desktop mouse interaction layer for the 3D viewport.
 *
 * - Hover scene objects → emissive highlight
 * - Right-drag remains exclusively available to camera pan controls
 */

import * as THREE from 'three';
import type { SceneObjectManager } from './scene-object-manager.js';

const HIGHLIGHT_EMISSIVE = new THREE.Color(0x4488ff);
const HIGHLIGHT_INTENSITY = 0.35;

export class DesktopInteraction {
  private renderer: THREE.WebGLRenderer;
  private camera: THREE.Camera;
  private sceneObjects: SceneObjectManager;
  private raycaster = new THREE.Raycaster();
  private mouseNDC = new THREE.Vector2();
  // Hover highlight state
  private hoveredMesh: THREE.Mesh | null = null;
  private hoveredOriginalEmissive: THREE.Color | null = null;
  private hoveredOriginalIntensity = 0;

  constructor(
    renderer: THREE.WebGLRenderer,
    camera: THREE.Camera,
    sceneObjects: SceneObjectManager,
  ) {
    this.renderer = renderer;
    this.camera = camera;
    this.sceneObjects = sceneObjects;
    const el = renderer.domElement;
    el.addEventListener('pointermove', this.onPointerMove);
  }

  dispose(): void {
    const el = this.renderer.domElement;
    el.removeEventListener('pointermove', this.onPointerMove);
    this.clearHighlight();
  }

  // ─── NDC helpers ──────────────────────────────────────────────────────

  private updateNDC(e: MouseEvent | PointerEvent): void {
    const rect = this.renderer.domElement.getBoundingClientRect();
    this.mouseNDC.x = ((e.clientX - rect.left) / rect.width) * 2 - 1;
    this.mouseNDC.y = -((e.clientY - rect.top) / rect.height) * 2 + 1;
  }

  // ─── Hover highlight ──────────────────────────────────────────────────

  private onPointerMove = (e: PointerEvent): void => {
    this.updateNDC(e);
    this.raycaster.setFromCamera(this.mouseNDC, this.camera);

    // Check scene objects for hover highlight
    const draggables = (this.sceneObjects as any).getDraggableMeshes?.() as
      Array<{ entry: { mesh: THREE.Mesh }; mesh: THREE.Mesh }> | undefined;

    if (!draggables || draggables.length === 0) {
      this.clearHighlight();
      return;
    }

    const hits = this.raycaster.intersectObjects(draggables.map(d => d.mesh), false);
    if (hits.length > 0) {
      const hitMesh = hits[0].object as THREE.Mesh;
      if (hitMesh !== this.hoveredMesh) {
        this.clearHighlight();
        this.applyHighlight(hitMesh);
      }
    } else {
      this.clearHighlight();
    }
  };

  private applyHighlight(mesh: THREE.Mesh): void {
    this.hoveredMesh = mesh;
    const mat = mesh.material as THREE.MeshStandardMaterial;
    if (!mat || !('emissive' in mat)) return;
    this.hoveredOriginalEmissive = mat.emissive.clone();
    this.hoveredOriginalIntensity = mat.emissiveIntensity;
    mat.emissive.copy(HIGHLIGHT_EMISSIVE);
    mat.emissiveIntensity = HIGHLIGHT_INTENSITY;
  }

  private clearHighlight(): void {
    if (!this.hoveredMesh) return;
    const mat = this.hoveredMesh.material as THREE.MeshStandardMaterial;
    if (mat && 'emissive' in mat && this.hoveredOriginalEmissive) {
      mat.emissive.copy(this.hoveredOriginalEmissive);
      mat.emissiveIntensity = this.hoveredOriginalIntensity;
    }
    this.hoveredMesh = null;
    this.hoveredOriginalEmissive = null;
  }

}
