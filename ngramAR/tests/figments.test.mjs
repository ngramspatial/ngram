import assert from "node:assert/strict";
import test, { after } from "node:test";
import { build } from "esbuild";
import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join, resolve, sep } from "node:path";
import { pathToFileURL } from "node:url";
import { runInNewContext } from "node:vm";
import { WorldStore, parseEntity, parseFigment, parseBody } from "@ngram-ar/core";
import { figmentGLB } from "./figment-fixture-assets.mjs";

const dir = await mkdtemp(join(tmpdir(), "ngram-figments-"));
after(async () => {
  assert.ok(resolve(dir).startsWith(resolve(tmpdir()) + sep));
  await rm(dir, { recursive: true, force: true });
});
await build({ stdin: { contents: `
export {CreationWorld} from './packages/surface-webxr/src/creation-world.ts';
export {CreationInput} from './packages/surface-webxr/src/creation-input.ts';
export {CreationPrograms,workerBootstrap} from './packages/surface-webxr/src/creation-programs.ts';
export {FigmentService} from './packages/surface-webxr/src/figment-service.ts';
export {FigmentLibrary,readFigmentAsset} from './packages/surface-webxr/src/figment-library.ts';
export {FIGMENT_PRESETS} from './packages/surface-webxr/src/figment-presets.ts';
export {resolveFigmentAnchors,poseAtGrip} from './packages/surface-webxr/src/figment-anchors.ts';
export {initPhysics,stepPhysics,getWorld} from './packages/surface-webxr/src/physics-world.ts';
export {Scene,Vector3,Quaternion,PerspectiveCamera,Ray,Group,Mesh,BoxGeometry,MeshBasicMaterial} from 'three';`, resolveDir: resolve(".") }, bundle: true, platform: "node", format: "esm", outfile: join(dir, "suite.mjs"), logLevel: "silent" });
const m = await import(pathToFileURL(join(dir, "suite.mjs")));
const apply = (world, operations, actor) => world.store.apply({ requestId: crypto.randomUUID(), operations }, actor);
const create = (id, extra = {}) => ({ op: "entity.create", entity: { id, ...extra } });
const patch = (id, patch) => ({ op: "entity.patch", id, patch });
const near = (actual, expected, epsilon = .002) => assert.ok(Math.abs(actual - expected) < epsilon, `${actual} != ${expected}`);

class MemoryStorage {
  assets = new Map(); versions = new Map();
  async get(table, key) { return structuredClone(this[table].get(key)); }
  async all() { return structuredClone([...this.versions.values()]); }
  async commit(assets, version) {
    for (const asset of assets) this.assets.set(asset.hash, structuredClone(asset));
    if (version) this.versions.set(version.id, structuredClone(version));
  }
}

test("Figment contracts reject invalid physics, dangling anchors/parts and property values atomically", () => {
  assert.throws(() => parseFigment({ grips: { hold: { anchor: "missing" } } }), /Unknown grip anchor/);
  assert.throws(() => parseFigment({ anchors: { grip: { part: "missing" } } }), /Unknown anchor part/);
  assert.throws(() => parseFigment({ properties: { speed: { type: "number", value: 12, min: 0, max: 10 } } }), /finite/);
  assert.throws(() => parseFigment(JSON.parse('{"properties":{"__proto__":{"value":2}}}')), /Reserved|ID must/);
  assert.throws(() => parseBody({ rotations: [true, 0, true] }), /boolean/);
  assert.throws(() => parseBody({ colliders: [{ id: "x" }, { id: "x" }] }), /unique/);
  assert.throws(() => parseBody({ mass: -1 }), /finite/);
  const store = new WorldStore({ commit() {} });
  assert.throws(() => store.apply({ requestId: "bad", operations: [create("lamp", { figment: { parts: { bulb: "missing" } } })] }), /Missing Figment part/);
  assert.equal(store.document.entities.length, 0);
  const body = parseEntity({ id: "asset", kind: "asset", asset: { url: "/mesh.glb" }, physics: { colliders: [{ id: "main", shape: "box" }] } });
  assert.equal(body.physics.colliders.length, 1);
});

