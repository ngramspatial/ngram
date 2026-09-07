// @ts-nocheck
import { createAction } from "@ngram-ar/core";
export class BehaviorRuntime {
    behaviors;
    state = new Map();
    activeBehaviorId = null;
    activePriority = 0;
    activeUntil = 0;
    constructor(behaviors) {
        this.behaviors = [...behaviors].sort((a, b) => b.priority - a.priority);
        for (const b of this.behaviors) {
            this.state.set(b.id, { lastFired: 0, fireCount: 0 });
        }
    }
    handleTrigger(event, sessionId) {
        const behavior = this.behaviors.find((b) => b.id === event.behaviorId);
        if (!behavior)
            return null;
        const now = Date.now();
        const st = this.state.get(behavior.id);
        const cooldownMs = (behavior.cooldown ?? 0) * 1000;
        if (cooldownMs > 0 && now - st.lastFired < cooldownMs)
            return null;
        if (this.activeBehaviorId && now < this.activeUntil && behavior.priority < this.activePriority) {
            return null;
        }
        st.lastFired = now;
        st.fireCount++;
        this.activeBehaviorId = behavior.id;
        this.activePriority = behavior.priority;
        this.activeUntil = now + 5000;
        switch (behavior.mode) {
            case "reflexive":
            case "reactive":
                return this.buildActionResult(behavior, event, sessionId);
            case "deliberative":
                return this.buildPromptResult(behavior, event);
            default:
                return null;
        }
    }
    getBehaviors() {
        return this.behaviors;
    }
    buildActionResult(behavior, event, sessionId) {
        if (!behavior.action)
            return null;
        const actionType = behavior.action.type;
        const params = { ...behavior.action.params };
        if (params["target"] === "trigger.object" && event.context["objectId"]) {
            params["target"] = String(event.context["objectId"]);
        }
        if (params["target"] === "trigger.user") {
            params["target"] = "user";
        }
        const action = createAction(actionType, sessionId, params);
        return { type: "action", actions: [action] };
    }
    buildPromptResult(behavior, event) {
        if (!behavior.prompt)
            return null;
        return {
            type: "prompt",
            prompt: behavior.prompt,
            context: {
                behaviorId: behavior.id,
                triggerType: event.triggerType,
                ...event.context,
            },
        };
    }
}
