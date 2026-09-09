// @ts-nocheck
import { captionPages, captionAt } from './speech-captions.js';
import { formatDictation } from './dictation-text.js';
export interface SpeechPlaybackOptions {
  text?: string;
  audioData?: string;
  audioUrl?: string;
  speed?: number;
  voice?: string;
  notification?: { goalId: string; kind: 'progress' | 'terminal' };
  onStart?: () => void;
  onCaption?: (text: string) => void;
}
type TranscriptionCallback = (text: string) => void;
type ListeningStateCallback = (
  state: 'recording' | 'transcribing' | 'idle' | 'error',
  detail?: string,
) => void;

type ListeningMode = 'browser' | 'recorded';

interface QueuedPlay {
  play: (onDone: () => void) => void;
  onEnd: (cancelled?: boolean) => void;
  notification?: SpeechPlaybackOptions['notification'];
  expiresAt?: number;
}

export class SpeechHandler {
  audioContext: AudioContext | null = null;
  private pannerNode: PannerNode | null = null;

  private mediaStream: MediaStream | null = null;
  private mediaRecorder: MediaRecorder | null = null;
  private browserRecognition: any = null;
  private browserPrefix = '';
  private browserResults: { text: string; final: boolean }[] = [];
  private lastTranscript = '';
  private recognitionEpoch = 0;
  private restartTimer: ReturnType<typeof setTimeout> | null = null;
  private stopTimer: ReturnType<typeof setTimeout> | null = null;
  private stopPromise: Promise<void> | null = null;
  private resolveStop: (() => void) | null = null;
  private listeningMode: ListeningMode | null = null;
  private audioChunks: Blob[] = [];
  private transcribeUrl: string;
  private onTranscription: TranscriptionCallback | null = null;
  private stateCallback: ListeningStateCallback | null = null;
  private analyser: AnalyserNode | null = null;
  private analyserBuf: Uint8Array | null = null;

  private playQueue: QueuedPlay[] = [];
  private isPlaying = false;
  private playbackEpoch = 0;
  private activeSource: AudioBufferSourceNode | null = null;
  private playbackEnd: (() => void) | null = null;
  private captionTimer: ReturnType<typeof setInterval> | null = null;
  private playbackAbort: AbortController | null = null;

  isListening = false;

  constructor() {
    const proto = window.location.protocol;
    const host = window.location.host;
    this.transcribeUrl = `${proto}//${host}/api/transcribe`;
  }

  onListeningState(cb: ListeningStateCallback): void {
    this.stateCallback = cb;
  }

  ensureAudioContext(): AudioContext {
    if (!this.audioContext) {
      this.audioContext = new AudioContext();
    }
    if (this.audioContext.state === 'suspended') {
      this.audioContext.resume();
    }
    return this.audioContext;
  }

  get browserSpeechSupported(): boolean {
    return Boolean((window as any).SpeechRecognition || (window as any).webkitSpeechRecognition);
  }

  get recordedSpeechSupported(): boolean {
    return Boolean(
      navigator.mediaDevices
      && navigator.mediaDevices.getUserMedia
      && (window as any).MediaRecorder,
    );
  }

  get micSupported(): boolean {
    return this.browserSpeechSupported || this.recordedSpeechSupported;
  }

  async startListening(
    onResult: TranscriptionCallback,
    mode: ListeningMode = 'recorded',
  ): Promise<void> {
    if (this.stopPromise) await this.stopPromise;
    if (this.isListening) return;
    const epoch = ++this.recognitionEpoch;
    this.isListening = true;
    this.onTranscription = onResult;
    this.listeningMode = mode;
    this.lastTranscript = '';
    this.browserPrefix = '';
    this.browserResults = [];

    if (mode === 'browser') {
      try {
        this.startBrowserRecognition();
        this.stateCallback?.('recording');
      } catch (error) {
        this.finishListening(false);
        throw error;
      }
      return;
    }

    if (!this.recordedSpeechSupported) {
      this.finishListening(false);
      throw new Error('Recorded voice input is not available in this browser.');
    }

    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      if (epoch !== this.recognitionEpoch || !this.isListening) {
        stream.getTracks().forEach((track) => track.stop());
        return;
      }
      this.mediaStream = stream;
    } catch (err: any) {
      if (epoch !== this.recognitionEpoch) return;
      this.finishListening(false);
      throw new Error(`Mic access denied: ${err?.message ?? err}`);
    }

