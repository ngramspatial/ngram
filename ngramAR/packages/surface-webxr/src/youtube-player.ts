// @ts-nocheck
/**
 * Floating DOM-overlay YouTube player for the WebXR surface.
 *
 * Uses the YouTube IFrame Player API for full programmatic control
 * (play, pause, seek, volume). Renders as a draggable overlay on top
 * of the 3D canvas, matching the ngram AR dark glass aesthetic.
 */

import { makeResizable } from './dom-resizable.js';
import { applyDomPanelLayout, captureDomPanelLayout, type SavedDomPanelLayout } from './dom-panel-state.js';

// Minimal YouTube IFrame API type stubs
interface YTPlayerOptions {
  width?: string | number;
  height?: string | number;
  videoId?: string;
  host?: string;
  playerVars?: Record<string, any>;
  events?: {
    onReady?: () => void;
    onStateChange?: (e: { data: number }) => void;
    onError?: (e: { data: number }) => void;
  };
}

interface YTPlayerInstance {
  loadVideoById(opts: { videoId: string; startSeconds?: number }): void;
  playVideo(): void;
  pauseVideo(): void;
  stopVideo(): void;
  seekTo(seconds: number, allowSeekAhead: boolean): void;
  setVolume(volume: number): void;
  getVolume(): number;
  getPlayerState(): number;
  getCurrentTime(): number;
  getDuration(): number;
  mute(): void;
  unMute(): void;
  destroy(): void;
}

interface YTStatic {
  Player: new (el: string | HTMLElement, opts: YTPlayerOptions) => YTPlayerInstance;
  PlayerState: { PLAYING: number; PAUSED: number; ENDED: number; BUFFERING: number };
}

declare global {
  interface Window {
    YT: YTStatic;
    onYouTubeIframeAPIReady: (() => void) | undefined;
  }
}

type StateCallback = (state: YTPlayerState) => void;
type ErrorCallback = (errorCode: number) => void;

export interface YTPlayerState {
  open: boolean;
  videoId: string;
  title: string;
  playing: boolean;
  paused: boolean;
  volume: number;
  currentTime: number;
  duration: number;
}

export interface SavedYouTubeState extends YTPlayerState {
  minimized: boolean;
  layout?: SavedDomPanelLayout;
}

let apiLoaded = false;
let apiReady = false;
const apiReadyCallbacks: Array<() => void> = [];

function ensureAPI(): Promise<void> {
  if (apiReady) return Promise.resolve();
  return new Promise((resolve) => {
    apiReadyCallbacks.push(resolve);
    if (!apiLoaded) {
      apiLoaded = true;
      const prev = window.onYouTubeIframeAPIReady;
      window.onYouTubeIframeAPIReady = () => {
        prev?.();
        apiReady = true;
        for (const cb of apiReadyCallbacks) cb();
        apiReadyCallbacks.length = 0;
      };
      const tag = document.createElement('script');
      tag.src = 'https://www.youtube.com/iframe_api';
      document.head.appendChild(tag);
    }
  });
}

export class YouTubePlayer {
  private container: HTMLDivElement | null = null;
  private player: YTPlayerInstance | null = null;
  private _videoId = '';
  private _title = '';
  private _volume = 50;
  private _open = false;
  private stateCallbacks: StateCallback[] = [];
  private errorCallbacks: ErrorCallback[] = [];
  private dragOffset = { x: 0, y: 0 };
  private dragging = false;
  private minimized = false;
  private disposeResize: (() => void) | null = null;
  private activeHost: 'youtube' | 'nocookie' = 'youtube';
  private retryTriedForCurrentVideo = false;
  private lastStartAt = 0;
  /** Immersive sessions block unmuted autoplay; squeeze consumes this to unMute(). */
  private pendingArAudioUnlock = false;
  private startPaused = false;

  onStateChange(cb: StateCallback): void {
    this.stateCallbacks.push(cb);
  }

  onError(cb: ErrorCallback): void {
    this.errorCallbacks.push(cb);
  }

  private emit(): void {
    const s = this.getState();
    for (const cb of this.stateCallbacks) cb(s);
  }

  private emitError(code: number): void {
    for (const cb of this.errorCallbacks) cb(code);
  }

