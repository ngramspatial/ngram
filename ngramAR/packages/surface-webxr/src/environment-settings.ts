// @ts-nocheck
/** Human controls use the same confirmed environment API as agents. */
export function setupEnvironmentSettings(manager) {
  const preset = document.getElementById('settings-env-preset');
  if (!preset || !manager.sceneEnvironment) return;
  const section = document.createElement('div'); section.className = 'settings-section';
  section.innerHTML = '<h3>Sky & lighting</h3><p class="settings-description">Shape the atmosphere, use a panoramic sky, or keep your surroundings visible.</p>';
  preset.closest('.settings-row').after(section);
  const status = document.createElement('p'); status.className = 'settings-caption'; status.setAttribute('role', 'status');
  const controls = new Map();
  const config = () => manager.sceneEnvironment.config;
  async function apply(patch) {
    try { await manager.handle('configure', patch); }
    catch (error) { status.textContent = error.message; }
    sync();
  }
  function control(label, type, read, change, options = {}) {
    const row = document.createElement('div'); row.className = 'settings-row';
    const caption = document.createElement('label'); caption.className = 'settings-row-label'; caption.textContent = label;
    const input = document.createElement('input'); input.type = type;
    input.id = `environment-${controls.size}`; caption.htmlFor = input.id;
    input.className = type === 'checkbox' ? '' : 'modal-input settings-select';
    input.style.accentColor = '#6E7DFF';
    Object.assign(input, options);
    input.onchange = () => change(type === 'checkbox' ? input.checked : Number(input.value));
    row.append(caption, input); section.append(row); controls.set(input, read); return input;
  }
  const skyPatch = patch => ({ sky: { ...(config().sky ?? { type: 'atmosphere' }), ...patch } });
  control('Show sky', 'checkbox', () => config().sky?.visible ?? false, visible => apply(skyPatch({ visible })));
  control('Sky reflections', 'checkbox', () => config().sky?.lighting ?? false, lighting => apply(skyPatch({ lighting })));
  control('Immersive sky in XR', 'checkbox', () => config().sky?.immersive ?? false, immersive => apply(skyPatch({ immersive })));
  control('Show ground grid', 'checkbox', () => config().ground ?? true, ground => apply({ ground }));
  control('Exposure', 'range', () => config().exposure ?? manager.sceneEnvironment.renderer.toneMappingExposure, exposure => apply({ exposure }), { min: .1, max: 3, step: .05 });
  control('Sky rotation', 'range', () => config().sky?.rotation ?? 0, rotation => apply(skyPatch({ rotation })), { min: -3.14, max: 3.14, step: .01 });
  control('Sky brightness', 'range', () => config().sky?.backgroundIntensity ?? 1, backgroundIntensity => apply(skyPatch({ backgroundIntensity })), { min: 0, max: 3, step: .05 });
  control('Reflection strength', 'range', () => config().sky?.intensity ?? 1, intensity => apply(skyPatch({ intensity })), { min: 0, max: 3, step: .05 });
  const elevation = control('Sun elevation', 'range', () => config().sky?.elevation ?? 25, elevation => apply(skyPatch({ elevation })), { min: -10, max: 90, step: 1 });
  function button(label, fn) {
    const button = document.createElement('button'); button.type = 'button'; button.className = 'modal-btn settings-btn-sm'; button.textContent = label;
    button.onclick = async () => { button.disabled = true; try { await fn(); } finally { button.disabled = false; } };
    section.append(button);
  }
  button('Atmospheric sky', () => apply({ sky: { type: 'atmosphere' } }));
  const urlLabel = document.createElement('label'); urlLabel.className = 'modal-label'; urlLabel.textContent = 'Panorama URL';
  const url = document.createElement('input'); url.className = 'modal-input'; url.type = 'text'; url.placeholder = '2:1 panorama, HDR or EXR';
  urlLabel.append(url); section.append(urlLabel);
  button('Apply panorama', () => apply({ sky: { type: 'panorama', url: url.value.trim() } }));
  button('Reset sky & lighting', async () => { await manager.handle('clear'); sync(); });
  section.append(status);
  function sync() {
    for (const [input, read] of controls) if (document.activeElement !== input) {
      if (input.type === 'checkbox') input.checked = read(); else input.value = String(read());
    }
    elevation.disabled = config().sky?.type !== 'atmosphere';
    const state = manager.sceneEnvironment.inspect();
    status.textContent = state.error ?? (state.status === 'loading' ? 'Loading sky…' : config().sky ? 'Sky ready. Changes are saved in this workspace.' : 'Using your scene preset.');
    if (document.activeElement !== url && config().sky?.url) url.value = config().sky.url;
  }
  const changed = manager.sceneEnvironment.onChange;
  manager.sceneEnvironment.onChange = () => { changed?.(); sync(); };
  sync();
}
