// @ts-nocheck
import * as THREE from 'three';
import { Sky } from 'three/addons/objects/Sky.js';
import { RGBELoader } from 'three/addons/loaders/RGBELoader.js';
import { EXRLoader } from 'three/addons/loaders/EXRLoader.js';
import { ENVIRONMENT_API_HELP, parseEnvironmentPatch } from './environment-contract.js';

const MAX_BYTES = 32 * 1024 * 1024;
function dimensions(width, height) {
  if (!width || !height || width > 8192 || height > 4096 || Math.abs(width - height * 2) > 2) throw Error('Sky panorama must be 2:1 and at most 8192×4096');
}
async function panorama(sky, signal) {
  const response = await fetch(sky.url, { signal, credentials: 'same-origin', redirect: 'error' });
  if (!response.ok) throw Error(`Sky image unavailable (${response.status})`);
  if (Number(response.headers.get('content-length')) > MAX_BYTES) { await response.body?.cancel(); throw Error('Sky image exceeds 32 MB'); }
  const reader = response.body?.getReader();
  if (!reader) throw Error('Sky image has no body');
  const chunks = []; let total = 0;
  try {
    while (true) {
      const { done, value } = await reader.read(); if (done) break;
      total += value.length;
      if (total > MAX_BYTES) throw Error('Sky image exceeds 32 MB');
      chunks.push(value);
    }
  } catch (error) { await reader.cancel(); throw error; }
  finally { reader.releaseLock(); }
  if (!total) throw Error('Sky image is empty');
  let texture;
  if (sky.format === 'image') {
    const bitmap = await createImageBitmap(new Blob(chunks), { imageOrientation: 'flipY' });
    try { dimensions(bitmap.width, bitmap.height); }
    catch (error) { bitmap.close(); throw error; }
    texture = new THREE.Texture(bitmap);
    texture.colorSpace = THREE.SRGBColorSpace;
  } else {
    const bytes = new Uint8Array(total); let offset = 0;
    for (const chunk of chunks) { bytes.set(chunk, offset); offset += chunk.length; }
    const data = (sky.format === 'hdr' ? new RGBELoader() : new EXRLoader()).parse(bytes.buffer);
    dimensions(data.width, data.height);
    texture = new THREE.DataTexture(data.data, data.width, data.height, data.format ?? THREE.RGBAFormat, data.type);
    texture.colorSpace = THREE.LinearSRGBColorSpace;
    texture.flipY = sky.format === 'hdr';
  }
  texture.mapping = THREE.EquirectangularReflectionMapping;
  texture.minFilter = THREE.LinearFilter; texture.magFilter = THREE.LinearFilter;
  texture.needsUpdate = true;
  return texture;
}

/** Prepare resources off-scene, then swap atomically. Failed or superseded loads
 * leave the last working sky intact. No network or PMREM work in the frame loop. */
export async function buildSky(sky, renderer, signal) {
  let texture, cube, environment, skyObject;
  const generator = new THREE.PMREMGenerator(renderer);
  const dispose = () => {
    environment?.dispose();
    skyObject?.removeFromParent(); skyObject?.geometry.dispose(); skyObject?.material.dispose();
    if (cube) cube.dispose();
    else { texture?.image?.close?.(); texture?.dispose(); }
  };
  try {
    if (sky.type === 'panorama') {
      texture = await panorama(sky, signal);
      signal.throwIfAborted();
      environment = generator.fromEquirectangular(texture);
    } else {
      const skyScene = new THREE.Scene();
      skyObject = new Sky(); skyObject.scale.setScalar(1000); skyScene.add(skyObject);
      const uniforms = skyObject.material.uniforms;
      for (const name of ['turbidity', 'rayleigh', 'mieCoefficient', 'mieDirectionalG']) uniforms[name].value = sky[name];
      uniforms.sunPosition.value.setFromSphericalCoords(1, THREE.MathUtils.degToRad(90 - sky.elevation), THREE.MathUtils.degToRad(sky.azimuth));
      cube = new THREE.WebGLCubeRenderTarget(512, { type: THREE.HalfFloatType });
      new THREE.CubeCamera(.1, 2000, cube).update(renderer, skyScene);
      texture = cube.texture;
      environment = generator.fromCubemap(texture);
      // Draw the analytic atmosphere at screen resolution. The cubemap is only
      // for reflections/blur, so the sun never becomes a large square pixel.
      skyObject.removeFromParent();
      skyObject.frustumCulled = false; skyObject.renderOrder = -10000;
      skyObject.material.uniforms.skyBrightness = { value: 1 };
      skyObject.material.fragmentShader = 'uniform float skyBrightness;\n' + skyObject.material.fragmentShader.replace('#include <tonemapping_fragment>', 'gl_FragColor.rgb *= skyBrightness;\n#include <tonemapping_fragment>');
      skyObject.material.needsUpdate = true;
      skyObject.onBeforeRender = (_renderer, _scene, camera) => { camera.getWorldPosition(skyObject.position); skyObject.updateMatrixWorld(); };
    }
    return { texture, environment: environment.texture, dome: skyObject, dispose };
  } catch (error) { dispose(); throw error; }
  finally { generator.dispose(); }
}

