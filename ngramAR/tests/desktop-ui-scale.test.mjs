import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';

const htmlPath = new URL('../packages/surface-webxr/public/index.html', import.meta.url);

function rule(css, selector) {
  const escaped = selector.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  const match = css.match(new RegExp(`${escaped}\\s*\\{([^}]+)\\}`));
  assert.ok(match, `expected a CSS rule for ${selector}`);
  return match[1];
}

test('desktop HUD is 1.25x while the WebGL viewport keeps its original geometry', async () => {
  const html = await readFile(htmlPath, 'utf8');
  const css = html.slice(html.indexOf('<style>'), html.indexOf('</style>'));
  const root = rule(css, ':root');
  const viewport = rule(css, '.viewport-container');
  const canvas = rule(css, '#viewport');

  assert.match(root, /--ui-scale:\s*1\.25/);
  assert.match(root, /--topbar-h:\s*45px/);
  assert.match(root, /--sidebar-w:\s*275px/);
  assert.match(root, /--drawer-w:\s*450px/);

  assert.match(root, /--scene-topbar-h:\s*36px/);
  assert.match(root, /--scene-sidebar-w:\s*220px/);
  assert.match(viewport, /top:\s*var\(--scene-topbar-h\)/);
  assert.match(viewport, /left:\s*var\(--scene-sidebar-w\)/);
  assert.match(canvas, /width:\s*100%;\s*height:\s*100%/);
  assert.doesNotMatch(viewport, /zoom|scale\s*\(/);
  assert.doesNotMatch(canvas, /zoom|scale\s*\(/);
});

test('scaled panel chrome uses physical pixels rather than CSS zoom', async () => {
  const [html, uiSource, appPanelSource] = await Promise.all([
    readFile(htmlPath, 'utf8'),
    readFile(new URL('../packages/surface-webxr/src/ui.ts', import.meta.url), 'utf8'),
    readFile(new URL('../packages/surface-webxr/src/app-panel.ts', import.meta.url), 'utf8'),
  ]);
  const css = html.slice(html.indexOf('<style>'), html.indexOf('</style>'));

  assert.match(rule(css, '.topbar-btn'), /width:\s*32\.5px;\s*height:\s*32\.5px/);
  assert.match(rule(css, '.sidebar-search-input'), /font-size:\s*13\.75px/);
  assert.match(rule(css, '.command-inner'), /max-width:\s*775px/);
  assert.doesNotMatch(css, /\bzoom\s*:/);

  assert.match(uiSource, /const UI_SCALE = 1\.25/);
  assert.match(uiSource, /--scene-sidebar-w/);
  assert.match(uiSource, /--scene-devpanel-h/);
  assert.match(appPanelSource, /h \+ 45/);
  assert.match(appPanelSource, /\? '45px'/);
});
