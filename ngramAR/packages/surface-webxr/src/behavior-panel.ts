// @ts-nocheck
const SVG_REMOVE = '<svg viewBox="0 0 24 24"><path d="M18.3 5.71a1 1 0 0 0-1.41 0L12 10.59 7.11 5.7A1 1 0 0 0 5.7 7.11L10.59 12 5.7 16.89a1 1 0 1 0 1.41 1.41L12 13.41l4.89 4.89a1 1 0 0 0 1.41-1.41L13.41 12l4.89-4.89a1 1 0 0 0 0-1.4Z"/></svg>';

const BEHAVIOR_PRIORITIES: Record<string, number> = {
  'anchor-to-surface': 20,
  'proximity-greet': 15,
  'gesture-respond': 12,
  'look-at-user': 10,
  'idle-breathe': 1,
};

const ALL_BEHAVIORS = [
  'look-at-user',
  'idle-breathe',
  'anchor-to-surface',
  'proximity-greet',
  'gesture-respond',
];

export interface BehaviorPanelState {
  activeBehaviors: string[];
  disabledBehaviors: Set<string>;
}

type ChangeCallback = (state: BehaviorPanelState) => void;

export class BehaviorPanel {
  private listEl: HTMLElement | null = null;
  private outputEl: HTMLElement | null = null;
  private addSelect: HTMLSelectElement | null = null;
  private addBtn: HTMLButtonElement | null = null;
  private state: BehaviorPanelState = {
    activeBehaviors: [],
    disabledBehaviors: new Set(),
  };
  private changeCallbacks: ChangeCallback[] = [];
  private lastOutputText = '';

  attach(): void {
    this.listEl = document.getElementById('behavior-list');
    this.outputEl = document.getElementById('behavior-output');
    this.addSelect = document.getElementById('behavior-add-select') as HTMLSelectElement;
    this.addBtn = document.getElementById('behavior-add-btn') as HTMLButtonElement;

    this.addBtn?.addEventListener('click', () => {
      const value = this.addSelect?.value;
      if (!value) return;
      if (this.state.activeBehaviors.includes(value)) return;
      this.state.activeBehaviors.push(value);
      this.state.disabledBehaviors.delete(value);
      this.render();
      this.notify();
      if (this.addSelect) this.addSelect.value = '';
    });
  }

  setBehaviors(behaviors: string[]): void {
    this.state.activeBehaviors = [...behaviors];
    this.state.disabledBehaviors.clear();
    this.render();
  }

  getState(): BehaviorPanelState {
    return {
      activeBehaviors: [...this.state.activeBehaviors],
      disabledBehaviors: new Set(this.state.disabledBehaviors),
    };
  }

  getEnabledBehaviors(): string[] {
    return this.state.activeBehaviors.filter(
      (b) => !this.state.disabledBehaviors.has(b)
    );
  }

  onChange(cb: ChangeCallback): void {
    this.changeCallbacks.push(cb);
  }

  updateOutput(output: Record<string, unknown>): void {
    if (!this.outputEl) return;
    const entries = Object.entries(output).filter(([, v]) => v !== undefined && v !== null);
    if (entries.length === 0) {
      if (this.lastOutputText !== 'No output') {
        this.outputEl.textContent = 'No output';
        this.lastOutputText = 'No output';
      }
      return;
    }
    const text = entries.map(([k, v]) => {
      if (typeof v === 'object') return `${k}: ${JSON.stringify(v)}`;
      return `${k}: ${v}`;
    }).join('\n');
    if (text !== this.lastOutputText) {
      this.outputEl.textContent = text;
      this.lastOutputText = text;
    }
  }

  private render(): void {
    if (!this.listEl) return;
    this.listEl.innerHTML = '';

    for (const name of this.state.activeBehaviors) {
      const isDisabled = this.state.disabledBehaviors.has(name);
      const priority = BEHAVIOR_PRIORITIES[name] ?? 0;

      const item = document.createElement('div');
      item.className = 'behavior-item' + (isDisabled ? ' disabled' : '');

      const toggle = document.createElement('label');
      toggle.className = 'behavior-toggle';
      const checkbox = document.createElement('input');
      checkbox.type = 'checkbox';
      checkbox.checked = !isDisabled;
      checkbox.addEventListener('change', () => {
        if (checkbox.checked) {
          this.state.disabledBehaviors.delete(name);
        } else {
          this.state.disabledBehaviors.add(name);
        }
        item.classList.toggle('disabled', !checkbox.checked);
        this.notify();
      });
      const track = document.createElement('div');
      track.className = 'toggle-track';
      const thumb = document.createElement('div');
      thumb.className = 'toggle-thumb';
      toggle.appendChild(checkbox);
      toggle.appendChild(track);
      toggle.appendChild(thumb);

      const nameEl = document.createElement('span');
      nameEl.className = 'behavior-item-name';
      nameEl.textContent = name;

      const prioEl = document.createElement('span');
      prioEl.className = 'behavior-item-priority';
      prioEl.textContent = `p${priority}`;
      prioEl.title = `Priority: ${priority}`;

      const removeBtn = document.createElement('button');
      removeBtn.className = 'behavior-item-remove';
      removeBtn.innerHTML = SVG_REMOVE;
      removeBtn.title = 'Remove behavior';
      removeBtn.addEventListener('click', () => {
        this.state.activeBehaviors = this.state.activeBehaviors.filter((b) => b !== name);
        this.state.disabledBehaviors.delete(name);
        this.render();
        this.notify();
      });

      item.appendChild(toggle);
      item.appendChild(nameEl);
      item.appendChild(prioEl);
      item.appendChild(removeBtn);
      this.listEl.appendChild(item);
    }

    this.updateAddOptions();
  }

  private updateAddOptions(): void {
    if (!this.addSelect) return;
    const options = this.addSelect.querySelectorAll('option');
    options.forEach((opt) => {
      if (!opt.value) return;
      (opt as HTMLOptionElement).disabled = this.state.activeBehaviors.includes(opt.value);
    });
  }

  private notify(): void {
    const snapshot = this.getState();
    this.changeCallbacks.forEach((cb) => cb(snapshot));
  }
}
