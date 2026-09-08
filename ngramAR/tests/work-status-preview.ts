import { setupWorkStatus } from '../packages/surface-webxr/src/work-status';
const host = document.getElementById('work-status')!;
const activity = setupWorkStatus(host);
document.querySelector('#shell-name')!.textContent = 'Activity preview';
const controls = document.createElement('div');
controls.style.cssText = 'position:fixed;top:65px;left:300px;z-index:20000;display:flex;gap:8px;flex-wrap:wrap;max-width:calc(100vw - 330px)';
document.body.append(controls);
let sequence = 0;
function button(name, action) {
  const button = document.createElement('button'); button.textContent = name;
  button.style.cssText = 'padding:8px;border:1px solid var(--border-focus);border-radius:8px;background:var(--surface);color:var(--text);font:12px var(--font-body)';
  button.onclick = action; controls.append(button);
}
const update = (data = {}) => activity.update({ runId: 'preview', instanceId: 'first', sequence: ++sequence,
  timestamp: Date.now(), scope: 'turn', status: 'running', stage: 'model_wait', elapsedMs: 540000,
  stageElapsedMs: 540000, idleMs: 540000, attempt: 1, maxAttempts: 3, timeoutMs: 1800000, ...data });
button('Normal chat', () => { activity.clear(); update(); });
button('Quick tool', () => { activity.clear(); update({ stage: 'tool_running', tool: 'run_command', elapsedMs: 2000, stageElapsedMs: 200 }); });
button('Model wait', () => update());
button('Retry', () => update({ stage: 'retry_wait', attempt: 2, retryDelayMs: 8000, stageElapsedMs: 0, idleMs: 0 }));
button('Blender', () => update({ stage: 'rendering', project: 'orion', revision: 3, attempt: undefined, timeoutMs: undefined, stageElapsedMs: 32000, idleMs: 10000 }));
button('Goal', () => update({ runId: 'goal', scope: 'code_task', phase: 4, mode: 'verify' }));
button('Disconnect', () => activity.setConnected(false));
button('Reconnect', () => { activity.setConnected(true); update(); });
button('Complete', () => update({ status: 'complete' }));
button('Complete all', () => {
  for (const record of [...activity.store.records.values()]) activity.update({ ...record.event, status: 'complete', sequence: ++sequence, timestamp: Date.now() });
});
function caption(speaker, text) {
  document.getElementById('subtitle-speaker')!.textContent = speaker + ':';
  document.getElementById('subtitle-text')!.textContent = text;
  document.getElementById('subtitle')!.classList.add('visible');
}
button('Your message', () => caption('You', 'Keep improving the model while I test its grip.'));
button('Agent reply', () => caption('Rook', 'The guard is finished. I am checking the new material and grip in the next render, then comparing them against the previous revision.'));
button('Hide caption', () => document.getElementById('subtitle')!.classList.remove('visible'));
button('Narrow layout', () => {
  const bar = document.querySelector('.command-bar') as HTMLElement;
  bar.style.width = bar.style.width ? '' : '360px';
});
button('Theme', () => { const html = document.documentElement; html.dataset.theme = html.dataset.theme === 'light' ? 'dark' : html.dataset.theme === 'dark' ? 'periwinkle' : 'light'; });
update();
setInterval(() => {
  const item = activity.store.records.get('preview');
  if (item?.event.status === 'running' && activity.store.connected) activity.update({ ...item.event, heartbeat: true,
    sequence: ++sequence, timestamp: Date.now(), elapsedMs: item.event.elapsedMs + 10000,
    stageElapsedMs: item.event.stageElapsedMs + 10000, idleMs: item.event.idleMs + 10000 });
}, 10000);
