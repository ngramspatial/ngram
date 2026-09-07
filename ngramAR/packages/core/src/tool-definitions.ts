// @ts-nocheck
// ─── Spatial Tool Definitions ────────────────────────────────────────────────
// Single source of truth for spatial tools. Bindings auto-generate their
// provider-specific schemas (OpenAI function calling, Anthropic tool blocks)
// from these definitions. Add a new tool here → every binding gets it.
export const SPATIAL_TOOL_DEFINITIONS = [
    {
        name: "move_to",
        description: "Walk or move to a target. Use when asked to come closer, move away, walk somewhere, etc.",
        parameters: {
            target: {
                type: "string",
                enum: ["user", "left", "right", "forward", "away", "random"],
                description: "Where to move. 'user' walks toward the user.",
            },
            speed: {
                type: "string",
                enum: ["walk", "fast"],
                description: "Movement speed. Defaults to walk.",
            },
        },
        required: ["target"],
    },
    {
        name: "gesture",
        description: "Perform a physical gesture or animation (wave, nod, dance, cheer, etc.).",
        parameters: {
            gesture: {
                type: "string",
                enum: [
                    "wave",
                    "greet",
                    "nod",
                    "point",
                    "shrug",
                    "celebrate",
                    "explain",
                    "dance",
                    "texting",
                    "coding",
                    "enteringCode",
                    "thinking",
                    "no",
                    "handRaising",
                    "terrified",
                    "drunkWalk",
                    "breakdancing",
                    "twerking",
                    "macarena",
                    "hipHop",
                    "twistDance",
                    "cheering",
                    "clapping",
                ],
                description: "The gesture to perform.",
            },
        },
        required: ["gesture"],
    },
    {
        name: "emote",
        description: "Express an emotion through your body language and presence.",
        parameters: {
            emotion: {
                type: "string",
                enum: [
                    "happy",
                    "excited",
                    "curious",
                    "thoughtful",
                    "concerned",
                    "attentive",
                    "calm",
                ],
                description: "The emotion to express.",
            },
            intensity: {
                type: "number",
                minimum: 0,
                maximum: 1,
                description: "How strongly to express it (0-1). Default 0.6.",
            },
        },
        required: ["emotion"],
    },
    {
        name: "look_at",
        description: "Turn to look at something.",
        parameters: {
            target: {
                type: "string",
                enum: ["user", "away"],
                description: "What to look at.",
            },
        },
        required: ["target"],
    },
    {
        name: "go_idle",
        description: "Return to a relaxed idle stance. Use after completing an action or when there's nothing to do.",
        parameters: {},
        required: [],
    },
    {
        name: "show_panel",
        description: "Show substantial text or code on a readable spatial panel.",
        parameters: {
            panel_id: { type: "string", description: "Stable panel identifier." },
            content: { type: "string", description: "Panel content." },
            title: { type: "string", description: "Optional panel title." },
            panel_type: {
                type: "string",
                enum: ["card", "markdown", "code", "image", "chart", "html"],
                description: "How the shell should render the panel.",
            },
        },
        required: ["panel_id", "content"],
    },
    {
        name: "open_browser",
        description: "Open an HTTP(S) page on the spatial browser surface.",
        parameters: {
            url: { type: "string", description: "HTTP(S) URL." },
            title: { type: "string", description: "Optional display title." },
        },
        required: ["url"],
    },
    {
        name: "spawn_text",
        description: "Place a short piece of text in the scene.",
        parameters: {
            object_id: { type: "string", description: "Stable object identifier." },
            text: { type: "string", description: "Text to place." },
            position: {
                type: "string",
                enum: ["here", "left", "right", "above", "front"],
                description: "Placement relative to the agent.",
            },
        },
        required: ["object_id", "text"],
    },
    {
        name: "set_environment",
        description: "Change the spatial environment preset.",
        parameters: {
            preset: {
                type: "string",
                enum: ["default", "workshop", "cozy", "nature", "space", "party", "focus", "night"],
                description: "Environment preset.",
            },
        },
        required: ["preset"],
    },
    {
        name: "request_capture",
        description: "Ask the user-controlled surface for a current view image.",
        parameters: {
            prompt: { type: "string", description: "Why the current view is needed." },
        },
        required: [],
    },
    {
        name: "generate_motion",
        description: "Request a novel body motion from the configured external GPU provider.",
        parameters: {
            prompt: { type: "string", description: "Physical motion to generate." },
            duration_seconds: {
                type: "number",
                minimum: 0.5,
                maximum: 30,
                description: "Approximate motion duration.",
            },
            root_target: {
                type: "string",
                enum: ["stationary", "user", "forward", "left", "right"],
                description: "High-level root motion constraint.",
            },
            loop: { type: "boolean", description: "Whether the resulting clip should loop." },
        },
        required: ["prompt"],
    },
];
// ─── Provider Schema Generators ─────────────────────────────────────────────
/**
 * Convert tool definitions to OpenAI function-calling format.
 */
export function toOpenAITools() {
    return SPATIAL_TOOL_DEFINITIONS.map((tool) => ({
        type: "function",
        function: {
            name: tool.name,
            description: tool.description,
            parameters: {
                type: "object",
                properties: Object.fromEntries(Object.entries(tool.parameters).map(([key, param]) => {
                    const prop = {
                        type: param.type,
                        description: param.description,
                    };
                    if (param.enum)
                        prop.enum = param.enum;
                    if (param.minimum !== undefined)
                        prop.minimum = param.minimum;
                    if (param.maximum !== undefined)
                        prop.maximum = param.maximum;
                    return [key, prop];
                })),
                required: tool.required,
            },
        },
    }));
}
/**
 * Convert tool definitions to Anthropic tool-use format.
 */
export function toAnthropicTools() {
    return SPATIAL_TOOL_DEFINITIONS.map((tool) => ({
        name: tool.name,
        description: tool.description,
        input_schema: {
            type: "object",
            properties: Object.fromEntries(Object.entries(tool.parameters).map(([key, param]) => {
                const prop = {
                    type: param.type,
                    description: param.description,
                };
                if (param.enum)
                    prop.enum = param.enum;
                if (param.minimum !== undefined)
                    prop.minimum = param.minimum;
                if (param.maximum !== undefined)
                    prop.maximum = param.maximum;
                return [key, prop];
            })),
            required: tool.required,
        },
    }));
}
