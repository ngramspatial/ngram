// @ts-nocheck
// Isolated production renderer/settings review. No harness or model is connected.
import * as THREE from 'three';
import { createScene } from '../packages/surface-webxr/src/scene-setup.js';
import { EnvironmentManager } from '../packages/surface-webxr/src/environment-manager.js';
import { setupEnvironmentSettings } from '../packages/surface-webxr/src/environment-settings.js';
await document.fonts.ready;
const { scene, camera, renderer, controls, lights, clock, updateThemeBackground } = createScene();
const manager = new EnvironmentManager(); manager.attach(scene, renderer, lights); setupEnvironmentSettings(manager);
document.querySelector('#status-text').textContent = 'Sky review · no model connected';
document.querySelector('.topbar-actions').append(document.getElementById('settings-btn'));
for (const selector of ['.sidebar','.command-bar','#sidebar-toggle','#ar-button']) document.querySelector(selector)?.remove();
document.querySelector('.viewport-container').style.left = '0';
camera.position.set(0,1.3,4); controls.target.set(0,.9,0); controls.update();
const metal = new THREE.Mesh(new THREE.SphereGeometry(.45,48,32), new THREE.MeshStandardMaterial({ color: '#eeeeee', metalness: 1, roughness: .12 })); metal.position.set(-.7,1,0); scene.add(metal);
const clay = new THREE.Mesh(new THREE.TorusKnotGeometry(.3,.1,120,16), new THREE.MeshStandardMaterial({ color: '#6e7dff', roughness: .3, metalness: .15 })); clay.position.set(.65,1,0); scene.add(clay);
const output = document.createElement('pre'); output.id = 'environment-review'; output.setAttribute('role','status');
Object.assign(output.style, { position:'fixed', bottom:'12px', left:'16px', padding:'8px', background:'white', color:'#171717', fontSize:'11px', maxWidth:'700px', maxHeight:'240px', overflow:'auto' }); document.body.append(output);
function show() { output.textContent = JSON.stringify(manager.sceneEnvironment.inspect(), null, 2); }
function button(label, fn) {
  const button = document.createElement('button'); button.className = 'modal-btn'; button.textContent = label;
  button.onclick = async () => { button.disabled = true; try { await fn(); show(); } catch (error) { output.textContent = error.message; } finally { button.disabled = false; } };
  document.querySelector('.topbar-actions').append(button);
}
button('Sunset', () => manager.handle('configure', { sky:{elevation:4,azimuth:180,turbidity:7}, exposure:.65, ground:false }));
button('Panorama', () => manager.handle('configure', { sky:{type:'panorama',url:'/test-sky.png'}, exposure:1 }));
button('HDR panorama', () => manager.handle('configure', { sky:{type:'panorama',url:'/test-sky.hdr'}, exposure:1 }));
button('Failure check', async () => {
  const before = scene.background;
  try { await manager.handle('configure',{sky:{type:'panorama',url:'/missing.png'}}); throw Error('Expected rejection'); }
  catch (error) { if (scene.background !== before) throw Error('Failed sky replaced the working sky'); }
});
button('Save & restore', async () => {
  const state = manager.getSavedState(); manager.clearEnvironment(); await manager.loadSavedState(state);
});
button('Review theme', () => { document.documentElement.setAttribute('data-theme', document.documentElement.getAttribute('data-theme') === 'dark' ? 'light' : 'dark'); updateThemeBackground(); manager.reapplyState(); });
const modal = document.getElementById('settings-modal');
document.getElementById('settings-btn').onclick = () => {
  modal.hidden = false; modal.classList.add('open');
  modal.querySelectorAll('.settings-panel').forEach(panel => { panel.hidden = panel.id !== 'settings-panel-scene'; });
  modal.querySelectorAll('[role="tab"]').forEach(tab => tab.setAttribute('aria-selected', String(tab.id === 'settings-tab-scene')));
};
document.getElementById('settings-close').onclick = () => { modal.hidden = true; modal.classList.remove('open'); show(); };
manager.onPersistChange = show;
renderer.setAnimationLoop(() => { manager.update(clock.getDelta()); controls.update(); renderer.render(scene,camera); });
show();
