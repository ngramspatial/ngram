// @ts-nocheck
import { chmod, mkdir, readFile, writeFile } from "node:fs/promises";
import { existsSync } from "node:fs";
import { dirname, join, resolve } from "node:path";

export const BRAIN_PROVIDERS = [
  { id: "openai", name: "OpenAI", mark: "OA", accent: "#10a37f", defaultModel: "gpt-6-astra", defaultEmbeddingModel: "text-embedding-3-small" },
  { id: "anthropic", name: "Anthropic", mark: "AN", accent: "#d97757", defaultModel: "", defaultEmbeddingModel: "" },
  { id: "gemini", name: "Google Gemini", mark: "G", accent: "#4285f4", defaultModel: "gemini-3.7-flash", defaultEmbeddingModel: "" },
  { id: "openrouter", name: "OpenRouter", mark: "OR", accent: "#6d5dfc", defaultModel: "~openai/gpt-latest", defaultEmbeddingModel: "" },
  { id: "xai", name: "xAI", mark: "x", accent: "#202124", defaultModel: "", defaultEmbeddingModel: "" },
  { id: "groq", name: "Groq", mark: "GQ", accent: "#f55036", defaultModel: "openai/gpt-oss-120b", defaultEmbeddingModel: "" },
  { id: "together", name: "Together AI", mark: "TO", accent: "#7c3aed", defaultModel: "", defaultEmbeddingModel: "" },
  { id: "fireworks", name: "Fireworks", mark: "FW", accent: "#f97316", defaultModel: "", defaultEmbeddingModel: "" },
  { id: "mistral", name: "Mistral", mark: "MI", accent: "#ff7000", defaultModel: "", defaultEmbeddingModel: "" },
  { id: "deepseek", name: "DeepSeek", mark: "DS", accent: "#4d6bfe", defaultModel: "", defaultEmbeddingModel: "" },
  { id: "venice", name: "Venice", mark: "V", accent: "#be123c", defaultModel: "", defaultEmbeddingModel: "" },
  { id: "custom", name: "Custom API", mark: "+", accent: "#64748b", defaultModel: "", defaultEmbeddingModel: "" },
];

const PROVIDER_IDS = new Set(BRAIN_PROVIDERS.map((provider) => provider.id));

export function brainConfigPath(shellsDir) {
  return join(resolve(shellsDir, ".."), ".runtime", "brain.json");
}

function cleanText(value, maxLength) {
  return typeof value === "string" ? value.trim().slice(0, maxLength) : "";
}

function validateBaseUrl(raw, required) {
  const value = cleanText(raw, 2048).replace(/\/+$/, "");
  if (!value) {
    if (required) throw new Error("A complete OpenAI-compatible API base URL is required.");
    return "";
  }
  let url;
  try {
    url = new URL(value);
  } catch {
    throw new Error("The API base URL is not valid.");
  }
  const loopback = ["localhost", "127.0.0.1", "::1", "[::1]"].includes(url.hostname);
  if (url.protocol !== "https:" && !(url.protocol === "http:" && loopback)) {
    throw new Error("API endpoints must use HTTPS unless they are on this device.");
  }
  if (url.username || url.password || url.search || url.hash) {
    throw new Error("The API base URL cannot contain credentials, a query, or a fragment.");
  }
  return value;
}

function activeBrainConfig(config) {
  if (!config) return null;
  return {
    version: Number(config.version) >= 2 ? 2 : 1,
    configured: config.configured === true,
    mode: cleanText(config.mode, 16).toLowerCase(),
    provider: cleanText(config.provider, 32).toLowerCase(),
    model: cleanText(config.model, 256),
    baseUrl: cleanText(config.baseUrl, 2048),
    apiKey: cleanText(config.apiKey, 8192),
    embeddingMode: cleanText(config.embeddingMode, 16).toLowerCase(),
    embeddingModel: cleanText(config.embeddingModel, 256),
    updatedAt: cleanText(config.updatedAt, 64),
  };
}

function frontierProfilesFrom(config) {
  const profiles = {};
  const saved = config?.frontierProfiles;
  if (saved && typeof saved === "object") {
    for (const [id, raw] of Object.entries(saved)) {
      if (!PROVIDER_IDS.has(id) || !raw || typeof raw !== "object") continue;
      const profile = activeBrainConfig(raw);
      if (profile?.mode === "frontier" && profile.provider === id && profile.apiKey) {
        profiles[id] = profile;
      }
    }
  }
  const current = activeBrainConfig(config);
  if (current?.mode === "frontier" && PROVIDER_IDS.has(current.provider) && current.apiKey) {
    profiles[current.provider] = current;
  }
  return profiles;
}

export function runtimeBrainConfig(config) {
  return activeBrainConfig(config);
}

