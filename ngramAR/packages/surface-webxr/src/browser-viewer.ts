// @ts-nocheck
/**
 * Floating DOM-overlay web browser for the WebXR surface.
 *
 * Creates a draggable panel with a real navigable iframe, URL bar,
 * and back/forward buttons. Architecturally similar to YouTubePlayer.
 */

import { makeResizable } from './dom-resizable.js';
import { applyDomPanelLayout, captureDomPanelLayout, type SavedDomPanelLayout } from './dom-panel-state.js';

type NavigateCallback = (url: string) => void;

export interface SavedBrowserState {
  open: boolean;
  url: string;
  title: string;
  layout?: SavedDomPanelLayout;
}

export class BrowserViewer {
  private container: HTMLDivElement | null = null;
  private iframe: HTMLIFrameElement | null = null;
  private urlInput: HTMLInputElement | null = null;
  private titleEl: HTMLDivElement | null = null;
  private history: string[] = [];
  private historyIndex = -1;
  private navigateCallbacks: NavigateCallback[] = [];
  private dragState: { offsetX: number; offsetY: number } | null = null;
  private dragPointerId: number | null = null;
  private dragHeader: HTMLDivElement | null = null;
  private boundDragMove = this.onDragMove.bind(this);
  private boundDragEnd = this.onDragEnd.bind(this);
  private disposeResize: (() => void) | null = null;
  private stateCallbacks: Array<() => void> = [];
  private panelTitle = '';

  get isOpen(): boolean {
    return this.container !== null;
  }

  onNavigate(cb: NavigateCallback): void {
    this.navigateCallbacks.push(cb);
  }

  onStateChange(cb: () => void): void {
    this.stateCallbacks.push(cb);
  }

  getSavedState(): SavedBrowserState | null {
    if (!this.container) return null;
    return {
      open: true,
      url: this.history[this.historyIndex] ?? this.urlInput?.value ?? '',
      title: this.panelTitle,
      layout: captureDomPanelLayout(this.container),
    };
  }

  loadSavedState(value: SavedBrowserState | null | undefined): void {
    if (!value?.open || typeof value.url !== 'string' || !value.url.trim()) return;
    const title = typeof value.title === 'string' ? value.title.slice(0, 500) : '';
    this.open(value.url.slice(0, 4096), title);
    applyDomPanelLayout(this.container, value.layout, { width: 320, height: 240 });
    this.notifyStateChange();
  }

  open(url: string, title?: string): void {
    if (this.container) {
      if (title) {
        this.panelTitle = title;
        if (this.titleEl) this.titleEl.textContent = title;
      }
      this.navigateTo(url);
      return;
    }
    this.panelTitle = title ?? '';
    this.buildDOM(url, title);
    this.navigateTo(url);
  }

  close(): void {
    if (!this.container) return;
    this.disposeResize?.();
    this.disposeResize = null;
    this.container.remove();
    this.container = null;
    this.iframe = null;
    this.urlInput = null;
    this.titleEl = null;
    this.history = [];
    this.historyIndex = -1;
    this.panelTitle = '';
    document.removeEventListener('pointermove', this.boundDragMove);
    document.removeEventListener('pointerup', this.boundDragEnd);
    document.removeEventListener('pointercancel', this.boundDragEnd);
    if (this.dragHeader && this.dragPointerId != null && this.dragHeader.hasPointerCapture(this.dragPointerId)) {
      this.dragHeader.releasePointerCapture(this.dragPointerId);
    }
    this.dragState = null;
    this.dragPointerId = null;
    this.dragHeader = null;
    this.notifyStateChange();
  }

