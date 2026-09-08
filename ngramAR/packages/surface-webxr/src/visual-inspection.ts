// @ts-nocheck
import * as THREE from 'three';
import { RoomEnvironment } from 'three/addons/environments/RoomEnvironment.js';
import { getWorld } from './physics-world.js';

const ANGLES = { front: [0,0,1], back: [0,0,-1], left: [-1,0,0], right: [1,0,0], top: [0,1,0], bottom: [0,-1,0], perspective: [1,.55,1] };
const number = (v, fallback, min, max) => {
  if (v === undefined) return fallback;
  if (typeof v !== 'number' || !Number.isFinite(v) || v < min || v > max) throw Error(`Expected a number in ${min}..${max}`);
  return v;
};
const vector = v => {
  if (!Array.isArray(v) || v.length !== 3) throw Error('Expected [x,y,z]');
  return new THREE.Vector3(...v.map(n => number(n, 0, -10000, 10000)));
};

export function inspectionOptions(input = {}) {
  if (!input || typeof input !== 'object' || Array.isArray(input)) throw Error('Capture options must be an object');
  const allowed = new Set(['target','views','position','lookAt','orbit','distance','projection','size','isolate','style','node','includeColliders']);
  for (const key of Object.keys(input)) if (!allowed.has(key)) throw Error(`Unknown capture option: ${key}`);
  const o = { ...input, size: number(input.size, 1024, 256, 1536), style: input.style ?? 'scene', projection: input.projection ?? 'perspective' };
  if (!Number.isInteger(o.size)) throw Error('Capture size must be an integer');
  if (!['scene','studio','clay','wireframe'].includes(o.style)) throw Error('Unknown inspection style');
  if (!['perspective','orthographic'].includes(o.projection)) throw Error('Unknown projection');
  if (o.target !== undefined && (typeof o.target !== 'string' || !o.target || o.target.length > 96)) throw Error('Invalid target ID');
  if (o.node !== undefined && (typeof o.node !== 'string' || !o.node || o.node.length > 160 || !o.target)) throw Error('node needs a target and a unique mesh name');
  for (const key of ['isolate','includeColliders']) if (o[key] !== undefined && typeof o[key] !== 'boolean') throw Error(`${key} must be boolean`);
  for (const key of ['position','lookAt']) if (o[key] !== undefined) vector(o[key]);
  if (o.distance !== undefined) number(o.distance, 1, .01, 10000);
  if (o.views !== undefined && (!Array.isArray(o.views) || !o.views.length || o.views.length > 4 || o.views.some(v => !Object.hasOwn(ANGLES, v)))) throw Error('views needs 1..4 named angles');
  if (o.orbit !== undefined && (!Array.isArray(o.orbit) || o.orbit.length !== 2 || o.orbit.some(v => typeof v !== 'number' || !Number.isFinite(v)) || Math.abs(o.orbit[1]) > 90)) throw Error('orbit needs [azimuthDegrees,elevationDegrees], elevation -90..90');
  if (o.isolate && !o.target) throw Error('isolate needs a target');
  return o;
}

export function inspectionCamera(options, bounds, source, angle) {
  const center = options.lookAt ? vector(options.lookAt) : bounds ? bounds.getCenter(new THREE.Vector3()) : source.position.clone().add(source.getWorldDirection(new THREE.Vector3()).multiplyScalar(2));
  const radius = bounds ? Math.max(.01, bounds.getSize(new THREE.Vector3()).length() / 2) : 1;
  const fov = 40;
  const distance = options.distance ?? radius / Math.sin(THREE.MathUtils.degToRad(fov / 2)) * 1.12;
  const clip = Math.max(100, distance + radius * 4);
  const camera = options.projection === 'orthographic'
    ? new THREE.OrthographicCamera(-radius * 1.15, radius * 1.15, radius * 1.15, -radius * 1.15, .001, clip)
    : new THREE.PerspectiveCamera(fov, 1, .001, clip);
  if (options.projection === 'perspective' && !bounds && !angle && !options.orbit && !options.position && !options.lookAt) {
    camera.copy(source, false); camera.matrixAutoUpdate = true;
    camera.position.copy(source.getWorldPosition(new THREE.Vector3()));
    camera.quaternion.copy(source.getWorldQuaternion(new THREE.Quaternion()));
    return camera;
  }
  let direction = new THREE.Vector3(...(ANGLES[angle] ?? ANGLES.perspective)).normalize();
  if (options.orbit) {
    const az = THREE.MathUtils.degToRad(options.orbit[0]), el = THREE.MathUtils.degToRad(options.orbit[1]);
    direction.set(Math.sin(az) * Math.cos(el), Math.sin(el), Math.cos(az) * Math.cos(el));
  }
  camera.position.copy(options.position ? vector(options.position) : center.clone().addScaledVector(direction, distance));
  const actualDirection = camera.position.clone().sub(center);
  if (actualDirection.lengthSq() < 1e-12) throw Error('Camera position must differ from lookAt');
  direction = actualDirection.normalize();
  camera.far = Math.max(camera.far, camera.position.distanceTo(center) + radius * 4);
  camera.updateProjectionMatrix();
  if (Math.abs(direction.y) > .999) camera.up.set(0,0,-Math.sign(direction.y));
  camera.lookAt(center); camera.updateMatrixWorld(true);
  return camera;
}

