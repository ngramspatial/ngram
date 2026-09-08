// @ts-nocheck
import * as THREE from 'three';
import type { SceneLights } from './scene-setup.js';
import type { EnvironmentPreset, LightingMood, ParticleType } from '@ngram-ar/core';
import { SceneEnvironment } from './scene-environment.js';

export interface SavedLightingOverride {
  color?: string;
  intensity?: number;
  mood?: LightingMood;
}

export interface SavedBackgroundOverride {
  color?: string;
  gradient?: { center: string; edge: string };
}

/** Durable environment state. Particle effects are intentionally transient. */
export interface SavedEnvironmentState {
  /** Present only when a preset was explicitly selected by the user/agent. */
  preset?: EnvironmentPreset;
  lighting?: SavedLightingOverride;
  background?: SavedBackgroundOverride;
  scene?: Record<string, unknown>;
}

// ─── Environment Presets ────────────────────────────────────────────────────

interface PresetConfig {
  bgCenter: string;
  bgEdge: string;
  keyColor: string;
  keyIntensity: number;
  fillColor: string;
  fillIntensity: number;
  rimColor: string;
  rimIntensity: number;
  hemiSky: string;
  hemiGround: string;
  hemiIntensity: number;
  ambientIntensity: number;
  exposure: number;
}

const PRESETS: Record<EnvironmentPreset, PresetConfig> = {
  default: {
    bgCenter: '#e8e8ec', bgEdge: '#d6d6da',
    keyColor: '#ffffff', keyIntensity: 1.8,
    fillColor: '#ffe8d0', fillIntensity: 0.6,
    rimColor: '#ddeeff', rimIntensity: 0.7,
    hemiSky: '#f0f0ff', hemiGround: '#d0c8c0', hemiIntensity: 0.8,
    ambientIntensity: 0.4, exposure: 1.1,
  },
  workshop: {
    bgCenter: '#2a2a30', bgEdge: '#1a1a1e',
    keyColor: '#ffe0b0', keyIntensity: 2.0,
    fillColor: '#ffd090', fillIntensity: 0.4,
    rimColor: '#88aacc', rimIntensity: 0.3,
    hemiSky: '#443322', hemiGround: '#221100', hemiIntensity: 0.4,
    ambientIntensity: 0.2, exposure: 0.9,
  },
  cozy: {
    bgCenter: '#3d2b1e', bgEdge: '#2a1c12',
    keyColor: '#ffcc88', keyIntensity: 1.4,
    fillColor: '#ff9944', fillIntensity: 0.5,
    rimColor: '#884400', rimIntensity: 0.2,
    hemiSky: '#ffddaa', hemiGround: '#442200', hemiIntensity: 0.5,
    ambientIntensity: 0.3, exposure: 0.85,
  },
  nature: {
    bgCenter: '#88bb88', bgEdge: '#668866',
    keyColor: '#ffffdd', keyIntensity: 1.6,
    fillColor: '#aaddaa', fillIntensity: 0.5,
    rimColor: '#88ccff', rimIntensity: 0.4,
    hemiSky: '#88bbff', hemiGround: '#446622', hemiIntensity: 0.7,
    ambientIntensity: 0.4, exposure: 1.0,
  },
  space: {
    bgCenter: '#0a0a1a', bgEdge: '#000008',
    keyColor: '#6688ff', keyIntensity: 1.0,
    fillColor: '#220044', fillIntensity: 0.2,
    rimColor: '#4444ff', rimIntensity: 0.5,
    hemiSky: '#111133', hemiGround: '#000000', hemiIntensity: 0.2,
    ambientIntensity: 0.1, exposure: 0.7,
  },
  party: {
    bgCenter: '#2a1040', bgEdge: '#180828',
    keyColor: '#ff44ff', keyIntensity: 1.8,
    fillColor: '#4488ff', fillIntensity: 0.8,
    rimColor: '#ff8844', rimIntensity: 0.6,
    hemiSky: '#ff66cc', hemiGround: '#4400aa', hemiIntensity: 0.6,
    ambientIntensity: 0.3, exposure: 1.0,
  },
  focus: {
    bgCenter: '#1e1e22', bgEdge: '#141416',
    keyColor: '#ccddff', keyIntensity: 1.5,
    fillColor: '#aabbdd', fillIntensity: 0.3,
    rimColor: '#8899bb', rimIntensity: 0.2,
    hemiSky: '#334455', hemiGround: '#111122', hemiIntensity: 0.3,
    ambientIntensity: 0.2, exposure: 0.8,
  },
  night: {
    bgCenter: '#0c0c14', bgEdge: '#04040a',
    keyColor: '#8888cc', keyIntensity: 0.8,
    fillColor: '#334466', fillIntensity: 0.2,
    rimColor: '#4444aa', rimIntensity: 0.3,
    hemiSky: '#222244', hemiGround: '#000000', hemiIntensity: 0.15,
    ambientIntensity: 0.1, exposure: 0.6,
  },
};

