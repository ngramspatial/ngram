import { mkdir, readFile, writeFile, rename } from 'node:fs/promises';
import { dirname, resolve } from 'node:path';
import { randomUUID } from 'node:crypto';

export const VOICE_PROVIDERS = [
  { id: 'edge', name: 'Microsoft Edge', voice: 'en-US-JennyNeural', model: '', needsKey: false },
  { id: 'cartesia', name: 'Cartesia', voice: 'db6b0ed5-d5d3-463d-ae85-518a07d3c2b4', model: 'sonic-3.6', needsKey: true },
  { id: 'openai', name: 'OpenAI', voice: 'alloy', model: 'tts-1', needsKey: true },
  { id: 'browser', name: 'Browser', voice: '', model: '', needsKey: false },
];
type VoiceSettings = { provider: string; voice: string; model: string; speed: number; apiKey: string };
const text = (value: unknown, max = 256) => typeof value === 'string' ? value.trim().slice(0, max) : '';
export function voiceEnvironmentKey(provider: string): string {
  const names = provider === 'cartesia' ? ['CARTESIA_API_KEY', 'NGRAM_AR_TTS_API_KEY']
    : provider === 'openai' ? ['NGRAM_AR_OPENAI_API_KEY', 'OPENAI_API_KEY', 'NGRAM_AR_TTS_API_KEY', 'NGRAM_AR_LLM_API_KEY'] : [];
  return names.map(name => process.env[name]?.trim()).find(Boolean) ?? '';
}
export function normalizeVoiceConfig(input: any, current: Partial<VoiceSettings> | null = null): VoiceSettings {
  const provider = VOICE_PROVIDERS.find(item => item.id === input?.provider);
  if (!provider) throw new Error('Choose a supported voice provider.');
  const speed = Number(input.speed ?? 1);
  if (!Number.isFinite(speed) || speed < 0.6 || speed > 1.5) throw new Error('Speech speed must be between 0.6 and 1.5.');
  const voice = text(input.voice) || provider.voice;
  if (provider.id === 'cartesia' && !/^[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}$/i.test(voice)) throw new Error('Enter a Cartesia voice ID from your voice library.');
  const model = provider.id === 'cartesia' ? text(input.model) || provider.model : provider.model;
  const apiKey = provider.needsKey ? text(input.apiKey, 8192) || (current?.provider === provider.id ? text(current.apiKey, 8192) : '') : '';
  if (provider.needsKey && !apiKey && !voiceEnvironmentKey(provider.id)) throw new Error(`Add a ${provider.name} API key.`);
  return { provider: provider.id, voice, model, speed, apiKey };
}
export function publicVoiceConfig(config: Partial<VoiceSettings>) {
  return { provider: config.provider, voice: config.voice ?? '', model: config.model ?? '', speed: config.speed ?? 1,
    hasApiKey: Boolean(config.apiKey || voiceEnvironmentKey(config.provider ?? '')) };
}
export const voiceConfigPath = (shellsDir: string) => resolve(shellsDir, '..', '.runtime', 'voice.json');
export async function loadVoiceConfig(shellsDir: string): Promise<VoiceSettings | null> {
  try { const data = JSON.parse(await readFile(voiceConfigPath(shellsDir), 'utf8')); return normalizeVoiceConfig(data, data); }
  catch { return null; }
}
export async function saveVoiceConfig(shellsDir: string, config: VoiceSettings): Promise<void> {
  const path = voiceConfigPath(shellsDir);
  await mkdir(dirname(path), { recursive: true });
  const temporary = `${path}.${randomUUID()}.tmp`;
  await writeFile(temporary, JSON.stringify(config, null, 2) + '\n', { mode: 0o600 });
  await rename(temporary, path);
}