/** On-demand pixels from the actual scene, using an independent camera and render target. */
export class VisualInspection {
  epoch = 0;
  busy = false;
  environment = null;
  constructor(renderer, scene, camera, world, enabled, onCapture = () => {}) {
    Object.assign(this, { renderer, scene, camera, world, enabled, onCapture });
  }
  cancel() { this.epoch++; }
  async capture(input = {}, { manual = false } = {}) {
    if (!manual && !this.enabled()) throw Error('View sharing is off. The human can enable the camera button in the top bar.');
    if (this.busy) throw Error('A visual inspection is already running');
    const options = inspectionOptions(input), epoch = this.epoch;
    const target = options.target ? this.world.entries.get(options.target) : null;
    if (options.target && !target) throw Error('Capture target does not exist; inspect the world for its ID');
    if (target?.spec.asset && target.status !== 'ready') throw Error('Target asset is not ready; wait for its published preview to load');
    const roots = target ? [target.node] : [];
    for (const id of Object.values(target?.spec.figment?.parts ?? {})) {
      const entry = this.world.entries.get(id);
      if (entry) roots.push(entry.node);
    }
    if (options.node) {
      const matches = new Set();
      for (const root of roots) root.traverse(n => { if (n.name === options.node) matches.add(n); });
      if (matches.size !== 1) throw Error('Mesh node name is missing or ambiguous');
      roots.splice(0, roots.length, ...matches);
    }
    this.scene.updateMatrixWorld(true);
    const bounds = roots.length ? new THREE.Box3() : null;
    for (const root of roots) bounds.expandByObject(root);
    if (bounds?.isEmpty()) throw Error('Target has no visible geometry to inspect');
    const views = options.views ?? [target || options.position || options.orbit ? 'perspective' : null];
    const images = [], cameras = [];
    this.busy = true;
    try {
      for (const angle of views) {
        // Yield between angles so Stop and a human turning sharing off take effect.
        await new Promise(resolve => setTimeout(resolve, 0));
        if (epoch !== this.epoch || (!manual && !this.enabled())) throw Error('Visual inspection cancelled');
        const source = this.renderer.xr.isPresenting ? this.renderer.xr.getCamera(this.camera).cameras[0] ?? this.camera : this.camera;
        const camera = inspectionCamera(options, bounds, source, angle);
        images.push({ url: this.render(camera, options, roots), label: `${target?.spec.name ?? 'Spatial view'} · ${angle ?? 'user viewpoint'} · ${options.style} · virtual scene only` });
        cameras.push({ position: camera.position.toArray(), quaternion: camera.quaternion.toArray(), projection: options.projection, angle });
      }
      if (epoch !== this.epoch || (!manual && !this.enabled())) throw Error('Visual inspection cancelled');
      const result = { ok: true, source: 'spatial-render', physicalCamera: false, target: options.target ?? null, revision: this.world.store.document.revision,
        capturedAt: new Date().toISOString(), style: options.style, isolated: !!options.isolate, cameras, images };
      this.onCapture(result);
      return result;
    } finally { this.busy = false; }
  }
  render(camera, options, roots) {
    const r = this.renderer, scene = this.scene;
    const ratio = camera.isPerspectiveCamera ? camera.aspect : 1;
    const width = Math.max(1, Math.round(options.size * Math.min(1, ratio))), height = Math.max(1, Math.round(options.size / Math.max(1, ratio)));
    const target = new THREE.WebGLRenderTarget(width, height, { depthBuffer: true });
    target.texture.colorSpace = THREE.SRGBColorSpace;
    const previous = { target: r.getRenderTarget(), viewport: r.getViewport(new THREE.Vector4()), scissor: r.getScissor(new THREE.Vector4()),
      scissorTest: r.getScissorTest(), xr: r.xr.enabled, autoClear: r.autoClear, background: scene.background, fog: scene.fog,
      environment: scene.environment, override: scene.overrideMaterial };
    const hidden = [], temporary = [], cleanup = [];
    try {
      r.xr.enabled = false;
      if (options.isolate) {
        const keep = new Set(); roots.forEach(root => root.traverse(n => keep.add(n)));
        scene.traverse(n => {
          if ((n.isMesh || n.isLine || n.isPoints || n.isSprite) && !keep.has(n)) { hidden.push([n, n.visible]); n.visible = false; }
        });
      }
      if (options.style !== 'scene') {
        if (!this.environment) {
          const generator = new THREE.PMREMGenerator(r), room = new RoomEnvironment();
          this.environment = generator.fromScene(room, .04); room.dispose(); generator.dispose();
        }
        scene.environment = this.environment.texture;
        scene.background = new THREE.Color('#c6c9d2'); scene.fog = null;
        scene.traverse(n => { if (n.isLight) { hidden.push([n,n.visible]); n.visible = false; } });
        const hemi = new THREE.HemisphereLight(0xffffff,0x333849,1.5);
        const key = new THREE.DirectionalLight(0xffffff,3); key.position.copy(camera.position);
        temporary.push(hemi,key); scene.add(hemi,key);
      } else if (!scene.background) scene.background = new THREE.Color('#e4e5e9');
      if (['clay','wireframe'].includes(options.style)) {
        const material = options.style === 'clay' ? new THREE.MeshStandardMaterial({ color: '#aaaeb9', roughness: .65 }) : new THREE.MeshBasicMaterial({ color: '#27324e', wireframe: true });
        scene.overrideMaterial = material; cleanup.push(material);
      }
      if (options.includeColliders) {
        const data = getWorld()?.debugRender();
        if (data) {
          const geometry = new THREE.BufferGeometry();
          geometry.setAttribute('position', new THREE.BufferAttribute(data.vertices, 3));
          const material = new THREE.LineBasicMaterial({ color: '#6e7dff', depthTest: false });
          const lines = new THREE.LineSegments(geometry, material); lines.renderOrder = 10000;
          temporary.push(lines); cleanup.push(geometry,material); scene.add(lines);
        }
      }
      r.xr.enabled = false; r.autoClear = true;
      r.setRenderTarget(target); r.setViewport(0,0,width,height); r.setScissorTest(false);
      r.render(scene, camera);
      const bytes = new Uint8Array(width * height * 4);
      r.readRenderTargetPixels(target,0,0,width,height,bytes);
      const canvas = document.createElement('canvas'); canvas.width = width; canvas.height = height;
      const context = canvas.getContext('2d'), pixels = context.createImageData(width,height);
      for (let y=0;y<height;y++) pixels.data.set(bytes.subarray((height-y-1)*width*4,(height-y)*width*4), y*width*4);
      context.putImageData(pixels,0,0);
      return canvas.toDataURL('image/jpeg',.88);
    } finally {
      for (const [node, visible] of hidden) node.visible = visible;
      temporary.forEach(n => scene.remove(n)); cleanup.forEach(n => n.dispose());
      scene.background = previous.background; scene.environment = previous.environment; scene.fog = previous.fog; scene.overrideMaterial = previous.override;
      r.setRenderTarget(previous.target); r.setViewport(previous.viewport); r.setScissor(previous.scissor); r.setScissorTest(previous.scissorTest);
      r.autoClear = previous.autoClear; r.xr.enabled = previous.xr; target.dispose();
    }
  }
  async dispatch(action, send) {
    try {
      const result = await this.capture(action.options ?? {});
      send({ type: 'event:action_completed', completedActionId: action.actionId, status: 'completed', result });
    } catch (error) {
      send({ type: 'event:action_completed', completedActionId: action.actionId, status: 'failed', error: String(error.message).slice(0,300) });
    }
  }
}