const MOOD_TINTS: Record<LightingMood, { keyColor: string; fillColor: string }> = {
  warm:     { keyColor: '#ffddaa', fillColor: '#ffcc88' },
  cool:     { keyColor: '#aaddff', fillColor: '#88bbee' },
  neutral:  { keyColor: '#ffffff', fillColor: '#eeeeee' },
  dramatic: { keyColor: '#ff8844', fillColor: '#220044' },
};

// ─── Particle System Configs ────────────────────────────────────────────────

interface ParticleConfig {
  count: number;
  spread: THREE.Vector3;
  velocity: THREE.Vector3;
  velocityRandom: THREE.Vector3;
  size: number;
  sizeRandom: number;
  color: THREE.Color;
  colorRandom: THREE.Color;
  lifetime: number;
  gravity: number;
  opacity: number;
}

const PARTICLE_CONFIGS: Record<ParticleType, ParticleConfig> = {
  sparkles: {
    count: 80, spread: new THREE.Vector3(2, 2, 2),
    velocity: new THREE.Vector3(0, 0.1, 0), velocityRandom: new THREE.Vector3(0.2, 0.2, 0.2),
    size: 0.03, sizeRandom: 0.02,
    color: new THREE.Color('#ffffcc'), colorRandom: new THREE.Color('#ffcc44'),
    lifetime: 2.0, gravity: 0, opacity: 0.8,
  },
  fireflies: {
    count: 40, spread: new THREE.Vector3(3, 1.5, 3),
    velocity: new THREE.Vector3(0, 0.02, 0), velocityRandom: new THREE.Vector3(0.1, 0.05, 0.1),
    size: 0.025, sizeRandom: 0.01,
    color: new THREE.Color('#ffee44'), colorRandom: new THREE.Color('#88aa00'),
    lifetime: 4.0, gravity: 0, opacity: 0.7,
  },
  confetti: {
    count: 200, spread: new THREE.Vector3(2, 0.2, 2),
    velocity: new THREE.Vector3(0, 1.5, 0), velocityRandom: new THREE.Vector3(1.5, 0.5, 1.5),
    size: 0.04, sizeRandom: 0.02,
    color: new THREE.Color('#ff4488'), colorRandom: new THREE.Color('#44aaff'),
    lifetime: 3.0, gravity: -2.5, opacity: 0.9,
  },
  snow: {
    count: 150, spread: new THREE.Vector3(4, 0.5, 4),
    velocity: new THREE.Vector3(0, -0.3, 0), velocityRandom: new THREE.Vector3(0.1, 0.05, 0.1),
    size: 0.02, sizeRandom: 0.015,
    color: new THREE.Color('#ffffff'), colorRandom: new THREE.Color('#ccddff'),
    lifetime: 5.0, gravity: -0.1, opacity: 0.7,
  },
  embers: {
    count: 60, spread: new THREE.Vector3(1.5, 0.3, 1.5),
    velocity: new THREE.Vector3(0, 0.5, 0), velocityRandom: new THREE.Vector3(0.3, 0.2, 0.3),
    size: 0.02, sizeRandom: 0.01,
    color: new THREE.Color('#ff6622'), colorRandom: new THREE.Color('#ffaa00'),
    lifetime: 2.5, gravity: 0.2, opacity: 0.9,
  },
  bubbles: {
    count: 50, spread: new THREE.Vector3(2, 0.5, 2),
    velocity: new THREE.Vector3(0, 0.4, 0), velocityRandom: new THREE.Vector3(0.1, 0.1, 0.1),
    size: 0.04, sizeRandom: 0.03,
    color: new THREE.Color('#aaeeff'), colorRandom: new THREE.Color('#eeccff'),
    lifetime: 4.0, gravity: 0.1, opacity: 0.5,
  },
};

