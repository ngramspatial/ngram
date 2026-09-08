// @ts-nocheck
import { randomBytes } from 'node:crypto';
import { mkdir, writeFile, rename, rm, cp } from 'node:fs/promises';
import { readFileSync, existsSync } from 'node:fs';
import { join, resolve, sep } from 'node:path';
import { WebSocket } from 'ws';
import { normalizeBrainConfig, runtimeBrainConfig } from './brain-config.js';

export const VOICES = ['en-US-JennyNeural', 'en-US-GuyNeural', 'en-US-AriaNeural', 'en-GB-SoniaNeural', 'en-GB-RyanNeural'];
export function connectionPath(shellsDir, id) {
  if (!/^[a-f0-9]{32}$/.test(id)) throw Error('Invalid saved connection.');
  return join(resolve(shellsDir, '..'), '.runtime', 'connections', `${id}.json`);
}
export function loadConnection(shellsDir, id) {
  return JSON.parse(readFileSync(connectionPath(shellsDir, id), 'utf8'));
}
export function normalizeConnection(input) {
  let url;
  try {
    let raw = String(input?.bridgeUrl || '').trim();
    if (!raw.includes('://')) raw = `https://${raw}`;
    url = new URL(raw);
  } catch { throw Error('Enter the public URL of your Railway worker.'); }
  if (url.protocol === 'https:') url.protocol = 'wss:';
  if (url.protocol === 'http:') url.protocol = 'ws:';
  const local = ['localhost', '127.0.0.1', '[::1]'].includes(url.hostname);
  if (url.protocol !== 'wss:' && !(local && url.protocol === 'ws:')) throw Error('Use a secure Railway URL, or a local WebSocket URL on this computer.');
  if (!url.hostname || url.username || url.password || url.search || url.hash) throw Error('Worker URLs cannot contain credentials, queries, or fragments.');
  const token = String(input?.token || '').trim();
  if ((!local && !token) || token.length > 8192 || /[\r\n]/.test(token)) throw Error('Paste the worker pairing token from setup.');
  return { bridgeUrl: url.toString(), token, target: local ? 'local' : 'cloud' };
}
export function normalizeCreation(input) {
  const name = typeof input?.name === 'string' ? input.name.trim() : '';
  if (!name || name.length > 60 || /[\x00-\x1f<>]/.test(name)) throw Error('Use a name of 1–60 characters without markup or line breaks.');
  const slug = name.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '');
  if (!slug) throw Error('Include at least one letter or number from a–z or 0–9 in the name.');
  const voice = input.voice || VOICES[0];
  if (!VOICES.includes(voice)) throw Error('Choose one of the available voices.');
  const embodiment = input.embodiment || 'orb';
  if (!['orb', 'human'].includes(embodiment)) throw Error('Choose a body from the available styles.');
  const brain = input.brain ? normalizeBrainConfig(input.brain) : null;
  return { name, slug, voice, embodiment, brain };
}

