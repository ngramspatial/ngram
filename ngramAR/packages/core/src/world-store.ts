import {
  WORLD_PROTOCOL,
  WORLD_LIMITS,
  identifier,
  number,
  vector,
  parseEntity,
  patchEntity,
  parseJoint,
} from "./world-contract.js";
import { propertyValue } from "./figment-contract.js";
import type {
  WorldDocument,
  WorldEdit,
  WorldEntity,
  WorldEvent,
  WorldJoint,
  WorldOperation,
} from "./world-contract.js";

export interface WorldAdapter {
  commit(
    before: WorldDocument,
    after: WorldDocument,
    effects: WorldOperation[],
  ): void;
  sample?(entity: WorldEntity): Partial<WorldEntity> & Record<string, unknown>;
}
/** Single writer authority. Renderer, humans and harness adapters share these checks. */
export class WorldStore {
  document: WorldDocument = {
    protocol: WORLD_PROTOCOL,
    id: "workspace",
    name: "Workspace",
    revision: 0,
    entities: [],
    joints: [],
  };
  readonly locks = new Map<string, string>();
  private adapter: WorldAdapter;
  private receipts = new Map<string, { fingerprint: string; result: any }>();
  private journal: WorldEvent[] = [];
  private sequence = 0;
  private undoStack: WorldDocument[] = [];
  private redoStack: WorldDocument[] = [];
  private interactionCheckpoints = new Map<string, WorldDocument>();
  onChange: (() => void) | null = null;
  onEvent: ((event: WorldEvent) => void) | null = null;
  constructor(adapter: WorldAdapter) {
    this.adapter = adapter;
  }
  heldBy(id: string, entities = this.document.entities): string | null {
    let cursor: string | null = id;
    const visited = new Set<string>();
    while (cursor) {
      if (visited.has(cursor)) throw new Error("Parent cycle");
      visited.add(cursor);
      const owner = this.locks.get(cursor);
      if (owner) return owner;
      cursor = entities.find((e) => e.id === cursor)?.parent ?? null;
    }
    return null;
  }
  beginInteraction(actor: string) {
    if (!this.interactionCheckpoints.has(actor))
      this.interactionCheckpoints.set(actor, this.checkpoint());
  }
  endInteraction(actor: string) {
    this.interactionCheckpoints.delete(actor);
  }

