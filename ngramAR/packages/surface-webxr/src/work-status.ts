/** Observed stages, separate from speech and animation. No inference or polling. */
export interface WorkStatus {
  runId: string; instanceId?: string; scope: 'turn' | 'code_task'; status: string; stage: string;
  sequence: number; timestamp: number; elapsedMs: number; stageElapsedMs: number; idleMs: number;
  heartbeat?: boolean; attempt?: number; maxAttempts?: number; timeoutMs?: number;
  retryDelayMs?: number; tool?: string; operation?: string; project?: string; revision?: number;
  phase?: number; mode?: string;
  summary?: string; reason?: string; nextSteps?: string;
}
type Record = { event: WorkStatus; received: number; history: string[];
  substantial: boolean; authoringSteps: number; authoringStartedMs?: number };
const AUTHORING_TOOLS = new Set(['execute_python', 'execute_javascript', 'apply_patch', 'write_file', 'append_file']);
const EXECUTION_TOOLS = new Set([...AUTHORING_TOOLS, 'run_command', 'run_background', 'check_process']);
function isAuthoring(event: WorkStatus): boolean {
  return event.stage === 'rendering' || (event.stage === 'tool_running' && (
    AUTHORING_TOOLS.has(event.tool ?? '') ||
    (event.tool === 'ar_blender' && ['execute', 'publish', 'render'].includes(event.operation ?? ''))));
}
function isExecution(event: WorkStatus): boolean {
  return isAuthoring(event) || (event.stage === 'tool_running' && EXECUTION_TOOLS.has(event.tool ?? ''));
}
export function duration(ms: number): string {
  const seconds = Math.max(0, Math.floor(ms / 1000));
  if (seconds < 60) return `${seconds}s`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ${String(seconds % 60).padStart(2, '0')}s`;
  return `${Math.floor(seconds / 3600)}h ${Math.floor(seconds % 3600 / 60)}m`;
}
export function stageLabel(event: WorkStatus): string {
  if (event.status !== 'running') return ({ complete: 'Finished', failed: 'Failed', cancelled: 'Stopped',
    paused: 'Paused', blocked: 'Blocked' } as { [key: string]: string })[event.status] ?? 'Paused';
  if (event.stage === 'model_wait') return 'Waiting for model';
  if (event.stage === 'retry_wait') return 'Retrying model request';
  if (event.stage === 'rendering') return 'Rendering in Blender';
  if (event.stage === 'tool_running') return event.tool === 'ar_blender'
    ? ({ execute: 'Editing in Blender', publish: 'Publishing from Blender', status: 'Inspecting Blender',
        show: 'Showing Blender preview' } as { [key: string]: string })[event.operation ?? ''] ?? 'Running Blender'
    : `Running ${event.tool || 'tool'}`;
  return event.stage === 'processing' ? 'Processing result' : 'Preparing';
}

export class WorkStatusStore {
  records = new Map<string, Record>();
  connected = true;
  update(event: WorkStatus, now = Date.now()): boolean {
    if (!event || typeof event.runId !== 'string' || !['turn', 'code_task'].includes(event.scope)
        || !Number.isFinite(event.sequence) || !Number.isFinite(event.elapsedMs)) return false;
    const old = this.records.get(event.runId);
    if (old?.event.status === 'cancelled' && event.status === 'running'
        && event.instanceId === old.event.instanceId) return false;
    if (old && (event.timestamp < old.event.timestamp ||
        (event.instanceId === old.event.instanceId && event.sequence <= old.event.sequence))) return false;
    const previous = old?.event.instanceId === event.instanceId ? old : undefined;
    const history = previous?.history ?? [];
    const label = stageLabel(event);
    if (!event.heartbeat && (!old || label !== stageLabel(old.event)))
      history.push(`${duration(event.elapsedMs)} · ${label}`);
    const newAuthoringStep = isAuthoring(event) && !event.heartbeat && (!previous ||
      previous.event.stage !== event.stage || previous.event.tool !== event.tool || previous.event.operation !== event.operation);
    const finishedLongExecution = previous && isExecution(previous.event)
      && (previous.event.stage !== event.stage || previous.event.tool !== event.tool)
      && event.elapsedMs - (previous.event.elapsedMs - previous.event.stageElapsedMs) >= 15000;
    this.records.set(event.runId, { event, received: now, history: history.slice(-8),
      substantial: (previous?.substantial ?? false) || !!finishedLongExecution,
      authoringSteps: (previous?.authoringSteps ?? 0) + Number(newAuthoringStep),
      authoringStartedMs: previous?.authoringStartedMs ?? (newAuthoringStep ? event.elapsedMs : undefined) });
    // Retain a small terminal-state history while never hiding concurrent active goals.
    for (const [id, record] of this.records) {
      if (this.records.size <= 12) break;
      if (record.event.status !== 'running') this.records.delete(id);
    }
    return true;
  }
  stopTurns(now = Date.now()) {
    for (const record of this.records.values()) {
      if (record.event.scope !== 'turn' || record.event.status !== 'running') continue;
      record.event = { ...record.event, status: 'cancelled', elapsedMs: record.event.elapsedMs + now - record.received };
      record.received = now;
    }
  }
  views(now = Date.now()) {
    return [...this.records.values()].filter(record => {
      const { event, received } = record;
      // A slow chat response, retry or lookup is not evidence of a coding job.
      // Once real work qualifies, keep its subsequent model/tool phases visible.
      if (event.status !== 'running') return event.scope === 'code_task' && now - received < 120000;
      const liveAge = this.connected && now - received <= 35000 ? Math.max(0, now - received) : 0;
      if (event.scope === 'code_task'
          || (isExecution(event) && event.stageElapsedMs + liveAge >= 15000)
          || (record.authoringSteps >= 2 && event.elapsedMs - (record.authoringStartedMs ?? event.elapsedMs) >= 30000))
        record.substantial = true;
      return record.substantial;
    })
      .map(({ event, received, history }) => {
        const active = event.status === 'running';
        const since = Math.max(0, now - received);
        const stale = active && (!this.connected || since > 35000);
        const elapsed = event.elapsedMs + (active ? since : 0);
        const stageElapsed = event.stageElapsedMs + (active ? since : 0);
        const idle = event.idleMs + (active ? since : 0);
        const details = [];
        if (event.scope === 'code_task') details.push(`Coding goal · Phase ${event.phase ?? 1}${event.mode === 'verify' ? ' · Verification' : ''}`);
        if (event.attempt) details.push(`Request attempt ${event.attempt}/${event.maxAttempts ?? 1}`);
        if (event.timeoutMs) details.push(`Request limit ${duration(event.timeoutMs)}`);
        if (event.stage === 'retry_wait') details.push(`Retry in ${duration(Math.max(0, (event.retryDelayMs ?? 0) - stageElapsed))}`);
        if (event.project) details.push(`${event.project}${event.revision ? ` · Revision ${event.revision}` : ''}`);
        if (active) details.push(`Last progress ${duration(idle)} ago`,
          this.connected ? `Worker update ${duration(since)} ago` : 'Connection lost · last known state');
        return { id: event.runId, scope: event.scope, active, stale, label: stageLabel(event),
          elapsed: duration(elapsed), stageElapsed: duration(stageElapsed), details, history,
          summary: event.summary ?? '', reason: event.reason ?? '', nextSteps: event.nextSteps ?? '',
          caution: stale ? 'Live status unavailable' : active && idle >= 60000 ? 'No new progress reported' : '' };
      });
  }
}

export function setupWorkStatus(host: HTMLElement, onLabel?: (label: string, active: boolean) => void) {
  const store = new WorkStatusStore();
  const rows = new Map<string, HTMLElement>();
  const render = () => {
    const views = store.views();
    host.hidden = views.length === 0;
    for (const [id, row] of rows) if (!views.some(v => v.id === id)) { row.remove(); rows.delete(id); }
    for (const view of views) {
      let row = rows.get(view.id);
      if (!row) {
        row = document.createElement('details'); row.className = 'work-status-row';
        const summary = document.createElement('summary');
        const title = document.createElement('span'); title.className = 'work-status-title';
        title.setAttribute('role', 'status'); title.setAttribute('aria-live', 'polite');
        const timer = document.createElement('span'); timer.className = 'work-status-time';
        const body = document.createElement('div'); body.className = 'work-status-detail';
        const progress = document.createElement('div'); progress.className = 'work-status-progress';
        const copy = document.createElement('span'); copy.className = 'work-status-copy';
        copy.append(title, progress); summary.append(copy, timer); row.append(summary, body); host.append(row); rows.set(view.id, row);
      }
      row.dataset.active = String(view.active); row.dataset.stale = String(view.stale);
      const title = row.querySelector('.work-status-title')!;
      const label = `${view.scope === 'code_task' ? 'Coding goal · ' : ''}${view.label}${view.caution ? ` · ${view.caution}` : ''}`;
      if (title.textContent !== label) title.textContent = label;
      row.querySelector('.work-status-time')!.textContent = view.elapsed;
      const progress = row.querySelector('.work-status-progress')! as HTMLElement;
      progress.textContent = view.reason || view.summary;
      progress.hidden = !progress.textContent;
      progress.title = progress.textContent || '';
      row.querySelector('.work-status-detail')!.textContent =
        `${view.summary}\n${view.reason}${view.nextSteps ? `\nNext: ${view.nextSteps}` : ''}\n\n${view.label} · ${view.stageElapsed} in this stage\n${view.details.join('\n')}\n\n${view.history.join('\n')}`;
    }
    const active = views.find(v => v.active && v.scope === 'turn') ?? views.find(v => v.active);
    // Put elapsed first so the narrow headset badge cannot truncate the timer.
    onLabel?.(active ? `${active.elapsed} · ${active.caution || active.label}` : '', !!active && !active.stale);
  };
  const timer = setInterval(render, 1000);
  return { store, update(event: WorkStatus) { store.update(event); render(); },
    setConnected(connected: boolean) { store.connected = connected; render(); },
    stopTurns() { store.stopTurns(); render(); },
    clear() { store.records.clear(); render(); },
    destroy() { clearInterval(timer); host.replaceChildren(); } };
}
