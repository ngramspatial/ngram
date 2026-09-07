/**
 * Mixamo FBX → GLB converter (Node.js, no Blender required)
 *
 * Usage:
 *   node scripts/convert-mixamo.mjs <character.fbx> <output.glb> [animDir]
 *
 * Example:
 *   node scripts/convert-mixamo.mjs chars/YBot/Y\ Bot.fbx shells/my-entity/models/character.glb chars/YBot/anims
 */

import { readFileSync, writeFileSync, readdirSync, mkdirSync } from "fs";
import { join, basename, extname, dirname, resolve } from "path";

// ── Browser polyfills required by Three.js in Node.js ───────────────────────

if (typeof globalThis.document === "undefined") {
  const noop = () => {};
  const noopObj = () => ({
    style: {},
    setAttribute: noop,
    getAttribute: () => null,
    addEventListener: noop,
    removeEventListener: noop,
    getContext: () => null,
    width: 0,
    height: 0,
    parentNode: null,
    appendChild: noop,
    removeChild: noop,
    childNodes: [],
    ownerDocument: null,
    getBoundingClientRect: () => ({ left: 0, top: 0, width: 0, height: 0 }),
  });

  globalThis.document = {
    createElementNS: (ns, tag) => noopObj(),
    createElement: (tag) => {
      if (tag === "canvas") {
        return {
          ...noopObj(),
          getContext: () => ({
            fillStyle: "",
            fillRect: noop,
            drawImage: noop,
            getImageData: () => ({ data: new Uint8Array(4) }),
            putImageData: noop,
            canvas: { width: 0, height: 0, toDataURL: () => "" },
          }),
          toDataURL: () => "",
          toBlob: (cb) => cb(new Blob()),
          width: 256,
          height: 256,
        };
      }
      return noopObj();
    },
    body: { appendChild: noop, removeChild: noop },
    head: { appendChild: noop },
    documentElement: { style: {} },
    addEventListener: noop,
    removeEventListener: noop,
    createComment: () => ({}),
  };
}

if (typeof globalThis.window === "undefined") {
  globalThis.window = {
    innerWidth: 1024,
    innerHeight: 768,
    addEventListener: () => {},
    removeEventListener: () => {},
    navigator: { userAgent: "node" },
    document: globalThis.document,
    URL: globalThis.URL,
    location: { protocol: "https:", host: "localhost" },
  };
}

if (typeof globalThis.self === "undefined") {
  globalThis.self = globalThis;
}

if (typeof globalThis.navigator === "undefined") {
  globalThis.navigator = { userAgent: "node", platform: "node" };
}

if (typeof globalThis.HTMLCanvasElement === "undefined") {
  globalThis.HTMLCanvasElement = class HTMLCanvasElement {};
}

if (typeof globalThis.OffscreenCanvas === "undefined") {
  globalThis.OffscreenCanvas = class OffscreenCanvas {
    constructor(w, h) {
      this.width = w;
      this.height = h;
    }
    getContext() {
      return null;
    }
  };
}

if (typeof globalThis.requestAnimationFrame === "undefined") {
  globalThis.requestAnimationFrame = (cb) => setTimeout(cb, 16);
}

if (typeof globalThis.DOMParser === "undefined") {
  globalThis.DOMParser = class {
    parseFromString() {
      return { documentElement: {} };
    }
  };
}

if (typeof globalThis.Image === "undefined") {
  globalThis.Image = class {
    set src(v) {}
    addEventListener() {}
    removeEventListener() {}
  };
}

if (typeof globalThis.createImageBitmap === "undefined") {
  globalThis.createImageBitmap = async () => ({
    width: 1,
    height: 1,
    close: () => {},
  });
}

if (typeof globalThis.FileReader === "undefined") {
  globalThis.FileReader = class FileReader {
    constructor() {
      this.result = null;
      this.onload = null;
      this.onerror = null;
    }
    readAsArrayBuffer(blob) {
      blob.arrayBuffer().then((buf) => {
        this.result = buf;
        if (this.onload) this.onload({ target: this });
      }).catch((err) => {
        if (this.onerror) this.onerror(err);
      });
    }
    readAsDataURL(blob) {
      blob.arrayBuffer().then((buf) => {
        const b64 = Buffer.from(buf).toString("base64");
        const type = blob.type || "application/octet-stream";
        this.result = `data:${type};base64,${b64}`;
        if (this.onload) this.onload({ target: this });
      }).catch((err) => {
        if (this.onerror) this.onerror(err);
      });
    }
    addEventListener(ev, fn) {
      if (ev === "load") this.onload = fn;
      if (ev === "error") this.onerror = fn;
    }
  };
}

