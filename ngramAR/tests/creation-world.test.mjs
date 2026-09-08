import assert from "node:assert/strict";
import test from "node:test";
import { WorldStore, parseEntity } from "@ngram-ar/core";

const entity = (id, extra = {}) => ({
  op: "entity.create",
  entity: { id, ...extra },
});
const apply = (store, operations, extra = {}) =>
  store.apply({ requestId: crypto.randomUUID(), operations, ...extra });

test("invalid batched edits commit nothing, retries are idempotent, revisions detect conflicts", () => {
  let commits = 0;
  const store = new WorldStore({ commit: () => commits++ });
  assert.throws(
    () =>
      apply(store, [
        entity("ok"),
        entity("bad", { transform: { position: [NaN, 0, 0] } }),
      ]),
    /finite/,
  );
  assert.equal(store.document.entities.length, 0);
  assert.equal(commits, 0);
  const edit = {
    requestId: "build-1",
    operations: [entity("orb", { geometry: { shape: "sphere" } })],
  };
  assert.deepEqual(store.apply(edit), store.apply(edit));
  assert.equal(commits, 1);
  assert.throws(
    () => store.apply({ ...edit, operations: [entity("another")] }),
    /different edit/,
  );
  assert.throws(
    () => apply(store, [entity("another")], { baseRevision: 0 }),
    /Revision conflict/,
  );
});

test("parent cycles, invalid bodies, triangles and dangling joints are rejected atomically", () => {
  const store = new WorldStore({ commit() {} });
  for (const ops of [
    [
      entity("a", { kind: "group", parent: "b" }),
      entity("b", { kind: "group", parent: "a" }),
    ],
    [entity("a", { physics: { mode: "dynamic" }, parent: "b" })],
    [
      entity("mesh", {
        geometry: {
          shape: "mesh",
          vertices: [0, 0, 0, 1, 0, 0, 0, 1, 0],
          indices: [0, 1, 9],
        },
      }),
    ],
    [{ op: "joint.create", joint: { id: "hinge", a: "a", b: "b" } }],
    [entity("a", { material: { color: "transparent" } })],
  ])
    assert.throws(() => apply(store, ops));
  assert.equal(store.document.revision, 0);
});

test("humans own held objects and ancestor transforms until release", () => {
  const store = new WorldStore({ commit() {} });
  apply(store, [
    entity("assembly", { kind: "group" }),
    entity("part", { parent: "assembly" }),
  ]);
  store.locks.set("part", "xr:left");
  for (const operation of [
    {
      op: "entity.patch",
      id: "part",
      patch: { material: { color: "#FFFFFF" } },
    },
    { op: "entity.delete", id: "assembly" },
    {
      op: "entity.patch",
      id: "assembly",
      patch: { transform: { position: [1, 2, 3] } },
    },
  ])
    assert.throws(() => apply(store, [operation]), /Human interaction owns/);
  store.locks.clear();
  store.locks.set("assembly", "xr:left");
  assert.equal(store.observe({ ids: ["part"] }).entities[0].heldBy, "xr:left");
  assert.throws(
    () =>
      apply(store, [
        {
          op: "entity.patch",
          id: "part",
          patch: { transform: { position: [1, 2, 3] } },
        },
      ]),
    /Human interaction owns/,
  );
  store.locks.clear();
  apply(store, [{ op: "entity.delete", id: "assembly" }]);
  assert.equal(store.document.entities.length, 0);
});

test("ownership checks terminate on a cycle introduced earlier in the same untrusted batch", () => {
  const store = new WorldStore({ commit() {} });
  assert.throws(
    () =>
      apply(store, [
        entity("a", { kind: "group", parent: "b" }),
        entity("b", { kind: "group", parent: "a" }),
        entity("child", { parent: "a" }),
      ]),
    /Parent cycle/,
  );
  assert.equal(store.document.entities.length, 0);
});

