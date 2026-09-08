type Creation = { name: string; voice: string; embodiment: string; reuseShell?: string;
  connection?: { bridgeUrl: string; token: string }; brain?: Record<string, string> };

export function initOnboarding(onCreate: (data: Creation) => void) {
  const backdrop = document.getElementById('create-modal')!;
  backdrop.hidden = true;
  backdrop.innerHTML = `
    <section class="onboarding" role="dialog" aria-modal="true" aria-labelledby="onboarding-title" tabindex="-1">
      <aside class="onboarding-atmosphere">
        <div class="onboarding-brand"><span class="onboarding-mark" aria-hidden="true"></span>ngram <span>AR</span></div>
        <div class="onboarding-orbit" aria-hidden="true"><i></i><i></i><i></i><div class="onboarding-seed"><span></span><span></span></div><b></b><b></b></div>
        <div class="onboarding-poem">A little presence.<br>A world of possibility.</div>
        <p>A mind that remembers.<br>A home in the cloud.<br>A place in your world.</p>
        <div class="onboarding-edition">PERSISTENT BY DESIGN <span>✳</span></div>
      </aside>
      <div class="onboarding-workspace">
        <header class="onboarding-top"><span class="onboarding-top-title" id="onboarding-progress">New ngram</span><button type="button" class="modal-close" id="onboarding-close" aria-label="Close setup">×</button></header>
        <nav class="onboarding-steps" aria-label="Setup progress">${['Home', 'Connection', 'Presence', 'Ready'].map((label, i) => `<span data-step="${i}"><b>${i + 1}</b>${label}</span>`).join('')}</nav>
        <form id="onboarding-form" autocomplete="off">
          <div class="onboarding-content">
            <section data-page="0">
              <h1 id="onboarding-title">Make room for your ngram.</h1>
              <p class="onboarding-lead">Give a persistent mind a place in your world. We’ll connect its home, its intelligence, and its presence.</p>
              <div class="onboarding-home"><div><strong>Cloud home</strong><span class="onboarding-badge">Recommended</span></div><p>Railway runs your ngram. Your API key powers its thinking and memory. Your browser brings it into the room.</p>
                <div class="onboarding-topology"><span>Provider API<small>Thinking + embeddings</small></span><b>↔</b><span>Railway<small>Worker + Linux files</small></span><b>↔</b><span>Your world<small>Browser + AR</small></span></div>
              </div>
              <div class="onboarding-benefits"><span>↗ No local models</span><span>◇ Persistent memory</span><span>◎ Your own API key</span></div>
              <p class="onboarding-note">Railway hosting and provider usage are billed by those services.</p>
              <details class="onboarding-details"><summary>Using your own hardware?</summary><p>You can pair a local worker below. Local inference and a protected home GPU remain available through <code>ngram setup --profile local</code> or <code>--profile hybrid</code>.</p></details>
            </section>
            <section data-page="1" hidden>
              <h2>Connect your worker.</h2><p class="onboarding-lead">Pair the worker that holds your ngram’s identity, memories, and files.</p>
              <div id="onboarding-saved" hidden><label for="onboarding-worker">Worker connection</label><select class="modal-select" id="onboarding-worker"><option value="">Connect another worker</option></select><p class="onboarding-note" id="onboarding-reuse-note" hidden>A new body for the same Entity. Its identity, relationships, and memory stay with the selected worker. Create a separate worker for a separate ngram.</p></div>
              <div id="onboarding-new-worker">
                <details class="onboarding-details onboarding-setup-help" open><summary>First ngram? Start here.</summary><p>Run the guided setup once from your ngram checkout. It creates your Entity, configures hosted thinking and embeddings, and pairs a Railway worker with durable storage.</p><div class="onboarding-command"><code>uv run ngram setup --profile cloud</code><button type="button" class="modal-btn" id="onboarding-copy" aria-label="Copy setup command">Copy</button></div><p>Then return here. Setup saves the connection automatically. Use “Refresh connections” below, or paste the worker URL and pairing token from your shell’s local <code>.env</code>.</p><button type="button" class="modal-btn onboarding-text-button" id="onboarding-refresh">Refresh connections ↗</button></details>
                <label for="onboarding-url">Railway worker URL</label><input class="modal-input" id="onboarding-url" type="text" placeholder="https://your-worker.up.railway.app" spellcheck="false" maxlength="2048"><p class="onboarding-note">The public worker domain from setup. Local workers can use ws://127.0.0.1:7878/.</p>
                <label for="onboarding-token">Pairing token</label><input class="modal-input" id="onboarding-token" type="password" placeholder="Paste your worker’s pairing token" autocomplete="new-password" maxlength="8192"><p class="onboarding-note">Connects this app to your worker. This is separate from your provider API key.</p>
                <label for="onboarding-brain-mode">Thinking &amp; memory</label><select class="modal-select" id="onboarding-brain-mode"><option value="existing">Keep worker settings · already configured by setup</option><option value="hosted">Connect a provider API · includes hosted embeddings</option></select>
                <div id="onboarding-brain" hidden>
                  <div class="onboarding-field-row"><div><label for="onboarding-provider">Provider</label><select class="modal-select" id="onboarding-provider"><option value="openai">OpenAI · recommended</option><option value="venice">Venice</option><option value="custom">Compatible API</option></select></div><div><label for="onboarding-model">Chat model</label><input class="modal-input" id="onboarding-model" maxlength="256" placeholder="Provider model ID"></div></div>
                  <div id="onboarding-base-field" hidden><label for="onboarding-base">API base URL</label><input class="modal-input" id="onboarding-base" type="url" placeholder="https://provider.example/v1" maxlength="2048"></div>
                  <label for="onboarding-key">Provider API key</label><input class="modal-input" id="onboarding-key" type="password" autocomplete="new-password" placeholder="Paste your API key" maxlength="8192">
                  <label for="onboarding-embedding">Memory embedding model</label><input class="modal-input" id="onboarding-embedding" value="text-embedding-3-small" maxlength="256"><p class="onboarding-note">Hosted embeddings power semantic recall. The worker verifies the model and vector size. Existing memories keep their original embedding model.</p>
                </div>
              </div>
              <div class="onboarding-trust">◇ Credentials are sent to your app server and stored in its private runtime files. They are never written into the shell or browser storage.</div>
            </section>
            <section data-page="2" hidden>
              <h2>A presence of your own.</h2><p class="onboarding-lead">Choose how your ngram appears and sounds. Its identity and memories come from its worker.</p>
              <label for="create-name">Name this body</label><input class="modal-input" id="create-name" maxlength="60" placeholder="Nova, Echo, Sage…" autocomplete="off"><p class="onboarding-note">A label for this spatial body. It won’t rename the Entity on your worker.</p>
              <fieldset class="onboarding-bodies"><legend>Choose a presence</legend><label><input type="radio" name="embodiment" value="orb" checked><span class="onboarding-body-card"><span class="onboarding-body-orb" aria-hidden="true">✳</span><strong>Orb</strong><small>A little light. A lot of personality.</small></span></label><label><input type="radio" name="embodiment" value="human"><span class="onboarding-body-card"><span class="onboarding-body-human" aria-hidden="true">◉<br>╱│╲<br>╱ ╲</span><strong>Human</strong><small>A full body, ready to move.</small></span></label></fieldset>
              <label for="create-voice">Voice</label><select class="modal-select" id="create-voice"><option value="en-US-JennyNeural">Jenny · English, US</option><option value="en-US-GuyNeural">Guy · English, US</option><option value="en-US-AriaNeural">Aria · English, US</option><option value="en-GB-SoniaNeural">Sonia · English, UK</option><option value="en-GB-RyanNeural">Ryan · English, UK</option></select><p class="onboarding-note">You can change bodies and voices later.</p>
            </section>
            <section data-page="3" hidden>
              <h2>Meet <span id="onboarding-review-name">your ngram</span>.</h2><p class="onboarding-lead">We’ll check the worker connection before bringing this presence into your space.</p>
              <dl class="onboarding-review"><div><dt>Home</dt><dd id="onboarding-review-home"></dd></div><div><dt>Intelligence</dt><dd id="onboarding-review-brain"></dd></div><div><dt>Memory</dt><dd id="onboarding-review-memory"></dd></div><div><dt>Presence</dt><dd id="onboarding-review-body"></dd></div></dl>
              <div class="onboarding-memory"><span>◇</span><div><strong>More than a conversation.</strong><p>Semantic recall, relationship memory, knowledge, and a journal live with the Entity. The cloud setup keeps its database and Linux workspace on durable storage.</p></div></div>
              <p class="onboarding-note">Connecting a new provider checks its model catalog and sends a small memory readiness probe. Provider changes apply to every surface using this worker.</p>
            </section>
            <section data-page="4" hidden><div class="onboarding-arrival" aria-hidden="true">✳</div><h2>Your world, a little more alive.</h2><p class="onboarding-lead" id="onboarding-success"></p><div class="onboarding-memory"><span>↗</span><div><strong>Start with a hello.</strong><p>Talk, explore an idea, or ask your ngram to make something in your space.</p></div></div></section>
            <div class="onboarding-error" id="onboarding-error" role="alert" hidden></div>
            <div class="onboarding-busy" id="onboarding-busy" role="status" hidden><span></span>Pairing your worker and checking its configuration…</div>
          </div>
          <footer class="onboarding-footer"><button type="button" class="modal-btn onboarding-back" id="onboarding-back">Back</button><span id="onboarding-footer-note">At your own pace</span><button type="submit" class="modal-btn modal-btn-primary onboarding-primary" id="create-modal-submit">Let’s begin <span>↗</span></button></footer>
        </form>
      </div>
    </section>`;
  const el = <T extends HTMLElement = HTMLElement>(id: string) => document.getElementById(id) as T;
  const input = (id: string) => el<HTMLInputElement>(id).value.trim();
  let page = 0;
  let busy = false;
  let previousFocus: HTMLElement | null = null;
  let completed: { slug: string } | null = null;
  const form = el<HTMLFormElement>('onboarding-form');
  const button = el<HTMLButtonElement>('create-modal-submit');
  const worker = el<HTMLSelectElement>('onboarding-worker');
  function error(message = '') { el('onboarding-error').textContent = message; el('onboarding-error').hidden = !message; }
  function updateConnection() {
    el('onboarding-new-worker').hidden = Boolean(worker.value);
    el('onboarding-reuse-note').hidden = !worker.value;
    el('onboarding-brain').hidden = input('onboarding-brain-mode') !== 'hosted';
  }
  async function refresh() {
    try {
      const response = await fetch('/api/onboarding', { cache: 'no-store' });
      if (!response.ok) throw Error();
      const data = await response.json();
      worker.replaceChildren(new Option('Connect another worker', ''));
      for (const connection of data.connections) worker.add(new Option(connection.name, connection.slug));
      if (data.connections.length) worker.value = data.connections[0].slug;
      el('onboarding-saved').hidden = !data.connections.length;
      if (!input('onboarding-model')) el<HTMLInputElement>('onboarding-model').value = data.defaults.model;
      const human = backdrop.querySelector<HTMLInputElement>('input[value="human"]')!;
      human.disabled = data.humanAvailable === false;
      human.closest('label')!.title = human.disabled ? 'Install the example human model assets to use this body.' : '';
      if (human.disabled) (human.nextElementSibling as HTMLElement).style.opacity = '0.45';
      updateConnection();
      return data;
    } catch { error('Could not load worker connections. Check the app server and retry.'); return null; }
  }
  function show(next: number) {
    page = next; error();
    backdrop.querySelectorAll<HTMLElement>('[data-page]').forEach(section => { section.hidden = Number(section.dataset.page) !== page; });
    backdrop.querySelectorAll<HTMLElement>('[data-step]').forEach(step => { step.classList.toggle('active', Number(step.dataset.step) === Math.min(page, 3)); step.setAttribute('aria-current', Number(step.dataset.step) === Math.min(page, 3) ? 'step' : 'false'); });
    el('onboarding-back').hidden = page === 0 || page === 4;
    el('onboarding-progress').textContent = page === 4 ? 'Welcome home' : 'New ngram';
    el('onboarding-footer-note').textContent = page === 0 ? 'At your own pace' : page === 4 ? 'Connected' : `Step ${page + 1} of 4`;
    button.innerHTML = ['Let’s begin <span>↗</span>', 'Continue <span>→</span>', 'Make it yours <span>→</span>', 'Connect & enter <span>↗</span>', 'Enter your world <span>↗</span>'][page];
    backdrop.querySelector('.onboarding-content')!.scrollTop = 0;
    const heading = backdrop.querySelector<HTMLElement>(`[data-page="${page}"] h1, [data-page="${page}"] h2`);
    heading?.setAttribute('tabindex', '-1'); heading?.focus();
  }
  async function open() {
    previousFocus = document.activeElement as HTMLElement;
    completed = null; busy = false; form.reset(); button.disabled = false;
    backdrop.hidden = false; backdrop.classList.add('open'); show(0); await refresh();
  }
  function close() {
    if (busy) return;
    backdrop.classList.remove('open');
    backdrop.hidden = true;
    el<HTMLInputElement>('onboarding-key').value = '';
    el<HTMLInputElement>('onboarding-token').value = '';
    previousFocus?.focus();
  }
  el('create-agent-btn')?.addEventListener('click', open);
  el('onboarding-close').addEventListener('click', close);
  el('onboarding-back').addEventListener('click', () => { if (!busy) show(page - 1); });
  worker.addEventListener('change', updateConnection);
  el('onboarding-brain-mode').addEventListener('change', updateConnection);
  el('onboarding-refresh').addEventListener('click', async () => { await refresh(); });
  el('onboarding-provider').addEventListener('change', () => {
    const provider = input('onboarding-provider');
    el('onboarding-base-field').hidden = provider !== 'custom';
    el<HTMLInputElement>('onboarding-model').value = provider === 'venice' ? 'qwen3-235b-a22b-instruct-2507' : '';
    el<HTMLInputElement>('onboarding-embedding').value = provider === 'openai' ? 'text-embedding-3-small' : provider === 'venice' ? 'text-embedding-bge-m3' : '';
    el<HTMLInputElement>('onboarding-key').value = '';
    if (provider === 'openai') void refresh();
  });
  el('onboarding-copy').addEventListener('click', async () => {
    try { await navigator.clipboard.writeText('uv run ngram setup --profile cloud'); el('onboarding-copy').textContent = 'Copied'; }
    catch { error('Select and copy the setup command above.'); }
  });
  backdrop.addEventListener('keydown', event => {
    if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); close(); }
    if (event.key !== 'Tab') return;
    const focusable = [...backdrop.querySelectorAll<HTMLElement>('button, input, select, summary, [tabindex="0"]')].filter(node => node.getClientRects().length && !(node as HTMLButtonElement).disabled);
    const first = focusable[0], last = focusable[focusable.length - 1];
    if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus(); }
    else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus(); }
  });
  form.addEventListener('submit', event => {
    event.preventDefault(); if (busy) return;
    if (page === 4) { const slug = completed?.slug; close(); if (slug) window.dispatchEvent(new CustomEvent('ngram:enter-shell', { detail: slug })); return; }
    if (page === 1 && !worker.value) {
      if (!input('onboarding-url')) { error('Add your worker URL, or run the guided cloud setup above.'); el('onboarding-url').focus(); return; }
      if (!input('onboarding-token') && !/^(ws|http):\/\/(127\.0\.0\.1|localhost|\[::1\])[:/]/.test(input('onboarding-url'))) { error('Paste the pairing token from worker setup.'); el('onboarding-token').focus(); return; }
      if (input('onboarding-brain-mode') === 'hosted' && (!input('onboarding-key') || !input('onboarding-model') || !input('onboarding-embedding'))) { error('Add your API key, chat model, and memory embedding model.'); return; }
    }
    if (page === 2) {
      const name = input('create-name');
      if (!name || !/[a-z0-9]/i.test(name) || /[<>\x00-\x1f]/.test(name)) { error('Give this body a name with at least one letter or number from a–z or 0–9.'); el('create-name').focus(); return; }
      el('onboarding-review-name').textContent = name;
      el('onboarding-review-home').textContent = worker.value ? worker.selectedOptions[0].text : input('onboarding-url');
      const hosted = !worker.value && input('onboarding-brain-mode') === 'hosted';
      el('onboarding-review-brain').textContent = hosted ? `${input('onboarding-provider')} / ${input('onboarding-model')}` : 'Worker’s current provider and model';
      el('onboarding-review-memory').textContent = hosted ? `${input('onboarding-embedding')} · hosted embeddings` : 'Keep existing embeddings and memories';
      el('onboarding-review-body').textContent = `${(new FormData(form).get('embodiment') || 'orb')} / ${el<HTMLSelectElement>('create-voice').selectedOptions[0].text}`;
    }
    if (page < 3) { show(page + 1); return; }
    const data: Creation = { name: input('create-name'), voice: input('create-voice'), embodiment: String(new FormData(form).get('embodiment') || 'orb') };
    if (worker.value) data.reuseShell = worker.value;
    else {
      data.connection = { bridgeUrl: input('onboarding-url'), token: input('onboarding-token') };
      if (input('onboarding-brain-mode') === 'hosted') data.brain = { mode: 'frontier', provider: input('onboarding-provider'), model: input('onboarding-model'), apiKey: input('onboarding-key'), baseUrl: input('onboarding-provider') === 'custom' ? input('onboarding-base') : '', embeddingMode: 'provider', embeddingModel: input('onboarding-embedding') };
    }
    busy = true; button.disabled = true; el<HTMLButtonElement>('onboarding-back').disabled = true;
    el<HTMLButtonElement>('onboarding-close').disabled = true; el('onboarding-busy').hidden = false;
    button.textContent = 'Connecting…'; error(); onCreate(data);
  });
  (window as any).__ngramArResetCreateModal = (result?: any, failure?: string) => {
    busy = false; button.disabled = false; el<HTMLButtonElement>('onboarding-back').disabled = false;
    el<HTMLButtonElement>('onboarding-close').disabled = false; el('onboarding-busy').hidden = true;
    if (failure || !result) { show(3); error(failure || 'Could not complete setup. Please retry.'); return; }
    completed = result;
    const status = result.status || {};
    el('onboarding-success').textContent = `${result.name} is paired with ${status.entityName || 'your persistent Entity'}. ${status.embeddingModel ? `Memory uses ${status.embeddingModel}${status.embeddingDimensions ? ` (${status.embeddingDimensions} dimensions)` : ''}.` : 'Your worker’s existing memory configuration is preserved.'}${status.volumeMounted === false ? ' A persistent Railway volume was not confirmed; check storage before redeploying the worker.' : ''}`;
    el<HTMLInputElement>('onboarding-key').value = ''; el<HTMLInputElement>('onboarding-token').value = ''; show(4);
  };
  // Shipped example bodies do not count as a configured first run.
  void refresh().then(data => { if (data?.needsSetup && !backdrop.classList.contains('open')) void open(); });
}
