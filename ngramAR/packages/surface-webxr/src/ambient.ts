// @ts-nocheck
import * as THREE from 'three';

type ModeChangeCallback = (mode: 'active' | 'ambient' | 'sleep') => void;

export class AmbientBehavior {
  private mode: 'active' | 'ambient' | 'sleep' = 'active';
  private lastInteractionTime = Date.now();
  private lastAmbientAction = 0;
  private nextAmbientDelay = 15;
  private onModeChange: ModeChangeCallback | null = null;

  private readonly IDLE_TO_AMBIENT_MS = 120_000;
  private readonly AMBIENT_TO_SLEEP_MS = 300_000;
  private readonly PROXIMITY_ACTIVE_DIST = 1.5;

  onMode(cb: ModeChangeCallback): void {
    this.onModeChange = cb;
  }

  recordInteraction(): void {
    this.lastInteractionTime = Date.now();
    if (this.mode !== 'active') {
      this.mode = 'active';
      this.onModeChange?.('active');
    }
  }

  getMode(): string {
    return this.mode;
  }

  getTimeSinceLastInteraction(): number {
    return (Date.now() - this.lastInteractionTime) / 1000;
  }

  update(
    dt: number,
    elapsed: number,
    avatarPos: THREE.Vector3,
    cameraPos: THREE.Vector3,
    setGaze: (target: THREE.Vector3 | null, weight?: number) => void,
    setAnimation: (state: string) => void,
  ): void {
    const now = Date.now();
    const sinceLast = now - this.lastInteractionTime;
    const dist = cameraPos.distanceTo(avatarPos);

    if (dist < this.PROXIMITY_ACTIVE_DIST && this.mode !== 'active') {
      this.recordInteraction();
      return;
    }

    if (this.mode === 'active' && sinceLast > this.IDLE_TO_AMBIENT_MS) {
      this.mode = 'ambient';
      this.onModeChange?.('ambient');
      setAnimation('idle');
    }

    if (this.mode === 'ambient' && sinceLast > this.AMBIENT_TO_SLEEP_MS) {
      this.mode = 'sleep';
      this.onModeChange?.('sleep');
      setGaze(null);
    }

    if (this.mode === 'ambient') {
      this.updateAmbient(elapsed, avatarPos, cameraPos, setGaze);
    }
  }

  private updateAmbient(
    elapsed: number,
    avatarPos: THREE.Vector3,
    cameraPos: THREE.Vector3,
    setGaze: (target: THREE.Vector3 | null, weight?: number) => void,
  ): void {
    if (elapsed - this.lastAmbientAction < this.nextAmbientDelay) return;

    this.lastAmbientAction = elapsed;
    this.nextAmbientDelay = 15 + Math.random() * 30;

    const roll = Math.random();
    if (roll < 0.4) {
      const offset = new THREE.Vector3(
        (Math.random() - 0.5) * 3,
        0.8 + Math.random() * 1.5,
        (Math.random() - 0.5) * 3,
      );
      setGaze(avatarPos.clone().add(offset), 0.4);
      setTimeout(() => setGaze(cameraPos, 0.6), 3000);
    } else if (roll < 0.7) {
      setGaze(cameraPos, 0.3);
    }
  }
}
