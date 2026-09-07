import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';

import {
  UI_THEMES,
  isThemeName,
  nextTheme,
  normalizeTheme,
  themeOverridesEnvironment,
  themeUsesDarkPanels,
} from '../packages/surface-webxr/src/theme.js';

test('theme names normalize and cycle through all three choices', () => {
  assert.deepEqual([...UI_THEMES], ['light', 'dark', 'periwinkle']);
  assert.equal(isThemeName('periwinkle'), true);
  assert.equal(isThemeName('unknown'), false);
  assert.equal(normalizeTheme('unknown'), 'light');
  assert.equal(nextTheme('light'), 'dark');
  assert.equal(nextTheme('dark'), 'periwinkle');
  assert.equal(nextTheme('periwinkle'), 'light');
});

test('only the dark theme selects dark binary panel fallbacks', () => {
  assert.equal(themeUsesDarkPanels('light'), false);
  assert.equal(themeUsesDarkPanels('periwinkle'), false);
  assert.equal(themeUsesDarkPanels('dark'), true);
});

test('UI themes allow explicit scene environments to remain visible', () => {
  assert.equal(themeOverridesEnvironment('light'), false);
  assert.equal(themeOverridesEnvironment('dark'), false);
  assert.equal(themeOverridesEnvironment('periwinkle'), false);
});

test('the WebXR shell exposes the periwinkle theme control and palette', async () => {
  const html = await readFile(new URL('../packages/surface-webxr/public/index.html', import.meta.url), 'utf8');
  assert.match(html, /\[data-theme="periwinkle"\]/);
  assert.match(html, /<option value="periwinkle">Periwinkle<\/option>/);
  assert.match(html, /id="theme-icon-periwinkle"/);
  assert.match(html, /--bg:\s*#6E7DFF/);
  assert.match(html, /--text:\s*#FFFFFF/);
  const palette = html.match(/\[data-theme="periwinkle"\]\s*\{([^}]+)\}/)[1];
  const ui = palette.match(/--bg:\s*(#[\da-f]+)/i)[1];
  const scene = palette.match(/--scene-bg-edge:\s*(#[\da-f]+)/i)[1];
  const luminance = (hex) => {
    const rgb = hex.slice(1).match(/../g).map(v => parseInt(v, 16) / 255)
      .map(v => v <= .04045 ? v / 12.92 : ((v + .055) / 1.055) ** 2.4);
    return rgb[0] * .2126 + rgb[1] * .7152 + rgb[2] * .0722;
  };
  assert.ok((luminance(ui) + .05) / (luminance(scene) + .05) > 4,
    'Periwinkle chrome must remain distinct from the graphite scene');
});
