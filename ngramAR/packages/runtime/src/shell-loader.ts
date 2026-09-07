// @ts-nocheck
import { readFile } from "node:fs/promises";
import path from "node:path";
import { parseYaml } from "./yaml.js";
// ─── Shell Loader ───────────────────────────────────────────────────────────
function parseAnimationPack(raw) {
    if (raw == null)
        return "standard";
    if (typeof raw === "string")
        return raw;
    if (typeof raw === "object" && !Array.isArray(raw)) {
        const map = {};
        for (const [k, v] of Object.entries(raw)) {
            map[k] = String(v);
        }
        return map;
    }
    return "standard";
}
export async function loadShellDefinition(shellDir) {
    const yamlPath = path.join(shellDir, "shell.yaml");
    const content = await readFile(yamlPath, "utf-8");
    const raw = parseYaml(content);
    const voice = raw["voice"];
    const binding = raw["binding"];
    const memory = raw["memory"];
    const voiceProfile = {
        provider: String(voice?.["provider"] ?? "browser"),
        voice: String(voice?.["voice"] ?? "default"),
        ...(voice?.["apiKey"] != null ? { apiKey: String(voice["apiKey"]) } : {}),
        ...(voice?.["speed"] != null ? { speed: Number(voice["speed"]) } : {}),
        ...(voice?.["model"] != null ? { model: String(voice["model"]) } : {}),
        ...(voice?.["emotion"] != null ? { emotion: String(voice["emotion"]) } : {}),
    };
    const yamlType = String(binding?.["type"] ?? "openai");
    const rawOptions = binding?.["options"];
    const options = rawOptions != null && typeof rawOptions === "object" && !Array.isArray(rawOptions)
        ? rawOptions
        : undefined;
    let bindingConfig;
    if (yamlType === "ngram_entity") {
        const bridgeUrl = (options?.["bridgeUrl"] != null ? String(options["bridgeUrl"]) : "") ||
            (binding?.["bridgeUrl"] != null ? String(binding["bridgeUrl"]) : "");
        bindingConfig = {
            type: "ngram_entity",
            ...(bridgeUrl ? { options: { ...options, bridgeUrl } } : options ? { options } : {}),
            ...(binding?.["system"] != null ? { system: String(binding["system"]) } : {}),
        };
    }
    else if (yamlType !== "openai") {
        console.warn(`[shell-loader] binding.type "${yamlType}" is not supported — use "openai" or "ngram_entity". Coercing to openai.`);
        bindingConfig = {
            type: "openai",
            ...(binding?.["model"] != null ? { model: String(binding["model"]) } : {}),
            ...(binding?.["system"] != null ? { system: String(binding["system"]) } : {}),
            ...(options ? { options } : {}),
        };
    }
    else {
        bindingConfig = {
            type: "openai",
            ...(binding?.["model"] != null ? { model: String(binding["model"]) } : {}),
            ...(binding?.["system"] != null ? { system: String(binding["system"]) } : {}),
            ...(options ? { options } : {}),
        };
    }
    const memoryConfig = memory
        ? { path: String(memory["path"] ?? "") }
        : undefined;
    const behaviorPack = Array.isArray(raw["behaviorPack"])
        ? raw["behaviorPack"].map(String)
        : [];
    const toolSurfaces = Array.isArray(raw["toolSurfaces"])
        ? raw["toolSurfaces"].map(String)
        : undefined;
    return {
        name: String(raw["name"] ?? ""),
        ...(raw["description"] != null ? { description: String(raw["description"]) } : {}),
        model: String(raw["model"] ?? "default"),
        ...(raw["scale"] != null ? { scale: Number(raw["scale"]) } : {}),
        animationPack: parseAnimationPack(raw["animationPack"]),
        behaviorPack,
        voice: voiceProfile,
        ...(toolSurfaces ? { toolSurfaces } : {}),
        binding: bindingConfig,
        ...(memoryConfig ? { memory: memoryConfig } : {}),
    };
}
