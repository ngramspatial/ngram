// @ts-nocheck
/** Native view-sharing control. This shares rendered creations, never webcam pixels. */
export function attachVisionControls({ onChange, onShare }) {
  let enabled = localStorage.getItem('ngram_ar:vision') === 'true';
  const container = document.querySelector('.topbar-actions');
  const button = document.createElement('button');
  button.id = 'vision-toggle'; button.className = 'topbar-btn'; button.type = 'button';
  button.innerHTML = '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" style="fill:none" stroke="currentColor" stroke-width="1.7" aria-hidden="true"><path d="M14.5 4h-5L7 7H4a2 2 0 0 0-2 2v10a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2V9a2 2 0 0 0-2-2h-3z"/><circle cx="12" cy="13" r="4"/></svg>';
  container?.prepend(button);
  const share = document.createElement('button'); share.className = 'topbar-btn'; share.type = 'button'; share.id = 'share-view';
  share.textContent = 'Share view'; share.style.width = 'auto'; share.style.paddingInline = '10px';
  share.title = 'Send one current virtual view to your agent';
  button.after(share);
  const refresh = () => {
    button.setAttribute('aria-pressed', String(enabled));
    button.setAttribute('aria-label', enabled ? 'Disable agent view sharing' : 'Enable agent view sharing');
    button.title = enabled ? 'Agent vision on · virtual scene only' : 'Agent vision off · click to allow Spatial inspection';
    button.style.background = enabled ? '#6E7DFF' : ''; button.style.color = enabled ? '#FFFFFF' : '';
    button.classList.toggle('active', enabled);
  };
  const toggle = () => {
    enabled = !enabled; localStorage.setItem('ngram_ar:vision', String(enabled)); refresh(); onChange(enabled);
    return enabled;
  };
  button.addEventListener('click', toggle);
  share.addEventListener('click', async () => {
    share.disabled = true;
    try { await onShare(); } finally { share.disabled = false; }
  });
  const sync = event => {
    if (event.key !== 'ngram_ar:vision') return;
    enabled = event.newValue === 'true'; refresh(); onChange(enabled);
  };
  window.addEventListener('storage', sync);
  refresh();
  return { enabled: () => enabled, toggle, dispose: () => { window.removeEventListener('storage', sync); button.remove(); share.remove(); } };
}
