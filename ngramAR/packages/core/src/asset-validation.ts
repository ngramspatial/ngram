/** Bounded validation shared by the renderer and portable Figment importer. */
export function validateGLB(bytes: Uint8Array): Record<string, any> {
  if (bytes.length < 20) throw Error("Invalid GLB header");
  const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  if (view.getUint32(0, true) !== 0x46546c67 || view.getUint32(4, true) !== 2 || view.getUint32(8, true) !== bytes.length || view.getUint32(16, true) !== 0x4e4f534a) throw Error("Expected a glTF 2.0 GLB");
  const jsonSize = view.getUint32(12, true);
  if (jsonSize > bytes.length - 20 || jsonSize > 8 * 1024 * 1024) throw Error("Invalid or excessive GLB JSON chunk");
  const manifest = JSON.parse(new TextDecoder().decode(bytes.subarray(20, 20 + jsonSize)));
  if (!manifest || typeof manifest !== "object" || manifest.asset?.version !== "2.0") throw Error("Invalid glTF manifest");
  const inspect = (value: any, depth = 0) => {
    if (depth > 64) throw Error("GLB manifest is too deeply nested");
    if (!value || typeof value !== "object") return;
    for (const [key, nested] of Object.entries(value)) {
      if (key === "uri" && (typeof nested !== "string" || !nested.startsWith("data:"))) throw Error("Use self-contained GLB assets with embedded textures and buffers");
      inspect(nested, depth + 1);
    }
  };
  inspect(manifest);
  let vertices = 0;
  for (const mesh of manifest.meshes ?? []) for (const primitive of mesh.primitives ?? []) {
    const count = manifest.accessors?.[primitive.attributes?.POSITION]?.count;
    if (!Number.isInteger(count) || count < 0) throw Error("Invalid GLB geometry");
    vertices += count;
  }
  if (vertices > 100000) throw Error("Asset exceeds 100,000 source vertices");
  return manifest;
}
