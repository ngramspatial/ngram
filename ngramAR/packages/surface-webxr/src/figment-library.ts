// @ts-nocheck
import { WorldStore, WORLD_PROTOCOL, fields, object, identifier, vector, validateGLB } from "@ngram-ar/core";

export const FIGMENT_PACKAGE_PROTOCOL = "ngram.figment.package/1";
export const FIGMENT_PACKAGE_LIMIT = 192 * 1024 * 1024;
const RAW_LIMIT = 128 * 1024 * 1024;
const ASSET_LIMIT = 32 * 1024 * 1024;

/** Binary content belongs in IndexedDB, never the world's localStorage snapshot. */
export class FigmentStorage {
  db = null;
  async database() {
    if (!this.db) this.db = new Promise((resolve, reject) => {
      const request = indexedDB.open("ngram_figments_v1", 2);
      request.onupgradeneeded = () => {
        if (!request.result.objectStoreNames.contains("assets")) request.result.createObjectStore("assets");
        const versions = request.result.objectStoreNames.contains("versions") ? request.transaction.objectStore("versions") : request.result.createObjectStore("versions");
        if (!versions.indexNames.contains("edition")) versions.createIndex("edition", ["title", "version"], { unique: true });
      };
      request.onsuccess = () => {
        request.result.onversionchange = () => { request.result.close(); this.db = null; };
        resolve(request.result);
      };
      request.onerror = () => { this.db = null; reject(request.error); };
    });
    return this.db;
  }
  async get(store, key) {
    const db = await this.database();
    return new Promise((resolve, reject) => {
      const request = db.transaction(store).objectStore(store).get(key);
      request.onsuccess = () => resolve(request.result);
      request.onerror = () => reject(request.error);
    });
  }
  async all() {
    const db = await this.database();
    return new Promise((resolve, reject) => {
      const request = db.transaction("versions").objectStore("versions").getAll();
      request.onsuccess = () => resolve(request.result);
      request.onerror = () => reject(request.error);
    });
  }
  async commit(assets, version, signal) {
    const db = await this.database();
    signal?.throwIfAborted();
    return new Promise((resolve, reject) => {
      const transaction = db.transaction(["assets", "versions"], "readwrite");
      const abort = () => { try { transaction.abort(); } catch {} };
      signal?.addEventListener("abort", abort, { once: true });
      for (const asset of assets) transaction.objectStore("assets").put(asset, asset.hash);
      if (version) transaction.objectStore("versions").put(version, version.id);
      transaction.oncomplete = () => { signal?.removeEventListener("abort", abort); resolve(); };
      transaction.onerror = transaction.onabort = () => {
        signal?.removeEventListener("abort", abort);
        reject(signal?.aborted ? signal.reason : transaction.error?.name === "ConstraintError" ? Error("That title/version is already published. Increase the version.") : transaction.error ?? Error("Figment storage failed"));
      };
    });
  }
}
export const figmentStorage = new FigmentStorage();
export async function digest(bytes) {
  const hash = await crypto.subtle.digest("SHA-256", bytes);
  return [...new Uint8Array(hash)].map(n => n.toString(16).padStart(2, "0")).join("");
}
function encode(bytes) {
  let binary = "";
  for (let offset = 0; offset < bytes.length; offset += 32768)
    binary += String.fromCharCode(...bytes.subarray(offset, offset + 32768));
  return btoa(binary);
}
function decode(data) {
  if (typeof data !== "string" || data.length > Math.ceil(RAW_LIMIT / 3) * 4 || data.length % 4 || /[^A-Za-z0-9+/=]/.test(data) || (data.includes("=") && !/^[^=]*={1,2}$/.test(data))) throw Error("Invalid packaged asset data");
  return Uint8Array.from(atob(data), c => c.charCodeAt(0));
}
export async function readFigmentAsset(url, { signal, maxBytes = ASSET_LIMIT, storage = figmentStorage } = {}) {
  signal?.throwIfAborted();
  if (url.startsWith("figment:")) {
    const asset = await storage.get("assets", url.slice(8));
    if (!asset) throw Error("Packaged asset is missing. Import its Figment package again.");
    const bytes = new Uint8Array(asset.bytes);
    if (bytes.length > maxBytes) throw Error("Packaged asset exceeds size limit");
    signal?.throwIfAborted();
    return bytes;
  }
  const response = await fetch(url, { signal, credentials: "same-origin" });
  if (!response.ok) throw Error(`Asset request failed (${response.status})`);
  if (Number(response.headers.get("content-length")) > maxBytes) {
    await response.body?.cancel();
    throw Error("Asset exceeds package size limit");
  }
  const reader = response.body?.getReader();
  if (!reader) throw Error("Asset response has no body");
  let size = 0;
  const chunks = [];
  try {
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      size += value.length;
      if (size > maxBytes) throw Error("Asset exceeds package size limit");
      chunks.push(value);
    }
  } catch (error) { await reader.cancel(); throw error; }
  const bytes = new Uint8Array(size);
  let offset = 0;
  for (const chunk of chunks) { bytes.set(chunk, offset); offset += chunk.length; }
  return bytes;
}
function unsigned(bundle) {
  return { protocol: bundle.protocol, root: bundle.root, world: bundle.world, assets: bundle.assets };
}
const packageDigest = bundle => digest(new TextEncoder().encode(JSON.stringify(unsigned(bundle))));

