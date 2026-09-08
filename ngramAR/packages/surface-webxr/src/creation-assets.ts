// @ts-nocheck
import * as THREE from "three";
import { GLTFLoader } from "three/addons/loaders/GLTFLoader.js";
import { readFigmentAsset } from "./figment-library.js";
import { validateGLB } from "@ngram-ar/core";

export function disposeCreationAsset(node) {
  const textures = new Set(),
    geometries = new Set(),
    materials = new Set();
  node.traverse((child) => {
    if (child.geometry) geometries.add(child.geometry);
    for (const m of Array.isArray(child.material)
      ? child.material
      : [child.material])
      if (m) {
        materials.add(m);
        for (const value of Object.values(m))
          if (value?.isTexture) textures.add(value);
      }
  });
  for (const t of textures) {
    t.image?.close?.();
    t.dispose();
  }
  for (const g of geometries) g.dispose();
  for (const m of materials) m.dispose();
}

export async function loadCreationAsset(asset, signal, onProgress) {
  const maxBytes = 32 * 1024 * 1024;
  const response = asset.url.startsWith("figment:")
    ? new Response(await readFigmentAsset(asset.url, { signal }))
    : await fetch(asset.url, { signal, credentials: "same-origin" });
  if (!response.ok) throw Error(`Asset request failed (${response.status})`);
  const total = Number(response.headers.get("content-length")) || 0;
  if (total > maxBytes) {
    await response.body?.cancel();
    throw Error("Asset exceeds 32 MB");
  }
  const reader = response.body?.getReader();
  if (!reader) throw Error("Asset response has no body");
  let size = 0;
  const chunks = [];
  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      size += value.length;
      if (size > maxBytes) throw Error("Asset exceeds 32 MB");
      chunks.push(value);
      onProgress({ bytes: size, total: total || null });
    }
  } catch (error) {
    await reader.cancel();
    throw error;
  }
  signal.throwIfAborted();
  const bytes = new Uint8Array(size);
  let offset = 0;
  for (const chunk of chunks) {
    bytes.set(chunk, offset);
    offset += chunk.length;
  }
  if (asset.format === "image") {
    const bitmap = await createImageBitmap(new Blob([bytes]));
    if (signal.aborted || bitmap.width > 4096 || bitmap.height > 4096) {
      bitmap.close();
      signal.throwIfAborted();
      throw Error("Images must be at most 4096 × 4096");
    }
    const texture = new THREE.Texture(bitmap);
    texture.needsUpdate = true;
    texture.colorSpace = THREE.SRGBColorSpace;
    const mesh = new THREE.Mesh(
      new THREE.PlaneGeometry(
        asset.fit,
        (asset.fit * bitmap.height) / bitmap.width,
      ),
      new THREE.MeshBasicMaterial({
        map: texture,
        side: THREE.DoubleSide,
        transparent: true,
      }),
    );
    return mesh;
  }
  validateGLB(bytes);
  const gltf = await new GLTFLoader().parseAsync(bytes.buffer, "");
  if (signal.aborted) {
    disposeCreationAsset(gltf.scene);
    signal.throwIfAborted();
  }
  const box = new THREE.Box3().setFromObject(gltf.scene),
    extent = box.getSize(new THREE.Vector3());
  const dimension = Math.max(extent.x, extent.y, extent.z);
  if (!Number.isFinite(dimension) || dimension <= 0) {
    disposeCreationAsset(gltf.scene);
    throw Error("Asset has no renderable bounds");
  }
  const container = new THREE.Group();
  const center = box.getCenter(new THREE.Vector3());
  if (asset.normalize !== false) gltf.scene.position.sub(center);
  container.add(gltf.scene);
  container.scale.setScalar(asset.normalize === false ? asset.fit : asset.fit / dimension);
  container.userData.animationClips = gltf.animations;
  return container;
}
