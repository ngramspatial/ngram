/** Harness-independent creation protocol. Metres, radians, seconds, right-handed Y-up. */
export const WORLD_PROTOCOL = "ngram.world/1" as const;
export const WORLD_LIMITS = Object.freeze({
  entities: 512,
  operations: 256,
  vertices: 30000,
  instances: 2048,
  programs: 12,
  assets: 8,
  lights: 8,
  sourceBytes: 64000,
  events: 512,
});
export type V3 = [number, number, number];
export type Q4 = [number, number, number, number];
export interface Transform {
  position: V3;
  rotation: V3;
  scale: V3;
}
export interface Material {
  color: string;
  roughness: number;
  metalness: number;
  opacity: number;
  emissive: string;
  glow: number;
}
export interface Geometry {
  shape: "box" | "sphere" | "cylinder" | "cone" | "torus" | "mesh" | "line";
  size: V3;
  vertices?: number[];
  indices?: number[];
  instances?: V3[];
}
export interface Body {
  mode: "fixed" | "dynamic" | "kinematic";
  mass: number;
  restitution: number;
  friction: number;
  damping: number;
  gravity: V3;
}
export interface Control {
  type: "button" | "toggle" | "slider";
  label: string;
  value: number;
  min: number;
  max: number;
  step: number;
}
export interface WorldEntity {
  id: string;
  kind: "group" | "shape" | "light" | "control" | "asset";
  name: string;
  parent: string | null;
  tags: string[];
  transform: Transform;
  material: Material;
  geometry: Geometry;
  physics: Body | null;
  control: Control | null;
  grabbable: boolean;
  visible: boolean;
  asset: {
    url: string;
    format: "glb" | "image";
    fit: number;
    preserveMaterials: boolean;
    normalize: boolean;
  } | null;
}
export interface WorldJoint {
  id: string;
  type: "hinge" | "slider" | "ball" | "spring" | "rope";
  a: string;
  b: string;
  anchorA: V3;
  anchorB: V3;
  axis: V3;
  limits: [number, number] | null;
  velocity: number;
  strength: number;
  length: number;
  stiffness: number;
  damping: number;
}
export interface WorldDocument {
  protocol: typeof WORLD_PROTOCOL;
  id: string;
  name: string;
  revision: number;
  entities: WorldEntity[];
  joints: WorldJoint[];
}
export type WorldOperation =
  | { op: "entity.create"; entity: unknown }
  | { op: "entity.patch"; id: string; patch: unknown }
  | { op: "entity.delete"; id: string }
  | { op: "joint.create"; joint: unknown }
  | { op: "joint.delete"; id: string }
  | { op: "joint.motor"; id: string; velocity: number; strength?: number }
  | { op: "body.impulse"; id: string; impulse: V3; torque?: V3 };
export interface WorldEdit {
  requestId: string;
  baseRevision?: number;
  operations: WorldOperation[];
}
export interface WorldEvent {
  sequence: number;
  time: number;
  type: string;
  actor: string;
  target?: string;
  data?: unknown;
}

