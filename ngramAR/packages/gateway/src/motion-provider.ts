// @ts-nocheck
import { createAction } from "@ngram-ar/core";

const MAX_RESPONSE_BYTES = 1_000_000;

/**
 * Server-side adapter for an external motion service (for example ARDY on RunPod).
 * The provider owns model-specific inference and exports a retargetable FBX/GLB
 * clip; browsers never receive provider credentials.
 */
export class MotionProviderClient {
    endpoint;
    token;
    timeoutMs;

    constructor(config = {}) {
        this.endpoint = String(config.endpoint ?? "").replace(/\/+$/, "");
        this.token = String(config.token ?? "").trim() || undefined;
        this.timeoutMs = Math.max(5_000, Number(config.timeoutMs ?? 120_000));
    }

    static fromEnvironment() {
        return new MotionProviderClient({
            endpoint: process.env["NGRAM_AR_MOTION_PROVIDER_URL"]?.trim(),
            token: process.env["NGRAM_AR_MOTION_PROVIDER_TOKEN"]?.trim(),
            timeoutMs: process.env["NGRAM_AR_MOTION_PROVIDER_TIMEOUT_MS"]?.trim(),
        });
    }

    get configured() {
        return Boolean(this.endpoint);
    }

    async generate(action, sessionId) {
        if (!this.configured) {
            throw new Error(
                "Generated motion is not configured. Set NGRAM_AR_MOTION_PROVIDER_URL on the AR gateway.",
            );
        }
        const endpoint = new URL(this.endpoint);
        if (!['http:', 'https:'].includes(endpoint.protocol)) {
            throw new Error("Motion provider URL must use HTTP(S)");
        }

        const controller = new AbortController();
        const timer = setTimeout(() => controller.abort(), this.timeoutMs);
        let response;
        try {
            response = await fetch(`${this.endpoint}/v1/motion`, {
                method: "POST",
                headers: {
                    "Content-Type": "application/json",
                    ...(this.token ? { Authorization: `Bearer ${this.token}` } : {}),
                },
                body: JSON.stringify({
                    protocolVersion: "1.0",
                    requestId: String(action.requestId ?? ""),
                    prompt: String(action.prompt ?? "").slice(0, 1000),
                    durationSeconds: Math.max(0.5, Math.min(30, Number(action.durationSeconds ?? 4))),
                    constraints: action.constraints ?? { rootTarget: "stationary" },
                    output: {
                        skeleton: "mixamo-humanoid",
                        formats: ["glb", "fbx"],
                        delivery: "url",
                    },
                }),
                signal: controller.signal,
            });
        }
        catch (error) {
            if (controller.signal.aborted)
                throw new Error("Motion provider timed out");
            throw error;
        }
        finally {
            clearTimeout(timer);
        }

        const raw = await response.text();
        if (raw.length > MAX_RESPONSE_BYTES)
            throw new Error("Motion provider response was too large");
        if (!response.ok)
            throw new Error(`Motion provider failed (${response.status})`);
        let body;
        try {
            body = JSON.parse(raw);
        }
        catch {
            throw new Error("Motion provider returned invalid JSON");
        }
        const clip = body.clip ?? body.output ?? body;
        const clipUrl = String(clip.url ?? clip.clipUrl ?? "").trim();
        const format = String(clip.format ?? clipUrl.split(/[?#]/)[0].split('.').pop() ?? "").toLowerCase();
        if (!clipUrl || !['fbx', 'glb', 'gltf'].includes(format))
            throw new Error("Motion provider did not return an FBX/GLB clip URL");
        const parsedClipUrl = new URL(clipUrl);
        if (!['http:', 'https:'].includes(parsedClipUrl.protocol))
            throw new Error("Motion clip URL must use HTTP(S)");

        return createAction("action:play_motion_clip", sessionId, {
            requestId: String(action.requestId ?? body.requestId ?? ""),
            clipUrl,
            format,
            name: String(clip.name ?? "generated-motion").slice(0, 120),
            loop: Boolean(action.loop ?? clip.loop),
        });
    }
}
