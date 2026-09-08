// @ts-nocheck
/** Small, editable examples. Every preset uses the same public behavior API. */
export const FIGMENT_PRESETS = {
  prop: {
    title: "Physical prop",
    description: "A tangible object with an authored grip and adjustable physics.",
    anchors: { handle: { position: [0, 0, 0], rotation: [0, 0, 0] } },
    grips: { hold: { anchor: "handle", twoHand: "aim" } },
  },
  lantern: {
    title: "Living lantern",
    description: "Toggle the light, change its tint, or adjust the pulse. All behavior runs in the room.",
    anchors: { handle: { position: [0, .15, 0] } },
    grips: { hold: { anchor: "handle" } },
    properties: {
      on: { type: "boolean", label: "Light", value: true },
      brightness: { type: "number", label: "Brightness", value: 2, min: 0, max: 10, step: .1 },
      pulse: { type: "number", label: "Pulse", value: .6, min: 0, max: 4, step: .1, unit: "Hz" },
      tint: { type: "color", label: "Tint", value: "#6E7DFF" },
    },
    actions: { toggle: { label: "Toggle light" } },
    behavior: { source: `return {
  event(e) { if (e.type === "action" && e.data.action === "toggle") api.setProperty("on", !api.property("on")); },
  tick() {
    const on = api.property("on"), pulse = api.property("pulse");
    const glow = on ? api.property("brightness") * (pulse ? .75 + .25 * Math.sin(api.time * pulse * Math.PI * 2) : 1) : 0;
    api.patch("self", { material: { emissive: api.property("tint"), glow } });
    if (api.part("light")) api.patch("light", { visible: on, material: { color: api.property("tint"), glow: Math.max(.001, glow) } });
  }
};`, hz: 20 },
  },
  kinetic: {
    title: "Kinetic sculpture",
    description: "A programmable motor, or a spinning sculpture when no joint is bound.",
    properties: {
      running: { type: "boolean", label: "Running", value: true },
      speed: { type: "number", label: "Speed", value: 1, min: -10, max: 10, step: .1, unit: "rad/s" },
      strength: { type: "number", label: "Motor strength", value: 5, min: 0, max: 100, step: .5 },
    },
    actions: { toggle: { label: "Start / stop" }, reverse: { label: "Reverse" } },
    behavior: { source: `return {
  event(e) {
    if (e.type !== "action") return;
    if (e.data.action === "toggle") api.setProperty("running", !api.property("running"));
    if (e.data.action === "reverse") api.setProperty("speed", -api.property("speed"));
  },
  tick() {
    const speed = api.property("running") ? api.property("speed") : 0;
    if (api.params.__figment.joints.motor) { api.motor("motor", speed, api.property("strength")); return; }
    const e = api.self;
    if (!e || e.heldBy || e.physics?.mode === "dynamic") return;
    const r = [...e.transform.rotation]; r[1] += speed * api.dt;
    api.patch("self", { transform: { rotation: r } });
  }
};`, hz: 30 },
  },
  impact: {
    title: "Impact crystal",
    description: "Glows when struck, then cools. Sensitivity and color are yours to change.",
    properties: {
      sensitivity: { type: "number", label: "Sensitivity", value: 2, min: .1, max: 20, step: .1 },
      tint: { type: "color", label: "Glow color", value: "#6E7DFF" },
    },
    actions: { excite: { label: "Excite" } },
    behavior: { source: `return {
  event(e) {
    if (e.type === "collision") api.state.energy = Math.min(10, (api.state.energy || 0) + Math.max(.2, e.data.impulse || 0) * api.property("sensitivity"));
    if (e.type === "action" && e.data.action === "excite") api.state.energy = 8;
  },
  tick() {
    api.state.energy = Math.max(0, (api.state.energy || 0) - api.dt * 3);
    api.patch("self", { material: { emissive: api.property("tint"), glow: api.state.energy } });
  }
};`, hz: 30 },
  },
};
