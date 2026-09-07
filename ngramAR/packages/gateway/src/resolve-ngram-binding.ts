// @ts-nocheck
import { loadngramInferenceFromEnv } from "@ngram-ar/core";
export { normalizengramInferenceGatewayBaseUrl } from "@ngram-ar/core";
/**
 * Build the OpenAI-compatible binding config used for all shells.
 * LLM traffic always uses `ngram.inference_gateway` (Bearer INFERENCE_GATEWAY_TOKEN).
 */
export function resolvengramBinding(binding) {
    const { baseUrlV1, token } = loadngramInferenceFromEnv();
    const model = binding.model?.trim() ||
        process.env["NGRAM_AR_INFERENCE_MODEL"]?.trim() ||
        "llama3.2";
    return {
        type: "openai",
        baseUrl: baseUrlV1,
        apiKey: token,
        model,
        system: binding.system,
        options: binding.options,
    };
}
