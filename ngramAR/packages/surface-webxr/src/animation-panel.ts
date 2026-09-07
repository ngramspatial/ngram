// @ts-nocheck
import type { AvatarController, ClipInfo } from './avatar.js';

const SVG_PLAY = '<svg viewBox="0 0 24 24"><path d="M8 5v14l11-7z"/></svg>';
const SVG_STOP = '<svg viewBox="0 0 24 24"><path d="M6 6h12v12H6z"/></svg>';

export class AnimationPanel {
  private avatar: AvatarController | null = null;
  private panelEl: HTMLElement | null = null;
  private emptyEl: HTMLElement | null = null;
  private clipListSection: HTMLElement | null = null;
  private stateMapSection: HTMLElement | null = null;
  private currentPlaying: string | null = null;
  private lastClipCount = 0;
  private lastClipSignature = '';
  private lastActiveClip = '';

  attach(avatar: AvatarController): void {
    this.avatar = avatar;
    this.panelEl = document.getElementById('anim-panel');
    this.emptyEl = document.getElementById('anim-empty');
  }

  refresh(): void {
    if (!this.avatar || !this.panelEl) return;

    const clips = this.avatar.getClipInfo();
    const activeClip = this.avatar.getActiveClipName();

    if (clips.length === 0) {
      if (this.emptyEl) this.emptyEl.style.display = '';
      if (this.clipListSection) this.clipListSection.style.display = 'none';
      if (this.stateMapSection) this.stateMapSection.style.display = 'none';
      this.lastClipCount = 0;
      this.lastClipSignature = '';
      return;
    }

    if (this.emptyEl) this.emptyEl.style.display = 'none';

    const clipSignature = clips
      .map((clip) => `${clip.name}:${clip.duration.toFixed(3)}`)
      .sort()
      .join('|');

    if (clipSignature !== this.lastClipSignature) {
      this.rebuild(clips, activeClip);
      this.lastClipCount = clips.length;
      this.lastClipSignature = clipSignature;
      this.lastActiveClip = activeClip;
      return;
    }

    if (activeClip !== this.lastActiveClip) {
      this.updateActiveHighlight(activeClip);
      this.lastActiveClip = activeClip;
    }
  }

  private rebuild(clips: ClipInfo[], activeClip: string): void {
    if (!this.panelEl) return;

    if (this.clipListSection) this.clipListSection.remove();
    if (this.stateMapSection) this.stateMapSection.remove();

    this.clipListSection = this.buildClipList(clips, activeClip);
    this.stateMapSection = this.buildStateMap(activeClip);

    this.panelEl.appendChild(this.clipListSection);
    this.panelEl.appendChild(this.stateMapSection);
  }

  private buildClipList(clips: ClipInfo[], activeClip: string): HTMLElement {
    const section = document.createElement('div');
    section.className = 'anim-section';

    const title = document.createElement('div');
    title.className = 'anim-section-title';
    title.textContent = `Loaded Clips (${clips.length})`;
    section.appendChild(title);

    const list = document.createElement('div');
    list.className = 'anim-clip-list';

    for (const clip of clips) {
      const row = document.createElement('div');
      row.className = 'anim-clip' + (clip.name === activeClip ? ' active' : '');
      row.setAttribute('data-clip', clip.name);

      const name = document.createElement('span');
      name.className = 'anim-clip-name';
      name.textContent = clip.name;
      name.title = clip.name;

      const duration = document.createElement('span');
      duration.className = 'anim-clip-duration';
      duration.textContent = clip.duration.toFixed(1) + 's';

      const btn = document.createElement('button');
      btn.className = 'anim-clip-btn' + (this.currentPlaying === clip.name ? ' playing' : '');
      btn.innerHTML = this.currentPlaying === clip.name ? SVG_STOP : SVG_PLAY;
      btn.title = this.currentPlaying === clip.name ? 'Stop' : 'Play';
      btn.addEventListener('click', (e) => {
        e.stopPropagation();
        this.togglePlay(clip.name);
      });

      row.appendChild(name);
      row.appendChild(duration);
      row.appendChild(btn);
      list.appendChild(row);
    }

    section.appendChild(list);
    return section;
  }

  private buildStateMap(activeClip: string): HTMLElement {
    const section = document.createElement('div');
    section.className = 'anim-section';

    const title = document.createElement('div');
    title.className = 'anim-section-title';
    title.textContent = 'Animation Pack (state \u2192 clip)';
    section.appendChild(title);

    const pack = this.avatar?.getAnimationPack() ?? {};
    const list = document.createElement('div');
    list.className = 'anim-clip-list';

    const entries = Object.entries(pack);
    if (entries.length === 0) {
      const empty = document.createElement('div');
      empty.className = 'anim-empty';
      empty.textContent = 'No animation pack configured';
      list.appendChild(empty);
    } else {
      for (const [state, clipFile] of entries) {
        const row = document.createElement('div');
        row.className = 'anim-state-row';

        const label = document.createElement('span');
        label.className = 'anim-state-label' + (state === activeClip ? ' current' : '');
        label.textContent = state;

        const value = document.createElement('span');
        value.className = 'anim-state-value';
        value.textContent = clipFile;
        value.title = clipFile;

        row.appendChild(label);
        row.appendChild(value);
        list.appendChild(row);
      }
    }

    section.appendChild(list);
    return section;
  }

  private togglePlay(clipName: string): void {
    if (!this.avatar) return;

    if (this.currentPlaying === clipName) {
      this.avatar.playClipByName('idle');
      this.currentPlaying = null;
    } else {
      this.avatar.playClipByName(clipName);
      this.currentPlaying = clipName;
    }

    this.updatePlayButtons();
  }

  private updatePlayButtons(): void {
    if (!this.clipListSection) return;
    const btns = this.clipListSection.querySelectorAll('.anim-clip-btn');
    btns.forEach((btn) => {
      const row = btn.closest('.anim-clip');
      const clipName = row?.getAttribute('data-clip') ?? '';
      const isPlaying = this.currentPlaying === clipName;
      btn.classList.toggle('playing', isPlaying);
      btn.innerHTML = isPlaying ? SVG_STOP : SVG_PLAY;
      (btn as HTMLElement).title = isPlaying ? 'Stop' : 'Play';
    });
  }

  private updateActiveHighlight(activeClip: string): void {
    if (!this.clipListSection) return;
    const rows = this.clipListSection.querySelectorAll('.anim-clip');
    rows.forEach((row) => {
      const name = row.getAttribute('data-clip') ?? '';
      row.classList.toggle('active', name === activeClip);
    });

    if (!this.stateMapSection) return;
    const labels = this.stateMapSection.querySelectorAll('.anim-state-label');
    labels.forEach((label) => {
      label.classList.toggle('current', label.textContent === activeClip);
    });
  }
}
