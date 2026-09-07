// @ts-nocheck
/**
 * Resolve WebSocket URL for the Python entity bridge.
 * Precedence: shell `binding.options.bridgeUrl`, then `NGRAM_AR_ENTITY_BRIDGE_URL`.
 * Optional token: `NGRAM_AR_ENTITY_BRIDGE_TOKEN` (sent as a Bearer header).
 */
export function resolveEntityBridgeConfig(binding) {
    const opts = binding.options ?? {};
    const fromYaml = (typeof opts["bridgeUrl"] === "string" && opts["bridgeUrl"].trim()) ||
        (typeof binding.bridgeUrl === "string" &&
            binding.bridgeUrl.trim()) ||
        "";
    const bridgeUrl = fromYaml || process.env["NGRAM_AR_ENTITY_BRIDGE_URL"]?.trim() || "";
    if (!bridgeUrl) {
        throw new Error('Binding type "ngram_entity" requires bridgeUrl in shell.yaml (binding.options.bridgeUrl) or NGRAM_AR_ENTITY_BRIDGE_URL in the environment.');
    }
    const token = process.env["NGRAM_AR_ENTITY_BRIDGE_TOKEN"]?.trim() || undefined;
    return { bridgeUrl, token };
}
export function isngramEntityBinding(binding) {
    return String(binding.type) === "ngram_entity";
}