// A real authenticated handshake. No conversation, greeting, or background turn.
export function verifyConnection(connection, brain = null, timeoutMs = 70000) {
  return new Promise((resolve, reject) => {
    const ws = new WebSocket(connection.bridgeUrl, {
      headers: connection.token ? { Authorization: `Bearer ${connection.token}` } : {},
      handshakeTimeout: 15000, maxPayload: 128 * 1024, followRedirects: false,
    });
    let settled = false;
    let ready = null;
    const requestId = randomBytes(12).toString('hex');
    const finish = (error, value) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      if (ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({ type: 'session.stop' }));
        ws.close();
      } else ws.terminate();
      error ? reject(error) : resolve(value);
    };
    const timer = setTimeout(() => finish(Error('The worker took too long to respond. Check its deployment logs and try again.')), timeoutMs);
    ws.on('open', () => ws.send(JSON.stringify({ type: 'session.start', sessionId: `setup-${requestId}`, shellName: 'Setup', shellSlug: 'setup-verification' })));
    ws.on('message', raw => {
      let message;
      try { message = JSON.parse(raw.toString()); } catch { return finish(Error('The worker returned an invalid response.')); }
      if (message.type === 'session.ready' && !ready) {
        ready = message.setup || {};
        if (!brain) return finish(null, { connected: true, ...ready, embeddingVerified: false });
        // Existing vectors must stay in the same semantic space, even when widths match.
        if (!ready.embeddingModel) return finish(Error('Update this worker before changing its memory provider, or choose “Keep worker settings”.'));
        if (brain.embeddingMode === 'provider' && ready.embeddingModel !== brain.embeddingModel && ready.hasMemories !== false) {
          return finish(Error(`This worker has memories using ${ready.embeddingModel}. Keep worker settings to preserve recall, or migrate its memories before switching embedding models.`));
        }
        ws.send(JSON.stringify({ type: 'brain.configure', id: requestId, config: runtimeBrainConfig(brain), interrupt: false }));
      }
      if (message.type === 'brain.configured' && message.replyTo === requestId) {
        if (!message.ok) return finish(Error('The worker could not verify the provider and memory model. Check the API key, model access, and worker logs.'));
        if (!message.status?.verified) return finish(Error('The provider did not confirm that chat model. Enter an available model ID and try again.'));
        finish(null, { connected: true, ...ready, ...message.status, embeddingVerified: brain.embeddingMode === 'provider' });
      }
    });
    ws.on('error', () => finish(Error('Could not pair with the worker. Check the URL, pairing token, and Railway deployment status.')));
    ws.on('close', () => finish(Error('The worker closed the connection before setup finished. Check the pairing token and retry.')));
  });
}

export async function writeConnectedShell(shellsDir, creation, connection, status) {
  const { name, slug, voice, embodiment, brain } = creation;
  const root = resolve(shellsDir);
  await mkdir(root, { recursive: true });
  const destination = join(root, slug);
  // mkdir is the collision lock. Never replace an existing body or its credentials.
  if (!destination.startsWith(root + sep)) throw Error('Invalid shell destination.');
  await mkdir(destination);
  const id = randomBytes(16).toString('hex');
  const secretPath = connectionPath(shellsDir, id);
  try {
    if (embodiment === 'human') {
      const source = join(root, 'canary', 'models', 'michelle');
      if (!existsSync(join(source, 'michelle.fbx'))) throw Error('The human model is not installed. Choose the Orb presence or install the example model assets.');
      await cp(source, join(destination, 'models', 'michelle'), { recursive: true });
    }
    await mkdir(resolve(secretPath, '..'), { recursive: true });
    await writeFile(secretPath, JSON.stringify({ ...connection, brain, status }), { mode: 0o600, flag: 'wx' });
    const body = embodiment === 'human'
      ? 'model: models/michelle/michelle.fbx\nscale: 0.01\nanimationPack:\n  idle: models/michelle/anims/idle-michelle.fbx\n  talking: models/michelle/anims/Talking-michelle.fbx\n  waving: models/michelle/anims/wave-michelle.fbx\n  walking: models/michelle/anims/Walking-michelle.fbx'
      : 'model: default\nscale: 0.4\nanimationPack: standard';
    // JSON-quoted strings are YAML scalars; user names cannot inject configuration.
    const yaml = `name: ${JSON.stringify(name)}\ndescription: ${JSON.stringify(`${name}'s spatial body. Identity and memory live on the connected worker.`)}\n${body}\nbehaviorPack:\n  - look-at-user\n  - idle-breathe\n  - anchor-to-surface\n  - proximity-greet\n  - gesture-respond\nvoice:\n  provider: edge\n  voice: ${JSON.stringify(voice)}\ntoolSurfaces:\n  - floating-card\nbinding:\n  type: ngram_entity\n  options:\n    connectionId: ${id}\n  system: |\n    This surface gives you an embodied presence. Your identity, memories, and\n    relationships remain those of the running ngram Entity.\n`;
    await writeFile(join(destination, 'shell.yaml.tmp'), yaml, 'utf8');
    await rename(join(destination, 'shell.yaml.tmp'), join(destination, 'shell.yaml'));
  } catch (error) {
    await rm(secretPath, { force: true });
    await rm(destination, { recursive: true, force: true });
    throw error;
  }
  return { slug, name, voice, bindingType: 'ngram_entity', description: 'Connected to a persistent ngram Entity.', connected: true, status };
}
