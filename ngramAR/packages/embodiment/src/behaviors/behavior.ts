import type { AnimationState, EmbodimentState, SceneState, Vec3 } from "@ngram-ar/core";

export interface BehaviorContext {
  scene: SceneState;
  embodiment: EmbodimentState;
  deltaTime: number;
  elapsedTime: number;
}

export interface BehaviorOutput {
  gazeTarget?: Vec3 | null;
  moveTarget?: Vec3 | null;
  animation?: AnimationState;
  expression?: string;
  expressionIntensity?: number;
  effect?: {
    type: string;
    intensity: number;
    duration: number;
  };
}

export interface Behavior {
  readonly name: string;
  readonly priority: number;
  update(context: BehaviorContext): BehaviorOutput | null;
  reset(): void;
}
