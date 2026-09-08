import assert from "node:assert/strict";
import test from "node:test";
import { build } from "esbuild";
import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { pathToFileURL } from "node:url";
import { createServer } from "node:http";

function glb() {
  const manifest = {
    asset: { version: "2.0" },
    scene: 0,
    scenes: [{ nodes: [0] }],
    nodes: [{ mesh: 0 }],
    meshes: [{ primitives: [{ attributes: { POSITION: 0 } }] }],
    buffers: [{ byteLength: 36 }],
    bufferViews: [{ buffer: 0, byteOffset: 0, byteLength: 36 }],
    accessors: [
      {
        bufferView: 0,
        componentType: 5126,
        count: 3,
        type: "VEC3",
        min: [0, 0, 0],
        max: [1, 1, 0],
      },
    ],
  };
  const raw = Buffer.from(JSON.stringify(manifest)),
    json = Buffer.alloc(Math.ceil(raw.length / 4) * 4, 32);
  raw.copy(json);
  const binary = Buffer.from(
      new Float32Array([0, 0, 0, 1, 0, 0, 0, 1, 0]).buffer,
    ),
    buffer = Buffer.alloc(12 + 8 + json.length + 8 + binary.length);
  buffer.writeUInt32LE(0x46546c67, 0);
  buffer.writeUInt32LE(2, 4);
  buffer.writeUInt32LE(buffer.length, 8);
  buffer.writeUInt32LE(json.length, 12);
  buffer.writeUInt32LE(0x4e4f534a, 16);
  json.copy(buffer, 20);
  const end = 20 + json.length;
  buffer.writeUInt32LE(binary.length, end);
  buffer.writeUInt32LE(0x004e4942, end + 4);
  binary.copy(buffer, end + 8);
  return buffer;
}

