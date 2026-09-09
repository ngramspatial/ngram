/** One input action: send while idle, stop while work or playback is active. */
export function setupResponseControl(button: HTMLButtonElement, send: () => void, stop: () => void) {
  let state = { agentState: 'idle', playing: false, compacting: false, dictating: false };
  const isActive = () => state.playing || state.compacting
    || ['thinking', 'planning', 'tool_running', 'messaging', 'speaking'].includes(state.agentState);
  function render() {
    const active = isActive() && !state.dictating;
    button.dataset.mode = active ? 'stop' : 'send';
    button.setAttribute('aria-label', active ? 'Stop response' : 'Send');
    button.title = active ? 'Stop response (Esc)' : 'Send message';
  }
  button.addEventListener('click', () => { if (isActive() && !state.dictating) stop(); else send(); });
  render();
  return {
    isActive,
    update(next: Partial<typeof state>) { state = { ...state, ...next }; render(); },
    reset() { state = { ...state, agentState: 'idle', playing: false, compacting: false }; render(); },
  };
}
