// @ts-nocheck
import * as THREE from 'three';
import { SPATIAL, drawBrand, setSpatialFont, loadSpatialAssets } from './spatial-design.js';
import { marked } from 'marked';
import hljs from 'highlight.js/lib/core';
import javascript from 'highlight.js/lib/languages/javascript';
import typescript from 'highlight.js/lib/languages/typescript';
import python from 'highlight.js/lib/languages/python';
import css from 'highlight.js/lib/languages/css';
import xml from 'highlight.js/lib/languages/xml';
import json from 'highlight.js/lib/languages/json';
import bash from 'highlight.js/lib/languages/bash';

hljs.registerLanguage('javascript', javascript);
hljs.registerLanguage('typescript', typescript);
hljs.registerLanguage('python', python);
hljs.registerLanguage('css', css);
hljs.registerLanguage('html', xml);
hljs.registerLanguage('xml', xml);
hljs.registerLanguage('json', json);
hljs.registerLanguage('bash', bash);

export type PanelContentType =
  | 'card'
  | 'markdown'
  | 'code'
  | 'image'
  | 'video'
  | 'chart'
  | 'html'
  | 'sandbox';

const BASE_RES = 1024;
const CONTENT_LABELS: Record<PanelContentType, string> = {
  card: 'NOTE', markdown: 'NOTE', code: 'CODE', chart: 'CHART',
  image: 'IMAGE', video: 'VIDEO', html: 'WORKSPACE', sandbox: 'APP',
};

interface ThemeColors {
  text: string;
  heading: string;
  muted: string;
  link: string;
  codeBg: string;
  codeText: string;
  blockBg: string;
  blockBorder: string;
  border: string;
  bqBorder: string;
  hlKeyword: string;
  hlString: string;
  hlNumber: string;
  hlFunction: string;
  hlComment: string;
  hlBuiltIn: string;
}

interface StyledRun {
  text: string;
  bold: boolean;
  italic: boolean;
  code: boolean;
}

function getPanelCSS(dark: boolean): string {
  const fg = dark ? 'rgba(255,255,255,0.88)' : 'rgba(15,15,20,0.85)';
  const heading = dark ? '#fff' : '#111';
  const link = dark ? 'rgba(160,200,255,0.9)' : 'rgba(30,80,180,0.9)';
  const codeBg = dark ? 'rgba(255,255,255,0.06)' : 'rgba(0,0,0,0.05)';
  const preBg = dark ? 'rgba(0,0,0,0.4)' : 'rgba(0,0,0,0.03)';
  const preBorder = dark ? 'rgba(255,255,255,0.06)' : 'rgba(0,0,0,0.08)';
  const tableBorder = dark ? 'rgba(255,255,255,0.1)' : 'rgba(0,0,0,0.1)';
  const tableHeadBg = dark ? 'rgba(255,255,255,0.04)' : 'rgba(0,0,0,0.03)';
  const bqBorder = dark ? 'rgba(255,255,255,0.15)' : 'rgba(0,0,0,0.15)';
  const bqColor = dark ? 'rgba(255,255,255,0.6)' : 'rgba(0,0,0,0.55)';
  const titleBorder = dark ? 'rgba(255,255,255,0.08)' : 'rgba(0,0,0,0.08)';
  const paramColor = dark ? 'rgba(255,255,255,0.7)' : 'rgba(0,0,0,0.6)';
  const commentColor = dark ? 'rgba(255,255,255,0.35)' : 'rgba(0,0,0,0.4)';

  const hlKeyword = dark ? '#c792ea' : '#7c4dff';
  const hlString = dark ? '#c3e88d' : '#2e7d32';
  const hlNumber = dark ? '#f78c6c' : '#e65100';
  const hlFunction = dark ? '#82aaff' : '#1565c0';
  const hlBuiltIn = dark ? '#ffcb6b' : '#b8860b';
  const hlTag = dark ? '#f07178' : '#c62828';

  return `
* { margin: 0; padding: 0; box-sizing: border-box; }
body, .panel-root {
  font-family: "Azeret Mono", ui-monospace, monospace;
  font-size: 15px;
  line-height: 1.55;
  color: ${fg};
  background: transparent;
  padding: 24px;
  overflow: hidden;
  -webkit-font-smoothing: antialiased;
}
h1 { font-size: 22px; font-weight: 600; margin-bottom: 10px; color: ${heading}; }
h2 { font-size: 18px; font-weight: 600; margin-bottom: 8px; color: ${heading}; }
h3 { font-size: 16px; font-weight: 600; margin-bottom: 6px; color: ${heading}; }
p { margin-bottom: 10px; }
ul, ol { margin-left: 20px; margin-bottom: 10px; }
li { margin-bottom: 4px; }
a { color: ${link}; text-decoration: none; }
strong { color: ${heading}; font-weight: 600; }
em { font-style: italic; }
code {
  font-family: "Azeret Mono", ui-monospace, monospace;
  background: ${codeBg};
  padding: 2px 5px;
  border-radius: 4px;
  font-size: 13px;
}
pre {
  background: ${preBg};
  border: 1px solid ${preBorder};
  border-radius: 8px;
  padding: 14px;
  margin-bottom: 12px;
  overflow-x: auto;
}
pre code {
  background: transparent;
  padding: 0;
  font-size: 13px;
  line-height: 1.5;
}
table { border-collapse: collapse; width: 100%; margin-bottom: 12px; }
th, td { border: 1px solid ${tableBorder}; padding: 6px 10px; text-align: left; }
th { background: ${tableHeadBg}; font-weight: 600; color: ${heading}; }
blockquote { border-left: 3px solid ${bqBorder}; padding-left: 12px; color: ${bqColor}; margin-bottom: 10px; }
img { max-width: 100%; border-radius: 6px; }
.panel-title {
  font-size: 16px;
  font-weight: 600;
  color: ${heading};
  margin-bottom: 10px;
  padding-bottom: 8px;
  border-bottom: 1px solid ${titleBorder};
}
.hljs-keyword { color: ${hlKeyword}; }
.hljs-string { color: ${hlString}; }
.hljs-number { color: ${hlNumber}; }
.hljs-comment { color: ${commentColor}; font-style: italic; }
.hljs-function { color: ${hlFunction}; }
.hljs-built_in { color: ${hlBuiltIn}; }
.hljs-title { color: ${hlFunction}; }
.hljs-params { color: ${paramColor}; }
.hljs-attr { color: ${hlBuiltIn}; }
.hljs-tag { color: ${hlTag}; }
.hljs-name { color: ${hlTag}; }
`;
}

