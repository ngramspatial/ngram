// @ts-nocheck
import type { Behavior, BehaviorContext, BehaviorOutput } from "./behavior.js";

const GREET_DISTANCE = 1.5;
const COOLDOWN_SECONDS = 30;

export class ProximityGreetBehavior implements Behavior {
  readonly name = "proximity-greet";
  readonly priority = 15;

  private wasClose = false;
  private lastGreetTime = -Infinity;

  update(context: BehaviorContext): BehaviorOutput | null {
    const { scene, embodiment, elapsedTime } = context;
    if (!scene.userTransform) {
      this.wasClose = false;
      return null;
    }

    const distance = this.distanceBetween(
      embodiment.transform.position,
      scene.userTransform.position,
    );

    const isClose = distance < GREET_DISTANCE;

    if (isClose && !this.wasClose) {
      this.wasClose = true;

      if (elapsedTime - this.lastGreetTime < COOLDOWN_SECONDS) return null;

      this.lastGreetTime = elapsedTime;
      return {
        animation: "waving",
        expression: "happy",
        expressionIntensity: 0.8,
      };
    }

    if (!isClose) {
      this.wasClose = false;
    }

    return null;
  }

  reset(): void {
    this.wasClose = false;
    this.lastGreetTime = -Infinity;
  }

  private distanceBetween(
    a: { x: number; y: number; z: number },
    b: { x: number; y: number; z: number },
  ): number {
    const dx = a.x - b.x;
    const dy = a.y - b.y;
    const dz = a.z - b.z;
    return Math.sqrt(dx * dx + dy * dy + dz * dz);
  }
}
