// @ts-nocheck
import { FIGMENT_API_HELP, parseFigment, propertyValue, identifier, object, assetReference } from "@ngram-ar/core";
import { FigmentLibrary, figmentScope, copyFigment, readFigmentAsset, FIGMENT_PACKAGE_LIMIT } from "./figment-library.js";
import { FIGMENT_PRESETS } from "./figment-presets.js";

export class FigmentService {
  jobs = new Set();
  constructor(service, library = new FigmentLibrary()) { this.service = service; this.library = library; }
  get world() { return this.service.world; }
  entry(id) {
    const e = this.world.entries.get(identifier(id));
    if (!e) throw Error("Unknown object");
    return e;
  }
  programId(id) {
    // World IDs can be 96 characters; retain uniqueness without exceeding that limit.
    let hash = 14695981039346656037n;
    for (const c of id) hash = BigInt.asUintN(64, (hash ^ BigInt(c.charCodeAt(0))) * 1099511628211n);
    return `figment.${id.slice(0, 60)}.${hash.toString(16)}`;
  }
  inspect(id, includeSource = false) {
    const entries = id ? [[id, this.entry(id)]] : [...this.world.entries].filter(([, e]) => e.spec.figment);
    return entries.map(([id, e]) => {
      const definition = structuredClone(e.spec.figment);
      if (definition?.behavior && !includeSource) definition.behavior = { hz: definition.behavior.hz, sourceChars: definition.behavior.source.length };
      const record = this.service.programs.inspect(this.programId(id))[0];
      return { id, definition, physics: e.spec.physics, ...this.world.sample(id), behavior: record ? { id: record.id, status: record.status, frames: record.frames, error: record.error, hz: record.hz } : null };
    });
  }
  async sync(id) {
    const f = this.entry(id).spec.figment;
    const programId = this.programId(id);
    if (!f?.behavior) {
      if (this.service.programs.records.has(programId)) await this.service.programs.command("remove", programId);
      return null;
    }
    return this.service.programs.install({ id: programId, name: f.title, source: f.behavior.source,
      entityIds: [...figmentScope(this.world.store.document, id)], hz: f.behavior.hz,
      params: { __figment: { root: id, parts: f.parts, joints: f.joints } }, state: {},
    }, { start: false });
  }
  async reconcile() {
    for (const record of [...this.service.programs.records.values()]) {
      const root = record.params.__figment?.root;
      if (root && (!this.world.entries.get(root)?.spec.figment?.behavior || record.id !== this.programId(root))) await this.service.programs.command("remove", record.id);
    }
    for (const [id, entry] of this.world.entries) {
      const f = entry.spec.figment;
      if (!f?.behavior) continue;
      const record = this.service.programs.records.get(this.programId(id));
      const bindings = { root: id, parts: f.parts, joints: f.joints };
      if (!record || record.source !== f.behavior.source || record.hz !== f.behavior.hz ||
        JSON.stringify(record.params.__figment) !== JSON.stringify(bindings) ||
        JSON.stringify(record.entityIds) !== JSON.stringify([...figmentScope(this.world.store.document, id)])) await this.sync(id);
    }
  }
  validateDocument(document, programs = this.service.programs.inspect()) {
    const figments = document.entities.filter(e => e.figment?.behavior);
    const generic = programs.filter(p => !p.params?.__figment?.root);
    if (figments.length + generic.length > 12) throw Error("Figment behaviors and creation programs exceed the shared program budget");
    for (const e of figments) {
      if (figmentScope(document, e.id).size > 256) throw Error("Figment behavior supports at most 256 assembly entities");
      if (generic.some(p => p.id === this.programId(e.id))) throw Error("A creation program already uses this Figment's program ID");
    }
  }
  async attach(payload) {
    const id = identifier(payload.id), entry = this.entry(id);
    const preset = payload.preset ? FIGMENT_PRESETS[payload.preset] : null;
    if (payload.preset && !preset) throw Error("Unknown Figment preset");
    const base = preset ? structuredClone(preset) : entry.spec.figment ?? { title: entry.spec.name };
    const project = this.service.blender?.forEntity(id);
    const source = project?.revision ? { url: `${project.base}/${project.revision}/project.blend`, filename: "project.blend" } : base.source;
    const definition = parseFigment({ ...base, source, ...object(payload.definition ?? {}, "definition") });
    const programId = this.programId(id);
    const changedBehavior = JSON.stringify([entry.spec.figment?.behavior, entry.spec.figment?.parts, entry.spec.figment?.joints]) !== JSON.stringify([definition.behavior, definition.parts, definition.joints]);
    if (definition.behavior && !this.service.programs.records.has(programId) && this.service.programs.records.size >= 12) throw Error("Remove an unused program before adding Figment behavior");
    const patch = { figment: definition, grabbable: payload.grabbable ?? (entry.spec.figment ? entry.spec.grabbable : true) };
    if (payload.physics !== undefined) patch.physics = payload.physics;
    // Validate the whole assembly before changing a running definition.
    const operations = [{ op: "entity.patch", id, patch }];
    this.validateDocument(this.world.store.preview({ requestId: payload.requestId ?? crypto.randomUUID(), operations }));
    this.world.store.apply({ requestId: payload.requestId ?? crypto.randomUUID(), operations });
    if (changedBehavior || (definition.behavior && !this.service.programs.records.has(programId))) await this.sync(id);
    if (payload.start === true && definition.behavior) {
      await this.service.programs.command("resume", programId);
      this.world.pause(false);
    }
    this.service.save();
    this.service.onChange?.();
    return this.inspect(id)[0];
  }
  setProperties(id, values, actor = "human") {
    const entry = this.entry(id), f = entry.spec.figment;
    if (!f) throw Error("Object is not a Figment");
    const changes = [];
    for (const [name, value] of Object.entries(object(values, "properties"))) {
      const p = f.properties[name];
      if (!p) throw Error(`Unknown property: ${name}`);
      if (actor === "human" && !p.editable) throw Error(`Property ${name} is read-only`);
      changes.push({ name, value: propertyValue(p, value) });
    }
    const result = this.world.store.apply({ requestId: crypto.randomUUID(), operations: changes.map(change => ({ op: "figment.property", id, ...change })) }, actor);
    return result;
  }
  action(id, action, actor = "human", data = {}) {
    const f = this.entry(id).spec.figment;
    if (!f || !Object.hasOwn(f.actions, action)) throw Error("Unknown Figment action");
    if (JSON.stringify(data).length > 2048) throw Error("Action data exceeds 2k");
    return this.world.store.emit("action", actor, id, { action, data });
  }
  async place(id, position) {
    return this.job(async signal => {
      const placement = await this.library.placement(id, position, signal);
      signal.throwIfAborted();
      return this.commitPlacement(placement);
    });
  }
  async duplicate(id) {
    const position = this.world.sample(id).transform.position.map((n, i) => n + (i === 0 ? .35 : 0));
    return this.commitPlacement(copyFigment(this.world.store.checkpoint(), id, position));
  }
  async commitPlacement(placement) {
    const root = placement.operations.find(op => op.entity?.id === placement.root).entity;
    if (root.figment.behavior && this.service.programs.records.size >= 12) throw Error("Remove an unused program before placing this Figment");
    if (placement.operations.length > 256 || (root.figment.behavior && placement.operations.filter(op => op.entity).length > 256)) throw Error("Figment assembly exceeds the placement budget");
    // Staged renderer commits ensure a failed assembly never leaves half its parts behind.
    this.world.store.apply({ requestId: crypto.randomUUID(), operations: placement.operations });
    await this.sync(placement.root);
    this.world.pause(true);
    this.service.save();
    return { id: placement.root, placed: true, paused: true, ...this.inspect(placement.root)[0] };
  }
  async detach(id) {
    this.world.store.apply({ requestId: crypto.randomUUID(), operations: [{ op: "entity.patch", id, patch: { figment: null } }] });
    if (this.service.programs.records.has(this.programId(id))) await this.service.programs.command("remove", this.programId(id));
    return { id, detached: true };
  }
  async remove(id) {
    const ids = figmentScope(this.world.store.document, id);
    const roots = this.world.store.document.entities.filter(e => ids.has(e.id) && (!e.parent || !ids.has(e.parent)));
    this.world.store.apply({ requestId: crypto.randomUUID(), operations: roots.map(e => ({ op: "entity.delete", id: e.id })) }, "human");
    await this.reconcile();
    this.service.save();
    return { removed: [...ids] };
  }
  async handle(payload = {}) {
    const command = payload.command ?? "inspect";
    switch (command) {
      case "capabilities": return { ...FIGMENT_API_HELP, presets: structuredClone(FIGMENT_PRESETS) };
      case "inspect": return this.inspect(payload.id, payload.includeSource === true);
      case "attach": case "configure": return this.attach(payload);
      case "properties": return this.setProperties(payload.id, payload.values, "agent");
      case "action": return this.action(payload.id, payload.action, "agent", payload.data ?? {});
      case "detach": return this.detach(payload.id);
      case "physics": {
        const id = identifier(payload.id);
        this.entry(id);
        return this.world.store.apply({ requestId: payload.requestId ?? crypto.randomUUID(), operations: [{ op: "entity.patch", id, patch: { physics: payload.physics } }] });
      }
      case "behavior": {
        const id = identifier(payload.id), f = this.entry(id).spec.figment;
        if (!f) throw Error("Object is not a Figment");
        if (payload.source !== undefined) return this.attach({ id, definition: { behavior: { source: payload.source, hz: payload.hz ?? 20 } }, start: payload.start === true });
        if (!["pause", "resume", "reset"].includes(payload.action)) throw Error("Use pause, resume or reset, or supply source");
        if (payload.action === "reset") { await this.sync(id); return this.inspect(id)[0]; }
        const result = await this.service.programs.command(payload.action, this.programId(id));
        if (payload.action === "resume") this.world.pause(false);
        return result;
      }
      case "publish": {
        const id = identifier(payload.id);
        for (const part of figmentScope(this.world.store.document, id)) {
          if (this.world.store.heldBy(part)) throw Error("Release the Figment before publishing");
          const entry = this.entry(part);
          if (entry.spec.asset && entry.status !== "ready") throw Error("Wait for all Figment models to finish loading before publishing");
        }
        const snapshot = this.world.store.checkpoint();
        const root = snapshot.entities.find(e => e.id === id), project = this.service.blender?.forEntity(id);
        if (project && root.asset?.url.startsWith(project.base + "/") && root.figment.source?.url.startsWith(project.base + "/"))
          root.figment.source.url = root.asset.url.replace(/\/preview\.glb$/, "/project.blend");
        const result = await this.job(signal => this.library.publish(snapshot, id, signal));
        this.service.onChange?.();
        return result;
      }
      case "library": return this.library.list();
      case "export": {
        const bundle = await this.job(signal => this.library.export(payload.packageId, signal));
        const root = bundle.world.entities.find(e => e.id === bundle.root);
        const filename = `${root.figment.title.replace(/[^a-z0-9-]+/gi, "-").slice(0, 80)}-${root.figment.version}.figment.json`;
        const blob = new Blob([JSON.stringify(bundle)], { type: "application/json" });
        const url = URL.createObjectURL(blob), link = document.createElement("a");
        link.href = url; link.download = filename; link.click();
        setTimeout(() => URL.revokeObjectURL(url), 30000);
        return { packageId: bundle.id, filename, bytes: blob.size, downloadStarted: true };
      }
      case "import": {
        const result = await this.job(async signal => {
          const raw = payload.url ? new TextDecoder().decode(await readFigmentAsset(assetReference(payload.url), { signal, maxBytes: FIGMENT_PACKAGE_LIMIT })) : payload.package;
          signal.throwIfAborted();
          return this.library.store(raw, signal);
        });
        this.service.onChange?.();
        return payload.place === true ? this.place(result.id, payload.position) : result;
      }
      case "place": return this.place(payload.packageId, payload.position);
      default: throw Error("Unknown Figment command");
    }
  }
  async job(fn) {
    const controller = new AbortController();
    this.jobs.add(controller);
    const timeout = setTimeout(() => controller.abort(Error("Figment operation timed out")), 90000);
    try { return await fn(controller.signal); }
    finally { clearTimeout(timeout); this.jobs.delete(controller); }
  }
  cancelPending() {
    for (const job of this.jobs) job.abort(Error("Figment operation cancelled"));
  }
}
