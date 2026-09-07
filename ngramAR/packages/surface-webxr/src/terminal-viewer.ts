// @ts-nocheck
/**
 * Floating terminal viewer for the WebXR surface.
 *
 * Displays the agent's workspace tool activity — shell commands,
 * file operations, web requests — as a live scrolling terminal.
 * Renders as a draggable DOM overlay matching the ngram AR aesthetic.
 */

import { makeResizable } from './dom-resizable.js';
import { applyDomPanelLayout, captureDomPanelLayout, type SavedDomPanelLayout } from './dom-panel-state.js';

export interface TerminalEntry {
  command?: string;
  output?: string;
  tool?: string;
  error?: boolean;
  clear?: boolean;
}

const MAX_LINES = 500;
const MAX_ENTRIES = 100;
const AUTO_SHOW_DELAY = 300;

export interface SavedTerminalState {
  visible: boolean;
  minimized: boolean;
  entries: TerminalEntry[];
  layout?: SavedDomPanelLayout;
}

export class TerminalViewer {
  private container: HTMLDivElement | null = null;
  private bodyEl: HTMLElement | null = null;
  private lineCount = 0;
  private minimized = false;
  private _visible = false;
  private dragOffset = { x: 0, y: 0 };
  private dragging = false;
  private showTimer: ReturnType<typeof setTimeout> | null = null;
  private disposeResize: (() => void) | null = null;
  private entries: TerminalEntry[] = [];
  private stateCallbacks: Array<() => void> = [];

  get isVisible(): boolean {
    return this._visible;
  }

  getContentHTML(): string {
    return this.bodyEl?.innerHTML ?? '';
  }

  onStateChange(cb: () => void): void {
    this.stateCallbacks.push(cb);
  }

  getSavedState(): SavedTerminalState {
    return {
      visible: this._visible,
      minimized: this.minimized,
      entries: this.entries.map((entry) => ({ ...entry })),
      layout: captureDomPanelLayout(this.container),
    };
  }

  loadSavedState(value: SavedTerminalState | null | undefined): void {
    if (!value || typeof value !== 'object') return;
    this.entries = [];
    this.ensureDOM();
    if (this.bodyEl) this.bodyEl.innerHTML = '';
    this.lineCount = 0;

    const entries = Array.isArray(value.entries) ? value.entries.slice(-MAX_ENTRIES) : [];
    for (const raw of entries) {
      const entry = this.normalizeEntry(raw);
      if (!entry) continue;
      this.entries.push(entry);
      this.renderEntry(entry, false);
    }

    applyDomPanelLayout(this.container, value.layout, { width: 320, height: 180 });
    this.setMinimized(Boolean(value.minimized), false);
    if (value.visible) this.show();
    else this.hide();
    this.scrollToBottom();
  }

  write(entry: TerminalEntry): void {
    if (entry.clear) {
      this.clear();
      return;
    }

    const normalized = this.normalizeEntry(entry);
    if (!normalized) return;
    this.entries.push(normalized);
    if (this.entries.length > MAX_ENTRIES) this.entries.shift();
    this.renderEntry(normalized, true);
    this.notifyStateChange();
  }

  private renderEntry(entry: TerminalEntry, autoShow: boolean): void {

    this.ensureDOM();

    if (entry.command) {
      this.appendLine(
        `<span class="tv-prompt">$</span> <span class="tv-cmd">${this.esc(entry.command)}</span>`,
      );
    }

    if (entry.output) {
      const cls = entry.error ? 'tv-error' : 'tv-output';
      const lines = entry.output.split('\n');
      // Truncate very long output in the viewer (agent still gets full text)
      const display = lines.length > 60
        ? [...lines.slice(0, 50), `  … ${lines.length - 50} more lines …`]
        : lines;
      for (const line of display) {
        this.appendLine(`<span class="${cls}">${this.esc(line)}</span>`);
      }
    }

    if (autoShow) this.autoShow();
    this.scrollToBottom();
  }

  clear(): void {
    if (this.bodyEl) {
      this.bodyEl.innerHTML = '';
      this.lineCount = 0;
    }
    this.entries = [];
    this.notifyStateChange();
  }

  show(): void {
    this.ensureDOM();
    if (this.container) {
      this.container.style.display = '';
      if (this._visible) return;
      this._visible = true;
      this.notifyStateChange();
    }
  }

