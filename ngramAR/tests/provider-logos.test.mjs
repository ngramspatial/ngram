import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { readFile } from 'node:fs/promises';
import test from 'node:test';
import { providerLogo } from '../packages/surface-webxr/src/provider-logos.ts';
import { BRAIN_PROVIDERS } from '../packages/gateway/dist/brain-config.js';

test('every offered provider has bundled artwork with verified provenance', async () => {
  const base = new URL('../packages/surface-webxr/public/', import.meta.url);
  const manifest = JSON.parse(await readFile(new URL('providers/manifest.json', base)));
  for (const provider of BRAIN_PROVIDERS) {
    const url = providerLogo(provider.id);
    assert.equal(url, `/providers/${provider.id}.svg`);
    const svg = await readFile(new URL(url.slice(1), base));
    assert.match(svg.toString(), /<svg\s/);
    assert.doesNotMatch(svg.toString(), /<(script|foreignObject|image|use|style)\b|\son\w+=|\shref=/i);
    if (provider.id !== 'custom') {
      assert.equal(createHash('sha256').update(svg).digest('hex'), manifest.icons[provider.id].sha256);
    }
  }
});

test('untrusted provider names cannot request external or arbitrary assets', () => {
  for (const id of ['https://tracker.invalid/logo', '//tracker.invalid', '../config', '__proto__', 'constructor']) {
    assert.equal(providerLogo(id), '/providers/custom.svg');
  }
});