  getState(): YTPlayerState {
    const ps = this.player?.getPlayerState?.() ?? -1;
    const YTState = window.YT?.PlayerState ?? { PLAYING: 1, PAUSED: 2, ENDED: 0, BUFFERING: 3 };
    return {
      open: this._open,
      videoId: this._videoId,
      title: this._title,
      playing: ps === YTState.PLAYING,
      paused: ps === YTState.PAUSED || (this._open && this.startPaused && ps !== YTState.PLAYING),
      volume: this._volume,
      currentTime: this.player?.getCurrentTime?.() ?? 0,
      duration: this.player?.getDuration?.() ?? 0,
    };
  }

  getSavedState(): SavedYouTubeState | null {
    if (!this._open || !this._videoId) return null;
    return {
      ...this.getState(),
      minimized: this.minimized,
      layout: captureDomPanelLayout(this.container),
    };
  }

  async loadSavedState(value: SavedYouTubeState | null | undefined): Promise<void> {
    if (!value?.open || typeof value.videoId !== 'string') return;
    const videoId = value.videoId.trim();
    if (!/^[A-Za-z0-9_-]{6,32}$/.test(videoId)) return;
    await this.play(videoId, {
      title: typeof value.title === 'string' ? value.title.slice(0, 500) : '',
      volume: Number.isFinite(value.volume) ? value.volume : 50,
      startAt: Number.isFinite(value.currentTime) ? Math.max(0, value.currentTime) : 0,
      startPaused: Boolean(value.paused),
    });
    this.setMinimized(Boolean(value.minimized), false);
    applyDomPanelLayout(this.container, value.layout, { width: 280, height: 180 });
    this.emit();
  }

  get isOpen(): boolean {
    return this._open;
  }

  /**
   * If true, next controller squeeze should call tryConsumeArAudioUnlock() before mic toggle.
   * Browsers allow muted autoplay in WebXR; unmute must follow a user gesture.
   */
  get needsArAudioUnlock(): boolean {
    return this.pendingArAudioUnlock;
  }

  /** Call from squeeze (user gesture). Returns true if this consumed the gesture for YouTube. */
  tryConsumeArAudioUnlock(): boolean {
    if (!this.pendingArAudioUnlock || !this.player) return false;
    this.pendingArAudioUnlock = false;
    try {
      this.player.unMute();
      this.player.setVolume(this._volume);
      this.player.playVideo();
    } catch (e) {
      console.warn('[youtube] AR unmute failed:', e);
    }
    this.emit();
    return true;
  }

  async play(videoId: string, opts?: { title?: string; volume?: number; startAt?: number; arAudioUnlock?: boolean; startPaused?: boolean }): Promise<void> {
    this._videoId = videoId;
    this._title = opts?.title ?? '';
    this._volume = opts?.volume ?? 50;
    this.lastStartAt = opts?.startAt ?? 0;
    const arUnlock = !!opts?.arAudioUnlock;
    this.startPaused = Boolean(opts?.startPaused);

    if (this._open) {
      this.player?.loadVideoById({ videoId, startSeconds: opts?.startAt ?? 0 });
      this.player?.setVolume(this._volume);
      this.retryTriedForCurrentVideo = false;
      if (this.startPaused) {
        this.player?.pauseVideo();
      } else if (arUnlock) {
        this.pendingArAudioUnlock = true;
        this.player?.mute();
        this.player?.playVideo();
      }
      this.updateTitle();
      this.emit();
      return;
    }

    this._open = true;
    this.activeHost = 'youtube';
    this.retryTriedForCurrentVideo = false;
    this.pendingArAudioUnlock = arUnlock;
    this.emit();
    await ensureAPI();
    this.createDOM();
    this.mountPlayer(videoId, this.lastStartAt, arUnlock, this.startPaused);
  }

  pause(): void {
    this.startPaused = true;
    this.player?.pauseVideo();
    this.emit();
  }

  resume(): void {
    this.startPaused = false;
    this.player?.playVideo();
    this.emit();
  }

  stop(): void {
    this.pendingArAudioUnlock = false;
    this.retryTriedForCurrentVideo = false;
    this.activeHost = 'youtube';
    this.lastStartAt = 0;
    this.startPaused = false;
    this.player?.destroy();
    this.player = null;
    this.disposeResize?.();
    this.disposeResize = null;
    this.container?.remove();
    this.container = null;
    this._open = false;
    this._videoId = '';
    this._title = '';
    this.emit();
  }

  seek(seconds: number): void {
    this.player?.seekTo(seconds, true);
  }