test("real Rapier compound bodies respect mass, axis locks, gravity, damping and sensors", async () => {
  await m.initPhysics();
  const world = new m.CreationWorld(new m.Scene());
  try {
    apply(world, [
      create("compound", { transform: { position: [0, 2, 0] }, physics: { mass: 4, gravity: [0,0,0], linearDamping: 0, rotations: [false,false,false], colliders: [
        { id: "left", size: [.2,.2,.2], position: [-.2,0,0] }, { id: "right", size: [.2,.2,.2], position: [.2,0,0] },
      ] } }),
      create("sensor", { transform: { position: [3, 2, 0] }, physics: { mode: "fixed", colliders: [{ id: "field", size: [1,1,1], sensor: true }] } }),
      create("visitor", { transform: { position: [3,2,0] }, physics: { gravity: [0,0,0], damping: 0 } }),
    ]);
    near(world.sample("compound").mass, 4);
    apply(world, [{ op: "body.impulse", id: "compound", impulse: [4,0,0], torque: [1,2,3] }]);
    near(world.sample("compound").velocity[0], 1);
    for (let i=0;i<3;i++) { world.update(1/60); m.stepPhysics(1/60); }
    assert.ok(world.store.events().events.some(e => e.type === "sensor.enter" && e.target === "sensor" && e.data.other === "visitor" && e.data.collider === "field"));
    assert.ok(world.store.events().events.some(e => e.type === "sensor.enter" && e.target === "visitor"));
    const rotation = world.sample("compound").transform.rotation;
    rotation.forEach(n => near(n, 0));
    const pose = world.sample("compound").transform.position;
    apply(world, [patch("compound", { physics: { mass: 8, damping: 3, gravity: [0,-2,0], translations: [false,true,true] } })]);
    world.sample("compound").transform.position.forEach((n,i) => near(n, pose[i]));
    near(world.sample("compound").mass, 8);
    near(world.entries.get("compound").body.linearDamping(), 3);
    apply(world, [patch("visitor", { transform: { position: [5,2,0] } })]);
    for (let i=0;i<10;i++) { world.update(1/60); m.stepPhysics(1/60); }
    assert.ok(world.store.events().events.some(e => e.type === "sensor.exit" && e.target === "sensor"));
    assert.ok(world.sample("compound").transform.position[1] < pose[1]);
    near(world.sample("compound").transform.position[0], pose[0]);
  } finally { world.unsubscribePhysics(); }
});

test("ground impacts provide measured contact data, and failed collider allocations preserve the scene", async () => {
  await m.initPhysics();
  const world = new m.CreationWorld(new m.Scene());
  try {
    apply(world, [create("falling", { transform: { position: [0,1,0] }, physics: { restitution: .7 } })]);
    for (let i=0;i<120;i++) { world.update(1/60); m.stepPhysics(1/60); }
    const impact = world.store.events().events.find(e => e.type === "collision" && e.target === "falling" && e.data.other === "ground");
    assert.ok(impact);
    assert.equal(impact.data.point.length, 3);
    assert.ok(impact.data.impulse > 0);
    const body = world.entries.get("falling").body;
    assert.throws(() => apply(world, [patch("falling", { physics: { colliders: [{ id: "flat", shape: "convex", vertices: [0,0,0,1,0,0,0,1,0,1,1,0] }] } })]), /collider/);
    assert.equal(world.entries.get("falling").body, body);
    assert.ok(body.isValid());
  } finally { world.unsubscribePhysics(); }
});