export class SceneEnvironment {
  config = {};
  revision = 0;
  status = 'ready';
  error = null;
  resource = null;
  generation = 0;
  pending = null;
  inAR = false;
  onChange = null;
  constructor(scene, renderer, lights, prepare = buildSky) {
    this.scene = scene; this.renderer = renderer; this.lights = lights; this.prepare = prepare;
    this.baseline = {
      background: scene.background, environment: scene.environment, fog: scene.fog,
      exposure: renderer.toneMappingExposure,
      environmentIntensity: scene.environmentIntensity, backgroundIntensity: scene.backgroundIntensity,
      backgroundBlurriness: scene.backgroundBlurriness,
      backgroundRotation: scene.backgroundRotation.clone(), environmentRotation: scene.environmentRotation.clone(),
      ground: scene.getObjectByName('ground-stage')?.visible ?? true,
      lights: Object.fromEntries(Object.entries(lights).map(([name, light]) => [name, {
        color: light.color.clone(), intensity: light.intensity, groundColor: light.groundColor?.clone(),
        position: light.position.clone(), target: light.target?.position.clone(), castShadow: light.castShadow,
      }])),
    };
  }
  inspect() {
    const { scene, renderer } = this;
    return { revision: this.revision, status: this.status, error: this.error, config: structuredClone(this.config),
      effective: { skyVisible: !!this.config.sky && scene.background === this.resource?.texture,
        reflections: !!this.config.sky?.lighting && scene.environment === this.resource?.environment,
        passthrough: this.inAR && !this.config.sky?.immersive,
        exposure: renderer.toneMappingExposure, ground: scene.getObjectByName('ground-stage')?.visible ?? false,
        fog: scene.fog ? { color: '#' + scene.fog.color.getHexString(), ...(scene.fog.isFogExp2 ? { type: 'exponential', density: scene.fog.density } : { type: 'linear', near: scene.fog.near, far: scene.fog.far }) } : null,
        lights: Object.fromEntries(Object.entries(this.lights).map(([name, light]) => [name, {
          color: '#' + light.color.getHexString(), intensity: light.intensity, position: light.position.toArray(),
          ...(light.groundColor ? { ground: '#' + light.groundColor.getHexString() } : {}),
          ...(light.target ? { target: light.target.position.toArray(), shadows: light.castShadow } : {}),
        }])) },
    };
  }
  async handle(command, payload = {}) {
    if (command === 'capabilities') return ENVIRONMENT_API_HELP;
    if (command === 'inspect') return this.inspect();
    if (command === 'clear') { this.clear(); return this.inspect(); }
    if (command !== 'configure') throw Error('Unknown environment command');
    return this.configure(payload);
  }
  async configure(payload) {
    const patch = parseEnvironmentPatch(payload);
    const next = { ...this.config };
    for (const [key, value] of Object.entries(patch)) { if (value === null) delete next[key]; else next[key] = value; }
    const generation = ++this.generation;
    this.pending?.abort();
    const controller = new AbortController(); this.pending = controller;
    const timer = setTimeout(() => controller.abort(Error('Sky load timed out')), 90000);
    const skyChanged = JSON.stringify(next.sky) !== JSON.stringify(this.config.sky);
    // Rotation, visibility and intensity edits reuse the uploaded sky.
    const content = sky => sky && (sky.type === 'panorama' ? [sky.type, sky.url, sky.format] : [sky.type, sky.elevation, sky.azimuth, sky.turbidity, sky.rayleigh, sky.mieCoefficient, sky.mieDirectionalG]);
    const rebuild = !!next.sky && JSON.stringify(content(next.sky)) !== JSON.stringify(content(this.config.sky));
    let prepared;
    this.status = rebuild ? 'loading' : 'ready'; this.error = null; this.onChange?.();
    try {
      if (rebuild) prepared = await this.prepare(next.sky, this.renderer, controller.signal);
      controller.signal.throwIfAborted();
      if (generation !== this.generation) throw Error('Sky update superseded');
      this.restoreControlled();
      const previous = this.resource;
      this.config = next;
      if (prepared) this.resource = prepared;
      else if (!next.sky) this.resource = null;
      this.fog = next.fog ? next.fog.type === 'linear' ? new THREE.Fog(next.fog.color, next.fog.near, next.fog.far) : new THREE.FogExp2(next.fog.color, next.fog.density) : null;
      this.revision++; this.status = 'ready';
      this.apply();
      if (skyChanged && previous !== this.resource) previous?.dispose();
      this.onChange?.();
      return this.inspect();
    } catch (error) {
      if (prepared !== this.resource) prepared?.dispose();
      if (generation === this.generation) { this.status = 'error'; this.error = String(error.message); this.onChange?.(); }
      throw error;
    } finally { clearTimeout(timer); if (this.pending === controller) this.pending = null; }
  }
  restoreControlled() {
    const { config, scene, baseline: b } = this;
    if (config.sky) {
      scene.background = b.background; scene.environment = b.environment;
      scene.environmentIntensity = b.environmentIntensity; scene.backgroundIntensity = b.backgroundIntensity;
      scene.backgroundBlurriness = b.backgroundBlurriness;
      scene.backgroundRotation.copy(b.backgroundRotation); scene.environmentRotation.copy(b.environmentRotation);
      if (config.sky.type === 'atmosphere') { this.lights.key.position.copy(b.lights.key.position); this.lights.key.target.position.copy(b.lights.key.target); this.lights.key.target.updateMatrixWorld(); }
    }
    if (config.fog) scene.fog = b.fog;
    if (config.exposure !== undefined) this.renderer.toneMappingExposure = b.exposure;
    if (config.ground !== undefined) { const ground = scene.getObjectByName('ground-stage'); if (ground) ground.visible = b.ground; }
    for (const [name, values] of Object.entries(config.lighting ?? {})) {
      const light = this.lights[name], original = b.lights[name];
      for (const key of Object.keys(values)) {
        if (key === 'target') { light.target.position.copy(original.target); light.target.updateMatrixWorld(); }
        else if (key === 'shadows') light.castShadow = original.castShadow;
        else if (key === 'intensity') light.intensity = original.intensity;
        else { const property = key === 'sky' ? 'color' : key === 'ground' ? 'groundColor' : key; light[property].copy(original[property]); }
      }
    }
  }
  clear() {
    ++this.generation; this.pending?.abort(); this.pending = null;
    this.restoreControlled(); this.config = {}; this.fog = null;
    this.resource?.dispose(); this.resource = null; this.status = 'ready'; this.error = null; this.revision++;
    this.apply(); this.onChange?.();
  }
  setAR(active) { this.inAR = active; this.apply(); }
  refreshBackgroundBaseline() {
    if (!this.inAR && this.scene.background !== this.resource?.texture) this.baseline.background = this.scene.background;
  }
  apply() {
    const { config, scene, renderer, resource } = this;
    const sky = config.sky;
    const passthrough = this.inAR && !sky?.immersive;
    if (sky && resource) {
      if (resource.dome) {
        if (resource.dome.parent !== scene) scene.add(resource.dome);
        resource.dome.visible = sky.visible && !passthrough && sky.blur === 0;
        resource.dome.material.uniforms.skyBrightness.value = sky.backgroundIntensity;
        resource.dome.material.uniforms.sunPosition.value.setFromSphericalCoords(1, THREE.MathUtils.degToRad(90 - sky.elevation), THREE.MathUtils.degToRad(sky.azimuth) + sky.rotation);
      }
      scene.background = sky.visible && !passthrough ? resource.texture : this.inAR ? null : this.baseline.background;
      scene.environment = sky.lighting ? resource.environment : this.baseline.environment;
      scene.environmentIntensity = sky.lighting ? sky.intensity : this.baseline.environmentIntensity;
      scene.backgroundIntensity = sky.visible ? sky.backgroundIntensity : this.baseline.backgroundIntensity;
      scene.backgroundBlurriness = sky.visible ? sky.blur : this.baseline.backgroundBlurriness;
      if (sky.visible) scene.backgroundRotation.set(0, sky.rotation, 0); else scene.backgroundRotation.copy(this.baseline.backgroundRotation);
      if (sky.lighting) scene.environmentRotation.set(0, sky.rotation, 0); else scene.environmentRotation.copy(this.baseline.environmentRotation);
      if (sky.type === 'atmosphere' && !config.lighting?.key?.position) {
        this.lights.key.position.setFromSphericalCoords(10, THREE.MathUtils.degToRad(90 - sky.elevation), THREE.MathUtils.degToRad(sky.azimuth) + sky.rotation);
        this.lights.key.target.position.set(0,0,0);
        this.lights.key.target.updateMatrixWorld();
      }
    }
    if (passthrough) scene.background = null;
    if (config.fog || passthrough) scene.fog = passthrough ? null : this.fog;
    if (config.exposure !== undefined) renderer.toneMappingExposure = config.exposure;
    const ground = scene.getObjectByName('ground-stage');
    if (ground && (config.ground !== undefined || this.inAR)) ground.visible = !this.inAR && (config.ground ?? this.baseline.ground);
    for (const [name, values] of Object.entries(config.lighting ?? {})) {
      const light = this.lights[name];
      for (const [key, value] of Object.entries(values)) {
        if (key === 'position') light.position.fromArray(value);
        else if (key === 'target') { light.target.position.fromArray(value); light.target.updateMatrixWorld(); }
        else if (key === 'shadows') light.castShadow = value;
        else if (key === 'intensity') light.intensity = value;
        else light[key === 'sky' ? 'color' : key === 'ground' ? 'groundColor' : key].set(value);
      }
    }
  }
}
