// @ts-nocheck
import { OpenAIBinding } from "./openai-binding.js";
export function createBinding(config) {
    const t = String(config.type);
    if (t === "ngram_entity") {
        throw new Error('Binding type "ngram_entity" is constructed by the gateway (EntityBridgeBinding); use createBinding only for "openai".');
    }
    if (t !== "openai") {
        throw new Error(`Unsupported binding type "${String(config.type)}". Use "openai" or "ngram_entity".`);
    }
    return new OpenAIBinding(config);
}
