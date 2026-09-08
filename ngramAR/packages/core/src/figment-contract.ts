/** Portable object definitions. Geometry lives in the world; behavior uses named parts. */
import { identifier, number, vector, type V3 } from "./world-contract.js";

export const FIGMENT_PROTOCOL = "ngram.figment/1" as const;
export interface FigmentProperty {
  type: "number" | "boolean" | "string" | "color";
  label: string;
  value: number | boolean | string;
  editable: boolean;
  min?: number;
  max?: number;
  step?: number;
  unit?: string;
}
export interface FigmentAnchor {
  part: string;
  node: string | null;
  position: V3;
  rotation: V3;
}
export interface FigmentGrip {
  anchor: string;
  hand: "either" | "left" | "right";
  radius: number;
  twoHand: "aim" | "scale";
}
export interface FigmentDefinition {
  protocol: typeof FIGMENT_PROTOCOL;
  title: string;
  description: string;
  version: string;
  parts: Record<string, string>;
  joints: Record<string, string>;
  anchors: Record<string, FigmentAnchor>;
  grips: Record<string, FigmentGrip>;
  properties: Record<string, FigmentProperty>;
  actions: Record<string, { label: string }>;
  behavior: { source: string; hz: number } | null;
  source: { url: string; filename: string } | null;
}

