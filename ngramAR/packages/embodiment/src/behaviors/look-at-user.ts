// @ts-nocheck
import type { Behavior, BehaviorContext, BehaviorOutput } from "./behavior.js";

export class LookAtUserBehavior implements Behavior {
  readonly name = "look-at-user";
  readonly priority = 10;

  update(context: BehaviorContext): BehaviorOutput | null {
    const { scene } = context;
    if (!scene.userTransform) return null;

    const userPos = scene.userTransform.position;
    return {
      gazeTarget: {
        x: userPos.x,
        y: userPos.y + 0.1,
        z: userPos.z,
      },
    };
  }

  reset(): void {}
}