// ─── Active Particle Effect ─────────────────────────────────────────────────

interface ActiveParticleEffect {
  id: string;
  points: THREE.Points;
  config: ParticleConfig;
  ages: Float32Array;
  velocities: Float32Array; // x,y,z interleaved
  origin: THREE.Vector3;
  elapsed: number;
  duration: number;
}

// ─── Environment Manager ────────────────────────────────────────────────────

const TRANSITION_SPEED = 1.5; // seconds for full transition

export class EnvironmentManager {
  sceneEnvironment: SceneEnvironment | null = null;
  private sceneRestoreFallback = null;
  private scene: THREE.Scene | null = null;
  private renderer: THREE.WebGLRenderer | null = null;
  private lights: SceneLights | null = null;

  private currentPreset: PresetConfig = PRESETS.default;
  private targetPreset: PresetConfig = PRESETS.default;
  private transitionProgress = 1; // 1 = complete
  private activePresetName: EnvironmentPreset = 'default';
  private lightingOverride: SavedLightingOverride | null = null;
  private backgroundOverride: SavedBackgroundOverride | null = null;
  private customBackgroundTexture: THREE.CanvasTexture | null = null;
  private isRestoringSavedState = false;
  private hasDurableState = false;
  private presetExplicit = false;
  private themeEnvironmentOverride = false;

  private effects = new Map<string, ActiveParticleEffect>();

  /** Called whenever durable environment state changes. */
  onPersistChange: (() => void) | null = null;

  attach(
    scene: THREE.Scene,
    renderer: THREE.WebGLRenderer,
    lights: SceneLights,
  ): void {
    this.scene = scene;
    this.renderer = renderer;
    this.lights = lights;
    this.sceneEnvironment = new SceneEnvironment(scene, renderer, lights);
    let sceneRevision = 0;
    this.sceneEnvironment.onChange = () => {
      if (sceneRevision !== this.sceneEnvironment.revision) {
        sceneRevision = this.sceneEnvironment.revision;
        this.sceneRestoreFallback = null;
      }
      if (Object.keys(this.sceneEnvironment.config).length) this.hasDurableState = true;
      this.notifyPersistChange();
    };
    this.snapshotCurrent();
    if (!this.themeEnvironmentOverride) this.applyBackgroundOverride();
  }

  getCurrentPreset(): EnvironmentPreset {
    return this.activePresetName;
  }

  /** Keep durable environment state while a UI theme temporarily owns its presentation. */
  setThemeEnvironmentOverride(active: boolean): void {
    this.themeEnvironmentOverride = active;
  }

  /** Re-assert the durable environment after an external theme refresh. */
  reapplyState(): void {
    if (this.themeEnvironmentOverride || !this.hasDurableState || !this.scene || !this.lights || !this.renderer) return;

    // updateThemeBackground() runs immediately before this method. Without an
    // explicit preset, preserve that fresh theme baseline and layer only the
    // durable lighting/background overrides on top of it.
    if (!this.presetExplicit && !this.lightingOverride) {
      if (this.backgroundOverride) this.applyBackgroundOverride();
      this.sceneEnvironment?.refreshBackgroundBaseline();
      this.sceneEnvironment?.apply();
      return;
    }
    if (!this.presetExplicit) this.snapshotCurrent();
    const target = this.presetExplicit
      ? this.makeTargetPreset(this.activePresetName, this.lightingOverride)
      : this.applyLightingOverride(this.currentPreset, this.lightingOverride);
    this.lights.key.color.set(target.keyColor);
    this.lights.key.intensity = target.keyIntensity;
    this.lights.fill.color.set(target.fillColor);
    this.lights.fill.intensity = target.fillIntensity;
    this.lights.rim.color.set(target.rimColor);
    this.lights.rim.intensity = target.rimIntensity;
    this.lights.hemi.color.set(target.hemiSky);
    this.lights.hemi.groundColor.set(target.hemiGround);
    this.lights.hemi.intensity = target.hemiIntensity;
    this.lights.ambient.intensity = target.ambientIntensity;
    this.renderer.toneMappingExposure = target.exposure;
    this.currentPreset = { ...target };
    this.targetPreset = { ...target };
    this.transitionProgress = 1;
    if (this.backgroundOverride) this.applyBackgroundOverride();
    else if (this.presetExplicit) this.scene.background = makeGradient(target.bgCenter, target.bgEdge);
    this.sceneEnvironment?.refreshBackgroundBaseline();
    this.sceneEnvironment?.apply();
  }

