import type { SpeechPlaybackOptions } from './speech.js';

export function setupVoiceSettings(player: { play: (options: SpeechPlaybackOptions, done: () => void) => void; stop: () => void }) {
  const element = <T extends HTMLElement>(id: string) => document.getElementById(id) as T;
  const provider = element<HTMLSelectElement>('voice-provider');
  const key = element<HTMLInputElement>('voice-key');
  const voice = element<HTMLInputElement>('voice-id');
  const model = element<HTMLInputElement>('voice-model');
  const speed = element<HTMLInputElement>('voice-speed');
  const save = element<HTMLButtonElement>('voice-save');
  const preview = element<HTMLButtonElement>('voice-preview');
  const feedback = element<HTMLElement>('voice-feedback');
  let providers: Array<{ id: string; voice: string; model: string; needsKey: boolean; hasApiKey?: boolean }> = [];
  let saved: any;
  let request: AbortController | null = null;
  let previewing = false;
  let saving = false;
  let loadEpoch = 0;
  const message = (text: string, error = false) => {
    feedback.textContent = text;
    feedback.dataset.state = error ? 'error' : 'success';
    feedback.classList.toggle('visible', !!text);
    feedback.classList.toggle('error', error);
  };
  const lock = (busy: boolean) => {
    for (const input of [provider, key, voice, model, speed, save]) input.disabled = busy;
  };
  const sync = () => {
    const definition = providers.find(item => item.id === provider.value);
    element('voice-key-field').hidden = !definition?.needsKey;
    element('voice-model-field').hidden = provider.value !== 'cartesia';
    const hasKey = (saved?.provider === provider.value && saved.hasApiKey) || definition?.hasApiKey;
    key.placeholder = hasKey ? 'Key configured — leave blank to keep it' : 'Paste your provider API key';
    element('voice-key-hint').textContent = hasKey ? 'A key is configured on this gateway.' : 'Your key stays on this gateway.';
    element('voice-id-hint').textContent = provider.value === 'cartesia' ? 'Paste a voice ID from your Cartesia voice library.'
      : provider.value === 'browser' ? 'Choose a voice on this device, or leave blank for its default.' : 'Choose a voice or enter its provider ID.';
    element('voice-speed-value').textContent = `${Number(speed.value)}×`;
    const choices = provider.value === 'browser' ? (window.speechSynthesis?.getVoices() ?? []).map(item => item.name)
      : provider.value === 'edge' ? ['en-US-JennyNeural', 'en-US-AriaNeural', 'en-US-GuyNeural', 'en-GB-SoniaNeural']
      : provider.value === 'openai' ? ['alloy', 'echo', 'fable', 'onyx', 'nova', 'shimmer'] : [];
    element('voice-choices').replaceChildren(...choices.map(value => { const option = document.createElement('option'); option.value = value; return option; }));
  };
  const stopPreview = () => {
    request?.abort(); request = null;
    if (previewing) { previewing = false; player.stop(); }
    preview.textContent = 'Preview voice';
    if (!saving && saved) lock(false);
  };
  provider.addEventListener('change', () => {
    stopPreview();
    const defaults = providers.find(item => item.id === provider.value);
    const config = saved?.provider === provider.value ? saved : defaults;
    voice.value = config?.voice ?? '';
    model.value = config?.model ?? '';
    key.value = '';
    sync(); message('');
  });
  speed.addEventListener('input', sync);
  window.speechSynthesis?.addEventListener('voiceschanged', sync);
  const load = async () => {
    const epoch = ++loadEpoch;
    lock(true); preview.disabled = true;
    try {
      const response = await fetch('/api/voice', { cache: 'no-store' });
      if (!response.ok) throw new Error('Voice settings are unavailable.');
      const data = await response.json();
      if (epoch !== loadEpoch) return;
      saved = data.config; providers = data.providers;
      provider.value = saved.provider; voice.value = saved.voice; model.value = saved.model;
      speed.value = String(saved.speed); key.value = '';
      lock(false); preview.disabled = false; sync(); message('');
    } catch (error) { if (epoch === loadEpoch) message(error instanceof Error ? error.message : 'Could not load voice settings.', true); }
  };
  const values = () => ({ provider: provider.value, voice: voice.value, model: model.value, speed: Number(speed.value), apiKey: key.value });
  save.addEventListener('click', async () => {
    stopPreview(); saving = true; lock(true); preview.disabled = true;
    message('Saving voice…');
    try {
      const response = await fetch('/api/voice', { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(values()) });
      const data = await response.json();
      if (!response.ok) throw new Error(data.error ?? 'Could not save voice settings.');
      saved = data.config; key.value = ''; sync(); message('Voice saved. Your next response will use these settings.');
    } catch (error) { message(error instanceof Error ? error.message : 'Could not save voice settings.', true); }
    finally { saving = false; lock(false); preview.disabled = false; }
  });
  preview.addEventListener('click', async () => {
    if (previewing) { stopPreview(); message('Preview stopped.'); return; }
    previewing = true; lock(true); preview.textContent = 'Stop preview';
    const controller = new AbortController(); request = controller;
    message('Preparing voice preview…');
    try {
      const response = await fetch('/api/voice/preview', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(values()), signal: controller.signal });
      const data = await response.json();
      if (controller.signal.aborted) return;
      if (!response.ok) throw new Error(data.error ?? 'Voice preview failed.');
      message('Playing preview…');
      player.play({ text: data.text, audioData: data.audioBase64, voice: data.config.voice, speed: data.config.speed }, () => {
        previewing = false; request = null; preview.textContent = 'Preview voice'; lock(false); message('Preview finished.');
      });
    } catch (error) {
      if (controller.signal.aborted) return;
      stopPreview(); message(error instanceof Error ? error.message : 'Voice preview failed.', true);
    }
  });
  document.addEventListener('settings:opened', () => { if (!saving) void load(); });
  document.addEventListener('settings:closed', () => { ++loadEpoch; stopPreview(); key.value = ''; });
}
