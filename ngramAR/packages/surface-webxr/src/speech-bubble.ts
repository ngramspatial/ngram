// @ts-nocheck
import * as THREE from 'three';
import { CSS2DRenderer, CSS2DObject } from 'three/addons/renderers/CSS2DRenderer.js';
import { captionPages } from './speech-captions.js';

export class SpeechBubbleManager {
  private renderer: CSS2DRenderer;
  private currentBubble: CSS2DObject | null = null;
  private currentEl: HTMLDivElement | null = null;
  private hideTimer: ReturnType<typeof setTimeout> | null = null;
  private anchor: THREE.Object3D | null = null;

  constructor() {
    this.renderer = new CSS2DRenderer();
    const container = document.getElementById('css2d-overlay');
    if (container) {
      this.renderer.domElement.style.position = 'absolute';
      this.renderer.domElement.style.top = '0';
      this.renderer.domElement.style.left = '0';
      this.renderer.domElement.style.pointerEvents = 'none';
      this.renderer.domElement.style.overflow = 'visible';
      container.appendChild(this.renderer.domElement);
    }

    const viewportContainer = document.querySelector('.viewport-container') as HTMLElement;
    const w = viewportContainer?.clientWidth || window.innerWidth;
    const h = viewportContainer?.clientHeight || window.innerHeight;
    this.renderer.setSize(w, h);

    if (viewportContainer) {
      const ro = new ResizeObserver((entries) => {
        for (const entry of entries) {
          const { width, height } = entry.contentRect;
          if (width > 0 && height > 0) {
            this.renderer.setSize(width, height);
          }
        }
      });
      ro.observe(viewportContainer);
    } else {
      window.addEventListener('resize', () => {
        this.renderer.setSize(window.innerWidth, window.innerHeight);
      });
    }
  }

  getRenderer(): CSS2DRenderer {
    return this.renderer;
  }

  show(text: string, scene: THREE.Scene, position: THREE.Vector3): void {
    if (this.hideTimer) { clearTimeout(this.hideTimer); this.hideTimer = null; }
    const caption = captionPages(text)[0]?.text ?? '';
    if (this.currentEl) {
      this.currentEl.textContent = caption;
      return;
    }

    const el = document.createElement('div');
    el.className = 'speech-bubble';
    el.textContent = caption;
    this.currentEl = el;

    const obj = new CSS2DObject(el);
    obj.center.set(0.5, 1);
    obj.position.copy(position);
    scene.add(obj);
    this.currentBubble = obj;

    requestAnimationFrame(() => el.classList.add('visible'));

    // Playback owns lifetime; a long utterance must not lose its caption.
  }

  showAtAvatar(text: string, scene: THREE.Scene, avatarPos: THREE.Vector3, headOffset = 2.0): void {
    const pos = avatarPos.clone();
    pos.y += headOffset;
    this.show(text, scene, pos);
    this.anchor = null;
  }

  showThinking(scene: THREE.Scene, avatarPos: THREE.Vector3, headOffset = 2.0): void {
    this.showAtAvatar('...', scene, avatarPos, headOffset);
    this.hideTimer = setTimeout(() => this.hide(scene), 15000);
  }

  hide(scene: THREE.Scene): void {
    if (this.hideTimer) {
      clearTimeout(this.hideTimer);
      this.hideTimer = null;
    }
    if (this.currentEl) {
      this.currentEl.classList.remove('visible');
    }
    if (this.currentBubble) {
      scene.remove(this.currentBubble);
      this.currentBubble = null;
      this.currentEl = null;
    }
  }

  updatePosition(avatarPos: THREE.Vector3, headOffset = 2.0): void {
    if (this.currentBubble) {
      this.currentBubble.position.set(avatarPos.x, avatarPos.y + headOffset, avatarPos.z);
    }
  }

  render(scene: THREE.Scene, camera: THREE.Camera): void {
    this.renderer.render(scene, camera);
  }
}