  getSavedState(): SavedEnvironmentState | undefined {
    if (!this.hasDurableState) return undefined;
    const state: SavedEnvironmentState = {};
    if (this.sceneRestoreFallback) state.scene = structuredClone(this.sceneRestoreFallback);
    else if (this.sceneEnvironment && Object.keys(this.sceneEnvironment.config).length) state.scene = structuredClone(this.sceneEnvironment.config);
    if (this.presetExplicit) state.preset = this.activePresetName;
    if (this.lightingOverride) state.lighting = { ...this.lightingOverride };
    if (this.backgroundOverride) {
      state.background = {
        ...(this.backgroundOverride.color ? { color: this.backgroundOverride.color } : {}),
        ...(this.backgroundOverride.gradient
          ? { gradient: { ...this.backgroundOverride.gradient } }
          : {}),
      };
    }
    return state;
  }

  async loadSavedState(state: SavedEnvironmentState | null | undefined): Promise<void> {
    if (!state || typeof state !== 'object') return;

    const preset = this.isEnvironmentPreset(state.preset) ? state.preset : null;
    const lighting = this.normalizeLightingOverride(state.lighting);
    const background = this.normalizeBackgroundOverride(state.background);
    if (!preset && !lighting && !background && !state.scene) return;

    this.isRestoringSavedState = true;
    try {
      this.clearBackgroundOverride();
      if (preset) this.setEnvironment(preset);
      if (lighting) this.setLighting(lighting);
      if (background) this.setBackground(background.color, background.gradient);
      if (state.scene) {
        this.sceneRestoreFallback = structuredClone(state.scene);
        this.hasDurableState = true;
        await this.sceneEnvironment?.configure(state.scene);
      }
    } finally {
      this.isRestoringSavedState = false;
      this.notifyPersistChange();
    }
  }

  setEnvironment(preset: EnvironmentPreset): void {
    if (!PRESETS[preset]) return;
    this.sceneEnvironment?.clear();
    this.hasDurableState = true;
    this.presetExplicit = true;
    this.snapshotCurrent();
    this.targetPreset = { ...PRESETS[preset] };
    this.activePresetName = preset;
    this.lightingOverride = null;
    this.transitionProgress = 0;
    this.notifyPersistChange();
  }

  setLighting(options: { color?: string; intensity?: number; mood?: LightingMood }): void {
    const normalized = this.normalizeLightingOverride(options);
    if (!normalized) return;
    this.hasDurableState = true;
    this.snapshotCurrent();

    const merged = { ...(this.lightingOverride ?? {}), ...normalized };
    // A newly selected mood owns the key tint unless this same update also
    // supplies an explicit color. Keeping an older color would mask the mood.
    if (normalized.mood && !normalized.color) delete merged.color;

    this.lightingOverride = merged;
    this.targetPreset = this.presetExplicit
      ? this.makeTargetPreset(this.activePresetName, merged)
      : this.applyLightingOverride(this.currentPreset, merged);
    this.transitionProgress = 0;
    this.notifyPersistChange();
  }

