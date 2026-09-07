export { AnimationStateMachine } from "./animation-state-machine.js";
export { BehaviorEngine } from "./behavior-engine.js";

export type {
  Behavior,
  BehaviorContext,
  BehaviorOutput,
} from "./behaviors/behavior.js";

export { LookAtUserBehavior } from "./behaviors/look-at-user.js";
export { IdleBreatheBehavior } from "./behaviors/idle-breathe.js";
export { AnchorToSurfaceBehavior } from "./behaviors/anchor-to-surface.js";
export { ProximityGreetBehavior } from "./behaviors/proximity-greet.js";
export { GestureRespondBehavior } from "./behaviors/gesture-respond.js";

export {
  VISEME_MAP,
  VISEME_NAMES,
  generateBasicVisemes,
} from "./viseme-map.js";