/**
 * Renders panel content to a THREE.CanvasTexture using an offscreen
 * foreignObject SVG pipeline. Works in both desktop and WebXR contexts.
 */
export class PanelRenderer {
  private canvas: HTMLCanvasElement;
  private ctx: CanvasRenderingContext2D;
  private texture: THREE.CanvasTexture;
  private resW: number;
  private resH: number;
  private videoEl: HTMLVideoElement | null = null;
  private sandboxEl: HTMLIFrameElement | null = null;
  private liveInterval: ReturnType<typeof setInterval> | null = null;
  private hiddenContainer: HTMLDivElement;
  private dark = true;
  private lastType: PanelContentType | null = null;
  private lastContent = '';
  private lastTitle: string | undefined;
  private frameAspect: number | undefined;
  private disposed = false;
  private renderVersion = 0;

  constructor(width = BASE_RES, height = BASE_RES, private immersive = false) {
    this.resW = width;
    this.resH = height;

    this.canvas = document.createElement('canvas');
    this.canvas.width = width;
    this.canvas.height = height;
    this.ctx = this.canvas.getContext('2d')!;

    this.texture = new THREE.CanvasTexture(this.canvas);
    this.texture.minFilter = THREE.LinearFilter;
    this.texture.magFilter = THREE.LinearFilter;
    this.texture.colorSpace = THREE.SRGBColorSpace;

    this.hiddenContainer = document.createElement('div');
    this.hiddenContainer.style.cssText = 'position:fixed;left:-9999px;top:-9999px;opacity:0;pointer-events:none;';
    document.body.appendChild(this.hiddenContainer);
  }

  getTexture(): THREE.CanvasTexture {
    return this.texture;
  }

  setImmersive(value: boolean): void {
    this.immersive = value;
  }

  setFrameAspect(aspect: number): void {
    this.frameAspect = Number.isFinite(aspect) && aspect > 0 ? aspect : undefined;
  }

  setTheme(dark: boolean): void {
    this.dark = dark;
  }

  async rerender(): Promise<{ width: number; height: number } | null> {
    if (!this.lastType || this.disposed) return null;
    // Appearance changes must not restart playback or replace a running app.
    if (this.lastType === 'video' && this.videoEl) {
      return this.paintVideoFrame(this.lastTitle);
    }
    if (this.lastType === 'sandbox' && this.sandboxEl) {
      return this.fitCanvasToFrame(this.renderContentToCanvas('html', this.lastContent, this.lastTitle));
    }
    return this.render(this.lastType, this.lastContent, this.lastTitle);
  }

  async render(
    type: PanelContentType,
    content: string,
    title?: string,
  ): Promise<{ width: number; height: number }> {
    const version = ++this.renderVersion;
    await loadSpatialAssets();
    if (this.disposed || version !== this.renderVersion) return this.canvasSize();
    this.stopLiveUpdates();
    this.lastType = type;
    this.lastContent = content;
    this.lastTitle = title;
    let size: { width: number; height: number };
    switch (type) {
      case 'video':
        return this.renderVideo(content, title, version);
      case 'sandbox':
        size = await this.renderSandbox(content, title);
        break;
      default:
        size = await this.renderHTML(type, content, title, version);
        break;
    }
    if (this.disposed || version !== this.renderVersion) return this.canvasSize();
    return this.fitCanvasToFrame(size);
  }

  private canvasSize(): { width: number; height: number } {
    return { width: this.canvas.width, height: this.canvas.height };
  }

  private frameSize(width: number, height: number): { width: number; height: number } {
    const desiredH = this.frameAspect ? width / this.frameAspect : height;
    const limit = Math.max(this.resW, this.resH) * 2;
    const scale = Math.min(1, limit / Math.max(width, desiredH));
    return { width: Math.max(1, Math.round(width * scale)), height: Math.max(1, Math.round(desiredH * scale)) };
  }

  private fitCanvasToFrame(size: { width: number; height: number }): { width: number; height: number } {
    if (!this.frameAspect) return size;
    // A restored or resized panel owns its physical shape. Letterbox its content
    // when XR typography reflows so letters and images are never stretched.
    const { width: targetW, height: targetH } = this.frameSize(size.width, size.height);
    if (targetW === size.width && targetH === size.height) return size;
    const copy = document.createElement('canvas');
    copy.width = size.width; copy.height = size.height;
    copy.getContext('2d')!.drawImage(this.canvas, 0, 0);
    this.canvas.width = targetW; this.canvas.height = targetH;
    this.ctx = this.canvas.getContext('2d')!;
    this.ctx.fillStyle = this.immersive ? SPATIAL.surface : this.dark ? '#0E0E12' : '#F8F8FA';
    this.ctx.fillRect(0, 0, targetW, targetH);
    const fit = Math.min(targetW / size.width, targetH / size.height);
    const fitW = size.width * fit, fitH = size.height * fit;
    this.ctx.drawImage(copy, (targetW - fitW) / 2, 0, fitW, fitH);
    this.texture.needsUpdate = true;
    return this.canvasSize();
  }

  private async renderHTML(
    type: PanelContentType,
    content: string,
    title?: string,
    version = this.renderVersion,
  ): Promise<{ width: number; height: number }> {
    if (type === 'image') {
      return this.renderImage(content, title, version);
    }
    return this.renderContentToCanvas(type, content, title);
  }

