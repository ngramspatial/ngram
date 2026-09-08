import assert from "node:assert/strict";
import test from "node:test";
import { build } from "esbuild";
import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { pathToFileURL } from "node:url";

test("actual Rapier mechanisms move, impulses affect velocity, gravity is scoped, pause freezes", async () => {
  const dir = await mkdtemp(join(tmpdir(), "ngram-world-"));
  try {
    await build({
      stdin: {
        contents: `export {CreationWorld} from './packages/surface-webxr/src/creation-world.ts';export {initPhysics,stepPhysics} from './packages/surface-webxr/src/physics-world.ts';export {Scene} from 'three';`,
        resolveDir: resolve("."),
      },
      bundle: true,
      platform: "node",
      format: "esm",
      outfile: join(dir, "test.mjs"),
      logLevel: "silent",
    });
    const { CreationWorld, initPhysics, stepPhysics, Scene } = await import(
      pathToFileURL(join(dir, "test.mjs"))
    );
    await initPhysics();
    const world = new CreationWorld(new Scene());
    const shape = (id, position, physics) => ({
      op: "entity.create",
      entity: { id, transform: { position }, physics },
    });
    world.store.apply({
      requestId: "mechanism",
      operations: [
        shape("pivot", [0, 2, 0], { mode: "fixed" }),
        shape("pendulum", [0.5, 1, 0], { mode: "dynamic" }),
        shape("orb", [3, 2, 0], { mode: "dynamic", gravity: [0, 0, 0] }),
        {
          op: "joint.create",
          joint: {
            id: "rope",
            type: "rope",
            a: "pivot",
            b: "pendulum",
            length: 1.2,
          },
        },
      ],
    });
    const start = world.sample("pendulum").transform.position;
    for (let i = 0; i < 120; i++) {
      world.update(1 / 60);
      stepPhysics(1 / 60);
    }
    const p = world.sample("pendulum").transform.position;
    assert.notDeepEqual(p, start);
    assert.ok(Math.hypot(p[0], p[1] - 2, p[2]) < 1.25);
    assert.ok(Math.abs(world.sample("orb").transform.position[1] - 2) < 0.001);
    world.store.apply({
      requestId: "push",
      operations: [{ op: "body.impulse", id: "orb", impulse: [2, 0, 0] }],
    });
    assert.ok(world.sample("orb").velocity[0] > 1.9);
    world.pause(true);
    const paused = world.sample("orb").transform.position;
    for (let i = 0; i < 60; i++) {
      world.update(1 / 60);
      stepPhysics(1 / 60);
    }
    assert.deepEqual(world.sample("orb").transform.position, paused);
    world.pause(false);
    world.update(1 / 60);
    stepPhysics(1 / 60);
    assert.ok(world.sample("orb").transform.position[0] > paused[0]);
    world.store.apply({
      requestId: "delete",
      operations: [{ op: "entity.delete", id: "pivot" }],
    });
    assert.equal(world.joints.size, 0);
    assert.equal(world.entries.has("pivot"), false);
  } finally {
    await rm(dir, { recursive: true, force: true });
  }
});
