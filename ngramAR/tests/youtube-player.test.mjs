import assert from 'node:assert/strict';
import test from 'node:test';
import { build } from 'esbuild';
import { fileURLToPath } from 'node:url';

const bundle = await build({
  entryPoints: [fileURLToPath(new URL('../packages/surface-webxr/src/youtube-player.ts', import.meta.url))],
  bundle: true, write: false, format: 'esm', platform: 'node',
});
const moduleUrl = `data:text/javascript;base64,${Buffer.from(bundle.outputFiles[0].contents).toString('base64')}`;
let sequence = 0;

async function setup(t) {
  const oldWindow = globalThis.window, oldDocument = globalThis.document;
  const players = [], containers = [];
  const frame = { innerHTML: '' };
  globalThis.document = {
    head: { appendChild() {} }, createElement: () => ({}),
    getElementById: id => id === 'yt-player-frame' ? frame : null,
  };
  globalThis.window = { YT: { PlayerState: { PLAYING: 1, PAUSED: 2 }, Player: class {
    constructor(_frame, options) { this.options = options; this.destroyed = false; players.push(this); }
    getPlayerState() { return 1; }
    getCurrentTime() { return 0; }
    getDuration() { return 60; }
    setVolume() {} pauseVideo() {} playVideo() {} mute() {}
    destroy() { this.destroyed = true; }
  } } };
  const { YouTubePlayer } = await import(`${moduleUrl}#${++sequence}`);
  const player = new YouTubePlayer();
  // Lifecycle-only DOM double. Actual pointer behavior is checked in the browser.
  player.createDOM = () => {
    if (player.container) return;
    const container = { removed: false, remove() { this.removed = true; } };
    containers.push(container); player.container = container;
  };
  t.after(() => { player.dispose(); globalThis.window = oldWindow; globalThis.document = oldDocument; });
  return { player, players, containers, ready: () => window.onYouTubeIframeAPIReady() };
}

test('closing during API loading removes the window and prevents a late reopen', async t => {
  const { player, players, containers, ready } = await setup(t);
  const pending = player.play('first-video');
  assert.equal(containers.length, 1, 'Close must be available during loading');
  player.stop();
  ready(); await pending;
  assert.equal(containers[0].removed, true);
  assert.equal(player.isOpen, false);
  assert.equal(player.getSavedState(), null);
  assert.equal(players.length, 0);
});

test('only the latest requested video mounts after the API finishes loading', async t => {
  const { player, players, ready } = await setup(t);
  const first = player.play('first-video');
  const second = player.play('second-video');
  ready(); await Promise.all([first, second]);
  assert.equal(players.length, 1);
  assert.equal(players[0].options.videoId, 'second-video');
});

test('stale iframe callbacks cannot restart playback after Close', async t => {
  const { player, players, ready } = await setup(t);
  const pending = player.play('first-video');
  ready(); await pending;
  const old = players[0];
  player.onError(() => assert.fail('a closed player must not start a fallback'));
  player.stop();
  old.options.events.onReady();
  old.options.events.onStateChange({ data: 1 });
  old.options.events.onError({ data: 150 });
  assert.equal(old.destroyed, true);
  assert.equal(players.length, 1);
  assert.equal(player.isOpen, false);
});

test('Close removes the panel even when YouTube iframe cleanup throws', async t => {
  const { player, players, containers, ready } = await setup(t);
  const pending = player.play('first-video');
  ready(); await pending;
  t.mock.method(console, 'warn', () => {});
  players[0].destroy = () => { throw new Error('iframe already gone'); };
  assert.doesNotThrow(() => player.stop());
  assert.equal(containers[0].removed, true);
  assert.equal(player.isOpen, false);
  assert.equal(player.getSavedState(), null);
});