  private async renderImage(url: string, title?: string, version = this.renderVersion): Promise<{ width: number; height: number }> {
    const w = this.resW;
    const h = this.resH;
    this.canvas.width = w;
    this.canvas.height = h;

    this.ctx.clearRect(0, 0, w, h);

    if (this.immersive) {
      this.ctx.fillStyle = SPATIAL.surface;
      this.ctx.fillRect(0, 0, w, h);
    }
    let yOffset = 0;
    if (title) {
      this.ctx.fillStyle = (this.immersive || this.dark) ? 'rgba(255,255,255,0.9)' : 'rgba(15,15,20,0.85)';
      setSpatialFont(this.ctx, '600 16px "Azeret Mono", ui-monospace, monospace');
      this.ctx.fillText(title, 24, 30);
      yOffset = 46;
    }

    return new Promise((resolve) => {
      const img = new Image();
      img.crossOrigin = 'anonymous';
      img.onload = () => {
        if (this.disposed || version !== this.renderVersion) { resolve(this.canvasSize()); return; }
        const availH = h - yOffset - 24;
        const availW = w - 48;
        const aspect = img.naturalWidth / (img.naturalHeight || 1);
        let dw = availW;
        let dh = dw / aspect;
        if (dh > availH) { dh = availH; dw = dh * aspect; }
        const dx = (w - dw) / 2;
        const dy = yOffset + (availH - dh) / 2;
        try {
          this.ctx.drawImage(img, dx, dy, dw, dh);
        } catch {
          this.ctx.fillStyle = (this.immersive || this.dark) ? SPATIAL.muted : 'rgba(0,0,0,0.4)';
          setSpatialFont(this.ctx, '13px "Azeret Mono", monospace');
          this.ctx.fillText('Image could not be displayed (CORS)', 24, yOffset + 30);
        }
        this.texture.needsUpdate = true;
        resolve({ width: w, height: h });
      };
      img.onerror = () => {
        if (this.disposed || version !== this.renderVersion) { resolve(this.canvasSize()); return; }
        this.ctx.fillStyle = (this.immersive || this.dark) ? SPATIAL.muted : 'rgba(0,0,0,0.4)';
        setSpatialFont(this.ctx, '13px "Azeret Mono", monospace');
        this.ctx.fillText('Failed to load image', 24, yOffset + 30);
        this.texture.needsUpdate = true;
        resolve({ width: w, height: h });
      };
      img.src = url;
    });
  }

  private renderCode(content: string): string {
    let highlighted: string;
    try {
      const result = hljs.highlightAuto(content);
      highlighted = result.value;
    } catch {
      highlighted = this.escapeHTML(content);
    }
    return `<pre><code>${highlighted}</code></pre>`;
  }

  private renderChart(jsonContent: string): string {
    try {
      const data = JSON.parse(jsonContent);
      const items = Array.isArray(data) ? data : data.data ?? [];
      if (!items.length) return '<p>No chart data</p>';

      const maxVal = Math.max(...items.map((d: any) => d.value ?? d.y ?? 0));
      let barsHTML = '';
      for (const item of items) {
        const val = item.value ?? item.y ?? 0;
        const label = item.label ?? item.x ?? item.name ?? '';
        const pct = maxVal > 0 ? (val / maxVal) * 100 : 0;
        barsHTML += `
          <div style="display:flex;align-items:center;margin-bottom:6px;">
            <span style="width:80px;font-size:12px;color:rgba(255,255,255,0.6);flex-shrink:0;">${this.escapeHTML(String(label))}</span>
            <div style="flex:1;height:20px;background:rgba(255,255,255,0.04);border-radius:4px;overflow:hidden;">
              <div style="width:${pct}%;height:100%;background:rgba(255,255,255,0.25);border-radius:4px;"></div>
            </div>
            <span style="width:50px;text-align:right;font-size:12px;color:rgba(255,255,255,0.6);margin-left:8px;">${val}</span>
          </div>`;
      }
      return barsHTML;
    } catch {
      return '<p>Invalid chart data</p>';
    }
  }

  private toXHTML(html: string): string {
    return html.replace(/<(br|hr|img|input|meta|link)(\s[^>]*?)?\s*\/?>/gi, '<$1$2/>');
  }