// ── Imports ─────────────────────────────────────────────────────────────────

const THREE = await import("three");
const { FBXLoader } = await import("three/addons/loaders/FBXLoader.js");
const { GLTFExporter } = await import("three/addons/exporters/GLTFExporter.js");

// ── Helpers ─────────────────────────────────────────────────────────────────

function loadFBX(filePath) {
  const buffer = readFileSync(filePath);
  const arrayBuffer = buffer.buffer.slice(
    buffer.byteOffset,
    buffer.byteOffset + buffer.byteLength
  );
  const loader = new FBXLoader();
  return loader.parse(arrayBuffer, dirname(resolve(filePath)) + "/");
}

function clipNameFromFile(filePath) {
  return basename(filePath, extname(filePath)).replace(/\s+/g, "_");
}

// ── Main ────────────────────────────────────────────────────────────────────

const args = process.argv.slice(2);

if (args.length < 2) {
  console.log(
    "Usage: node scripts/convert-mixamo.mjs <character.fbx> <output.glb> [animDir]"
  );
  process.exit(1);
}

const characterPath = resolve(args[0]);
const outputPath = resolve(args[1]);
const animDir = args[2] ? resolve(args[2]) : null;

console.log(`\nLoading character: ${characterPath}`);
const character = loadFBX(characterPath);

const charClipName = clipNameFromFile(characterPath);
if (character.animations.length > 0) {
  character.animations[0].name = charClipName;
  console.log(
    `  Character has ${character.animations.length} animation(s), renamed first to "${charClipName}"`
  );
}

if (animDir) {
  const fbxFiles = readdirSync(animDir)
    .filter((f) => f.toLowerCase().endsWith(".fbx"))
    .sort();

  console.log(`\nFound ${fbxFiles.length} animation files in ${animDir}:`);

  for (const file of fbxFiles) {
    const filePath = join(animDir, file);
    const clipName = clipNameFromFile(file);
    console.log(`  Loading ${file} → "${clipName}"`);

    try {
      const animGroup = loadFBX(filePath);
      for (const clip of animGroup.animations) {
        clip.name = clipName;
        character.animations.push(clip);
        console.log(
          `    Added clip "${clipName}" (${clip.duration.toFixed(2)}s, ${clip.tracks.length} tracks)`
        );
      }
    } catch (err) {
      console.error(`    ERROR loading ${file}: ${err.message}`);
    }
  }
}

console.log(`\nTotal animation clips: ${character.animations.length}`);
for (const clip of character.animations) {
  console.log(`  ${clip.name} (${clip.duration.toFixed(2)}s)`);
}

// Convert Phong/Lambert materials to Standard and strip textures
// (textures require canvas rendering which isn't available in Node.js)
character.traverse((child) => {
  if (!child.isMesh) return;
  const mats = Array.isArray(child.material) ? child.material : [child.material];
  const converted = mats.map((mat) => {
    const std = new THREE.MeshStandardMaterial({
      name: mat.name,
      color: mat.color?.clone() ?? new THREE.Color(0x888888),
      roughness: 0.6,
      metalness: 0.2,
      transparent: mat.transparent ?? false,
      opacity: mat.opacity ?? 1,
      side: mat.side ?? THREE.FrontSide,
    });
    if (mat.emissive) std.emissive = mat.emissive.clone();
    return std;
  });
  child.material = converted.length === 1 ? converted[0] : converted;
});

console.log(`\nExporting GLB to: ${outputPath}`);

mkdirSync(dirname(outputPath), { recursive: true });

const exporter = new GLTFExporter();

const glb = await new Promise((resolve, reject) => {
  const timeout = setTimeout(() => reject(new Error("Export timed out")), 30000);
  exporter.parse(
    character,
    (result) => { clearTimeout(timeout); resolve(result); },
    (err) => { clearTimeout(timeout); reject(err); },
    {
      binary: true,
      animations: character.animations,
    }
  );
});

writeFileSync(outputPath, Buffer.from(glb));
const sizeMB = (readFileSync(outputPath).length / 1024 / 1024).toFixed(2);
console.log(`\nDone! ${outputPath} (${sizeMB} MB)`);
