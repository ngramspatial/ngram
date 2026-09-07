// @ts-nocheck
// Visual/runtime fixture. Private canvases are inspected here, never exposed by the product.
import * as THREE from 'three';
import { RadialMenu } from '../packages/surface-webxr/src/radial-menu.js';
import { WristMenu } from '../packages/surface-webxr/src/wrist-menu.js';
import { SpatialUI } from '../packages/surface-webxr/src/spatial-ui.js';
import { SpatialPanel } from '../packages/surface-webxr/src/spatial-panel.js';
import { loadSpatialAssets, SPATIAL, setSpatialFont, wrapSpatialText } from '../packages/surface-webxr/src/spatial-design.js';

await loadSpatialAssets();
const stage = document.querySelector('#stage');
const renderer = new THREE.WebGLRenderer({ antialias: true, preserveDrawingBuffer: true });
renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
renderer.toneMapping = THREE.ACESFilmicToneMapping;
renderer.toneMappingExposure = 1.2;
stage.append(renderer.domElement);
const scene = new THREE.Scene(); scene.background = new THREE.Color('#e4e5e2');
const camera = new THREE.PerspectiveCamera(58, 1, 0.01, 20);
camera.position.set(0, 1.6, 0);
const radial = new RadialMenu(); radial.attach(scene);
const wrist = new WristMenu(); wrist.attach(scene);
const ui = new SpatialUI(); ui.attach(scene);
const content = new SpatialPanel({ id: 'note', type: 'markdown', pinned: true, width: .48,
  content: '## A little more room to think.\nYour ngram stays with you, wherever the conversation goes.\n\n- Persistent memory\n- Tools within reach\n- A presence in your space\n\n> Same entity. A new point of view.', title: 'Right here with you.' }, true, true);
const code = new SpatialPanel({ id: 'code', type: 'code', pinned: true, width: .48,
  content: 'const ngram = {\n  memory: "persistent",\n  presence: "spatial",\n  accent: "#6E7DFF"\n};', title: 'A familiar workspace' }, true, true);
