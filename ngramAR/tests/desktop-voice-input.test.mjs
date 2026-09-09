import assert from 'node:assert/strict';
import test from 'node:test';

class FakeSpeechRecognition {
  static instances = [];

  constructor() {
    FakeSpeechRecognition.instances.push(this);
  }

  start() {
    this.started = true;
  }

  stop() {
    this.stopped = true;
    if (!this.deferEnd) this.onend?.();
  }

  abort() {
    this.aborted = true;
  }

  emitResult(text, isFinal = true) {
    this.emitResults([[text, isFinal]]);
  }

  emitResults(entries, resultIndex = 0) {
    const results = entries.map(([text, isFinal]) => {
      const result = [{ transcript: text }];
      result.isFinal = isFinal;
      return result;
    });
    this.onresult?.({ resultIndex, results });
  }

  emitEnd() {
    this.onend?.();
  }

  emitError(error) {
    this.onerror?.({ error });
  }
}

globalThis.window = {
  location: { protocol: 'http:', host: 'localhost:3000' },
  webkitSpeechRecognition: FakeSpeechRecognition,
};

const { SpeechHandler } = await import('../packages/surface-webxr/src/speech.ts');
const { VoiceDraft } = await import('../packages/surface-webxr/src/voice-draft.ts');
const { formatDictation } = await import('../packages/surface-webxr/src/dictation-text.js');

test('sentences get capitals and punctuation without changing names, numbers or URLs', () => {
  assert.equal(formatDictation('hello rook', false), 'Hello rook');
  assert.equal(formatDictation('hello rook', true), 'Hello rook.');
  assert.equal(formatDictation('can you make a coin', true), 'Can you make a coin?');
  assert.equal(formatDictation("i think i'm ready. let's build", true), "I think I'm ready. Let's build.");
  assert.equal(formatDictation('use Blender 4.3 and https://ngram.com with NASA', true), 'Use Blender 4.3 and https://ngram.com with NASA.');
  assert.equal(formatDictation('hello, Rook!', true), 'Hello, Rook!');
  assert.equal(formatDictation('what a lovely day', true), 'What a lovely day.');
  assert.equal(formatDictation('bonjour', true, 'fr-FR'), 'Bonjour.');
});

test('spoken punctuation and paragraphs appear while dictating', () => {
  assert.equal(formatDictation('hello comma rook question mark new paragraph i am ready exclamation point', true), 'Hello, rook?\n\nI am ready!');
  assert.equal(formatDictation('start full stop new line next'), 'Start.\nNext');
});

test('live hypotheses replace the dictated range instead of appending duplicates', () => {
  const draft = new VoiceDraft('My request:');
  let next = draft.update('My request:', 'Open a');
  assert.equal(next.value, 'My request: Open a');
  next = draft.update(next.value, 'Open a panel.');
  assert.equal(next.value, 'My request: Open a panel.');
  assert.equal(next.selectionStart, next.value.length);
  assert.equal(draft.update(next.value, '').value, 'My request:');
});

test('dictation replaces a selection and preserves its surrounding text', () => {
  const draft = new VoiceDraft('Before old after', 7, 10);
  let next = draft.update('Before old after', 'New', 7, 10);
  assert.equal(next.value, 'Before New after');
  assert.equal(next.selectionStart, 10);
  next = draft.update(next.value, 'New words.', 0, 0);
  assert.equal(next.value, 'Before New words. after');
  assert.equal(next.selectionStart, 0, 'recognition must not steal a caret elsewhere');
});

test('typed prefix and suffix edits survive continued recognition', () => {
  const draft = new VoiceDraft('Note:');
  let next = draft.update('Note:', 'Hello');
  next = draft.update('My ' + next.value, 'Hello rook.', 2, 2);
  assert.equal(next.value, 'My Note: Hello rook.');
  assert.equal(next.selectionStart, 2);
  next = draft.update(next.value + ' (please)', 'Hello Rook.', 30, 30);
  assert.equal(next.value, 'My Note: Hello Rook. (please)');
});

