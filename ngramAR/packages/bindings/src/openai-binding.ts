// @ts-nocheck
import { isFigmentTool, figmentToolPayload } from "@ngram-ar/core";
import { createAction, toOpenAITools } from "@ngram-ar/core";
const MAX_HISTORY = 50;
// Auto-generated from the single source of truth in @ngram-ar/core
const SPATIAL_TOOLS = toOpenAITools();
const MOVE_OFFSETS = {
    away: { x: 0, y: 0, z: 2 },
    left: { x: -1.5, y: 0, z: 0 },
    right: { x: 1.5, y: 0, z: 0 },
    forward: { x: 0, y: 0, z: -1.5 },
};
export class OpenAIBinding {
    config;
    baseUrl;
    model;
    apiKey;
    systemPrompt = "";
    history = [];
    sceneAnchors = [];
    proactiveCallback = null;
    worldPending = new Map();
    turnEpoch = 0;
    turnController = null;
    onProactiveAction(callback) { this.proactiveCallback = callback; }
    cancelActiveTurn() {
        this.turnEpoch++;
        this.turnController?.abort();
        for (const pending of this.worldPending.values()) { clearTimeout(pending.timeout); pending.reject(new Error('Turn cancelled')); }
        this.worldPending.clear();
    }
    constructor(config) {
        this.config = config;
        this.baseUrl = (config.baseUrl ?? "").replace(/\/+$/, "");
        this.model =
            config.model?.trim() ||
                process.env["NGRAM_AR_INFERENCE_MODEL"]?.trim() ||
                "llama3.2";
        this.apiKey = config.apiKey?.trim() ?? "";
    }
    async start(systemPrompt) {
        if (!this.baseUrl || !this.apiKey) {
            throw new Error("ngram AR requires a resolved ngram inference gateway binding (baseUrl + Bearer token). " +
                "Set NGRAM_INFERENCE_GATEWAY_BASE_URL and INFERENCE_GATEWAY_TOKEN.");
        }
        this.systemPrompt = systemPrompt;
        this.history = [];
    }
    async stop() {
        this.cancelActiveTurn();
        this.history = [];
    }
    async injectBehaviorPrompt(prompt, context) {
        const contextStr = Object.entries(context)
            .filter(([k]) => k !== "behaviorId")
            .map(([k, v]) => `${k}: ${typeof v === "number" ? v.toFixed(2) : v}`)
            .join(", ");
        const nudge = contextStr
            ? `[Spatial Awareness | ${contextStr}] ${prompt}`
            : `[Spatial Awareness] ${prompt}`;
        const sid = context["sessionId"] ?? "behavior";
        const messages = this.buildMessages();
        messages.push({ role: "system", content: nudge });
        try {
            const response = await this.callApi(messages);
            const choice = response.choices[0];
            if (!choice)
                return [];
            const actions = [];
            const text = choice.message.content?.trim();
            const toolCalls = choice.message.tool_calls;
            if (text) {
                this.addMessage({ role: "agent", content: text, timestamp: Date.now() });
                actions.push(createAction("action:speak", sid, { text }));
            }
            if (toolCalls) {
                for (const tc of toolCalls) {
                    actions.push(...this.resolveToolCall(tc, sid));
                }
            }
            return actions;
        }
        catch (e) {
            console.warn("[openai-binding] Behavior prompt failed:", e);
            return [];
        }
    }
    async handleEvent(event) {
        const sid = event.sessionId;
        switch (event.type) {
            case "event:action_completed": {
                const pending = this.worldPending.get(event.completedActionId);
                if (pending && event.status !== "accepted") {
                    clearTimeout(pending.timeout); this.worldPending.delete(event.completedActionId);
                    pending.resolve({ status: event.status, result: event.result, error: event.error });
                }
                return [];
            }
            case "event:cancel_turn":
                this.cancelActiveTurn();
                return [createAction('action:turn_cancelled', sid, { reason: 'user' })];
            case "event:user_speech":
                return event.isFinal ? this.handleSpeech(event.text, sid) : [];
            case "event:user_proximity":
                if (event.approaching && event.distance < 1.0) {
                    return [
                        createAction("action:emote", sid, {
                            emotion: "attentive",
                            intensity: 0.6,
                        }),
                        createAction("action:look_at", sid, {
                            target: "user",
                        }),
                    ];
                }
                return [];
            case "event:user_gesture":
                return this.handleGesture(event.gesture, sid);
            case "event:user_gaze":
                if (event.lookingAtAgent) {
                    return [
                        createAction("action:look_at", sid, {
                            target: "user",
                        }),
                        createAction("action:emote", sid, {
                            emotion: "attentive",
                            intensity: 0.4,
                        }),
                    ];
                }
                return [];
            case "event:shell_ready":
                return this.handleShellReady(sid);
            case "event:scene_ready":
                this.sceneAnchors = event.anchors;
                return [
                    createAction("action:look_at", sid, {
                        target: "user",
                    }),
                ];
            default:
                return [];
        }
    }
    handleGesture(gesture, sid) {
        switch (gesture) {
            case "wave":
                return [
                    createAction("action:gesture", sid, {
                        gesture: "wave",
                    }),
                    createAction("action:emote", sid, {
                        emotion: "happy",
                        intensity: 0.7,
                    }),
                ];
            case "thumbs_up":
                return [
                    createAction("action:emote", sid, {
                        emotion: "happy",
                        intensity: 0.8,
                    }),
                    createAction("action:gesture", sid, {
                        gesture: "nod",
                    }),
                ];
            case "open_palm":
                return [createAction("action:go_idle", sid, {})];
            case "point":
                return [
                    createAction("action:look_at", sid, {
                        target: "user",
                    }),
                    createAction("action:emote", sid, {
                        emotion: "curious",
                        intensity: 0.6,
                    }),
                ];
            default:
                return [];
        }
    }
    async handleShellReady(sessionId) {
        return [
            createAction("action:spawn", sessionId, {}),
            createAction("action:go_idle", sessionId, {}),
        ];
    }
    async handleSpeech(text, sessionId) {
        this.cancelActiveTurn();
        this.turnController = new AbortController();
        const epoch = this.turnEpoch;
        this.addMessage({ role: "user", content: text, timestamp: Date.now() });
        const messages = this.buildMessages();
        let response;
        try { response = await this.callApi(messages); }
        catch (error) { if (epoch !== this.turnEpoch) return []; throw error; }
        if (epoch !== this.turnEpoch) return [];
        if (response.choices[0]?.message.tool_calls?.some(tc => tc.function.name === 'world' || isFigmentTool(tc.function.name))) {
            return this.handleWorldTurn(messages, response, sessionId, epoch);
        }
        const choice = response.choices[0];
        if (!choice)
            return [];
        const actions = [];
        const spokenText = choice.message.content?.trim();
        const toolCalls = choice.message.tool_calls;
        if (spokenText) {
            this.addMessage({
                role: "agent",
                content: spokenText,
                timestamp: Date.now(),
            });
            actions.push(createAction("action:speak", sessionId, {
                text: spokenText,
            }));
            actions.push(createAction("action:look_at", sessionId, {
                target: "user",
            }));
            const codeBlocks = this.extractCodeBlocks(spokenText);
            for (const block of codeBlocks) {
                actions.push(createAction("action:show_panel", sessionId, {
                    panel: {
                        id: `code-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
                        type: "code",
                        title: block.language || "Code",
                        content: block.code,
                    },
                }));
            }
        }
        if (toolCalls && toolCalls.length > 0) {
            for (const tc of toolCalls) {
                const toolActions = this.resolveToolCall(tc, sessionId);
                actions.push(...toolActions);
            }
            if (!spokenText) {
                this.addMessage({
                    role: "agent",
                    content: "[performed spatial action]",
                    timestamp: Date.now(),
                });
            }
        }
        if (actions.length === 0) {
            actions.push(createAction("action:speak", sessionId, {
                text: "I have nothing to say.",
            }));
        }
        return actions;
    }
    resolveToolCall(tc, sessionId) {
        const name = tc.function.name;
        let args = {};
        try {
            args = JSON.parse(tc.function.arguments);
        }
        catch {
            return [];
        }
        if (isFigmentTool(name)) return [createAction('action:world', sessionId, { command: 'figment', payload: figmentToolPayload(name, args) })];
        switch (name) {
            case "world":
                return [createAction('action:world', sessionId, { command: args.command, payload: args.payload ?? {} })];
            case "move_to": {
                const target = args.target ?? "forward";
                const speed = args.speed ?? "walk";
                if (target === "user") {
                    return [
                        createAction("action:move_to", sessionId, {
                            target: "user",
                            speed: speed,
                        }),
                    ];
                }
                if (target === "random") {
                    const angle = Math.random() * Math.PI * 2;
                    const dist = 0.8 + Math.random() * 1.2;
                    return [
                        createAction("action:move_to", sessionId, {
                            target: {
                                x: Math.cos(angle) * dist,
                                y: 0,
                                z: Math.sin(angle) * dist,
                            },
                            speed: speed,
                        }),
                    ];
                }
                const offset = MOVE_OFFSETS[target] ?? MOVE_OFFSETS["forward"];
                return [
                    createAction("action:move_to", sessionId, {
                        target: offset,
                        speed: speed,
                    }),
                ];
            }
            case "gesture":
                return [
                    createAction("action:gesture", sessionId, {
                        gesture: args.gesture ?? "wave",
                    }),
                ];
            case "emote":
                return [
                    createAction("action:emote", sessionId, {
                        emotion: args.emotion ?? "happy",
                        intensity: args.intensity ?? 0.6,
                    }),
                ];
            case "look_at":
                return [
                    createAction("action:look_at", sessionId, {
                        target: args.target ?? "user",
                    }),
                ];
            case "go_idle":
                return [createAction("action:go_idle", sessionId, {})];
            case "show_panel":
                return [createAction("action:show_panel", sessionId, {
                    panel: {
                        id: String(args.panel_id ?? "panel").slice(0, 80),
                        type: args.panel_type ?? "markdown",
                        title: String(args.title ?? "").slice(0, 160),
                        content: String(args.content ?? "").slice(0, 20000),
                        position: "auto",
                    },
                })];
            case "open_browser": {
                const url = String(args.url ?? "");
                if (!url.startsWith("https://") && !url.startsWith("http://"))
                    return [];
                return [createAction("action:open_browser", sessionId, {
                    url,
                    title: String(args.title ?? "").slice(0, 160),
                })];
            }
            case "spawn_text":
                return [createAction("action:spawn_text", sessionId, {
                    objectId: String(args.object_id ?? "text").slice(0, 80),
                    text: String(args.text ?? "").slice(0, 2000),
                    position: args.position ?? "front",
                })];
            case "set_environment":
                return [createAction("action:set_environment", sessionId, {
                    preset: args.preset ?? "default",
                })];
            case "request_capture":
                return [createAction("action:request_capture", sessionId, {
                    prompt: String(args.prompt ?? "").slice(0, 500),
                })];
            case "generate_motion":
                return [createAction("action:generate_motion", sessionId, {
                    requestId: crypto.randomUUID(),
                    prompt: String(args.prompt ?? "").slice(0, 1000),
                    durationSeconds: args.duration_seconds ?? 4,
                    constraints: { rootTarget: args.root_target ?? "stationary" },
                    loop: Boolean(args.loop),
                })];
            default:
                return [];
        }
    }
    async worldRequest(action) {
        if (!this.proactiveCallback) throw new Error('This adapter has no live renderer connection');
        return new Promise((resolve, reject) => {
            const timeout = setTimeout(() => { this.worldPending.delete(action.actionId); reject(new Error('World confirmation timed out; execution unknown')); }, action.command === 'figment' && ['publish','export','import','place'].includes(action.payload?.command) ? 120000 : 8000);
            this.worldPending.set(action.actionId, { resolve, reject, timeout });
            try { this.proactiveCallback([action]); } catch (error) { clearTimeout(timeout); this.worldPending.delete(action.actionId); reject(error); }
        });
    }
    async handleWorldTurn(messages, response, sessionId, epoch) {
        const repeated = new Map();
        for (let round = 0; round < 64 && epoch === this.turnEpoch; round++) {
            const message = response.choices[0]?.message;
            if (!message) return [];
            const calls = message.tool_calls ?? [];
            if (!calls.length) {
                const text = message.content?.trim();
                if (!text) return [];
                this.addMessage({ role: 'agent', content: text, timestamp: Date.now() });
                return [createAction('action:speak', sessionId, { text })];
            }
            messages.push(message);
            for (const call of calls) {
                if (epoch !== this.turnEpoch) return [];
                const fingerprint = call.function.name + call.function.arguments;
                const count = (repeated.get(fingerprint) ?? 0) + 1; repeated.set(fingerprint, count);
                if (count > 3) return [createAction('action:speak', sessionId, { text: 'I stopped because the same spatial request kept repeating. The creation is available to inspect and adjust.' })];
                let result;
                try {
                    const actions = this.resolveToolCall(call, sessionId);
                    if (call.function.name === 'world' || isFigmentTool(call.function.name)) result = await this.worldRequest(actions[0]);
                    else { this.proactiveCallback?.(actions); result = { status: 'accepted', detail: 'Completion not confirmed' }; }
                } catch (error) { if (epoch !== this.turnEpoch) return []; result = { status: 'failed', error: String(error.message) }; }
                messages.push({ role: 'tool', tool_call_id: call.id, content: JSON.stringify(result) });
            }
            if (epoch !== this.turnEpoch) return [];
            try { response = await this.callApi(messages); }
            catch (error) { if (epoch !== this.turnEpoch) return []; throw error; }
        }
        return epoch === this.turnEpoch ? [createAction('action:speak', sessionId, { text: 'The spatial work reached this turn’s execution limit. The current creation has been kept.' })] : [];
    }
    buildMessages() {
        const msgs = [
            { role: "system", content: this.buildSystemContent() },
        ];
        for (const m of this.history) {
            msgs.push({
                role: m.role === "agent" ? "assistant" : m.role,
                content: m.content,
            });
        }
        return msgs;
    }
    buildSystemContent() {
        let content = this.systemPrompt || this.config.system || "";
        if (this.sceneAnchors.length > 0) {
            const anchors = this.sceneAnchors
                .map((a) => `${a.label} (${a.id})`)
                .join(", ");
            content += `\n\nScene anchors: ${anchors}`;
        }
        return content;
    }
    async callApi(messages) {
        const body = {
            model: this.model,
            messages,
            tools: SPATIAL_TOOLS,
            tool_choice: "auto",
            ...(this.config.options ?? {}),
        };
        const res = await fetch(`${this.baseUrl}/chat/completions`, {
            method: "POST",
            headers: {
                "Content-Type": "application/json",
                Authorization: `Bearer ${this.apiKey}`,
            },
            body: JSON.stringify(body),
            signal: this.turnController?.signal,
        });
        if (!res.ok) {
            const text = await res.text();
            throw new Error(`OpenAI API error ${res.status}: ${text}`);
        }
        return (await res.json());
    }
    extractCodeBlocks(text) {
        const blocks = [];
        const re = /```(\w*)\n([\s\S]*?)```/g;
        let match;
        while ((match = re.exec(text)) !== null) {
            blocks.push({ language: match[1] ?? "", code: match[2] ?? "" });
        }
        return blocks;
    }
    addMessage(msg) {
        this.history.push(msg);
        if (this.history.length > MAX_HISTORY) {
            this.history = this.history.slice(-MAX_HISTORY);
        }
    }
}