test("real GLB refresh preserves Figment anchors and physics; tuning physics retains the loaded mesh", async () => {
  await m.initPhysics();
  const previousFetch = globalThis.fetch;
  let requests = 0;
  globalThis.fetch = async () => new Response(figmentGLB(++requests));
  const world = new m.CreationWorld(new m.Scene());
  const ready = () => new Promise((resolve, reject) => {
    const timeout = setTimeout(() => reject(Error("asset did not finish")), 1500);
    world.store.onEvent = event => {
      if (!["asset.ready", "asset.failed"].includes(event.type)) return;
      clearTimeout(timeout);
      if (event.type === "asset.failed") reject(Error(event.data.error)); else resolve();
    };
  });
  try {
    let loaded = ready();
    apply(world, [create("model", { kind: "asset", asset: { url: "/revision-1.glb", normalize: false }, transform: { position: [1,2,3] }, physics: { mode: "kinematic", colliders: [{ id: "body", size: [1,1,.1] }] }, figment: { anchors: { handle: { node: "Handle" } }, grips: { hold: { anchor: "handle" } } } })]);
    await loaded;
    const visual = world.entries.get("model").visual;
    apply(world, [create("display", { parent: "model", transform: { position: [0,.25,.1] } })]);
    const display = world.entries.get("display").node;
    assert.equal(display.parent, world.entries.get("model").node);
    assert.deepEqual(display.getWorldPosition(new m.Vector3()).toArray(), [1,2.25,3.1]);
    const definition = structuredClone(world.entries.get("model").spec.figment);
    const pose = world.sample("model").transform;
    apply(world, [patch("model", { physics: { mass: 2, friction: .9 } })]);
    assert.equal(world.entries.get("model").visual, visual);
    assert.equal(requests, 1);
    const body = world.entries.get("model").body;
    loaded = ready();
    apply(world, [patch("model", { asset: { url: "/revision-2.glb" } })]);
    await loaded;
    assert.notEqual(world.entries.get("model").visual, visual);
    assert.equal(world.entries.get("model").body, body);
    assert.deepEqual(world.entries.get("model").spec.figment, definition);
    assert.deepEqual(world.sample("model").transform, pose);
    near(world.sample("model").anchors.handle.position[1], 1.8);
    assert.equal(world.entries.get("model").spec.physics.mass, 2);
    assert.equal(world.entries.get("display").node, display);
    assert.equal(display.parent, world.entries.get("model").node);
    assert.deepEqual(display.getWorldPosition(new m.Vector3()).toArray(), [1,2.25,3.1]);
    assert.ok(world.hold("model", "desktop"));
    assert.throws(() => apply(world, [patch("display", { transform: { position: [9,9,9] } })], "program:figment.model"), /Human interaction/);
    apply(world, [patch("display", { material: { color: "#42f5f5" } })], "program:figment.model");
    apply(world, [{ op: "entity.delete", id: "model" }], "desktop");
    assert.equal(world.entries.has("display"), false);
  } finally { world.unsubscribePhysics(); globalThis.fetch = previousFetch; }
});

test("asset hierarchies reject cycles, missing parents and unsupported parent kinds atomically", () => {
  const store = new WorldStore({ commit() {} });
  const model = id => create(id, { kind: "asset", asset: { url: "/model.glb" } });
  store.apply({ requestId: "initial", operations: [model("model"), create("child", { kind: "group", parent: "model" })] });
  assert.throws(() => store.apply({ requestId: "cycle", operations: [patch("model", { parent: "child" })] }), /Parent cycle/);
  assert.throws(() => store.apply({ requestId: "missing", operations: [patch("child", { parent: "missing" })] }), /must be a group or asset/);
  assert.throws(() => store.apply({ requestId: "shape", operations: [create("shape"), patch("child", { parent: "shape" })] }), /must be a group or asset/);
  assert.equal(store.document.entities.length, 2);
  assert.equal(store.document.entities.find(e => e.id === "model").parent, null);
  assert.equal(store.document.entities.find(e => e.id === "child").parent, "model");
});