  spawnParticles(
    effectId: string,
    particleType: ParticleType,
    options: {
      position?: { x: number; y: number; z: number } | 'ambient';
      duration?: number;
      intensity?: number;
    } = {},
  ): void {
    if (!this.scene) return;
    this.removeEffect(effectId);

    const cfg = PARTICLE_CONFIGS[particleType];
    if (!cfg) return;

    const intensity = Math.max(0, Math.min(1, options.intensity ?? 0.5));
    const count = Math.round(cfg.count * (0.3 + intensity * 0.7));
    const duration = Math.min(options.duration ?? 10, 60);

    const origin = new THREE.Vector3(0, 1.0, 0);
    if (options.position && options.position !== 'ambient') {
      origin.set(options.position.x, options.position.y, options.position.z);
    }

    const positions = new Float32Array(count * 3);
    const colors = new Float32Array(count * 3);
    const sizes = new Float32Array(count);
    const ages = new Float32Array(count);
    const velocities = new Float32Array(count * 3);

    for (let i = 0; i < count; i++) {
      const i3 = i * 3;

      positions[i3]     = origin.x + (Math.random() - 0.5) * cfg.spread.x;
      positions[i3 + 1] = origin.y + (Math.random() - 0.5) * cfg.spread.y;
      positions[i3 + 2] = origin.z + (Math.random() - 0.5) * cfg.spread.z;

      velocities[i3]     = cfg.velocity.x + (Math.random() - 0.5) * cfg.velocityRandom.x;
      velocities[i3 + 1] = cfg.velocity.y + (Math.random() - 0.5) * cfg.velocityRandom.y;
      velocities[i3 + 2] = cfg.velocity.z + (Math.random() - 0.5) * cfg.velocityRandom.z;

      const c = cfg.color.clone().lerp(cfg.colorRandom, Math.random());
      colors[i3]     = c.r;
      colors[i3 + 1] = c.g;
      colors[i3 + 2] = c.b;

      sizes[i] = cfg.size + Math.random() * cfg.sizeRandom;
      ages[i] = Math.random() * cfg.lifetime; // stagger initial ages
    }

    const geo = new THREE.BufferGeometry();
    geo.setAttribute('position', new THREE.BufferAttribute(positions, 3));
    geo.setAttribute('color', new THREE.BufferAttribute(colors, 3));
    geo.setAttribute('size', new THREE.BufferAttribute(sizes, 1));

    const mat = new THREE.PointsMaterial({
      size: cfg.size,
      vertexColors: true,
      transparent: true,
      opacity: cfg.opacity,
      depthWrite: false,
      sizeAttenuation: true,
      blending: THREE.AdditiveBlending,
    });

    const points = new THREE.Points(geo, mat);
    points.renderOrder = 950;
    this.scene.add(points);

    this.effects.set(effectId, {
      id: effectId,
      points,
      config: cfg,
      ages,
      velocities,
      origin,
      elapsed: 0,
      duration,
    });
  }

  setBackground(color?: string, gradient?: { center: string; edge: string }): void {
    const normalized = this.normalizeBackgroundOverride({ color, gradient });
    if (!normalized) return;

    this.hasDurableState = true;
    this.clearBackgroundTexture();
    this.backgroundOverride = normalized;
    if (!this.themeEnvironmentOverride) this.applyBackgroundOverride();
    this.notifyPersistChange();
  }

  clearEnvironment(): void {
    this.sceneEnvironment?.clear();
    this.snapshotCurrent();
    this.targetPreset = { ...PRESETS.default };
    this.activePresetName = 'default';
    this.lightingOverride = null;
    this.clearBackgroundOverride();
    this.hasDurableState = false;
    this.presetExplicit = false;
    this.transitionProgress = 0;

    for (const [id] of this.effects) {
      this.removeEffect(id);
    }
    this.notifyPersistChange();
  }

  update(dt: number): void {
    this.updateTransition(dt);
    this.updateParticles(dt);
    this.sceneEnvironment?.apply();
  }

  async handle(command, payload = {}) {
    if (!this.sceneEnvironment) throw Error('Environment renderer is not attached');
    const result = await this.sceneEnvironment.handle(command, payload);
    if (command === 'clear' || command === 'configure') this.reapplyState();
    return command === 'capabilities' ? result : this.sceneEnvironment.inspect();
  }

  // ─── Internal ──────────────────────────────────────────────────────────────

  private snapshotCurrent(): void {
    if (!this.lights || !this.renderer) return;

    this.currentPreset = {
      bgCenter: this.targetPreset.bgCenter,
      bgEdge: this.targetPreset.bgEdge,
      keyColor: '#' + this.lights.key.color.getHexString(),
      keyIntensity: this.lights.key.intensity,
      fillColor: '#' + this.lights.fill.color.getHexString(),
      fillIntensity: this.lights.fill.intensity,
      rimColor: '#' + this.lights.rim.color.getHexString(),
      rimIntensity: this.lights.rim.intensity,
      hemiSky: '#' + this.lights.hemi.color.getHexString(),
      hemiGround: '#' + this.lights.hemi.groundColor.getHexString(),
      hemiIntensity: this.lights.hemi.intensity,
      ambientIntensity: this.lights.ambient.intensity,
      exposure: this.renderer.toneMappingExposure,
    };
  }

