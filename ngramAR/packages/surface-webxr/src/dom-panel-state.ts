export interface SavedDomPanelLayout {
  left: number;
  top: number;
  width: number;
  height: number;
}

function finite(value: unknown): value is number {
  return typeof value === 'number' && Number.isFinite(value);
}

export function captureDomPanelLayout(element: HTMLElement | null): SavedDomPanelLayout | undefined {
  if (!element) return undefined;
  const rect = element.getBoundingClientRect();
  return {
    left: Math.round(rect.left),
    top: Math.round(rect.top),
    width: Math.round(rect.width),
    height: Math.round(rect.height),
  };
}

export function applyDomPanelLayout(
  element: HTMLElement | null,
  value: unknown,
  minimum: { width: number; height: number },
): void {
  if (!element || !value || typeof value !== 'object') return;
  const layout = value as Partial<SavedDomPanelLayout>;
  if (![layout.left, layout.top, layout.width, layout.height].every(finite)) return;

  const width = Math.min(
    Math.max(minimum.width, layout.width!),
    Math.max(minimum.width, window.innerWidth - 16),
  );
  const height = Math.min(
    Math.max(minimum.height, layout.height!),
    Math.max(minimum.height, window.innerHeight - 16),
  );
  const left = Math.max(0, Math.min(layout.left!, Math.max(0, window.innerWidth - width)));
  const top = Math.max(0, Math.min(layout.top!, Math.max(0, window.innerHeight - height)));

  Object.assign(element.style, {
    left: `${left}px`,
    top: `${top}px`,
    width: `${width}px`,
    height: `${height}px`,
    right: 'auto',
    bottom: 'auto',
  });
}