test("anchors resolve unique authored mesh nodes and grip poses align without resizing", () => {
  const world = new m.CreationWorld(new m.Scene());
  apply(world, [create("tool", { transform: { position: [1,2,3], rotation: [0,.5,0], scale: [2,2,2] }, figment: { anchors: { handle: { node: "Handle", position: [0,.1,0] } }, grips: { main: { anchor: "handle" } } } })]);
  const entry = world.entries.get("tool"), handle = new m.Group();
  handle.name = "Handle"; handle.position.y = -.2; entry.visual.add(handle);
  const anchor = m.resolveFigmentAnchors(world, "tool").handle;
  assert.equal(anchor.ready, true);
  near(anchor.position[1], 1.8);
  const hand = new m.Vector3(4,5,6), orientation = new m.Quaternion().setFromAxisAngle(new m.Vector3(0,0,1), .7);
  world.setPose("tool", m.poseAtGrip(entry.node, anchor, hand, orientation));
  const aligned = m.resolveFigmentAnchors(world, "tool").handle;
  aligned.position.forEach((n,i) => near(n, hand.toArray()[i]));
  near(Math.abs(new m.Quaternion(...aligned.quaternion).dot(orientation)), 1);
  assert.deepEqual(world.sample("tool").transform.scale, [2,2,2]);
  const duplicate = handle.clone(); entry.visual.add(duplicate);
  assert.match(m.resolveFigmentAnchors(world, "tool").handle.error, /Ambiguous/);
  world.unsubscribePhysics();
});

test("holding a Figment preserves physical ownership while its actions, properties and glow still work", () => {
  const world = new m.CreationWorld(new m.Scene());
  apply(world, [create("lantern", { figment: m.FIGMENT_PRESETS.lantern })]);
  assert.ok(world.hold("lantern", "xr:left"));
  apply(world, [{ op: "figment.property", id: "lantern", name: "on", value: false }], "program:figment.lantern");
  apply(world, [patch("lantern", { material: { glow: 3 } })], "program:figment.lantern");
  assert.equal(world.entries.get("lantern").spec.figment.properties.on.value, false);
  assert.equal(world.entries.get("lantern").spec.material.glow, 3);
  assert.throws(() => apply(world, [patch("lantern", { transform: { position: [9,9,9] } })], "program:figment.lantern"), /Human interaction/);
  assert.throws(() => apply(world, [{ op: "figment.property", id: "lantern", name: "brightness", value: 200 }]), /finite/);
  world.unsubscribePhysics();
});

test("Figment outlines are opt-in per object, including newly spawned copies", () => {
  const previousWindow = globalThis.window;
  globalThis.window = new EventTarget();
  const world = new m.CreationWorld(new m.Scene());
  const input = new m.CreationInput(world, new m.PerspectiveCamera(), new EventTarget());
  const coin = { geometry: { shape: "cylinder", size: [.06,.009,.06] }, figment: {
    anchors: { center: {} }, grips: { whole_coin: { anchor: "center", radius: .04 } },
  } };
  const visibleGrips = () => input.gripMarkers.children.filter(marker => marker.visible);
  try {
    apply(world, [create("coin", coin)]);
    input.select("coin"); input.update();
    assert.equal(input.showGrips, false);
    assert.equal(input.outline.visible, false);
    assert.equal(visibleGrips().length, 0);

    input.showGrips = true; input.update();
    assert.equal(input.outline.visible, true);
    assert.equal(visibleGrips().length, 1);

    apply(world, [create("copy", coin)]);
    input.select("copy"); input.update();
    assert.equal(input.showGrips, false);
    assert.equal(input.outline.visible, false);
    assert.equal(visibleGrips().length, 0);

    input.select("coin"); input.update();
    assert.equal(input.showGrips, true);
    assert.equal(input.outline.visible, true);
    input.showGrips = false; input.update();
    assert.equal(input.outline.visible, false);
    assert.equal(visibleGrips().length, 0);

    apply(world, [create("primitive")]);
    input.select("primitive"); input.update();
    assert.equal(input.outline.visible, true);
    assert.equal(visibleGrips().length, 0);
    input.select(null); input.update();
    assert.equal(input.outline.visible, false);
  } finally { world.unsubscribePhysics(); globalThis.window = previousWindow; }
});

