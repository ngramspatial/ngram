// @ts-nocheck
import type { Behavior, BehaviorContext, BehaviorOutput } from "./behavior.js";

const BREATHE_AMPLITUDE = 0.005;
const BREATHE_FREQUENCY = 1.5;

export class IdleBreatheBehavior implements Behavior {
  readonly name = "idle-breathe";
  readonly priority = 1;

  update(context: BehaviorContext): BehaviorOutput | null {
    if (context.embodiment.animation !== "idle") return null;

    const pos = context.embodiment.transform.position;
    const offset =
      Math.sin(context.elapsedTime * BREATHE_FREQUENCY * Math.PI * 2) *
      BREATHE_AMPLITUDE;

    return {
      moveTarget: {
        x: pos.x,
        y: pos.y + offset,
        z: pos.z,
      },
    };
  }

  reset(): void {}
}
