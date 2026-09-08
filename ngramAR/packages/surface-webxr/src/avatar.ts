// @ts-nocheck
import * as THREE from 'three';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import { FBXLoader } from 'three/addons/loaders/FBXLoader.js';
import { makeLocomotionClipInPlace } from './locomotion.js';

// ─── Shell Config (received from server) ────────────────────────────────────

export interface ShellConfig {
  model: string;
  scale?: number;
  animationPack: string | Record<string, string>;
  shellSlug?: string;
}

export interface ClipInfo {
  name: string;
  duration: number;
  isLooping: boolean;
}

export interface BehaviorOutputData {
  gazeTarget?: { x: number; y: number; z: number } | null;
  moveTarget?: { x: number; y: number; z: number } | null;
  animation?: string;
  expression?: string;
  expressionIntensity?: number;
}

function isFBX(path: string): boolean {
  return path.split(/[?#]/)[0].toLowerCase().endsWith('.fbx');
}

function isFilePath(value: string): boolean {
  return /\.(fbx|glb|gltf)$/i.test(value);
}

function mixamoBoneKey(name: string): string {
  const leaf = name.split(/[|:]/).pop() ?? name;
  return leaf
    .replace(/^mixamorig\d*/i, '')
    .replace(/[^a-z0-9]/gi, '')
    .toLowerCase();
}

function retargetMixamoClip(
  source: THREE.AnimationClip,
  model: THREE.Object3D,
): { clip: THREE.AnimationClip; remapped: number; unresolved: string[] } {
  const targetBones = new Map<string, string>();
  model.traverse((node) => {
    if ((node as THREE.Bone).isBone) {
      const key = mixamoBoneKey(node.name);
      if (key && !targetBones.has(key)) targetBones.set(key, node.name);
    }
  });

  const clip = source.clone();
  const unresolved = new Set<string>();
  let remapped = 0;

  for (const track of clip.tracks) {
    const propertyDot = track.name.lastIndexOf('.');
    if (propertyDot <= 0) continue;

    const sourceNode = track.name.slice(0, propertyDot);
    if (model.getObjectByName(sourceNode)) continue;

    const targetNode = targetBones.get(mixamoBoneKey(sourceNode));
    if (targetNode) {
      track.name = `${targetNode}${track.name.slice(propertyDot)}`;
      remapped += 1;
    } else {
      unresolved.add(sourceNode);
    }
  }

  return { clip, remapped, unresolved: [...unresolved] };
}

// ─── Public interface both avatar backends implement ─────────────────────────

type WalkCallback = () => void;

interface AvatarBackend {
  spawn(scene: THREE.Scene, position: THREE.Vector3): Promise<void>;
  update(dt: number): void;
  setAnimation(state: string): void;
  setGazeTarget(target: THREE.Vector3 | null, weight?: number): void;
  setSpeaking(speaking: boolean): void;
  setEmote(expression: string, intensity: number): void;
  moveTo(position: THREE.Vector3): void;
  walkTo(target: THREE.Vector3, speed: string, onArrive: WalkCallback): void;
  isWalking(): boolean;
  getPosition(): THREE.Vector3;
  /** World-space point where the avatar's voice should originate. */
  getVoicePosition(): THREE.Vector3;
  setScale(scale: number): void;
  getScale(): number;
  /** World-space height (modelScale × group scale × mesh bounds). */
  getVisualHeight(): number;
  dispose(): void;
}

// ─── AvatarController (public API, delegates to backend) ─────────────────────

export class AvatarController {
  private backend: AvatarBackend | null = null;
  private config: ShellConfig = { model: 'default', animationPack: 'standard' };
  private _speaking = false;

  configure(config: ShellConfig): void {
    this.config = config;
  }

  async spawn(scene: THREE.Scene, position: THREE.Vector3): Promise<void> {
    if (this.backend) this.backend.dispose();

    if (this.config.model !== 'default') {
      try {
        const modelBackend = new ModelAvatar(this.config);
        await modelBackend.spawn(scene, position);
        this.backend = modelBackend;
        return;
      } catch (err) {
        console.warn('[avatar] Model load failed, falling back to procedural:', err);
      }
    }

    const proc = new ProceduralAvatar();
    await proc.spawn(scene, position);
    this.backend = proc;
  }

  update(dt: number): void {
    this.backend?.update(dt);
  }

  setAnimation(state: string): void {
    this.backend?.setAnimation(state);
  }

  setGazeTarget(target: THREE.Vector3 | null, weight = 1): void {
    this.backend?.setGazeTarget(target, weight);
  }

  setSpeaking(speaking: boolean): void {
    this._speaking = speaking;
    this.backend?.setSpeaking(speaking);
  }

  isSpeaking(): boolean {
    return this._speaking;
  }

  setEmote(expression: string, intensity: number): void {
    this.backend?.setEmote(expression, intensity);
  }

  moveTo(position: THREE.Vector3): void {
    this.backend?.moveTo(position);
  }

  faceToward(target: THREE.Vector3): void {
    const pos = this.getPosition();
    const dir = new THREE.Vector3(target.x - pos.x, 0, target.z - pos.z);
    if (dir.lengthSq() > 0.001) {
      const angle = Math.atan2(dir.x, dir.z);
      if (this.backend) {
        (this.backend as any).group?.rotation?.set?.(0, angle, 0);
      }
    }
  }

  walkTo(target: THREE.Vector3, speed = 'walk', onArrive?: () => void): void {
    this.backend?.walkTo(target, speed, onArrive ?? (() => {}));
  }

  isWalking(): boolean {
    return this.backend?.isWalking() ?? false;
  }

  getPosition(): THREE.Vector3 {
    return this.backend?.getPosition() ?? new THREE.Vector3();
  }

  getVoicePosition(): THREE.Vector3 {
    if (this.backend) return this.backend.getVoicePosition();
    return this.getPosition();
  }

  adjustScale(delta: number): void {
    this.setScale(this.getScale() + delta);
  }

  setScale(scale: number): void {
    const next = THREE.MathUtils.clamp(scale, 0.05, 10);
    if ((this.backend as any)?.setScale) {
      (this.backend as any).setScale(next);
      return;
    }
    const group = (this.backend as any)?.group as THREE.Group | undefined;
    group?.scale.setScalar(next);
  }

  getScale(): number {
    return (this.backend as any)?.getScale?.() ?? 1;
  }

  getVisualHeight(): number {
    return this.backend?.getVisualHeight() ?? Math.max(this.getScale() * 1.65, 0.2);
  }

  getConfig(): ShellConfig {
    return { ...this.config };
  }

  getClipInfo(): ClipInfo[] {
    const backend = this.backend as any;
    if (!backend?.clips) return [];
    const clips: ClipInfo[] = [];
    for (const [name, clip] of backend.clips as Map<string, THREE.AnimationClip>) {
      clips.push({
        name,
        duration: clip.duration,
        isLooping: true,
      });
    }
    return clips;
  }

  getAnimationPack(): Record<string, string> {
    const backend = this.backend as any;
    if (!backend?.animMap) return {};
    return { ...backend.animMap };
  }

  getActiveClipName(): string {
    const backend = this.backend as any;
    return backend?.activeClipName ?? '';
  }

  getGroup(): THREE.Group | null {
    return (this.backend as any)?.group ?? null;
  }

  getMixer(): THREE.AnimationMixer | null {
    const backend = this.backend as any;
    return backend?.mixer ?? null;
  }

  playClipByName(name: string): void {
    this.backend?.setAnimation(name);
  }

  async playMotionClip(url: string, name: string, loop = false): Promise<void> {
    const backend = this.backend as any;
    if (!backend || typeof backend.playMotionClip !== 'function') {
      throw new Error('The active body cannot play generated motion clips');
    }
    await backend.playMotionClip(url, name, loop);
  }

  applyBehaviorOutput(output: BehaviorOutputData): void {
    if (!this.backend) return;
    if (output.gazeTarget !== undefined) {
      if (output.gazeTarget) {
        this.setGazeTarget(new THREE.Vector3(
          output.gazeTarget.x, output.gazeTarget.y, output.gazeTarget.z
        ));
      } else {
        this.setGazeTarget(null);
      }
    }
    if (output.animation !== undefined) {
      this.setAnimation(output.animation);
    }
    if (output.expression !== undefined) {
      this.setEmote(output.expression, output.expressionIntensity ?? 0.5);
    }
  }

  hide(): void {
    if (this.backend) {
      (this.backend as any).group?.traverse?.((obj: THREE.Object3D) => { obj.visible = false; });
    }
  }

  show(): void {
    if (this.backend) {
      (this.backend as any).group?.traverse?.((obj: THREE.Object3D) => { obj.visible = true; });
    }
  }

  dispose(): void {
    this.backend?.dispose();
    this.backend = null;
  }
}

// ═════════════════════════════════════════════════════════════════════════════
// Model Avatar — loads GLB or FBX models with AnimationMixer
// ═════════════════════════════════════════════════════════════════════════════

const CROSSFADE_DURATION = 0.3;
const WALK_ARRIVAL_DISTANCE = 0.015;
const WALK_SPEED = 2.0;
const FAST_WALK_SPEED = 3.5;
const WALK_ACCELERATION = 7.0;
const WALK_DECELERATION = 9.0;
const WALK_TURN_RATE = Math.PI * 3;
const GESTURE_HOLD_MS = 8_000;
const DANCE_GESTURE_HOLD_MS = 60_000;
const NON_INTERRUPTIBLE_CLIP_HINTS = [
  'waving',
  'wave',
  'pointing',
  'point',
  'explaining',
  'explain',
  'nodding',
  'nod',
  'celebrating',
  'celebrate',
];
const DANCE_CLIP_HINTS = [
  'dancing',
  'dance',
  'breakdanc',
  'twerk',
  'macarena',
  'hiphop',
  'twist',
  'cheering',
  'cheer',
  'clapping',
  'clap',
];

class ModelAvatar implements AvatarBackend {
  private group = new THREE.Group();
  private mixer: THREE.AnimationMixer | null = null;
  private clips = new Map<string, THREE.AnimationClip>();
  private activeAction: THREE.AnimationAction | null = null;
  private activeClipName = '';
  private headBone: THREE.Bone | null = null;
  private animMap: Record<string, string> = {};
  private modelScale: number;
  private modelPath: string;
  private shellSlug: string | undefined;
  private modelRoot: THREE.Object3D | null = null;

  private gazeTarget: THREE.Vector3 | null = null;
  private gazeWeight = 1;
  private isSpeaking = false;
  private walkTarget: THREE.Vector3 | null = null;
  private walkSpeed = WALK_SPEED;
  private walkVelocity = 0;
  private walkArriveCallback: WalkCallback | null = null;
  private normalizedLocomotionClips = new Set<string>();
  private walkClipNaturalSpeed = 0;
  private emoteColor = new THREE.Color(0x000000);
  private emoteIntensity = 0;
  private speakGlowPhase = 0;
  private disposed = false;
  private gestureUntil = 0;
  private userScale = 1;

  constructor(config: ShellConfig) {
    this.modelScale = config.scale ?? 1;
    this.modelPath = config.model;
    this.shellSlug = config.shellSlug;
    if (typeof config.animationPack === 'object') {
      this.animMap = { ...config.animationPack };
    }
  }

  async spawn(scene: THREE.Scene, position: THREE.Vector3): Promise<void> {
    const modelUrl = this.shellSlug
      ? `/shells/${this.shellSlug}/${this.modelPath}`
      : `/${this.modelPath}`;
    const model = await this.loadModel(modelUrl);
    this.modelRoot = model;

    model.scale.setScalar(this.modelScale);
    this.group.add(model);
    this.group.position.copy(position);

    this.mixer = new THREE.AnimationMixer(model);

    model.traverse((child: THREE.Object3D) => {
      if ((child as THREE.Bone).isBone && /head/i.test(child.name)) {
        this.headBone = child as THREE.Bone;
      }
      if ((child as THREE.Mesh).isMesh) {
        const mesh = child as THREE.Mesh;
        mesh.castShadow = true;
        mesh.receiveShadow = true;
        // Three.js caches a SkinnedMesh bounding sphere from one pose. Retargeted
        // animations can move limbs well beyond it, causing the whole avatar to
        // pop out near a frustum edge even while animated geometry is visible.
        if ((mesh as THREE.SkinnedMesh).isSkinnedMesh) {
          mesh.frustumCulled = false;
        }
        const mats = Array.isArray(mesh.material) ? mesh.material : [mesh.material];
        for (const mat of mats) {
          const std = mat as THREE.MeshStandardMaterial;
          if (std.isMeshStandardMaterial) {
            if (std.metalness > 0.8) std.metalness = 0.4;
            if (std.roughness < 0.15) std.roughness = 0.3;
            std.envMapIntensity = 1.2;
            std.needsUpdate = true;
          } else if ((mat as THREE.MeshPhongMaterial).isMeshPhongMaterial) {
            const phong = mat as THREE.MeshPhongMaterial;
            const color = phong.color.clone();
            const emissive = phong.emissive?.clone() || new THREE.Color(0x000000);
            const newMat = new THREE.MeshStandardMaterial({
              color,
              emissive,
              roughness: 0.45,
              metalness: 0.05,
              envMapIntensity: 1.2,
            });
            if (phong.map) newMat.map = phong.map;
            if (phong.normalMap) newMat.normalMap = phong.normalMap;
            mesh.material = Array.isArray(mesh.material) ? mesh.material.map((m) => m === mat ? newMat : m) : newMat;
            phong.dispose();
          }
        }
      }
    });

    // Load animation clips — either from the model itself or from separate files
    await this.loadAnimations(model);

    scene.add(this.group);

    this.playClip('idle');

    this.group.scale.setScalar(0.001);
    this.animateAppear();
  }

  private async loadModel(url: string): Promise<THREE.Group> {
    if (isFBX(url)) {
      const loader = new FBXLoader();
      return new Promise<THREE.Group>((resolve, reject) => {
        loader.load(url, (group) => resolve(group), undefined, reject);
      });
    }
    const loader = new GLTFLoader();
    const gltf = await new Promise<any>((resolve, reject) => {
      loader.load(url, resolve, undefined, reject);
    });
    for (const clip of gltf.animations as THREE.AnimationClip[]) {
      this.clips.set(clip.name, clip);
    }
    return gltf.scene as THREE.Group;
  }

  private async loadAnimations(model: THREE.Group): Promise<void> {
    // Register any animations already on the model (from GLB or FBX)
    if (model.animations) {
      for (const clip of model.animations) {
        if (!this.clips.has(clip.name)) {
          this.clips.set(clip.name, clip);
        }
      }
    }

    // Load animation clips from separate files when animMap values are file paths
    const loadPromises: Promise<void>[] = [];

    for (const [state, value] of Object.entries(this.animMap)) {
      if (isFilePath(value)) {
        const url = this.shellSlug
          ? `/shells/${this.shellSlug}/${value}`
          : `/${value}`;
        loadPromises.push(this.loadAnimationFile(state, url, model));
      }
    }

    if (loadPromises.length > 0) {
      await Promise.all(loadPromises);
    }
  }

  private async loadAnimationFile(
    stateName: string,
    url: string,
    model: THREE.Object3D,
  ): Promise<void> {
    try {
      let clips: THREE.AnimationClip[];

      if (isFBX(url)) {
        const loader = new FBXLoader();
        const group = await new Promise<THREE.Group>((resolve, reject) => {
          loader.load(url, (g) => resolve(g), undefined, reject);
        });
        clips = group.animations ?? [];
      } else {
        const loader = new GLTFLoader();
        const gltf = await new Promise<any>((resolve, reject) => {
          loader.load(url, resolve, undefined, reject);
        });
        clips = gltf.animations ?? [];
      }

      if (clips.length > 0) {
        const result = retargetMixamoClip(clips[0], model);
        const clip = result.clip;
        clip.name = stateName;
        this.clips.set(stateName, clip);
        this.animMap[stateName] = stateName;
        if (result.remapped > 0) {
          console.log(
            `[avatar] Remapped ${result.remapped}/${clip.tracks.length} Mixamo tracks for "${stateName}"`,
          );
        }
        if (result.unresolved.length > 0) {
          console.warn(
            `[avatar] Animation "${stateName}" has unresolved targets: ${result.unresolved.join(', ')}`,
          );
        }
        console.log(`[avatar] Loaded animation "${stateName}" from ${url} (${clip.duration.toFixed(1)}s)`);
      }
    } catch (err) {
      console.warn(`[avatar] Failed to load animation "${stateName}" from ${url}:`, err);
    }
  }

  private animateAppear(): void {
    let progress = 0;
    const tick = () => {
      if (this.disposed) return;
      progress += 0.016 * 1.8;
      if (progress >= 1) {
        this.group.scale.setScalar(this.userScale);
        return;
      }
      const t = easeOutBack(progress);
      this.group.scale.setScalar(t * this.userScale);
      requestAnimationFrame(tick);
    };
    requestAnimationFrame(tick);
  }

  private resolveClipName(state: string): string | null {
    const mapped = this.animMap[state];
    if (mapped && this.clips.has(mapped)) return mapped;
    if (this.clips.has(state)) return state;

    const capitalized = state.charAt(0).toUpperCase() + state.slice(1);
    if (this.clips.has(capitalized)) return capitalized;

    for (const [name] of this.clips) {
      if (name.toLowerCase().includes(state.toLowerCase())) return name;
    }
    return null;
  }

  private playClip(state: string): void {
    if (!this.mixer) return;
    // Enforce locomotion ownership at the mixer boundary as well as at the
    // public setters. Speech and behavior events can arrive in either order;
    // neither may cross-fade the walking action while a path is active.
    if (this.walkTarget && state.toLowerCase() !== 'walking') return;

    const clipName = this.resolveClipName(state);
    if (!clipName || clipName === this.activeClipName) return;

    const clip = this.clips.get(clipName);
    if (!clip) return;

    if (state.toLowerCase() === 'walking' && !this.normalizedLocomotionClips.has(clipName)) {
      const result = makeLocomotionClipInPlace(clip);
      this.normalizedLocomotionClips.add(clipName);
      if (result.horizontalDistance > 0 && clip.duration > 0) {
        this.walkClipNaturalSpeed = result.horizontalDistance * this.modelScale / clip.duration;
      }
      if (result.normalizedTracks > 0) {
        console.log(
          `[avatar] Converted locomotion clip "${clipName}" to an in-place seamless loop`,
        );
      }
    }

    const newAction = this.mixer.clipAction(clip);

    if (this.activeAction) {
      newAction.reset();
      newAction.play();
      this.activeAction.crossFadeTo(newAction, CROSSFADE_DURATION, true);
    } else {
      newAction.reset().play();
    }

    this.activeAction = newAction;
    this.activeClipName = clipName;
  }

  update(dt: number): void {
    if (this.disposed) return;
    this.mixer?.update(dt);
    this.updateGaze(dt);
    this.updateWalk(dt);
    this.updateSpeakGlow(dt);
  }

  private updateWalk(dt: number): void {
    if (!this.walkTarget) return;
    const pos = this.group.position;
    const dir = this.walkTarget.clone().sub(pos);
    dir.y = 0;
    const dist = dir.length();

    if (dist <= WALK_ARRIVAL_DISTANCE) {
      pos.copy(this.walkTarget);
      this.walkTarget = null;
      this.walkVelocity = 0;
      this.playClip(this.isSpeaking ? 'talking' : 'idle');
      const onArrive = this.walkArriveCallback;
      this.walkArriveCallback = null;
      onArrive?.();
      return;
    }

    const frameDt = THREE.MathUtils.clamp(dt, 0, 0.1);
    const brakingSpeed = Math.sqrt(
      2 * WALK_DECELERATION * Math.max(0, dist - WALK_ARRIVAL_DISTANCE),
    );
    const targetVelocity = Math.min(this.walkSpeed, brakingSpeed);
    const acceleration = targetVelocity >= this.walkVelocity
      ? WALK_ACCELERATION
      : WALK_DECELERATION;
    const maxVelocityChange = acceleration * frameDt;
    this.walkVelocity += THREE.MathUtils.clamp(
      targetVelocity - this.walkVelocity,
      -maxVelocityChange,
      maxVelocityChange,
    );

    dir.normalize();
    const angle = Math.atan2(dir.x, dir.z);
    const targetRotation = new THREE.Quaternion().setFromAxisAngle(
      new THREE.Vector3(0, 1, 0),
      angle,
    );
    this.group.quaternion.rotateTowards(targetRotation, WALK_TURN_RATE * frameDt);

    const step = Math.min(this.walkVelocity * frameDt, dist);
    dir.multiplyScalar(step);
    pos.add(dir);
  }

  private updateSpeakGlow(dt: number): void {
    if (!this.isSpeaking) {
      if (this.speakGlowPhase > 0) {
        this.speakGlowPhase = 0;
        this.resetEmissive();
      }
      return;
    }
    this.speakGlowPhase += dt * 2.5;
    const pulse = 0.04 + Math.sin(this.speakGlowPhase) * 0.03;
    this.group.traverse((child: THREE.Object3D) => {
      if (!(child as THREE.Mesh).isMesh) return;
      const mats = Array.isArray((child as THREE.Mesh).material)
        ? (child as THREE.Mesh).material as THREE.Material[]
        : [(child as THREE.Mesh).material as THREE.Material];
      for (const mat of mats) {
        if ((mat as THREE.MeshStandardMaterial).emissiveIntensity !== undefined) {
          (mat as THREE.MeshStandardMaterial).emissiveIntensity = pulse;
        }
      }
    });
  }

  private resetEmissive(): void {
    this.group.traverse((child: THREE.Object3D) => {
      if (!(child as THREE.Mesh).isMesh) return;
      const mats = Array.isArray((child as THREE.Mesh).material)
        ? (child as THREE.Mesh).material as THREE.Material[]
        : [(child as THREE.Mesh).material as THREE.Material];
      for (const mat of mats) {
        if ((mat as THREE.MeshStandardMaterial).emissiveIntensity !== undefined) {
          (mat as THREE.MeshStandardMaterial).emissiveIntensity = 0;
        }
      }
    });
  }

  private updateGaze(dt: number): void {
    if (!this.headBone || !this.gazeTarget) return;

    const headWorld = new THREE.Vector3();
    this.headBone.getWorldPosition(headWorld);
    const dir = this.gazeTarget.clone().sub(headWorld).normalize();
    const targetQuat = new THREE.Quaternion().setFromUnitVectors(
      new THREE.Vector3(0, 0, 1),
      dir,
    );

    const parentWorldQuat = new THREE.Quaternion();
    this.headBone.parent?.getWorldQuaternion(parentWorldQuat);
    const localTarget = targetQuat.premultiply(parentWorldQuat.invert());

    const speed = 3.0 * this.gazeWeight;
    this.headBone.quaternion.slerp(localTarget, 1 - Math.exp(-speed * dt));
  }

  setAnimation(state: string): void {
    // Behavior, thinking, and speech systems may all request a pose every
    // frame. Locomotion owns the body until the active path is complete;
    // otherwise a late "idle" write makes the avatar float along its route.
    if (this.walkTarget && state.toLowerCase() !== 'walking') return;

    const resolved = this.resolveClipName(state);
    if (!resolved) {
      this.gestureUntil = 0;
      this.playClip(state);
      return;
    }

    const clipName = resolved.toLowerCase();
    if (DANCE_CLIP_HINTS.some((hint) => clipName.includes(hint))) {
      this.gestureUntil = performance.now() + DANCE_GESTURE_HOLD_MS;
    } else if (NON_INTERRUPTIBLE_CLIP_HINTS.some((hint) => clipName.includes(hint))) {
      this.gestureUntil = performance.now() + GESTURE_HOLD_MS;
    } else {
      this.gestureUntil = 0;
    }

    this.playClip(state);
  }

  setGazeTarget(target: THREE.Vector3 | null, weight = 1): void {
    this.gazeTarget = target;
    this.gazeWeight = weight;
  }

  setSpeaking(speaking: boolean): void {
    this.isSpeaking = speaking;
    if (this.walkTarget) return;
    if (performance.now() < this.gestureUntil) return;
    if (speaking) this.playClip('talking');
    else this.playClip('idle');
  }

  setEmote(expression: string, intensity: number): void {
    this.emoteIntensity = intensity;
    const c = this.emoteColor;
    switch (expression) {
      case 'happy': c.set(0x00ffa0); break;
      case 'excited': c.set(0x44ffcc); break;
      case 'curious': case 'thoughtful': c.set(0x3388ff); break;
      case 'concerned': c.set(0x8866ff); break;
      case 'surprised': c.set(0xffffff); break;
      case 'attentive': c.set(0x00eeff); break;
      default: c.set(0x000000); this.emoteIntensity = 0;
    }
    this.applyEmoteTint();
  }

  private applyEmoteTint(): void {
    this.group.traverse((child: THREE.Object3D) => {
      if (!(child as THREE.Mesh).isMesh) return;
      const mesh = child as THREE.Mesh;
      const mats = Array.isArray(mesh.material) ? mesh.material : [mesh.material];
      for (const mat of mats) {
        if ((mat as THREE.MeshStandardMaterial).emissive) {
          (mat as THREE.MeshStandardMaterial).emissive.copy(this.emoteColor);
          (mat as THREE.MeshStandardMaterial).emissiveIntensity = this.emoteIntensity * 0.08;
        }
      }
    });
  }

  moveTo(position: THREE.Vector3): void {
    const wasWalking = this.walkTarget !== null;
    this.walkTarget = null;
    this.walkVelocity = 0;
    this.walkArriveCallback = null;
    this.group.position.copy(position);
    if (wasWalking) this.playClip(this.isSpeaking ? 'talking' : 'idle');
  }

  walkTo(target: THREE.Vector3, speed: string, onArrive: WalkCallback): void {
    if (speed === 'instant') {
      this.moveTo(target);
      onArrive();
      return;
    }

    const wasWalking = this.walkTarget !== null;
    this.walkTarget = target.clone();
    this.walkSpeed = speed === 'fast' ? FAST_WALK_SPEED : WALK_SPEED;
    if (!wasWalking) this.walkVelocity = 0;
    this.walkArriveCallback = onArrive;
    this.playClip('walking');

    if (this.activeAction && this.walkClipNaturalSpeed > 0.05) {
      const playbackRate = THREE.MathUtils.clamp(
        this.walkSpeed / this.walkClipNaturalSpeed,
        0.35,
        2.5,
      );
      this.activeAction.setEffectiveTimeScale(playbackRate);
    }
  }

  isWalking(): boolean {
    return this.walkTarget !== null;
  }

  getPosition(): THREE.Vector3 {
    return this.group.position.clone();
  }

  getVoicePosition(): THREE.Vector3 {
    if (this.headBone) {
      const position = new THREE.Vector3();
      this.headBone.getWorldPosition(position);
      return position;
    }
    const bounds = new THREE.Box3().setFromObject(this.group);
    const center = new THREE.Vector3();
    const size = new THREE.Vector3();
    bounds.getCenter(center);
    bounds.getSize(size);
    center.y = bounds.max.y - size.y * 0.08;
    return center;
  }

  setScale(scale: number): void {
    this.userScale = THREE.MathUtils.clamp(scale, 0.05, 10);
    this.group.scale.setScalar(this.userScale);
  }

  getScale(): number {
    return this.userScale;
  }

  getVisualHeight(): number {
    const box = new THREE.Box3().setFromObject(this.group);
    const size = new THREE.Vector3();
    box.getSize(size);
    return Math.max(size.y, 0.08);
  }

  async playMotionClip(url: string, name: string, loop = false): Promise<void> {
    if (!this.modelRoot) throw new Error('Avatar model is not ready');
    const stateName = (name || 'generated-motion').slice(0, 120);
    await this.loadAnimationFile(stateName, url, this.modelRoot);
    if (!this.clips.has(stateName)) {
      throw new Error('Generated motion clip contained no usable animation');
    }
    this.activeClipName = '';
    this.setAnimation(stateName);
    if (this.activeAction) {
      this.activeAction.setLoop(loop ? THREE.LoopRepeat : THREE.LoopOnce, loop ? Infinity : 1);
      this.activeAction.clampWhenFinished = !loop;
    }
  }

  dispose(): void {
    this.disposed = true;
    this.mixer?.stopAllAction();
    this.group.traverse((obj) => {
      if (obj instanceof THREE.Mesh) {
        obj.geometry.dispose();
        if (Array.isArray(obj.material)) {
          obj.material.forEach((m: THREE.Material) => m.dispose());
        } else {
          (obj.material as THREE.Material).dispose();
        }
      }
    });
    this.group.parent?.remove(this.group);
  }
}

// ═════════════════════════════════════════════════════════════════════════════
// Procedural Avatar — the original capsule+sphere+particles avatar
// ═════════════════════════════════════════════════════════════════════════════

const PARTICLE_VERTEX = /* glsl */ `
  attribute float aSize;
  attribute float aAlpha;
  varying float vAlpha;
  uniform float uTime;
  uniform float uSpeedMultiplier;
  void main() {
    vAlpha = aAlpha;
    vec3 pos = position;
    float angle = uTime * 0.3 * uSpeedMultiplier + float(gl_VertexID) * 0.12;
    float cosA = cos(angle);
    float sinA = sin(angle);
    pos.x = position.x * cosA - position.z * sinA;
    pos.z = position.x * sinA + position.z * cosA;
    vec4 mvPos = modelViewMatrix * vec4(pos, 1.0);
    gl_PointSize = aSize * (200.0 / -mvPos.z);
    gl_Position = projectionMatrix * mvPos;
  }
`;

const PARTICLE_FRAGMENT = /* glsl */ `
  varying float vAlpha;
  uniform vec3 uColor;
  void main() {
    float d = length(gl_PointCoord - vec2(0.5));
    if (d > 0.5) discard;
    float alpha = smoothstep(0.5, 0.05, d) * vAlpha;
    gl_FragColor = vec4(uColor, alpha);
  }
`;

type ProceduralAnimState = 'idle' | 'talking' | 'thinking' | 'waving' | 'appearing';

class ProceduralAvatar implements AvatarBackend {
  private group = new THREE.Group();
  private body!: THREE.Mesh;
  private head!: THREE.Mesh;
  private core!: THREE.Mesh;
  private particles!: THREE.Points;
  private particleMaterial!: THREE.ShaderMaterial;
  private groundDisc!: THREE.Mesh;
  private groundDiscMaterial!: THREE.MeshBasicMaterial;
  private agentLight!: THREE.PointLight;

  private elapsed = 0;
  private animState: ProceduralAnimState = 'idle';
  private isSpeaking = false;
  private gazeTarget: THREE.Vector3 | null = null;
  private gazeWeight = 1;

  private baseCoreScale = 1;
  private baseEmissive = new THREE.Color(0x00d4ff);
  private currentEmissive = new THREE.Color(0x00d4ff);

  private appearProgress = 0;
  private disposed = false;
  private userScale = 1;

  async spawn(scene: THREE.Scene, position: THREE.Vector3): Promise<void> {
    this.group.position.copy(position);

    const bodyGeo = new THREE.CapsuleGeometry(0.07, 0.18, 16, 24);
    const bodyMat = new THREE.MeshStandardMaterial({
      color: 0x44ccff, emissive: this.baseEmissive, emissiveIntensity: 1.5,
      roughness: 0.2, metalness: 0.0, transparent: true, opacity: 0.88,
      side: THREE.DoubleSide,
    });
    this.body = new THREE.Mesh(bodyGeo, bodyMat);
    this.body.position.y = 0.18;
    this.group.add(this.body);

    const headGeo = new THREE.SphereGeometry(0.065, 32, 24);
    const headMat = new THREE.MeshStandardMaterial({
      color: 0x88eeff, emissive: this.baseEmissive, emissiveIntensity: 1.8,
      roughness: 0.1, metalness: 0.0, transparent: true, opacity: 0.92,
      side: THREE.DoubleSide,
    });
    this.head = new THREE.Mesh(headGeo, headMat);
    this.head.position.y = 0.38;
    this.group.add(this.head);

    const coreGeo = new THREE.SphereGeometry(0.035, 24, 16);
    const coreMat = new THREE.MeshBasicMaterial({
      color: 0xffffff, transparent: true, opacity: 0.95,
    });
    this.core = new THREE.Mesh(coreGeo, coreMat);
    this.core.position.y = 0.22;
    this.group.add(this.core);

    this.agentLight = new THREE.PointLight(0x00d4ff, 3, 3);
    this.agentLight.position.y = 0.25;
    this.group.add(this.agentLight);

    const particleCount = 250;
    const positions = new Float32Array(particleCount * 3);
    const sizes = new Float32Array(particleCount);
    const alphas = new Float32Array(particleCount);
    for (let i = 0; i < particleCount; i++) {
      const theta = Math.random() * Math.PI * 2;
      const phi = Math.acos(2 * Math.random() - 1);
      const r = 0.12 + Math.random() * 0.18;
      positions[i * 3] = r * Math.sin(phi) * Math.cos(theta);
      positions[i * 3 + 1] = 0.22 + (Math.random() - 0.5) * 0.36;
      positions[i * 3 + 2] = r * Math.sin(phi) * Math.sin(theta);
      sizes[i] = 1.2 + Math.random() * 2.5;
      alphas[i] = 0.2 + Math.random() * 0.5;
    }
    const particleGeo = new THREE.BufferGeometry();
    particleGeo.setAttribute('position', new THREE.BufferAttribute(positions, 3));
    particleGeo.setAttribute('aSize', new THREE.BufferAttribute(sizes, 1));
    particleGeo.setAttribute('aAlpha', new THREE.BufferAttribute(alphas, 1));
    this.particleMaterial = new THREE.ShaderMaterial({
      vertexShader: PARTICLE_VERTEX, fragmentShader: PARTICLE_FRAGMENT,
      uniforms: {
        uTime: { value: 0 }, uColor: { value: new THREE.Color(0x88eeff) },
        uSpeedMultiplier: { value: 1.0 },
      },
      transparent: true, depthWrite: false, blending: THREE.AdditiveBlending,
    });
    this.particles = new THREE.Points(particleGeo, this.particleMaterial);
    this.group.add(this.particles);

    const discGeo = new THREE.CircleGeometry(0.2, 48);
    this.groundDiscMaterial = new THREE.MeshBasicMaterial({
      color: 0x00d4ff, transparent: true, opacity: 0.15,
      side: THREE.DoubleSide, blending: THREE.AdditiveBlending,
    });
    this.groundDisc = new THREE.Mesh(discGeo, this.groundDiscMaterial);
    this.groundDisc.rotation.x = -Math.PI / 2;
    this.groundDisc.position.y = 0.001;
    this.group.add(this.groundDisc);

    scene.add(this.group);
    this.animState = 'appearing';
    this.appearProgress = 0;
    this.group.scale.setScalar(0.001);
  }

  update(dt: number): void {
    if (this.disposed) return;
    this.elapsed += dt;

    if (this.animState === 'appearing') {
      this.appearProgress += dt * 1.8;
      if (this.appearProgress >= 1) { this.appearProgress = 1; this.animState = 'idle'; }
      this.group.scale.setScalar(easeOutBack(this.appearProgress) * this.userScale);
    }

    const rate = this.isSpeaking ? 4.5 : this.animState === 'thinking' ? 1.2 : 2.0;
    const pulse = Math.sin(this.elapsed * rate) * 0.5 + 0.5;
    this.core.scale.setScalar(this.baseCoreScale * (0.8 + pulse * 0.4));
    (this.core.material as THREE.MeshBasicMaterial).opacity = 0.7 + pulse * 0.3;
    const bodyMat = this.body.material as THREE.MeshStandardMaterial;
    bodyMat.emissiveIntensity = 1.2 + pulse * 0.8;
    bodyMat.emissive.copy(this.currentEmissive);
    const headMat = this.head.material as THREE.MeshStandardMaterial;
    headMat.emissive.copy(this.currentEmissive);
    headMat.emissiveIntensity = 1.5 + pulse * 0.5;
    this.agentLight.intensity = 2 + pulse * 2;

    if (this.animState !== 'appearing') {
      const amp = this.animState === 'talking' ? 0.005 : 0.003;
      const spd = this.animState === 'talking' ? 3.5 : 1.8;
      this.body.position.y = 0.18 + Math.sin(this.elapsed * spd) * amp;
      if (this.animState === 'talking') this.body.rotation.z = Math.sin(this.elapsed * 2.3) * 0.03;
      else if (this.animState === 'waving') this.body.rotation.z = Math.sin(this.elapsed * 4) * 0.1;
      else this.body.rotation.z *= 0.95;
      this.head.rotation.z = THREE.MathUtils.lerp(
        this.head.rotation.z, this.animState === 'thinking' ? 0.15 : 0, this.animState === 'thinking' ? 0.02 : 0.05,
      );
    }

    this.particleMaterial.uniforms.uTime.value = this.elapsed;
    let speedMult = 1.0;
    if (this.isSpeaking || this.animState === 'talking') speedMult = 2.8;
    if (this.animState === 'thinking') speedMult = 0.4;
    this.particleMaterial.uniforms.uSpeedMultiplier.value = THREE.MathUtils.lerp(
      this.particleMaterial.uniforms.uSpeedMultiplier.value, speedMult, 0.05,
    );

    if (this.gazeTarget) {
      const worldPos = new THREE.Vector3();
      this.head.getWorldPosition(worldPos);
      const dir = this.gazeTarget.clone().sub(worldPos).normalize();
      const tq = new THREE.Quaternion().setFromUnitVectors(new THREE.Vector3(0, 0, 1), dir);
      this.head.quaternion.slerp(tq, 1 - Math.exp(-3.0 * this.gazeWeight * dt));
    }

    if (this.animState === 'waving' && Math.sin(this.elapsed * 4) < -0.95) {
      this.animState = 'idle';
    }
  }

  setAnimation(state: string): void {
    if (['appearing', 'idle', 'talking', 'thinking', 'waving'].includes(state)) {
      this.animState = state as ProceduralAnimState;
    }
  }

  setGazeTarget(target: THREE.Vector3 | null, weight = 1): void {
    this.gazeTarget = target;
    this.gazeWeight = weight;
  }

  setSpeaking(speaking: boolean): void {
    this.isSpeaking = speaking;
    if (speaking && this.animState === 'idle') this.animState = 'talking';
    if (!speaking && this.animState === 'talking') this.animState = 'idle';
  }

  setEmote(expression: string, intensity: number): void {
    const c = this.currentEmissive;
    const b = this.baseEmissive;
    switch (expression) {
      case 'happy': c.set(0x00ffa0).lerp(b, 1 - intensity); break;
      case 'excited': c.set(0x44ffcc).lerp(b, 1 - intensity); break;
      case 'curious': case 'thoughtful': c.set(0x3388ff).lerp(b, 1 - intensity); break;
      case 'concerned': c.set(0x8866ff).lerp(b, 1 - intensity); break;
      case 'surprised': c.set(0xffffff).lerp(b, 1 - intensity); break;
      case 'attentive': c.set(0x00eeff).lerp(b, 1 - intensity); break;
      default: c.copy(b);
    }
  }

  moveTo(position: THREE.Vector3): void { this.group.position.copy(position); }
  walkTo(target: THREE.Vector3, _speed: string, onArrive: WalkCallback): void {
    this.group.position.copy(target);
    onArrive();
  }
  isWalking(): boolean { return false; }
  getPosition(): THREE.Vector3 { return this.group.position.clone(); }
  getVoicePosition(): THREE.Vector3 {
    const position = new THREE.Vector3();
    this.head.getWorldPosition(position);
    return position;
  }
  setScale(scale: number): void {
    this.userScale = THREE.MathUtils.clamp(scale, 0.05, 10);
    this.group.scale.setScalar(this.userScale);
  }
  getScale(): number { return this.userScale; }

  getVisualHeight(): number {
    const box = new THREE.Box3().setFromObject(this.group);
    const size = new THREE.Vector3();
    box.getSize(size);
    return Math.max(size.y, 0.08);
  }

  dispose(): void {
    this.disposed = true;
    this.group.traverse((obj) => {
      if (obj instanceof THREE.Mesh || obj instanceof THREE.Points) {
        obj.geometry.dispose();
        if (Array.isArray(obj.material)) obj.material.forEach((m) => m.dispose());
        else obj.material.dispose();
      }
    });
    this.group.parent?.remove(this.group);
  }
}

// ─── Shared ──────────────────────────────────────────────────────────────────

function easeOutBack(t: number): number {
  const c1 = 1.70158;
  const c3 = c1 + 1;
  return 1 + c3 * Math.pow(t - 1, 3) + c1 * Math.pow(t - 1, 2);
}