test('a manual correction takes ownership over the recognizer', () => {
  const draft = new VoiceDraft('');
  draft.update('', 'Make a blue coin');
  let next = draft.update('Make a gold coin', 'Make a blue coin', 16, 16);
  assert.equal(next.value, 'Make a gold coin');
  next = draft.update(next.value, 'Make a blue coin with physics.');
  assert.equal(next.value, 'Make a gold coin with physics.');
});

test('deleting a live draft keeps old hypotheses from resurrecting it', () => {
  const draft = new VoiceDraft('');
  draft.update('', 'Old words');
  assert.equal(draft.update('', 'Old words.').value, '');
  assert.equal(draft.update('', 'Old words. New words').value, 'New words');
});

test('desktop recognition streams provisional words and finalized sentences immediately', async (t) => {
  const speech = new SpeechHandler();
  t.after(() => speech.dispose());
  const states = [];
  const transcripts = [];
  speech.onListeningState((state) => states.push(state));

  await speech.startListening((text) => transcripts.push(text), 'browser');
  const recognition = FakeSpeechRecognition.instances.at(-1);
  recognition.emitResult('hello', false);
  assert.deepEqual(transcripts, ['Hello']);
  recognition.emitResult('hello rook', false);
  recognition.emitResult('hello rook');

  assert.equal(recognition.started, true);
  assert.equal(recognition.continuous, true);
  assert.equal(recognition.interimResults, true);
  assert.deepEqual(transcripts, ['Hello', 'Hello rook', 'Hello rook.']);
  assert.deepEqual(states, ['recording']);
  assert.equal(speech.isListening, true);
});

test('multiple finals and revised or removed interim results never duplicate words', async (t) => {
  const speech = new SpeechHandler();
  t.after(() => speech.dispose());
  let text;
  await speech.startListening((value) => { text = value; }, 'browser');
  const recognition = FakeSpeechRecognition.instances.at(-1);
  recognition.emitResults([['hello rook', true], ['make', false], ['a coin', false]]);
  assert.equal(text, 'Hello rook. Make a coin');
  recognition.emitResults([['hello rook', true], ['make a sword', false]], 1);
  assert.equal(text, 'Hello rook. Make a sword');
  recognition.emitResults([['hello rook', true]], 1);
  assert.equal(text, 'Hello rook.');
  recognition.emitResults([['hello rook', true], ['make a sword', true]], 1);
  assert.equal(text, 'Hello rook. Make a sword.');
});

test('silence restarts the service while the mic stays on, retaining earlier sentences', async (t) => {
  t.mock.timers.enable({ apis: ['setTimeout'] });
  const speech = new SpeechHandler();
  t.after(() => speech.dispose());
  const states = [];
  let text;
  speech.onListeningState((state) => states.push(state));
  await speech.startListening((value) => { text = value; }, 'browser');
  const first = FakeSpeechRecognition.instances.at(-1);
  first.emitResult('hello rook');
  first.emitError('no-speech');
  first.emitEnd();
  assert.equal(speech.isListening, true);
  t.mock.timers.tick(300);
  const second = FakeSpeechRecognition.instances.at(-1);
  assert.notEqual(second, first);
  second.emitResult('make a coin', false);
  assert.equal(text, 'Hello rook. Make a coin');
  first.emitResult('stale words');
  assert.equal(text, 'Hello rook. Make a coin');
  assert.deepEqual(states, ['recording']);
  await speech.stopListening();
  assert.equal(text, 'Hello rook. Make a coin.');
  assert.deepEqual(states, ['recording', 'transcribing', 'idle']);
});

