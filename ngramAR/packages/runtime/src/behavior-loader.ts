// @ts-nocheck
import { readFile } from "node:fs/promises";
import { existsSync } from "node:fs";
import path from "node:path";
import { parseYaml } from "./yaml.js";
// ─── Parse a single behavior from YAML map ──────────────────────────────────
function parseBehaviorDef(raw) {
    const trigger = raw["trigger"];
    const triggerParams = trigger?.["params"] ?? {};
    const triggerConfig = {
        type: String(trigger?.["type"] ?? "proximity"),
        params: triggerParams,
    };
    const actionRaw = raw["action"];
    let action;
    if (actionRaw) {
        action = {
            type: String(actionRaw["type"] ?? ""),
            params: (actionRaw["params"] ?? {}),
        };
    }
    return {
        id: String(raw["id"] ?? ""),
        ...(raw["description"] != null ? { description: String(raw["description"]) } : {}),
        trigger: triggerConfig,
        priority: Number(raw["priority"] ?? 0),
        ...(raw["cooldown"] != null ? { cooldown: Number(raw["cooldown"]) } : {}),
        mode: String(raw["mode"] ?? "reactive"),
        ...(action ? { action } : {}),
        ...(raw["prompt"] != null ? { prompt: String(raw["prompt"]) } : {}),
        ...(raw["enabled"] != null ? { enabled: raw["enabled"] === true || raw["enabled"] === "true" } : {}),
    };
}
// ─── Load a behavior pack YAML file ─────────────────────────────────────────
async function loadPackFile(filePath) {
    const content = await readFile(filePath, "utf-8");
    const raw = parseYaml(content);
    const behaviorsRaw = Array.isArray(raw["behaviors"]) ? raw["behaviors"] : [];
    const behaviors = behaviorsRaw
        .filter((b) => typeof b === "object" && b !== null && !Array.isArray(b))
        .map(parseBehaviorDef);
    return {
        name: String(raw["name"] ?? path.basename(filePath, ".yaml")),
        ...(raw["description"] != null ? { description: String(raw["description"]) } : {}),
        behaviors,
    };
}
// ─── Parse inline behaviors from shell.yaml raw data ────────────────────────
export function parseInlineBehaviors(rawBehaviors) {
    if (!rawBehaviors || !Array.isArray(rawBehaviors))
        return [];
    return rawBehaviors
        .filter((b) => typeof b === "object" && b !== null && !Array.isArray(b))
        .map(parseBehaviorDef);
}
// ─── Resolve all behaviors for a shell ──────────────────────────────────────
// Loads referenced packs from the behaviors/ directory, merges with inline
// behaviors. Inline behaviors override pack behaviors with the same id.
export async function loadBehaviors(behaviorPackNames, inlineBehaviors, behaviorsDir) {
    const byId = new Map();
    for (const packName of behaviorPackNames) {
        const packPath = path.join(behaviorsDir, `${packName}.yaml`);
        if (!existsSync(packPath))
            continue;
        try {
            const pack = await loadPackFile(packPath);
            for (const b of pack.behaviors) {
                byId.set(b.id, b);
            }
        }
        catch (e) {
            console.warn(`[behavior-loader] Failed to load pack "${packName}":`, e);
        }
    }
    for (const b of inlineBehaviors) {
        byId.set(b.id, b);
    }
    return Array.from(byId.values()).filter((b) => b.enabled !== false);
}
