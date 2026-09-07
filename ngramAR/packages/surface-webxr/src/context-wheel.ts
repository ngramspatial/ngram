import type { ContextDisplayState } from './context-status.js';
import { contextFraction, contextPricing, PRICING_SOURCE } from './context-pricing.js';

export function setupContextWheel(requestStatus: () => void) {
  const button = document.getElementById('context-wheel') as HTMLButtonElement;
  const ring = button.querySelector<SVGCircleElement>('.context-wheel-fill')!;
  const tooltip = document.getElementById('context-tooltip')!;
  let state: ContextDisplayState = {};
  let connected = false;
  let hovered = false;
  let pinned = false;
  let dismissTimer: ReturnType<typeof setTimeout>;
  const text = (id: string, value: string) => { document.getElementById(id)!.textContent = value; };
  const money = (value: number) => `$${value.toLocaleString('en-US', { maximumFractionDigits: 2 })}`;
  const tokens = (value: number) => Math.round(value).toLocaleString('en-US');

  function render() {
    const fraction = connected ? contextFraction(state.estimatedTokens, state.inputBudgetTokens) : null;
    const percent = fraction === null ? null : Math.round(fraction * 100);
    ring.style.strokeDasharray = `${Math.min(100, Math.max(0, (fraction ?? 0) * 100))} 100`;
    button.dataset.unknown = String(fraction === null);
    button.dataset.compacting = String(!!state.compacting);
    button.dataset.level = fraction !== null && fraction >= 0.95 ? 'full' : fraction !== null && fraction >= 0.8 ? 'high' : 'normal';
    button.setAttribute('aria-label', percent === null ? 'Context usage and pricing' : `Context ${percent}% used. Show usage and pricing`);
    text('context-wheel-percent', percent === null ? '—' : `${percent}%`);
    text('context-wheel-tokens', fraction === null ? (connected ? 'Waiting for context usage' : 'Connect to see context usage')
      : `≈${tokens(state.estimatedTokens!)} / ${tokens(state.inputBudgetTokens!)} tokens`);
    text('context-wheel-model', state.model || 'Model not reported');
    text('context-wheel-detail', state.compacting ? 'Compacting older context…'
      : state.source === 'history' ? 'Stored history estimate. Full prompt usage updates on the next model request.'
      : 'Estimated prompt size against the working context budget.');
    const pricing = contextPricing(state.provider, state.model, fraction === null ? undefined : state.estimatedTokens);
    document.getElementById('context-price-rates')!.hidden = !pricing;
    document.getElementById('context-price-missing')!.hidden = !!pricing;
    if (pricing) {
      text('context-price-input', `${money(pricing.input)} / 1M`);
      text('context-price-cached', `${money(pricing.cached)} / 1M`);
      text('context-price-output', `${money(pricing.output)} / 1M`);
      text('context-price-estimate', pricing.estimatedInputCost === null ? '—' : `≈$${pricing.estimatedInputCost.toFixed(4)}`);
      text('context-price-basis', `Standard USD rates${pricing.longContext ? ' · long context' : ''} · Sep 7, 2026`);
    } else {
      text('context-price-missing', state.provider === 'local' || state.provider === 'ollama'
        ? 'Local compute; no provider token rate reported.' : 'Rates unavailable for this provider or model.');
    }
  }
  function position() {
    const rect = button.getBoundingClientRect();
    tooltip.style.left = `${Math.max(12, Math.min(rect.right - tooltip.offsetWidth, window.innerWidth - tooltip.offsetWidth - 12))}px`;
    tooltip.style.top = `${Math.max(12, rect.top - tooltip.offsetHeight - 12)}px`;
  }
  function open() {
    clearTimeout(dismissTimer);
    tooltip.hidden = false;
    requestStatus();
    render(); position();
  }
  function close() { tooltip.hidden = true; pinned = false; }
  function leave() {
    dismissTimer = setTimeout(() => {
      if (!hovered && !pinned && document.activeElement !== button && !tooltip.contains(document.activeElement)) close();
    }, 120);
  }
  button.addEventListener('pointerenter', () => { hovered = true; open(); });
  button.addEventListener('pointerleave', () => { hovered = false; leave(); });
  button.addEventListener('focus', open);
  button.addEventListener('blur', leave);
  button.addEventListener('click', () => { pinned = !pinned; if (pinned) open(); else close(); });
  tooltip.addEventListener('pointerenter', () => { hovered = true; clearTimeout(dismissTimer); });
  tooltip.addEventListener('pointerleave', () => { hovered = false; leave(); });
  tooltip.addEventListener('focusout', leave);
  document.addEventListener('pointerdown', event => {
    if (!button.contains(event.target as Node) && !tooltip.contains(event.target as Node)) close();
  });
  document.addEventListener('keydown', event => {
    if (event.key === 'Escape' && !tooltip.hidden) { event.preventDefault(); event.stopImmediatePropagation(); close(); }
  }, true);
  window.addEventListener('resize', () => { if (!tooltip.hidden) position(); });
  document.getElementById('context-price-source')!.setAttribute('href', PRICING_SOURCE);
  render();
  return {
    update(next: ContextDisplayState) { state = next; render(); if (!tooltip.hidden) position(); },
    setConnected(value: boolean) { connected = value; if (!value) state = {}; render(); },
  };
}
