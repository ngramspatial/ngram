// @ts-nocheck
/**
 * Spatial music player for the WebXR surface.
 *
 * - Non-spatial: plain <audio> (no crossOrigin) so most direct URLs play without CORS.
 * - Spatial: Web Audio + MediaElementSource + PannerNode; falls back to plain stereo if
 *   CORS blocks the element (common for random MP3 links).
 */

export interface PlayOptions {
  url: string;
  title?: string;
  volume?: number;
  loop?: boolean;
  spatial?: boolean;
}

type StateCallback = (state: MusicState) => void;

export interface MusicState {
  playing: boolean;
  paused: boolean;
  title: string;
  volume: number;
  loop: boolean;
  spatial: boolean;
  currentTime: number;
  duration: number;
}

export class MusicPlayer {
  private audioContext: AudioContext | null = null;
  private audioElement: HTMLAudioElement | null = null;
  private sourceNode: MediaElementAudioSourceNode | null = null;
  private gainNode: GainNode | null = null;
  private pannerNode: PannerNode | null = null;

  /** True when using plain <audio> without Web Audio graph */
  private plainMode = false;

  private _playing = false;
  private _paused = false;
  private _title = '';
  private _volume = 0.5;
  private _loop = false;
  private _spatial = false;

  private stateCallbacks: StateCallback[] = [];

  private ensureContext(): AudioContext {
    if (!this.audioContext) {
      this.audioContext = new AudioContext();
    }
    if (this.audioContext.state === 'suspended') {
      this.audioContext.resume();
    }
    return this.audioContext;
  }

  onStateChange(cb: StateCallback): void {
    this.stateCallbacks.push(cb);
  }

  private emitState(): void {
    const state = this.getState();
    for (const cb of this.stateCallbacks) cb(state);
  }

  getState(): MusicState {
    return {
      playing: this._playing,
      paused: this._paused,
      title: this._title,
      volume: this._volume,
      loop: this._loop,
      spatial: this._spatial,
      currentTime: this.audioElement?.currentTime ?? 0,
      duration: this.audioElement?.duration ?? 0,
    };
  }

  get isPlaying(): boolean {
    return this._playing;
  }

  play(opts: PlayOptions): void {
    this.stop();

    this._volume = opts.volume ?? 0.5;
    this._loop = opts.loop ?? false;
    this._spatial = opts.spatial ?? false;
    this._title = opts.title ?? '';

    if (this._spatial) {
      this.tryPlaySpatial(opts);
    } else {
      this.playPlain(opts);
    }
  }

  /** Web Audio spatial path; on failure uses plain stereo. */
  private tryPlaySpatial(opts: PlayOptions): void {
    try {
      const ctx = this.ensureContext();
      const audio = new Audio();
      audio.crossOrigin = 'anonymous';
      audio.loop = this._loop;
      audio.src = opts.url;
      this.audioElement = audio;
      this.plainMode = false;

      this.sourceNode = ctx.createMediaElementSource(audio);
      this.gainNode = ctx.createGain();
      this.gainNode.gain.value = this._volume;

      this.pannerNode = ctx.createPanner();
      this.pannerNode.panningModel = 'HRTF';
      this.pannerNode.distanceModel = 'inverse';
      this.pannerNode.refDistance = 1;
      this.pannerNode.maxDistance = 20;
      this.pannerNode.rolloffFactor = 1;
      this.sourceNode.connect(this.gainNode).connect(this.pannerNode).connect(ctx.destination);

      audio.addEventListener('ended', () => {
        if (!this._loop) {
          this._playing = false;
          this._paused = false;
          this.emitState();
        }
      });

      audio.addEventListener('error', () => {
        console.warn('[music] Spatial element error, trying plain playback');
        this.stop();
        this._spatial = false;
        this.playPlain(opts);
      });

      audio.play()
        .then(() => {
          this._playing = true;
          this._paused = false;
          console.log(`[music] Playing (spatial): ${this._title || opts.url}`);
          this.emitState();
        })
        .catch((err) => {
          console.warn('[music] Spatial play failed, trying plain:', err);
          this.stop();
          this._spatial = false;
          this.playPlain(opts);
        });
    } catch (e) {
      console.warn('[music] Web Audio setup failed, using plain <audio>:', e);
      this.stop();
      this._spatial = false;
      this.playPlain(opts);
    }
  }

  /** No crossOrigin — works for many hosts that do not send ACAO headers. Not spatial. */
  private playPlain(opts: PlayOptions): void {
    this.disconnectGraph();

    const audio = new Audio();
    audio.loop = this._loop;
    audio.volume = this._volume;
    audio.src = opts.url;
    this.audioElement = audio;
    this.plainMode = true;

    audio.addEventListener('ended', () => {
      if (!this._loop) {
        this._playing = false;
        this._paused = false;
        this.emitState();
      }
    });

    audio.addEventListener('error', () => {
      console.warn('[music] Playback error (URL blocked, invalid, or needs login):', opts.url.slice(0, 80));
      this._playing = false;
      this._paused = false;
      this.emitState();
    });

    audio.play()
      .then(() => {
        this._playing = true;
        this._paused = false;
        console.log(`[music] Playing: ${this._title || opts.url}`);
        this.emitState();
      })
      .catch((err) => {
        console.warn('[music] Failed to start (browser may require a button tap first):', err);
        this._playing = false;
        this.emitState();
      });
  }

  private disconnectGraph(): void {
    this.sourceNode?.disconnect();
    this.sourceNode = null;
    this.gainNode?.disconnect();
    this.gainNode = null;
    this.pannerNode?.disconnect();
    this.pannerNode = null;
    this.plainMode = false;
  }

  stop(): void {
    if (this.audioElement) {
      this.audioElement.pause();
      this.audioElement.src = '';
      this.audioElement.load();
      this.audioElement = null;
    }

    this.disconnectGraph();

    this._playing = false;
    this._paused = false;
    this._title = '';
    this.emitState();
  }

  pause(): void {
    if (!this.audioElement || !this._playing) return;
    this.audioElement.pause();
    this._paused = true;
    this.emitState();
  }

  resume(): void {
    if (!this.audioElement || !this._paused) return;
    this.audioElement.play().then(() => {
      this._paused = false;
      this.emitState();
    }).catch((err) => {
      console.warn('[music] Resume failed:', err);
    });
  }

  setVolume(volume: number): void {
    this._volume = Math.max(0, Math.min(1, volume));
    if (this.plainMode && this.audioElement) {
      this.audioElement.volume = this._volume;
    } else if (this.gainNode && this.audioContext) {
      this.gainNode.gain.setValueAtTime(this._volume, this.audioContext.currentTime);
    }
    this.emitState();
  }

  setSpatialPosition(x: number, y: number, z: number): void {
    if (this.plainMode || !this.pannerNode || !this.audioContext) return;
    this.pannerNode.positionX.setValueAtTime(x, this.audioContext.currentTime);
    this.pannerNode.positionY.setValueAtTime(y, this.audioContext.currentTime);
    this.pannerNode.positionZ.setValueAtTime(z, this.audioContext.currentTime);
  }

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
    if (this.plainMode || !this.audioContext) return;
    const listener = this.audioContext.listener;
    const time = this.audioContext.currentTime;

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

  dispose(): void {
    this.stop();
    this.audioContext?.close();
    this.audioContext = null;
    this.stateCallbacks = [];
  }
}