function record(v: unknown, label: string): Record<string, any> {
  if (!v || typeof v !== "object" || Array.isArray(v))
    throw new Error(`${label} must be an object`);
  return v as Record<string, any>;
}
function keys(v: Record<string, any>, allowed: string[], label: string) {
  for (const k of Object.keys(v))
    if (!allowed.includes(k)) throw new Error(`Unknown ${label} field: ${k}`);
}
export function number(
  v: unknown,
  fallback: number,
  min = -10000,
  max = 10000,
): number {
  if (v === undefined) return fallback;
  if (typeof v !== "number" || !Number.isFinite(v) || v < min || v > max)
    throw new Error(`Expected a finite number in [${min}, ${max}]`);
  return v;
}
export function vector(
  v: unknown,
  fallback: V3 = [0, 0, 0],
  min = -10000,
  max = 10000,
): V3 {
  if (v === undefined) return [...fallback];
  if (!Array.isArray(v) || v.length !== 3) throw new Error("Expected [x,y,z]");
  return v.map((n) => number(n, 0, min, max)) as V3;
}
function text(v: unknown, fallback: string, max = 160): string {
  if (v === undefined) return fallback;
  if (typeof v !== "string" || v.length > max)
    throw new Error(`Expected text up to ${max} characters`);
  return v;
}
export function identifier(v: unknown): string {
  if (typeof v !== "string" || !/^[a-zA-Z0-9][a-zA-Z0-9_.:-]{0,95}$/.test(v))
    throw new Error("ID must be 1–96 letters, digits, _, ., :, or -");
  return v;
}
function choice<T extends string>(
  v: unknown,
  options: readonly T[],
  fallback: T,
): T {
  if (v === undefined) return fallback;
  if (!options.includes(v as T))
    throw new Error(`Expected one of: ${options.join(", ")}`);
  return v as T;
}
function bool(v: unknown, fallback: boolean) {
  if (v === undefined) return fallback;
  if (typeof v !== "boolean") throw new Error("Expected boolean");
  return v;
}
function color(v: unknown, fallback: string) {
  const c = text(v, fallback);
  if (!/^#[\da-f]{6}$/i.test(c)) throw new Error("Colors must be #RRGGBB");
  return c;
}

export function parseEntity(value: unknown): WorldEntity {
  const v = record(value, "entity");
  keys(
    v,
    [
      "id",
      "kind",
      "name",
      "parent",
      "tags",
      "transform",
      "material",
      "geometry",
      "physics",
      "control",
      "asset",
      "grabbable",
      "visible",
    ],
    "entity",
  );
  const id = identifier(v.id);
  const kind = choice(
    v.kind,
    ["group", "shape", "light", "control", "asset"],
    "shape",
  );
  const t = record(v.transform ?? {}, "transform");
  keys(t, ["position", "rotation", "scale"], "transform");
  const m = record(v.material ?? {}, "material");
  keys(
    m,
    ["color", "roughness", "metalness", "opacity", "emissive", "glow"],
    "material",
  );
  const g = record(v.geometry ?? {}, "geometry");
  keys(g, ["shape", "size", "vertices", "indices", "instances"], "geometry");
  const geometry: Geometry = {
    shape: choice(
      g.shape,
      ["box", "sphere", "cylinder", "cone", "torus", "mesh", "line"],
      "box",
    ),
    size: vector(g.size, [0.2, 0.2, 0.2], 0.001, 100),
  };
  if (["mesh", "line"].includes(geometry.shape)) {
    if (
      !Array.isArray(g.vertices) ||
      g.vertices.length < 6 ||
      g.vertices.length % 3 ||
      g.vertices.length > WORLD_LIMITS.vertices * 3
    )
      throw new Error("Geometry requires bounded XYZ vertices");
    geometry.vertices = g.vertices.map((n: unknown) => number(n, 0, -100, 100));
    if (geometry.shape === "mesh") {
      if (
        !Array.isArray(g.indices) ||
        g.indices.length % 3 ||
        g.indices.length < 3 ||
        g.indices.length > WORLD_LIMITS.vertices * 6
      )
        throw new Error("Mesh requires triangle indices");
      geometry.indices = g.indices.map((n: unknown) => {
        const i = number(n, 0, 0, g.vertices.length / 3 - 1);
        if (!Number.isInteger(i)) throw new Error("Indices must be integers");
        return i;
      });
    }
  } else if (g.vertices !== undefined || g.indices !== undefined)
    throw new Error("Custom vertices require mesh or line");
  if (g.instances !== undefined) {
    if (
      !Array.isArray(g.instances) ||
      !g.instances.length ||
      g.instances.length > WORLD_LIMITS.instances
    )
      throw new Error("Invalid instance count");
    geometry.instances = g.instances.map((p: unknown) => vector(p));
  }
  let physics: Body | null = null;
  if (v.physics != null) {
    const p = record(v.physics, "physics");
    keys(
      p,
      ["mode", "mass", "restitution", "friction", "damping", "gravity"],
      "physics",
    );
    physics = {
      mode: choice(p.mode, ["fixed", "dynamic", "kinematic"], "dynamic"),
      mass: number(p.mass, 1, 0.001, 1000),
      restitution: number(p.restitution, 0.35, 0, 1),
      friction: number(p.friction, 0.5, 0, 10),
      damping: number(p.damping, 0.2, 0, 100),
      gravity: vector(p.gravity, [0, -9.81, 0], -100, 100),
    };
    if (
      kind !== "shape" ||
      geometry.instances ||
      ["mesh", "line", "torus"].includes(geometry.shape)
    )
      throw new Error(
        "Physics supports uninstanced box, sphere, cylinder or cone shapes",
      );
    if (v.parent != null)
      throw new Error(
        "Physics bodies must be world roots; use joints to connect them",
      );
  }
  let control: Control | null = null;
  if (kind === "control") {
    const c = record(v.control ?? {}, "control");
    keys(c, ["type", "label", "value", "min", "max", "step"], "control");
    const min = number(c.min, 0),
      max = number(c.max, 1);
    if (min >= max) throw new Error("Control max must exceed min");
    control = {
      type: choice(c.type, ["button", "toggle", "slider"], "button"),
      label: text(c.label, v.name ?? id, 80),
      min,
      max,
      step: number(c.step, 0.01, 0.0001, max - min),
      value: number(c.value, min, min, max),
    };
  } else if (v.control != null)
    throw new Error("Only controls accept control properties");
  if (v.tags !== undefined && (!Array.isArray(v.tags) || v.tags.length > 16))
    throw new Error("Use at most 16 tags");
  let asset: WorldEntity["asset"] = null;
  if (kind === "asset") {
    const a = record(v.asset, "asset");
    keys(a, ["url", "format", "fit", "preserveMaterials", "normalize"], "asset");
    const url = text(a.url, "", 2048);
    if (!/^https?:\/\//i.test(url) && !/^\/(?!\/)/.test(url))
      throw new Error("Assets require HTTP(S) or a root-relative URL");
    if (/^https?:\/\//i.test(url)) {
      const parsed = new URL(url);
      if (parsed.username || parsed.password)
        throw new Error("Do not put credentials in asset URLs");
    }
    asset = {
      url,
      format: choice(a.format, ["glb", "image"], "glb"),
      fit: number(a.fit, 1, 0.001, 100),
      preserveMaterials: bool(a.preserveMaterials, v.material === undefined),
      normalize: bool(a.normalize, true),
    };
  } else if (v.asset != null)
    throw new Error("Only asset entities accept asset properties");
  return {
    id,
    kind,
    name: text(v.name, id),
    parent: v.parent == null ? null : identifier(v.parent),
    tags: (v.tags ?? []).map((t: unknown) => text(t, "", 64)),
    transform: {
      position: vector(t.position),
      rotation: vector(t.rotation),
      scale: vector(t.scale, [1, 1, 1], 0.001, 100),
    },
    material: {
      color: color(m.color, "#6E7DFF"),
      roughness: number(m.roughness, 0.28, 0, 1),
      metalness: number(m.metalness, 0.25, 0, 1),
      opacity: number(m.opacity, 1, 0, 1),
      emissive: color(m.emissive, "#000000"),
      glow: number(m.glow, 0, 0, 20),
    },
    geometry,
    physics,
    control,
    asset,
    grabbable: bool(v.grabbable, kind === "shape" || kind === "asset"),
    visible: bool(v.visible, true),
  };
}

export function patchEntity(entity: WorldEntity, value: unknown): WorldEntity {
  const patch = record(value, "patch");
  if (entity.kind === "asset" && patch.geometry)
    throw new Error("Resize assets with transform.scale or asset.fit");
  if ("id" in patch || "kind" in patch)
    throw new Error("An entity ID and kind are immutable");
  const merged = { ...entity, ...patch };
  for (const k of [
    "transform",
    "material",
    "geometry",
    "physics",
    "control",
    "asset",
  ]) {
    if (patch[k] != null)
      (merged as any)[k] = { ...(entity as any)[k], ...record(patch[k], k) };
  }
  if (
    entity.asset &&
    patch.material &&
    patch.asset?.preserveMaterials === undefined
  )
    merged.asset = { ...merged.asset!, preserveMaterials: false };
  return parseEntity(merged);
}

export function parseJoint(value: unknown): WorldJoint {
  const j = record(value, "joint");
  keys(
    j,
    [
      "id",
      "type",
      "a",
      "b",
      "anchorA",
      "anchorB",
      "axis",
      "limits",
      "velocity",
      "strength",
      "length",
      "stiffness",
      "damping",
    ],
    "joint",
  );
  const axis = vector(j.axis, [0, 0, 1], -1, 1);
  const norm = Math.hypot(...axis);
  if (norm < 0.001) throw new Error("Joint axis cannot be zero");
  let limits: [number, number] | null = null;
  if (j.limits != null) {
    if (!Array.isArray(j.limits) || j.limits.length !== 2)
      throw new Error("Expected [min,max] joint limits");
    limits = [number(j.limits[0], 0), number(j.limits[1], 0)];
    if (limits[0] > limits[1]) throw new Error("Invalid joint limits");
  }
  return {
    id: identifier(j.id),
    type: choice(
      j.type,
      ["hinge", "slider", "ball", "spring", "rope"],
      "hinge",
    ),
    a: identifier(j.a),
    b: identifier(j.b),
    anchorA: vector(j.anchorA),
    anchorB: vector(j.anchorB),
    axis: axis.map((n) => n / norm) as V3,
    limits,
    velocity: number(j.velocity, 0, -100, 100),
    strength: number(j.strength, 1, 0, 1000),
    length: number(j.length, 1, 0.001, 100),
    stiffness: number(j.stiffness, 20, 0, 10000),
    damping: number(j.damping, 2, 0, 1000),
  };
}

export const WORLD_API_HELP = {
  protocol: WORLD_PROTOCOL,
  coordinates:
    "Right-handed Y-up. Metres, Euler XYZ radians, seconds. Root transforms are world-space; children use parent-local transforms. Physics bodies are roots; joint anchors are body-local.",
  operations: [
    "entity.create {entity}",
    "entity.patch {id,patch}",
    "entity.delete {id} (includes descendants and joints)",
    "joint.create {joint}",
    "joint.delete {id}",
    "joint.motor {id,velocity,strength?}",
    "body.impulse {id,impulse:[x,y,z],torque?:[x,y,z]}",
  ],
  entity: {
    id: "unique-id",
    kind: "shape|group|light|control|asset",
    parent: "group-id or null",
    tags: ["assembly-name"],
    transform: { position: [0, 1, -1], rotation: [0, 0, 0], scale: [1, 1, 1] },
    geometry: {
      shape: "box|sphere|cylinder|cone|torus|mesh|line",
      size: [0.2, 0.2, 0.2],
      vertices: "mesh/line: flat XYZ array",
      indices: "mesh: triangle index array",
      instances: "optional array of XYZ offsets",
    },
    material: {
      color: "#6E7DFF",
      roughness: 0.28,
      metalness: 0.25,
      opacity: 1,
      emissive: "#000000",
      glow: 0,
    },
    physics:
      "optional {mode:fixed|dynamic|kinematic,mass,restitution,friction,damping,gravity:[0,-9.81,0]}",
    control:
      "control kind: {type:button|toggle|slider,label,value,min,max,step}",
    grabbable: true,
    visible: true,
  },
  joint: {
    id: "hinge",
    type: "hinge|slider|ball|spring|rope",
    a: "body-id",
    b: "body-id",
    anchorA: [0, 0, 0],
    anchorB: [0, 0, 0],
    axis: [0, 0, 1],
    velocity: 0,
    strength: 1,
    limits: "optional [min,max]",
    length: 1,
    stiffness: 20,
    damping: 2,
  },
  constraints:
    "Edits validate before commit. Human grabs own transforms until release. requestId deduplicates retries; baseRevision detects conflicting edits. Simulation poses are live samples, not edit revisions. Events are polled and never automatically invoke an LLM.",
  limits: WORLD_LIMITS,
  observe:
    "{ids?:[id,...],tag?:string,offset?:number,limit?:1..512,includeGeometry?:boolean}. Default page 16. Large geometry arrays are summarized as counts; request includeGeometry for selected entities when you need raw data.",
  assets:
    "kind:asset with asset:{url,format:glb|image,fit:metres}. Images and self-contained GLB files up to 32 MB. observe reports loading/ready/failed, progress and errors. Replacing an asset or deleting its entity cancels the old load. Asset entities are grabbable and may belong to groups; use simple separate shapes for physics colliders.",
  perform:
    "command:perform with {action:look|approach,target:entityId}. Look sets gaze toward the live object. Approach starts a walk with a 0.7 metre standoff and returns an accepted task ID; poll events for perform.completed/perform.cancelled. Pause cancels it. No fabricated hand contact or room understanding.",
};