export function figmentScope(document, rootId) {
  const root = document.entities.find(e => e.id === rootId);
  if (!root?.figment) throw Error("Select an attached Figment");
  const ids = new Set([rootId, ...Object.values(root.figment.parts)]);
  let changed = true;
  while (changed) {
    changed = false;
    for (const e of document.entities) if (e.parent && ids.has(e.parent) && !ids.has(e.id)) { ids.add(e.id); changed = true; }
  }
  for (const e of document.entities.filter(e => ids.has(e.id))) {
    if (e.parent && !ids.has(e.parent)) throw Error(`Part ${e.id} belongs to an external group; include that group`);
    if (e.id !== rootId && e.figment) throw Error("Nested Figments must be published separately");
  }
  return ids;
}

/** Remap an assembly without rewriting authored JavaScript: code uses named bindings. */
export function copyFigment(document, rootId, position) {
  const ids = figmentScope(document, rootId);
  const source = document.entities.find(e => e.id === rootId);
  const origin = source.transform.position;
  const destination = vector(position, origin);
  const entities = structuredClone(document.entities.filter(e => ids.has(e.id)));
  const joints = document.joints.filter(j => ids.has(j.a) && ids.has(j.b));
  const prefix = `f-${crypto.randomUUID().slice(0, 12)}`;
  const map = new Map(entities.map((e, i) => [e.id, `${prefix}-${i}`]));
  const jointMap = new Map(joints.map((j, i) => [j.id, `${prefix}-j${i}`]));
  for (const e of entities) {
    e.id = map.get(e.id);
    e.parent = e.parent ? map.get(e.parent) : null;
    if (!e.parent) e.transform.position = e.transform.position.map((n, i) => n - origin[i] + destination[i]);
    if (e.figment) {
      e.figment.parts = Object.fromEntries(Object.entries(e.figment.parts).map(([name, id]) => [name, map.get(id)]));
      e.figment.joints = Object.fromEntries(Object.entries(e.figment.joints).map(([name, id]) => [name, jointMap.get(id)]));
    }
  }
  return { root: map.get(rootId), operations: [
    ...entities.map(entity => ({ op: "entity.create", entity })),
    ...joints.map(j => ({ op: "joint.create", joint: { ...j, id: jointMap.get(j.id), a: map.get(j.a), b: map.get(j.b) } })),
  ] };
}

