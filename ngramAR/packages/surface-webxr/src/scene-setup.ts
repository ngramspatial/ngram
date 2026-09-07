// @ts-nocheck
import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import {
  DEFAULT_CAMERA_FAR,
  DEFAULT_CAMERA_NEAR,
  getOrbitCameraClipRange,
} from './camera-clipping.js';
import { DesktopNavigationController } from './desktop-navigation.js';
import { normalizeTheme, themeOverridesEnvironment } from './theme.js';

export interface SceneLights {
  key: THREE.DirectionalLight;
  fill: THREE.DirectionalLight;
  rim: THREE.DirectionalLight;
  hemi: THREE.HemisphereLight;
  ambient: THREE.AmbientLight;
}

export interface SceneContext {
  scene: THREE.Scene;
  camera: THREE.PerspectiveCamera;
  renderer: THREE.WebGLRenderer;
  clock: THREE.Clock;
  controls: OrbitControls | null;
  desktopNavigation: DesktopNavigationController | null;
  shadowPlane: THREE.Mesh | null;
  updateThemeBackground: () => void;
  envMap: THREE.Texture | null;
  lights: SceneLights;
}

function readCSSColor(prop: string): string {
  return getComputedStyle(document.documentElement).getPropertyValue(prop).trim();
}

function makeBackground(centerColor: string, edgeColor: string): THREE.CanvasTexture {
  const c = document.createElement('canvas');
  c.width = 512; c.height = 512;
  const ctx = c.getContext('2d')!;
  const g = ctx.createRadialGradient(256, 256, 0, 256, 256, 360);
  g.addColorStop(0, centerColor);
  g.addColorStop(1, edgeColor);
  ctx.fillStyle = g;
  ctx.fillRect(0, 0, 512, 512);
  return new THREE.CanvasTexture(c);
}

