/** A tiny self-contained mesh and named anchor, generated without Blender. */
export function figmentGLB(height = 1) {
  const manifest = { asset: { version: "2.0" }, scene: 0, scenes: [{ nodes: [0] }],
    nodes: [{ name: "Body", mesh: 0, children: [1] }, { name: "Handle", translation: [0, -.2, 0] }],
    meshes: [{ primitives: [{ attributes: { POSITION: 0 } }] }], buffers: [{ byteLength: 36 }],
    bufferViews: [{ buffer: 0, byteOffset: 0, byteLength: 36 }],
    accessors: [{ bufferView: 0, componentType: 5126, count: 3, type: "VEC3", min: [0,0,0], max: [1,height,0] }],
  };
  const raw = Buffer.from(JSON.stringify(manifest)), json = Buffer.alloc(Math.ceil(raw.length / 4) * 4, 32);
  raw.copy(json);
  const binary = Buffer.from(new Float32Array([0,0,0,1,0,0,0,height,0]).buffer);
  const bytes = Buffer.alloc(28 + json.length + binary.length);
  bytes.writeUInt32LE(0x46546c67, 0); bytes.writeUInt32LE(2, 4); bytes.writeUInt32LE(bytes.length, 8);
  bytes.writeUInt32LE(json.length, 12); bytes.writeUInt32LE(0x4e4f534a, 16); json.copy(bytes, 20);
  const end = 20 + json.length;
  bytes.writeUInt32LE(binary.length, end); bytes.writeUInt32LE(0x004e4942, end + 4); binary.copy(bytes, end + 8);
  return bytes;
}
