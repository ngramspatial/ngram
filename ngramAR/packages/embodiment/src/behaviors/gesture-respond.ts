// @ts-nocheck
import type { Behavior, BehaviorContext, BehaviorOutput } from "./behavior.js";

const REACTION_DURATION = 1.5;

interface PendingReaction {
  output: BehaviorOutput;
  remaining: number;
}

export class GestureRespondBehavior implements Behavior {
  readonly name = "gesture-respond";
  readonly priority = 12;

  private queue: PendingReaction[] = [];

  onGesture(gesture: string): BehaviorOutput {
    let output: BehaviorOutput;

    switch (gesture) {
      case "wave":
        output = { animation: "waving", expression: "happy" };
        break;
      case "thumbs_up":
        output = {
          expression: "happy",
          expressionIntensity: 1.0,
          effect: { type: "pulse", intensity: 0.5, duration: 0.5 },
        };
        break;
      case "point":
        output = { gazeTarget: null };
        break;
      default:
        output = { expression: "curious", expressionIntensity: 0.5 };
        break;
    }

    this.queue.push({ output, remaining: REACTION_DURATION });
    return output;
  }

  update(context: BehaviorContext): BehaviorOutput | null {
    if (this.queue.length === 0) return null;

    const front = this.queue[0];
    front.remaining -= context.deltaTime;

    if (front.remaining <= 0) {
      this.queue.shift();
      return this.queue.length > 0 ? this.queue[0].output : null;
    }

    return front.output;
  }

  reset(): void {
    this.queue = [];
  }
}
