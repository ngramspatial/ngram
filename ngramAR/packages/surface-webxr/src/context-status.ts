export interface ContextDisplayState {
  estimatedTokens?: number;
  inputBudgetTokens?: number;
  notice?: string;
  noticeUntil?: number;
  lastCompactedAt?: number;
  compacting?: boolean;
  model?: string;
  provider?: string;
  source?: string;
}

/** Estimates are deliberately marked; these are not billed API token counts. */
export function updateContextDisplay(state: ContextDisplayState, event: {
  phase: string; estimatedTokens?: number; inputBudgetTokens?: number;
  model?: string; provider?: string; source?: string;
}, now = Date.now()): { state: ContextDisplayState; text: string; title: string } {
  const next = { ...state };
  if ((event.model && state.model && event.model !== state.model)
      || (event.provider && state.provider && event.provider !== state.provider)) next.estimatedTokens = undefined;
  if (event.model) next.model = event.model;
  if (event.provider) next.provider = event.provider;
  if (event.source) next.source = event.source;
  if (Number.isFinite(event.inputBudgetTokens) && event.inputBudgetTokens !== state.inputBudgetTokens
      && !Number.isFinite(event.estimatedTokens)) next.estimatedTokens = undefined;
  if (Number.isFinite(event.estimatedTokens) && event.estimatedTokens! >= 0) next.estimatedTokens = event.estimatedTokens;
  if (Number.isFinite(event.inputBudgetTokens) && event.inputBudgetTokens! > 0) next.inputBudgetTokens = event.inputBudgetTokens;
  if (event.phase === 'compacting') next.compacting = true;
  if (['compacted', 'failed', 'unchanged', 'stopped'].includes(event.phase)) next.compacting = false;
  if (event.phase === 'compacted') next.lastCompactedAt = now;
  const notices: Record<string, string> = {
    compacted: 'Context compacted', failed: 'Compaction failed · history retained',
    unchanged: 'Nothing to compact yet', stopped: 'Compaction stopped',
  };
  if (notices[event.phase]) {
    next.notice = notices[event.phase];
    next.noticeUntil = now + 6000;
  }
  const format = (tokens: number) => tokens >= 1000 ? `${(tokens / 1000).toFixed(1).replace(/\.0$/, '')}K` : String(tokens);
  const usage = next.estimatedTokens !== undefined && next.inputBudgetTokens
    ? `Context ≈${format(next.estimatedTokens)} / ${format(next.inputBudgetTokens)}` : 'Context';
  const text = next.compacting ? 'Compacting context…'
    : (next.noticeUntil ?? 0) > now ? next.notice! : usage;
  const last = next.lastCompactedAt ? ` Last compacted ${new Date(next.lastCompactedAt).toLocaleTimeString()}.` : '';
  return { state: next, text, title: `${usage}. Estimated prompt size, not billing usage.${last} Type /compact to summarize older turns.` };
}