await new Promise(requestAnimationFrame);
const panels = [content, code];
for (const [i, item] of [content, code].entries()) {
  item.setPosition(i ? .27 : -.27, 1.58, -.85); item.group.visible = false;
  scene.add(item.group);
}
let current = 'help', selected = -1, dark = false, recording = false;
const desktopHtml = new DOMParser().parseFromString(await (await fetch('/')).text(), 'text/html');
const desktopStyles = Array.from(desktopHtml.querySelectorAll('style')).map(s => s.outerHTML).join('');
const desktopMic = desktopHtml.querySelector('#mic-btn')!.outerHTML;
const result = document.querySelector('#result');
const sheet = document.querySelector('#sheets');
const checks: string[] = [];
function check(name, valid) { checks.push(`${valid ? 'PASS' : 'FAIL'} · ${name}`); }
function textureSheet(name, canvas) {
  const figure = document.createElement('figure'); const caption = document.createElement('figcaption'); caption.textContent = name;
  const copy = document.createElement('canvas'); copy.width = canvas.width; copy.height = canvas.height;
  copy.getContext('2d').drawImage(canvas, 0, 0); figure.append(caption, copy); sheet.append(figure);
}
function microphoneSheet(active) {
  ui.setMicState(active, camera); ui.updateMicPulse(1, active ? .6 : 0);
  textureSheet(active ? 'Microphone / listening' : 'Microphone / off', ui.micCanvas);
}
function desktopMicrophoneSheet() {
  const figure = document.createElement('figure');
  const caption = document.createElement('figcaption'); caption.textContent = 'Desktop / off and recording (actual button styles)';
  const states = document.createElement('div');
  states.style.cssText = 'display:flex;align-items:center;justify-content:center;gap:32px;height:160px;';
  for (const active of [false,true]) {
    const host = document.createElement('div');
    const shadow = host.attachShadow({mode:'open'});
    const markup = active ? desktopMic.replace('id="mic-btn"', 'id="mic-btn" class="active"').replaceAll('Start voice input','Stop voice input').replace('aria-pressed="false"','aria-pressed="true"') : desktopMic;
    shadow.innerHTML = `${desktopStyles}<div class="command-actions">${markup}</div>`;
    states.append(host);
  }
  figure.append(caption,states); sheet.append(figure);
}
function show(view) {
  current = view; radial.close(); wrist.close(); ui.hideHelp(); ui.hideBubble(); ui.hideStatus();
  ui.micSprite.visible = false; panels.forEach(p => p.group.visible = false); sheet.replaceChildren();
  if (view === 'help') {
    ui.showHelp(camera); ui.helpGroup.quaternion.copy(camera.quaternion);
    textureSheet('Welcome / controls', ui.helpGroup.children[0].material.map.image);
  }
  if (view === 'radial') {
    radial.open(camera);
    for (const [i, tex] of radial.normalTextures.entries()) textureSheet(['Microphone', 'Shell', 'Share view', 'Resize', 'Move', 'Terminal'][i], tex.image);
  }
  if (view === 'wrist') {
    wrist.menuSprite.visible = true; wrist.menuSprite.position.set(0, 1.6, -.48);
    wrist.highlightedIndex = selected; wrist.renderMenu();
    textureSheet('Wrist / gaze selection', wrist.menuCanvas);
  }
  if (view === 'conversation') {
    ui.showBubble('I’m here. Let’s make a little room for what comes next.', new THREE.Vector3(0, 1.68, -.85), 0);
    clearTimeout(ui.hideTimer); ui.hideTimer = null;
    ui.showStatus('Point at the floor, then trigger or pinch to place.'); ui.positionStatus(camera);
    ui.setMicState(recording, camera); ui.positionMicIndicator(camera); ui.updateMicPulse(1, recording ? .6 : 0);
    textureSheet('Speech', ui.bubbleMesh.material.map.image);
    textureSheet('Placement guidance', ui.statusMesh.material.map.image);
    textureSheet(recording ? 'Microphone / listening' : 'Microphone / off', ui.micCanvas);
  }
  if (view === 'microphone') {
    ui.positionMicIndicator(camera);
    microphoneSheet(false); microphoneSheet(true); desktopMicrophoneSheet();
    ui.setMicState(recording, camera); ui.updateMicPulse(1, recording ? .6 : 0);
  }
  if (view === 'panels') {
    panels.forEach(p => p.group.visible = true);
    textureSheet('Content / markdown', content.renderer.getTexture().image);
    textureSheet('Content / code', code.renderer.getTexture().image);
  }
  for (const button of document.querySelectorAll('[data-view]')) button.setAttribute('aria-pressed', String(button.dataset.view === view));
  result.textContent = `Azeret Mono loaded · ${view} · actual XR textures at physical scale`;
}
document.querySelectorAll('[data-view]').forEach(button => button.addEventListener('click', () => show(button.dataset.view)));
document.querySelector('#backdrop').addEventListener('click', e => {
  dark = !dark; scene.background.set(dark ? '#121522' : '#e4e5e2'); e.target.textContent = dark ? 'Light backdrop' : 'Dark backdrop';
});
document.querySelector('#recording').addEventListener('click', e => {
  recording = !recording;
  ui.setMicState(recording, camera); radial.setMicState(recording); wrist.setMicState(recording);
  e.target.textContent = recording ? 'Stop simulation' : 'Simulate recording';
  e.target.setAttribute('aria-pressed',String(recording));
  if (!['microphone','conversation','wrist','radial'].includes(current)) show('microphone');
  else show(current);
});
document.querySelector('#select').addEventListener('click', () => {
  selected = (selected + 1) % (current === 'wrist' ? 5 : 6);
  if (current === 'wrist') {
    wrist.highlightedIndex = selected; wrist.renderMenu(); sheet.replaceChildren(); textureSheet('Wrist / selected', wrist.menuCanvas);
  } else {
    if (current !== 'radial') show('radial');
    const a = Math.PI / 2 + selected * Math.PI * 2 / 6;
    radial.highlight(Math.cos(a), -Math.sin(a));
    result.textContent = `Selected action: ${radial.select()}`;
  }
});