  hide(): void {
    if (this.container) {
      this.container.style.display = 'none';
      if (!this._visible) return;
      this._visible = false;
      this.notifyStateChange();
    }
  }

  toggle(): void {
    if (this._visible) this.hide();
    else this.show();
  }

  dispose(): void {
    if (this.showTimer) clearTimeout(this.showTimer);
    this.disposeResize?.();
    this.disposeResize = null;
    this.container?.remove();
    this.container = null;
    this.bodyEl = null;
    this._visible = false;
    this.entries = [];
    this.stateCallbacks = [];
  }

  // ─── Internal ──────────────────────────────────────────────────────────

  private autoShow(): void {
    if (this._visible) return;
    if (this.showTimer) return;
    this.showTimer = setTimeout(() => {
      this.showTimer = null;
      this.show();
    }, AUTO_SHOW_DELAY);
  }

  private appendLine(html: string): void {
    if (!this.bodyEl) return;
    const div = document.createElement('div');
    div.className = 'tv-line';
    div.innerHTML = html;
    this.bodyEl.appendChild(div);
    this.lineCount++;

    if (this.lineCount > MAX_LINES) {
      const first = this.bodyEl.firstChild;
      if (first) this.bodyEl.removeChild(first);
      this.lineCount--;
    }
  }

  private scrollToBottom(): void {
    if (this.bodyEl) {
      this.bodyEl.scrollTop = this.bodyEl.scrollHeight;
    }
  }

  private ensureDOM(): void {
    if (this.container) return;

    const c = document.createElement('div');
    c.id = 'tv-container';
    c.innerHTML = `
      <div class="tv-header" id="tv-header">
        <div class="tv-header-dots">
          <span class="tv-dot tv-dot-red"></span>
          <span class="tv-dot tv-dot-yellow"></span>
          <span class="tv-dot tv-dot-green"></span>
        </div>
        <div class="tv-title">terminal</div>
        <div class="tv-header-btns">
          <button class="tv-btn" id="tv-btn-min" title="Minimize">─</button>
          <button class="tv-btn tv-btn-close" id="tv-btn-close" title="Hide">✕</button>
        </div>
      </div>
      <div class="tv-body" id="tv-body"></div>
    `;
    c.style.display = 'none';
    document.body.appendChild(c);
    this.container = c;
    this.bodyEl = document.getElementById('tv-body')!;

    this.injectStyles();
    this.bindDrag();
    this.disposeResize = makeResizable(c, {
      minWidth: 320,
      minHeight: 180,
      maxWidth: Math.max(360, Math.floor(window.innerWidth - 24)),
      maxHeight: Math.max(240, Math.floor(window.innerHeight - 24)),
      onResize: () => this.notifyStateChange(),
    });

    document.getElementById('tv-btn-close')!.addEventListener('click', () => this.hide());
    document.getElementById('tv-btn-min')!.addEventListener('click', () => this.toggleMinimize());
  }

  private toggleMinimize(): void {
    this.setMinimized(!this.minimized);
  }

  private setMinimized(minimized: boolean, notify = true): void {
    this.minimized = minimized;
    const body = document.getElementById('tv-body');
    const btn = document.getElementById('tv-btn-min');
    if (body) body.style.display = this.minimized ? 'none' : '';
    if (btn) btn.textContent = this.minimized ? '□' : '─';
    if (notify) this.notifyStateChange();
  }

  private bindDrag(): void {
    const header = document.getElementById('tv-header');
    if (!header || !this.container) return;

    const onDown = (e: PointerEvent) => {
      if ((e.target as HTMLElement).closest('.tv-btn')) return;
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
      this.notifyStateChange();
    };

    header.addEventListener('pointerdown', onDown);
    header.addEventListener('pointermove', onMove);
    header.addEventListener('pointerup', onUp);
    header.addEventListener('pointercancel', onUp);
  }

  private esc(s: string): string {
    return s
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;');
  }

