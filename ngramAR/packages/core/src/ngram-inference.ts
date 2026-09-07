// @ts-nocheck
/**
 * ngram AR talks only to `ngram.inference_gateway` — the same narrow HTTP
 * surface the Python harness uses (`/v1/chat/completions`, Bearer token).
 */
/** Normalize any gateway origin to OpenAI-style `.../v1` (for `POST .../chat/completions`). */
export function normalizengramInferenceGatewayBaseUrl(raw) {
    const t = raw.trim().replace(/\/+$/, "");
    return t.endsWith("/v1") ? t : `${t}/v1`;
}
/**
 * Read `NGRAM_INFERENCE_GATEWAY_BASE_URL` and `INFERENCE_GATEWAY_TOKEN` from the environment.
 * Same variables as `ngram.inference_gateway` and `RemoteGatewayProvider` in the harness.
 */
export function loadngramInferenceFromEnv() {
    const baseEnv = process.env["NGRAM_INFERENCE_GATEWAY_BASE_URL"]?.trim() ?? "";
    const token = process.env["INFERENCE_GATEWAY_TOKEN"]?.trim() ?? "";
    if (!baseEnv || !token) {
        throw new Error("ngram AR requires NGRAM_INFERENCE_GATEWAY_BASE_URL and INFERENCE_GATEWAY_TOKEN " +
            "(same as the Python ngram inference gateway / harness remote_gateway).");
    }
    return {
        baseUrlV1: normalizengramInferenceGatewayBaseUrl(baseEnv),
        token,
    };
}
