// @ts-nocheck
/** Human controls edit the same world agents observe. No private renderer shortcuts. */
export function attachCreationStudio(
  service,
  input,
  { origin = () => [0, 0, -1.5], focus = () => {} } = {},
) {
  const style = document.createElement("style");
  style.textContent = `
  #creation-studio{position:fixed;left:calc(var(--sidebar-w,275px) + 18px);top:65px;z-index:35;font-family:'Azeret Mono',monospace;font-size:11px;color:#151728;max-width:calc(100vw - 36px)}
  body.sidebar-collapsed #creation-studio,body.ar-active #creation-studio{left:18px}
  #creation-studio *{box-sizing:border-box;font-family:inherit}
  #creation-studio button{font-size:11px;font-weight:600;cursor:pointer;border:1.5px solid #202439;border-radius:7px;background:#fff;color:#202439;box-shadow:3px 3px 0 #202439;padding:8px 11px;transition:transform .12s,box-shadow .12s}
  #creation-studio button:hover{transform:translate(-1px,-1px);box-shadow:4px 4px 0 #202439}
  #creation-studio button:active{transform:translate(2px,2px);box-shadow:1px 1px 0 #202439}
  #creation-studio button:focus-visible,#creation-studio input:focus-visible,#creation-studio textarea:focus-visible{outline:3px solid #6E7DFF;outline-offset:3px}
  #creation-studio .primary{background:#6E7DFF;color:white}#creation-studio .row{display:flex;gap:9px;flex-wrap:wrap;align-items:center;margin-bottom:12px}
  #creation-studio .body{margin-top:12px;width:310px;max-width:100%;max-height:calc(100dvh - 220px);overflow:auto;padding:18px;border:1px solid #D9DDF4;background:#F8F9FF;box-shadow:0 16px 48px #14183525;border-radius:16px}
  #creation-studio .body[hidden]{display:none}#creation-studio h2{font-size:16px;letter-spacing:-.7px;margin:0 0 8px}#creation-studio p{line-height:1.6;margin:8px 0 14px}
  #creation-studio .muted{color:#555C79}#creation-studio hr{border:0;border-top:1px solid #D9DDF4;margin:18px 0}
  #creation-studio label{display:block;margin:10px 0 5px}#creation-studio input,#creation-studio select,#creation-studio textarea{color:#202439;background:white;border:1px solid #C5CAE4;border-radius:6px;padding:7px;width:100%;font-size:11px}
  #creation-studio input[type=color]{height:36px;padding:2px}#creation-studio textarea{min-height:160px;resize:vertical;tab-size:2}#creation-studio .objects{display:grid;gap:7px;max-height:160px;overflow:auto;padding:2px 5px 6px 0}
  #creation-studio .objects button{text-align:left;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}#creation-studio .objects button[aria-pressed=true]{background:#E0E4FF;border-color:#6E7DFF}
  #creation-studio .axes{display:grid;grid-template-columns:repeat(3,1fr);gap:6px}#creation-studio .status{max-width:270px;overflow-wrap:anywhere;font-size:10px;line-height:1.6}
  @media(max-width:600px){#creation-studio{top:78px;left:12px}.body{max-height:55dvh!important}}
  `;
  document.head.append(style);
  const root = document.createElement("section");
  root.id = "creation-studio";
  root.setAttribute("aria-label", "Creation studio");
  const make = (tag, text, parent = root) => {
    const e = document.createElement(tag);
    if (text) e.textContent = text;
    parent.append(e);
    return e;
  };
  const toolbar = make("div");
  toolbar.className = "row";
  const button = (label, fn, parent = toolbar, primary = false) => {
    const b = make("button", label, parent);
    b.type = "button";
    if (primary) b.className = "primary";
    b.onclick = () => run(fn);
    return b;
  };
  let opened = false;
  const open = button("Creations", () => {
    opened = !opened;
    body.hidden = !opened;
    open.setAttribute("aria-expanded", String(opened));
    refresh();
  });
  open.setAttribute("aria-expanded", "false");
  const pause = button("Pause creations", async () =>
    service.world.paused ? service.resume() : service.pause(),
  );
  const body = make("div");
  body.className = "body";
  body.hidden = true;
  make("h2", "Make something move.", body);
  make("p", "Grab it. Change it. Let it keep running.", body).className =
    "muted";
  const intro = make("div", null, body);
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
  button("Focus", focus, intro);
  const history = make("div", null, body);
  history.className = "row";
  button("Undo", () => service.handle("undo"), history);
  button("Redo", () => service.handle("redo"), history);
  const status = make("p", "Ready to create.", body);
  status.className = "status";
  status.setAttribute("role", "status");
  status.id = "creation-status";
  const list = make("div", null, body);
  list.className = "objects";
  list.setAttribute("aria-label", "World objects");
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
    const signature = JSON.stringify([
      entries.map((e) => [
        e.id,
        e.name,
        e.asset ? service.world.entries.get(e.id)?.status : null,
      ]),
      input.selected,
      service.programs.inspect().map((p) => [p.id, p.status, p.error]),
    ]);
    if (signature !== lastSignature) {
      lastSignature = signature;
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
    refresh();
  };
  document.body.append(root);
  refresh();
  return { refresh, root };
}