  private updateTransition(dt: number): void {
    if (this.themeEnvironmentOverride) return;
    if (this.transitionProgress >= 1 || !this.lights || !this.renderer || !this.scene) return;

    this.transitionProgress = Math.min(this.transitionProgress + dt / TRANSITION_SPEED, 1);
    const t = easeInOutCubic(this.transitionProgress);

    const a = this.currentPreset;
    const b = this.targetPreset;

    this.lights.key.color.lerpColors(new THREE.Color(a.keyColor), new THREE.Color(b.keyColor), t);
    this.lights.key.intensity = lerp(a.keyIntensity, b.keyIntensity, t);

    this.lights.fill.color.lerpColors(new THREE.Color(a.fillColor), new THREE.Color(b.fillColor), t);
    this.lights.fill.intensity = lerp(a.fillIntensity, b.fillIntensity, t);

    this.lights.rim.color.lerpColors(new THREE.Color(a.rimColor), new THREE.Color(b.rimColor), t);
    this.lights.rim.intensity = lerp(a.rimIntensity, b.rimIntensity, t);

    this.lights.hemi.color.lerpColors(new THREE.Color(a.hemiSky), new THREE.Color(b.hemiSky), t);
    this.lights.hemi.groundColor.lerpColors(new THREE.Color(a.hemiGround), new THREE.Color(b.hemiGround), t);
    this.lights.hemi.intensity = lerp(a.hemiIntensity, b.hemiIntensity, t);

    this.lights.ambient.intensity = lerp(a.ambientIntensity, b.ambientIntensity, t);
    this.renderer.toneMappingExposure = lerp(a.exposure, b.exposure, t);

    // Background gradient
    if (this.sceneEnvironment?.config.sky) return;
    if (this.backgroundOverride) {
      // A custom background is an override, so preset/lighting transitions must
      // never replace it, including on the final transition frame.
      this.applyBackgroundOverride();
    } else {
      const bgC = new THREE.Color(a.bgCenter).lerp(new THREE.Color(b.bgCenter), t);
      const bgE = new THREE.Color(a.bgEdge).lerp(new THREE.Color(b.bgEdge), t);
      this.scene.background = makeGradient('#' + bgC.getHexString(), '#' + bgE.getHexString());
    }
  }

  private updateParticles(dt: number): void {
    for (const [id, effect] of this.effects) {
      effect.elapsed += dt;

      if (effect.elapsed >= effect.duration) {
        this.removeEffect(id);
        continue;
      }

      const posAttr = effect.points.geometry.getAttribute('position') as THREE.BufferAttribute;
      const positions = posAttr.array as Float32Array;
      const count = posAttr.count;
      const cfg = effect.config;

      for (let i = 0; i < count; i++) {
        const i3 = i * 3;

        effect.ages[i] += dt;

        // Respawn if past lifetime
        if (effect.ages[i] >= cfg.lifetime) {
          effect.ages[i] = 0;
          positions[i3]     = effect.origin.x + (Math.random() - 0.5) * cfg.spread.x;
          positions[i3 + 1] = effect.origin.y + (Math.random() - 0.5) * cfg.spread.y;
          positions[i3 + 2] = effect.origin.z + (Math.random() - 0.5) * cfg.spread.z;

          effect.velocities[i3]     = cfg.velocity.x + (Math.random() - 0.5) * cfg.velocityRandom.x;
          effect.velocities[i3 + 1] = cfg.velocity.y + (Math.random() - 0.5) * cfg.velocityRandom.y;
          effect.velocities[i3 + 2] = cfg.velocity.z + (Math.random() - 0.5) * cfg.velocityRandom.z;
          continue;
        }

        // Apply velocity + gravity
        effect.velocities[i3 + 1] += cfg.gravity * dt;
        positions[i3]     += effect.velocities[i3] * dt;
        positions[i3 + 1] += effect.velocities[i3 + 1] * dt;
        positions[i3 + 2] += effect.velocities[i3 + 2] * dt;

        // Fireflies: gentle random walk
        if (cfg === PARTICLE_CONFIGS.fireflies) {
          effect.velocities[i3]     += (Math.random() - 0.5) * 0.3 * dt;
          effect.velocities[i3 + 2] += (Math.random() - 0.5) * 0.3 * dt;
        }
      }

      posAttr.needsUpdate = true;

      // Fade out near end of duration
      const fadeStart = effect.duration - 1.0;
      if (effect.elapsed > fadeStart) {
        const fade = 1 - (effect.elapsed - fadeStart) / 1.0;
        (effect.points.material as THREE.PointsMaterial).opacity = cfg.opacity * Math.max(0, fade);
      }
    }
  }