test('manual stop during a restart gap cannot turn the mic back on', async (t) => {
  t.mock.timers.enable({ apis: ['setTimeout'] });
  const speech = new SpeechHandler();
  await speech.startListening(() => {}, 'browser');
  const recognition = FakeSpeechRecognition.instances.at(-1);
  recognition.emitEnd();
  await speech.stopListening();
  t.mock.timers.tick(10000);
  assert.equal(FakeSpeechRecognition.instances.at(-1), recognition);
  assert.equal(speech.isListening, false);
});

test('desktop mic toggle retains both a final prefix and its interim tail', async () => {
  const speech = new SpeechHandler();
  const transcripts = [];

  await speech.startListening((text) => transcripts.push(text), 'browser');
  const recognition = FakeSpeechRecognition.instances.at(-1);
  recognition.emitResults([['hello rook', true], ['open a panel', false]]);
  await speech.stopListening();

  assert.equal(recognition.stopped, true);
  assert.deepEqual(transcripts, ['Hello rook. Open a panel', 'Hello rook. Open a panel.']);
  assert.equal(speech.isListening, false);
});

test('Send can await final recognition, and late callbacks cannot fill the next draft', async () => {
  const speech = new SpeechHandler();
  let text = '';
  await speech.startListening((value) => { text = value; }, 'browser');
  const recognition = FakeSpeechRecognition.instances.at(-1);
  recognition.deferEnd = true;
  recognition.emitResult('make a', false);
  let sent;
  const stop = speech.stopListening();
  assert.equal(speech.stopListening(), stop, 'double stop shares the pending finalization');
  const send = stop.then(() => { sent = text; text = ''; });
  await Promise.resolve();
  assert.equal(sent, undefined);
  recognition.emitResult('make a coin');
  recognition.emitEnd();
  await send;
  assert.equal(sent, 'Make a coin.');
  recognition.emitResult('ghost text');
  assert.equal(text, '');
  await speech.startListening((value) => { text = value; }, 'browser');
  recognition.emitEnd();
  assert.equal(speech.isListening, true);
  await speech.stopListening();
});

test('Send has a bounded finalization fallback if the service never reports end', async (t) => {
  t.mock.timers.enable({ apis: ['setTimeout'] });
  const speech = new SpeechHandler();
  let text;
  await speech.startListening((value) => { text = value; }, 'browser');
  const recognition = FakeSpeechRecognition.instances.at(-1);
  recognition.deferEnd = true;
  recognition.emitResult('keep these words', false);
  const stopped = speech.stopListening();
  t.mock.timers.tick(1500);
  await stopped;
  assert.equal(text, 'Keep these words.');
  assert.equal(speech.isListening, false);
  assert.equal(recognition.aborted, true);
});

test('desktop recognition surfaces permission failures instead of silently doing nothing', async () => {
  const speech = new SpeechHandler();
  const states = [];
  speech.onListeningState((state, detail) => states.push({ state, detail }));

  await speech.startListening(() => {}, 'browser');
  FakeSpeechRecognition.instances.at(-1).emitError('not-allowed');

  assert.deepEqual(states, [
    { state: 'recording', detail: undefined },
    { state: 'error', detail: 'Microphone permission was denied.' },
  ]);
  assert.equal(speech.isListening, false);
});

test('a fatal service error preserves the visible draft and does not endlessly retry', async (t) => {
  t.mock.timers.enable({ apis: ['setTimeout'] });
  const speech = new SpeechHandler();
  let text;
  let error;
  speech.onListeningState((state, detail) => { if (state === 'error') error = detail; });
  await speech.startListening((value) => { text = value; }, 'browser');
  const recognition = FakeSpeechRecognition.instances.at(-1);
  recognition.emitResult('keep my draft', false);
  recognition.emitError('network');
  recognition.emitEnd();
  t.mock.timers.tick(10000);
  assert.equal(text, 'Keep my draft.');
  assert.match(error, /could not be reached/);
  assert.equal(FakeSpeechRecognition.instances.at(-1), recognition);
  assert.equal(speech.isListening, false);
});

