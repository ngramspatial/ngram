// @ts-nocheck
/**
 * Adds edge/corner resize handles to a fixed-position DOM overlay.
 *
 * Call `makeResizable(container, opts)` after the container is in the DOM.
 * Returns a dispose function that removes all listeners and handles.
 */

const HANDLE_SIZE = 10;

type Edge = 'n' | 's' | 'e' | 'w' | 'ne' | 'nw' | 'se' | 'sw';

const CURSORS: Record<Edge, string> = {
  n: 'ns-resize', s: 'ns-resize',
  e: 'ew-resize', w: 'ew-resize',
  ne: 'nesw-resize', sw: 'nesw-resize',
  nw: 'nwse-resize', se: 'nwse-resize',
};

interface ResizableOptions {
  minWidth?: number;
  minHeight?: number;
  maxWidth?: number;
  maxHeight?: number;
  /** If true, maintain aspect ratio while resizing from corners. */
  keepAspect?: boolean;
  onResize?: (w: number, h: number) => void;
}

export function makeResizable(
  container: HTMLElement,
  opts: ResizableOptions = {},
): () => void {
  const minW = opts.minWidth ?? 200;
  const minH = opts.minHeight ?? 120;
  const maxW = opts.maxWidth ?? window.innerWidth - 32;
  const maxH = opts.maxHeight ?? window.innerHeight - 32;

  const handles: HTMLDivElement[] = [];
  const edges: Edge[] = ['n', 's', 'e', 'w', 'ne', 'nw', 'se', 'sw'];

  for (const edge of edges) {
    const h = document.createElement('div');
    h.className = `hs-resize-handle hs-resize-${edge}`;
    h.dataset.edge = edge;
    applyHandleStyle(h, edge);
    container.appendChild(h);
    handles.push(h);
  }

  let active: {
    edge: Edge;
    handle: HTMLElement;
    pointerId: number;
    startX: number;
    startY: number;
    startW: number;
    startH: number;
    startLeft: number;
    startTop: number;
    aspect: number;
  } | null = null;

  const onDown = (e: PointerEvent) => {
    const target = e.target as HTMLElement;
    const edge = target.dataset?.edge as Edge | undefined;
    if (!edge) return;

    e.preventDefault();
    e.stopPropagation();

    const rect = container.getBoundingClientRect();
    active = {
      edge,
      handle: target,
      pointerId: e.pointerId,
      startX: e.clientX,
      startY: e.clientY,
      startW: rect.width,
      startH: rect.height,
      startLeft: rect.left,
      startTop: rect.top,
      aspect: rect.width / rect.height,
    };
    target.setPointerCapture?.(e.pointerId);
    document.body.style.cursor = CURSORS[edge];
    // Prevent iframe from stealing pointer events during resize
    container.style.pointerEvents = 'none';
    document.body.style.userSelect = 'none';
  };

  const onMove = (e: PointerEvent) => {
    if (!active) return;
    if (e.pointerId !== active.pointerId) return;

    const dx = e.clientX - active.startX;
    const dy = e.clientY - active.startY;
    const edge = active.edge;

    let newW = active.startW;
    let newH = active.startH;
    let newLeft = active.startLeft;
    let newTop = active.startTop;

    if (edge.includes('e')) newW = active.startW + dx;
    if (edge.includes('w')) { newW = active.startW - dx; newLeft = active.startLeft + dx; }
    if (edge.includes('s')) newH = active.startH + dy;
    if (edge.includes('n')) { newH = active.startH - dy; newTop = active.startTop + dy; }

    // Aspect ratio lock for corners
    if (opts.keepAspect && edge.length === 2) {
      const avgDelta = (Math.abs(dx) > Math.abs(dy)) ? dx : dy * active.aspect;
      if (edge.includes('e')) newW = active.startW + avgDelta;
      if (edge.includes('w')) { newW = active.startW - avgDelta; newLeft = active.startLeft + avgDelta; }
      newH = newW / active.aspect;
      if (edge.includes('n')) newTop = active.startTop + (active.startH - newH);
    }

    newW = Math.max(minW, Math.min(maxW, newW));
    newH = Math.max(minH, Math.min(maxH, newH));

    container.style.width = `${newW}px`;
    container.style.height = `${newH}px`;
    container.style.left = `${newLeft}px`;
    container.style.top = `${newTop}px`;
    // Clear right/bottom anchoring so left/top take effect
    container.style.right = 'auto';
    container.style.bottom = 'auto';

    opts.onResize?.(newW, newH);
  };

  const onUp = (e: PointerEvent) => {
    if (!active) return;
    if (e.pointerId !== active.pointerId) return;
    if (active.handle.hasPointerCapture?.(active.pointerId)) {
      active.handle.releasePointerCapture(active.pointerId);
    }
    active = null;
    document.body.style.cursor = '';
    container.style.pointerEvents = '';
    document.body.style.userSelect = '';
  };

  container.addEventListener('pointerdown', onDown);
  document.addEventListener('pointermove', onMove);
  document.addEventListener('pointerup', onUp);
  document.addEventListener('pointercancel', onUp);

  return () => {
    container.removeEventListener('pointerdown', onDown);
    document.removeEventListener('pointermove', onMove);
    document.removeEventListener('pointerup', onUp);
    document.removeEventListener('pointercancel', onUp);
    for (const h of handles) h.remove();
    handles.length = 0;
  };
}

function applyHandleStyle(el: HTMLDivElement, edge: Edge): void {
  Object.assign(el.style, {
    position: 'absolute',
    zIndex: '10002',
    cursor: CURSORS[edge],
    pointerEvents: 'auto',
    touchAction: 'none',
  });

  const S = `${HANDLE_SIZE}px`;
  const FULL = '100%';

  switch (edge) {
    case 'n':
      Object.assign(el.style, { top: '0', left: S, right: S, height: S });
      break;
    case 's':
      Object.assign(el.style, { bottom: '0', left: S, right: S, height: S });
      break;
    case 'e':
      Object.assign(el.style, { right: '0', top: S, bottom: S, width: S });
      break;
    case 'w':
      Object.assign(el.style, { left: '0', top: S, bottom: S, width: S });
      break;
    case 'ne':
      Object.assign(el.style, { top: '0', right: '0', width: `${HANDLE_SIZE * 2}px`, height: `${HANDLE_SIZE * 2}px` });
      break;
    case 'nw':
      Object.assign(el.style, { top: '0', left: '0', width: `${HANDLE_SIZE * 2}px`, height: `${HANDLE_SIZE * 2}px` });
      break;
    case 'se':
      Object.assign(el.style, { bottom: '0', right: '0', width: `${HANDLE_SIZE * 2}px`, height: `${HANDLE_SIZE * 2}px` });
      break;
    case 'sw':
      Object.assign(el.style, { bottom: '0', left: '0', width: `${HANDLE_SIZE * 2}px`, height: `${HANDLE_SIZE * 2}px` });
      break;
  }
}
