// @ts-nocheck
const BLEND_DURATIONS = {
    "idle->talking": 0.3,
    "talking->idle": 0.5,
    "idle->waving": 0.2,
    "waving->idle": 0.3,
    "idle->thinking": 0.4,
    "thinking->idle": 0.4,
    "thinking->talking": 0.3,
    "talking->thinking": 0.3,
    "idle->gesturing": 0.2,
    "gesturing->idle": 0.3,
    "idle->walking": 0.25,
    "walking->idle": 0.35,
    "idle->reacting": 0.15,
    "reacting->idle": 0.3,
    "idle->appearing": 0.0,
    "appearing->idle": 0.5,
    "idle->disappearing": 0.4,
    "disappearing->idle": 0.0,
};
const DEFAULT_BLEND_DURATION = 0.3;
export class AnimationStateMachine {
    state;
    constructor(initialState = "idle") {
        this.state = initialState;
    }
    get current() {
        return this.state;
    }
    transition(to) {
        const from = this.state;
        const key = `${from}->${to}`;
        const blendDuration = BLEND_DURATIONS[key] ?? DEFAULT_BLEND_DURATION;
        this.state = to;
        return { from, to, blendDuration };
    }
    canTransition(_to) {
        return true;
    }
    getIdleVariant() {
        return "idle";
    }
}