test('leaving the scene cancels pending restarts and results', async (t) => {
  t.mock.timers.enable({ apis: ['setTimeout'] });
  const speech = new SpeechHandler();
  const text = [];
  await speech.startListening((value) => text.push(value), 'browser');
  const recognition = FakeSpeechRecognition.instances.at(-1);
  recognition.emitEnd();
  speech.dispose();
  t.mock.timers.tick(10000);
  recognition.emitResult('do not send');
  assert.deepEqual(text, []);
  assert.equal(FakeSpeechRecognition.instances.at(-1), recognition);
  assert.equal(speech.isListening, false);
});

function recordedFixture(t) {
  const track = { stopped: false, stop() { this.stopped = true; } };
  const stream = { getTracks: () => [track] };
  const oldDevices = Object.getOwnPropertyDescriptor(navigator, 'mediaDevices');
  Object.defineProperty(navigator, 'mediaDevices', { configurable: true, value: { getUserMedia: async () => stream } });
  class Recorder {
    static isTypeSupported() { return true; }
    constructor() { Recorder.instance = this; this.state = 'inactive'; }
    start() { this.state = 'recording'; }
    stop() {
      this.state = 'inactive';
      this.ondataavailable?.({ data: new Blob(['audio']) });
      void this.onstop?.();
    }
  }
  globalThis.MediaRecorder = window.MediaRecorder = Recorder;
  t.after(() => {
    if (oldDevices) Object.defineProperty(navigator, 'mediaDevices', oldDevices);
    else delete navigator.mediaDevices;
    delete globalThis.MediaRecorder;
    delete window.MediaRecorder;
  });
  const speech = new SpeechHandler();
  speech.audioContext = {
    createMediaStreamSource: () => ({ connect() {} }),
    createAnalyser: () => ({ frequencyBinCount: 8, getByteFrequencyData(buf) { buf.fill(0); } }),
    close() {},
  };
  t.after(() => speech.dispose());
  return { speech, Recorder, track };
}

test('recorded XR input stays active through silence and beyond thirty seconds until stopped', async (t) => {
  t.mock.timers.enable({ apis: ['setTimeout'] });
  const { speech, Recorder, track } = recordedFixture(t);
  let uploads = 0;
  t.mock.method(globalThis, 'fetch', async () => {
    uploads++;
    return { ok: true, json: async () => ({ text: 'A complete recording.' }) };
  });
  const transcripts = [];
  await speech.startListening((text) => transcripts.push(text), 'recorded');
  t.mock.timers.tick(120000);
  assert.equal(speech.isListening, true);
  assert.equal(Recorder.instance.state, 'recording');
  assert.equal(uploads, 0);
  assert.deepEqual(transcripts, []);
  await speech.stopListening();
  assert.equal(uploads, 1);
  assert.deepEqual(transcripts, ['A complete recording.']);
  assert.equal(track.stopped, true);
  assert.equal(speech.isListening, false);
});

test('cancelled recordings cannot deliver after the user leaves the scene', async (t) => {
  const { speech } = recordedFixture(t);
  let respond;
  t.mock.method(globalThis, 'fetch', () => new Promise((resolve) => { respond = resolve; }));
  const transcripts = [];
  await speech.startListening((text) => transcripts.push(text), 'recorded');
  const stopped = speech.stopListening();
  speech.cancelListening();
  respond({ ok: true, json: async () => ({ text: 'Late recording' }) });
  await stopped;
  await new Promise((resolve) => setImmediate(resolve));
  assert.deepEqual(transcripts, []);
});

test('cancelling while microphone permission is pending releases a late stream', async (t) => {
  const { speech, track } = recordedFixture(t);
  let grant;
  t.mock.method(navigator.mediaDevices, 'getUserMedia', () => new Promise((resolve) => { grant = resolve; }));
  const started = speech.startListening(() => {}, 'recorded');
  await speech.stopListening();
  grant({ getTracks: () => [track] });
  await started;
  assert.equal(track.stopped, true);
  assert.equal(speech.isListening, false);
});
