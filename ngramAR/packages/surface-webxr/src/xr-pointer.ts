// @ts-nocheck
import * as THREE from 'three';

const PINCH_THRESHOLD = 0.035;
const RAY_MAX_LENGTH = 5.0;
const RAY_COLOR = 0x6e7dff;
const RAY_OPACITY = 0.4;
const RETICLE_RADIUS = 0.006;
const RETICLE_COLOR = 0xffffff;
const RETICLE_OPACITY = 0.8;

export interface XRPointer {
  id: string;
  handedness: 'left' | 'right';
  type: 'hand' | 'controller';
  ray: THREE.Ray;
  isActive: boolean;
  wasActive: boolean;
  pinchDistance?: number;
  position: THREE.Vector3;
  quaternion: THREE.Quaternion;
  actionActive?: boolean;
  actionWasActive?: boolean;
}

interface PointerState {
  wasActive: boolean;
  actionWasActive?: boolean;
}

interface RayVisual {
  line: THREE.Line;
  dot: THREE.Mesh;
  dotMat: THREE.MeshBasicMaterial;
  lineMat: THREE.LineBasicMaterial;
}

export class XRPointerManager {
  private scene: THREE.Scene | null = null;
  private pointers: XRPointer[] = [];
  private prevState = new Map<string, PointerState>();
  private rayVisuals = new Map<string, RayVisual>();

  private tmpVec = new THREE.Vector3();
  private tmpVec2 = new THREE.Vector3();
  private tmpVec3 = new THREE.Vector3();
  private tmpMatrix = new THREE.Matrix4();

  attach(scene: THREE.Scene): void {
    this.scene = scene;
  }

  getPointers(): XRPointer[] {
    return this.pointers;
  }

  update(frame: XRFrame, refSpace: XRReferenceSpace): void {
    const session = frame.session;
    const newPointers: XRPointer[] = [];

    for (const source of session.inputSources) {
      if (source.hand) {
        const pointer = this.buildHandPointer(source, frame, refSpace);
        if (pointer) newPointers.push(pointer);
      } else if (source.gamepad && source.targetRaySpace) {
        const pointer = this.buildControllerPointer(source, frame, refSpace);
        if (pointer) newPointers.push(pointer);
      }
    }

    this.pointers = newPointers;

    const activeIds = new Set<string>();
    for (const p of newPointers) {
      activeIds.add(p.id);
    }
    for (const id of this.prevState.keys()) {
      if (!activeIds.has(id)) this.prevState.delete(id);
    }

    for (const p of newPointers) {
      this.prevState.set(p.id, { wasActive: p.isActive, actionWasActive: p.actionActive });
    }

    this.updateRayVisuals();
  }

  private buildHandPointer(
    source: XRInputSource,
    frame: XRFrame,
    refSpace: XRReferenceSpace,
  ): XRPointer | null {
    const hand = source.hand!;
    const handedness = source.handedness as 'left' | 'right';
    if (handedness !== 'left' && handedness !== 'right') return null;

    const indexTip = hand.get('index-finger-tip');
    const thumbTip = hand.get('thumb-tip');
    const wrist = hand.get('wrist');
    if (!indexTip || !thumbTip) return null;

    const iPose = frame.getJointPose?.(indexTip, refSpace);
    const tPose = frame.getJointPose?.(thumbTip, refSpace);
    if (!iPose || !tPose) return null;

    const indexPos = this.tmpVec.set(
      iPose.transform.position.x,
      iPose.transform.position.y,
      iPose.transform.position.z,
    );
    const thumbPos = this.tmpVec2.set(
      tPose.transform.position.x,
      tPose.transform.position.y,
      tPose.transform.position.z,
    );

    const pinchDistance = indexPos.distanceTo(thumbPos);
    const isActive = pinchDistance < PINCH_THRESHOLD;

    const pinchMid = new THREE.Vector3()
      .addVectors(indexPos, thumbPos)
      .multiplyScalar(0.5);

    let rayOrigin: THREE.Vector3;
    if (wrist) {
      const wPose = frame.getJointPose?.(wrist, refSpace);
      if (wPose) {
        rayOrigin = new THREE.Vector3(
          wPose.transform.position.x,
          wPose.transform.position.y,
          wPose.transform.position.z,
        );
      } else {
        rayOrigin = pinchMid.clone();
      }
    } else {
      rayOrigin = pinchMid.clone();
    }

    const rayDir = new THREE.Vector3()
      .subVectors(pinchMid, rayOrigin)
      .normalize();

    const id = `hand-${handedness}`;
    const prev = this.prevState.get(id);

    return {
      id,
      handedness,
      type: 'hand',
      ray: new THREE.Ray(rayOrigin, rayDir),
      isActive,
      wasActive: prev?.wasActive ?? false,
      pinchDistance,
      position: pinchMid.clone(),
      quaternion: new THREE.Quaternion().setFromUnitVectors(new THREE.Vector3(0, 0, -1), rayDir),
    };
  }