export class FigmentLibrary {
  constructor(storage = figmentStorage, read = readFigmentAsset) { this.storage = storage; this.read = read; }
  async list() {
    return (await this.storage.all()).map(v => ({ id: v.id, title: v.title, version: v.version, description: v.description, bytes: v.bytes, editableSource: v.editableSource }));
  }
  async publish(document, rootId, signal) {
    const ids = figmentScope(document, rootId);
    const entities = structuredClone(document.entities.filter(e => ids.has(e.id)));
    const root = entities.find(e => e.id === rootId);
    const origin = [...root.transform.position];
    for (const e of entities) if (!e.parent) e.transform.position = e.transform.position.map((n, i) => n - origin[i]);
    const assets = [], cached = new Map();
    let total = 0;
    const include = async (url, type) => {
      if (cached.has(url)) return cached.get(url);
      const bytes = await this.read(url, { signal, storage: this.storage, maxBytes: type === "blend" ? RAW_LIMIT : ASSET_LIMIT });
      total += bytes.length;
      if (total > RAW_LIMIT) throw Error("Figment assets exceed 128 MB combined");
      const hash = await digest(bytes);
      if (!assets.some(a => a.hash === hash)) assets.push({ hash, type, data: encode(bytes) });
      cached.set(url, `figment:${hash}`);
      return `figment:${hash}`;
    };
    for (const e of entities) if (e.asset) e.asset.url = await include(e.asset.url, e.asset.format);
    if (root.figment.source) root.figment.source.url = await include(root.figment.source.url, "blend");
    const bundle = { protocol: FIGMENT_PACKAGE_PROTOCOL, root: rootId, world: { protocol: WORLD_PROTOCOL, id: "figment", name: root.figment.title, revision: 0, entities, joints: structuredClone(document.joints.filter(j => ids.has(j.a) && ids.has(j.b))) }, assets };
    bundle.id = await packageDigest(bundle);
    signal?.throwIfAborted();
    return this.store(bundle, signal);
  }
  async verify(raw, signal) {
    signal?.throwIfAborted();
    if (typeof raw === "string" && raw.length > FIGMENT_PACKAGE_LIMIT) throw Error("Figment package exceeds 192 MB");
    const bundle = typeof raw === "string" ? JSON.parse(raw) : structuredClone(raw);
    if (JSON.stringify(bundle).length > FIGMENT_PACKAGE_LIMIT) throw Error("Figment package exceeds 192 MB");
    fields(object(bundle, "package"), ["protocol", "id", "root", "world", "assets"], "package");
    if (bundle.protocol !== FIGMENT_PACKAGE_PROTOCOL || !/^[a-f0-9]{64}$/.test(bundle.id)) throw Error("Unsupported or invalid Figment package");
    if (!Array.isArray(bundle.assets) || bundle.assets.length > 9) throw Error("Invalid package assets");
    const checked = new WorldStore({ commit() {} });
    checked.restore(bundle.world, "validation", false);
    const scope = figmentScope(checked.document, identifier(bundle.root));
    if (scope.size !== checked.document.entities.length) throw Error("Package contains objects outside its Figment");
    const root = checked.document.entities.find(e => e.id === bundle.root);
    const references = checked.document.entities.filter(e => e.asset).map(e => ({ url: e.asset.url, type: e.asset.format }));
    if (root.figment.source) references.push({ url: root.figment.source.url, type: "blend" });
    const hashes = new Set(), binaries = [];
    let total = 0;
    for (const a of bundle.assets) {
      fields(object(a, "packaged asset"), ["hash", "type", "data"], "packaged asset");
      if (!/^[a-f0-9]{64}$/.test(a.hash) || hashes.has(a.hash) || !["glb", "image", "blend"].includes(a.type)) throw Error("Invalid or duplicate asset hash/type");
      hashes.add(a.hash);
      const bytes = decode(a.data);
      total += bytes.length;
      if (total > RAW_LIMIT || (a.type !== "blend" && bytes.length > ASSET_LIMIT)) throw Error("Package asset size limit exceeded");
      if (await digest(bytes) !== a.hash) throw Error("Packaged asset integrity check failed");
      signal?.throwIfAborted();
      if (a.type === "glb") validateGLB(bytes);
      if (a.type === "image" && typeof createImageBitmap === "function") {
        const bitmap = await createImageBitmap(new Blob([bytes]));
        const valid = bitmap.width <= 4096 && bitmap.height <= 4096;
        bitmap.close();
        if (!valid) throw Error("Packaged images must be at most 4096 × 4096");
      }
      if (a.type === "blend" && new TextDecoder().decode(bytes.subarray(0, 7)) !== "BLENDER" &&
        !(bytes[0] === 0x1f && bytes[1] === 0x8b && bytes[2] === 0x08) &&
        !(bytes[0] === 0x28 && bytes[1] === 0xb5 && bytes[2] === 0x2f && bytes[3] === 0xfd)) throw Error("Editable source must be a .blend file");
      binaries.push({ hash: a.hash, type: a.type, bytes });
    }
    for (const r of references) {
      if (!/^figment:[a-f0-9]{64}$/.test(r.url)) throw Error("Portable Figments cannot reference external assets");
      if (!bundle.assets.some(a => r.url === `figment:${a.hash}` && r.type === a.type)) throw Error("Package is missing a referenced asset");
    }
    if (hashes.size !== new Set(references.map(r => r.url)).size) throw Error("Package contains unreferenced assets");
    if (await packageDigest(bundle) !== bundle.id) throw Error("Figment package integrity check failed");
    signal?.throwIfAborted();
    return { bundle, binaries, root, total };
  }
  async store(raw, signal) {
    const { bundle, binaries, root, total } = await this.verify(raw, signal);
    const existing = (await this.storage.all()).find(v => v.title === root.figment.title && v.version === root.figment.version);
    if (existing && existing.id !== bundle.id) throw Error("That title/version is already published. Increase the Figment version to publish changes.");
    const manifest = { ...unsigned(bundle), assets: bundle.assets.map(({ data, ...meta }) => meta), id: bundle.id };
    const version = { id: bundle.id, title: root.figment.title, version: root.figment.version, description: root.figment.description, bytes: total, editableSource: !!root.figment.source, manifest };
    signal?.throwIfAborted();
    await this.storage.commit(binaries, version, signal);
    return { id: version.id, title: version.title, version: version.version, bytes: total, editableSource: version.editableSource, published: true, location: "local-library" };
  }
  async export(id, signal) {
    const version = await this.storage.get("versions", id);
    if (!version) throw Error("Unknown published Figment");
    const bundle = structuredClone(version.manifest);
    for (const asset of bundle.assets) {
      const stored = await this.storage.get("assets", asset.hash);
      if (!stored) throw Error("Published asset is missing");
      asset.data = encode(new Uint8Array(stored.bytes));
      signal?.throwIfAborted();
    }
    return bundle;
  }
  async placement(id, position = [0, 1, -1], signal) {
    const bundle = await this.export(id, signal);
    // Validate at the boundary as well as import; corrupted browser storage never enters the scene.
    await this.verify(bundle, signal);
    return copyFigment(bundle.world, bundle.root, position);
  }
}