  private normalizeEntry(value: unknown): TerminalEntry | null {
    if (!value || typeof value !== 'object') return null;
    const raw = value as TerminalEntry;
    const command = typeof raw.command === 'string' ? raw.command.slice(0, 4_000) : undefined;
    const output = typeof raw.output === 'string' ? raw.output.slice(0, 12_000) : undefined;
    const tool = typeof raw.tool === 'string' ? raw.tool.slice(0, 500) : undefined;
    if (!command && !output && !tool) return null;
    return { command, output, tool, error: Boolean(raw.error) };
  }

  private notifyStateChange(): void {
    for (const cb of this.stateCallbacks) cb();
  }

  private injectStyles(): void {
    if (document.getElementById('tv-styles')) return;
    const style = document.createElement('style');
    style.id = 'tv-styles';
    style.textContent = `
      #tv-container {
        position: fixed;
        bottom: 80px;
        left: 16px;
        width: 440px;
        max-height: 380px;
        z-index: 10000;
        border-radius: 10px;
        overflow: hidden;
        box-shadow: 0 8px 32px rgba(0,0,0,0.55), 0 0 0 1px rgba(255,255,255,0.06);
        background: rgba(18, 18, 22, 0.96);
        backdrop-filter: blur(20px);
        -webkit-backdrop-filter: blur(20px);
        font-family: "Azeret Mono", ui-monospace, monospace;
        display: flex;
        flex-direction: column;
        animation: tv-slide-in 0.25s ease-out;
      }

      @keyframes tv-slide-in {
        from { opacity: 0; transform: translateY(16px) scale(0.97); }
        to { opacity: 1; transform: translateY(0) scale(1); }
      }

      .tv-header {
        display: flex;
        align-items: center;
        padding: 7px 12px;
        cursor: grab;
        user-select: none;
        background: rgba(30, 30, 36, 0.9);
        border-bottom: 1px solid rgba(255,255,255,0.05);
        gap: 10px;
        flex-shrink: 0;
      }
      .tv-header:active { cursor: grabbing; }

      .tv-header-dots {
        display: flex;
        gap: 6px;
        flex-shrink: 0;
      }
      .tv-dot {
        width: 10px;
        height: 10px;
        border-radius: 50%;
        background: rgba(255,255,255,0.08);
      }
      .tv-dot-red { background: #ff5f57; }
      .tv-dot-yellow { background: #febc2e; }
      .tv-dot-green { background: #28c840; }

      .tv-title {
        font-size: 12px;
        color: rgba(255,255,255,0.4);
        flex: 1;
        text-align: center;
        letter-spacing: 0.5px;
      }

      .tv-header-btns {
        display: flex;
        gap: 4px;
        flex-shrink: 0;
      }

      .tv-btn {
        width: 22px;
        height: 22px;
        border: none;
        border-radius: 5px;
        background: rgba(255,255,255,0.05);
        color: rgba(255,255,255,0.4);
        font-size: 11px;
        cursor: pointer;
        display: flex;
        align-items: center;
        justify-content: center;
        transition: background 0.15s, color 0.15s;
        line-height: 1;
      }
      .tv-btn:hover {
        background: rgba(255,255,255,0.1);
        color: rgba(255,255,255,0.75);
      }
      .tv-btn-close:hover {
        background: rgba(255, 60, 60, 0.25);
        color: #ff6b6b;
      }

      .tv-body {
        flex: 1;
        overflow-y: auto;
        padding: 10px 14px;
        font-size: 12px;
        line-height: 1.6;
        max-height: 320px;
        scrollbar-width: thin;
        scrollbar-color: rgba(255,255,255,0.08) transparent;
      }
      .tv-body::-webkit-scrollbar { width: 6px; }
      .tv-body::-webkit-scrollbar-track { background: transparent; }
      .tv-body::-webkit-scrollbar-thumb { background: rgba(255,255,255,0.08); border-radius: 3px; }

      .tv-line {
        white-space: pre-wrap;
        word-break: break-all;
        min-height: 1em;
      }

      .tv-prompt {
        color: #28c840;
        font-weight: 600;
      }

      .tv-cmd {
        color: rgba(255,255,255,0.88);
      }

      .tv-output {
        color: rgba(255,255,255,0.55);
      }

      .tv-error {
        color: #ff6b6b;
      }

      /* AR mode */
      .ar-active #tv-container {
        bottom: 16px;
        left: 16px;
        width: 360px;
        max-height: 280px;
      }
    `;
    document.head.appendChild(style);
  }
}