  private buildControllerPointer(
    source: XRInputSource,
    frame: XRFrame,
    refSpace: XRReferenceSpace,
  ): XRPointer | null {
    const handedness = source.handedness as 'left' | 'right';
    if (handedness !== 'left' && handedness !== 'right') return null;

    const pose = frame.getPose(source.targetRaySpace!, refSpace);
    if (!pose) return null;

    const p = pose.transform.position;
    const origin = new THREE.Vector3(p.x, p.y, p.z);

    this.tmpMatrix.fromArray(pose.transform.matrix);
    const direction = this.tmpVec3.set(0, 0, -1).applyMatrix4(this.tmpMatrix).sub(origin).normalize();

    const buttons = source.gamepad!.buttons;
    const isActive = buttons[0]?.pressed ?? false;

    const id = `controller-${handedness}`;
    const prev = this.prevState.get(id);
    const gripPose = source.gripSpace ? frame.getPose(source.gripSpace, refSpace) : pose;
    const orientation = (gripPose ?? pose).transform.orientation;
    const gripPosition = (gripPose ?? pose).transform.position;

    return {
      id,
      handedness,
      type: 'controller',
      ray: new THREE.Ray(origin.clone(), direction.clone()),
      isActive,
      wasActive: prev?.wasActive ?? false,
      position: new THREE.Vector3(gripPosition.x, gripPosition.y, gripPosition.z),
      quaternion: new THREE.Quaternion(orientation.x, orientation.y, orientation.z, orientation.w),
      actionActive: buttons[1]?.pressed ?? false,
      actionWasActive: prev?.actionWasActive ?? false,
    };
  }

  private updateRayVisuals(): void {
    if (!this.scene) return;

    const activeIds = new Set<string>();

    for (const pointer of this.pointers) {
      if (pointer.type !== 'controller') continue;
      activeIds.add(pointer.id);

      let visual = this.rayVisuals.get(pointer.id);
      if (!visual) {
        visual = this.createRayVisual();
        this.rayVisuals.set(pointer.id, visual);
        this.scene.add(visual.line);
        this.scene.add(visual.dot);
      }

      const endPoint = new THREE.Vector3()
        .copy(pointer.ray.direction)
        .multiplyScalar(RAY_MAX_LENGTH)
        .add(pointer.ray.origin);

      const positions = visual.line.geometry.getAttribute('position') as THREE.BufferAttribute;
      positions.setXYZ(0, pointer.ray.origin.x, pointer.ray.origin.y, pointer.ray.origin.z);
      positions.setXYZ(1, endPoint.x, endPoint.y, endPoint.z);
      positions.needsUpdate = true;

      visual.dot.position.copy(endPoint);
      visual.dot.visible = false;

      visual.line.visible = true;
      visual.lineMat.opacity = pointer.isActive ? 0.7 : RAY_OPACITY;
    }

    for (const [id, visual] of this.rayVisuals) {
      if (!activeIds.has(id)) {
        visual.line.visible = false;
        visual.dot.visible = false;
      }
    }
  }

  setRayHitPoint(pointerId: string, point: THREE.Vector3): void {
    const visual = this.rayVisuals.get(pointerId);
    if (!visual) return;

    visual.dot.position.copy(point);
    visual.dot.visible = true;

    const pointer = this.pointers.find(p => p.id === pointerId);
    if (pointer) {
      const positions = visual.line.geometry.getAttribute('position') as THREE.BufferAttribute;
      positions.setXYZ(1, point.x, point.y, point.z);
      positions.needsUpdate = true;
    }
  }

  private createRayVisual(): RayVisual {
    const geo = new THREE.BufferGeometry();
    geo.setAttribute('position', new THREE.Float32BufferAttribute([0, 0, 0, 0, 0, -1], 3));

    const lineMat = new THREE.LineBasicMaterial({
      color: RAY_COLOR,
      transparent: true,
      opacity: RAY_OPACITY,
      depthTest: false,
    });
    const line = new THREE.Line(geo, lineMat);
    line.renderOrder = 999;
    line.frustumCulled = false;

    const dotGeo = new THREE.SphereGeometry(RETICLE_RADIUS, 12, 8);
    const dotMat = new THREE.MeshBasicMaterial({
      color: RETICLE_COLOR,
      transparent: true,
      opacity: RETICLE_OPACITY,
      depthTest: false,
    });
    const dot = new THREE.Mesh(dotGeo, dotMat);
    dot.renderOrder = 999;
    dot.visible = false;

    return { line, dot, dotMat, lineMat };
  }

  dispose(): void {
    for (const visual of this.rayVisuals.values()) {
      visual.line.geometry.dispose();
      visual.lineMat.dispose();
      visual.dot.geometry.dispose();
      visual.dotMat.dispose();
      visual.line.removeFromParent();
      visual.dot.removeFromParent();
    }
    this.rayVisuals.clear();
    this.prevState.clear();
    this.pointers = [];
  }
}
