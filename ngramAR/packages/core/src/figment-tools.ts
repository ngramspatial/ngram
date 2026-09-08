/** Transport-neutral Figment bundle shared by provider schema adapters. */
export const FIGMENT_TOOL_COMMANDS = {
  figment: ["capabilities", "inspect", "attach", "configure", "detach"],
  figment_physics: ["physics"],
  figment_behavior: ["behavior"],
  figment_interact: ["properties", "action"],
  figment_library: ["library", "publish", "export", "import", "place"],
} as const;
const descriptions = {
  figment: "Attach programmable Figments to Spatial objects and Blender models. Start with capabilities for schemas and presets. attach/configure payload: {id,definition,preset?,physics?,start?}. Named parts, joints, anchors, grips, typed properties, actions and local behavior travel with the object. New behavior is paused by default.",
  figment_physics: "Adjust physics with payload {id,physics}: mass, gravity, friction, restitution, linearDamping, angularDamping, axis flags, compound colliders and sensors. GLB assets require explicit colliders. Use world apply for joints. Read figment capabilities for exact schema.",
  figment_behavior: "Program a Figment with payload {id,source,hz?,start?} or control it with {id,action:pause|resume|reset}. JavaScript returns tick/event handlers; api.self, part, anchor, property, setProperty, patch, impulse, motor, signal, state, time and dt. Local sandbox; no network/DOM/model calls. Human grabs take priority.",
  figment_interact: "Interact with Figments through properties {id,values:{name:value}} or action {id,action,data?}. Inspect first for available names. These are local events, not model turns.",
  figment_library: "Publish and share portable Figments: publish {id}, library {}, export {packageId}, import {url|package,place?,position?}, place {packageId,position?}. Export downloads a file on the human's device and returns a small receipt. Includes models, code, physics, grips and optional .blend source. Import creates paused editable copies. Published title/version is immutable; bump version for changes. Local library, no public upload. Never repeatedly poll or replay uncertain placements.",
};
export const FIGMENT_TOOL_DEFINITIONS = Object.entries(FIGMENT_TOOL_COMMANDS).map(([name, commands]) => ({
  name,
  description: descriptions[name as keyof typeof descriptions],
  parameters: {
    command: { type: "string", enum: [...commands], description: "Figment operation." },
    payload: { type: "object", description: "Payload per figment capabilities." },
  },
  required: ["command"],
}));
export function isFigmentTool(name: string): boolean { return Object.hasOwn(FIGMENT_TOOL_COMMANDS, name); }
export function figmentToolPayload(name: string, args: Record<string, any>) {
  const commands = FIGMENT_TOOL_COMMANDS[name as keyof typeof FIGMENT_TOOL_COMMANDS] as readonly string[] | undefined;
  if (!commands?.includes(args.command)) throw Error("Invalid Figment tool command");
  if (args.payload != null && (typeof args.payload !== "object" || Array.isArray(args.payload))) throw Error("Figment payload must be an object");
  return { ...(args.payload ?? {}), command: args.command };
}