  setVolume(vol: number): void {
    this._volume = Math.max(0, Math.min(100, vol));
    this.player?.setVolume(this._volume);
    this.emit();
  }

  command(cmd: "pause" | "resume" | "stop" | "seek" | "volume", opts?: { seekTo?: number; volume?: number }): void {
    switch (cmd) {
      case 'pause': this.pause(); break;
      case 'resume': this.resume(); break;
      case 'stop': this.stop(); break;
      case 'seek': if (opts?.seekTo != null) this.seek(opts.seekTo); break;
      case 'volume': if (opts?.volume != null) this.setVolume(opts.volume); break;
    }
  }

  dispose(): void {
    this.stop();
    this.stateCallbacks = [];
    this.errorCallbacks = [];
  }

  private mountPlayer(videoId: string, startAt: number, arUnlock: boolean, startPaused = false): void {
    const playerDiv = document.getElementById('yt-player-frame')!;
    playerDiv.innerHTML = '';

    const host = this.activeHost === 'nocookie'
      ? 'https://www.youtube-nocookie.com'
      : 'https://www.youtube.com';

    this.player = new window.YT.Player(playerDiv, {
      width: '100%',
      height: '100%',
      videoId,
      host,
      playerVars: {
        autoplay: startPaused ? 0 : 1,
        // Muted autoplay is allowed in immersive WebXR; user squeezes grip to unmute.
        mute: arUnlock ? 1 : 0,
        start: startAt,
        modestbranding: 1,
        rel: 0,
        playsinline: 1,
      },
      events: {
        onReady: () => {
          this.player!.setVolume(this._volume);
          if (startPaused) {
            this.player!.pauseVideo();
          } else if (arUnlock) {
            this.player!.mute();
            this.player!.playVideo();
          }
          this.emit();
        },
        onStateChange: (event) => {
          const states = window.YT?.PlayerState;
          if (event.data === states?.PLAYING) this.startPaused = false;
          if (event.data === states?.PAUSED) this.startPaused = true;
          this.emit();
        },
        onError: (e: { data: number }) => this.handlePlayerError(e.data),
      },
    });
  }

  private handlePlayerError(code: number): void {
    console.warn('[youtube] Player error:', code, '(host:', this.activeHost, ')');

    // 2/100 are invalid ID / removed video and won't improve with host switch.
    const retriable = code !== 2 && code !== 100;
    if (retriable && !this.retryTriedForCurrentVideo && this._videoId) {
      this.retryTriedForCurrentVideo = true;
      this.activeHost = this.activeHost === 'youtube' ? 'nocookie' : 'youtube';
      const seek = this.player?.getCurrentTime?.() ?? this.lastStartAt;
      this.player?.destroy();
      this.player = null;
      this.mountPlayer(this._videoId, seek, this.pendingArAudioUnlock, this.startPaused);
      return;
    }

    this.emitError(code);
  }

  // ─── DOM ────────────────────────────────────────────────────────────────

  private createDOM(): void {
    if (this.container) return;

    const c = document.createElement('div');
    c.id = 'yt-player-container';
    c.innerHTML = `
      <div class="yt-player-header" id="yt-player-header">
        <div class="yt-player-title" id="yt-player-title">${this.esc(this._title || 'YouTube')}</div>
        <div class="yt-player-controls">
          <button class="yt-player-btn" id="yt-btn-minimize" title="Minimize">─</button>
          <button class="yt-player-btn yt-btn-close" id="yt-btn-close" title="Close">✕</button>
        </div>
      </div>
      <div class="yt-player-body" id="yt-player-body">
        <div id="yt-player-frame"></div>
      </div>
    `;
    document.body.appendChild(c);
    this.container = c;

    this.injectStyles();
    this.bindDrag();
    this.disposeResize = makeResizable(c, {
      minWidth: 280,
      minHeight: 180,
      keepAspect: true,
      onResize: () => this.emit(),
    });

    document.getElementById('yt-btn-close')!.addEventListener('click', () => this.stop());
    document.getElementById('yt-btn-minimize')!.addEventListener('click', () => this.toggleMinimize());
  }

  private updateTitle(): void {
    const el = document.getElementById('yt-player-title');
    if (el) el.textContent = this._title || 'YouTube';
  }

  private toggleMinimize(): void {
    this.setMinimized(!this.minimized);
  }

