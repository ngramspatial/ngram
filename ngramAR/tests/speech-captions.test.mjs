import assert from 'node:assert/strict';
import test from 'node:test';
import { captionPages, captionAt } from '../packages/surface-webxr/src/speech-captions.js';
import { SpeechHandler } from '../packages/surface-webxr/src/speech.ts';

test('long responses retain every word across bounded caption pages', () => {
  const text = 'A long answer with useful details. '.repeat(100);
  const pages = captionPages(text);
  assert.ok(pages.length > 20);
  assert.ok(pages.every(page => page.text.length <= 80));
  assert.equal(pages.map(page => page.text).join(' '), text.trim());
  assert.equal(captionAt(pages, pages[2].start), pages[2].text);
  assert.equal(captionAt(pages, text.length + 500), pages.at(-1).text);
  assert.equal(captionAt([], 0), '');
  assert.ok(captionPages('x'.repeat(1000)).every(page => page.text.length <= 80));
  assert.ok(captionPages('空間で話しています。'.repeat(20)).every(page => page.text.length <= 40));
});

function audioHarness(t) {
  globalThis.window = { location: { protocol: 'http:', host: 'localhost' } };
  const speech = new SpeechHandler();
  const decodes = [], sources = [], ticks = new Set();
  const ctx = {
    currentTime: 0, destination: {}, state: 'running',
    decodeAudioData: (_bytes, ok, fail) => decodes.push({ ok, fail }),
    createBufferSource: () => {
      const source = { connect() {}, start() { this.started = true; }, stop() { this.stopped = true; } };
      sources.push(source); return source;
    },
  };
  speech.audioContext = ctx;
  t.mock.method(globalThis, 'setInterval', callback => { ticks.add(callback); return callback; });
  t.mock.method(globalThis, 'clearInterval', callback => ticks.delete(callback));
  t.after(() => speech.stopPlayback());
  return { speech, decodes, sources, ctx, ticks };
}

test('captions follow playback duration, stay through long audio, and queue in speech order', t => {
  const { speech, decodes, sources, ctx, ticks } = audioHarness(t);
  const captions = [], lifecycle = [];
  const text = 'This sentence is part of a long spoken response. '.repeat(30);
  speech.playResponse({ text, audioData: 'AA==', onCaption: value => captions.push(value), onStart: () => lifecycle.push('first start') }, () => lifecycle.push('first end'));
  speech.playResponse({ text: 'Second response.', audioData: 'AA==', onCaption: value => captions.push(value), onStart: () => lifecycle.push('second start') }, () => lifecycle.push('second end'));
  assert.deepEqual(captions, []);
  assert.equal(decodes.length, 1);
  decodes.shift().ok({ duration: 60 });
  assert.equal(captions[0], captionPages(text)[0].text);
  ctx.currentTime = 35; for (const tick of ticks) tick();
  assert.equal(captions.at(-1), captionAt(captionPages(text), text.length * 35 / 60));
  assert.deepEqual(lifecycle, ['first start'], 'eight seconds cannot end a sixty-second response');
  sources[0].onended();
  decodes.shift().ok({ duration: 3 });
  assert.equal(captions.at(-1), 'Second response.');
  sources[1].onended();
  assert.deepEqual(lifecycle, ['first start', 'first end', 'second start', 'second end']);
  assert.equal(ticks.size, 0);
});

test('Stop cancels a pending download and cannot resurrect queued audio or captions', async t => {
  const { speech, sources, ticks } = audioHarness(t);
  let resolveDownload, signal;
  t.mock.method(globalThis, 'fetch', (_url, options) => { signal = options.signal; return new Promise(resolve => { resolveDownload = resolve; }); });
  const ended = [], captions = [];
  speech.playResponse({ text: 'Pending response', audioUrl: '/audio.wav', onCaption: value => captions.push(value) }, cancelled => ended.push(cancelled));
  speech.playResponse({ text: 'Queued response', audioData: 'AA==' }, () => ended.push('queued'));
  speech.stopPlayback();
  resolveDownload({ ok: true, arrayBuffer: async () => new ArrayBuffer(1) });
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(signal.aborted, true);
  assert.deepEqual(ended, [true]);
  assert.deepEqual(captions, []);
  assert.equal(sources.length, 0);
  assert.equal(ticks.size, 0);
});

test('browser boundaries advance captions and duplicate end/error events finish only once', t => {
  const { speech, ticks } = audioHarness(t);
  let utterance;
  globalThis.SpeechSynthesisUtterance = class { constructor(text) { this.text = text; } };
  window.speechSynthesis = { speak: value => { utterance = value; }, cancel() {}, getVoices: () => [] };
  const captions = []; let ended = 0;
  const text = 'Browser speech should remain visible until its actual end. '.repeat(20);
  speech.playResponse({ text, onCaption: value => captions.push(value) }, () => ended++);
  assert.deepEqual(captions, []);
  utterance.onstart();
  utterance.onboundary({ charIndex: 500 });
  assert.equal(captions.at(-1), captionAt(captionPages(text), 500));
  utterance.onend(); utterance.onerror();
  assert.equal(ended, 1);
  assert.equal(ticks.size, 0);
});