  navigateTo(url: string): void {
    if (!url.match(/^https?:\/\//i)) url = 'https://' + url;
    if (this.iframe) this.iframe.src = url;
    if (this.urlInput) this.urlInput.value = url;

    if (this.historyIndex < this.history.length - 1) {
      this.history = this.history.slice(0, this.historyIndex + 1);
    }
    this.history.push(url);
    this.historyIndex = this.history.length - 1;
    this.updateNavButtons();
    this.notifyStateChange();
  }

  goBack(): void {
    if (this.historyIndex <= 0) return;
    this.historyIndex--;
    const url = this.history[this.historyIndex];
    if (this.iframe) this.iframe.src = url;
    if (this.urlInput) this.urlInput.value = url;
    this.updateNavButtons();
    this.notifyStateChange();
  }

  goForward(): void {
    if (this.historyIndex >= this.history.length - 1) return;
    this.historyIndex++;
    const url = this.history[this.historyIndex];
    if (this.iframe) this.iframe.src = url;
    if (this.urlInput) this.urlInput.value = url;
    this.updateNavButtons();
    this.notifyStateChange();
  }

  private updateNavButtons(): void {
    const back = this.container?.querySelector('.bv-back') as HTMLButtonElement | null;
    const fwd = this.container?.querySelector('.bv-fwd') as HTMLButtonElement | null;
    if (back) back.disabled = this.historyIndex <= 0;
    if (fwd) fwd.disabled = this.historyIndex >= this.history.length - 1;
  }

  private buildDOM(url: string, title?: string): void {
    const container = document.createElement('div');
    container.className = 'bv-container';
    Object.assign(container.style, {
      position: 'fixed', zIndex: '10000',
      left: 'calc(50% - 320px)', top: 'calc(50% - 260px)',
      width: '640px', height: '520px',
      display: 'flex', flexDirection: 'column',
      background: '#16162a', borderRadius: '10px',
      border: '1px solid #2a2a3e', overflow: 'hidden',
      boxShadow: '0 8px 32px rgba(0,0,0,.5)',
      fontFamily: '-apple-system,system-ui,sans-serif',
    });

    // Header
    const header = document.createElement('div');
    Object.assign(header.style, {
      display: 'flex', alignItems: 'center', gap: '6px',
      padding: '6px 8px', background: '#1a1a30',
      borderBottom: '1px solid #2a2a3e', cursor: 'grab',
      flexShrink: '0', userSelect: 'none',
    });
    header.addEventListener('pointerdown', (e) => this.onDragStart(e, container, header));

    // Nav buttons
    const backBtn = document.createElement('button');
    backBtn.className = 'bv-back';
    backBtn.innerHTML = '&#9664;';
    this.styleNavBtn(backBtn);
    backBtn.disabled = true;
    backBtn.addEventListener('click', () => this.goBack());

    const fwdBtn = document.createElement('button');
    fwdBtn.className = 'bv-fwd';
    fwdBtn.innerHTML = '&#9654;';
    this.styleNavBtn(fwdBtn);
    fwdBtn.disabled = true;
    fwdBtn.addEventListener('click', () => this.goForward());

    // URL bar
    const urlInput = document.createElement('input');
    Object.assign(urlInput.style, {
      flex: '1', padding: '5px 10px', background: '#22223a',
      border: '1px solid #3a3a5c', borderRadius: '6px',
      color: '#ccc', fontSize: '12px', outline: 'none',
      minWidth: '0',
    });
    urlInput.value = url;
    urlInput.spellcheck = false;
    urlInput.addEventListener('keydown', (e) => {
      if (e.key === 'Enter') {
        const v = urlInput.value.trim();
        if (v) {
          this.navigateTo(v);
          this.navigateCallbacks.forEach(cb => cb(v));
        }
      }
    });

    // Title
    const titleEl = document.createElement('div');
    Object.assign(titleEl.style, {
      fontSize: '12px', color: '#888', maxWidth: '120px',
      overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
    });
    titleEl.textContent = title || 'Browser';

    // Close
    const closeBtn = document.createElement('button');
    closeBtn.innerHTML = '&times;';
    Object.assign(closeBtn.style, {
      background: 'none', border: 'none', color: '#888',
      fontSize: '18px', cursor: 'pointer', padding: '2px 6px',
      lineHeight: '1',
    });
    closeBtn.addEventListener('click', () => this.close());

    header.append(backBtn, fwdBtn, urlInput, titleEl, closeBtn);

    // Iframe
    const iframe = document.createElement('iframe');
    Object.assign(iframe.style, {
      flex: '1', width: '100%', border: 'none',
      background: '#fff',
    });
    iframe.setAttribute('referrerpolicy', 'no-referrer');

    container.append(header, iframe);
    document.body.appendChild(container);

    this.container = container;
    this.iframe = iframe;
    this.urlInput = urlInput;
    this.titleEl = titleEl;

    document.addEventListener('pointermove', this.boundDragMove);
    document.addEventListener('pointerup', this.boundDragEnd);
    document.addEventListener('pointercancel', this.boundDragEnd);

    this.disposeResize = makeResizable(container, {
      minWidth: 320,
      minHeight: 240,
      onResize: () => this.notifyStateChange(),
    });
  }

  private styleNavBtn(btn: HTMLButtonElement): void {
    Object.assign(btn.style, {
      width: '28px', height: '28px', background: '#22223a',
      border: '1px solid #3a3a5c', borderRadius: '6px',
      color: '#aaa', fontSize: '12px', cursor: 'pointer',
      display: 'flex', alignItems: 'center', justifyContent: 'center',
    });
  }

  private onDragStart(e: PointerEvent, container: HTMLDivElement, header: HTMLDivElement): void {
    const target = e.target as HTMLElement | null;
    if (target?.closest('button, input')) return;
    e.preventDefault();
    const rect = container.getBoundingClientRect();
    this.dragState = { offsetX: e.clientX - rect.left, offsetY: e.clientY - rect.top };
    this.dragPointerId = e.pointerId;
    this.dragHeader = header;
    header.setPointerCapture(e.pointerId);
    container.style.cursor = 'grabbing';
  }

  private onDragMove(e: PointerEvent): void {
    if (this.dragPointerId != null && e.pointerId !== this.dragPointerId) return;
    if (!this.dragState || !this.container) return;
    this.container.style.left = (e.clientX - this.dragState.offsetX) + 'px';
    this.container.style.top = (e.clientY - this.dragState.offsetY) + 'px';
  }

  private onDragEnd(e: PointerEvent): void {
    if (this.dragPointerId != null && e.pointerId !== this.dragPointerId) return;
    if (!this.dragState) return;
    if (this.dragHeader && this.dragPointerId != null && this.dragHeader.hasPointerCapture(this.dragPointerId)) {
      this.dragHeader.releasePointerCapture(this.dragPointerId);
    }
    this.dragState = null;
    this.dragPointerId = null;
    this.dragHeader = null;
    if (this.container) this.container.style.cursor = '';
    this.notifyStateChange();
  }

  private notifyStateChange(): void {
    for (const cb of this.stateCallbacks) cb();
  }

  dispose(): void {
    this.close();
    this.navigateCallbacks = [];
    this.stateCallbacks = [];
  }
}