test("click-away deselects models and hides guides without interrupting a grab or inspector edits", () => {
  const previousWindow = globalThis.window;
  globalThis.window = new EventTarget();
  const world = new m.CreationWorld(new m.Scene()), canvas = new EventTarget();
  canvas.getBoundingClientRect = () => ({ left: 0, top: 0, width: 100, height: 100 });
  canvas.setPointerCapture = () => {}; canvas.hasPointerCapture = () => false; canvas.style = {};
  const input = new m.CreationInput(world, new m.PerspectiveCamera(60,1,.1,100), canvas);
  const pointer = (target, type, values = {}) => {
    const event = new Event(type); Object.assign(event, { button: 0, pointerId: 1, clientX: 50, clientY: 50, ...values });
    target.dispatchEvent(event);
  };
  try {
    apply(world, [create("model", { transform: { position: [0,0,-3] }, figment: { anchors: { center: {} }, grips: { hold: { anchor: "center" } } } })]);
    pointer(canvas, 'pointerdown');
    assert.equal(input.selected, 'model'); assert.equal(input.holds.size, 1);
    pointer(canvas, 'pointerdown', { pointerId: 2, clientX: 0, clientY: 0 });
    assert.equal(input.selected, 'model'); assert.equal(input.holds.size, 1);
    pointer(canvas, 'pointerup');
    input.showGrips = true; input.update(); assert.equal(input.outline.visible, true);
    pointer(canvas, 'pointerdown', { clientX: 0, clientY: 0 }); input.update();
    assert.equal(input.selected, null); assert.equal(input.outline.visible, false);
    assert.ok(input.gripMarkers.children.every(marker => !marker.visible));

    input.select('model');
    pointer(window, 'pointerdown', { composedPath: () => [{ id: 'creation-studio' }, window] });
    assert.equal(input.selected, 'model');
    pointer(window, 'pointerdown', { composedPath: () => [canvas, window] });
    assert.equal(input.selected, 'model');
    pointer(window, 'pointerdown', { composedPath: () => [{ id: 'chat-input' }, window] });
    assert.equal(input.selected, null);

    input.select('model');
    input.updateXR([{ id:'left', handedness:'left', position:new m.Vector3(10,0,0), ray:new m.Ray(new m.Vector3(10,0,0),new m.Vector3(0,0,-1)), isActive:true, wasActive:false }]);
    assert.equal(input.selected, null);
  } finally { input.releaseAll(); world.unsubscribePhysics(); globalThis.window = previousWindow; }
});

test("actual XR grip input aims between two authored handles without stretching the object", () => {
  const previousWindow = globalThis.window;
  globalThis.window = new EventTarget();
  const world = new m.CreationWorld(new m.Scene());
  const input = new m.CreationInput(world, new m.PerspectiveCamera(), new EventTarget());
  try {
    apply(world, [create("bar", { geometry: { size: [.1,1.2,.1] }, transform: { position: [0,1,0] }, figment: {
      anchors: { low: { position: [0,-.5,0] }, high: { position: [0,.5,0] } },
      grips: { left: { anchor: "low", hand: "left", radius: .15 }, right: { anchor: "high", hand: "right", radius: .15 } },
    } })]);
    const pointer = (id, xyz, active, was) => ({ id, handedness: id, type: "controller", position: new m.Vector3(...xyz), quaternion: new m.Quaternion(), ray: new m.Ray(new m.Vector3(...xyz), new m.Vector3(0,0,-1)), isActive: active, wasActive: was });
    input.updateXR([pointer("left", [0,.5,.03], true, false), pointer("right", [0,1.5,.03], true, false)]);
    assert.equal(world.store.locks.get("bar"), "xr:left");
    assert.equal(input.holds.get("xr:left").grip.name, "left");
    assert.equal(input.holds.get("xr:right").grip.name, "right");
    input.update();
    assert.equal(input.outline.visible, false);
    assert.ok(input.gripMarkers.children.every(marker => !marker.visible));
    input.updateXR([pointer("left", [0,1,0], true, true), pointer("right", [1,1,0], true, true)]);
    const anchors = m.resolveFigmentAnchors(world, "bar");
    anchors.low.position.forEach((n,i) => near(n, [0,1,0][i]));
    anchors.high.position.forEach((n,i) => near(n, [1,1,0][i]));
    assert.deepEqual(world.sample("bar").transform.scale, [1,1,1]);
    input.updateXR([pointer("left", [0,1,0], false, true), pointer("right", [1,1,0], false, true)]);
    assert.equal(world.store.locks.size, 0);
    assert.ok(world.store.events().events.some(e => e.type === "grip"));
  } finally { world.unsubscribePhysics(); globalThis.window = previousWindow; }
});

