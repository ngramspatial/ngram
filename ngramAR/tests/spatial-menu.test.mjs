import assert from 'node:assert/strict';
import test from 'node:test';
import { build } from 'esbuild';
import { fileURLToPath } from 'node:url';

// Exercise production geometry and selection math without a headset or a DOM.
const bundle = await build({
  stdin: { contents: `export { WristMenu } from './wrist-menu.ts'; export { RadialMenu } from './radial-menu.ts'; export * as THREE from 'three';`,
    resolveDir: fileURLToPath(new URL('../packages/surface-webxr/src/', import.meta.url)), loader: 'ts' },
  bundle: true, write: false, platform: 'node', format: 'esm', target: 'es2022',
});
const { WristMenu, RadialMenu, THREE } = await import(`data:text/javascript;base64,${Buffer.from(bundle.outputFiles[0].contents).toString('base64')}`);

function gazeAtPixel(menu, camera, x, y) {
  const width = .22, canvasWidth = 360, canvasHeight = 434;
  const height = width * canvasHeight / canvasWidth;
  // Set the menu center relative to the forward gaze. This matches a camera-facing sprite.
  const center = new THREE.Vector3((.5 - x / canvasWidth) * width, (y / canvasHeight - .5) * height, -.48);
  center.applyQuaternion(camera.getWorldQuaternion(new THREE.Quaternion()));
  menu._menuPos.copy(camera.getWorldPosition(new THREE.Vector3())).add(center);
  return menu.getGazeItem(camera);
}

test('wrist gaze selects the drawn rows through head pitch, roll, and a transformed camera rig', () => {
  const menu = new WristMenu();
  const rig = new THREE.Group(); rig.position.set(3, 2, -1); rig.rotation.y = .7;
  const camera = new THREE.PerspectiveCamera(); rig.add(camera);
  for (const rotation of [[0,0,0], [.6,-.2,.5], [-.4,.3,-.7]]) {
    camera.rotation.set(...rotation); rig.updateMatrixWorld(true);
    for (let i = 0; i < 5; i++) assert.equal(gazeAtPixel(menu, camera, 180, 74 + i * 62 + 31), i);
  }
});

test('wrist header, footer, gutters, row gaps, and space behind the viewer are not actions', () => {
  const menu = new WristMenu(); const camera = new THREE.PerspectiveCamera();
  for (const [x,y] of [[180,30],[180,415],[3,105],[357,105],[180,74],[180,135]]) {
    assert.equal(gazeAtPixel(menu,camera,x,y), -1, `Unexpected action at ${x},${y}`);
  }
  menu._menuPos.set(0,0,.48); assert.equal(menu.getGazeItem(camera), -1);
});

test('controller menu retains all six action directions and its neutral dead zone', () => {
  const menu = new RadialMenu(); menu._isOpen = true;
  ['mic','switch','vision','resize','reposition','terminal'].forEach((id,i) => {
    const angle = Math.PI/2 + i * Math.PI*2/6;
    menu.highlight(Math.cos(angle), -Math.sin(angle)); assert.equal(menu.select(),id);
  });
  menu.highlight(.1,.1); assert.equal(menu.select(),null);
  menu.close(); assert.equal(menu.isOpen,false);
});
