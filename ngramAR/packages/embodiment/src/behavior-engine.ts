// @ts-nocheck
import type {
  Behavior,
  BehaviorContext,
  BehaviorOutput,
} from "./behaviors/behavior.js";
import { GestureRespondBehavior } from "./behaviors/gesture-respond.js";

export class BehaviorEngine {
  private behaviors: Behavior[];

  constructor(behaviors: Behavior[]) {
    this.behaviors = [...behaviors];
  }

  update(context: BehaviorContext): BehaviorOutput {
    const outputs: { priority: number; output: BehaviorOutput }[] = [];

    for (const behavior of this.behaviors) {
      const result = behavior.update(context);
      if (result) {
        outputs.push({ priority: behavior.priority, output: result });
      }
    }

    outputs.sort((a, b) => b.priority - a.priority);
    return this.merge(outputs);
  }

  addBehavior(behavior: Behavior): void {
    this.behaviors.push(behavior);
  }

  removeBehavior(name: string): void {
    this.behaviors = this.behaviors.filter((b) => b.name !== name);
  }

  triggerGesture(gesture: string): void {
    const found = this.behaviors.find((b) => b.name === "gesture-respond");
    if (found && found instanceof GestureRespondBehavior) {
      found.onGesture(gesture);
    }
  }

  private merge(
    outputs: { priority: number; output: BehaviorOutput }[],
  ): BehaviorOutput {
    const merged: BehaviorOutput = {};

    for (const { output } of outputs) {
      if (merged.gazeTarget === undefined && output.gazeTarget !== undefined) {
        merged.gazeTarget = output.gazeTarget;
      }
      if (merged.moveTarget === undefined && output.moveTarget !== undefined) {
        merged.moveTarget = output.moveTarget;
      }
      if (merged.animation === undefined && output.animation !== undefined) {
        merged.animation = output.animation;
      }
      if (merged.expression === undefined && output.expression !== undefined) {
        merged.expression = output.expression;
      }
      if (
        merged.expressionIntensity === undefined &&
        output.expressionIntensity !== undefined
      ) {
        merged.expressionIntensity = output.expressionIntensity;
      }
      if (merged.effect === undefined && output.effect !== undefined) {
        merged.effect = output.effect;
      }
    }

    return merged;
  }
}
