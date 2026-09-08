import { number, vector, identifier, type V3 } from "./world-contract.js";
import { object, fields, flag, option } from "./figment-contract.js";

export interface ColliderDefinition {
  id: string;
  shape: "box" | "sphere" | "capsule" | "cylinder" | "cone" | "convex";
  size: V3;
  position: V3;
  rotation: V3;
  sensor: boolean;
  groups: number;
  vertices?: number[];
}
export interface Body {
  mode: "fixed" | "dynamic" | "kinematic";
  mass: number;
  restitution: number;
  friction: number;
  damping: number;
  linearDamping: number;
  angularDamping: number;
  gravity: V3;
  translations: [boolean, boolean, boolean];
  rotations: [boolean, boolean, boolean];
  colliders: ColliderDefinition[];
}
const axes = (raw: unknown): [boolean, boolean, boolean] => {
  if (raw === undefined) return [true, true, true];
  if (!Array.isArray(raw) || raw.length !== 3) throw Error("Expected three boolean axis flags");
  return raw.map(v => flag(v, true)) as [boolean, boolean, boolean];
};
export function parseBody(raw: unknown): Body {
  const p = object(raw, "physics");
  fields(p, ["mode", "mass", "restitution", "friction", "damping", "linearDamping", "angularDamping", "gravity", "translations", "rotations", "colliders"], "physics");
  const damping = number(p.damping, .2, 0, 100);
  if (p.colliders !== undefined && (!Array.isArray(p.colliders) || p.colliders.length > 16)) throw Error("Use at most 16 colliders per body");
  const colliders: ColliderDefinition[] = (p.colliders ?? []).map((v: unknown) => {
    const c = object(v, "collider");
    fields(c, ["id", "shape", "size", "position", "rotation", "sensor", "groups", "vertices"], "collider");
    const collider: ColliderDefinition = { id: identifier(c.id), shape: option(c.shape, ["box", "sphere", "capsule", "cylinder", "cone", "convex"], "box"), size: vector(c.size, [.2, .2, .2], .001, 100), position: vector(c.position), rotation: vector(c.rotation), sensor: flag(c.sensor, false), groups: number(c.groups, 0xffffffff, 0, 0xffffffff) };
    if (!Number.isInteger(collider.groups)) throw Error("Collision groups must be an unsigned 32-bit integer");
    if (collider.shape === "convex") {
      if (!Array.isArray(c.vertices) || c.vertices.length < 12 || c.vertices.length % 3 || c.vertices.length > 768) throw Error("Convex colliders require 4–256 XYZ vertices");
      collider.vertices = c.vertices.map((n: unknown) => number(n, 0, -100, 100));
      const points = Array.from({ length: collider.vertices!.length / 3 }, (_, i) => collider.vertices!.slice(i * 3, i * 3 + 3));
      const a = points[0];
      const offsets = points.map(p => p.map((n, i) => n - a[i]));
      const b = offsets.reduce((best, p) => Math.hypot(...p) > Math.hypot(...best) ? p : best);
      const crosses = offsets.map(p => [b[1]*p[2]-b[2]*p[1], b[2]*p[0]-b[0]*p[2], b[0]*p[1]-b[1]*p[0]]);
      const normal = crosses.reduce((best, p) => Math.hypot(...p) > Math.hypot(...best) ? p : best);
      if (!offsets.some(p => Math.abs(p.reduce((sum, n, i) => sum + n * normal[i], 0)) > 1e-12))
        throw Error("Convex collider vertices must span a volume");
    } else if (c.vertices !== undefined) throw Error("Collider vertices require convex shape");
    if (collider.shape === "capsule" && collider.size[1] < Math.max(collider.size[0], collider.size[2])) throw Error("Capsule height must be at least its diameter");
    return collider;
  });
  if (new Set(colliders.map(c => c.id)).size !== colliders.length) throw Error("Collider IDs must be unique within a body");
  return { mode: option(p.mode, ["fixed", "dynamic", "kinematic"], "dynamic"), mass: number(p.mass, 1, .001, 1000), restitution: number(p.restitution, .35, 0, 1), friction: number(p.friction, .5, 0, 10), damping, linearDamping: number(p.linearDamping, damping, 0, 100), angularDamping: number(p.angularDamping, damping, 0, 100), gravity: vector(p.gravity, [0, -9.81, 0], -100, 100), translations: axes(p.translations), rotations: axes(p.rotations), colliders };
}