  /**
   * Strip patterns that cause the SVG→canvas path to become tainted (WebGL then
   * throws on texSubImage2D). drawImage often does not throw; getImageData does.
   */
  private sanitizeForForeignObject(html: string): string {
    let s = html;
    s = s.replace(/<script\b[^<]*(?:(?!<\/script>)<[^<]*)*<\/script>/gi, '');
    s = s.replace(/<style\b[^<]*(?:(?!<\/style>)<[^<]*)*<\/style>/gi, '');
    for (const tag of ['iframe', 'object', 'embed', 'video', 'audio', 'canvas'] as const) {
      const reBlock = new RegExp(`<${tag}\\b[^>]*>[\\s\\S]*?<\\/${tag}>`, 'gi');
      s = s.replace(reBlock, '<span style="color:rgba(255,255,255,0.35)">[embedded]</span>');
      const reVoid = new RegExp(`<${tag}\\b[^>]*\\/?>`, 'gi');
      s = s.replace(reVoid, '<span style="color:rgba(255,255,255,0.35)">[embedded]</span>');
    }
    s = s.replace(/<picture\b[\s\S]*?<\/picture>/gi, '<span style="color:rgba(255,255,255,0.35)">[image]</span>');
    s = s.replace(/<img[^>]*>/gi, '<span style="color:rgba(255,255,255,0.35)">[image]</span>');
    s = s.replace(/<svg\b[\s\S]*?<\/svg>/gi, '<span style="color:rgba(255,255,255,0.35)">[svg]</span>');
    s = s.replace(/\sstyle\s*=\s*"([^"]*)"/gi, (_, st: string) => {
      const cleaned = st.replace(/url\s*\([^)]*\)/gi, 'none').replace(/@import[^;]+;?/gi, '');
      return ` style="${cleaned}"`;
    });
    s = s.replace(/\sstyle\s*=\s*'([^']*)'/gi, (_, st: string) => {
      const cleaned = st.replace(/url\s*\([^)]*\)/gi, 'none').replace(/@import[^;]+;?/gi, '');
      return ` style='${cleaned}'`;
    });
    return s;
  }

  private isCanvasTainted(canvas: HTMLCanvasElement): boolean {
    const c = canvas.getContext('2d');
    if (!c) return true;
    try {
      c.getImageData(0, 0, 1, 1);
      return false;
    } catch {
      return true;
    }
  }

  private wrapLines(ctx: CanvasRenderingContext2D, text: string, maxW: number): string[] {
    const lines: string[] = [];
    const words = text.split(/\s+/).filter(Boolean);
    let line = '';
    for (const word of words) {
      if (ctx.measureText(word).width > maxW) {
        if (line) { lines.push(line); line = ''; }
        let rest = word;
        while (rest.length) {
          let n = rest.length;
          while (n > 1 && ctx.measureText(rest.slice(0, n)).width > maxW) n--;
          lines.push(rest.slice(0, n));
          rest = rest.slice(n);
        }
        continue;
      }
      const test = line ? `${line} ${word}` : word;
      if (ctx.measureText(test).width <= maxW) line = test;
      else {
        if (line) lines.push(line);
        line = word;
      }
    }
    if (line) lines.push(line);
    return lines.length ? lines : [''];
  }

  private drawPlainTextFallback(html: string, w: number, h: number): void {
    const div = document.createElement('div');
    div.innerHTML = html;
    const text = (div.textContent ?? '').replace(/\s+/g, ' ').trim() || '(empty panel)';
    this.ctx.fillStyle = this.dark ? '#0a0a0f' : '#f5f5f7';
    this.ctx.fillRect(0, 0, w, h);
    this.ctx.fillStyle = this.dark ? 'rgba(255,255,255,0.88)' : 'rgba(15,15,20,0.85)';
    setSpatialFont(this.ctx, '15px "Azeret Mono", ui-monospace, monospace');
    const maxW = w - 48;
    const lines = this.wrapLines(this.ctx, text, maxW);
    const lineHeight = 24;
    let y = 40;
    for (const ln of lines) {
      if (y > h - 12) break;
      this.ctx.fillText(ln, 24, y);
      y += lineHeight;
    }
    this.texture.needsUpdate = true;
  }

  private async rasterizeHTML(html: string): Promise<{ width: number; height: number }> {
    const safeHTML = this.sanitizeForForeignObject(this.toXHTML(html));

    // Measure content height using a hidden DOM element
    const css = getPanelCSS(this.dark);
    const measurer = document.createElement('div');
    measurer.className = 'panel-root';
    measurer.style.cssText = `width:${this.resW - 48}px;position:absolute;visibility:hidden;`;
    measurer.innerHTML = `<style>${css}</style>${safeHTML}`;
    this.hiddenContainer.appendChild(measurer);

    // Force layout
    await new Promise(r => requestAnimationFrame(r));
    const contentH = Math.min(measurer.scrollHeight + 48, this.resH * 2);
    this.hiddenContainer.removeChild(measurer);

    // Resize canvas to fit content
    const w = this.resW;
    const h = Math.max(contentH, 80);
    // Force new backing store so prior tainted paints cannot break WebGL tex uploads.
    this.canvas.width = 0;
    this.canvas.height = 0;
    this.canvas.width = w;
    this.canvas.height = h;
    this.ctx = this.canvas.getContext('2d', { alpha: true })!;

    this.ctx.clearRect(0, 0, w, h);

    // Render via SVG foreignObject — draw only onto a probe canvas first; taint is silent until WebGL upload
    const svgStr = `<svg xmlns="http://www.w3.org/2000/svg" width="${w}" height="${h}">
  <foreignObject width="100%" height="100%">
    <div xmlns="http://www.w3.org/1999/xhtml" class="panel-root" style="width:${w}px;height:${h}px;">
      <style>${css}</style>
      ${safeHTML}
    </div>
  </foreignObject>
</svg>`;

    const blob = new Blob([svgStr], { type: 'image/svg+xml;charset=utf-8' });
    const url = URL.createObjectURL(blob);

    return new Promise((resolve) => {
      const img = new Image();
      img.onload = () => {
        const probe = document.createElement('canvas');
        probe.width = w;
        probe.height = h;
        const pctx = probe.getContext('2d')!;
        try {
          pctx.drawImage(img, 0, 0);
        } catch {
          URL.revokeObjectURL(url);
          this.drawPlainTextFallback(safeHTML, w, h);
          resolve({ width: w, height: h });
          return;
        }

        if (this.isCanvasTainted(probe)) {
          URL.revokeObjectURL(url);
          console.warn('[panel-renderer] SVG foreignObject raster is tainted; using plain-text fallback');
          this.drawPlainTextFallback(safeHTML, w, h);
          resolve({ width: w, height: h });
          return;
        }

        this.ctx.clearRect(0, 0, w, h);
        this.ctx.drawImage(probe, 0, 0);
        this.texture.needsUpdate = true;
        URL.revokeObjectURL(url);
        resolve({ width: w, height: h });
      };
      img.onerror = () => {
        URL.revokeObjectURL(url);
        this.ctx.fillStyle = 'rgba(255,255,255,0.8)';
        setSpatialFont(this.ctx, '14px "Azeret Mono", monospace');
        this.ctx.fillText('Render error', 24, 40);
        this.texture.needsUpdate = true;
        console.warn('[panel-renderer] SVG foreignObject render failed');
        resolve({ width: w, height: h });
      };
      img.src = url;
    });
  }

  // ─── Canvas-based content renderer (bypasses SVG foreignObject taint) ────────

  private getThemeColors(): ThemeColors {
    const d = this.immersive || this.dark;
    if (this.immersive) return {
      text: SPATIAL.text, heading: SPATIAL.text, muted: SPATIAL.muted,
      link: '#BEC6FF', codeBg: SPATIAL.raised, codeText: SPATIAL.text,
      blockBg: '#101A52', blockBorder: SPATIAL.line,
      border: SPATIAL.line, bqBorder: SPATIAL.accent,
      hlKeyword: '#C7BBFF', hlString: '#BCE9D7', hlNumber: '#FFD0B8',
      hlFunction: '#BFC8FF', hlComment: '#A6B0E0', hlBuiltIn: '#F0DCAD',
    };
    return {
      text: d ? 'rgba(255,255,255,0.88)' : 'rgba(15,15,20,0.85)',
      heading: d ? '#fff' : '#111',
      muted: d ? 'rgba(255,255,255,0.5)' : 'rgba(0,0,0,0.45)',
      link: d ? 'rgba(160,200,255,0.9)' : 'rgba(30,80,180,0.9)',
      codeBg: d ? 'rgba(255,255,255,0.07)' : 'rgba(0,0,0,0.05)',
      codeText: d ? 'rgba(255,255,255,0.8)' : 'rgba(15,15,20,0.75)',
      blockBg: d ? 'rgba(0,0,0,0.35)' : 'rgba(0,0,0,0.03)',
      blockBorder: d ? 'rgba(255,255,255,0.06)' : 'rgba(0,0,0,0.08)',
      border: d ? 'rgba(255,255,255,0.08)' : 'rgba(0,0,0,0.08)',
      bqBorder: d ? 'rgba(255,255,255,0.15)' : 'rgba(0,0,0,0.15)',
      hlKeyword: d ? '#c792ea' : '#7c4dff',
      hlString: d ? '#c3e88d' : '#2e7d32',
      hlNumber: d ? '#f78c6c' : '#e65100',
      hlFunction: d ? '#82aaff' : '#1565c0',
      hlComment: d ? 'rgba(255,255,255,0.3)' : 'rgba(0,0,0,0.35)',
      hlBuiltIn: d ? '#ffcb6b' : '#b8860b',
    };
  }

  private renderContentToCanvas(
    type: PanelContentType,
    content: string,
    title?: string,
  ): { width: number; height: number } {
    // Render larger type in XR, at 2x density. World size and grab geometry stay stable.
    const density = this.immersive ? 2 : 1;
    const w = this.resW / density;
    const pad = 24;
    const colors = this.getThemeColors();
    const tmp = document.createElement('canvas');
    const tmpCtx = tmp.getContext('2d')!;
    tmpCtx.textBaseline = 'top';
    const contentH = this.paintContent(tmpCtx, type, content, title, w, 99999, pad, colors, true);
    const h = Math.min(Math.max(Math.ceil(contentH) + pad, 120), this.resH * 2 / density);
    this.canvas.width = w * density;
    this.canvas.height = h * density;
    this.ctx = this.canvas.getContext('2d', { alpha: true })!;
    this.ctx.scale(density, density);
    this.ctx.textBaseline = 'top';
    this.ctx.fillStyle = this.immersive ? SPATIAL.surface : this.dark ? 'rgba(14,14,18,0.98)' : 'rgba(248,248,250,0.98)';
    this.ctx.fillRect(0, 0, w, h);
    this.paintContent(this.ctx, type, content, title, w, h, pad, colors, false);
    this.texture.needsUpdate = true;
    return { width: this.canvas.width, height: this.canvas.height };
  }

  private paintContent(
    ctx: CanvasRenderingContext2D,
    type: PanelContentType,
    content: string,
    title: string | undefined,
    w: number, maxH: number, pad: number,
    colors: ThemeColors,
    measure: boolean,
  ): number {
    const maxW = w - pad * 2;
    let y = pad;
    if (this.immersive) {
      if (!measure) {
        ctx.fillStyle = SPATIAL.accent; ctx.fillRect(0, 0, w, 54);
        drawBrand(ctx, pad - 3, 13, 28);
        setSpatialFont(ctx, `600 12px ${SPATIAL.font}`, 0.08);
        ctx.fillStyle = SPATIAL.text; ctx.fillText('NGRAM', pad + 33, 21);
        ctx.textAlign = 'right'; ctx.fillText(CONTENT_LABELS[type], w - pad, 21);
        ctx.textAlign = 'left';
      }
      y = 76;
    }

    if (title) {
      setSpatialFont(ctx, `600 ${this.immersive ? 23 : 18}px ${SPATIAL.font}`, -0.045);
      if (!measure) ctx.fillStyle = colors.heading;
      const lines = this.wrapLines(ctx, title, maxW);
      for (const line of lines) {
        if (!measure) ctx.fillText(line, pad, y);
        y += 28;
      }
      y += 2;
      if (!measure) {
        ctx.strokeStyle = colors.border;
        ctx.lineWidth = 1;
        ctx.beginPath();
        ctx.moveTo(pad, y);
        ctx.lineTo(w - pad, y);
        ctx.stroke();
      }
      y += 12;
    }

    switch (type) {
      case 'markdown':
      case 'card':
        y = this.paintMarkdown(ctx, content, pad, y, maxW, maxH, colors, measure);
        break;
      case 'code':
        y = this.paintCodeBlock(ctx, content, pad, y, maxW, maxH, colors, measure);
        break;
      case 'chart':
        y = this.paintChart(ctx, content, pad, y, maxW, maxH, colors, measure);
        break;
      case 'html': {
        const div = document.createElement('div');
        div.innerHTML = content;
        const text = div.textContent ?? content;
        y = this.paintWrappedText(ctx, text, pad, y, maxW, 15, '400', colors.text, measure);
        break;
      }
      default:
        y = this.paintWrappedText(ctx, content, pad, y, maxW, 15, '400', colors.text, measure);
        break;
    }

    return y;
  }

  private paintWrappedText(
    ctx: CanvasRenderingContext2D,
    text: string,
    x: number, y: number, maxW: number,
    fontSize: number, weight: string, color: string,
    measure: boolean,
  ): number {
    const lineH = Math.round(fontSize * 1.55);
    setSpatialFont(ctx, `${weight} ${fontSize}px "Azeret Mono", ui-monospace, monospace`);
    if (!measure) ctx.fillStyle = color;
    const lines = this.wrapLines(ctx, text, maxW);
    for (const line of lines) {
      if (!measure) ctx.fillText(line, x, y);
      y += lineH;
    }
    return y;
  }

  private paintMarkdown(
    ctx: CanvasRenderingContext2D,
    content: string,
    x: number, startY: number,
    maxW: number, maxH: number,
    colors: ThemeColors,
    measure: boolean,
  ): number {
    const tokens = marked.lexer(content);
    let y = startY;
    for (const token of tokens) {
      if (y > maxH - 20) break;
      y = this.paintToken(ctx, token as any, x, y, maxW, maxH, colors, measure);
    }
    return y;
  }

  private paintToken(
    ctx: CanvasRenderingContext2D,
    token: any,
    x: number, y: number,
    maxW: number, maxH: number,
    colors: ThemeColors,
    measure: boolean,
  ): number {
    switch (token.type) {
      case 'heading': {
        const sizes = [22, 18, 16, 15, 14, 13];
        const size = sizes[Math.min(token.depth - 1, 5)];
        y += 4;
        y = this.paintWrappedText(ctx, this.tokenPlainText(token), x, y, maxW, size, '600', colors.heading, measure);
        y += 6;
        break;
      }
      case 'paragraph': {
        const runs = this.flattenInline(token.tokens ?? []);
        y = this.paintRuns(ctx, runs, x, y, maxW, 15, colors, measure);
        y += 10;
        break;
      }
      case 'code': {
        y = this.paintCodeBlock(ctx, token.text ?? '', x, y, maxW, maxH, colors, measure);
        y += 10;
        break;
      }
      case 'list': {
        for (let i = 0; i < token.items.length; i++) {
          const item = token.items[i];
          const prefix = token.ordered ? `${(token.start ?? 1) + i}. ` : '\u2022  ';
          setSpatialFont(ctx, '400 15px "Azeret Mono", ui-monospace, monospace');
          const prefixW = ctx.measureText(prefix).width;

          if (!measure) {
            ctx.fillStyle = colors.muted;
            ctx.fillText(prefix, x, y);
          }

          const firstToken = item.tokens?.[0];
          const inlineTokens = firstToken?.tokens ?? (firstToken ? [firstToken] : []);
          const runs = this.flattenInline(inlineTokens);
          y = this.paintRuns(ctx, runs, x + prefixW, y, maxW - prefixW, 15, colors, measure);
          y += 4;
        }
        y += 6;
        break;
      }
      case 'blockquote': {
        const startBqY = y;
        for (const t of (token.tokens ?? [])) {
          y = this.paintToken(ctx, t, x + 14, y, maxW - 14, maxH, colors, measure);
        }
        if (!measure) {
          ctx.fillStyle = colors.bqBorder;
          ctx.fillRect(x, startBqY, 3, y - startBqY);
        }
        y += 6;
        break;
      }
      case 'hr': {
        if (!measure) {
          ctx.strokeStyle = colors.border;
          ctx.lineWidth = 1;
          ctx.beginPath();
          ctx.moveTo(x, y + 6);
          ctx.lineTo(x + maxW, y + 6);
          ctx.stroke();
        }
        y += 16;
        break;
      }
      case 'table': {
        y = this.paintTable(ctx, token, x, y, maxW, colors, measure);
        y += 10;
        break;
      }
      case 'space':
        y += 8;
        break;
      default: {
        const text = this.tokenPlainText(token);
        if (text) {
          y = this.paintWrappedText(ctx, text, x, y, maxW, 15, '400', colors.text, measure);
          y += 10;
        }
        break;
      }
    }
    return y;
  }

  private tokenPlainText(token: any): string {
    if (typeof token.text === 'string') return token.text;
    if (token.tokens) return token.tokens.map((t: any) => this.tokenPlainText(t)).join('');
    if (token.raw) return token.raw;
    return '';
  }

  private flattenInline(tokens: any[]): StyledRun[] {
    const runs: StyledRun[] = [];
    function walk(toks: any[], bold: boolean, italic: boolean) {
      for (const t of toks) {
        switch (t.type) {
          case 'text':
          case 'escape':
            runs.push({ text: t.text ?? t.raw ?? '', bold, italic, code: false });
            break;
          case 'strong':
            if (t.tokens?.length) walk(t.tokens, true, italic);
            else runs.push({ text: t.text ?? '', bold: true, italic, code: false });
            break;
          case 'em':
            if (t.tokens?.length) walk(t.tokens, bold, true);
            else runs.push({ text: t.text ?? '', bold, italic: true, code: false });
            break;
          case 'codespan':
            runs.push({ text: t.text ?? '', bold: false, italic: false, code: true });
            break;
          case 'link':
            if (t.tokens?.length) walk(t.tokens, bold, italic);
            else runs.push({ text: t.text ?? t.href ?? '', bold, italic, code: false });
            break;
          case 'br':
            runs.push({ text: '\n', bold, italic, code: false });
            break;
          default:
            if (t.tokens?.length) walk(t.tokens, bold, italic);
            else if (t.text) runs.push({ text: t.text, bold, italic, code: false });
            else if (t.raw) runs.push({ text: t.raw, bold, italic, code: false });
            break;
        }
      }
    }
    walk(tokens, false, false);
    return runs;
  }

  private paintRuns(
    ctx: CanvasRenderingContext2D,
    runs: StyledRun[],
    x: number, startY: number,
    maxW: number, fontSize: number,
    colors: ThemeColors,
    measure: boolean,
  ): number {
    const codeFontSize = fontSize - 2;
    const lineH = Math.round(fontSize * 1.55);
    let curX = x;
    let curY = startY;

    for (const run of runs) {
      if (run.text === '\n') {
        curX = x;
        curY += lineH;
        continue;
      }

      const sz = run.code ? codeFontSize : fontSize;
      const wt = run.bold ? '600' : '400';
      const st = run.italic ? 'italic ' : '';
      const family = run.code
        ? '"Azeret Mono", ui-monospace, monospace'
        : '"Azeret Mono", ui-monospace, monospace';
      setSpatialFont(ctx, `${st}${wt} ${sz}px ${family}`, run.code ? 0 : -0.035);

      const parts = run.text.split(/(\s+)/);
      for (const part of parts) {
        if (!part) continue;
        const pw = ctx.measureText(part).width;

        if (curX + pw > x + maxW && curX > x && part.trim()) {
          curX = x;
          curY += lineH;
        }

        if (!measure && part.trim()) {
          if (run.code) {
            const bgPad = 3;
            ctx.fillStyle = colors.codeBg;
            ctx.fillRect(curX - bgPad, curY - bgPad, pw + bgPad * 2, sz + bgPad * 2);
            ctx.fillStyle = colors.codeText;
          } else {
            ctx.fillStyle = colors.text;
          }
          ctx.fillText(part, curX, curY);
        }

        curX += pw;
      }
    }

    return curY + lineH;
  }

  private paintCodeBlock(
    ctx: CanvasRenderingContext2D,
    code: string,
    x: number, y: number,
    maxW: number, _maxH: number,
    colors: ThemeColors,
    measure: boolean,
  ): number {
    const fontSize = 13;
    const lineH = Math.round(fontSize * 1.5);
    const blockPad = 14;

    let highlighted: string;
    try {
      highlighted = hljs.highlightAuto(code).value;
    } catch {
      highlighted = this.escapeHTML(code);
    }

    const lines = this.parseHighlightedLines(highlighted, colors);
    const blockH = lines.length * lineH + blockPad * 2;

    if (!measure) {
      ctx.beginPath();
      ctx.roundRect(x, y, maxW, blockH, 8);
      ctx.fillStyle = colors.blockBg;
      ctx.fill();
      ctx.strokeStyle = colors.blockBorder;
      ctx.lineWidth = 1;
      ctx.stroke();
    }

    let lineY = y + blockPad;
    setSpatialFont(ctx, `400 ${fontSize}px "Azeret Mono", ui-monospace, monospace`, 0);
    for (const segs of lines) {
      let lineX = x + blockPad;
      for (const seg of segs) {
        if (!measure) {
          ctx.fillStyle = seg.color;
          ctx.fillText(seg.text, lineX, lineY);
        }
        lineX += ctx.measureText(seg.text).width;
      }
      lineY += lineH;
    }

    return y + blockH;
  }

  private parseHighlightedLines(
    html: string,
    colors: ThemeColors,
  ): Array<Array<{ text: string; color: string }>> {
    const colorMap: Record<string, string> = {
      'hljs-keyword': colors.hlKeyword,
      'hljs-string': colors.hlString,
      'hljs-number': colors.hlNumber,
      'hljs-function': colors.hlFunction,
      'hljs-title': colors.hlFunction,
      'hljs-comment': colors.hlComment,
      'hljs-built_in': colors.hlBuiltIn,
      'hljs-params': colors.muted,
      'hljs-attr': colors.hlBuiltIn,
      'hljs-tag': colors.hlKeyword,
      'hljs-name': colors.hlKeyword,
      'hljs-literal': colors.hlNumber,
      'hljs-type': colors.hlBuiltIn,
      'hljs-meta': colors.hlComment,
      'hljs-regexp': colors.hlString,
      'hljs-subst': colors.text,
      'hljs-variable': colors.text,
      'hljs-property': colors.hlBuiltIn,
    };

    const div = document.createElement('div');
    div.innerHTML = html;

    type Seg = { text: string; color: string };
    const all: Seg[] = [];

    const walk = (node: Node, parentColor: string) => {
      if (node.nodeType === Node.TEXT_NODE) {
        const t = node.textContent ?? '';
        if (t) all.push({ text: t, color: parentColor });
      } else if (node.nodeType === Node.ELEMENT_NODE) {
        const el = node as Element;
        let color = parentColor;
        for (const cls of el.classList) {
          if (colorMap[cls]) { color = colorMap[cls]; break; }
        }
        for (const child of el.childNodes) walk(child, color);
      }
    };
    walk(div, colors.codeText);

    const lines: Array<Seg[]> = [[]];
    for (const seg of all) {
      const parts = seg.text.split('\n');
      for (let i = 0; i < parts.length; i++) {
        if (i > 0) lines.push([]);
        if (parts[i]) lines[lines.length - 1].push({ text: parts[i], color: seg.color });
      }
    }
    return lines;
  }

  private paintChart(
    ctx: CanvasRenderingContext2D,
    jsonContent: string,
    x: number, y: number,
    maxW: number, _maxH: number,
    colors: ThemeColors,
    measure: boolean,
  ): number {
    try {
      const data = JSON.parse(jsonContent);
      const items = Array.isArray(data) ? data : data.data ?? [];
      if (!items.length)
        return this.paintWrappedText(ctx, 'No chart data', x, y, maxW, 15, '400', colors.muted, measure);

      const maxVal = Math.max(...items.map((d: any) => d.value ?? d.y ?? 0));
      const barH = 20;
      const gap = 6;
      const labelW = 80;
      const valueW = 50;
      const barMaxW = maxW - labelW - valueW - 16;

      for (const item of items) {
        const val = item.value ?? item.y ?? 0;
        const label = String(item.label ?? item.x ?? item.name ?? '');
        const pct = maxVal > 0 ? val / maxVal : 0;

        if (!measure) {
          setSpatialFont(ctx, '400 12px "Azeret Mono", ui-monospace, monospace');
          ctx.fillStyle = colors.muted;
          ctx.fillText(label, x, y + 4, labelW);

          ctx.fillStyle = colors.codeBg;
          ctx.beginPath();
          ctx.roundRect(x + labelW + 8, y, barMaxW, barH, 4);
          ctx.fill();

          const fillW = Math.max(barMaxW * pct, 2);
          ctx.globalAlpha = 0.25;
          ctx.fillStyle = colors.text;
          ctx.beginPath();
          ctx.roundRect(x + labelW + 8, y, fillW, barH, 4);
          ctx.fill();
          ctx.globalAlpha = 1;

          ctx.fillStyle = colors.muted;
          ctx.fillText(String(val), x + labelW + barMaxW + 16, y + 4);
        }

        y += barH + gap;
      }
      return y;
    } catch {
      return this.paintWrappedText(ctx, 'Invalid chart data', x, y, maxW, 15, '400', colors.muted, measure);
    }
  }

  private paintTable(
    ctx: CanvasRenderingContext2D,
    token: any,
    x: number, y: number,
    maxW: number,
    colors: ThemeColors,
    measure: boolean,
  ): number {
    const header = token.header ?? [];
    const rows = token.rows ?? [];
    if (!header.length) return y;

    const colCount = header.length;
    const colW = Math.floor(maxW / colCount);
    const rowH = 28;
    const cellPad = 8;

    if (!measure) {
      ctx.fillStyle = colors.codeBg;
      ctx.fillRect(x, y, maxW, rowH);
    }
    setSpatialFont(ctx, '600 12px "Azeret Mono", ui-monospace, monospace');
    for (let c = 0; c < colCount; c++) {
      const text = this.tokenPlainText(header[c]) || '';
      if (!measure) {
        ctx.fillStyle = colors.heading;
        ctx.fillText(text, x + c * colW + cellPad, y + 8, colW - cellPad * 2);
      }
    }
    y += rowH;

    if (!measure) {
      ctx.strokeStyle = colors.border;
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.moveTo(x, y); ctx.lineTo(x + maxW, y);
      ctx.stroke();
    }

    setSpatialFont(ctx, '400 12px "Azeret Mono", ui-monospace, monospace');
    for (const row of rows) {
      for (let c = 0; c < colCount; c++) {
        const text = this.tokenPlainText(row[c]) || '';
        if (!measure) {
          ctx.fillStyle = colors.text;
          ctx.fillText(text, x + c * colW + cellPad, y + 8, colW - cellPad * 2);
        }
      }
      y += rowH;
      if (!measure) {
        ctx.strokeStyle = colors.border;
        ctx.lineWidth = 0.5;
        ctx.beginPath();
        ctx.moveTo(x, y); ctx.lineTo(x + maxW, y);
        ctx.stroke();
      }
    }
    return y;
  }

  private async renderVideo(url: string, title: string | undefined, version: number): Promise<{ width: number; height: number }> {
    const video = document.createElement('video');
    this.videoEl = video;
    video.src = url;
    video.crossOrigin = 'anonymous';
    video.muted = true;
    video.loop = true;
    video.playsInline = true;
    this.hiddenContainer.appendChild(video);
    await video.play().catch(() => {});
    if (this.disposed || version !== this.renderVersion || this.videoEl !== video) return this.canvasSize();
    const size = this.paintVideoFrame(title);
    this.liveInterval = setInterval(() => {
      if (!video.paused && video.readyState >= 2) this.paintVideoFrame(this.lastTitle);
    }, 1000 / 24);
    return size;
  }

  private paintVideoFrame(title?: string): { width: number; height: number } {
    const { width: w, height: h } = this.frameSize(this.resW, this.resH);
    if (this.canvas.width !== w || this.canvas.height !== h) {
      this.canvas.width = w; this.canvas.height = h;
    }
    const ctx = this.ctx;
    ctx.setTransform(1, 0, 0, 1, 0, 0);
    ctx.textAlign = 'left';
    ctx.clearRect(0, 0, w, h);
    if (this.immersive) {
      ctx.fillStyle = SPATIAL.surface;
      ctx.fillRect(0, 0, w, h);
    }
    let yOff = 0;
    if (title) {
      const fontSize = this.immersive ? 30 : 16;
      const lineHeight = this.immersive ? 40 : 24;
      setSpatialFont(ctx, `600 ${fontSize}px ${SPATIAL.font}`);
      ctx.textBaseline = 'top';
      ctx.fillStyle = '#FFFFFF';
      const lines = this.wrapLines(ctx, title, w - 48);
      lines.forEach((line, i) => ctx.fillText(line, 24, 20 + i * lineHeight));
      yOff = 32 + lines.length * lineHeight;
    }
    const video = this.videoEl;
    const availH = h - yOff;
    if (video && video.readyState >= 2 && video.videoWidth > 0 && video.videoHeight > 0 && availH > 0) {
      const fit = Math.min(w / video.videoWidth, availH / video.videoHeight);
      const dw = video.videoWidth * fit, dh = video.videoHeight * fit;
      ctx.drawImage(video, (w - dw) / 2, yOff + (availH - dh) / 2, dw, dh);
    }
    this.texture.needsUpdate = true;
    return this.canvasSize();
  }

  private async renderSandbox(htmlContent: string, title?: string): Promise<{ width: number; height: number }> {
    this.stopLiveUpdates();

    this.sandboxEl = document.createElement('iframe');
    this.sandboxEl.sandbox.add('allow-scripts');
    this.sandboxEl.style.cssText = `width:${this.resW}px;height:${this.resH}px;border:none;`;
    const sbBg = this.dark ? '#0c0c0e' : '#f5f5f7';
    const sbFg = this.dark ? '#fff' : '#111';
    this.sandboxEl.srcdoc = `<!DOCTYPE html><html><head><meta charset="utf-8"><style>body{margin:0;background:${sbBg};color:${sbFg};font-family:"Azeret Mono",monospace;}</style></head><body>${htmlContent}</body></html>`;
    this.hiddenContainer.appendChild(this.sandboxEl);

    return this.renderContentToCanvas('html', htmlContent, title);
  }

  stopLiveUpdates(): void {
    if (this.liveInterval) {
      clearInterval(this.liveInterval);
      this.liveInterval = null;
    }
    if (this.videoEl) {
      this.videoEl.pause();
      this.videoEl.remove();
      this.videoEl = null;
    }
    if (this.sandboxEl) {
      this.sandboxEl.remove();
      this.sandboxEl = null;
    }
  }

  dispose(): void {
    if (this.disposed) return;
    this.disposed = true;
    this.renderVersion++;
    this.stopLiveUpdates();
    this.texture.dispose();
    this.hiddenContainer.remove();
  }

  private escapeHTML(str: string): string {
    return str
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }
}