test("publish/export/import preserves editable assets, behavior and joints while remapping copies", async () => {
  const storage = new MemoryStorage(), library = new m.FigmentLibrary(storage, async url => url.endsWith(".blend") ? new TextEncoder().encode("BLENDER-v300editable source") : figmentGLB());
  const world = new WorldStore({ commit() {} });
  world.apply({ requestId: "assembly", operations: [
    create("base", { kind: "asset", asset: { url: "/private/base.glb", normalize: false }, transform: { position: [3,2,1] }, physics: { mode: "fixed", colliders: [{ id: "base" }] }, figment: { ...m.FIGMENT_PRESETS.kinetic, title: "Machine", parts: { wheel: "wheel" }, joints: { motor: "motor" }, source: { url: "/private/source.blend", filename: "machine.blend" } } }),
    create("wheel", { transform: { position: [3,3,1] }, physics: {} }),
    { op: "joint.create", joint: { id: "motor", a: "base", b: "wheel", type: "hinge" } },
  ] });
  const result = await library.publish(world.document, "base");
  assert.equal(result.editableSource, true);
  const bundle = await library.export(result.id);
  assert.ok(!JSON.stringify(bundle).includes("/private/"));
  assert.equal(bundle.world.entities[0].figment.behavior.source, m.FIGMENT_PRESETS.kinetic.behavior.source);
  const second = new m.FigmentLibrary(new MemoryStorage());
  await second.store(JSON.stringify(bundle));
  const a = await second.placement(result.id, [0,1,-2]);
  const b = await second.placement(result.id, [2,1,-2]);
  const placed = new WorldStore({ commit() {} });
  placed.apply({ requestId: "a", operations: a.operations });
  placed.apply({ requestId: "b", operations: b.operations });
  assert.equal(placed.document.entities.length, 4);
  assert.notEqual(a.root, b.root);
  const root = placed.document.entities.find(e => e.id === a.root);
  assert.deepEqual(root.transform.position, [0,1,-2]);
  assert.ok(root.figment.parts.wheel.startsWith("f-"));
  const joint = placed.document.joints.find(j => j.id === root.figment.joints.motor);
  assert.equal(joint.a, a.root);
  assert.equal(joint.b, root.figment.parts.wheel);
  const source = await m.readFigmentAsset(root.figment.source.url, { storage: second.storage });
  assert.match(new TextDecoder().decode(source), /^BLENDER/);
  const damaged = structuredClone(bundle); damaged.assets[0].data = btoa("tampered");
  await assert.rejects(() => second.store(damaged), /integrity/);
  assert.equal((await second.list()).length, 1);
  world.apply({ requestId: "edit", operations: [patch("base", { material: { color: "#ffffff" } })] });
  await assert.rejects(() => library.publish(world.document, "base"), /already published/);
  world.apply({ requestId: "version", operations: [patch("base", { figment: { version: "1.0.1" } })] });
  await library.publish(world.document, "base");
  assert.equal((await library.list()).length, 2);
});

