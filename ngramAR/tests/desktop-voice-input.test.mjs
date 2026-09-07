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
    this.onend?.();
  }

  abort() {
    this.aborted = true;
  }

  emitResult(text, isFinal = true) {
    const result = [{ transcript: text }];
    result.isFinal = isFinal;
    this.onresult?.({ resultIndex: 0, results: [result] });
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
const { mergeVoiceDraft } = await import('../packages/surface-webxr/src/voice-draft.ts');

test('desktop dictation becomes an editable command draft', () => {
  assert.equal(mergeVoiceDraft('', '  hello rook  '), 'hello rook');
  assert.equal(mergeVoiceDraft('please open', ' a panel '), 'please open a panel');
  assert.equal(mergeVoiceDraft('keep this', '   '), 'keep this');
});

test('desktop voice recognition returns final text without the transcription endpoint', async () => {
  const speech = new SpeechHandler();
  const states = [];
  const transcripts = [];
  speech.onListeningState((state) => states.push(state));

  await speech.startListening((text) => transcripts.push(text), 'browser');
  const recognition = FakeSpeechRecognition.instances.at(-1);
  recognition.emitResult('hello rook');
  recognition.emitEnd();

  assert.equal(recognition.started, true);
  assert.deepEqual(transcripts, ['hello rook']);
  assert.deepEqual(states, ['recording', 'transcribing', 'idle']);
  assert.equal(speech.isListening, false);
});

test('desktop mic toggle delivers the best interim text when manually stopped', async () => {
  const speech = new SpeechHandler();
  const transcripts = [];

  await speech.startListening((text) => transcripts.push(text), 'browser');
  const recognition = FakeSpeechRecognition.instances.at(-1);
  recognition.emitResult('open a panel', false);
  speech.stopListening();

  assert.equal(recognition.stopped, true);
  assert.deepEqual(transcripts, ['open a panel']);
  assert.equal(speech.isListening, false);
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
