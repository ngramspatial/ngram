// @ts-nocheck
import { SPATIAL_TOOL_DEFINITIONS } from "@ngram-ar/core";
/**
 * Markdown block injected into the Python `Entity` perceive path so the same brain
 * sees what the in-gateway OpenAI binding would: shell prompt slice, spatial affordances,
 * and loaded behaviors.
 */
export function buildArCognitionContextMarkdown(args) {
    const { shellSlug, shell, resolvedBehaviors, bindingSystemPrompt } = args;
    const lines = [];
    lines.push("### Surface");
    lines.push(`- **Shell**: ${shell.name} (\`${shellSlug}\`)`);
    if (shell.description?.trim()) {
        lines.push(`- **Description**: ${shell.description.trim()}`);
    }
    lines.push(`- **3D model**: \`${shell.model}\`${shell.scale != null ? ` · scale ${shell.scale}` : ""}`);
    const ap = shell.animationPack;
    if (typeof ap === "string") {
        lines.push(`- **Animation pack**: ${ap}`);
    }
    else if (ap && typeof ap === "object") {
        lines.push(`- **Animation pack**: ${Object.keys(ap).join(", ")}`);
    }
    if (shell.behaviorPack?.length) {
        lines.push(`- **Behavior pack ids**: ${shell.behaviorPack.join(", ")}`);
    }
    lines.push("");
    lines.push("### Spatial affordances (intent vocabulary)");
    lines.push("In the AR-only OpenAI loop these are **function tools** on the inference gateway. " +
        "With the **Python entity bridge**, the harness exposes the same tools with an **ar_** prefix, plus " +
        "**ar_speak**, **ar_terminal**, **ar_hide_panel**, and **ar_draw_annotation** — call them via the normal tool API; " +
        "they queue the same protocol actions below. Your final reply text is still spoken as TTS after tool rounds.");
    lines.push("");
    for (const t of SPATIAL_TOOL_DEFINITIONS) {
        const params = Object.entries(t.parameters)
            .map(([k, p]) => {
            const bits = [k, p.type];
            if (p.enum?.length)
                bits.push(`one of: ${p.enum.join("|")}`);
            return `${bits.join(": ")} — ${p.description}`;
        })
            .join("; ");
        lines.push(`- **${t.name}** — ${t.description}`);
        if (params)
            lines.push(`  - Parameters: ${params}`);
    }
    lines.push("");
    lines.push("### Loaded behaviors (embodiment)");
    if (resolvedBehaviors.length === 0) {
        lines.push("(none loaded)");
    }
    else {
        for (const b of resolvedBehaviors) {
            const bits = [`**${b.id}**`, `mode=${b.mode}`];
            if (b.description?.trim())
                bits.push(b.description.trim());
            lines.push(`- ${bits.join(" — ")}`);
            lines.push(`  - Trigger: ${b.trigger.type} ${JSON.stringify(b.trigger.params)}`);
            if (b.mode === "deliberative" && b.prompt?.trim()) {
                lines.push(`  - Deliberative nudge: ${b.prompt.trim().slice(0, 400)}`);
            }
        }
    }
    lines.push("");
    lines.push("### Shell system prompt (AR channel)");
    lines.push("The following is the same system prompt slice the in-gateway AR binding would append to the model (plus any shell memory merged by the gateway).");
    lines.push("");
    lines.push(bindingSystemPrompt.trim() || "(empty)");
    return lines.join("\n");
}
