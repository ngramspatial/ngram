// @ts-nocheck
import { WORLD_LIMITS, identifier, number } from "@ngram-ar/core";

// Runs inside a dedicated Worker inside an opaque-origin iframe. The iframe's
// CSP is inherited by its blob Worker: no network, imports, nested workers or DOM.
function workerBootstrap() {
  let handlers = {},
    state = {},
    params = {},
    entities = [],
    time = 0,
    dt = 0,
    operations = [];
  const send = self.postMessage.bind(self);
  for (const key of ["Worker", "SharedWorker", "importScripts", "postMessage"])
    Object.defineProperty(self, key, {
      value: undefined,
      writable: false,
      configurable: false,
    });
  const api = Object.freeze({
    get state() {
      return state;
    },
    get params() {
      return params;
    },
    get time() {
      return time;
    },
    get dt() {
      return dt;
    },
    get: (id) => entities.find((e) => e.id === id) ?? null,
    emit: (ops) => {
      if (!Array.isArray(ops) || operations.length + ops.length > 128)
        throw Error("Program command budget exceeded");
      operations.push(...ops);
    },
  });
  self.onmessage = async ({ data }) => {
    try {
      if (data.type === "init") {
        state = data.state;
        params = data.params;
        handlers =
          (await new Function("api", '"use strict";\n' + data.source)(api)) ||
          {};
        if (Object.keys(handlers).some((k) => !["tick", "event"].includes(k)))
          throw Error("Return only tick and/or event handlers");
        send({ type: "ready" });
        return;
      }
      if (data.type !== "step") return;
      entities = data.entities;
      params = data.params;
      time = data.time;
      dt = data.dt;
      operations = [];
      for (const event of data.events) await handlers.event?.(event);
      await handlers.tick?.();
      const output = { type: "frame", operations, state };
      if (JSON.stringify(output).length > 192000)
        throw Error("Program output/state budget exceeded");
      send(output);
    } catch (error) {
      send({
        type: "error",
        error: String(error?.message ?? error).slice(0, 300),
      });
    }
  };
}

const WORKER_SOURCE = `(${workerBootstrap.toString()})()`;
const FRAME_HTML = `<!doctype html><meta http-equiv="Content-Security-Policy" content="default-src 'none'; script-src 'unsafe-inline' 'unsafe-eval'; worker-src blob:; connect-src 'none'; child-src 'none'; img-src 'none'; style-src 'none';"><script>
let worker;const parentWindow=parent;const send=data=>parentWindow.postMessage(data,'*');
addEventListener('message',event=>{
 if(event.source!==parentWindow)return;
 const data=event.data;
 if(data.type==='boot'){
  worker?.terminate();
  const url=URL.createObjectURL(new Blob([data.workerSource],{type:'text/javascript'}));
  worker=new Worker(url);URL.revokeObjectURL(url);
  worker.onmessage=event=>send(event.data);
  worker.onerror=event=>send({type:'error',error:event.message||'Program worker failed'});
  worker.postMessage(data.init);
 }else if(data.type==='terminate'){worker?.terminate();worker=null;}
 else worker?.postMessage(data);
});
<\/script>`;

export const PROGRAM_API_HELP = {
  install:
    "{id,name?,source,entityIds:[...],params:{...},state:{...},hz?:1..30}. Source is JavaScript returning {tick(){...},event(event){...}}. No imports, DOM or network. Runs locally without model requests.",
  api: "api.time (simulation seconds), api.dt (seconds), api.params, api.state (mutable JSON persisted at checkpoints), api.get(id) (live entity snapshot including heldBy), api.emit([world operations]). Skip held entities when animating.",
  permissions:
    "Only listed entity IDs and joints entirely within that list may be patched, motorized or receive impulses. Programs may animate geometry vertices/instances, transforms, materials and controls within world budgets. Programs cannot create/delete objects or install programs. Create geometry through world.apply, then animate it locally. Human grabs take priority.",
  events:
    "grab, release, control {value}, collision {other}, world.edited. Events have sequence, time, type, actor, target, data. Only events targeting owned entities are delivered.",
  example:
    'return {tick(){const e=api.get("orb");if(e&&!e.heldBy)api.emit([{op:"entity.patch",id:"orb",patch:{transform:{position:[Math.cos(api.time)*.5,1,Math.sin(api.time)*.5]}}}]);},event(e){if(e.type==="control")api.state.lastValue=e.data.value;}};',
  lifecycle:
    "inspect, install (replaces same ID after validation), pause, resume, remove, parameters. Stop pauses creations. Failed or runaway programs terminate. Restored programs remain paused until Resume creations.",
};