export function object(value: unknown, label: string): Record<string, any> {
  if (!value || typeof value !== "object" || Array.isArray(value))
    throw Error(`${label} must be an object`);
  return value as Record<string, any>;
}
export function fields(value: Record<string, any>, names: string[], label: string) {
  for (const key of Object.keys(value))
    if (!names.includes(key)) throw Error(`Unknown ${label} field: ${key}`);
}
export function label(value: unknown, fallback: string, max = 160): string {
  if (value === undefined) return fallback;
  if (typeof value !== "string" || value.length > max)
    throw Error(`Expected text of at most ${max} characters`);
  return value;
}
export function flag(value: unknown, fallback: boolean): boolean {
  if (value === undefined) return fallback;
  if (typeof value !== "boolean") throw Error("Expected boolean");
  return value;
}
export function option<T extends string>(value: unknown, choices: T[], fallback: T): T {
  if (value === undefined) return fallback;
  if (!choices.includes(value as T)) throw Error(`Expected ${choices.join("|")}`);
  return value as T;
}
export function assetReference(value: unknown): string {
  const url = label(value, "", 2048);
  if (/^figment:[a-f0-9]{64}$/.test(url)) return url;
  if (/^\/(?!\/)/.test(url) && !url.includes("\\")) return url;
  if (!/^https?:\/\//i.test(url)) throw Error("Expected HTTP(S), root-relative or packaged asset URL");
  const parsed = new URL(url);
  if (parsed.username || parsed.password) throw Error("Do not put credentials in asset URLs");
  return url;
}
function dictionary<T>(raw: unknown, max: number, parse: (v: any, key: string) => T): Record<string, T> {
  const entries = Object.entries(object(raw ?? {}, "named fields"));
  if (entries.length > max) throw Error(`Use at most ${max} named fields`);
  return Object.fromEntries(entries.map(([key, value]) => {
    identifier(key);
    if (["__proto__", "prototype", "constructor", "self"].includes(key))
      throw Error(`Reserved name: ${key}`);
    return [key, parse(value, key)];
  }));
}
export function propertyValue(property: FigmentProperty, value: unknown): number | string | boolean {
  switch (property.type) {
    case "number": return number(value, property.value as number, property.min ?? -10000, property.max ?? 10000);
    case "boolean": return flag(value, property.value as boolean);
    case "color": {
      if (typeof value !== "string" || !/^#[0-9a-f]{6}$/i.test(value)) throw Error("Expected #RRGGBB color");
      return value;
    }
    case "string": return label(value, property.value as string, 2048);
  }
}
export function parseFigment(raw: unknown): FigmentDefinition {
  const f = object(raw, "figment");
  fields(f, ["protocol", "title", "description", "version", "parts", "joints", "anchors", "grips", "properties", "actions", "behavior", "source"], "figment");
  if (f.protocol !== undefined && f.protocol !== FIGMENT_PROTOCOL) throw Error("Unsupported Figment protocol");
  const version = label(f.version, "1.0.0", 40);
  if (!/^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$/.test(version)) throw Error("Figment version must be major.minor.patch");
  const parts = dictionary(f.parts, 64, v => identifier(v));
  const joints = dictionary(f.joints, 64, v => identifier(v));
  const anchors = dictionary(f.anchors, 32, v => {
    const a = object(v, "anchor");
    fields(a, ["part", "node", "position", "rotation"], "anchor");
    const part = a.part === undefined ? "self" : identifier(a.part);
    if (part !== "self" && !Object.hasOwn(parts, part)) throw Error(`Unknown anchor part: ${part}`);
    return { part, node: a.node == null ? null : label(a.node, "", 256), position: vector(a.position), rotation: vector(a.rotation) };
  });
  const grips = dictionary(f.grips, 8, v => {
    const g = object(v, "grip");
    fields(g, ["anchor", "hand", "radius", "twoHand"], "grip");
    const anchor = identifier(g.anchor);
    if (!Object.hasOwn(anchors, anchor)) throw Error(`Unknown grip anchor: ${anchor}`);
    return { anchor, hand: option(g.hand, ["either", "left", "right"], "either"), radius: number(g.radius, .3, .01, 2), twoHand: option(g.twoHand, ["aim", "scale"], "aim") };
  });
  const properties = dictionary(f.properties, 32, (v, key) => {
    const p = object(v, "property");
    fields(p, ["type", "label", "value", "editable", "min", "max", "step", "unit"], "property");
    const type = option(p.type, ["number", "boolean", "string", "color"], "number");
    const result: FigmentProperty = { type, label: label(p.label, key), value: p.value ?? (type === "boolean" ? false : type === "number" ? 0 : type === "color" ? "#6E7DFF" : ""), editable: flag(p.editable, true) };
    if (type === "number") {
      result.min = number(p.min, -10000);
      result.max = number(p.max, 10000);
      if (result.min >= result.max) throw Error("Property max must exceed min");
      result.step = number(p.step, .01, .000001, result.max - result.min);
      result.unit = label(p.unit, "", 24);
    } else if (["min", "max", "step", "unit"].some(k => p[k] !== undefined)) throw Error("Numeric bounds require a number property");
    result.value = propertyValue(result, result.value);
    return result;
  });
  const actions = dictionary(f.actions, 16, (v, key) => {
    const a = object(v, "action");
    fields(a, ["label"], "action");
    return { label: label(a.label, key, 80) };
  });
  let behavior: FigmentDefinition["behavior"] = null;
  if (f.behavior != null) {
    const b = object(f.behavior, "behavior");
    fields(b, ["source", "hz"], "behavior");
    behavior = { source: label(b.source, "", 60000), hz: number(b.hz, 20, 1, 30) };
  }
  let source: FigmentDefinition["source"] = null;
  if (f.source != null) {
    const s = object(f.source, "source");
    fields(s, ["url", "filename"], "source");
    const filename = label(s.filename, "source.blend", 128);
    if (!/^[\w .-]+\.blend$/.test(filename)) throw Error("Editable source needs a .blend filename without paths");
    source = { url: assetReference(s.url), filename };
  }
  return { protocol: FIGMENT_PROTOCOL, title: label(f.title, "Figment"), description: label(f.description, "", 2048), version, parts, joints, anchors, grips, properties, actions, behavior, source };
}

export const FIGMENT_API_HELP = {
  protocol: FIGMENT_PROTOCOL,
  definition: "Attach a Figment to an existing entity. parts maps stable names to entity IDs; self always means the root. anchors use part-local metres/radians and optional unique GLB node names. grips reference anchors. properties have type, value, label, editable and optional numeric bounds. actions map names to {label}. behavior contains JavaScript source and hz. source optionally references the editable .blend.",
  behavior: "Return {tick(){},event(e){}}. api.self, api.part(name), api.anchor(name), api.property(name), api.setProperty(name,value), api.patch(part,patch), api.impulse(part,xyz,torque?), api.signal(name,data), api.state, api.time, api.dt. Runs in the existing network-free worker sandbox. Physics/property changes are validated; human grabs own transforms. Event types include action, property, grab, release, collision, collision.end, sensor.enter, sensor.exit, signal.",
  publishing: "Publish freezes a self-contained version in the local Figment library. Export a .figment.json package to share; import verifies its SHA-256 assets before placing a paused editable instance with new IDs. No cloud account or public upload is implied. Included .blend source remains downloadable and editable.",
  physics: "Use physics mode, mass, gravity, friction, restitution, linearDamping, angularDamping, translations/rotations [boolean,boolean,boolean] and compound colliders. Collider shapes: box, sphere, capsule, cylinder, cone, convex. size is full XYZ extent; position/rotation are object-local. Sensors generate enter/exit. Assets need explicit colliders. Hinge/slider/ball/spring/rope joints connect root bodies.",
  example: {
    title: "Interactive object", version: "1.0.0", parts: {}, joints: {},
    anchors: { handle: { part: "self", node: null, position: [0, 0, 0], rotation: [0, 0, 0] } },
    grips: { hold: { anchor: "handle", hand: "either", radius: .3, twoHand: "aim" } },
    properties: { brightness: { type: "number", label: "Brightness", value: 2, min: 0, max: 10, step: .1, editable: true } },
    actions: { activate: { label: "Activate" } },
    behavior: { hz: 20, source: 'return {event(e){if(e.type==="action")api.setProperty("brightness",5)},tick(){api.patch("self",{material:{emissive:"#6E7DFF",glow:api.property("brightness")}})}};' },
  },
  commands: "All bundle tools take command and payload. attach/configure: {id,definition?,preset?,physics?,grabbable?,start?}. properties: {id,values}. behavior: {id,source,hz?,start?} or {id,action:pause|resume|reset}. physics: {id,physics}. action: {id,action,data?}. publish: {id}; export/place: {packageId,position?}; import: {url|package,place?,position?}. inspect: {id?,includeSource?:true}. Raw maps replace in full. Presets prop/lantern/kinetic/impact are editable, optional starting points.",
};