test("XR two-hand manipulation and asset replacement operate through the real renderer", async () => {
  const dir = await mkdtemp(join(tmpdir(), "ngram-lifecycle-"));
  let server;
  const previousWindow = globalThis.window;
  try {
    await build({
      stdin: {
        contents: `export {CreationWorld} from './packages/surface-webxr/src/creation-world.ts';export {CreationInput} from './packages/surface-webxr/src/creation-input.ts';export {Scene,PerspectiveCamera,Ray,Vector3} from 'three';`,
        resolveDir: resolve("."),
      },
      bundle: true,
      platform: "node",
      format: "esm",
      outfile: join(dir, "test.mjs"),
      logLevel: "silent",
    });
    const {
      CreationWorld,
      CreationInput,
      Scene,
      PerspectiveCamera,
      Ray,
      Vector3,
    } = await import(pathToFileURL(join(dir, "test.mjs")));
    globalThis.window = new EventTarget();
    const canvas = new EventTarget(),
      world = new CreationWorld(new Scene()),
      input = new CreationInput(world, new PerspectiveCamera(), canvas);
    world.store.apply({
      requestId: "create",
      operations: [
        {
          op: "entity.create",
          entity: {
            id: "orb",
            geometry: { shape: "sphere", size: [0.5, 0.5, 0.5] },
            transform: { position: [0, 1, 0] },
          },
        },
      ],
    });
    const pointer = (id, position, active, was) => ({
      id,
      position: new Vector3(...position),
      ray: new Ray(
        new Vector3(...position),
        new Vector3(0, 1, 0).sub(new Vector3(...position)).normalize(),
      ),
      isActive: active,
      wasActive: was,
      type: "hand",
    });
    assert.equal(
      input.updateXR([
        pointer("left", [-0.1, 1, 1], true, false),
        pointer("right", [0.1, 1, 1], true, false),
      ]).length,
      0,
    );
    assert.equal(world.store.locks.get("orb"), "xr:left");
    input.updateXR([
      pointer("left", [-0.2, 1, 1], true, true),
      pointer("right", [0.2, 1, 1], true, true),
    ]);
    assert.ok(Math.abs(world.sample("orb").transform.scale[0] - 2) < 0.001);
    input.updateXR([
      pointer("left", [0, 0.8, 1], true, true),
      pointer("right", [0, 1.2, 1], true, true),
    ]);
    assert.ok(
      Math.abs(world.sample("orb").transform.rotation[2] - Math.PI / 2) < 0.001,
    );
    input.updateXR([
      pointer("left", [0, 0.8, 1], true, true),
      pointer("right", [0, 1.2, 1], false, true),
    ]);
    assert.equal(world.store.locks.size, 0);
    assert.ok(world.store.events().events.some((e) => e.type === "release"));
    world.store.undo();
    assert.deepEqual(world.sample("orb").transform.scale, [1, 1, 1]);

    // A failed allocation must not delete an existing entity or expose a partial batch.
    const buildEntry = world.build.bind(world);
    world.build = (e) => {
      if (e.id === "bad") throw Error("Allocation failed");
      return buildEntry(e);
    };
    assert.throws(
      () =>
        world.store.apply({
          requestId: "atomic",
          operations: [
            { op: "entity.delete", id: "orb" },
            { op: "entity.create", entity: { id: "good" } },
            { op: "entity.create", entity: { id: "bad" } },
          ],
        }),
      /Allocation failed/,
    );
    assert.equal(world.entries.has("orb"), true);
    assert.equal(world.entries.has("good"), false);
    world.build = buildEntry;

    server = createServer((req, res) => {
      const reply = () => {
        res.writeHead(200, { "content-type": "model/gltf-binary" });
        res.end(req.url === "/invalid" ? Buffer.from("bad") : glb());
      };
      if (req.url === "/slow") setTimeout(reply, 150);
      else reply();
    });
    await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
    const base = `http://127.0.0.1:${server.address().port}`;
    const waitEvent = (type, target) =>
      new Promise((resolve, reject) => {
        const timeout = setTimeout(
          () => reject(Error(`Missing ${type}`)),
          2000,
        );
        world.store.onEvent = (e) => {
          if (e.type === type && e.target === target) {
            clearTimeout(timeout);
            resolve(e);
          }
        };
      });
    world.store.apply({
      requestId: "asset",
      operations: [
        {
          op: "entity.create",
          entity: {
            id: "model",
            kind: "asset",
            asset: { url: base + "/slow", format: "glb", fit: 1 },
          },
        },
      ],
    });
    const first = world.entries.get("model").abort,
      loaded = waitEvent("asset.ready", "model");
    world.store.apply({
      requestId: "replace",
      operations: [
        {
          op: "entity.patch",
          id: "model",
          patch: { asset: { url: base + "/fast", fit: 2 } },
        },
      ],
    });
    assert.equal(first.signal.aborted, true);
    await loaded;
    assert.equal(world.sample("model").ready, true);
    assert.ok(
      Math.abs(
        world.sample("model").bounds.max[0] -
          world.sample("model").bounds.min[0] -
          2,
      ) < 0.001,
    );
    const failed = waitEvent("asset.failed", "model");
    const visible = world.entries.get('model').visual;
    const placement = world.sample('model').transform;
    world.store.apply({
      requestId: "invalid",
      operations: [
        {
          op: "entity.patch",
          id: "model",
          patch: { asset: { url: base + "/invalid" } },
        },
      ],
    });
    await failed;
    assert.equal(world.sample("model").status, "failed");
    assert.equal(world.entries.get('model').visual, visible, 'Failed live update retains the previous mesh');
    assert.deepEqual(world.sample('model').transform, placement, 'Updates retain the human placement');
    world.store.apply({
      requestId: "remove",
      operations: [{ op: "entity.delete", id: "model" }],
    });
    assert.equal(world.entries.has("model"), false);
  } finally {
    globalThis.window = previousWindow;
    if (server) {
      server.closeAllConnections();
      await new Promise((resolve) => server.close(resolve));
    }
    await rm(dir, { recursive: true, force: true });
  }
});
