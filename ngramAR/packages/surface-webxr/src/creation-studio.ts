// @ts-nocheck
/** Human controls edit the same world agents observe. No private renderer shortcuts. */
export function attachCreationStudio(
  service,
  input,
  { origin = () => [0, 0, -1.5], focus = () => {} } = {},
) {
  const root = document.createElement("section");
  root.id = "creation-studio";
  root.className = "context-drawer";
  root.hidden = true;
  root.setAttribute("aria-label", "Objects");
  const make = (tag, text, parent = root) => {
    const e = document.createElement(tag);
    if (["input", "select", "textarea"].includes(tag)) e.className = "modal-input";
    if (tag === "label") e.className = "modal-label";
    if (text) e.textContent = text;
    parent.append(e);
    return e;
  };
  const header = make("div");
  header.className = "drawer-header";
  make("h2", "Objects", header).className = "drawer-shell-name";
  const body = make("div");
  body.className = "body";
  const toolbar = make("div", null, body);
  toolbar.className = "row";
  const button = (label, fn, parent = toolbar, primary = false) => {
    const b = make("button", label, parent);
    b.type = "button";
    b.className = primary ? "modal-btn modal-btn-primary" : "modal-btn";
    b.onclick = () => run(fn);
    return b;
  };
  let opened = false;
  const open = document.createElement("button");
  open.type = "button";
  open.className = "topbar-btn";
  open.setAttribute("aria-label", "Objects");
  open.title = "Objects";
  open.setAttribute("aria-controls", root.id);
  open.innerHTML = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="m12 2 9 5v10l-9 5-9-5V7l9-5Zm0 2.3L6 7.6l6 3.3 6-3.3-6-3.3Zm-7 5v6.5l6 3.4v-6.5l-6-3.4Zm8 9.9 6-3.4V9.3l-6 3.4v6.5Z"/></svg>';
  document.querySelector('.topbar-actions')?.prepend(open);
  function setOpen(value) {
    opened = value;
    root.hidden = !opened;
    root.classList.toggle("open", opened);
    open.classList.toggle("active", opened);
    open.setAttribute("aria-expanded", String(opened));
    if (opened) document.getElementById('context-drawer')?.classList.remove('open');
    refresh();
  }
  open.onclick = () => setOpen(!opened);
  open.setAttribute("aria-expanded", "false");
  const close = button("Close", () => { setOpen(false); open.focus(); }, header);
  close.className = 'drawer-close';
  close.setAttribute('aria-label', 'Close objects');
  close.innerHTML = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="m6.4 5 5.6 5.6L17.6 5 19 6.4 13.4 12l5.6 5.6-1.4 1.4-5.6-5.6L6.4 19 5 17.6l5.6-5.6L5 6.4 6.4 5Z"/></svg>';
  document.getElementById('drawer-toggle')?.addEventListener('click', () => setOpen(false));
  root.addEventListener('keydown', e => { if (e.key === 'Escape') { setOpen(false); open.focus(); } });
  const pause = button("Pause creations", async () =>
    service.world.paused ? service.resume() : service.pause(),
  );
  button("Focus", focus, toolbar);
  const examples = make("details", null, body);
  make("summary", "Examples", examples);
  const intro = make("div", null, examples);
  intro.className = "row";
  button(
    "Kinetic workshop",
    async () => {
      await service.handle("workshop", { origin: origin() });
      focus();
    },
    intro,
    true,
  );
  button(
    "Resonance garden",
    async () => {
      await service.handle("garden", {
        origin: origin().map((n, i) => n + (i === 0 ? -2.6 : 0)),
      });
      focus();
    },
    intro,
    true,
  );
  const history = make("div", null, body);
  history.className = "row";
  button("Undo", () => service.handle("undo"), history);
  button("Redo", () => service.handle("redo"), history);
  const status = make("p", "Select an object to inspect it.", body);
  status.className = "status";
  status.setAttribute("role", "status");
  status.id = "creation-status";
  const list = make("div", null, body);
  list.className = "objects";
  list.setAttribute("aria-label", "World objects");
  const empty = make("p", "Ask your agent to build something. Blender projects, shapes, and interactive creations appear here.", body);
  empty.className = 'muted';
  const createRow = make("div", null, body);
  createRow.className = "row";
  for (const [label, shape] of [
    ["+ Cube", "box"],
    ["+ Orb", "sphere"],
  ])
    button(
      label,
      () => {
        const id = `shape.${crypto.randomUUID()}`;
        service.world.store.apply(
          {
            requestId: crypto.randomUUID(),
            operations: [
              {
                op: "entity.create",
                entity: {
                  id,
                  name: shape === "box" ? "Cube" : "Orb",
                  geometry: { shape },
                  transform: {
                    position: origin().map((n, i) => n + (i === 1 ? 1 : 0)),
                  },
                },
              },
            ],
          },
          "human",
        );
        input.select(id);
      },
      createRow,
    );
  const inspector = make("div", null, body);
  let inspectorId = null;
  let inspectorAssetStatus = null;
  function edit(patch) {
    if (!input.selected) return;
    service.world.store.apply(
      {
        requestId: crypto.randomUUID(),
        operations: [{ op: "entity.patch", id: input.selected, patch }],
      },
      "human",
    );
  }
  function inspect(id) {
    inspector.replaceChildren();
    inspectorId = id;
    const e = service.world.store.observe({ ids: [id], includeGeometry: true })
      .entities[0];
    if (!e) return;
    inspectorAssetStatus = e.asset ? e.status : null;
    make("hr", null, inspector);
    make("h2", e.name, inspector);
    make(
      "p",
      e.grabbable
        ? "Drag to move. Scroll for depth; Shift-scroll to resize; Alt-scroll to rotate. Release to throw."
        : "Fixed part of the creation.",
      inspector,
    ).className = "muted";
    const label = make("label", "Color", inspector),
      color = make("input", null, label);
    color.type = "color";
    color.value = e.material.color;
    color.onchange = () =>
      run(() => edit({ material: { color: color.value } }));
    for (const [field, title] of [
      ["position", "Position · metres"],
      ["rotation", "Rotation · radians"],
      ["scale", "Scale"],
    ]) {
      make("label", title, inspector);
      const row = make("div", null, inspector);
      row.className = "axes";
      for (let axis = 0; axis < 3; axis++) {
        const value = make("input", null, row);
        value.type = "number";
        value.step = field === "scale" ? ".1" : ".05";
        value.value = String(Number(e.transform[field][axis].toFixed(3)));
        value.setAttribute(
          "aria-label",
          `${title.split(" ·")[0]} ${"XYZ"[axis]}`,
        );
        value.onchange = () =>
          run(() => {
            const current = service.world.store
              .observe({ ids: [id] })
              .entities[0].transform[field].slice();
            current[axis] = Number(value.value);
            edit({ transform: { [field]: current } });
          });
      }
    }
    if (e.control) {
      const label = make("label", e.control.label, inspector);
      if (e.control.type === "slider") {
        const slider = make("input", null, label);
        slider.type = "range";
        slider.min = String(e.control.min);
        slider.max = String(e.control.max);
        slider.step = String(e.control.step);
        slider.value = String(e.control.value);
        slider.oninput = () =>
          run(() =>
            service.world.activate(
              id,
              (Number(slider.value) - e.control.min) /
                (e.control.max - e.control.min),
            ),
          );
      } else
        button(
          e.control.type === "toggle" ? "Toggle" : "Press",
          () => service.world.activate(id),
          inspector,
          true,
        );
    }
    const actions = make("div", null, inspector);
    actions.className = "row";
    actions.style.marginTop = "14px";
    if (e.asset) {
      const project = service.blender.forEntity(id);
      if (project) {
        const live = service.blender.inspect(project);
        make('p', `Blender · revision ${live.displayedRevision ?? 'loading'}${live.pending ? ' · update queued' : ''}${project.paused ? ' · updates paused' : ''}`, inspector).className = 'muted';
        if (project.error) make('p', project.error, inspector).className = 'status';
        button(project.paused ? 'Resume updates' : 'Pause updates', () => service.blender.command(id, project.paused ? 'resume' : 'pause'), actions);
        button('Refresh project', () => service.blender.command(id, 'refresh'), actions);
        button('Stop Blender', () => service.blender.command(id, 'stop'), actions);
        for (const [title, filename] of [['Download .blend', 'project.blend'], ['Download GLB', 'preview.glb']]) {
          const link = make('a', title, actions);
          link.className = 'modal-btn';
          link.href = `${project.base}/${project.revision}/${filename}`;
          link.download = filename;
        }
      }
      make(
        "p",
        e.status === "failed" ? `Load failed: ${e.error}` : `Asset ${e.status}`,
        inspector,
      );
      button(
        e.status === "loading" ? "Cancel load" : "Reload asset",
        () =>
          service.world.assetJob(
            e.status === "loading" ? "cancel" : "retry",
            id,
          ),
        actions,
      );
    }
    if (e.physics?.mode === "dynamic")
      button(
        "Push",
        () =>
          service.world.store.apply(
            {
              requestId: crypto.randomUUID(),
              operations: [{ op: "body.impulse", id, impulse: [1, 1, 0] }],
            },
            "human",
          ),
        actions,
      );
    button(
      "Duplicate",
      () => {
        const copy = structuredClone(
          service.world.store.document.entities.find((item) => item.id === id),
        );
        copy.transform = service.world.sample(id).transform;
        copy.id = `copy.${crypto.randomUUID()}`;
        copy.name += " copy";
        copy.transform.position[0] += 0.3;
        service.world.store.apply(
          {
            requestId: crypto.randomUUID(),
            operations: [{ op: "entity.create", entity: copy }],
          },
          "human",
        );
        input.select(copy.id);
      },
      actions,
    );
    button(
      "Delete",
      () => {
        service.world.store.apply(
          {
            requestId: crypto.randomUUID(),
            operations: [{ op: "entity.delete", id }],
          },
          "human",
        );
        input.select(null);
      },
      actions,
    );
  }
  make("hr", null, body);
  const programDetails = make("details", null, body);
  make("summary", "Programs", programDetails);
  const programs = make("div", null, programDetails);
  make(
    "p",
    "JavaScript runs here, without model calls. Return tick and event handlers; use api.get, api.emit, api.params and api.state.",
    programDetails,
  ).className = "muted";
  const code = make("textarea", null, programDetails);
  code.setAttribute("aria-label", "Creation program source");
  code.placeholder = "return { tick() { /* animate a creation */ } };";
  button(
    "Run on selected object",
    async () => {
      if (!input.selected) throw Error("Select an object first");
      await service.programs.install({
        id: `program.${input.selected}`,
        source: code.value,
        entityIds: [input.selected],
        params: {},
        state: {},
      });
      service.world.pause(false);
    },
    programDetails,
    true,
  );
  make("hr", null, body);
  const files = make("div", null, body);
  files.className = "row";
  button("Save", () => service.handle("save"), files);
  button(
    "Export",
    () => {
      const blob = new Blob([JSON.stringify(service.export(), null, 2)], {
          type: "application/json",
        }),
        url = URL.createObjectURL(blob),
        a = document.createElement("a");
      a.href = url;
      a.download = `${service.world.store.document.id}.ngram.json`;
      a.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    },
    files,
  );
  const upload = make("input", null, body);
  upload.type = "file";
  upload.accept = ".json";
  upload.hidden = true;
  upload.onchange = () =>
    run(async () => {
      const file = upload.files[0];
      if (!file) return;
      if (file.size > 4000000) throw Error("World import must be under 4 MB");
      await service.handle("import", JSON.parse(await file.text()));
      upload.value = "";
    });
  button("Import", () => upload.click(), files);
  let lastSignature = "",
    queued = false;
  function refresh() {
    queued = false;
    pause.textContent = service.world.paused
      ? "Resume creations"
      : "Pause creations";
    if (service.storageError) status.textContent = service.storageError;
    const entries = service.world.store.document.entities;
    empty.hidden = entries.length > 0;
    const signature = JSON.stringify([
      entries.map((e) => [
        e.id,
        e.name,
        e.asset ? service.world.entries.get(e.id)?.status : null,
      ]),
      input.selected,
      service.programs.inspect().map((p) => [p.id, p.status, p.error]),
      service.blender.list().map(p => [p.id, p.revision, p.pending, p.paused, p.state, p.error]),
    ]);
    if (signature !== lastSignature) {
      lastSignature = signature;
      if (input.selected && !inspector.contains(document.activeElement)) inspect(input.selected);
      list.replaceChildren();
      for (const e of entries) {
        const b = button(
          e.asset
            ? `${e.name} · ${service.world.entries.get(e.id)?.status}`
            : e.name,
          () => {
            input.select(e.id);
            inspect(e.id);
          },
          list,
        );
        b.setAttribute("aria-pressed", String(input.selected === e.id));
      }
      programs.replaceChildren();
      for (const p of service.programs.inspect()) {
        make(
          "p",
          `${p.name} · ${p.status}${p.error ? " · " + p.error : ""}`,
          programs,
        );
        const row = make("div", null, programs);
        row.className = "row";
        button(
          p.status === "running" ? "Pause program" : "Resume program",
          () =>
            service.programs.command(
              p.status === "running" ? "pause" : "resume",
              p.id,
            ),
          row,
        );
        button(
          "Remove program",
          () => service.programs.command("remove", p.id),
          row,
        );
      }
    }
    if (inspectorId !== input.selected) inspect(input.selected);
    const selectedEntry = service.world.entries.get(input.selected);
    if (
      selectedEntry?.spec.asset &&
      inspectorAssetStatus !== selectedEntry.status &&
      !inspector.contains(document.activeElement)
    )
      inspect(input.selected);
    root.dataset.revision = String(service.world.store.document.revision);
    root.dataset.entityCount = String(entries.length);
    root.dataset.programs = JSON.stringify(
      service.programs.inspect().map((p) => ({
        id: p.id,
        status: p.status,
        frames: p.frames,
        error: p.error,
      })),
    );
  }
  async function run(fn) {
    try {
      const result = await fn();
      status.textContent = result?.error || "Ready.";
      refresh();
    } catch (error) {
      status.textContent = String(error.message);
      refresh();
    }
  }
  service.onChange = () => {
    if (!queued) {
      queued = true;
      setTimeout(refresh, 250);
    }
  };
  input.onSelect = () => {
    inspect(input.selected);
    if (input.selected) setOpen(true);
    refresh();
  };
  document.body.append(root);
  refresh();
  return { refresh, root };
}