export function normalizeBrainConfig(input, current = null) {
  const frontierProfiles = frontierProfilesFrom(current);
  const mode = cleanText(input?.mode, 16).toLowerCase();
  if (!["local", "private", "frontier"].includes(mode)) {
    throw new Error("Choose Local, Private GPU, or Frontier API.");
  }

  let provider = cleanText(input?.provider, 32).toLowerCase();
  if (mode === "local") provider = "local";
  if (mode === "private") provider = "remote_gateway";
  if (mode === "frontier" && !PROVIDER_IDS.has(provider)) {
    throw new Error("Choose a supported frontier provider.");
  }

  const model = cleanText(input?.model, 256);
  if (mode === "frontier" && !model) {
    throw new Error("Enter the provider's model ID.");
  }
  const sameCredential = current && current.mode === mode && current.provider === provider;
  const suppliedKey = cleanText(input?.apiKey, 8192);
  const savedKey = mode === "frontier" ? cleanText(frontierProfiles[provider]?.apiKey, 8192) : "";
  const apiKey = suppliedKey || (sameCredential ? cleanText(current.apiKey, 8192) : "") || savedKey;
  if (mode === "frontier" && !apiKey) {
    throw new Error("Paste an API key for this provider.");
  }

  const baseUrl = validateBaseUrl(input?.baseUrl, mode === "frontier" && provider === "custom");
  const requestedEmbeddingMode = cleanText(input?.embeddingMode, 16).toLowerCase();
  const loadingLegacy = input === current && Number(input?.version || 1) < 2;
  let embeddingMode = mode === "frontier"
    ? (requestedEmbeddingMode || (loadingLegacy ? "existing" : "provider"))
    : "provider";
  if (!["provider", "existing"].includes(embeddingMode)) {
    throw new Error("Choose hosted or existing memory embeddings.");
  }
  const providerDefinition = BRAIN_PROVIDERS.find((candidate) => candidate.id === provider);
  let embeddingModel = cleanText(input?.embeddingModel, 256);
  if (mode === "frontier" && embeddingMode === "provider") {
    embeddingModel ||= providerDefinition?.defaultEmbeddingModel || "";
    if (!embeddingModel) {
      throw new Error("Enter an embedding model ID for fully hosted memory.");
    }
  }
  if (embeddingMode === "existing") embeddingModel = "";
  const active = {
    version: 2,
    configured: true,
    mode,
    provider,
    model,
    baseUrl,
    apiKey: mode === "frontier" ? apiKey : "",
    embeddingMode,
    embeddingModel,
    updatedAt: new Date().toISOString(),
  };
  if (mode === "frontier") frontierProfiles[provider] = activeBrainConfig(active);
  const priorLastProvider = cleanText(current?.lastFrontierProvider, 32).toLowerCase();
  return {
    ...active,
    frontierProfiles,
    lastFrontierProvider: mode === "frontier"
      ? provider
      : (PROVIDER_IDS.has(priorLastProvider) ? priorLastProvider : Object.keys(frontierProfiles)[0] || "openai"),
  };
}

export function publicBrainConfig(config) {
  if (!config) {
    return {
      configured: false,
      mode: "private",
      provider: "remote_gateway",
      model: "",
      baseUrl: "",
      hasApiKey: false,
    };
  }
  const active = activeBrainConfig(config);
  const { apiKey: _secret, ...safe } = active;
  const savedFrontierProfiles = {};
  for (const [id, profile] of Object.entries(frontierProfilesFrom(config))) {
    const { apiKey: _profileSecret, ...publicProfile } = profile;
    savedFrontierProfiles[id] = { ...publicProfile, hasApiKey: Boolean(profile.apiKey) };
  }
  return {
    ...safe,
    hasApiKey: Boolean(active.apiKey),
    savedFrontierProfiles,
    lastFrontierProvider: cleanText(config.lastFrontierProvider, 32) || Object.keys(savedFrontierProfiles)[0] || "openai",
  };
}

export async function loadBrainConfig(shellsDir) {
  const path = brainConfigPath(shellsDir);
  if (!existsSync(path)) return null;
  try {
    const parsed = JSON.parse(await readFile(path, "utf8"));
    if (parsed?.configured !== true) return null;
    return normalizeBrainConfig(parsed, parsed);
  } catch {
    return null;
  }
}

export async function saveBrainConfig(shellsDir, config) {
  const path = brainConfigPath(shellsDir);
  await mkdir(dirname(path), { recursive: true });
  await writeFile(path, `${JSON.stringify(config, null, 2)}\n`, { encoding: "utf8", mode: 0o600 });
  try { await chmod(path, 0o600); } catch { /* Windows and restricted filesystems may ignore POSIX modes. */ }
}
