// @ts-nocheck
import { FIGMENT_PACKAGE_LIMIT, readFigmentAsset } from "./figment-library.js";
import { FIGMENT_PRESETS } from "./figment-presets.js";

const download = (bytes, filename, type = "application/json") => {
  const url = URL.createObjectURL(new Blob([bytes], { type }));
  const link = document.createElement("a");
  link.href = url; link.download = filename; link.click();
  setTimeout(() => URL.revokeObjectURL(url), 30000);
};

export function figmentLibraryUI(service, parent, { make, button, run, origin, select }) {
  const details = make("details", null, parent);
  make("summary", "Figment library", details);
  make("p", "Publish a version, share its file, or bring a Figment into this room.", details).className = "muted";
  const row = make("div", null, details); row.className = "row";
  const list = make("div", null, details);
  const upload = make("input", null, details);
  upload.type = "file"; upload.accept = ".figment.json,.json"; upload.hidden = true;
  upload.setAttribute("aria-label", "Import Figment package");
  upload.onchange = () => run(async () => {
    try {
      const file = upload.files[0];
      if (!file) return;
      if (file.size > FIGMENT_PACKAGE_LIMIT) throw Error("Figment package must be under 192 MB");
      const result = await service.figments.job(async signal => service.figments.library.store(await file.text(), signal));
      await refresh();
      return { message: `${result.title} imported. Choose Place to add it to the room.` };
    } finally { upload.value = ""; }
  });
  button("Import Figment", () => upload.click(), row, true);
  button("Refresh library", () => refresh(), row);
  let loading = false;
  async function refresh() {
    if (loading) return;
    loading = true;
    try {
      const versions = await service.figments.library.list();
      list.replaceChildren();
      if (!versions.length) make("p", "Your published and imported Figments will appear here.", list).className = "muted";
      for (const version of versions) {
        const item = make("div", null, list); item.className = "figment-version";
        make("h3", `${version.title} · ${version.version}`, item);
        if (version.description) make("p", version.description, item).className = "muted";
        make("p", `${(version.bytes / 1048576).toFixed(1)} MB${version.editableSource ? " · Editable Blender source" : ""}`, item).className = "muted";
        const actions = make("div", null, item); actions.className = "row";
        button("Place", async () => {
          const result = await service.figments.place(version.id, origin().map((n, i) => n + (i === 1 ? 1 : 0)));
          select(result.id);
          return { message: "Figment placed. Resume creations when you are ready." };
        }, actions, true);
        button("Export to share", async () => {
          return service.figments.handle({ command: "export", packageId: version.id });
        }, actions);
      }
    } finally { loading = false; }
  }
  details.addEventListener("toggle", () => { if (details.open) void run(refresh); });
  return { refresh };
}