test("named behavior executes through the actual worker API after ID remapping, without model calls", async () => {
  const outputs = [];
  const worker = { postMessage: data => outputs.push(structuredClone(data)) };
  runInNewContext(`(${m.workerBootstrap.toString()})()`, { self: worker, Error, Object, Array, JSON });
  const root = parseEntity({ id: "imported", figment: m.FIGMENT_PRESETS.lantern });
  const params = { __figment: { root: "imported", parts: {}, joints: {} } };
  await worker.onmessage({ data: { type: "init", source: root.figment.behavior.source, state: {}, params } });
  assert.equal(outputs.pop().type, "ready");
  await worker.onmessage({ data: { type: "step", entities: [root], params, time: 1, dt: .05, events: [{ type: "action", target: root.id, data: { action: "toggle" } }] } });
  const frame = outputs.pop();
  assert.equal(frame.type, "frame");
  assert.deepEqual(frame.operations[0], { op: "figment.property", id: "imported", name: "on", value: false });
  assert.equal(frame.operations[1].patch.material.glow, 0);
  assert.equal(frame.operations[1].id, "imported");
});

test("Figment service replaces behavior on code edits, preserves it on metadata edits and starts imports paused", async () => {
  const previous = globalThis.window;
  globalThis.window = new EventTarget();
  const world = new m.CreationWorld(new m.Scene()), programs = new m.CreationPrograms(world);
  const service = { world, programs, save() {}, blender: { forEntity() { return null; } } };
  const figments = new m.FigmentService(service, new m.FigmentLibrary(new MemoryStorage()));
  try {
    apply(world, [create("lamp")]);
    await figments.attach({ id: "lamp", preset: "lantern" });
    const old = programs.records.get(figments.programId("lamp"));
    assert.equal(old.status, "paused");
    old.state.count = 4;
    await figments.attach({ id: "lamp", definition: { title: "New title" } });
    assert.equal(programs.records.get(old.id), old);
    await figments.handle({ command: "behavior", id: "lamp", source: "return {tick(){api.state.count=2}};" });
    assert.notEqual(programs.records.get(old.id), old);
    assert.equal(programs.records.get(old.id).state.count, undefined);
    const published = await figments.handle({ command: "publish", id: "lamp" });
    const placed = await figments.place(published.id, [2,1,0]);
    assert.equal(placed.behavior.status, "paused");
    assert.equal(world.paused, true);
    assert.notEqual(placed.id, "lamp");
    const duplicate = await figments.duplicate("lamp");
    assert.notEqual(duplicate.id, "lamp");
    assert.equal(duplicate.behavior.status, "paused");
    await figments.remove(duplicate.id);
    assert.equal(world.entries.has(duplicate.id), false);
    assert.equal(programs.records.has(figments.programId(duplicate.id)), false);
    world.store.undo();
    await figments.reconcile();
    assert.equal(programs.records.get(figments.programId(duplicate.id)).status, "paused");
  } finally { programs.dispose(); world.unsubscribePhysics(); globalThis.window = previous; }
});

test("whole assemblies duplicate independently, cannot share owners, and observations omit behavior source", async () => {
  const world = new m.CreationWorld(new m.Scene());
  apply(world, [create("root", { kind: "group", figment: { ...m.FIGMENT_PRESETS.lantern, parts: { bulb: "bulb" } } }), create("bulb", { parent: "root" })]);
  assert.throws(() => apply(world, [create("other", { figment: { parts: { bulb: "bulb" } } })]), /another Figment/);
  const observed = world.store.observe({ ids: ["root"] }).entities[0];
  assert.equal(observed.figment.behavior.source, undefined);
  assert.ok(observed.figment.behavior.sourceChars > 100);
  assert.match(world.store.observe({ ids: ["root"], includeSource: true }).entities[0].figment.behavior.source, /api.patch/);
  const edit = { requestId: "idempotent", baseRevision: world.store.document.revision, operations: [create("new")] };
  world.store.preview(edit);
  world.store.apply(edit);
  assert.equal(world.store.preview(edit).entities.length, 3);
  assert.equal(world.store.apply(edit).status, "completed");
  world.unsubscribePhysics();
});