  private setMinimized(minimized: boolean, notify = true): void {
    this.minimized = minimized;
    const body = document.getElementById('yt-player-body');
    const btn = document.getElementById('yt-btn-minimize');
    if (body) body.style.display = this.minimized ? 'none' : 'block';
    if (btn) btn.textContent = this.minimized ? '□' : '─';
    if (this.container) {
      this.container.style.width = this.minimized ? '280px' : '';
    }
    if (notify) this.emit();
  }

  private bindDrag(): void {
    const header = document.getElementById('yt-player-header');
    if (!header || !this.container) return;

    const onDown = (e: PointerEvent) => {
      this.dragging = true;
      const rect = this.container!.getBoundingClientRect();
      this.dragOffset.x = e.clientX - rect.left;
      this.dragOffset.y = e.clientY - rect.top;
      header.setPointerCapture(e.pointerId);
      e.preventDefault();
    };

    const onMove = (e: PointerEvent) => {
      if (!this.dragging || !this.container) return;
      const x = e.clientX - this.dragOffset.x;
      const y = e.clientY - this.dragOffset.y;
      this.container.style.left = `${Math.max(0, x)}px`;
      this.container.style.top = `${Math.max(0, y)}px`;
      this.container.style.right = 'auto';
      this.container.style.bottom = 'auto';
    };

    const onUp = () => {
      if (!this.dragging) return;
      this.dragging = false;
      this.emit();
    };

    header.addEventListener('pointerdown', onDown);
    header.addEventListener('pointermove', onMove);
    header.addEventListener('pointerup', onUp);
    header.addEventListener('pointercancel', onUp);
  }

  private esc(s: string): string {
    const d = document.createElement('div');
    d.textContent = s;
    return d.innerHTML;
  }

  private injectStyles(): void {
    if (document.getElementById('yt-player-styles')) return;
    const style = document.createElement('style');
    style.id = 'yt-player-styles';
    style.textContent = `
      #yt-player-container {
        position: fixed;
        bottom: 80px;
        right: 16px;
        width: 420px;
        z-index: 10000;
        border-radius: 12px;
        overflow: hidden;
        box-shadow: 0 8px 32px rgba(0,0,0,0.5), 0 0 0 1px rgba(255,255,255,0.08);
        background: rgba(12, 12, 14, 0.95);
        backdrop-filter: blur(20px);
        -webkit-backdrop-filter: blur(20px);
        font-family: "Azeret Mono", ui-monospace, monospace;
        animation: yt-slide-in 0.3s ease-out;
      }

      @keyframes yt-slide-in {
        from { opacity: 0; transform: translateY(20px) scale(0.96); }
        to { opacity: 1; transform: translateY(0) scale(1); }
      }

      .yt-player-header {
        display: flex;
        align-items: center;
        justify-content: space-between;
        padding: 8px 12px;
        cursor: grab;
        user-select: none;
        border-bottom: 1px solid rgba(255,255,255,0.06);
        background: rgba(255,255,255,0.03);
      }

      .yt-player-header:active { cursor: grabbing; }

      .yt-player-title {
        font-size: 13px;
        font-weight: 500;
        color: rgba(255,255,255,0.75);
        overflow: hidden;
        text-overflow: ellipsis;
        white-space: nowrap;
        flex: 1;
        margin-right: 8px;
      }

      .yt-player-controls {
        display: flex;
        gap: 4px;
        flex-shrink: 0;
      }

      .yt-player-btn {
        width: 24px;
        height: 24px;
        border: none;
        border-radius: 6px;
        background: rgba(255,255,255,0.06);
        color: rgba(255,255,255,0.5);
        font-size: 12px;
        cursor: pointer;
        display: flex;
        align-items: center;
        justify-content: center;
        transition: background 0.15s, color 0.15s;
        line-height: 1;
      }
      .yt-player-btn:hover {
        background: rgba(255,255,255,0.12);
        color: rgba(255,255,255,0.85);
      }
      .yt-btn-close:hover {
        background: rgba(255, 60, 60, 0.3);
        color: #ff6b6b;
      }

      .yt-player-body {
        aspect-ratio: 16 / 9;
        width: 100%;
        background: #000;
      }

      .yt-player-body iframe {
        width: 100%;
        height: 100%;
        border: none;
        display: block;
      }

      /* AR mode: make it smaller and reposition */
      .ar-active #yt-player-container {
        bottom: 16px;
        right: 16px;
        width: 320px;
      }
    `;
    document.head.appendChild(style);
  }
}