export function figmentInspector(service, input, e, parent, { make, button, run, inspect, library }) {
  const id = e.id;
  const update = async payload => { const result = await service.figments.handle({ id, ...payload }); inspect(id); return result; };
  const current = () => service.world.entries.get(id)?.spec;
  const field = (label, value, type, callback, container = parent, options = {}) => {
    const row = make("label", label, container), control = make("input", null, row);
    control.type = type;
    if (type === "checkbox") control.checked = value;
    else control.value = control.defaultValue = String(value);
    for (const [key, value] of Object.entries(options)) control[key] = String(value);
    control.onchange = () => run(() => callback(type === "checkbox" ? control.checked : type === "number" || type === "range" ? Number(control.value) : control.value));
    return control;
  };
  const select = (label, value, options, callback, container = parent) => {
    const row = make("label", label, container), control = make("select", null, row);
    for (const option of options) { const item = make("option", option, control); item.value = option; }
    control.value = value;
    control.onchange = () => run(() => callback(control.value));
    return control;
  };
  const f = e.figment;
  const section = make("section", null, parent); section.className = "figment-inspector";
  make("h3", f ? "Figment" : "Make interactive", section);
  if (!f) {
    const row = make("div", null, section); row.className = "row";
    for (const [name, preset] of Object.entries(FIGMENT_PRESETS))
      button(preset.title, () => update({ command: "attach", preset: name }), row, name === "prop");
  } else {
    const actions = make("div", null, section); actions.className = "row";
    for (const [action, descriptor] of Object.entries(f.actions))
      button(descriptor.label, () => service.figments.action(id, action), actions, true);
    if (Object.keys(f.actions).length) make("p", "Double-click or press E for the first action. In XR, squeeze the controller while holding it.", section).className = "muted";
    for (const [name, property] of Object.entries(f.properties)) {
      if (!property.editable) {
        make("p", `${property.label}: ${property.value}`, section).className = "muted";
        continue;
      }
      field(`${property.label}${property.unit ? ` · ${property.unit}` : ""}`, property.value,
        property.type === "boolean" ? "checkbox" : property.type === "string" ? "text" : property.type,
        value => service.figments.setProperties(id, { [name]: value }), section,
        property.type === "number" ? { min: property.min, max: property.max, step: property.step } : {});
    }
    const behavior = service.programs.inspect(service.figments.programId(id))[0];
    if (f.behavior) {
      make("p", `Behavior · ${behavior?.status ?? "not installed"}${behavior?.error ? ` · ${behavior.error}` : ""}`, section).className = "status";
      const controls = make("div", null, section); controls.className = "row";
      button(behavior?.status === "running" ? "Pause behavior" : "Run behavior", async () => {
        const action = behavior?.status === "running" ? "pause" : "resume";
        await update({ command: "behavior", action });
        if (action === "resume") service.world.pause(false);
      }, controls, true);
      button("Reset behavior", () => update({ command: "behavior", action: "reset" }), controls);
    }
    const grips = make("details", null, section);
    make("summary", `Grips · ${Object.keys(f.grips).length}`, grips);
    field("Show grip markers", input.showGrips, "checkbox", value => { input.showGrips = value; }, grips);
    const anchors = service.world.sample(id).anchors ?? {};
    for (const [name, grip] of Object.entries(f.grips)) {
      const anchor = f.anchors[grip.anchor];
      make("h4", name, grips);
      if (anchors[grip.anchor]?.error) make("p", anchors[grip.anchor].error, grips).className = "status";
      make("p", `${anchor.part}${anchor.node ? ` / ${anchor.node}` : ""}`, grips).className = "muted";
      for (const prop of ["position", "rotation"]) {
        const row = make("div", null, grips); row.className = "axes";
        for (let i = 0; i < 3; i++) field(`${name} ${prop} ${"XYZ"[i]}`, anchor[prop][i], "number", value => {
          const anchors = structuredClone(current().figment.anchors);
          anchors[grip.anchor][prop][i] = value;
          return update({ command: "configure", definition: { anchors } });
        }, row, { step: .01 });
      }
      select(`${name} hand`, grip.hand, ["either", "left", "right"], value => update({ command: "configure", definition: { grips: { ...current().figment.grips, [name]: { ...grip, hand: value } } } }), grips);
      select(`${name} two hands`, grip.twoHand, ["aim", "scale"], value => update({ command: "configure", definition: { grips: { ...current().figment.grips, [name]: { ...grip, twoHand: value } } } }), grips);
    }
    const authoring = make("details", null, section);
    make("summary", "Edit definition & code", authoring);
    make("p", "Named parts, anchors, grips, properties and actions travel with the Figment. Saving code leaves behavior paused.", authoring).className = "muted";
    const definition = make("textarea", null, authoring);
    definition.setAttribute("aria-label", "Figment definition JSON");
    const { behavior: _behavior, ...metadata } = f;
    definition.value = definition.defaultValue = JSON.stringify(metadata, null, 2);
    button("Save definition", () => update({ command: "configure", definition: JSON.parse(definition.value) }), authoring);
    const source = make("textarea", null, authoring);
    source.setAttribute("aria-label", "Figment behavior source");
    source.value = source.defaultValue = f.behavior?.source ?? "return { tick() {}, event(e) {} };";
    button("Save behavior", () => update({ command: "behavior", source: source.value, hz: f.behavior?.hz ?? 20 }), authoring);
    const publish = make("details", null, section);
    make("summary", "Publish & share", publish);
    const title = field("Figment title", f.title, "text", () => {}, publish);
    const version = field("Version", f.version, "text", () => {}, publish);
    make("p", "A published version is a fixed snapshot in your library. Export its file to share the model, code, physics and source.", publish).className = "muted";
    button("Publish version", async () => {
      // Preserve typed title/version until the entire operation completes.
      await service.figments.attach({ id, definition: { title: title.value, version: version.value } });
      const result = await service.figments.handle({ command: "publish", id });
      await library.refresh();
      inspect(id);
      return { message: `${result.title} ${result.version} published to your Figment library.` };
    }, publish, true);
    if (f.source) button("Download Blender source", async () => {
      const bytes = await service.figments.job(signal => readFigmentAsset(f.source.url, { signal, maxBytes: 128 * 1024 * 1024 }));
      download(bytes, f.source.filename, "application/octet-stream");
    }, publish);
    button("Remove Figment properties", () => update({ command: "detach" }), authoring);
  }
  // Physics is also available before converting a primitive into a Figment.
  if (!["shape", "asset", "group"].includes(e.kind)) return;
  const physics = make("details", null, parent);
  make("summary", "Physics", physics);
  const patchPhysics = patch => service.world.store.apply({ requestId: crypto.randomUUID(), operations: [{ op: "entity.patch", id, patch: { physics: patch } }] }, "human");
  if (!e.physics) {
    button("Add physics", () => {
      let colliders;
      if (e.kind !== "shape" || ["mesh", "line", "torus"].includes(e.geometry.shape)) {
        const size = e.bounds ? e.bounds.max.map((n, i) => Math.max(.01, (n - e.bounds.min[i]) / e.transform.scale[i])) : [.2, .2, .2];
        colliders = [{ id: "body", shape: "box", size }];
      }
      patchPhysics({ mode: "dynamic", ...(colliders ? { colliders } : {}) });
      inspect(id);
    }, physics, true);
    return;
  }
  select("Body mode", e.physics.mode, ["dynamic", "fixed", "kinematic"], mode => { patchPhysics({ mode }); inspect(id); }, physics);
  for (const [name, label, min, max, step] of [
    ["mass", "Mass · kg", .001, 1000, .1], ["friction", "Friction", 0, 10, .05],
    ["restitution", "Bounce", 0, 1, .05], ["linearDamping", "Linear damping", 0, 100, .1], ["angularDamping", "Angular damping", 0, 100, .1],
  ]) field(label, e.physics[name], "number", value => patchPhysics({ [name]: value }), physics, { min, max, step });
  const gravity = make("div", null, physics); gravity.className = "axes";
  for (let i = 0; i < 3; i++) field(`Gravity ${"XYZ"[i]}`, e.physics.gravity[i], "number", value => {
    const g = [...current().physics.gravity]; g[i] = value; return patchPhysics({ gravity: g });
  }, gravity, { min: -100, max: 100, step: .1 });
  for (const [name, label] of [["translations", "Move"], ["rotations", "Rotate"]]) {
    const row = make("div", null, physics); row.className = "axes";
    for (let i = 0; i < 3; i++) field(`${label} ${"XYZ"[i]}`, e.physics[name][i], "checkbox", value => {
      const axes = [...current().physics[name]]; axes[i] = value; return patchPhysics({ [name]: axes });
    }, row);
  }
  const colliderDetails = make("details", null, physics);
  make("summary", "Collision shapes & sensors", colliderDetails);
  make("p", "Compound shapes use object-local metres. Enable sensor for enter/exit events. Empty uses a primitive’s shape.", colliderDetails).className = "muted";
  const colliders = make("textarea", null, colliderDetails);
  colliders.setAttribute("aria-label", "Physics colliders JSON");
  colliders.value = colliders.defaultValue = JSON.stringify(e.physics.colliders, null, 2);
  button("Apply colliders", () => { patchPhysics({ colliders: JSON.parse(colliders.value) }); inspect(id); }, colliderDetails);
  button("Remove physics", () => { patchPhysics(null); inspect(id); }, physics);
  const joints = service.world.store.document.joints.filter(j => j.a === id || j.b === id);
  if (joints.length) {
    const connections = make("details", null, physics);
    make("summary", "Connections & constraints", connections);
    for (const joint of joints) {
      const details = make("details", null, connections);
      const name = Object.entries(f?.joints ?? {}).find(([, value]) => value === joint.id)?.[0] ?? joint.id;
      make("summary", `${name} · ${joint.type}`, details);
      make("p", `${joint.a} ↔ ${joint.b}`, details).className = "muted";
      const change = patch => {
        const live = service.world.store.document.joints.find(j => j.id === joint.id);
        if (!live) throw Error("Connection was removed");
        return service.world.store.apply({ requestId: crypto.randomUUID(), operations: [
          { op: "joint.delete", id: joint.id }, { op: "joint.create", joint: { ...live, ...patch } },
        ] }, "human");
      };
      if (["hinge", "slider"].includes(joint.type)) {
        field("Motor speed", joint.velocity, "number", velocity => change({ velocity }), details, { min: -100, max: 100, step: .1 });
        field("Motor strength", joint.strength, "number", strength => change({ strength }), details, { min: 0, max: 1000, step: .5 });
        field("Limit motion", !!joint.limits, "checkbox", value => { change({ limits: value ? [-1,1] : null }); inspect(id); }, details);
        if (joint.limits) for (let i = 0; i < 2; i++) field(i ? "Upper limit" : "Lower limit", joint.limits[i], "number", value => {
          const limits = [...service.world.store.document.joints.find(j => j.id === joint.id).limits]; limits[i] = value; return change({ limits });
        }, details, { step: .05 });
      }
      if (["rope", "spring"].includes(joint.type)) field("Length · metres", joint.length, "number", length => change({ length }), details, { min: .001, max: 100, step: .05 });
      if (joint.type === "spring") {
        field("Stiffness", joint.stiffness, "number", stiffness => change({ stiffness }), details, { min: 0, max: 10000, step: 1 });
        field("Spring damping", joint.damping, "number", damping => change({ damping }), details, { min: 0, max: 1000, step: .1 });
      }
    }
  }
}