export class CreationPrograms {
  records = new Map();
  world;
  onChange = null;
  private timer;
  private epoch = 0;
  private pending = new Set();
  private versions = new Map();
  constructor(world) {
    this.world = world;
    this.timer = setInterval(() => this.pump(), 16);
  }
  inspect(id?, includeSource = false) {
    return [...this.records.values()]
      .filter((r) => !id || r.id === id)
      .map((r) => ({
        id: r.id,
        name: r.name,
        status: r.status,
        error: r.error,
        entityIds: r.entityIds,
        params: r.params,
        state: r.state,
        hz: r.hz,
        frames: r.frames,
        time: r.time,
        ...(includeSource ? { source: r.source } : {}),
      }));
  }
  export() {
    return [...this.records.values()].map((r) => ({
      id: r.id,
      name: r.name,
      source: r.source,
      entityIds: r.entityIds,
      params: r.params,
      state: r.state,
      hz: r.hz,
      time: r.time,
      status: r.status === "failed" ? "failed" : "paused",
      error: r.error,
    }));
  }
  async install(value, { start = true } = {}) {
    const id = identifier(value?.id);
    if (!this.records.has(id) && this.records.size >= WORLD_LIMITS.programs)
      throw Error("Program budget exceeded");
    if (
      typeof value.source !== "string" ||
      value.source.length > WORLD_LIMITS.sourceBytes
    )
      throw Error("Program source must be at most 64k characters");
    if (
      !Array.isArray(value.entityIds) ||
      !value.entityIds.length ||
      value.entityIds.length > 256
    )
      throw Error("Programs require 1–256 entity IDs");
    const ids = [...new Set(value.entityIds.map(identifier))];
    if (ids.some((id) => !this.world.entries.has(id)))
      throw Error("Create all program entities before installing");
    if (
      JSON.stringify({ params: value.params ?? {}, state: value.state ?? {} })
        .length > 32000
    )
      throw Error("Parameters/state exceed 32k");
    for (const k of ["params", "state"])
      if (
        value[k] != null &&
        (typeof value[k] !== "object" || Array.isArray(value[k]))
      )
        throw Error(`${k} must be a JSON object`);
    const r = {
      id,
      name: String(value.name ?? id).slice(0, 160),
      source: value.source,
      entityIds: ids,
      params: structuredClone(value.params ?? {}),
      state: structuredClone(value.state ?? {}),
      hz: number(value.hz, 20, 1, 30),
      frames: 0,
      time: number(value.time, 0, 0, 1e12),
      cursor: this.world.store.events().cursor,
      status: "paused",
      error: null,
      frame: null,
      busy: false,
      last: performance.now(),
      started: 0,
    };
    if (!start && value.status === "failed") {
      r.status = "failed";
      r.error = String(value.error ?? "Program failed before saving").slice(
        0,
        300,
      );
    }
    const version = (this.versions.get(id) ?? 0) + 1;
    this.versions.set(id, version);
    // Compile the replacement before disposing the previous version.
    const epoch = this.epoch;
    if (start) {
      this.pending.add(r);
      try {
        await this.boot(r);
      } finally {
        this.pending.delete(r);
      }
    }
    if (epoch !== this.epoch || version !== this.versions.get(id)) {
      this.destroy(r);
      throw Error("Program installation cancelled or superseded");
    }
    this.destroy(this.records.get(id));
    this.records.set(id, r);
    this.onChange?.();
    return this.inspect().find((p) => p.id === id);
  }
  private boot(r) {
    return new Promise((resolve, reject) => {
      const frame = document.createElement("iframe");
      frame.hidden = true;
      frame.setAttribute("sandbox", "allow-scripts");
      frame.setAttribute("aria-hidden", "true");
      frame.srcdoc = FRAME_HTML;
      r.frame = frame;
      r.status = "starting";
      r.started = performance.now();
      let settled = false;
      const finish = (error) => {
        if (settled) return;
        settled = true;
        clearTimeout(startTimer);
        if (error) {
          this.fail(r, error);
          reject(Error(error));
        } else {
          r.status = "running";
          r.last = performance.now();
          r.nextDue = r.last + 1000 / r.hz;
          resolve();
        }
      };
      const startTimer = setTimeout(
        () => finish("Program startup exceeded 3 seconds"),
        3000,
      );
      r.cancelBoot = () => finish("Program installation cancelled");
      r.listener = (event) => {
        if (event.source !== frame.contentWindow) return;
        const data = event.data;
        if (!data || typeof data !== "object") return;
        if (data.type === "ready" && !settled) {
          finish();
          return;
        }
        if (data.type === "error") {
          const error = String(data.error ?? "Program failed").slice(0, 300);
          if (!settled) finish(error);
          else this.fail(r, error);
          return;
        }
        if (data.type !== "frame" || r.status !== "running" || !r.busy) return;
        try {
          if (
            JSON.stringify(data).length > 192000 ||
            !Array.isArray(data.operations) ||
            data.operations.length > 128 ||
            !data.state ||
            typeof data.state !== "object" ||
            Array.isArray(data.state) ||
            JSON.stringify(data.state).length > 32000
          )
            throw Error("Program output budget exceeded");
          const operations = data.operations.filter((op) => {
            if (
              !["entity.patch", "body.impulse", "joint.motor"].includes(op?.op)
            )
              throw Error("Program operation is outside its permissions");
            if (op.op === "joint.motor") {
              const j = this.world.store.document.joints.find(
                (j) => j.id === op.id,
              );
              if (
                !j ||
                !r.entityIds.includes(j.a) ||
                !r.entityIds.includes(j.b)
              )
                throw Error("Program cannot control this joint");
              return (
                !this.world.store.heldBy(j.a) && !this.world.store.heldBy(j.b)
              );
            }
            if (!r.entityIds.includes(op.id))
              throw Error("Program cannot control this entity");
            if (
              op.op === "entity.patch" &&
              Object.keys(op.patch ?? {}).some(
                (k) =>
                  ![
                    "transform",
                    "material",
                    "geometry",
                    "control",
                    "visible",
                  ].includes(k),
              )
            )
              throw Error(
                "Programs may animate geometry, transforms, materials, controls and visibility",
              );
            return !this.world.store.heldBy(op.id);
          });
          if (operations.length)
            this.world.store.apply(
              { requestId: crypto.randomUUID(), operations },
              `program:${r.id}`,
            );
          r.state = structuredClone(data.state);
          r.frames++;
          r.busy = false;
          r.error = null;
        } catch (error) {
          this.fail(r, error.message);
        }
      };
      window.addEventListener("message", r.listener);
      frame.onload = () =>
        frame.contentWindow?.postMessage(
          {
            type: "boot",
            workerSource: WORKER_SOURCE,
            init: {
              type: "init",
              source: r.source,
              params: r.params,
              state: r.state,
            },
          },
          "*",
        );
      document.body.append(frame);
    });
  }
  private pump() {
    const now = performance.now();
    for (const r of this.records.values()) {
      if (r.status !== "running") continue;
      if (document.hidden) {
        r.last = now;
        r.started = now;
        r.nextDue = now + 1000 / r.hz;
        continue;
      }
      if (r.busy) {
        if (now - r.started > 500)
          this.fail(r, "Program exceeded its 500 ms response budget");
        continue;
      }
      const elapsed = (now - r.last) / 1000;
      if (now < r.nextDue) continue;
      const dt = Math.min(elapsed, 2 / r.hz);
      if (r.entityIds.some((id) => !this.world.entries.has(id))) {
        this.fail(r, "A program entity was deleted");
        continue;
      }
      if (this.world.paused) {
        r.last = now;
        r.nextDue = now + 1000 / r.hz;
        continue;
      }
      r.last = now;
      r.nextDue = Math.max(r.nextDue + 1000 / r.hz, now);
      r.started = now;
      r.busy = true;
      r.time += dt;
      const events = this.world.store.events(r.cursor);
      r.cursor = events.cursor;
      const filtered = events.events
        .filter((e) => e.target && r.entityIds.includes(e.target))
        .slice(-64);
      r.frame.contentWindow?.postMessage(
        {
          type: "step",
          dt,
          time: r.time,
          params: r.params,
          entities: this.world.store.observe({ ids: r.entityIds, limit: 256 })
            .entities,
          events: filtered,
        },
        "*",
      );
    }
  }
  private destroy(r) {
    if (!r) return;
    r.frame?.contentWindow?.postMessage({ type: "terminate" }, "*");
    r.frame?.remove();
    r.frame = null;
    if (r.listener) window.removeEventListener("message", r.listener);
    r.busy = false;
  }
  private fail(r, error) {
    r.status = "failed";
    r.error = String(error).slice(0, 300);
    this.destroy(r);
    this.world.store.emit("program.failed", "runtime", r.id, {
      error: r.error,
    });
    this.onChange?.();
  }
  async command(command, id?, params?) {
    const records = id
      ? [this.records.get(identifier(id))]
      : [...this.records.values()];
    if (records.some((r) => !r)) throw Error("Unknown program");
    if (!["pause", "resume", "remove", "parameters"].includes(command))
      throw Error("Unknown program command");
    if (command === "pause" || command === "remove") {
      this.epoch++;
      for (const r of this.pending) r.cancelBoot?.();
    }
    if (
      command === "parameters" &&
      (!id ||
        !params ||
        typeof params !== "object" ||
        Array.isArray(params) ||
        JSON.stringify(params).length > 16000)
    )
      throw Error("Parameters require an ID and a JSON object under 16k");
    for (const r of records) {
      if (command === "pause") {
        this.destroy(r);
        if (r.status !== "failed") r.status = "paused";
      } else if (command === "resume") {
        if (r.status !== "running" && (id || r.status !== "failed")) {
          this.destroy(r);
          this.pending.add(r);
          try {
            await this.boot(r);
          } finally {
            this.pending.delete(r);
          }
        }
      } else if (command === "remove") {
        this.destroy(r);
        this.records.delete(r.id);
      } else r.params = structuredClone(params);
    }
    this.onChange?.();
    return this.inspect();
  }
  dispose() {
    clearInterval(this.timer);
    for (const r of this.records.values()) this.destroy(r);
    this.records.clear();
  }
}
