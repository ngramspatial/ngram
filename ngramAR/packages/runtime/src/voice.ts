// @ts-nocheck
import { Communicate } from "edge-tts-universal";
// ─── Microsoft Edge online TTS (undocumented Read Aloud API; no API key) ────
const DEFAULT_EDGE_VOICE = "en-US-JennyNeural";
/** Map common OpenAI voice ids so existing shells can switch provider to `edge`. */
const OPENAI_VOICE_TO_EDGE = {
    alloy: "en-US-JennyNeural",
    ash: "en-US-AndrewNeural",
    ballad: "en-US-BrandonNeural",
    coral: "en-US-AvaNeural",
    echo: "en-US-GuyNeural",
    fable: "en-GB-SoniaNeural",
    nova: "en-US-AriaNeural",
    onyx: "en-US-EricNeural",
    sage: "en-US-JennyNeural",
    shimmer: "en-US-AriaNeural",
    verse: "en-US-ChristopherNeural",
    default: DEFAULT_EDGE_VOICE,
};
function speedToEdgeRate(speed) {
    const pct = Math.round((speed - 1) * 100);
    if (pct >= 0)
        return `+${pct}%`;
    return `${pct}%`;
}
function resolveEdgeVoiceName(profile) {
    const fromEnv = process.env["NGRAM_AR_EDGE_TTS_VOICE"]?.trim();
    const raw = (profile.voice || "").trim();
    if (!raw || raw === "default") {
        return fromEnv || DEFAULT_EDGE_VOICE;
    }
    if (/^[a-z]{2}-[A-Z]{2}-.+Neural$/i.test(raw)) {
        return raw;
    }
    const mapped = OPENAI_VOICE_TO_EDGE[raw.toLowerCase()];
    if (mapped)
        return mapped;
    return raw;
}
class EdgeVoiceEngine {
    voice;
    rate;
    proxy;
    connectionTimeout;
    constructor(profile) {
        this.voice = resolveEdgeVoiceName(profile);
        this.rate = speedToEdgeRate(profile.speed ?? 1.0);
        this.proxy = process.env["NGRAM_AR_EDGE_TTS_PROXY"]?.trim() || process.env["HTTPS_PROXY"]?.trim();
        const rawTimeout = process.env["NGRAM_AR_EDGE_TTS_TIMEOUT_MS"]?.trim();
        this.connectionTimeout = rawTimeout ? parseInt(rawTimeout, 10) : undefined;
    }
    async synthesize(text) {
        const communicate = new Communicate(text, {
            voice: this.voice,
            rate: this.rate,
            ...(this.proxy ? { proxy: this.proxy } : {}),
            ...(this.connectionTimeout != null && !Number.isNaN(this.connectionTimeout)
                ? { connectionTimeout: this.connectionTimeout }
                : {}),
        });
        const chunks = [];
        for await (const chunk of communicate.stream()) {
            if (chunk.type === "audio" && chunk.data) {
                chunks.push(chunk.data);
            }
        }
        if (chunks.length === 0) {
            throw new Error("Edge TTS returned no audio");
        }
        const buf = Buffer.concat(chunks);
        return { audioBase64: buf.toString("base64") };
    }
}
// ─── OpenAI TTS ─────────────────────────────────────────────────────────────
class OpenAIVoiceEngine {
    apiKey;
    voice;
    speed;
    constructor(profile) {
        const key = profile.apiKey?.trim() ||
            process.env["NGRAM_AR_OPENAI_API_KEY"]?.trim() ||
            process.env["OPENAI_API_KEY"]?.trim() ||
            process.env["NGRAM_AR_TTS_API_KEY"]?.trim() ||
            process.env["NGRAM_AR_LLM_API_KEY"]?.trim() ||
            "";
        if (!key) {
            throw new Error("OpenAI TTS requires an API key via VoiceProfile.apiKey, NGRAM_AR_OPENAI_API_KEY, or OPENAI_API_KEY");
        }
        this.apiKey = key;
        this.voice = profile.voice || "alloy";
        this.speed = profile.speed ?? 1.0;
    }
    async synthesize(text) {
        const response = await fetch("https://api.openai.com/v1/audio/speech", {
            method: "POST",
            headers: {
                Authorization: `Bearer ${this.apiKey}`,
                "Content-Type": "application/json",
            },
            body: JSON.stringify({
                model: "tts-1",
                input: text,
                voice: this.voice,
                speed: this.speed,
                response_format: "mp3",
            }),
        });
        if (!response.ok) {
            const body = await response.text().catch(() => "");
            throw new Error(`OpenAI TTS failed (${response.status}): ${body}`);
        }
        const buffer = await response.arrayBuffer();
        const audioBase64 = btoa(Array.from(new Uint8Array(buffer), (b) => String.fromCharCode(b)).join(""));
        return { audioBase64 };
    }
}
// ─── Cartesia TTS ───────────────────────────────────────────────────────────
class CartesiaVoiceEngine {
    apiKey;
    voiceId;
    modelId;
    speed;
    emotion;
    constructor(profile) {
        const key = profile.apiKey?.trim() ||
            process.env["CARTESIA_API_KEY"]?.trim() ||
            process.env["NGRAM_AR_TTS_API_KEY"]?.trim() ||
            "";
        if (!key) {
            throw new Error("Cartesia TTS requires an API key via VoiceProfile.apiKey, " +
                "CARTESIA_API_KEY, or NGRAM_AR_TTS_API_KEY");
        }
        this.apiKey = key;
        this.voiceId = profile.voice || "db6b0ed5-d5d3-463d-ae85-518a07d3c2b4";
        this.modelId = profile.model || "sonic-3.6";
        this.speed = profile.speed ?? 1.0;
        this.emotion = profile.emotion;
    }
    async synthesize(text) {
        const body = {
            model_id: this.modelId,
            transcript: text,
            voice: this.voiceId,
            language: "en",
            output_format: {
                container: "wav",
                encoding: "pcm_s16le",
                sample_rate: 44100,
            },
        };
        if (this.speed !== 1.0 || this.emotion) {
            const genConfig = {};
            if (this.speed !== 1.0)
                genConfig["speed"] = this.speed;
            if (this.emotion)
                genConfig["emotion"] = this.emotion;
            body["generation_config"] = genConfig;
        }
        const response = await fetch("https://api.cartesia.ai/tts/bytes", {
            method: "POST",
            headers: {
                Authorization: `Bearer ${this.apiKey}`,
                "Cartesia-Version": "2026-08-14",
                "Content-Type": "application/json",
            },
            body: JSON.stringify(body),
            signal: AbortSignal.timeout(30000),
        });
        if (!response.ok) {
            throw new Error(`Cartesia TTS failed (${response.status}). Check the key, voice ID, and model.`);
        }
        const buffer = await response.arrayBuffer();
        const audioBase64 = btoa(Array.from(new Uint8Array(buffer), (b) => String.fromCharCode(b)).join(""));
        return { audioBase64 };
    }
}
// ─── Browser TTS ────────────────────────────────────────────────────────────
// The actual speech synthesis happens on the shell surface (browser/WebXR).
// This engine returns an empty payload so the surface knows to use its own TTS.
class BrowserVoiceEngine {
    constructor(profile) { this.profile = profile; }
    async synthesize(_text) {
        return { audioBase64: "", voiceConfig: { voice: this.profile.voice, speed: this.profile.speed ?? 1 } };
    }
}
// ─── Factory ────────────────────────────────────────────────────────────────
export function createVoiceEngine(profile) {
    switch (profile.provider) {
        case "edge":
            return new EdgeVoiceEngine(profile);
        case "openai":
            return new OpenAIVoiceEngine(profile);
        case "cartesia":
            return new CartesiaVoiceEngine(profile);
        case "browser":
            return new BrowserVoiceEngine(profile);
        default:
            throw new Error(`Unsupported voice provider: ${profile.provider}`);
    }
}
