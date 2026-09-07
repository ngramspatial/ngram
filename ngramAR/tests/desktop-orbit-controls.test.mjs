import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';

const source = (name) => readFile(
  new URL(`../packages/surface-webxr/src/${name}`, import.meta.url),
  'utf8',
);

test('desktop left-drag orbits and right-drag pans the camera', async () => {
  const [sceneSetup, sceneObjects, panels, desktopInteraction] = await Promise.all([
    source('scene-setup.ts'),
    source('scene-object-manager.ts'),
    source('grab-controller.ts'),
    source('desktop-interaction.ts'),
  ]);

  assert.match(sceneSetup, /mouseButtons\.LEFT\s*=\s*THREE\.MOUSE\.ROTATE/);
  assert.match(sceneSetup, /mouseButtons\.MIDDLE\s*=\s*THREE\.MOUSE\.DOLLY/);
  assert.match(sceneSetup, /mouseButtons\.RIGHT\s*=\s*THREE\.MOUSE\.PAN/);

  assert.match(sceneObjects, /onPointerDown[^]*?if \(e\.button !== 0\) return;/);
  assert.match(panels, /onMouseDown[^]*?if \(e\.button !== 0\) return;/);
  assert.doesNotMatch(desktopInteraction, /addEventListener\(['"]contextmenu['"]/);
});