export function createScene(): SceneContext {
  const canvas = document.getElementById('viewport') as HTMLCanvasElement;
  const container = document.querySelector('.viewport-container') as HTMLElement;

  const cw = container?.clientWidth || window.innerWidth;
  const ch = container?.clientHeight || window.innerHeight;
  const isQuestBrowser = /OculusBrowser|Quest/i.test(navigator.userAgent);

  const renderer = new THREE.WebGLRenderer({
    canvas,
    antialias: true,
    alpha: true,
    premultipliedAlpha: false,
    powerPreference: 'high-performance',
    preserveDrawingBuffer: true,
  });
  renderer.toneMapping = THREE.ACESFilmicToneMapping;
  renderer.toneMappingExposure = 1.1;
  renderer.outputColorSpace = THREE.SRGBColorSpace;
  renderer.setClearColor(0x000000, 0);
  renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
  renderer.setSize(cw, ch);
  renderer.shadowMap.enabled = true;
  renderer.shadowMap.type = THREE.PCFSoftShadowMap;

  // WebXR copies these clipping planes into the XR session. Keep the base
  // range tight enough that clothing, hair, and skin do not z-fight in AR.
  const camera = new THREE.PerspectiveCamera(
    60,
    cw / ch,
    DEFAULT_CAMERA_NEAR,
    DEFAULT_CAMERA_FAR,
  );
  camera.position.set(0, 1.2, 2.5);
  camera.lookAt(0, 0.9, 0);

  const scene = new THREE.Scene();

  const initialTheme = normalizeTheme(document.documentElement.getAttribute('data-theme'));
  const centerCol = readCSSColor('--scene-bg-center') || '#e8e8ec';
  const edgeCol = readCSSColor('--scene-bg-edge') || '#d6d6da';
  scene.background = themeOverridesEnvironment(initialTheme) ? null : makeBackground(centerCol, edgeCol);

  function updateThemeBackground(): void {
    const c = readCSSColor('--scene-bg-center') || '#e8e8ec';
    const e = readCSSColor('--scene-bg-edge') || '#d6d6da';
    const theme = normalizeTheme(document.documentElement.getAttribute('data-theme'));
    scene.background = themeOverridesEnvironment(theme) ? null : makeBackground(c, e);
    const dark = theme === 'dark';

    // Refresh grid floor for current theme
    stageTex.dispose();
    stageTex = makeGridTexture(theme);
    stageMat.map = stageTex;
    stageMat.needsUpdate = true;

    if (dark) {
      hemi.intensity = 0.3;
      ambient.intensity = 0.2;
      key.intensity = 0.9;
      fill.intensity = 0.25;
      rim.intensity = 0.35;
      renderer.toneMappingExposure = 0.85;
    } else {
      hemi.intensity = 0.8;
      ambient.intensity = 0.4;
      key.intensity = 1.8;
      fill.intensity = 0.6;
      rim.intensity = 0.7;
      renderer.toneMappingExposure = 1.1;
    }
  }

  // Procedural environment map for PBR reflections
  const pmremGenerator = new THREE.PMREMGenerator(renderer);
  pmremGenerator.compileEquirectangularShader();

  const envScene = new THREE.Scene();
  const envGeo = new THREE.SphereGeometry(10, 32, 16);
  const envCanvas = document.createElement('canvas');
  envCanvas.width = 512; envCanvas.height = 256;
  const envCtx = envCanvas.getContext('2d')!;
  const envGrad = envCtx.createLinearGradient(0, 0, 0, 256);
  envGrad.addColorStop(0, '#d8dce8');
  envGrad.addColorStop(0.35, '#e8eaf0');
  envGrad.addColorStop(0.5, '#f0f0f4');
  envGrad.addColorStop(0.65, '#e0e0e4');
  envGrad.addColorStop(1, '#c8c8cc');
  envCtx.fillStyle = envGrad;
  envCtx.fillRect(0, 0, 512, 256);
  const envTex = new THREE.CanvasTexture(envCanvas);
  envTex.mapping = THREE.EquirectangularReflectionMapping;
  const envMat = new THREE.MeshBasicMaterial({ map: envTex, side: THREE.BackSide });
  envScene.add(new THREE.Mesh(envGeo, envMat));
  const envRT = pmremGenerator.fromScene(envScene, 0.04);
  const envMap = envRT.texture;
  scene.environment = envMap;
  pmremGenerator.dispose();
  envGeo.dispose();
  envMat.dispose();
  envTex.dispose();

  // Lighting — key, fill, rim, and hemisphere
  const hemi = new THREE.HemisphereLight(0xf0f0ff, 0xd0c8c0, 0.8);
  scene.add(hemi);

  const ambient = new THREE.AmbientLight(0xffffff, 0.4);
  scene.add(ambient);

  const key = new THREE.DirectionalLight(0xffffff, 1.8);
  key.position.set(2, 4, 3);
  key.castShadow = true;
  key.shadow.mapSize.set(1024, 1024);
  key.shadow.camera.near = 0.1;
  key.shadow.camera.far = 10;
  key.shadow.camera.left = -3;
  key.shadow.camera.right = 3;
  key.shadow.camera.top = 3;
  key.shadow.camera.bottom = -3;
  key.shadow.bias = -0.001;
  scene.add(key);

  const fill = new THREE.DirectionalLight(0xffe8d0, 0.6);
  fill.position.set(-2, 2, -1);
  scene.add(fill);

  const rim = new THREE.DirectionalLight(0xddeeff, 0.7);
  rim.position.set(-1, 2, -3);
  scene.add(rim);

  // Ground grid floor
  const gridSize = 20;
  const gridRes = 2048;
  const stageGeo = new THREE.PlaneGeometry(gridSize, gridSize);
  stageGeo.rotateX(-Math.PI / 2);

  function makeGridTexture(theme: string): THREE.CanvasTexture {
    const c = document.createElement('canvas');
    c.width = gridRes; c.height = gridRes;
    const ctx = c.getContext('2d')!;

    ctx.clearRect(0, 0, gridRes, gridRes);

    const divisions = 20;
    const cellSize = gridRes / divisions;
    const lineColor = theme === 'dark'
      ? 'rgba(140, 140, 140, 0.25)'
      : theme === 'periwinkle'
        ? 'rgba(255, 255, 255, 0.24)'
        : 'rgba(30, 30, 30, 0.18)';
    const majorColor = theme === 'dark'
      ? 'rgba(140, 140, 140, 0.4)'
      : theme === 'periwinkle'
        ? 'rgba(255, 255, 255, 0.42)'
        : 'rgba(30, 30, 30, 0.3)';

    // Minor grid lines
    ctx.strokeStyle = lineColor;
    ctx.lineWidth = 0.1;
    for (let i = 0; i <= divisions; i++) {
      const p = i * cellSize;
      ctx.beginPath(); ctx.moveTo(p, 0); ctx.lineTo(p, gridRes); ctx.stroke();
      ctx.beginPath(); ctx.moveTo(0, p); ctx.lineTo(gridRes, p); ctx.stroke();
    }

    // Major grid lines (every 5th)
    ctx.strokeStyle = majorColor;
    ctx.lineWidth = 0.15;
    for (let i = 0; i <= divisions; i += 5) {
      const p = i * cellSize;
      ctx.beginPath(); ctx.moveTo(p, 0); ctx.lineTo(p, gridRes); ctx.stroke();
      ctx.beginPath(); ctx.moveTo(0, p); ctx.lineTo(gridRes, p); ctx.stroke();
    }

    // Radial fade — grid vanishes toward edges
    const fade = ctx.createRadialGradient(gridRes / 2, gridRes / 2, 0, gridRes / 2, gridRes / 2, gridRes / 2);
    fade.addColorStop(0, 'rgba(0,0,0,0)');
    fade.addColorStop(0.6, 'rgba(0,0,0,0)');
    fade.addColorStop(1, theme === 'light' ? 'rgba(255,255,255,1)' : 'rgba(0,0,0,1)');
    ctx.globalCompositeOperation = 'destination-out';
    ctx.fillStyle = fade;
    ctx.fillRect(0, 0, gridRes, gridRes);
    ctx.globalCompositeOperation = 'source-over';

    const tex = new THREE.CanvasTexture(c);
    tex.wrapS = THREE.ClampToEdgeWrapping;
    tex.wrapT = THREE.ClampToEdgeWrapping;
    return tex;
  }

  let stageTex = makeGridTexture(initialTheme);
  const stageMat = new THREE.MeshBasicMaterial({ map: stageTex, transparent: true, depthWrite: false });
  const stagePlane = new THREE.Mesh(stageGeo, stageMat);
  stagePlane.position.y = 0.001;
  stagePlane.name = 'ground-stage';
  scene.add(stagePlane);

  // Contact shadow
  const shadowGeo = new THREE.PlaneGeometry(1.5, 1.5);
  shadowGeo.rotateX(-Math.PI / 2);
  const shadowCanvas = document.createElement('canvas');
  shadowCanvas.width = 128; shadowCanvas.height = 128;
  const ctx = shadowCanvas.getContext('2d')!;
  const gradient = ctx.createRadialGradient(64, 64, 0, 64, 64, 64);
  gradient.addColorStop(0, 'rgba(0,0,0,0.25)');
  gradient.addColorStop(1, 'rgba(0,0,0,0)');
  ctx.fillStyle = gradient;
  ctx.fillRect(0, 0, 128, 128);
  const shadowTex = new THREE.CanvasTexture(shadowCanvas);
  const shadowMat = new THREE.MeshBasicMaterial({ map: shadowTex, transparent: true, depthWrite: false, opacity: 0.5 });
  const shadowPlane = new THREE.Mesh(shadowGeo, shadowMat);
  shadowPlane.name = 'contact-shadow';
  shadowPlane.visible = false;
  shadowPlane.position.y = 0.005;
  scene.add(shadowPlane);

  // Orbit controls
  let controls: OrbitControls | null = null;
  let desktopNavigation: DesktopNavigationController | null = null;
  if (!isQuestBrowser) {
    controls = new OrbitControls(camera, canvas);
    controls.target.set(0, 0.9, 0);
    controls.enableDamping = true;
    controls.dampingFactor = 0.1;
    controls.enablePan = true;
    controls.screenSpacePanning = true;
    controls.zoomToCursor = true;
    // Left-drag orbits around the current pivot; right-drag travels through
    // the world and moves the orbit pivot with the camera.
    controls.mouseButtons.LEFT = THREE.MOUSE.ROTATE;
    controls.mouseButtons.MIDDLE = THREE.MOUSE.DOLLY;
    controls.mouseButtons.RIGHT = THREE.MOUSE.PAN;

    // OrbitControls already supports infinite panning; make every remaining
    // navigation constraint explicit so future defaults cannot reintroduce a
    // bounded workspace.
    controls.minDistance = 0;
    controls.maxDistance = Infinity;
    controls.minTargetRadius = 0;
    controls.maxTargetRadius = Infinity;
    controls.minAzimuthAngle = -Infinity;
    controls.maxAzimuthAngle = Infinity;
    controls.minPolarAngle = 0;
    controls.maxPolarAngle = Math.PI;

    // Keep the projection volume proportional to the current orbit radius.
    // A bounded far/near ratio preserves depth precision while still allowing
    // millimetre-scale inspection and navigation across very large scenes.
    const updateCameraClipRange = () => {
      const distance = camera.position.distanceTo(controls!.target);
      if (!Number.isFinite(distance)) return;
      const { near: nextNear, far: nextFar } = getOrbitCameraClipRange(distance);
      if (camera.near !== nextNear || camera.far !== nextFar) {
        camera.near = nextNear;
        camera.far = nextFar;
        camera.updateProjectionMatrix();
      }
    };
    controls.addEventListener('change', updateCameraClipRange);

    // Double-clicking establishes a new orbit/focus point. Scene geometry wins;
    // empty space falls back to an infinite horizontal ground plane so the
    // camera can establish a pivot beyond the finite decorative grid.
    const focusRaycaster = new THREE.Raycaster();
    const focusPointer = new THREE.Vector2();
    const infiniteGround = new THREE.Plane(new THREE.Vector3(0, 1, 0), 0);
    const groundHit = new THREE.Vector3();
    canvas.addEventListener('dblclick', (event) => {
      const rect = canvas.getBoundingClientRect();
      focusPointer.set(
        ((event.clientX - rect.left) / rect.width) * 2 - 1,
        -((event.clientY - rect.top) / rect.height) * 2 + 1,
      );
      focusRaycaster.setFromCamera(focusPointer, camera);
      const hit = focusRaycaster.intersectObjects(scene.children, true)
        .find((candidate) => candidate.object.visible);
      if (hit) {
        controls!.target.copy(hit.point);
      } else if (focusRaycaster.ray.intersectPlane(infiniteGround, groundHit)) {
        controls!.target.copy(groundHit);
      } else {
        return;
      }
      controls!.update();
    });

    controls.update();
    updateCameraClipRange();
    desktopNavigation = new DesktopNavigationController(camera, controls);
  }

  const clock = new THREE.Clock();

  if (container) {
    const ro = new ResizeObserver((entries) => {
      for (const entry of entries) {
        const { width, height } = entry.contentRect;
        if (width === 0 || height === 0) continue;
        camera.aspect = width / height;
        camera.updateProjectionMatrix();
        renderer.setSize(width, height);
      }
    });
    ro.observe(container);
  } else {
    window.addEventListener('resize', () => {
      camera.aspect = window.innerWidth / window.innerHeight;
      camera.updateProjectionMatrix();
      renderer.setSize(window.innerWidth, window.innerHeight);
    });
  }

  const lights: SceneLights = { key, fill, rim, hemi, ambient };

  return {
    scene,
    camera,
    renderer,
    clock,
    controls,
    desktopNavigation,
    shadowPlane,
    updateThemeBackground,
    envMap,
    lights,
  };
}
