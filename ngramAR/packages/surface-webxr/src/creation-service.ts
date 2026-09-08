// @ts-nocheck
import { WORLD_API_HELP, WorldStore, identifier } from "@ngram-ar/core";
import { CreationPrograms, PROGRAM_API_HELP } from "./creation-programs.js";
import { kineticWorkshop } from "./kinetic-workshop.js";
import { resonanceGarden } from "./resonance-garden.js";
import { BlenderProjects } from './blender-projects.js';

const STORAGE_KEY = "ngram_creation_worlds_v1";
/** Transport-neutral actions. No action/event here initiates model inference. */
export class CreationService {
  world;
  programs;
  library = { active: "workspace", worlds: {} };
  onChange = null;
  saveTimer = null;
  storageError = null;
  restoreError = null;
  surfaceContext = null;
  legacyObjects = null;
  perform = null;
  cancelPerformance = null;
  constructor(world) {
    this.world = world;
    this.programs = new CreationPrograms(world);
    this.blender = new BlenderProjects(this);
    const changed = () => {
      this.onChange?.();
      if (!this.saveTimer)
        this.saveTimer = setTimeout(() => {
          this.saveTimer = null;
          this.save();
        }, 1500);
    };
    world.store.onChange = changed;
    this.programs.onChange = changed;
    window.addEventListener("pagehide", () => this.save());
    document.addEventListener("visibilitychange", () => {
      if (document.hidden) this.save();
    });
  }
  async restore() {
    try {
      const raw = localStorage.getItem(STORAGE_KEY);
      if (!raw) return;
      const library = JSON.parse(raw);
      if (!library.worlds || typeof library.worlds !== "object")
        throw Error("Invalid creation library");
      const current = library.worlds[library.active];
      if (!current) throw Error("Saved creation library has no active world");
      this.library = library;
      await this.loadDocument(current);
      this.world.pause(true);
    } catch (error) {
      this.restoreError = `Could not restore creations: ${error.message}. Saved data is preserved; import a repaired world to resume saving.`;
      this.storageError = this.restoreError;
      this.onChange?.();
    }
  }
  export() {
    return {
      world: this.world.store.checkpoint(),
      programs: this.programs.export(),
      blender: this.blender.export(),
    };
  }
  save(name?) {
    // Page-hide and autosave must never replace a failed restore with an empty scene.
    if (this.restoreError)
      return { id: this.library.active, saved: false, error: this.restoreError };
    if (name) this.world.store.document.name = String(name).slice(0, 160);
    this.library.active = this.world.store.document.id;
    this.library.worlds[this.library.active] = this.export();
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(this.library));
      this.storageError = null;
    } catch (error) {
      this.storageError = `Could not save creations: ${error.message}`;
      this.onChange?.();
    }
    return {
      id: this.library.active,
      saved: !this.storageError,
      error: this.storageError,
    };
  }
  private async loadDocument(doc) {
    if (this.world.store.locks.size)
      throw Error("Release grabbed objects before importing a world");
    if (!doc?.world || !Array.isArray(doc.programs) || doc.programs.length > 12)
      throw Error("Invalid creation document");
    // Validate the complete import before pausing or replacing the user's world.
    const checked = new WorldStore({ commit() {} });
    checked.restore(doc.world, "validation", false);
    if (doc.blender !== undefined && (!Array.isArray(doc.blender) || doc.blender.length > 8))
      throw Error('Invalid Blender project links');
    for (const link of doc.blender ?? []) this.blender.validate(link);
    const validationPrograms = new CreationPrograms({
      entries: new Map(checked.document.entities.map((e) => [e.id, e])),
      store: checked,
    });
    try {
      if (new Set(doc.programs.map((p) => p.id)).size !== doc.programs.length)
        throw Error("Duplicate program IDs");
      for (const p of doc.programs)
        await validationPrograms.install(p, { start: false });
    } finally {
      validationPrograms.dispose();
    }
    await this.programs.command("pause");
    this.world.pause(true);
    this.world.store.restore(doc.world);
    await this.programs.command("remove");
    for (const p of doc.programs)
      await this.programs.install(p, { start: false });
    this.blender.restore(doc.blender);
    this.restoreError = null;
    this.storageError = null;
  }
  async pause() {
    this.cancelPerformance?.();
    this.world.pause(true);
    await this.programs.command("pause");
    this.save();
    this.onChange?.();
    return { paused: true };
  }
  clear() {
    if (this.world.store.locks.size)
      throw Error("Release grabbed objects before clearing creations");
    this.cancelPerformance?.();
    this.blender.clear();
    void this.programs.command("remove");
    this.world.store.restore({
      ...this.world.store.document,
      entities: [],
      joints: [],
    });
    this.save();
  }
  async resume() {
    this.world.pause(false);
    await this.programs.command("resume");
    this.onChange?.();
    return { paused: false };
  }
  async handle(command, payload = {}) {
    switch (command) {
      case "capabilities":
        return {
          ...WORLD_API_HELP,
          programs: PROGRAM_API_HELP,
          features: {
            blenderPreviews: true,
            physics: true,
            customMeshes: true,
            instancing: true,
            groups: true,
            liveControls: true,
            desktopGrab: true,
            xrRayGrab: true,
            roomMesh: false,
            cloudWorldSync: false,
          },
          worlds: Object.keys(this.library.worlds),
        };
      case "observe":
        return {
          ...this.world.store.observe(payload),
          programs: this.programs.inspect(),
          paused: this.world.paused,
          storageError: this.storageError,
          performance: { ...this.world.metrics },
          surface: this.surfaceContext?.(),
          legacyObjects: this.legacyObjects?.(),
          blender: this.blender.list(),
        };
      case 'blender':
        return this.blender.attach(payload);
      case "perform": {
        if (!this.perform) throw Error("This surface has no avatar performer");
        if (!["look", "approach"].includes(payload.action))
          throw Error("Use look or approach");
        const target = identifier(payload.target);
        if (!this.world.entries.has(target))
          throw Error("Unknown target entity");
        return this.perform({
          ...payload,
          target,
          position: this.world.sample(target).worldPosition,
        });
      }
      case "apply":
        return this.world.store.apply(payload);
      case "assets":
        return payload.command === "inspect"
          ? [...this.world.entries]
              .filter(([, e]) => e.spec.asset)
              .map(([id]) => ({ id, ...this.world.sample(id) }))
          : this.world.assetJob(payload.command, identifier(payload.id));
      case "events":
        return this.world.store.events(payload.after ?? 0);
      case "program":
        return payload.command === "inspect"
          ? this.programs.inspect(payload.id, payload.includeSource)
          : payload.command === "install"
            ? this.programs.install(payload.program)
            : this.programs.command(
                payload.command,
                payload.id,
                payload.params,
              );
      case "pause":
        return this.pause();
      case "resume":
        return this.resume();
      case "save":
        return this.save(payload.name);
      case "export":
        return this.export();
      case "import":
        await this.loadDocument(payload);
        return this.save();
      case "load": {
        const doc = this.library.worlds[identifier(payload.id)];
        if (!doc) throw Error("Unknown saved world");
        this.save();
        await this.loadDocument(doc);
        return this.save();
      }
      case "fork": {
        const id = identifier(payload.id);
        if (this.library.worlds[id]) throw Error("World ID already exists");
        this.save();
        this.world.store.document.id = id;
        return this.save(payload.name ?? id);
      }
      case "undo":
        await this.pause();
        this.world.store.undo();
        return { revision: this.world.store.document.revision };
      case "redo":
        await this.pause();
        this.world.store.redo();
        return { revision: this.world.store.document.revision };
      case "garden":
      case "workshop": {
        if (
          this.world.entries.has(
            command === "workshop" ? "kinetic.plinth" : "garden.seed",
          )
        )
          throw Error("This example already exists");
        const { operations, program } = (
          command === "workshop" ? kineticWorkshop : resonanceGarden
        )(payload.origin ?? [0, 0, -1.5]);
        const result = this.world.store.apply({
          requestId: crypto.randomUUID(),
          operations,
        });
        this.world.pause(false);
        await this.programs.install(program);
        this.save();
        return result;
      }
      default:
        throw Error(`Unknown world command: ${command}`);
    }
  }
  async dispatch(action, send) {
    try {
      const result = await this.handle(action.command, action.payload ?? {});
      send({
        type: "event:action_completed",
        completedActionId: action.actionId,
        action: action.type,
        status: "completed",
        result,
        sessionId: action.sessionId,
        timestamp: Date.now(),
      });
    } catch (error) {
      send({
        type: "event:action_completed",
        completedActionId: action.actionId,
        action: action.type,
        status: "failed",
        error: String(error.message).slice(0, 300),
        sessionId: action.sessionId,
        timestamp: Date.now(),
      });
    }
  }
}