test("live observation and checkpoints use the simulated pose and preserve controls through undo", () => {
  const store = new WorldStore({
    commit() {},
    sample: () => ({
      transform: { position: [3, 2, 1], rotation: [0, 0, 0], scale: [1, 1, 1] },
    }),
  });
  apply(store, [
    entity("slider", {
      kind: "control",
      control: { type: "slider", label: "Speed", min: 0, max: 5, value: 2 },
    }),
  ]);
  apply(store, [
    { op: "entity.patch", id: "slider", patch: { control: { value: 4 } } },
  ]);
  assert.equal(store.observe().entities[0].control.value, 4);
  assert.deepEqual(
    store.checkpoint().entities[0].transform.position,
    [3, 2, 1],
  );
  store.undo();
  assert.equal(store.observe().entities[0].control.value, 2);
  store.redo();
  assert.equal(store.observe().entities[0].control.value, 4);
});

test("physics impulses and motors validate before reaching the renderer", () => {
  let effects = [];
  const store = new WorldStore({ commit: (a, b, e) => (effects = e) });
  apply(store, [
    entity("fixed", { physics: { mode: "fixed" } }),
    entity("ball", { physics: { mode: "dynamic" } }),
    {
      op: "joint.create",
      joint: { id: "motor", a: "fixed", b: "ball", type: "hinge" },
    },
  ]);
  assert.throws(
    () =>
      apply(store, [{ op: "body.impulse", id: "fixed", impulse: [1, 0, 0] }]),
    /dynamic/,
  );
  apply(store, [
    { op: "body.impulse", id: "ball", impulse: [1, 2, 3] },
    { op: "joint.motor", id: "motor", velocity: 2 },
  ]);
  assert.deepEqual(effects[0].impulse, [1, 2, 3]);
  assert.equal(store.document.joints[0].velocity, 2);
  assert.throws(
    () =>
      apply(store, [
        { op: "body.impulse", id: "ball", impulse: [1, 0, 0] },
        { op: "entity.delete", id: "ball" },
      ]),
    /remain a dynamic body/,
  );
  assert.ok(store.document.entities.some((e) => e.id === "ball"));
});

test("event cursors report truncation and return detached data, without dispatching inference", () => {
  const store = new WorldStore({ commit() {} });
  for (let i = 0; i < 520; i++)
    store.emit("control", "human", "speed", { value: i });
  const all = store.events(0);
  assert.equal(all.truncated, true);
  assert.equal(all.events.length, 512);
  assert.equal(store.events(519).events.length, 1);
  assert.equal(store.events(520).events.length, 0);
  all.events[0].data.value = -1;
  assert.notEqual(store.events(0).events[0].data.value, -1);
});

test("appearance patches preserve unspecified geometry and reject unsupported fields", () => {
  const store = new WorldStore({ commit() {} });
  apply(store, [
    entity("orb", { geometry: { shape: "sphere", size: [0.4, 0.4, 0.4] } }),
  ]);
  apply(store, [
    {
      op: "entity.patch",
      id: "orb",
      patch: { material: { color: "#FFFFFF" } },
    },
  ]);
  assert.equal(store.document.entities[0].geometry.shape, "sphere");
  assert.throws(
    () =>
      apply(store, [
        { op: "entity.patch", id: "orb", patch: { script: "alert(1)" } },
      ]),
    /Unknown entity field/,
  );
  assert.equal(parseEntity({ id: "default" }).material.color, "#6E7DFF");
});

test("local programs do not churn edit revisions and human drags undo to the grab start", () => {
  let position = [0, 1, 0];
  const store = new WorldStore({
    commit: (a, b) => {
      position = b.entities[0]?.transform.position ?? position;
    },
    sample: () => ({
      transform: {
        position: [...position],
        rotation: [0, 0, 0],
        scale: [1, 1, 1],
      },
    }),
  });
  apply(store, [entity("orb", { transform: { position: [0, 1, 0] } })]);
  const revision = store.document.revision;
  for (let i = 0; i < 20; i++)
    store.apply(
      {
        requestId: `animation-${i}`,
        operations: [
          {
            op: "entity.patch",
            id: "orb",
            patch: { material: { glow: i % 3 } },
          },
        ],
      },
      "program:test",
    );
  assert.equal(store.document.revision, revision);
  store.beginInteraction("pointer:1");
  position = [3, 2, 1];
  store.apply(
    {
      requestId: "release",
      operations: [
        { op: "entity.patch", id: "orb", patch: { transform: { position } } },
      ],
    },
    "pointer:1",
  );
  store.undo();
  assert.deepEqual(store.document.entities[0].transform.position, [0, 1, 0]);
});
