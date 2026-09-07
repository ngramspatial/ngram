import assert from 'node:assert/strict';
import test from 'node:test';
import { build } from 'esbuild';
import { fileURLToPath } from 'node:url';

const bundle = await build({
  entryPoints: [fileURLToPath(new URL('../packages/surface-webxr/src/panel-renderer.ts', import.meta.url))],
  bundle: true, write: false, platform: 'node', format: 'esm', target: 'es2022',
});
const { PanelRenderer } = await import(`data:text/javascript;base64,${Buffer.from(bundle.outputFiles[0].contents).toString('base64')}`);

// Lifecycle doubles only: no browser, WebGL, pixel rendering, or visual assertions.
function setup(t) {
  const images = [];
  const videos = [];
  let pendingPlay;
  function element(tag) {
    const node = {
      tag, style: {}, sandbox: { add() {} }, children: [],
      appendChild(child) { this.children.push(child); child.parent = this; },
      remove() { this.parent?.children.splice(this.parent.children.indexOf(this), 1); },
      set innerHTML(value) { this.textContent = value.replace(/<[^>]+>/g, ''); },
    };
    if (tag === 'canvas') {
      node.width = 300; node.height = 150;
      const context = {
        images: [], clearRect() {}, fillRect() {}, fillText() {}, scale() {}, setTransform() {},
        beginPath() {}, moveTo() {}, lineTo() {}, stroke() {}, roundRect() {}, fill() {},
        drawImage(image) { this.images.push(image); },
        measureText(text) { return { width: text.length * 8 }; },
        save() {}, restore() {},
      };
      node.getContext = () => context;
    }
    if (tag === 'video') {
      Object.assign(node, {
        readyState: 4, videoWidth: 1920, videoHeight: 1080, currentTime: 0, paused: true, playCalls: 0,
        play() { this.playCalls++; this.paused = false; return pendingPlay ?? Promise.resolve(); },
        pause() { this.paused = true; },
      });
      videos.push(node);
    }
    return node;
  }
  const oldDocument = globalThis.document, oldImage = globalThis.Image;
  globalThis.document = { createElement: element, body: element('body'), fonts: { load: async () => [] } };
  globalThis.Image = class {
    constructor() { images.push(this); }
    set src(value) {
      this.url = value;
      if (value === '/ngram-logo.svg') queueMicrotask(() => this.onload?.());
    }
  };
  const renderer = new PanelRenderer(800, 600);
  t.after(() => {
    renderer.dispose();
    globalThis.document = oldDocument;
    globalThis.Image = oldImage;
  });
  return { renderer, images, videos, delayPlay(promise) { pendingPlay = promise; } };
}

test('XR appearance changes preserve video identity, position, and paused state', async t => {
  const { renderer, videos } = setup(t);
  await renderer.render('video', '/clip.mp4', 'A video');
  const video = videos[0]; video.currentTime = 12.5; video.pause();
  renderer.setImmersive(true); renderer.setFrameAspect(4 / 3);
  await renderer.rerender();
  renderer.setTheme(false); await renderer.rerender();
  renderer.setImmersive(false); await renderer.rerender();
  assert.equal(videos.length, 1);
  assert.equal(renderer.videoEl, video);
  assert.equal(video.currentTime, 12.5);
  assert.equal(video.paused, true);
  assert.equal(video.playCalls, 1);
});

test('XR appearance changes retain the running sandbox frame', async t => {
  const { renderer } = setup(t);
  await renderer.render('sandbox', '<p>A running app</p>');
  const frame = renderer.sandboxEl;
  frame.applicationState = { draft: 'Keep this draft' };
  renderer.setImmersive(true); await renderer.rerender();
  renderer.setTheme(false); await renderer.rerender();
  assert.equal(renderer.sandboxEl, frame);
  assert.deepEqual(frame.applicationState, { draft: 'Keep this draft' });
});

test('replacing live content stops its updates and releases its element', async t => {
  const { renderer, videos } = setup(t);
  await renderer.render('video', '/clip.mp4');
  await renderer.render('html', '<p>Replacement content</p>');
  assert.equal(videos[0].paused, true);
  assert.equal(renderer.videoEl, null);
  assert.equal(renderer.liveInterval, null);
  assert.equal(renderer.hiddenContainer.children.length, 0);
});

test('a late image load cannot overwrite newer panel content', async t => {
  const { renderer, images } = setup(t);
  const older = renderer.render('image', '/older.png');
  await new Promise(setImmediate);
  const image = images.find(item => item.url === '/older.png');
  assert.ok(image);
  await renderer.render('html', '<p>New content</p>');
  const before = renderer.canvasSize();
  image.onload(); await older;
  assert.deepEqual(renderer.canvasSize(), before);
  assert.equal(renderer.ctx.images.includes(image), false);
});

test('disposal during pending playback cannot restart a frame-update timer', async t => {
  const fixture = setup(t);
  let finishPlay;
  fixture.delayPlay(new Promise(resolve => { finishPlay = resolve; }));
  const pending = fixture.renderer.render('video', '/clip.mp4');
  await new Promise(setImmediate);
  fixture.renderer.dispose(); finishPlay(); await pending;
  assert.equal(fixture.renderer.liveInterval, null);
  assert.equal(fixture.renderer.videoEl, null);
  assert.equal(fixture.videos[0].paused, true);
});

test('tall restored panel frames keep their proportions within the texture size budget', async t => {
  const { renderer } = setup(t);
  renderer.setFrameAspect(.1);
  for (const type of ['html', 'video']) {
    const size = await renderer.render(type, type === 'video' ? '/clip.mp4' : '<p>Content</p>');
    assert.ok(size.width <= 1600 && size.height <= 1600);
    assert.equal(size.width / size.height, .1);
  }
});