    try {
      const ctx = this.ensureAudioContext();
      const source = ctx.createMediaStreamSource(this.mediaStream);
      this.analyser = ctx.createAnalyser();
      this.analyser.fftSize = 256;
      this.analyser.smoothingTimeConstant = 0.8;
      source.connect(this.analyser);
      this.analyserBuf = new Uint8Array(this.analyser.frequencyBinCount);

      const mimeType = MediaRecorder.isTypeSupported('audio/webm;codecs=opus')
        ? 'audio/webm;codecs=opus'
        : MediaRecorder.isTypeSupported('audio/webm')
          ? 'audio/webm'
          : 'audio/mp4';

      this.mediaRecorder = new MediaRecorder(this.mediaStream, { mimeType });
      this.audioChunks = [];

      this.mediaRecorder.ondataavailable = (e) => {
        if (epoch === this.recognitionEpoch && e.data.size > 0) this.audioChunks.push(e.data);
      };

      this.mediaRecorder.onstop = async () => {
        if (epoch !== this.recognitionEpoch) return;
        try {
          if (this.audioChunks.length > 0) {
            const blob = new Blob(this.audioChunks, { type: mimeType });
            this.audioChunks = [];
            this.stateCallback?.('transcribing');
            const text = await this.sendForTranscription(blob);
            if (epoch !== this.recognitionEpoch) return;
            if (text) this.onTranscription?.(text);
          }
          this.finishListening();
        } catch (error: any) {
          if (epoch !== this.recognitionEpoch) return;
          this.finishListening(false);
          this.stateCallback?.('error', error?.message || 'Transcription failed.');
        }
      };
      this.mediaRecorder.onerror = () => {
        if (epoch !== this.recognitionEpoch) return;
        this.finishListening(false);
        this.stateCallback?.('error', 'Microphone recording failed.');
      };

      this.mediaRecorder.start(1000);
      this.stateCallback?.('recording');
      // A pause is not a request to send. Recording ends only on an explicit stop.
    } catch (error) {
      this.finishListening(false);
      throw error;
    }
  }

  /**
   * Desktop speech input uses the browser's native recognition path. This keeps
   * ordinary desktop dictation independent from the optional Whisper endpoint.
   */
  private startBrowserRecognition(): void {
    const Recognition = (window as any).SpeechRecognition || (window as any).webkitSpeechRecognition;
    if (!Recognition) {
      this.listeningMode = null;
      throw new Error('Desktop voice input requires a Chromium-based browser.');
    }

    const recognition = new Recognition();
    recognition.continuous = true;
    recognition.interimResults = true;
    recognition.maxAlternatives = 1;
    recognition.lang = navigator.language || 'en-US';

    this.browserRecognition = recognition;
    this.browserResults = [];

    recognition.onresult = (event: any) => {
      if (recognition !== this.browserRecognition) return;
      // Results are cumulative within this recognition run. Interim entries can
      // be replaced or removed, so appending resultIndex onward duplicates text.
      this.browserResults = [];
      for (let i = 0; i < event.results.length; i++) {
        this.browserResults.push({
          text: String(event.results[i]?.[0]?.transcript ?? ''),
          final: Boolean(event.results[i].isFinal),
        });
      }
      this.publishBrowserTranscript(false);
    };

    recognition.onerror = (event: any) => {
      if (recognition !== this.browserRecognition) return;
      const code = String(event?.error ?? 'unknown');
      // Silence regularly ends a browser recognition service session. Keep the
      // user's mic session alive; onend will start another service session.
      if (code === 'no-speech' || (code === 'aborted' && !this.isListening)) return;
      const messages: Record<string, string> = {
        'audio-capture': 'No working microphone was found.',
        'not-allowed': 'Microphone permission was denied.',
        'service-not-allowed': 'Browser speech recognition is disabled.',
        'network': 'The browser speech service could not be reached.',
      };
      this.publishBrowserTranscript(true);
      this.finishListening(false);
      this.stateCallback?.('error', messages[code] ?? `Voice recognition failed (${code}).`);
    };

    recognition.onend = () => {
      if (recognition !== this.browserRecognition) return;
      this.publishBrowserTranscript(true);
      if (!this.isListening) {
        this.finishListening();
        return;
      }
      this.browserPrefix = this.lastTranscript;
      this.browserResults = [];
      this.browserRecognition = null;
      const epoch = this.recognitionEpoch;
      this.restartTimer = setTimeout(() => {
        this.restartTimer = null;
        if (!this.isListening || epoch !== this.recognitionEpoch) return;
        try { this.startBrowserRecognition(); }
        catch {
          this.finishListening(false);
          this.stateCallback?.('error', 'Could not restart voice input. Tap the microphone to try again.');
        }
      }, 300);
    };

    try {
      recognition.start();
    } catch (err) {
      this.browserRecognition = null;
      throw err;
    }
  }

  private publishBrowserTranscript(complete: boolean): void {
    const language = navigator.language || 'en-US';
    const parts: string[] = [];
    let interim = '';
    for (const result of this.browserResults) {
      if (result.final) parts.push(formatDictation(result.text, true, language));
      else interim += `${result.text} `;
    }
    if (interim.trim()) parts.push(formatDictation(interim, complete, language));
    const text = [this.browserPrefix, ...parts].filter(Boolean).join(' ');
    if (text !== this.lastTranscript) {
      this.lastTranscript = text;
      this.onTranscription?.(text);
    }
  }

  /** Abandon capture when leaving its scene; never submit it to another agent. */
  cancelListening(): void {
    this.finishListening();
  }

  /** Resolves after the final words are delivered, before callers clear/send a draft. */
  stopListening(): Promise<void> {
    if (this.stopPromise) return this.stopPromise;
    if (!this.listeningMode) return Promise.resolve();
    const pending = new Promise<void>((resolve) => { this.resolveStop = resolve; });
    this.stopPromise = pending;
    this.isListening = false;
    if (this.restartTimer) { clearTimeout(this.restartTimer); this.restartTimer = null; }
    this.stateCallback?.('transcribing');
    if (this.listeningMode === 'browser') {
      if (!this.browserRecognition) {
        this.finishListening();
        return pending;
      }
      // Some browser services never send onend after stop. Keep the latest
      // visible hypothesis and fence late events instead of hanging Send.
      this.stopTimer = setTimeout(() => {
        this.publishBrowserTranscript(true);
        this.finishListening();
      }, 1500);
      try {
        this.browserRecognition.stop();
      } catch {
        this.publishBrowserTranscript(true);
        this.finishListening();
      }
    } else if (this.mediaRecorder && this.mediaRecorder.state !== 'inactive') {
      this.mediaRecorder.stop();
      this.mediaStream?.getTracks().forEach((track) => track.stop());
    } else {
      this.finishListening();
    }
    return pending;
  }

  private finishListening(notifyIdle = true): void {
    ++this.recognitionEpoch;
    this.isListening = false;
    if (this.restartTimer) { clearTimeout(this.restartTimer); this.restartTimer = null; }
    if (this.stopTimer) { clearTimeout(this.stopTimer); this.stopTimer = null; }
    const recognition = this.browserRecognition;
    this.browserRecognition = null;
    try { recognition?.abort(); } catch { /* already ended */ }
    if (this.mediaRecorder && this.mediaRecorder.state !== 'inactive') {
      try { this.mediaRecorder.stop(); } catch { /* already stopped */ }
    }
    this.mediaStream?.getTracks().forEach((track) => track.stop());
    this.mediaStream = null;
    this.mediaRecorder = null;
    this.audioChunks = [];
    this.analyser = null;
    this.analyserBuf = null;
    this.listeningMode = null;
    this.onTranscription = null;
    if (notifyIdle) this.stateCallback?.('idle');
    this.resolveStop?.();
    this.resolveStop = null;
    this.stopPromise = null;
  }

  private async sendForTranscription(blob: Blob): Promise<string> {
    try {
      const formData = new FormData();
      const ext = blob.type.includes('webm') ? 'webm' : 'mp4';
      formData.append('audio', blob, `recording.${ext}`);

      const res = await fetch(this.transcribeUrl, {
        method: 'POST',
        body: formData,
      });

      if (!res.ok) {
        const payload = await res.json().catch(() => ({}));
        const detail = typeof payload?.error === 'string'
          ? payload.error
          : `Transcription failed (${res.status}).`;
        console.warn('[speech] transcription failed:', res.status, detail);
        throw new Error(detail);
      }

      const data = await res.json();
      return typeof data.text === 'string' ? data.text.trim() : '';
    } catch (err: any) {
      console.warn('[speech] transcription error:', err);
      throw new Error(err?.message || 'The transcription service could not be reached.');
    }
  }

  // --- TTS playback (queued to prevent overlaps) ---

  stopPlayback(): void {
    this.clearCaptionTimer();
    this.playbackAbort?.abort();
    this.playbackEpoch++;
    this.playQueue = [];
    if (this.activeSource) {
      this.activeSource.onended = null;
      try { this.activeSource.stop(); } catch { /* already ended */ }
      this.activeSource = null;
    }
    window.speechSynthesis?.cancel();
    this.isPlaying = false;
    this.setMicMuted(false);
    const onEnd = this.playbackEnd;
    this.playbackEnd = null;
    onEnd?.(true);
  }

  playAudio(audioBase64: string, onEnd: () => void): void {
    this.playResponse({ audioData: audioBase64 }, onEnd);
  }

  playSpeechFallback(text: string, onEnd: () => void): void {
    this.playResponse({ text }, onEnd);
  }

  playResponse(options: SpeechPlaybackOptions, onEnd: (cancelled?: boolean) => void): void {
    // Ordinary conversation takes precedence over pending progress notices.
    // A new goal notice replaces older unspoken notices for that goal only.
    if (options.notification) this.discardQueuedNotice(options.notification.goalId, true);
    else this.discardQueuedNotice();
    this.enqueue((done) => {
      const epoch = this.playbackEpoch;
      const fallback = () => {
        if (epoch !== this.playbackEpoch) return;
        if (options.text) this.playSpeechFallbackImmediate(options.text, done, options);
        else done();
      };
      const play = (base64: string) => this.playAudioImmediate(base64, done, options, fallback);
      if (options.audioData) play(options.audioData);
      else if (options.audioUrl) {
        this.playbackAbort = new AbortController();
        fetch(options.audioUrl, { signal: this.playbackAbort.signal })
          .then(response => { if (!response.ok) throw new Error('Audio download failed'); return response.arrayBuffer(); })
          .then(buffer => {
            if (epoch !== this.playbackEpoch) return;
            const bytes = new Uint8Array(buffer);
            let binary = '';
            for (let i = 0; i < bytes.length; i++) binary += String.fromCharCode(bytes[i]);
            play(btoa(binary));
          }).catch(fallback);
      } else fallback();
    }, onEnd, options.notification);
  }

  private clearCaptionTimer(): void {
    if (this.captionTimer) clearInterval(this.captionTimer);
    this.captionTimer = null;
  }

  private startCaptions(options: SpeechPlaybackOptions, position: () => number): (index: number) => void {
    this.clearCaptionTimer();
    const pages = captionPages(options.text ?? '');
    let last = '';
    const update = (index: number) => {
      const text = captionAt(pages, index);
      if (text && text !== last) { last = text; options.onCaption?.(text); }
    };
    options.onStart?.();
    update(0);
    this.captionTimer = setInterval(() => update(position()), 100);
    return update;
  }

  discardQueuedNotice(goalId?: string, includeTerminal = false): void {
    const removed = this.playQueue.filter(item => item.notification
      && (!goalId || item.notification.goalId === goalId)
      && (includeTerminal || item.notification.kind === 'progress'));
    this.playQueue = this.playQueue.filter(item => !removed.includes(item));
    for (const item of removed) item.onEnd(true);
  }

  private enqueue(play: (onDone: () => void) => void, onEnd: () => void, notification?: SpeechPlaybackOptions['notification']): void {
    this.playQueue.push({ play, onEnd, notification,
      expiresAt: notification?.kind === 'progress' ? Date.now() + 90000 : undefined });
    if (!this.isPlaying) this.drainQueue();
  }

  private drainQueue(): void {
    while (this.playQueue[0]?.expiresAt && this.playQueue[0].expiresAt! <= Date.now()) {
      this.playQueue.shift()!.onEnd(true);
    }
    if (this.playQueue.length === 0) {
      this.isPlaying = false;
      this.setMicMuted(false);
      return;
    }
    this.isPlaying = true;
    this.setMicMuted(true);
    const item = this.playQueue.shift()!;
    const epoch = this.playbackEpoch;
    this.playbackEnd = item.onEnd;
    let finished = false;
    item.play(() => {
      if (finished || epoch !== this.playbackEpoch) return;
      finished = true;
      this.clearCaptionTimer();
      this.playbackEnd = null;
      this.activeSource = null;
      item.onEnd();
      this.drainQueue();
    });
  }

  /** Mute/unmute mic tracks to prevent agent voice from feeding back into the transcriber */
  private setMicMuted(muted: boolean): void {
    if (!this.mediaStream) return;
    for (const track of this.mediaStream.getAudioTracks()) {
      track.enabled = !muted;
    }
  }

  private playAudioImmediate(audioBase64: string, onDone: () => void, options: SpeechPlaybackOptions = {}, onError = onDone): void {
    try {
    const ctx = this.ensureAudioContext();
    const epoch = this.playbackEpoch;

    const binaryStr = atob(audioBase64);
    const bytes = new Uint8Array(binaryStr.length);
    for (let i = 0; i < binaryStr.length; i++) bytes[i] = binaryStr.charCodeAt(i);

    ctx.decodeAudioData(
      bytes.buffer as ArrayBuffer,
      (buffer) => {
        if (epoch !== this.playbackEpoch) return;
        const source = ctx.createBufferSource();
        this.activeSource = source;
        source.buffer = buffer;

        if (this.pannerNode) {
          source.connect(this.pannerNode);
        } else {
          source.connect(ctx.destination);
        }

        source.onended = onDone;
        source.start(0);
        const startedAt = ctx.currentTime;
        this.startCaptions(options, () => (options.text?.length ?? 0) * (ctx.currentTime - startedAt) / Math.max(buffer.duration, 0.01));
      },
      (err) => {
        if (epoch !== this.playbackEpoch) return;
        console.warn('[speech] audio decode failed, using browser speech');
        onError();
      },
    );
    } catch { onError(); }
  }

  private playSpeechFallbackImmediate(text: string, onDone: () => void, options: SpeechPlaybackOptions = {}): void {
    if (!window.speechSynthesis) {
      onDone();
      return;
    }
    const utterance = new SpeechSynthesisUtterance(text);
    utterance.rate = options.speed ?? 1.0;
    const selectedVoice = window.speechSynthesis.getVoices?.().find(voice => voice.voiceURI === options.voice || voice.name === options.voice);
    if (selectedVoice) utterance.voice = selectedVoice;
    utterance.pitch = 1.0;
    let finished = false;
    const finish = () => { if (finished) return; finished = true; onDone(); };
    utterance.onend = finish;
    utterance.onerror = finish;
    const epoch = this.playbackEpoch;
    let update: ((index: number) => void) | undefined;
    let boundary: number | undefined;
    utterance.onstart = () => {
      if (finished || epoch !== this.playbackEpoch) return;
      const start = performance.now();
      update = this.startCaptions({ ...options, text }, () => boundary ?? (performance.now() - start) / 1000 * 15 * utterance.rate);
    };
    utterance.onboundary = event => {
      if (finished || epoch !== this.playbackEpoch) return;
      boundary = event.charIndex;
      update?.(boundary);
    };
    window.speechSynthesis.speak(utterance);
  }

  setSpatialPosition(x: number, y: number, z: number): void {
    const ctx = this.ensureAudioContext();
    if (!this.pannerNode) {
      this.pannerNode = ctx.createPanner();
      this.pannerNode.panningModel = 'HRTF';
      this.pannerNode.distanceModel = 'inverse';
      this.pannerNode.refDistance = 0.75;
      this.pannerNode.maxDistance = 30;
      this.pannerNode.rolloffFactor = 1;
      this.pannerNode.connect(ctx.destination);
    }
    this.pannerNode.positionX.setValueAtTime(x, ctx.currentTime);
    this.pannerNode.positionY.setValueAtTime(y, ctx.currentTime);
    this.pannerNode.positionZ.setValueAtTime(z, ctx.currentTime);
  }

  /** Keep the Web Audio listener attached to the viewer's live head/camera pose. */
  setListenerPose(
    x: number,
    y: number,
    z: number,
    forwardX: number,
    forwardY: number,
    forwardZ: number,
    upX: number,
    upY: number,
    upZ: number,
  ): void {
    const ctx = this.audioContext;
    if (!ctx) return;
    const listener = ctx.listener;
    const time = ctx.currentTime;

    if (listener.positionX) {
      listener.positionX.setValueAtTime(x, time);
      listener.positionY.setValueAtTime(y, time);
      listener.positionZ.setValueAtTime(z, time);
      listener.forwardX.setValueAtTime(forwardX, time);
      listener.forwardY.setValueAtTime(forwardY, time);
      listener.forwardZ.setValueAtTime(forwardZ, time);
      listener.upX.setValueAtTime(upX, time);
      listener.upY.setValueAtTime(upY, time);
      listener.upZ.setValueAtTime(upZ, time);
    } else {
      listener.setPosition(x, y, z);
      listener.setOrientation(forwardX, forwardY, forwardZ, upX, upY, upZ);
    }
  }

  /**
   * Returns normalized 0–1 average audio amplitude from the mic.
   * Returns 0 when not recording.
   */
  getAudioLevel(): number {
    if (!this.analyser || !this.analyserBuf || !this.isListening) return 0;
    this.analyser.getByteFrequencyData(this.analyserBuf);
    let sum = 0;
    for (let i = 0; i < this.analyserBuf.length; i++) sum += this.analyserBuf[i];
    return (sum / this.analyserBuf.length) / 255;
  }

  /**
   * Returns per-bin frequency data (0–1) for visualization.
   * The returned array has `analyser.frequencyBinCount` entries.
   * Returns null when not recording.
   */
  getFrequencyBins(): Float32Array | null {
    if (!this.analyser || !this.analyserBuf || !this.isListening) return null;
    this.analyser.getByteFrequencyData(this.analyserBuf);
    const out = new Float32Array(this.analyserBuf.length);
    for (let i = 0; i < this.analyserBuf.length; i++) out[i] = this.analyserBuf[i] / 255;
    return out;
  }

  dispose(): void {
    this.stopPlayback();
    this.cancelListening();
    this.audioContext?.close();
  }
}