  emit(type: string, actor: string, target?: string, data?: unknown) {
    const event = {
      sequence: ++this.sequence,
      time: Date.now(),
      type,
      actor,
      target,
      data,
    };
    this.journal.push(event);
    if (this.journal.length > WORLD_LIMITS.events) this.journal.shift();
    this.onEvent?.(structuredClone(event));
    return event;
  }
  events(after = 0) {
    return {
      cursor: this.sequence,
      truncated: !!this.journal.length && after < this.journal[0].sequence - 1,
      events: structuredClone(this.journal.filter((e) => e.sequence > after)),
    };
  }
  observe(
    options: {
      ids?: string[];
      tag?: string;
      offset?: number;
      limit?: number;
      includeGeometry?: boolean;
      includeSource?: boolean;
    } = {},
  ) {
    if (
      options.ids !== undefined &&
      (!Array.isArray(options.ids) ||
        options.ids.length > WORLD_LIMITS.entities)
    )
      throw new Error("ids must be a bounded array");
    const all = this.document.entities.filter(
      (e) =>
        (!options.ids || options.ids.includes(e.id)) &&
        (!options.tag || e.tags.includes(options.tag)),
    );
    const offset = Math.floor(
      number(options.offset, 0, 0, WORLD_LIMITS.entities),
    );
    const limit = Math.floor(
      number(options.limit, 16, 1, WORLD_LIMITS.entities),
    );
    return {
      protocol: WORLD_PROTOCOL,
      id: this.document.id,
      name: this.document.name,
      revision: this.document.revision,
      sampledAt: Date.now(),
      total: all.length,
      nextOffset: offset + limit < all.length ? offset + limit : null,
      entities: all.slice(offset, offset + limit).map((e) => {
        const geometry = options.includeGeometry
          ? e.geometry
          : {
              ...e.geometry,
              vertices: undefined,
              indices: undefined,
              instances: undefined,
              vertexCount: (e.geometry.vertices?.length ?? 0) / 3,
              indexCount: e.geometry.indices?.length ?? 0,
              instanceCount: e.geometry.instances?.length ?? 0,
            };
        return {
          ...structuredClone(e),
          figment: e.figment ? {
            ...structuredClone(e.figment),
            behavior: e.figment.behavior ? (options.includeSource ? { ...e.figment.behavior } : { hz: e.figment.behavior.hz, sourceChars: e.figment.behavior.source.length }) : null,
          } : null,
          geometry: structuredClone(geometry),
          ...this.adapter.sample?.(e),
          heldBy: this.heldBy(e.id),
        };
      }),
      joints: structuredClone(this.document.joints),
      eventCursor: this.sequence,
    };
  }
  checkpoint(): WorldDocument {
    return {
      ...structuredClone(this.document),
      entities: this.document.entities.map((e) => {
        const live = this.adapter.sample?.(e);
        return {
          ...structuredClone(e),
          ...(live?.transform ? { transform: live.transform } : {}),
        };
      }),
    };
  }
  private validateGraph(doc: WorldDocument) {
    if (doc.entities.length > WORLD_LIMITS.entities)
      throw new Error("World entity budget exceeded");
    if (doc.joints.length > WORLD_LIMITS.entities)
      throw new Error("World joint budget exceeded");
    const entities = new Map(doc.entities.map((e) => [e.id, e]));
    const figmentOwners = new Map<string, string>();
    if (
      entities.size !== doc.entities.length ||
      new Set(doc.joints.map((j) => j.id)).size !== doc.joints.length
    )
      throw new Error("Duplicate world IDs");
    let vertices = 0,
      instances = 0,
      lights = 0,
      assets = 0;
    for (const e of doc.entities) {
      vertices += (e.geometry.vertices?.length ?? 0) / 3;
      instances += e.geometry.instances?.length ?? 0;
      lights += Number(e.kind === "light");
      assets += Number(e.kind === "asset");
      if (e.figment) {
        if (e.parent) throw Error("Figment roots must be world roots");
        const scope = new Set([e.id, ...Object.values(e.figment.parts)]);
        let expanded = true;
        while (expanded) {
          expanded = false;
          for (const part of doc.entities) if (part.parent && scope.has(part.parent) && !scope.has(part.id)) { scope.add(part.id); expanded = true; }
        }
        for (const id of scope) {
          if (!entities.has(id)) throw Error(`Missing Figment part: ${id}`);
          if (id !== e.id && entities.get(id)!.figment) throw Error("A Figment cannot own another Figment");
          if (figmentOwners.has(id) && figmentOwners.get(id) !== e.id) throw Error(`Part ${id} already belongs to another Figment`);
          figmentOwners.set(id, e.id);
        }
        for (const id of Object.values(e.figment.joints)) {
          const joint = doc.joints.find(j => j.id === id);
          if (!joint || !scope.has(joint.a) || !scope.has(joint.b)) throw Error(`Figment joint ${id} must connect named parts`);
        }
      }
      const visited = new Set([e.id]);
      let p = e.parent;
      while (p) {
        if (visited.has(p)) throw new Error("Parent cycle");
        visited.add(p);
        const parent = entities.get(p);
        if (!parent || !["group", "asset"].includes(parent.kind))
          throw new Error(`Parent ${p} must be a group or asset`);
        p = parent.parent;
      }
    }
    if (
      vertices > WORLD_LIMITS.vertices ||
      instances > WORLD_LIMITS.instances ||
      lights > 8 ||
      assets > 8
    )
      throw new Error(
        "World geometry, instance, light or asset budget exceeded",
      );
    for (const j of doc.joints) {
      if (
        j.a === j.b ||
        !entities.get(j.a)?.physics ||
        !entities.get(j.b)?.physics
      )
        throw new Error(`Joint ${j.id} needs two different physics bodies`);
      if (
        (j.velocity !== 0 || j.limits) &&
        !["hinge", "slider"].includes(j.type)
      )
        throw new Error("Motors and limits require hinge or slider joints");
    }
  }
  private assertUnlocked(id: string, actor: string, entities: WorldEntity[]) {
    const owner = this.heldBy(id, entities);
    if (owner && owner !== actor)
      throw new Error(`Human interaction owns ${id} until release`);
    for (const [lockedId, owner] of this.locks) {
      if (owner === actor) continue;
      let cursor: string | null = lockedId;
      const visited = new Set<string>();
      while (cursor) {
        if (visited.has(cursor)) throw new Error("Parent cycle");
        visited.add(cursor);
        if (cursor === id)
          throw new Error(`Human interaction owns ${lockedId} until release`);
        cursor = entities.find((e) => e.id === cursor)?.parent ?? null;
      }
    }
  }
  apply(edit: WorldEdit, actor = "agent") {
    const requestId = identifier(edit?.requestId);
    const isProgram = actor.startsWith("program:");
    const fingerprint = isProgram ? "" : JSON.stringify({ actor, edit });
    const previousReceipt = this.receipts.get(requestId);
    if (previousReceipt) {
      if (previousReceipt.fingerprint !== fingerprint)
        throw new Error("requestId already used for a different edit");
      return structuredClone(previousReceipt.result);
    }
    if (
      edit.baseRevision !== undefined &&
      edit.baseRevision !== this.document.revision
    )
      throw new Error(
        `Revision conflict: expected ${edit.baseRevision}, current ${this.document.revision}`,
      );
    if (
      !Array.isArray(edit.operations) ||
      !edit.operations.length ||
      edit.operations.length > WORLD_LIMITS.operations
    )
      throw new Error(`Use 1–${WORLD_LIMITS.operations} operations per edit`);
    const next = structuredClone(this.document);
    const effects: WorldOperation[] = [];
    const changed = new Set<string>();
    const propertyEvents: { id: string; name: string; value: unknown }[] = [];
    for (const op of edit.operations) {
      if (!op || typeof op !== "object") throw new Error("Invalid operation");
      if (op.op === "entity.create") {
        const e = parseEntity(op.entity);
        if (e.parent) this.assertUnlocked(e.parent, actor, next.entities);
        if (next.entities.some((v) => v.id === e.id))
          throw new Error(`Entity ${e.id} already exists; patch it`);
        next.entities.push(e);
        changed.add(e.id);
      } else if (op.op === "figment.property") {
        const id = identifier(op.id), name = identifier(op.name);
        const entity = next.entities.find(e => e.id === id);
        const property = entity?.figment?.properties[name];
        if (!property || !Object.hasOwn(entity!.figment!.properties, name)) throw Error(`Unknown Figment property: ${name}`);
        if (actor === "human" && !property.editable) throw Error(`Property ${name} is read-only`);
        property.value = propertyValue(property, op.value);
        propertyEvents.push({ id, name, value: property.value });
        changed.add(id);
      } else if (
        op.op === "entity.patch" ||
        op.op === "entity.delete" ||
        op.op === "body.impulse"
      ) {
        const id = identifier(op.id);
        // A held lamp can still switch on. Physical pose and geometry remain owned by the hand.
        const nonPhysicalProgramEdit = isProgram && op.op === "entity.patch" && op.patch && typeof op.patch === "object" &&
          Object.keys(op.patch).every(k => ["material", "control", "visible"].includes(k));
        if (!nonPhysicalProgramEdit) this.assertUnlocked(id, actor, next.entities);
        const index = next.entities.findIndex((e) => e.id === id);
        if (index < 0) throw new Error(`Unknown entity: ${id}`);
        if (op.op === "entity.patch") {
          const updated = patchEntity(next.entities[index], op.patch);
          if (updated.parent && updated.parent !== next.entities[index].parent)
            this.assertUnlocked(updated.parent, actor, next.entities);
          next.entities[index] = updated;
        } else if (op.op === "entity.delete") {
          const removed = new Set([id]);
          let n = 0;
          while (n !== removed.size) {
            n = removed.size;
            for (const e of next.entities)
              if (e.parent && removed.has(e.parent)) removed.add(e.id);
          }
          next.entities = next.entities.filter((e) => !removed.has(e.id));
          next.joints = next.joints.filter(
            (j) => !removed.has(j.a) && !removed.has(j.b),
          );
          removed.forEach((id) => changed.add(id));
        } else {
          if (next.entities[index].physics?.mode !== "dynamic")
            throw new Error("Impulses require a dynamic body");
          effects.push({
            op: "body.impulse",
            id,
            impulse: vector(op.impulse, [0, 0, 0], -1000, 1000),
            ...(op.torque
              ? { torque: vector(op.torque, [0, 0, 0], -1000, 1000) }
              : {}),
          });
        }
        changed.add(id);
      } else if (op.op === "joint.create") {
        const j = parseJoint(op.joint);
        if (next.joints.some((v) => v.id === j.id))
          throw new Error(`Joint ${j.id} already exists`);
        this.assertUnlocked(j.a, actor, next.entities);
        this.assertUnlocked(j.b, actor, next.entities);
        next.joints.push(j);
        changed.add(j.id);
      } else if (op.op === "joint.delete" || op.op === "joint.motor") {
        const index = next.joints.findIndex((j) => j.id === op.id);
        if (index < 0) throw new Error(`Unknown joint: ${op.id}`);
        const j = next.joints[index];
        this.assertUnlocked(j.a, actor, next.entities);
        this.assertUnlocked(j.b, actor, next.entities);
        if (op.op === "joint.delete") next.joints.splice(index, 1);
        else
          next.joints[index] = parseJoint({
            ...j,
            velocity: op.velocity,
            strength: op.strength ?? j.strength,
          });
        changed.add(op.id);
      } else throw new Error(`Unknown world operation: ${(op as any).op}`);
    }
    this.validateGraph(next);
    for (const effect of effects)
      if (
        effect.op === "body.impulse" &&
        next.entities.find((e) => e.id === effect.id)?.physics?.mode !==
          "dynamic"
      )
        throw new Error("Impulse target must remain a dynamic body at commit");
    // Local animation is a live sample. It must not invalidate an agent's edit
    // revision twenty times a second while the agent is deciding what to change.
    if (!actor.startsWith("program:")) next.revision++;
    const checkpoint = actor.startsWith("program:")
      ? null
      : (this.interactionCheckpoints.get(actor) ?? this.checkpoint());
    this.adapter.commit(this.document, next, effects);
    if (checkpoint) {
      this.undoStack.push(checkpoint);
      if (this.undoStack.length > 30) this.undoStack.shift();
      this.redoStack = [];
    }
    this.interactionCheckpoints.delete(actor);
    this.document = next;
    for (const event of propertyEvents) this.emit("property", actor, event.id, { name: event.name, value: event.value });
    const result = {
      protocol: WORLD_PROTOCOL,
      status: "completed",
      requestId,
      revision: next.revision,
      changed: [...changed],
    };
    if (!isProgram) this.receipts.set(requestId, { fingerprint, result });
    if (this.receipts.size > 256)
      this.receipts.delete(this.receipts.keys().next().value!);
    if (!actor.startsWith("program:"))
      this.emit("world.edited", actor, undefined, result);
    this.onChange?.();
    return structuredClone(result);
  }
  /** Validate extra components against a prospective edit, preserving receipt/lock semantics. */
  preview(edit: WorldEdit, actor = "agent"): WorldDocument {
    const checked = new WorldStore({ commit() {} });
    checked.document = structuredClone(this.document);
    checked.receipts = new Map(this.receipts);
    for (const [id, owner] of this.locks) checked.locks.set(id, owner);
    checked.apply(edit, actor);
    return checked.document;
  }
  restore(value: unknown, actor = "human", history = true) {
    const v = value as WorldDocument;
    if (
      v?.protocol !== WORLD_PROTOCOL ||
      !Array.isArray(v.entities) ||
      !Array.isArray(v.joints)
    )
      throw new Error("Unsupported world document");
    if (this.locks.size)
      throw new Error("Release grabbed objects before restoring a world");
    const next: WorldDocument = {
      protocol: WORLD_PROTOCOL,
      id: identifier(v.id),
      name: typeof v.name === "string" ? v.name.slice(0, 160) : v.id,
      revision: this.document.revision + 1,
      entities: v.entities.map(parseEntity),
      joints: v.joints.map(parseJoint),
    };
    this.validateGraph(next);
    const checkpoint = this.checkpoint();
    this.adapter.commit(this.document, next, []);
    if (history) {
      this.undoStack.push(checkpoint);
      if (this.undoStack.length > 30) this.undoStack.shift();
      this.redoStack = [];
    }
    this.document = next;
    this.receipts.clear();
    this.emit("world.restored", actor);
    this.onChange?.();
    return { status: "completed", revision: next.revision };
  }
  undo() {
    const previous = this.undoStack.at(-1);
    if (!previous) throw new Error("Nothing to undo");
    const current = this.checkpoint();
    this.restore(previous, "human", false);
    this.undoStack.pop();
    this.redoStack.push(current);
  }
  redo() {
    const next = this.redoStack.at(-1);
    if (!next) throw new Error("Nothing to redo");
    const current = this.checkpoint();
    this.restore(next, "human", false);
    this.redoStack.pop();
    this.undoStack.push(current);
  }
}
