// @ts-nocheck
const object = (value, name, keys) => {
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw Error(`${name} must be an object`);
  for (const key of Object.keys(value)) if (!keys.includes(key)) throw Error(`Unknown ${name}.${key}`);
  return value;
};
const number = (v, name, min, max) => {
  if (typeof v !== 'number' || !Number.isFinite(v) || v < min || v > max) throw Error(`${name} must be ${min}..${max}`);
  return v;
};
const color = (v, name) => {
  if (typeof v !== 'string' || !/^#[0-9a-f]{6}$/i.test(v)) throw Error(`${name} must be a #RRGGBB color`);
  return v;
};
const bool = (v, name) => { if (typeof v !== 'boolean') throw Error(`${name} must be boolean`); return v; };
const vector = (v, name) => {
  if (!Array.isArray(v) || v.length !== 3) throw Error(`${name} must be [x,y,z]`);
  return v.map(n => number(n, name, -10000, 10000));
};

export function parseEnvironmentPatch(value) {
  const p = object(value, 'environment', ['sky', 'lighting', 'fog', 'exposure', 'ground']);
  const result = {};
  for (const [key, value] of Object.entries(p)) {
    if (value === null) { result[key] = null; continue; }
    if (key === 'ground') result.ground = bool(value, key);
    if (key === 'exposure') result.exposure = number(value, key, .01, 8);
    if (key === 'sky') {
      object(value, 'sky', ['type', 'url', 'format', 'elevation', 'azimuth', 'turbidity', 'rayleigh', 'mieCoefficient', 'mieDirectionalG', 'rotation', 'intensity', 'backgroundIntensity', 'blur', 'visible', 'lighting', 'immersive']);
      const sky = { type: 'atmosphere', rotation: 0, intensity: 1, backgroundIntensity: 1, blur: 0, visible: true, lighting: true, immersive: false, ...value };
      if (!['atmosphere', 'panorama'].includes(sky.type)) throw Error('sky.type must be atmosphere or panorama');
      for (const key of ['visible', 'lighting', 'immersive']) bool(sky[key], `sky.${key}`);
      number(sky.rotation, 'sky.rotation (radians)', -Math.PI * 2, Math.PI * 2);
      number(sky.intensity, 'sky.intensity', 0, 10);
      number(sky.backgroundIntensity, 'sky.backgroundIntensity', 0, 10);
      number(sky.blur, 'sky.blur', 0, 1);
      if (sky.type === 'panorama') {
        if (typeof sky.url !== 'string' || sky.url.length > 2048 || /[\\\s]/.test(sky.url) || !/^(https?:\/\/|\/(?!\/))/.test(sky.url)) throw Error('Panorama URL must be an HTTP(S) URL or a same-origin /path');
        sky.format ??= /\.hdr(?:[?#]|$)/i.test(sky.url) ? 'hdr' : /\.exr(?:[?#]|$)/i.test(sky.url) ? 'exr' : 'image';
        if (!['image', 'hdr', 'exr'].includes(sky.format)) throw Error('sky.format must be image, hdr or exr');
      } else {
        for (const [key, initial, min, max] of [['elevation', 25, -10, 90], ['azimuth', 180, -360, 360], ['turbidity', 10, 0, 20], ['rayleigh', 2, 0, 4], ['mieCoefficient', .005, 0, .1], ['mieDirectionalG', .8, 0, .999]]) sky[key] = number(sky[key] ?? initial, `sky.${key}`, min, max);
      }
      result.sky = sky;
    }
    if (key === 'lighting') {
      object(value, key, ['key', 'fill', 'rim', 'hemi', 'ambient']);
      result.lighting = {};
      for (const [name, light] of Object.entries(value)) {
        const directional = ['key', 'fill', 'rim'].includes(name);
        object(light, name, directional ? ['color', 'intensity', 'position', 'target', 'shadows'] : name === 'hemi' ? ['sky', 'ground', 'intensity'] : ['color', 'intensity']);
        const out = {};
        for (const [k, v] of Object.entries(light)) out[k] = k === 'intensity' ? number(v, name, 0, 30) : k === 'shadows' ? bool(v, name) : ['position', 'target'].includes(k) ? vector(v, name) : color(v, name);
        if (out.position && out.target && out.position.every((n,i) => n === out.target[i])) throw Error(`${name} position must differ from target`);
        result.lighting[name] = out;
      }
    }
    if (key === 'fog') {
      object(value, key, ['type', 'color', 'density', 'near', 'far']);
      const fog = { type: 'exponential', color: '#bcc8dd', ...value };
      color(fog.color, 'fog.color');
      if (fog.type === 'exponential') fog.density = number(fog.density ?? .02, 'fog.density', 0, 1);
      else if (fog.type === 'linear') {
        fog.near = number(fog.near ?? 10, 'fog.near', 0, 10000);
        fog.far = number(fog.far ?? 100, 'fog.far', .01, 10000);
        if (fog.near >= fog.far) throw Error('fog.far must exceed fog.near');
      } else throw Error('fog.type must be exponential or linear');
      result.fog = fog;
    }
  }
  return result;
}

export const ENVIRONMENT_API_HELP = {
  commands: ['capabilities', 'inspect', 'configure', 'clear'],
  configure: 'Payload {sky?,lighting?,fog?,exposure?,ground?}. Omitted blocks keep their current value; supplied blocks replace in full; null restores that block. Changes commit only after the sky loads. inspect reports the actual state and any load error. clear restores the scene preset. No model polling or per-frame calls.',
  sky: { type: 'atmosphere | panorama', url: 'panorama only: 2:1 equirectangular JPG/PNG/WebP, Radiance HDR or EXR; HTTP(S) with CORS or same-origin /api path. Up to 32 MB and 8192×4096.', format: 'image | hdr | exr (inferred from URL)', elevation: '-10..90 degrees', azimuth: '-360..360 degrees', turbidity: '0..20', rayleigh: '0..4', mieCoefficient: '0...1', mieDirectionalG: '0...999', rotation: 'yaw in radians', intensity: 'reflection/light strength 0..10', backgroundIntensity: 'visible sky strength 0..10', blur: '0..1', visible: 'boolean; sky can light objects while invisible', lighting: 'boolean; use sky for PBR reflections and illumination', immersive: 'default false: keep XR passthrough. true explicitly displays the sky in XR; the human can turn this off in Settings.' },
  lighting: { key: '{color:#RRGGBB,intensity:0..30,position:[x,y,z],target:[x,y,z],shadows:boolean}', fill: 'same as key', rim: 'same as key', hemi: '{sky:#RRGGBB,ground:#RRGGBB,intensity:0..30}', ambient: '{color:#RRGGBB,intensity:0..30}' },
  fog: '{type:exponential,color:#RRGGBB,density:0..1} or {type:linear,color,near,far}; null clears fog',
  exposure: '.01..8; tone mapping exposure', ground: 'boolean: show the built-in grid; custom geometry/colliders remain unchanged',
  blender: 'Build a world in Blender with ar_blender execute, then ar_blender render with options {projection:equirectangular,style:scene,position:[0,0,1.6],look_at:[0,1,1.6],size:2048}. Use the returned skybox payload with configure. The panorama is served through your hybrid host; no external image storage is needed. This is a distant backdrop: import nearby geometry as GLB with ar_blender and use ar_world/Figments for collision and interaction. Capture Spatial afterwards to judge the final appearance.',
  example: { sky: { type: 'atmosphere', elevation: 6, azimuth: 240, turbidity: 6 }, lighting: { key: { color: '#ffd6aa', intensity: 2, position: [-4,2,-3] }, ambient: { intensity: .15 } }, fog: { color: '#d5bcba', density: .015 }, exposure: .8, ground: false },
};
