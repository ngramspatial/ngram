// @ts-nocheck
/**
 * App Panel Manager for the WebXR surface.
 *
 * Creates sandboxed iframe DOM overlays that agents can use to run
 * interactive HTML/CSS/JS applications. Supports multiple simultaneous
 * panels with a postMessage bridge for bidirectional communication.
 */

import { applyDomPanelLayout, captureDomPanelLayout, type SavedDomPanelLayout } from './dom-panel-state.js';

const BRIDGE_SCRIPT = `<script>
window.NgramAR = {
  send(action, data) {
    parent.postMessage({ type: 'hs:action', action, data }, '*');
  },
};
window.addEventListener('message', function(e) {
  if (e.data && e.data.type === 'hs:update') {
    window.dispatchEvent(new CustomEvent('hs:update', { detail: e.data.data }));
  }
});
<\/script>`;

interface AppPanelEntry {
  id: string;
  title: string;
  htmlContent: string;
  container: HTMLDivElement;
  iframe: HTMLIFrameElement;
  minimized: boolean;
}

export interface SavedAppPanelState {
  id: string;
  title: string;
  htmlContent: string;
  minimized: boolean;
  layout?: SavedDomPanelLayout;
}

type InteractionCallback = (panelId: string, action: string, data?: Record<string, unknown>) => void;
type CloseCallback = (panelId: string) => void;

export class AppPanelManager {
  private panels = new Map<string, AppPanelEntry>();
  private interactionCallbacks: InteractionCallback[] = [];
  private closeCallbacks: CloseCallback[] = [];
  private stateCallbacks: Array<() => void> = [];
  private dragState: { el: HTMLDivElement; offsetX: number; offsetY: number } | null = null;

  constructor() {
    window.addEventListener('message', this.onMessage);
    document.addEventListener('pointermove', this.onDragMove);
    document.addEventListener('pointerup', this.onDragEnd);
  }

  onInteraction(cb: InteractionCallback): void {
    this.interactionCallbacks.push(cb);
  }

  onClose(cb: CloseCallback): void {
    this.closeCallbacks.push(cb);
  }

  onStateChange(cb: () => void): void {
    this.stateCallbacks.push(cb);
  }

  getSavedState(): SavedAppPanelState[] {
    return Array.from(this.panels.values()).map((entry) => ({
      id: entry.id,
      title: entry.title,
      htmlContent: entry.htmlContent.slice(0, 100_000),
      minimized: entry.minimized,
      layout: captureDomPanelLayout(entry.container),
    }));
  }

  loadSavedState(value: SavedAppPanelState[] | null | undefined): void {
    if (!Array.isArray(value)) return;
    for (const raw of value.slice(0, 8)) {
      if (!raw || typeof raw !== 'object') continue;
      const id = typeof raw.id === 'string' ? raw.id.slice(0, 200) : '';
      if (!id) continue;
      const title = typeof raw.title === 'string' ? raw.title.slice(0, 500) : '';
      const htmlContent = typeof raw.htmlContent === 'string' ? raw.htmlContent.slice(0, 100_000) : '';
      const layout = raw.layout;
      const width = layout?.width && Number.isFinite(layout.width) ? layout.width : undefined;
      const height = layout?.height && Number.isFinite(layout.height)
        ? Math.max(120, layout.height - 45)
        : undefined;
      this.open(id, title, htmlContent, { width, height });
      const entry = this.panels.get(id);
      if (!entry) continue;
      applyDomPanelLayout(entry.container, layout, { width: 240, height: 45 });
      if (raw.minimized) this.setMinimized(entry, true, false);
    }
  }

