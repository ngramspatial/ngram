// ─── Behavior System ─────────────────────────────────────────────────────────
// Autonomous spatial programs that give agents agency over their 3D environment.
// Behaviors bridge embodiment (body), intelligence (mind), and environment (space).
//
// Three execution modes:
//   Reflexive:    Direct SpatialAction, no LLM. Instant.
//   Reactive:     Direct SpatialAction triggered by spatial conditions.
//   Deliberative: Injects a prompt into the LLM. Agent decides whether to act.

// ─── Trigger Types ──────────────────────────────────────────────────────────

export type BehaviorTriggerType =
  | "proximity"
  | "gaze"
  | "idle_timeout"
  | "gesture_detected"
  | "scene_change"
  | "silence"
  | "schedule";

export interface BehaviorTriggerConfig {
  type: BehaviorTriggerType;
  params: Record<string, unknown>;
}

// ─── Execution Modes ────────────────────────────────────────────────────────

export type BehaviorMode = "reflexive" | "reactive" | "deliberative";

// ─── Action Config (for reflexive/reactive) ─────────────────────────────────

export interface BehaviorActionConfig {
  type: string;
  params: Record<string, unknown>;
}

// ─── Behavior Definition ────────────────────────────────────────────────────

export interface BehaviorDefinition {
  id: string;
  description?: string;
  trigger: BehaviorTriggerConfig;
  priority: number;
  /** Cooldown in seconds before this behavior can fire again */
  cooldown?: number;
  mode: BehaviorMode;
  /** Direct action for reflexive/reactive modes */
  action?: BehaviorActionConfig;
  /** Prompt nudge for deliberative mode */
  prompt?: string;
  /** Whether this behavior is enabled (defaults to true) */
  enabled?: boolean;
}

// ─── Behavior Pack ──────────────────────────────────────────────────────────

export interface BehaviorPackDefinition {
  name: string;
  description?: string;
  behaviors: BehaviorDefinition[];
}

// ─── Runtime Result ─────────────────────────────────────────────────────────

export interface BehaviorActionResult {
  type: "action";
  actions: import("./protocol.js").SpatialAction[];
}

export interface BehaviorPromptResult {
  type: "prompt";
  prompt: string;
  context: Record<string, unknown>;
}

export type BehaviorResult = BehaviorActionResult | BehaviorPromptResult | null;