check('Azeret Mono loaded at all four UI weights', [400,500,600,700].every(w => document.fonts.check(`${w} 24px "Azeret Mono"`)));
radial.open(camera);
['mic','switch','vision','resize','reposition','terminal'].forEach((id, i) => {
  const a = Math.PI / 2 + i * Math.PI * 2 / 6;
  radial.highlight(Math.cos(a), -Math.sin(a)); check(`Controller selection: ${id}`, radial.select() === id);
});
radial.highlight(0,0); check('Controller dead zone has no selection', radial.select() === null); radial.close();
check('All menu textures use sRGB', [...radial.normalTextures,...radial.highlightTextures].every(t => t.colorSpace === THREE.SRGBColorSpace));
const rgba = radial.highlightTextures[1].image.getContext('2d').getImageData(150,20,1,1).data;
check('Selection field is exactly #6E7DFF', rgba[0] === 110 && rgba[1] === 125 && rgba[2] === 255 && rgba[3] === 255);
function pixelIs(canvas, x, y, expected) {
  return [...canvas.getContext('2d').getImageData(x,y,1,1).data].join(',') === expected;
}
for (const active of [true, false]) {
  ui.setMicState(active, camera); radial.setMicState(active); wrist.setMicState(active);
  const expected = active ? '110,125,255,255' : '255,255,255,255';
  const state = active ? 'Recording is periwinkle' : 'Off is white';
  check(`${state}: floating indicator updates immediately`, pixelIs(ui.micCanvas,150,20,expected));
  check(`${state}: controller microphone`, pixelIs(radial.normalTextures[0].image,150,20,expected));
  check(`${state}: wrist microphone`, pixelIs(wrist.menuCanvas,150,90,expected));
}
check('Microphone material preserves its exact color', ui.micSprite.material.toneMapped === false && ui.micTex.colorSpace === THREE.SRGBColorSpace);
const measure = document.createElement('canvas').getContext('2d'); setSpatialFont(measure, `500 23px ${SPATIAL.font}`);
const longText = 'https://ngram.sh/' + 'long-content-'.repeat(25);
check('Long unbroken status/speech content wraps within its bounds', wrapSpatialText(measure, longText, 536).every(line => measure.measureText(line).width <= 536));
check('Canvas letter spacing is active', measure.letterSpacing === '-0.805px' || Math.abs(parseFloat(measure.letterSpacing) + .805) < .001);
wrist._menuPos.set(0,1.6,-.48);
const wh = .22 * wrist.menuCanvas.height / wrist.menuCanvas.width;
for (let i = 0; i < 5; i++) {
  const y = 1.6 + wh/2 - (74 + i*62 + 31) / wrist.menuCanvas.height * wh;
  camera.lookAt(0,y,-.48); check(`Wrist gaze hits visible row ${i+1}`, wrist.getGazeItem(camera) === i);
}
camera.lookAt(0,1.6 + wh/2 - .01,-.48); check('Wrist header is not an action', wrist.getGazeItem(camera) === -1);
camera.lookAt(0,1.6 - wh/2 + .01,-.48); check('Wrist footer is not an action', wrist.getGazeItem(camera) === -1);
camera.quaternion.identity();
const restored = new SpatialPanel({id:'test',type:'card',content:'Restored content',width:.45,height:.3,pinned:true},false);
await new Promise(requestAnimationFrame);
restored.setImmersive(true); await new Promise(requestAnimationFrame);
check('Entering XR preserves restored panel dimensions', restored.getWorldSize().w === .45 && restored.getWorldSize().h === .3);
const restoredTexture = restored.renderer.getTexture().image;
check('Restored panel typography retains its proportions', Math.abs(restoredTexture.width/restoredTexture.height - 1.5) < .005);
check('XR content material bypasses scene tone mapping', restored.contentMesh.material.toneMapped === false);
restored.setImmersive(false); await new Promise(requestAnimationFrame);
check('Leaving XR restores desktop panel material', restored.contentMesh.material.toneMapped === true);
restored.dispose();
document.querySelector('#checks').textContent = checks.join('\n');
show('help');
const resize = () => { renderer.setSize(stage.clientWidth,stage.clientHeight); camera.aspect = stage.clientWidth/stage.clientHeight; camera.updateProjectionMatrix(); };
new ResizeObserver(resize).observe(stage); resize();
renderer.setAnimationLoop(() => {
  panels.forEach(p => p.update(.016, camera));
  // Keep fixture states visible for inspection after their product auto-hide timers.
  if(current === 'help') ui.helpGroup.visible = true;
  if(current === 'conversation' || current === 'microphone') { ui.billboardToCamera(camera); ui.updateMicPulse(.016,recording ? .6 : 0); }
  renderer.render(scene,camera);
});