  open(id: string, title: string, htmlContent: string, options?: { width?: number; height?: number }): void {
    this.close(id);

    const w = options?.width ?? 480;
    const h = options?.height ?? 400;

    const container = document.createElement('div');
    container.className = 'app-panel';
    container.setAttribute('data-panel-id', id);
    container.style.width = w + 'px';
    container.style.height = (h + 45) + 'px';

    // Position near center with slight offset per panel
    const offset = this.panels.size * 30;
    container.style.left = `calc(50% - ${w / 2 - offset}px)`;
    container.style.top = `calc(50% - ${(h + 45) / 2 - offset}px)`;

    const header = document.createElement('div');
    header.className = 'app-panel-header';
    header.addEventListener('pointerdown', (e) => this.onDragStart(e, container));

    const titleEl = document.createElement('div');
    titleEl.className = 'app-panel-title';
    titleEl.textContent = title || 'App';
    header.appendChild(titleEl);

    const controls = document.createElement('div');
    controls.className = 'app-panel-controls';

    const minBtn = document.createElement('button');
    minBtn.className = 'app-panel-btn app-panel-min';
    minBtn.innerHTML = '&minus;';
    minBtn.addEventListener('click', () => this.toggleMinimize(id));
    controls.appendChild(minBtn);

    const closeBtn = document.createElement('button');
    closeBtn.className = 'app-panel-btn app-panel-close';
    closeBtn.innerHTML = '&times;';
    closeBtn.addEventListener('click', () => this.close(id));
    controls.appendChild(closeBtn);

    header.appendChild(controls);
    container.appendChild(header);

    const iframe = document.createElement('iframe');
    iframe.className = 'app-panel-frame';
    iframe.setAttribute('sandbox', 'allow-scripts');
    iframe.setAttribute('referrerpolicy', 'no-referrer');
    iframe.style.width = '100%';
    iframe.style.height = h + 'px';

    const fullHtml = `<!DOCTYPE html><html><head><meta charset="utf-8"><meta name="referrer" content="no-referrer"><meta name="viewport" content="width=device-width,initial-scale=1">${BRIDGE_SCRIPT}<link rel="stylesheet" href="/fonts/azeret-mono-embedded.css"><style>*{box-sizing:border-box}body{margin:0;font-family:"Azeret Mono",ui-monospace,monospace;color:#e0e0e0;background:#1a1a22}</style></head><body>${htmlContent}</body></html>`;
    iframe.srcdoc = fullHtml;

    container.appendChild(iframe);
    document.body.appendChild(container);

    this.panels.set(id, { id, title, htmlContent, container, iframe, minimized: false });
    this.notifyStateChange();
  }

  close(id: string): void {
    const entry = this.panels.get(id);
    if (!entry) return;
    entry.container.remove();
    this.panels.delete(id);
    this.closeCallbacks.forEach(cb => cb(id));
    this.notifyStateChange();
  }

  closeAll(): void {
    for (const [id] of this.panels) this.close(id);
  }

  sendData(id: string, data: Record<string, unknown>): void {
    const entry = this.panels.get(id);
    if (!entry) return;
    entry.iframe.contentWindow?.postMessage({ type: 'hs:update', data }, '*');
  }

  has(id: string): boolean {
    return this.panels.has(id);
  }

  private toggleMinimize(id: string): void {
    const entry = this.panels.get(id);
    if (!entry) return;
    this.setMinimized(entry, !entry.minimized);
  }

  private setMinimized(entry: AppPanelEntry, minimized: boolean, notify = true): void {
    entry.minimized = minimized;
    entry.iframe.style.display = entry.minimized ? 'none' : '';
    entry.container.style.height = entry.minimized
      ? '45px'
      : (parseInt(entry.iframe.style.height || '400') + 45) + 'px';
    if (notify) this.notifyStateChange();
  }

  private onMessage = (e: MessageEvent): void => {
    if (!e.data || e.data.type !== 'hs:action') return;

    // Find which panel this came from
    for (const entry of this.panels.values()) {
      if (entry.iframe.contentWindow === e.source) {
        const action = e.data.action as string ?? 'unknown';
        const data = e.data.data as Record<string, unknown> | undefined;
        this.interactionCallbacks.forEach(cb => cb(entry.id, action, data));
        return;
      }
    }
  };

  private onDragStart(e: PointerEvent, container: HTMLDivElement): void {
    e.preventDefault();
    const rect = container.getBoundingClientRect();
    this.dragState = {
      el: container,
      offsetX: e.clientX - rect.left,
      offsetY: e.clientY - rect.top,
    };
    container.classList.add('dragging');
  }

  private onDragMove = (e: PointerEvent): void => {
    if (!this.dragState) return;
    const x = e.clientX - this.dragState.offsetX;
    const y = e.clientY - this.dragState.offsetY;
    this.dragState.el.style.left = x + 'px';
    this.dragState.el.style.top = y + 'px';
  };

  private onDragEnd = (): void => {
    if (!this.dragState) return;
    this.dragState.el.classList.remove('dragging');
    this.dragState = null;
    this.notifyStateChange();
  };

  private notifyStateChange(): void {
    for (const cb of this.stateCallbacks) cb();
  }

  dispose(): void {
    window.removeEventListener('message', this.onMessage);
    document.removeEventListener('pointermove', this.onDragMove);
    document.removeEventListener('pointerup', this.onDragEnd);
    this.closeAll();
    this.stateCallbacks = [];
  }
}
