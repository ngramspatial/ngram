// @ts-nocheck
/** Project links are transport state. Blender owns geometry; humans own placement. */
export class BlenderProjects {
  records = new Map();
  origin = () => [0, 0, -1.5];
  constructor(service) {
    this.service = service;
    this.timer = setInterval(() => this.flush(), 200);
    this.timer.unref?.();
  }
  validate(p) {
    if (!p || !/^[a-zA-Z0-9_-]{1,64}$/.test(p.projectId) ||
        !Number.isSafeInteger(p.revision) || p.revision < 1 ||
        !new RegExp('^/api/shells/[a-z0-9-]+/blender/' + p.projectId + '$').test(p.base))
      throw Error('Invalid Blender project link');
    if (p.position != null && (!Array.isArray(p.position) || p.position.length !== 3 || !p.position.every(Number.isFinite)))
      throw Error('Blender placement must be three finite metre coordinates');
    return { projectId: p.projectId, name: String(p.name || 'Blender project').slice(0, 120),
      revision: p.revision, base: p.base, position: p.position, state: p.state };
  }
  attach(payload) {
    const p = this.validate(payload);
    const key = p.base;
    let record = this.records.get(key);
    // Host status/Stop may update an existing link, but cannot create a new object.
    if (payload.stateOnly) {
      if (!record) return { projectId: p.projectId, state: p.state, attached: false };
      record.state = p.state ?? record.state;
      this.service.onChange?.();
      return this.inspect(record);
    }
    if (record) record.state = p.state ?? record.state;
    if (record && p.revision <= Math.max(record.revision, record.pending?.revision ?? 0)) {
      this.service.onChange?.();
      return this.inspect(record);
    }
    const id = record?.id ?? `blender.${p.base.split('/')[3]}.${p.projectId}`;
    if (!record) {
      record = { ...p, id, revision: 0, paused: false, detached: false, pending: null };
      this.records.set(key, record);
    }
    record.pending = p;
    record.state = p.state;
    this.flush();
    return this.inspect(record);
  }
  inspect(record) {
    const entry = this.service.world.entries.get(record.id);
    return { ...record, pending: record.pending?.revision ?? null,
      status: entry?.status ?? (record.detached ? 'removed' : 'waiting'), error: record.error || entry?.error,
      displayedRevision: entry?.status === 'ready' ? record.revision : record.displayedRevision ?? null };
  }
  list() { return [...this.records.values()].map(r => this.inspect(r)); }
  forEntity(id) { return [...this.records.values()].find(r => r.id === id); }
  flush() {
    const world = this.service.world;
    for (const record of this.records.values()) {
      const entry = world.entries.get(record.id);
      if (entry?.status === 'ready') record.displayedRevision = record.revision;
      if (record.revision && !entry) record.detached = true;
      if (!record.pending || record.paused || record.detached || world.store.heldBy(record.id)) continue;
      const p = record.pending;
      const asset = { url: `${p.base}/${p.revision}/preview.glb`, format: 'glb', fit: 1, normalize: false, preserveMaterials: true };
      try {
        world.store.apply({ requestId: crypto.randomUUID(), operations: [entry
          ? { op: 'entity.patch', id: record.id, patch: { asset: { ...asset, preserveMaterials: entry.spec.asset.preserveMaterials } } }
          : { op: 'entity.create', entity: { id: record.id, kind: 'asset', name: p.name,
              tags: ['blender'], asset, grabbable: true,
              transform: { position: p.position ?? this.origin() } } } ] }, 'blender');
        record.revision = p.revision; record.pending = null; record.error = null;
        this.service.onChange?.();
      } catch (error) {
        record.error = String(error.message); record.pending = null;
        this.service.onChange?.();
      }
    }
  }
  async refresh(record) {
    try {
      const response = await fetch(`${record.base}/status`, { signal: AbortSignal.timeout(10000) });
      if (!response.ok) throw Error(`Project host unavailable (${response.status})`);
      const data = await response.json();
      if (this.records.get(record.base) !== record) return;
      if (!data.ok) throw Error(data.error || 'Project host unavailable');
      record.state = data.state; record.error = data.error;
      if (data.snapshot?.revision > record.revision)
        this.attach({ ...record, name: data.name, revision: data.snapshot.revision, state: data.state });
      this.service.onChange?.();
      return data;
    } catch (error) {
      record.error = String(error.message); this.service.onChange?.();
    }
  }
  async command(id, command) {
    const record = this.forEntity(id);
    if (!record) throw Error('Unknown Blender link');
    if (command === 'pause') record.paused = true;
    else if (command === 'resume') { record.paused = false; await this.refresh(record); this.flush(); }
    else if (command === 'refresh') await this.refresh(record);
    else if (command === 'stop') {
      const response = await fetch(`${record.base}/stop`, { method: 'POST', signal: AbortSignal.timeout(10000) });
      if (!response.ok) throw Error(`Could not stop Blender (${response.status})`);
      const result = await response.json();
      if (!result.ok) throw Error(result.error || 'Could not stop Blender');
      record.state = result.state;
    } else throw Error('Unknown Blender control');
    this.service.save(); this.service.onChange?.();
    return this.inspect(record);
  }
  export() {
    return [...this.records.values()].filter(r => this.service.world.entries.has(r.id)).map(r => ({
      ...this.validate(r), id: r.id, paused: r.paused,
    }));
  }
  restore(records = []) {
    this.records.clear();
    for (const r of records) {
      const p = this.validate(r);
      const id = `blender.${p.base.split('/')[3]}.${p.projectId}`;
      if (!this.service.world.entries.has(id)) continue;
      const record = { ...p, id, paused: Boolean(r.paused), pending: null, detached: false };
      this.records.set(p.base, record);
      void this.refresh(record);
    }
  }
  clear() { this.records.clear(); }
}