  private removeEffect(effectId: string): void {
    const effect = this.effects.get(effectId);
    if (!effect) return;

    effect.points.geometry.dispose();
    (effect.points.material as THREE.Material).dispose();
    effect.points.removeFromParent();
    this.effects.delete(effectId);
  }

  private notifyPersistChange(): void {
    if (!this.isRestoringSavedState) this.onPersistChange?.();
  }

  private isEnvironmentPreset(value: unknown): value is EnvironmentPreset {
    return typeof value === 'string' && Object.prototype.hasOwnProperty.call(PRESETS, value);
  }

  private normalizeLightingOverride(value: unknown): SavedLightingOverride | null {
    if (!value || typeof value !== 'object') return null;
    const input = value as Record<string, unknown>;
    const result: SavedLightingOverride = {};
    if (typeof input.color === 'string' && input.color.trim()) result.color = input.color;
    if (typeof input.intensity === 'number' && Number.isFinite(input.intensity)) {
      result.intensity = input.intensity;
    }
    if (typeof input.mood === 'string' && Object.prototype.hasOwnProperty.call(MOOD_TINTS, input.mood)) {
      result.mood = input.mood as LightingMood;
    }
    return Object.keys(result).length > 0 ? result : null;
  }

  private normalizeBackgroundOverride(value: unknown): SavedBackgroundOverride | null {
    if (!value || typeof value !== 'object') return null;
    const input = value as Record<string, unknown>;
    const gradient = input.gradient as Record<string, unknown> | undefined;
    if (
      gradient
      && typeof gradient === 'object'
      && typeof gradient.center === 'string'
      && gradient.center.trim()
      && typeof gradient.edge === 'string'
      && gradient.edge.trim()
    ) {
      return { gradient: { center: gradient.center, edge: gradient.edge } };
    }
    if (typeof input.color === 'string' && input.color.trim()) return { color: input.color };
    return null;
  }

  private makeTargetPreset(
    preset: EnvironmentPreset,
    lighting: SavedLightingOverride | null,
  ): PresetConfig {
    return this.applyLightingOverride(PRESETS[preset], lighting);
  }

  private applyLightingOverride(
    base: PresetConfig,
    lighting: SavedLightingOverride | null,
  ): PresetConfig {
    const target = { ...base };
    if (!lighting) return target;

    if (lighting.mood && MOOD_TINTS[lighting.mood]) {
      target.keyColor = MOOD_TINTS[lighting.mood].keyColor;
      target.fillColor = MOOD_TINTS[lighting.mood].fillColor;
    }
    if (lighting.color) target.keyColor = lighting.color;
    if (lighting.intensity !== undefined) target.keyIntensity = lighting.intensity;
    return target;
  }

  private applyBackgroundOverride(): void {
    if (!this.scene || !this.backgroundOverride) return;

    if (this.backgroundOverride.gradient) {
      if (!this.customBackgroundTexture) {
        const { center, edge } = this.backgroundOverride.gradient;
        this.customBackgroundTexture = makeGradient(center, edge);
      }
      this.scene.background = this.customBackgroundTexture;
    } else if (this.backgroundOverride.color) {
      this.scene.background = new THREE.Color(this.backgroundOverride.color);
    }
  }

  private clearBackgroundTexture(): void {
    this.customBackgroundTexture?.dispose();
    this.customBackgroundTexture = null;
  }

  private clearBackgroundOverride(): void {
    this.clearBackgroundTexture();
    this.backgroundOverride = null;
  }
}

// ─── Utilities ──────────────────────────────────────────────────────────────

function lerp(a: number, b: number, t: number): number {
  return a + (b - a) * t;
}

function easeInOutCubic(t: number): number {
  return t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2;
}

function makeGradient(center: string, edge: string): THREE.CanvasTexture {
  const c = document.createElement('canvas');
  c.width = 512;
  c.height = 512;
  const ctx = c.getContext('2d')!;
  const g = ctx.createRadialGradient(256, 256, 0, 256, 256, 360);
  g.addColorStop(0, center);
  g.addColorStop(1, edge);
  ctx.fillStyle = g;
  ctx.fillRect(0, 0, 512, 512);
  return new THREE.CanvasTexture(c);
}
